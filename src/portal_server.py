"""FastAPI server for Cloud Run App 1: Student & Store Manager Self-Service Demo Portal.

Strictly segregated from the HR Gemini Enterprise Agent (Cloud Run App 2):
- Allows students (`STU-XXXX`) to update store choices, transfer dates / continuity gap, and working hours window.
- Allows store managers (`SM-XXX`) to update shift hours, Green Box protection, and 3-day internal CMP clearance.
- Provides a collapsible Presenter Stage Dock (gated behind `DEMO_MODE=true`) with 1-Click Scenario Injectors:
  * Scenario 1 (`1`): Valid 3-day CMP clearance (`SM-019` on `SHF-9001..9003`) -> `PROMOTED` to `store_shifts_gold`.
  * Scenario 2 (`2`): Duplicate store (`DQ-01`) + Under-18 `23:30` curfew (`DQ-06`) -> `QUARANTINED` in `portal_change_requests`.
  * Scenario 3 (`3`): `3.0h` sub-minimum shift (`DQ-03`) + Green Box unlock (`DQ-05`) -> `QUARANTINED` in `portal_change_requests`.
  * Scenario 4 (`R`): Reset staging & demo state (append-only `DEMO_RESET` audit event).
- Triggers real Dataplex Auto Data Quality scan (`next-student-transfer-dq-scan`) asynchronously on every write.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from src.bq_repository import get_bq_repository

load_dotenv()

UI_HTML_PATH = Path(__file__).resolve().parents[1] / "ui" / "portal_intake.html"


class StudentPreferenceUpdateRequest(BaseModel):
    actor_token: str = "STU-0412"
    choice_1_store_id: Optional[str] = None
    choice_2_store_id: Optional[str] = None
    choice_3_store_id: Optional[str] = None
    preferred_transfer_date: Optional[str] = None
    target_transfer_date: Optional[str] = None
    transfer_gap_days: Optional[int] = None
    preferred_latest_finish: Optional[str] = None
    wants_night_shift_ending_after_2200: Optional[bool] = None
    available_days: Optional[str] = None
    small_contract_opt_in: Optional[bool] = None
    note: str = ""


class StoreShiftUpdateRequest(BaseModel):
    actor_token: str = "SM-019"
    weekly_hours: Optional[float] = None
    is_green_box_protected: Optional[bool] = None
    cmp_3day_cleared: Optional[bool] = None
    note: str = ""


app = FastAPI(
    title="NEXT PLC — Colleague Store Transfers Self-Service Portal (App 1)",
    version="2.1.0",
    description=(
        "Cloud Run App 1: Self-Service Portal for Students (STU-XXXX) and Store Managers (SM-XXX) "
        "writing to BigQuery Medallion (`next_student_transfer_staging` & `next_student_transfer_gold`) "
        "with asynchronous Dataplex Auto Data Quality (`next-student-transfer-dq-scan`) and `0ms` "
        "`v_data_quality_warnings` quarantine evaluation."
    ),
)


@app.get("/", response_class=HTMLResponse)
@app.get("/ui", response_class=HTMLResponse)
def serve_portal_ui() -> HTMLResponse:
    if not UI_HTML_PATH.exists():
        raise HTTPException(status_code=404, detail="ui/portal_intake.html not found")
    return HTMLResponse(content=UI_HTML_PATH.read_text(encoding="utf-8"))


@app.get("/healthz")
@app.get("/api/health")
def healthz() -> dict:
    return {
        "status": "ok",
        "service": "next-portal-intake-app",
        "role": "student-and-store-manager-intake-portal",
        "region": os.getenv("GOOGLE_CLOUD_LOCATION", "europe-west2"),
        "bq_staging_dataset": os.getenv("BQ_STAGING_DATASET_ID", "next_student_transfer_staging"),
        "bq_gold_dataset": os.getenv("BQ_GOLD_DATASET_ID", "next_student_transfer_gold"),
        "demo_mode": os.getenv("DEMO_MODE", "true").lower() in ("1", "true", "yes"),
    }


@app.get("/api/state")
def get_portal_state() -> dict:
    """Return spotlight student rows, spotlight store shift rows, directory stores, DQ warnings, and Dataplex state."""
    repo = get_bq_repository()
    students, shifts = repo.fetch_raw_students_and_shifts()

    spotlight_student_ids = {"STU-0412", "STU-0109", "STU-0221", "STU-0222", "STU-0305"}
    spotlight_shift_ids = {
        "SHF-9001",
        "SHF-9002",
        "SHF-9003",
        "SHF-1028",
        "SHF-1003",
        "SHF-1019",
        "SHF-1044",
    }

    stu_sub = [dict(s) for s in students if str(s.get("student_id")) in spotlight_student_ids]
    shf_sub = [dict(sh) for sh in shifts if str(sh.get("shift_id")) in spotlight_shift_ids]

    # Build top destination store directory for the Student Guided Flow
    featured_store_ids = {
        "STORE-042",
        "STORE-043",
        "STORE-012",
        "STORE-019",
        "STORE-051",
        "STORE-001",
        "STORE-008",
        "STORE-014",
        "STORE-025",
        "STORE-033",
    }
    store_rows = [sh for sh in shifts if str(sh.get("store_id")) in featured_store_ids]
    if not store_rows:
        store_rows = shifts[:30]

    shifts_by_store: dict[str, list[dict]] = {}
    for sh in shifts:
        shifts_by_store.setdefault(str(sh.get("store_id")), []).append(sh)

    store_directory = []
    seen_stores = set()
    for row in store_rows:
        sid = str(row.get("store_id"))
        if sid in seen_stores:
            continue
        seen_stores.add(sid)
        s_all = shifts_by_store.get(sid, [])
        open_shifts = [
            sh for sh in s_all
            if not bool(sh.get("is_green_box_protected")) and bool(sh.get("cmp_3day_cleared"))
        ]
        sample_shift = (
            f"{row.get('day_of_week')} {row.get('start_time')}–{row.get('end_time')} · "
            f"{float(row.get('weekly_hours', 0.0)):.1f} hrs"
        )
        store_directory.append({
            "store_id": sid,
            "store_name": str(row.get("store_name", "")),
            "region": str(row.get("region", "UK Retail")),
            "total_shifts": len(s_all),
            "open_cleared_shifts": len(open_shifts),
            "sample_window": sample_shift,
        })

    return {
        "status": "ok",
        "demo_mode": os.getenv("DEMO_MODE", "true").lower() in ("1", "true", "yes"),
        "spotlight_students": stu_sub,
        "spotlight_shifts": shf_sub,
        "store_directory": store_directory,
        "dq_warnings": repo.get_dq_warnings(limit=50),
        "portal_change_requests": repo.get_portal_change_requests(limit=20),
        "recent_changes": repo.get_recent_changes(limit=25),
        "recent_emails": repo.get_recent_outreach_emails(limit=15),
        "dataplex_scan": repo.get_latest_dataplex_scan_status(),
    }


@app.post("/api/student/{student_id}")
def update_student(student_id: str, req: StudentPreferenceUpdateRequest) -> dict:
    repo = get_bq_repository()
    try:
        res = repo.update_student_preferences(
            student_id=student_id,
            actor_token=req.actor_token,
            choice_1_store_id=req.choice_1_store_id,
            choice_2_store_id=req.choice_2_store_id,
            choice_3_store_id=req.choice_3_store_id,
            preferred_transfer_date=req.preferred_transfer_date,
            target_transfer_date=req.target_transfer_date,
            transfer_gap_days=req.transfer_gap_days,
            preferred_latest_finish=req.preferred_latest_finish,
            wants_night_shift_ending_after_2200=req.wants_night_shift_ending_after_2200,
            available_days=req.available_days,
            small_contract_opt_in=req.small_contract_opt_in,
            note=req.note,
        )
        scan_trigger = repo.trigger_dataplex_scan_async()
        res["dataplex_scan_trigger"] = scan_trigger
        return res
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/shift/{shift_id}")
def update_shift(shift_id: str, req: StoreShiftUpdateRequest) -> dict:
    repo = get_bq_repository()
    try:
        res = repo.update_store_shift(
            shift_id=shift_id,
            actor_token=req.actor_token,
            weekly_hours=req.weekly_hours,
            is_green_box_protected=req.is_green_box_protected,
            cmp_3day_cleared=req.cmp_3day_cleared,
            note=req.note,
        )
        scan_trigger = repo.trigger_dataplex_scan_async()
        res["dataplex_scan_trigger"] = scan_trigger
        return res
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/demo-scenario/{scenario_id}")
def trigger_demo_scenario(scenario_id: str) -> dict:
    repo = get_bq_repository()
    try:
        return repo.run_demo_scenario(scenario_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
