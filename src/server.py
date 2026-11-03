#!/usr/bin/env python3
"""Headless HR Multi-Agent A2A Server for Cloud Run App 2 (`next-student-transfer-agent`).

Registered on Gemini Enterprise (`gemini-enterprise-retail-app`) via A2A JSON-RPC 2.0 (`/a2a/app`),
backed 100% by Google Cloud BigQuery (`next_student_transfer_staging` & `next_student_transfer_gold` in `europe-west2`),
Dataplex Auto Data Quality (`next-student-transfer-dq-scan`), and Regional Model Armor (`europe-west2`).
"""

from __future__ import annotations

import json
import os
import uuid
from pathlib import Path
from typing import Any, Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

from src.agent import (
    GEMINI_MODEL_ID,
    armor_guard,
    audit_recent_changes_and_dq_warnings,
    generate_and_send_comms_strategy,
    handle_chat_turn,
    record_hr_exception_decision,
    resolve_quarantined_change,
    revert_invalid_change,
)
from src.bq_repository import get_bq_repository
from src.matcher import (
    compute_graduation_scorecard,
    explain_student_placement,
    record_hitl_override,
    rerun_batch2_cmp_for_store,
    run_matching_engine,
    run_what_if_simulation,
)

ROOT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(ROOT_DIR / ".env")

app = FastAPI(
    title="NEXT PLC HR Student Transfer Multi-Agent Service (App 2)",
    version="2.1.0",
    description=(
        "Headless HR Multi-Agent A2A Backend (`dq_and_change_auditor_agent`, "
        "`matcher_and_simulator_agent`, `hr_comms_and_intervention_agent`) backed by "
        "BigQuery (`next_student_transfer_staging` & `next_student_transfer_gold` in `europe-west2`), "
        "Dataplex Auto Data Quality (`next-student-transfer-dq-scan`), and Regional Model Armor (`europe-west2`)."
    ),
)


class MatchRequest(BaseModel):
    include_batch2_cmp: bool = Field(default=False, description="Whether to include newly cleared Batch-2 CMP shifts (SHF-9001..9003)")


class SimulateRequest(BaseModel):
    min_hours_for_4star: float = Field(default=4.0, description="Minimum single-shift hours for 4-star students with small_contract_opt_in=true")
    target_cities: list[str] = Field(default_factory=lambda: ["Manchester", "Leeds"])


class Batch2CmpRequest(BaseModel):
    store_id: str = Field(default="STORE-019")
    newly_cleared_shift_ids: list[str] = Field(default_factory=lambda: ["SHF-9001", "SHF-9002", "SHF-9003"])


class OverrideRequest(BaseModel):
    student_id: str = Field(..., description="Tokenized student ID (STU-XXXX)")
    human_action: str = Field(default="OVERRIDE", description="APPROVE or OVERRIDE")
    override_reason: str = Field(..., description="Structured reason for Stage 1 HITL decision")
    reviewer_note: str = Field(default="")
    agent_status: str = Field(default="SMALL_CONTRACT_EXCEPTION")
    confirm: bool = Field(default=True, description="HITL confirmation flag")


class RevertChangeRequest(BaseModel):
    change_id: str = Field(..., description="Change ID or Request ID in staging (CHG-XXXXXXXX / REQ-XXXXXXXX)")
    hr_reason: str = Field(default="Reverted by HR Data Quality Audit")
    decision: str = Field(default="REJECT")
    confirm: bool = Field(default=True)


class CommsRequest(BaseModel):
    send_emails: bool = Field(default=True, description="Whether to stage tokenized outreach emails in BigQuery for @Gmail")


class ChatRequest(BaseModel):
    message: str = Field(..., description="HR Specialist question via Gemini Enterprise")
    use_vertex_llm: bool = Field(default=True, description="Whether to format response with Vertex AI Gemini 2.5 Flash")


@app.get("/healthz")
def healthz() -> dict[str, Any]:
    repo = get_bq_repository()
    return {
        "status": "ok",
        "service": os.getenv("CLOUD_RUN_SERVICE_NAME", "next-student-transfer-agent"),
        "architecture_role": "APP_2_HEADLESS_HR_MULTI_AGENT_A2A",
        "sub_agents": [
            "dq_and_change_auditor_agent",
            "matcher_and_simulator_agent",
            "hr_comms_and_intervention_agent",
        ],
        "project_id": repo.project_id,
        "bq_staging_dataset": f"{repo.project_id}.{repo.staging_dataset_id}",
        "bq_gold_dataset": f"{repo.project_id}.{repo.gold_dataset_id}",
        "dataplex_scan": f"projects/{repo.project_id}/locations/{repo.location}/dataScans/{repo.dataplex_scan_id}",
        "region": os.getenv("GOOGLE_CLOUD_REGION", "europe-west2"),
        "model_id": GEMINI_MODEL_ID,
        "model_armor": {
            "enabled": os.getenv("ENABLE_MODEL_ARMOR", "1"),
            "location": armor_guard.location,
            "endpoint": armor_guard.api_endpoint,
            "template": armor_guard.template_resource,
        },
        "gemini_enterprise_app_id": os.getenv("GEMINI_ENTERPRISE_APP_ID", ""),
    }


@app.get("/audit-changes")
def audit_changes_endpoint(limit: int = 25) -> dict[str, Any]:
    return audit_recent_changes_and_dq_warnings(limit=limit)


@app.post("/revert-change")
def revert_change_endpoint(req: RevertChangeRequest) -> dict[str, Any]:
    if req.decision.upper() == "OVERRIDE_PROMOTE":
        return resolve_quarantined_change(
            request_id=req.change_id,
            decision=req.decision,
            hr_reason=req.hr_reason,
            confirm=req.confirm,
        )
    return revert_invalid_change(change_id=req.change_id, hr_reason=req.hr_reason, confirm=req.confirm)


@app.post("/comms-strategy")
def comms_strategy_endpoint(req: Optional[CommsRequest] = None) -> dict[str, Any]:
    send = True if req is None else req.send_emails
    return generate_and_send_comms_strategy(send_emails=send)


@app.post("/match")
def match_endpoint(req: Optional[MatchRequest] = None) -> dict[str, Any]:
    newly = {"SHF-9001", "SHF-9002", "SHF-9003"} if (req and req.include_batch2_cmp) else None
    result = run_matching_engine(newly_cleared_shift_ids=newly)
    placements = list(result["placements"].values())
    repo = get_bq_repository()

    return {
        "summary": result["summary"],
        "active_dq_warnings": repo.get_data_quality_warnings(),
        "top_contended_stores": result["top_contended_stores"],
        "under18_blocks": result["under18_blocks"],
        "graduation_scorecard": result["graduation_scorecard"],
        "hero_cases": [
            result["placements"][sid]
            for sid in ["STU-0412", "STU-0109", "STU-0221", "STU-0222", "STU-0305", "STU-0018", "STU-0049"]
            if sid in result["placements"]
        ],
        "queues": {
            "SMALL_CONTRACT_EXCEPTION": [p for p in placements if p["status"] == "SMALL_CONTRACT_EXCEPTION"],
            "SM_EXTEND_SHIFT_REVIEW": [p for p in placements if p["status"] == "SM_EXTEND_SHIFT_REVIEW"],
            "DATE_GAP_REVIEW": [p for p in placements if p["status"] == "DATE_GAP_REVIEW"],
            "MATCHED_COMPLETE": [p for p in placements if p["status"] == "MATCHED_COMPLETE"][:60],
            "UNMATCHED_WAITLIST": [p for p in placements if p["status"] == "UNMATCHED_WAITLIST"][:40],
            "INELIGIBLE_ROUTED": [p for p in placements if p["status"] == "INELIGIBLE_ROUTED"][:40],
        },
    }


@app.get("/student/{student_id}")
def get_student(student_id: str) -> dict[str, Any]:
    res = explain_student_placement(student_id)
    if "error" in res:
        raise HTTPException(status_code=404, detail=res["error"])
    return res


@app.post("/simulate")
def simulate_endpoint(req: SimulateRequest) -> dict[str, Any]:
    return run_what_if_simulation(
        min_hours_for_4star=req.min_hours_for_4star,
        target_cities=req.target_cities,
    )


@app.post("/batch2-cmp")
def batch2_cmp_endpoint(req: Batch2CmpRequest) -> dict[str, Any]:
    return rerun_batch2_cmp_for_store(
        store_id=req.store_id,
        newly_cleared_shift_ids=req.newly_cleared_shift_ids,
    )


@app.post("/override")
def override_endpoint(req: OverrideRequest) -> dict[str, Any]:
    verdict = armor_guard.sanitize_user_prompt(f"{req.student_id} {req.override_reason} {req.reviewer_note}")
    if not verdict.allowed:
        raise HTTPException(status_code=400, detail=verdict.block_reason)
    if not req.confirm:
        return record_hr_exception_decision(
            student_id=req.student_id,
            decision=req.human_action,
            reason_code=req.override_reason,
            note=req.reviewer_note,
            confirm=False,
        )
    entry = record_hitl_override(
        student_id=req.student_id,
        human_action=req.human_action,
        override_reason=req.override_reason,
        reviewer_note=req.reviewer_note,
        agent_status=req.agent_status,
    )
    engine_res = run_matching_engine()
    return {
        "status": "RECORDED",
        "entry": entry,
        "graduation_scorecard": compute_graduation_scorecard(
            matched_complete_count=engine_res["summary"]["matched_complete"],
            total_eligible_count=engine_res["summary"]["eligible_applicants"],
        ),
    }


@app.post("/chat")
def chat_endpoint(req: ChatRequest) -> JSONResponse:
    turn = handle_chat_turn(req.message, use_vertex_llm=req.use_vertex_llm)
    status_code = 400 if turn["blocked"] else 200
    return JSONResponse(status_code=status_code, content=turn)


@app.get("/a2a/app/.well-known/agent-card.json")
@app.get("/a2a/app/.well-known/agent.json")
@app.get("/.well-known/agent-card.json")
@app.get("/.well-known/agent.json")
def agent_card(request: Request) -> dict[str, Any]:
    base_url = str(request.base_url).rstrip("/")
    if ".run.app" in base_url and base_url.startswith("http://"):
        base_url = "https://" + base_url[len("http://"):]
    return {
        "name": "NEXT Student Transfer Assistant",
        "description": (
            "NEXT PLC HR Student Transfer Multi-Agent Assistant — orchestrates Data Quality & Quarantine Auditing "
            "(Dataplex AutoDQ + BigQuery Staging/Gold), Deterministic 3-Pass Shift Matching (1,050 students x 470 stores), "
            "and Staged @Gmail Outreach protected by Regional Model Armor (europe-west2)."
        ),
        "url": f"{base_url}/a2a/app",
        "version": "2.1.0",
        "protocolVersion": "0.2.1",
        "capabilities": {"streaming": False, "pushNotifications": False},
        "defaultInputModes": ["text", "text/plain", "application/json"],
        "defaultOutputModes": ["text", "text/plain", "application/json"],
        "skills": [
            {
                "id": "audit_recent_changes_and_dq_warnings",
                "name": "Audit Portal Changes & Dataplex DQ Quarantine",
                "description": "Audit recent Student and Store Manager requests in next_student_transfer_staging, flag DQ-01..DQ-06 quarantine rows, and stage copy-clean outreach emails for @Gmail.",
                "examples": [
                    "Audit all recent Student and Store Manager changes, flag any Data Quality warnings, and stage the outreach emails for @Gmail."
                ],
            },
            {
                "id": "explain_student_status",
                "name": "Explain Student Transfer Match & Contention",
                "description": "Explain why a tokenized student (e.g., STU-0412 or STU-0109) matched or cascaded across Choice 1/2/3 stores.",
                "examples": [
                    "Why wasn't STU-0412 placed into Choice 1 (STORE-042 Nottingham Victoria), and which store and shifts were they matched to instead?"
                ],
            },
            {
                "id": "rerun_batch2_cmp_shifts",
                "name": "Batch-2 3-Day CMP Clearance Re-Match",
                "description": "Re-run matching for a store when Scratchpad shifts clear the 3-day Contract Marketplace (e.g., SHF-9001..9003 at STORE-019 Leeds Trinity).",
                "examples": [
                    "Store Manager SM-019 at STORE-019 Leeds Trinity just confirmed shifts SHF-9001, SHF-9002, and SHF-9003 cleared the 3-day Contract Marketplace. Re-run Batch 2 for Leeds Trinity and show the newly matched students."
                ],
            },
            {
                "id": "simulate_policy_change",
                "name": "What-If Small Contract Policy Simulator",
                "description": "Simulate how many additional 4-star students in Manchester & Leeds get placed if 4-star students can opt into 4.0hr single shifts.",
                "examples": [
                    "How many additional 4-star students in Manchester & Leeds get placed if 4-star students can opt into 4.0hr single shifts?"
                ],
            },
        ],
    }


def _extract_a2a_user_text(payload: dict[str, Any]) -> tuple[str, bool]:
    params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
    msg = params.get("message") if isinstance(params.get("message"), dict) else {}
    parts = msg.get("parts") if isinstance(msg.get("parts"), list) else []

    texts: list[str] = []
    uses_kind = True
    for part in parts:
        if isinstance(part, dict):
            if "type" in part and "kind" not in part:
                uses_kind = False
            if isinstance(part.get("text"), str) and part["text"].strip():
                texts.append(part["text"].strip())
            elif isinstance(part.get("root"), dict) and isinstance(part["root"].get("text"), str):
                texts.append(part["root"]["text"].strip())
    if texts:
        return "\n".join(texts), uses_kind

    for fallback_key in ("message", "query", "text", "prompt"):
        val = params.get(fallback_key) or payload.get(fallback_key)
        if isinstance(val, str) and val.strip():
            return val.strip(), uses_kind
    return "Show summary of student transfer matching", uses_kind


@app.post("/a2a/app")
@app.post("/a2a/app/")
@app.post("/a2a")
async def a2a_jsonrpc_endpoint(request: Request):
    """Handle A2A JSON-RPC 2.0 requests from Gemini Enterprise."""
    try:
        payload = await request.json()
    except Exception:
        payload = {}

    if not isinstance(payload, dict):
        payload = {}

    if "jsonrpc" not in payload and "method" not in payload and isinstance(payload.get("message"), str):
        turn = handle_chat_turn(payload["message"], use_vertex_llm=bool(payload.get("use_vertex_llm", True)))
        return JSONResponse(status_code=200, content=turn)

    rpc_id = payload.get("id", str(uuid.uuid4()))
    method = str(payload.get("method", "message/send"))
    params = payload.get("params") if isinstance(payload.get("params"), dict) else {}
    msg_in = params.get("message") if isinstance(params.get("message"), dict) else {}

    task_id = str(params.get("id") or msg_in.get("taskId") or f"task-{uuid.uuid4()}")
    context_id = str(
        params.get("contextId")
        or params.get("sessionId")
        or msg_in.get("contextId")
        or f"ctx-{uuid.uuid4()}"
    )
    resp_msg_id = f"msg-{uuid.uuid4()}"
    artifact_id = f"art-{uuid.uuid4()}"

    user_text, uses_kind = _extract_a2a_user_text(payload)
    print(
        f"A2A_RPC method={method} id={rpc_id} task_id={task_id} context_id={context_id} uses_kind={uses_kind}",
        flush=True,
    )

    turn = handle_chat_turn(user_text, use_vertex_llm=bool(payload.get("use_vertex_llm", True)))
    answer_text = str(turn.get("reply") or turn.get("answer") or "Processed request.")

    part_obj: dict[str, Any] = {"kind": "text", "text": answer_text} if uses_kind else {"type": "text", "text": answer_text}

    agent_message: dict[str, Any] = {
        "kind": "message",
        "role": "agent",
        "messageId": resp_msg_id,
        "taskId": task_id,
        "contextId": context_id,
        "parts": [part_obj],
    }

    task_result: dict[str, Any] = {
        "kind": "task",
        "id": task_id,
        "contextId": context_id,
        "status": {
            "state": "completed",
            "message": agent_message,
        },
        "artifacts": [
            {
                "artifactId": artifact_id,
                "name": "response",
                "parts": [part_obj],
            }
        ],
    }

    rpc_response = {
        "jsonrpc": "2.0",
        "id": rpc_id,
        "result": task_result,
    }

    if method in ("message/stream", "tasks/sendSubscribe", "SendStreamingMessage"):
        async def _sse_gen():
            yield f"data: {json.dumps(rpc_response)}\n\n"

        return StreamingResponse(_sse_gen(), media_type="text/event-stream")

    return JSONResponse(status_code=200, content=rpc_response)
