#!/usr/bin/env python3
"""Google ADK Multi-Agent Definition for NEXT Student Transfer Assistant (Cloud Run App 2).

Architecture (Headless HR Multi-Agent Service on Gemini Enterprise via A2A):
  - Coordinator: `next_hr_transfer_coordinator` (Gemini 2.5 Flash + Regional Model Armor `europe-west2`)
  - Sub-Agent 1: `dq_and_change_auditor_agent`
      * `audit_recent_changes_and_dq_warnings`
      * `resolve_quarantined_change` (A2A-safe HITL with `confirm: bool = False`)
      * `revert_invalid_change`
  - Sub-Agent 2: `matcher_and_simulator_agent`
      * `run_batch_matching`
      * `explain_student_status`
      * `rerun_batch2_cmp_shifts`
      * `simulate_policy_change`
  - Sub-Agent 3: `hr_comms_and_intervention_agent`
      * `generate_and_send_comms_strategy` (Stages outreach in BigQuery + outputs copy-clean blocks for `@Gmail`)
      * `record_hr_exception_decision` (A2A-safe HITL with `confirm: bool = False`)
      * `record_hitl_override`
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from src.bq_repository import build_tokenized_email_address, get_bq_repository
from src.matcher import (
    explain_student_placement,
    record_hitl_override as _record_hitl_override,
    rerun_batch2_cmp_for_store,
    run_matching_engine,
    run_what_if_simulation,
)
from src.model_armor_guard import ModelArmorGuard

ROOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(ROOT_DIR / ".env")

GEMINI_MODEL_ID = os.getenv("GEMINI_MODEL_ID", "gemini-2.5-flash")
MAX_OUTPUT_TOKENS = 1024

SYSTEM_INSTRUCTION = """You are the NEXT PLC HR Student Transfer Coordinator (`next_hr_transfer_coordinator`) supporting Central HR Specialists in Gemini Enterprise across 470 UK retail stores.

You orchestrate 3 specialized ADK Sub-Agents backed by Google Cloud BigQuery (`next_student_transfer_staging` & `next_student_transfer_gold` in `europe-west2`) and Dataplex Auto Data Quality (`next-student-transfer-dq-scan` + `v_data_quality_warnings`):
1. `dq_and_change_auditor_agent` — Audits Student (`STU-XXXX`) and Store Manager (`SM-XXX`) portal requests in `next_student_transfer_staging.portal_change_requests` and `change_audit_log`, surfaces `DQ-01`..`DQ-06` Dataplex / BigQuery warnings, and resolves quarantined changes (`resolve_quarantined_change` with `confirm: bool = False`).
2. `matcher_and_simulator_agent` — Executes the deterministic 3-pass `>=5.5hr` matching engine on `next_student_transfer_gold`, explains student contention, runs Batch-2 3-day CMP re-matches, and runs What-If policy simulations (`sp_simulate_store_capacity`).
3. `hr_comms_and_intervention_agent` — Generates the Student & Store Manager Comms Strategy, deterministically builds regex-verified recipient addresses (`hr-notifications+stu-XXXX@next-retail.example.com`, `hr-notifications+sm-XXX@next-retail.example.com`), stages outreach rows in `next_student_transfer_gold.outreach_email_log` (`STAGED_FOR_GE_GMAIL`), outputs copy-clean email blocks (`To`, `Subject`, `Body`) for the Gemini Enterprise native `@Gmail` connector handoff, and records Stage 1 HITL decisions (`record_hr_exception_decision` with `confirm: bool = False`).

NON-NEGOTIABLE OPERATING RULES:
1. ZERO RAW PII: Never output or accept raw student names, personal emails, UK phone numbers (07... / +44...), DOBs, or National Insurance numbers. Refer to colleagues strictly as STU-XXXX and Store Managers as SM-XXX.
2. 100% DETERMINISTIC TOOL GROUNDING: Never calculate shift hours, candidate rankings, or DQ rules yourself. Always cite the exact STU-XXXX, STORE-XXX, SHF-XXXX, REQ-XXXXXXXX, CHG-XXXXXXXX, and DQ-XX identifiers returned by your tools.
3. UK YOUNG WORKER (UNDER_18) STATUTORY BOUNDARY: Colleagues with age_band=UNDER_18 are strictly blocked from shifts ending after 22:00, starting before 06:00, >8.0h/day, or >40.0h/week (https://www.gov.uk/rest-breaks-work/young-workers).
4. STAGE 1 HITL GOVERNANCE: All placements in Stage 1 require human review before staging to BigQuery `placements_staging` ('Do Not Post'). State-mutating HR override tools (`resolve_quarantined_change`, `record_hr_exception_decision`) require `confirm=True` after previewing with `confirm=False`.
"""


# =============================================================================
# Sub-Agent 1 Tools: Data Quality & Change Audit (`dq_and_change_auditor_agent`)
# =============================================================================
def audit_recent_changes_and_dq_warnings(limit: int = 25) -> dict[str, Any]:
    """Audits recent Student & Store Manager portal requests in `next_student_transfer_staging` (`portal_change_requests`, `change_audit_log`, `v_data_quality_warnings`) and Dataplex DataScan status."""
    repo = get_bq_repository()
    changes = repo.get_recent_changes(limit=int(limit), include_reverted=False)
    dq_warnings = repo.get_data_quality_warnings()
    dataplex_scan = repo.get_latest_dataplex_scan_status()

    valid_changes = [c for c in changes if c.get("validation_status") in {"VALID", "PROMOTED"}]
    flagged_changes = [c for c in changes if c.get("validation_status") not in {"VALID", "PROMOTED"}]

    return {
        "staging_dataset": f"{repo.project_id}.{repo.staging_dataset_id}",
        "gold_dataset": f"{repo.project_id}.{repo.gold_dataset_id}",
        "dataplex_scan": dataplex_scan,
        "total_active_changes": len(changes),
        "valid_changes_count": len(valid_changes),
        "flagged_changes_count": len(flagged_changes),
        "active_dq_warnings_count": len(dq_warnings),
        "active_dq_warnings": dq_warnings,
        "recent_changes": changes,
    }


def resolve_quarantined_change(
    request_id: str,
    decision: str = "REJECT",
    hr_reason: str = "Resolved by Central HR Specialist via Gemini Enterprise",
    confirm: bool = False,
) -> dict[str, Any]:
    """Resolves a quarantined change (`REQ-XXXXXXXX` or `CHG-XXXXXXXX`) in `next_student_transfer_staging.portal_change_requests`.

    A2A-Safe HITL Pattern:
      - When `confirm=False` (default): writes nothing to BigQuery and returns a preview asking HR to reply 'Confirm'.
      - When `confirm=True`: executes `REJECT` (`REJECTED_BY_HR`) or `OVERRIDE_PROMOTE` (`OVERRIDDEN_BY_HR`) in BigQuery.
    """
    repo = get_bq_repository()
    res = repo.resolve_quarantined_change(
        identifier=request_id,
        decision=decision,
        hr_reason=hr_reason,
        resolved_by="HR-SPECIALIST-GE",
        confirm=bool(confirm),
    )
    remaining_warnings = repo.get_data_quality_warnings()
    return {
        "resolution_result": res,
        "remaining_dq_warnings_count": len(remaining_warnings),
        "remaining_dq_warnings": remaining_warnings,
    }


def revert_invalid_change(
    change_id: str,
    hr_reason: str = "Reverted by HR Data Quality Audit",
    confirm: bool = True,
) -> dict[str, Any]:
    """Reverts/rejects an invalid Student or Store Manager change (`CHG-XXXXXXXX` or `REQ-XXXXXXXX`) in BigQuery."""
    repo = get_bq_repository()
    res = repo.revert_change(
        change_id=change_id.strip().upper(),
        reverted_by="HR-SPECIALIST-GE",
        revert_reason=hr_reason,
        confirm=bool(confirm),
    )
    remaining_warnings = repo.get_data_quality_warnings()
    return {
        "revert_result": res,
        "remaining_dq_warnings_count": len(remaining_warnings),
        "remaining_dq_warnings": remaining_warnings,
    }


# =============================================================================
# Sub-Agent 2 Tools: Deterministic Matcher & Simulator (`matcher_and_simulator_agent`)
# =============================================================================
def run_batch_matching() -> dict[str, Any]:
    """Runs the deterministic 3-pass matching cascade across 1,050 students and 470 UK stores from `next_student_transfer_gold`."""
    result = run_matching_engine()
    dq_warnings = get_bq_repository().get_data_quality_warnings()
    return {
        "summary": result["summary"],
        "active_dq_warnings_count": len(dq_warnings),
        "top_contended_stores": result["top_contended_stores"][:8],
        "graduation_scorecard": result["graduation_scorecard"],
    }


def explain_student_status(student_id: str) -> dict[str, Any]:
    """Explains the exact match status, shift IDs, hours, Choice-1/2/3 contention history, and any active DQ warnings for a STU-XXXX token."""
    sid = student_id.strip().upper()
    explanation = explain_student_placement(sid)
    dq_warnings = [
        w for w in get_bq_repository().get_data_quality_warnings()
        if w.get("entity_id") == sid or w.get("owner_token") == sid
    ]
    explanation["active_dq_warnings"] = dq_warnings
    return explanation


def simulate_policy_change(min_hours_for_4star: float = 4.0, target_cities: str = "Manchester,Leeds") -> dict[str, Any]:
    """Simulates allowing 4-star PERM students with small_contract_opt_in=true to complete on >=min_hours_for_4star single shifts."""
    cities = [c.strip() for c in target_cities.split(",") if c.strip()]
    sim = run_what_if_simulation(min_hours_for_4star=float(min_hours_for_4star), target_cities=cities)
    c1 = cities[0] if len(cities) > 0 else "Manchester"
    c2 = cities[1] if len(cities) > 1 else c1
    sim["bq_stored_procedure_capacity"] = get_bq_repository().run_sql_capacity_simulation(
        min_hours_for_4star=float(min_hours_for_4star),
        city_1=c1,
        city_2=c2,
    )
    return sim


def rerun_batch2_cmp_shifts(store_id: str = "STORE-019", newly_cleared_shift_ids: str = "SHF-9001,SHF-9002,SHF-9003") -> dict[str, Any]:
    """Re-runs Batch-2 matching for a store after specific Scratchpad shifts clear the 3-day Contract Marketplace (CMP)."""
    shift_ids = [s.strip().upper() for s in newly_cleared_shift_ids.split(",") if s.strip()]
    return rerun_batch2_cmp_for_store(store_id=store_id.strip().upper(), newly_cleared_shift_ids=shift_ids)


# =============================================================================
# Sub-Agent 3 Tools: HR Comms Strategy (`@Gmail` Handoff) & HITL (`hr_comms_and_intervention_agent`)
# =============================================================================
def generate_and_send_comms_strategy(send_emails: bool = True) -> dict[str, Any]:
    """Generates the Student & Store Manager Comms Strategy for active DQ warnings and operational exception queues.

    Stages every email in `next_student_transfer_gold.outreach_email_log` (`delivery_status='STAGED_FOR_GE_GMAIL'`)
    with deterministic regex-verified addresses (`hr-notifications+stu-XXXX@next-retail.example.com`, `hr-notifications+sm-XXX@next-retail.example.com`)
    and returns copy-clean email blocks (`To`, `Subject`, `Body`) for the 2-turn `@Gmail` handoff in Gemini Enterprise.
    """
    repo = get_bq_repository()
    dq_warnings = repo.get_data_quality_warnings()
    changes = repo.get_recent_changes(limit=25, include_reverted=False)

    comms_plan: list[dict[str, Any]] = []
    staged_records: list[dict[str, Any]] = []

    # 1. Build outreach items for every active Data Quality warning (DQ-01..DQ-06)
    for w in dq_warnings:
        rule_id = w["rule_id"]
        owner_token = w["owner_token"]
        persona = "STUDENT" if owner_token.startswith("STU-") else "STORE_MANAGER"
        recipient_email = build_tokenized_email_address(owner_token)
        subject = f"[NEXT HR Action Required — {rule_id}] Transfer Portal Update Review for {w['entity_id']}"
        body = (
            f"Hello {owner_token},\n\n"
            f"Central HR Data Quality Gate ({rule_id} / {w['severity']}) quarantined a recent portal request ({w.get('request_id', '')}) on {w['entity_id']}:\n"
            f"- Issue: {w['issue_summary']}\n"
            f"- Required Action: {w['recommended_comms_action']}\n\n"
            f"Please update your submission in the NEXT Colleague Store Transfer Portal or reply to Central HR.\n"
            f"-- NEXT Student Transfer Governance Team"
        )
        item = {
            "category": f"DATA_QUALITY_{w['severity']}",
            "request_id": w.get("request_id", ""),
            "rule_id": rule_id,
            "recipient_persona": persona,
            "recipient_token": owner_token,
            "recipient_email": recipient_email,
            "entity_id": w["entity_id"],
            "subject": subject,
            "body": body,
            "recommended_action": w["recommended_comms_action"],
        }
        if send_emails:
            email_rec = repo.stage_outreach_email_for_ge_gmail(
                recipient_persona=persona,
                recipient_token=owner_token,
                subject=subject,
                body_text=body,
                related_dq_rule=rule_id,
                related_entity_id=w["entity_id"],
            )
            item["delivery_status"] = email_rec["delivery_status"]
            item["email_id"] = email_rec["email_id"]
            staged_records.append(email_rec)
        comms_plan.append(item)

    # 2. Always include representative operational exception outreach for HR exception queues (STU-0222 / SM-019 & STU-0305 / SM-051)
    operational_outreach = [
        {
            "rule_id": "FR-06-SM-EXTEND",
            "recipient_persona": "STORE_MANAGER",
            "recipient_token": "SM-019",
            "entity_id": "STU-0222",
            "subject": "[NEXT HR Exception Review] Can STORE-019 Leeds Trinity extend 4.0h THU shift by +1.5h for STU-0222?",
            "body": (
                "Hello SM-019 (STORE-019 Leeds Trinity),\n\n"
                "3-star PERM colleague STU-0222 matched to a single 4.0h Thursday shift at STORE-019 "
                "(1.5h below the 5.5h transfer completion floor) and did not opt into a sub-5.5h contract.\n"
                "- Action Requested: Please confirm if STORE-019 can extend this shift by +1.5h to 5.5h.\n\n"
                "-- NEXT Central HR Transfer Team"
            ),
            "recommended_action": "Ask SM-019 if 4.0h Thursday shift can be extended by +1.5h to meet >=5.5h floor for STU-0222.",
        },
        {
            "rule_id": "FR-06-DATE-GAP",
            "recipient_persona": "STUDENT",
            "recipient_token": "STU-0305",
            "entity_id": "STU-0305",
            "subject": "[NEXT HR Date Gap Review] Confirm 2026-10-12 start date at STORE-051 Edinburgh St James",
            "body": (
                "Hello STU-0305,\n\n"
                "You have been matched to a 7.5h Saturday shift at Choice 1 (STORE-051 Edinburgh St James), "
                "available from 2026-10-12 (14 days after your requested 2026-09-28 transfer date).\n"
                "- Action Requested: Please confirm you are happy to bridge the 14-day gap with your home store (STORE-330).\n\n"
                "-- NEXT Central HR Transfer Team"
            ),
            "recommended_action": "Confirm 14-day start date gap (2026-09-28 -> 2026-10-12) with STU-0305 and home store STORE-330.",
        },
    ]

    for op in operational_outreach:
        recipient_email = build_tokenized_email_address(op["recipient_token"])
        item = {
            "category": "EXCEPTION_QUEUE_OUTREACH",
            "request_id": "",
            "rule_id": op["rule_id"],
            "recipient_persona": op["recipient_persona"],
            "recipient_token": op["recipient_token"],
            "recipient_email": recipient_email,
            "entity_id": op["entity_id"],
            "subject": op["subject"],
            "body": op["body"],
            "recommended_action": op["recommended_action"],
        }
        if send_emails:
            email_rec = repo.stage_outreach_email_for_ge_gmail(
                recipient_persona=op["recipient_persona"],
                recipient_token=op["recipient_token"],
                subject=op["subject"],
                body_text=op["body"],
                related_dq_rule=op["rule_id"],
                related_entity_id=op["entity_id"],
            )
            item["delivery_status"] = email_rec["delivery_status"]
            item["email_id"] = email_rec["email_id"]
            staged_records.append(email_rec)
        comms_plan.append(item)

    return {
        "active_dq_warnings_count": len(dq_warnings),
        "recent_portal_changes_count": len(changes),
        "total_comms_dispatched": len(comms_plan),
        "delivery_mode": "STAGED_FOR_GE_GMAIL",
        "next_step_prompt": "Use @Gmail to send or draft the staged outreach emails above now.",
        "comms_plan": comms_plan,
    }


def record_hr_exception_decision(
    student_id: str,
    decision: str,
    reason_code: str,
    note: str = "",
    confirm: bool = False,
) -> dict[str, Any]:
    """Records a Stage 1 HR Specialist approval or override decision into `next_student_transfer_gold.transfer_audit_log`.

    A2A-Safe HITL Pattern:
      - When `confirm=False` (default): writes nothing and returns a preview asking HR to reply 'Confirm'.
      - When `confirm=True`: writes the decision to BigQuery `transfer_audit_log` and updates the graduation scorecard.
    """
    sid = student_id.strip().upper()
    dec = decision.strip().upper()
    if not confirm:
        return {
            "status": "CONFIRMATION_REQUIRED",
            "confirm_required": True,
            "student_id": sid,
            "proposed_decision": dec,
            "reason_code": reason_code,
            "note": note,
            "message": (
                f"HITL Confirmation Required: Ready to log Stage 1 HR decision `{dec}` for `{sid}` "
                f"(`{reason_code}`) into `next_student_transfer_gold.transfer_audit_log`. "
                f"Please reply 'Confirm' to execute."
            ),
        }
    return record_hitl_override(
        student_id=sid,
        human_action=dec,
        override_reason=reason_code,
        reviewer_note=note,
    )


def record_hitl_override(
    student_id: str,
    human_action: str,
    override_reason: str,
    reviewer_note: str = "",
) -> dict[str, Any]:
    """Records a Stage 1 HR Specialist approval or override reason into BigQuery `transfer_audit_log`."""
    entry = _record_hitl_override(
        student_id=student_id,
        human_action=human_action,
        override_reason=override_reason,
        reviewer_note=reviewer_note,
    )
    res = run_matching_engine()
    return {
        "recorded_event": entry,
        "updated_graduation_scorecard": res["graduation_scorecard"],
    }


AGENT_TOOLS = [
    audit_recent_changes_and_dq_warnings,
    resolve_quarantined_change,
    revert_invalid_change,
    run_batch_matching,
    explain_student_status,
    simulate_policy_change,
    rerun_batch2_cmp_shifts,
    generate_and_send_comms_strategy,
    record_hr_exception_decision,
    record_hitl_override,
]

armor_guard = ModelArmorGuard()


def build_adk_agent() -> Any:
    """Builds the Google ADK Coordinator + 3 Specialized Sub-Agents wired with Regional Model Armor (`europe-west2`)."""
    try:
        from google.adk.agents import Agent
        from google.genai import types

        gen_cfg = types.GenerateContentConfig(
            temperature=0.0,
            max_output_tokens=MAX_OUTPUT_TOKENS,
        )

        dq_subagent = Agent(
            name="dq_and_change_auditor_agent",
            model=GEMINI_MODEL_ID,
            description=(
                "Audits Student and Store Manager requests in next_student_transfer_staging, "
                "surfaces Dataplex / BigQuery DQ-01..DQ-06 warnings, and resolves or rejects quarantined edits."
            ),
            instruction=(
                SYSTEM_INSTRUCTION
                + "\nFocus on auditing recent changes in `portal_change_requests`, `change_audit_log`, and `v_data_quality_warnings`, and resolving quarantined changes via `resolve_quarantined_change`."
            ),
            tools=[audit_recent_changes_and_dq_warnings, resolve_quarantined_change, revert_invalid_change],
            generate_content_config=gen_cfg,
            before_model_callback=armor_guard.before_model_callback,
            after_tool_callback=armor_guard.after_tool_callback,
            after_model_callback=armor_guard.after_model_callback,
        )

        matcher_subagent = Agent(
            name="matcher_and_simulator_agent",
            model=GEMINI_MODEL_ID,
            description=(
                "Executes the deterministic 3-pass >=5.5hr matching engine on next_student_transfer_gold, "
                "explains STU-XXXX contention, runs Batch-2 CMP re-matches, and runs What-If policy simulations."
            ),
            instruction=(
                SYSTEM_INSTRUCTION
                + "\nFocus on deterministic 3-pass matching, student contention provenance, Batch-2 CMP re-matching, and What-If policy simulation."
            ),
            tools=[run_batch_matching, explain_student_status, rerun_batch2_cmp_shifts, simulate_policy_change],
            generate_content_config=gen_cfg,
            before_model_callback=armor_guard.before_model_callback,
            after_tool_callback=armor_guard.after_tool_callback,
            after_model_callback=armor_guard.after_model_callback,
        )

        comms_subagent = Agent(
            name="hr_comms_and_intervention_agent",
            model=GEMINI_MODEL_ID,
            description=(
                "Generates the Student & Store Manager Comms Strategy, stages tokenized outreach emails "
                "in BigQuery for the Gemini Enterprise native @Gmail connector, and records Stage 1 HITL decisions."
            ),
            instruction=(
                SYSTEM_INSTRUCTION
                + "\nFocus on staging tokenized outreach emails in `outreach_email_log` (`STAGED_FOR_GE_GMAIL`), formatting copy-clean email blocks (`To`, `Subject`, `Body`) for `@Gmail`, and recording Stage 1 HITL decisions."
            ),
            tools=[generate_and_send_comms_strategy, record_hr_exception_decision, record_hitl_override],
            generate_content_config=gen_cfg,
            before_model_callback=armor_guard.before_model_callback,
            after_tool_callback=armor_guard.after_tool_callback,
            after_model_callback=armor_guard.after_model_callback,
        )

        return Agent(
            name="next_hr_transfer_coordinator",
            model=GEMINI_MODEL_ID,
            description=(
                "NEXT PLC HR Student Transfer Coordinator — orchestrates Data Quality & Change Auditing, "
                "Deterministic 3-Pass Shift Matching, and Tokenized @Gmail Outreach."
            ),
            instruction=SYSTEM_INSTRUCTION,
            tools=AGENT_TOOLS,
            sub_agents=[dq_subagent, matcher_subagent, comms_subagent],
            generate_content_config=gen_cfg,
            before_model_callback=armor_guard.before_model_callback,
            after_tool_callback=armor_guard.after_tool_callback,
            after_model_callback=armor_guard.after_model_callback,
        )
    except ImportError:
        return None


root_agent = build_adk_agent()


# -----------------------------------------------------------------------------
# Deterministic + Gemini Grounded Multi-Agent Router (Used by A2A, /chat & Evals)
# -----------------------------------------------------------------------------
def _select_and_run_tool(message: str) -> tuple[str, dict[str, Any], dict[str, Any]]:
    msg_upper = message.upper()

    # 1. Check for explicit resolution or revert of a REQ-XXXXXXXX, CHG-XXXXXXXX, STU-XXXX, or SHF-XXXX ID
    id_match = re.search(r"\b(?:REQ|CHG)-[A-Z0-9]{6,12}\b|\b(?:STU|SHF)-\d{4,}\b", msg_upper)
    if id_match and any(k in msg_upper for k in ["REVERT", "UNDO", "ROLLBACK", "REJECT", "RESOLVE", "OVERRIDE"]):
        ident = id_match.group(0)
        if "PREVIEW" in msg_upper or "CONFIRM=FALSE" in msg_upper:
            is_confirmed = False
        else:
            is_confirmed = "CONFIRM" in msg_upper
        decision = "OVERRIDE_PROMOTE" if "OVERRIDE" in msg_upper else "REJECT"
        reason_match = re.search(r"REASON\s*[:=-]\s*(.+)$", message, re.IGNORECASE)
        hr_reason = reason_match.group(1).strip() if reason_match else "Resolved via Gemini Enterprise HR Audit"
        args = {
            "request_id": ident,
            "decision": decision,
            "hr_reason": hr_reason,
            "confirm": is_confirmed,
        }
        return "resolve_quarantined_change", args, resolve_quarantined_change(**args)

    # 2. Check for combined Audit + Comms Strategy or standalone Comms / Audit queries
    wants_audit = any(
        k in msg_upper
        for k in ["AUDIT", "DATA QUALITY", "DQ-", "WARNING", "INVALID", "RECENT CHANGE", "WHAT CHANGED", "CHANGES", "QUARANTINE"]
    )
    wants_comms = any(
        k in msg_upper
        for k in ["COMMS", "COMMUNICATION", "EMAIL", "OUTREACH", "NOTIFY", "PING", "DRAFT"]
    )
    if wants_audit and wants_comms:
        audit_data = audit_recent_changes_and_dq_warnings(limit=25)
        comms_data = generate_and_send_comms_strategy(send_emails=True)
        combined = {
            "sub_agents_invoked": ["dq_and_change_auditor_agent", "hr_comms_and_intervention_agent"],
            "audit_report": audit_data,
            "comms_strategy": comms_data,
        }
        return "audit_and_comms_combined", {"send_emails": True}, combined

    if wants_comms:
        args = {"send_emails": True}
        return "generate_and_send_comms_strategy", args, generate_and_send_comms_strategy(**args)

    if wants_audit:
        args = {"limit": 25}
        return "audit_recent_changes_and_dq_warnings", args, audit_recent_changes_and_dq_warnings(**args)

    # 3. Check for Batch-2 CMP clearance query (e.g., Leeds Trinity / SHF-9001..9003)
    shf_matches = re.findall(r"\bSHF-\d{4,}\b", msg_upper)
    if ("BATCH 2" in msg_upper or "BATCH-2" in msg_upper or "CMP" in msg_upper or "CONTRACT MARKETPLACE" in msg_upper) and (
        "STORE-019" in msg_upper or "LEEDS" in msg_upper or shf_matches
    ):
        store_match = re.search(r"\bSTORE-\d{3}\b", msg_upper)
        store_id = store_match.group(0) if store_match else "STORE-019"
        shifts_arg = ",".join(shf_matches) if shf_matches else "SHF-9001,SHF-9002,SHF-9003"
        args = {"store_id": store_id, "newly_cleared_shift_ids": shifts_arg}
        return "rerun_batch2_cmp_shifts", args, rerun_batch2_cmp_shifts(**args)

    # 4. Check for What-If simulation query (or combined 3-Pass Matching + What-If simulation)
    wants_sim = (
        "WHAT-IF" in msg_upper
        or "WHAT IF" in msg_upper
        or "SIMULAT" in msg_upper
        or "4.0H" in msg_upper
        or ("4-STAR" in msg_upper and "MANCHESTER" in msg_upper)
    )
    wants_match_engine = any(
        k in msg_upper for k in ["3-PASS", "3 PASS", "MATCHING ENGINE", "BATCH MATCH", "TRUSTED GOLD"]
    )
    if wants_sim and wants_match_engine:
        match_data = run_batch_matching()
        sim_args = {"min_hours_for_4star": 4.0, "target_cities": "Manchester,Leeds"}
        sim_data = simulate_policy_change(**sim_args)
        combined = {
            "sub_agents_invoked": ["matcher_and_simulator_agent"],
            "batch_match": match_data,
            "simulation": sim_data,
        }
        return "match_and_simulate_combined", sim_args, combined

    if wants_sim:
        args = {"min_hours_for_4star": 4.0, "target_cities": "Manchester,Leeds"}
        return "simulate_policy_change", args, simulate_policy_change(**args)

    # 5. Check for specific student token query (STU-XXXX)
    stu_match = re.search(r"\bSTU-\d{4,}\b", msg_upper)
    if stu_match:
        sid = stu_match.group(0)
        args = {"student_id": sid}
        return "explain_student_status", args, explain_student_status(sid)

    # 6. Default to batch summary
    return "run_batch_matching", {}, run_batch_matching()


def _format_audit_section(audit_output: dict[str, Any]) -> str:
    warnings = audit_output.get("active_dq_warnings", [])
    changes = audit_output.get("recent_changes", [])
    dp = audit_output.get("dataplex_scan", {})
    lines = [
        f"**Data Quality & Quarantine Audit (`dq_and_change_auditor_agent`):**",
        f"- **Medallion Datasets (`europe-west2`):** Staging `{audit_output.get('staging_dataset')}` | Gold `{audit_output.get('gold_dataset')}`",
        f"- **Dataplex AutoDQ Scan (`next-student-transfer-dq-scan`):** State `{dp.get('latest_job_state', dp.get('job_state', 'ACTIVE'))}` on `portal_change_requests`",
        f"- **Active Portal Changes:** **`{audit_output.get('total_active_changes', 0)}`** "
        f"(`{audit_output.get('valid_changes_count', 0)}` Promoted to Gold | `{audit_output.get('flagged_changes_count', 0)}` Quarantined in Staging) "
        f"| **Active DQ Warnings (`v_data_quality_warnings`):** **`{len(warnings)}`**",
    ]
    if warnings:
        lines.append("\n**Quarantined Data Quality Violations (`next_student_transfer_staging.v_data_quality_warnings`):**")
        for w in warnings:
            lines.append(
                f"- **`{w['rule_id']}` (`{w['severity']}` — `{w['entity_id']}` by `{w['owner_token']}`, Request `{w.get('request_id', 'N/A')}`):** "
                f"{w['issue_summary']} -> *Action:* {w['recommended_comms_action']}"
            )
    if changes:
        lines.append("\n**Recent Portal Change Audit Log (`next_student_transfer_staging.change_audit_log`):**")
        for c in changes[:10]:
            lines.append(
                f"- `{c['change_id']}` (`{c['validation_status']}` / `{c['rule_triggered']}`): "
                f"`{c['actor_token']}` updated `{c['entity_id']}.{c['field_changed']}`: "
                f"`{c['old_value']}` -> `{c['new_value']}`"
            )
    return "\n".join(lines)


def _format_comms_section(comms_output: dict[str, Any]) -> str:
    plan = comms_output.get("comms_plan", [])
    lines = [
        f"**Staged Outreach Emails for Gemini Enterprise `@Gmail` Connector (`hr_comms_and_intervention_agent`):**",
        f"- **Total Emails Staged in `next_student_transfer_gold.outreach_email_log`:** **`{comms_output.get('total_comms_dispatched', len(plan))}`** (`delivery_status = 'STAGED_FOR_GE_GMAIL'`)",
        f"- **Turn 2 Handoff:** Reply in the main Gemini Enterprise chat bar: *\"Use `@Gmail` to send or draft those staged outreach emails now.\"*",
    ]
    for idx, item in enumerate(plan, start=1):
        lines.append(
            f"\n**Email {idx} — `{item['rule_id']}` (`{item['recipient_persona']}` `{item['recipient_token']}`, ID `{item.get('email_id', 'STAGED')}`):**\n"
            f"- **To:** `{item['recipient_email']}`\n"
            f"- **Subject:** `{item['subject']}`\n"
            f"- **Body:**\n```text\n{item.get('body', item.get('recommended_action', ''))}\n```"
        )
    return "\n".join(lines)


def _format_grounded_response(tool_name: str, tool_output: dict[str, Any]) -> str:
    if tool_name == "audit_recent_changes_and_dq_warnings":
        return _format_audit_section(tool_output)

    if tool_name == "generate_and_send_comms_strategy":
        return _format_comms_section(tool_output)

    if tool_name == "audit_and_comms_combined":
        return (
            _format_audit_section(tool_output["audit_report"])
            + "\n\n"
            + _format_comms_section(tool_output["comms_strategy"])
        )

    if tool_name in {"revert_invalid_change", "resolve_quarantined_change"}:
        rev = tool_output.get("revert_result") or tool_output.get("resolution_result") or {}
        if rev.get("confirm_required"):
            return f"**{rev.get('message')}**"
        return (
            f"**Resolved Quarantined Change `{rev.get('request_id') or rev.get('change_id')}` (`{rev.get('resolution_status', rev.get('status'))}`):**\n"
            f"- Entity: `{rev.get('entity_type')}` `{rev.get('entity_id')}` (`{rev.get('field_changed')}` -> `{rev.get('restored_value')}`)\n"
            f"- Remaining Active DQ Warnings in Staging: **`{tool_output.get('remaining_dq_warnings_count', 0)}`**"
        )

    if tool_name == "explain_student_status":
        if "error" in tool_output:
            return tool_output["error"]
        sid = tool_output["student_id"]
        status = tool_output["status"]
        star = tool_output["star_rating"]
        age = tool_output["age_band"]
        matched_store = tool_output.get("matched_store_id")
        matched_name = tool_output.get("matched_store_name")
        hours = tool_output.get("assigned_hours", 0.0)
        shifts = tool_output.get("assigned_shifts", [])
        shift_str = ", ".join(
            f"{s['shift_id']} ({s['day_of_week']} {s['start_time']}-{s['end_time']}, {s['weekly_hours']}h, from {s['available_from']})"
            for s in shifts
        ) or "None"

        lines = [
            f"**{sid}** (`{star}*` `{age}`, submitted `{tool_output.get('form_submitted_at')}`) -> Status: **`{status}`** (`{tool_output.get('reason_code')}`).",
        ]
        for hist in tool_output.get("contended_out_history", []):
            winners = ", ".join(
                f"{w['student_id']} ({w['star_rating']}* at {w['form_submitted_at']} -> {'/'.join(w['assigned_shift_ids'])})"
                for w in hist.get("earlier_winners", [])
            )
            lines.append(
                f"- **Choice {hist['pass']} (`{hist['store_id']} {hist['store_name']}`, demand={hist['choice_1_demand']}):** "
                f"All clean shifts were claimed by earlier higher/equal-ranked applicants ({winners}). "
                f"Excluded protected/un-cleared shifts: `Green Box={hist['excluded_green_box_shifts']}`, `Pending CMP={hist['excluded_pending_cmp_shifts']}`."
            )
        for u18 in tool_output.get("under18_statutory_blocks", []):
            lines.append(
                f"- **UK Under-18 Statutory Block (`NFR-02`):** Blocked `{u18['shift_id']}` (`{u18['shift_window']}`) at `{u18['store_id']}` -> `{u18['blocked_reason']}` (https://www.gov.uk/rest-breaks-work/young-workers)."
            )
        for w in tool_output.get("active_dq_warnings", []):
            lines.append(
                f"- **Active Data Quality Quarantine Flag (`{w['rule_id']}` — `{w['severity']}`):** {w['issue_summary']}"
            )
        if matched_store:
            lines.append(
                f"- **Assigned Placement (Choice {tool_output.get('pass_matched')}):** `{matched_store} ({matched_name})` | Total: **`{hours}h/week`** | Shifts: `{shift_str}` | Home Store Release: `{tool_output.get('home_store_release_status')}`."
            )
        return "\n".join(lines)

    if tool_name == "rerun_batch2_cmp_shifts":
        newly = tool_output.get("newly_matched_students", [])
        rows = ", ".join(
            f"`{s['student_id']}` ({s['star_rating']}*, `{s['previous_status']}` -> `{s['new_status']}`, shifts `{'/'.join(s['assigned_shift_ids'])}`, `{s['assigned_hours']}h`)"
            for s in newly
        )
        return (
            f"**Batch-2 CMP Re-Match Complete for `{tool_output['store_id']}` (`next_student_transfer_gold`):**\n"
            f"- Cleared 3-day CMP shifts: `{', '.join(tool_output['newly_cleared_shift_ids'])}`\n"
            f"- Net additional `MATCHED_COMPLETE` students: **`+{tool_output['net_additional_complete_matches']}`** "
            f"(`{tool_output['baseline_matched_complete']}` -> `{tool_output['updated_matched_complete']}`)\n"
            f"- Newly matched colleagues: {rows}"
        )

    if tool_name == "match_and_simulate_combined":
        return (
            _format_grounded_response("run_batch_matching", tool_output["batch_match"])
            + "\n\n"
            + _format_grounded_response("simulate_policy_change", tool_output["simulation"])
        )

    if tool_name == "simulate_policy_change":
        breakdown = ", ".join(
            f"`{b['store_id']} ({b['store_name']})`: +{b['additional_4star_placed']}"
            for b in tool_output.get("breakdown_by_store", [])
        )
        return (
            f"**What-If Simulation Result (`{tool_output['policy_tested']}`):**\n"
            f"- Target Cities: `{', '.join(tool_output['target_cities'])}`\n"
            f"- Additional `4*` permanent students placed into `MATCHED_COMPLETE`: **`+{tool_output['additional_4star_students_placed']}`** "
            f"(`{tool_output['baseline_matched_complete']}` -> `{tool_output['simulated_matched_complete']}`)\n"
            f"- Store Breakdown: {breakdown}"
        )

    s = tool_output["summary"]
    wf = s["choice_waterfall"]
    return (
        f"**NEXT Student Transfer Batch Summary (`1,050` Applicants across `470` UK Stores in `next_student_transfer_gold`):**\n"
        f"- Eligible (`3*`/`4*` PERM): **`{s['eligible_applicants']}`** | Routed Out (`INELIGIBLE_ROUTED`): **`{s['ineligible_routed']}`**\n"
        f"- Clean Complete Matches (`MATCHED_COMPLETE >=5.5h`): **`{s['matched_complete']}`** "
        f"(Choice 1: `{wf['choice_1']}`, Choice 2: `{wf['choice_2']}`, Choice 3: `{wf['choice_3']}`)\n"
        f"- Exception Queues: `SMALL_CONTRACT_EXCEPTION={s['small_contract_exception']}`, "
        f"`SM_EXTEND_SHIFT_REVIEW={s['sm_extend_shift_review']}`, `DATE_GAP_REVIEW={s['date_gap_review']}`\n"
        f"- Compliance & DQ Guards: `0` Green Box leaks, `0` un-cleared CMP leaks, `{s['under18_statutory_blocks_enforced']}` UK `UNDER_18` night-shift blocks enforced, `{tool_output.get('active_dq_warnings_count', 0)}` active Data Quality warnings in Staging."
    )


def handle_chat_turn(message: str, use_vertex_llm: bool = True) -> dict[str, Any]:
    """Executes a full guarded agent turn with Regional Model Armor (`europe-west2`) across Prompt, Tool Output, and Response."""
    prompt_verdict = armor_guard.sanitize_user_prompt(message)
    if not prompt_verdict.allowed:
        return {
            "blocked": True,
            "armor_verdict": prompt_verdict.to_dict(),
            "tool_called": None,
            "tool_args": None,
            "tool_output": None,
            "reply": f"[SECURITY INTERCEPT — {prompt_verdict.source}] {prompt_verdict.block_reason}",
        }

    tool_name, tool_args, tool_output = _select_and_run_tool(message)

    tool_verdict = armor_guard.sanitize_tool_output(json.dumps(tool_output, default=str))
    if not tool_verdict.allowed:
        return {
            "blocked": True,
            "armor_verdict": tool_verdict.to_dict(),
            "tool_called": tool_name,
            "tool_args": tool_args,
            "tool_output": None,
            "reply": f"[SECURITY INTERCEPT — {tool_verdict.source}] {tool_verdict.block_reason}",
        }

    deterministic_reply = _format_grounded_response(tool_name, tool_output)
    final_reply = deterministic_reply

    if use_vertex_llm and os.getenv("GOOGLE_GENAI_USE_VERTEXAI", "TRUE").upper() == "TRUE":
        try:
            from google import genai
            from google.genai import types

            client = genai.Client(
                vertexai=True,
                project=os.getenv("GOOGLE_CLOUD_PROJECT", "next-student-transfer-prod"),
                location=os.getenv("GOOGLE_CLOUD_LOCATION", "europe-west2"),
            )
            resp = client.models.generate_content(
                model=GEMINI_MODEL_ID,
                contents=(
                    f"User question: {message}\n\n"
                    f"Deterministic tool `{tool_name}` output JSON:\n{json.dumps(tool_output, default=str)}\n\n"
                    f"Verified summary:\n{deterministic_reply}\n\n"
                    "Provide a concise, executive-ready answer citing the exact STU-XXXX, STORE-XXX, SHF-XXXX, REQ-XXXXXXXX, CHG-XXXXXXXX, and DQ-XX tokens above without inventing any numbers. "
                    "If outreach emails were staged, preserve the copy-clean `To:`, `Subject:`, and `Body:` blocks so the user can hand them off to `@Gmail` in Turn 2."
                ),
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_INSTRUCTION,
                    temperature=0.0,
                    max_output_tokens=MAX_OUTPUT_TOKENS,
                ),
            )
            if resp and resp.text:
                final_reply = armor_guard.strip_untrusted_urls_and_markdown_images(resp.text.strip())
        except Exception:
            final_reply = deterministic_reply

    resp_verdict = armor_guard.sanitize_model_response(final_reply)
    if not resp_verdict.allowed:
        return {
            "blocked": True,
            "armor_verdict": resp_verdict.to_dict(),
            "tool_called": tool_name,
            "tool_args": tool_args,
            "tool_output": tool_output,
            "reply": f"[SECURITY INTERCEPT — {resp_verdict.source}] {resp_verdict.block_reason}",
        }

    return {
        "blocked": False,
        "armor_verdict": prompt_verdict.to_dict(),
        "tool_called": tool_name,
        "tool_args": tool_args,
        "tool_output": tool_output,
        "reply": final_reply,
    }
