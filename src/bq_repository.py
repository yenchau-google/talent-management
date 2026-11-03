"""Live Google Cloud BigQuery Medallion Repository & Dataplex Data Quality Guardrail Engine.

Two-Dataset Medallion Architecture (`europe-west2`):
  1. Bronze / Quarantine (`<GOOGLE_CLOUD_PROJECT>.next_student_transfer_staging`):
      - `portal_change_requests` (append-only self-service requests with flat denormalized DQ columns)
      - `change_audit_log` (strictly append-only event log; never DELETE/TRUNCATE)
      - `v_data_quality_warnings` (`0ms` real-time SQL view evaluating `DQ-01..DQ-06` on `QUARANTINED` requests)
      - Dataplex Auto Data Quality Scan (`next-student-transfer-dq-scan`) triggered asynchronously
  2. Trusted Gold (`<GOOGLE_CLOUD_PROJECT>.next_student_transfer_gold`):
      - `students_gold` & `students_gold_baseline` (`1,050` tokenized students)
      - `store_shifts_gold` & `store_shifts_gold_baseline` (`1,093` shifts across `470` UK stores)
      - `placements_staging`
      - `transfer_audit_log`
      - `outreach_email_log` (`STAGED_FOR_GE_GMAIL`)
      - `v_eligible_shifts_sql` & `sp_simulate_store_capacity`
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from google.cloud import bigquery


def _load_dotenv() -> None:
    env_path = Path(__file__).resolve().parents[1] / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


_load_dotenv()

_VALID_STUDENT_ID_RE = re.compile(r"^STU-\d{4}$")
_VALID_MANAGER_ID_RE = re.compile(r"^SM-\d{3}$")


def build_tokenized_email_address(token: str) -> str:
    """Deterministically build a zero-PII demo email alias from a regex-verified STU-XXXX or SM-XXX token."""
    clean = token.strip().upper()
    if not (_VALID_STUDENT_ID_RE.match(clean) or _VALID_MANAGER_ID_RE.match(clean)):
        raise ValueError(
            f"Invalid colleague token '{token}': must strictly match ^STU-\\d{{4}}$ or ^SM-\\d{{3}}$."
        )
    base_email = os.environ.get("DEMO_EMAIL_BASE", "hr-notifications@next-retail.example.com").strip()
    if "@" not in base_email:
        base_email = "hr-notifications@next-retail.example.com"
    local_part, domain = base_email.split("@", 1)
    return f"{local_part}+{clean.lower()}@{domain}"


class BigQueryRepository:
    """100% live BigQuery Medallion data access layer (`next_student_transfer_staging` + `next_student_transfer_gold`)."""

    def __init__(
        self,
        project_id: Optional[str] = None,
        staging_dataset_id: Optional[str] = None,
        gold_dataset_id: Optional[str] = None,
        location: Optional[str] = None,
    ) -> None:
        self.project_id = project_id or os.environ.get("GOOGLE_CLOUD_PROJECT", "next-student-transfer-prod")
        self.staging_dataset_id = staging_dataset_id or os.environ.get(
            "BQ_STAGING_DATASET_ID", "next_student_transfer_staging"
        )
        self.gold_dataset_id = gold_dataset_id or os.environ.get(
            "BQ_GOLD_DATASET_ID", "next_student_transfer_gold"
        )
        self.dataset_id = self.gold_dataset_id
        self.location = location or os.environ.get("GCP_REGION", "europe-west2")
        self.dataplex_scan_id = os.environ.get("DATAPLEX_SCAN_ID", "next-student-transfer-dq-scan")
        self._client: Optional[bigquery.Client] = None
        self._cache_ts: float = 0.0
        self._cached_students: list[dict[str, Any]] = []
        self._cached_shifts: list[dict[str, Any]] = []
        self._cache_ttl_sec: float = 2.0
        self._last_dataplex_trigger: dict[str, Any] = {
            "scan_resource": f"projects/{self.project_id}/locations/{self.location}/dataScans/{self.dataplex_scan_id}",
            "status": "READY",
        }

    @property
    def client(self) -> bigquery.Client:
        if self._client is None:
            self._client = bigquery.Client(project=self.project_id, location=self.location)
        return self._client

    def staging_fqn(self, table_name: str) -> str:
        return f"`{self.project_id}.{self.staging_dataset_id}.{table_name}`"

    def gold_fqn(self, table_name: str) -> str:
        return f"`{self.project_id}.{self.gold_dataset_id}.{table_name}`"

    def table_fqn(self, table_name: str) -> str:
        if table_name in {"portal_change_requests", "change_audit_log", "v_data_quality_warnings"}:
            return self.staging_fqn(table_name)
        return self.gold_fqn(table_name)

    def invalidate_cache(self) -> None:
        self._cache_ts = 0.0

    # =========================================================================
    # Dataplex Auto Data Quality Scan Integration (`europe-west2`)
    # =========================================================================
    def _run_dataplex_scan_worker(self) -> None:
        scan_name = f"projects/{self.project_id}/locations/{self.location}/dataScans/{self.dataplex_scan_id}"
        try:
            import google.auth
            from google.auth.transport.requests import Request as GoogleAuthRequest

            creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
            creds.refresh(GoogleAuthRequest())
            url = f"https://dataplex.googleapis.com/v1/{scan_name}:run"
            req = urllib.request.Request(
                url=url,
                data=b"{}",
                headers={
                    "Authorization": f"Bearer {creds.token}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            with urllib.request.urlopen(req, timeout=5.0) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
                job_info = payload.get("job", {})
                self._last_dataplex_trigger = {
                    "scan_resource": scan_name,
                    "triggered_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    "job_name": job_info.get("name", ""),
                    "job_state": job_info.get("state", "RUNNING"),
                    "uid": job_info.get("uid", ""),
                }
        except Exception as exc:
            self._last_dataplex_trigger = {
                "scan_resource": scan_name,
                "triggered_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "job_state": f"ASYNC_TRIGGER_SKIPPED ({type(exc).__name__})",
            }

    def trigger_dataplex_scan_async(self) -> dict[str, Any]:
        """Fire Dataplex Auto Data Quality Scan (`next-student-transfer-dq-scan`) asynchronously in background."""
        t = threading.Thread(target=self._run_dataplex_scan_worker, daemon=True)
        t.start()
        return self._last_dataplex_trigger

    def get_latest_dataplex_scan_status(self) -> dict[str, Any]:
        """Fetch latest Dataplex DataScan job metadata in `europe-west2` (with fast timeout)."""
        scan_name = f"projects/{self.project_id}/locations/{self.location}/dataScans/{self.dataplex_scan_id}"
        try:
            import google.auth
            from google.auth.transport.requests import Request as GoogleAuthRequest

            creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
            creds.refresh(GoogleAuthRequest())
            url = f"https://dataplex.googleapis.com/v1/{scan_name}/jobs?pageSize=3"
            req = urllib.request.Request(
                url=url,
                headers={"Authorization": f"Bearer {creds.token}"},
                method="GET",
            )
            with urllib.request.urlopen(req, timeout=2.5) as resp:
                payload = json.loads(resp.read().decode("utf-8"))
                jobs = payload.get("dataScanJobs", [])
                if jobs:
                    latest = jobs[0]
                    return {
                        "scan_resource": scan_name,
                        "target_table": f"{self.project_id}.{self.staging_dataset_id}.portal_change_requests",
                        "latest_job_name": latest.get("name", ""),
                        "latest_job_state": latest.get("state", "UNKNOWN"),
                        "start_time": latest.get("startTime", ""),
                        "end_time": latest.get("endTime", ""),
                        "recent_jobs_count": len(jobs),
                    }
        except Exception:
            pass
        return {
            "scan_resource": scan_name,
            "target_table": f"{self.project_id}.{self.staging_dataset_id}.portal_change_requests",
            **self._last_dataplex_trigger,
        }

    # =========================================================================
    # Gold Dataset Queries (`next_student_transfer_gold`)
    # =========================================================================
    def fetch_raw_students_and_shifts(self, force_refresh: bool = False) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        now = time.monotonic()
        if not force_refresh and self._cached_students and (now - self._cache_ts) < self._cache_ttl_sec:
            return self._cached_students, self._cached_shifts

        stu_sql = f"SELECT * FROM {self.gold_fqn('students_gold')} ORDER BY student_id"
        shf_sql = f"SELECT * FROM {self.gold_fqn('store_shifts_gold')} ORDER BY shift_id"

        stu_rows = [dict(r.items()) for r in self.client.query(stu_sql).result()]
        shf_rows = [dict(r.items()) for r in self.client.query(shf_sql).result()]

        self._cached_students = stu_rows
        self._cached_shifts = shf_rows
        self._cache_ts = now
        return stu_rows, shf_rows

    def fetch_students_df(self):
        import pandas as pd

        students, _ = self.fetch_raw_students_and_shifts()
        return pd.DataFrame(students)

    def fetch_shifts_df(self):
        import pandas as pd

        _, shifts = self.fetch_raw_students_and_shifts()
        return pd.DataFrame(shifts)

    def run_sql_capacity_simulation(
        self,
        min_hours_for_4star: float = 4.0,
        city_1: str = "Manchester",
        city_2: str = "Leeds",
    ) -> list[dict[str, Any]]:
        """Invoke BigQuery Stored Procedure `sp_simulate_store_capacity` on `next_student_transfer_gold`."""
        sql = f"CALL {self.gold_fqn('sp_simulate_store_capacity')}(@min_h, @c1, @c2)"
        cfg = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("min_h", "FLOAT64", float(min_hours_for_4star)),
                bigquery.ScalarQueryParameter("c1", "STRING", str(city_1)),
                bigquery.ScalarQueryParameter("c2", "STRING", str(city_2)),
            ]
        )
        try:
            return [dict(r.items()) for r in self.client.query(sql, job_config=cfg).result()]
        except Exception:
            return []

    def get_student(self, student_id: str) -> Optional[dict[str, Any]]:
        sql = f"SELECT * FROM {self.gold_fqn('students_gold')} WHERE student_id = @student_id LIMIT 1"
        cfg = bigquery.QueryJobConfig(
            query_parameters=[bigquery.ScalarQueryParameter("student_id", "STRING", student_id)]
        )
        rows = [dict(r.items()) for r in self.client.query(sql, job_config=cfg).result()]
        return rows[0] if rows else None

    def get_store_shifts(self, store_id: str) -> list[dict[str, Any]]:
        sql = f"SELECT * FROM {self.gold_fqn('store_shifts_gold')} WHERE store_id = @store_id ORDER BY shift_id"
        cfg = bigquery.QueryJobConfig(
            query_parameters=[bigquery.ScalarQueryParameter("store_id", "STRING", store_id)]
        )
        return [dict(r.items()) for r in self.client.query(sql, job_config=cfg).result()]

    def get_shift(self, shift_id: str) -> Optional[dict[str, Any]]:
        sql = f"SELECT * FROM {self.gold_fqn('store_shifts_gold')} WHERE shift_id = @shift_id LIMIT 1"
        cfg = bigquery.QueryJobConfig(
            query_parameters=[bigquery.ScalarQueryParameter("shift_id", "STRING", shift_id)]
        )
        rows = [dict(r.items()) for r in self.client.query(sql, job_config=cfg).result()]
        return rows[0] if rows else None

    # =========================================================================
    # Staging & Quarantine Queries (`next_student_transfer_staging`)
    # =========================================================================
    def get_data_quality_warnings(self) -> list[dict[str, Any]]:
        """Query the real-time `0ms` BigQuery Data Quality view `next_student_transfer_staging.v_data_quality_warnings`."""
        sql = f"""
        SELECT
            request_id,
            rule_id,
            dq_rule_id,
            severity,
            entity_type,
            entity_id,
            owner_token,
            actor_token,
            issue_summary,
            dq_reason,
            recommended_comms_action,
            updated_at,
            promotion_status
        FROM {self.staging_fqn('v_data_quality_warnings')}
        ORDER BY
            CASE WHEN severity = 'BLOCKING' THEN 0 ELSE 1 END,
            rule_id ASC,
            entity_id ASC
        """
        return [dict(r.items()) for r in self.client.query(sql).result()]

    def get_dq_warnings(self, limit: int = 50) -> list[dict[str, Any]]:
        return self.get_data_quality_warnings()[:limit]

    def get_portal_change_requests(
        self,
        limit: int = 50,
        status_filter: Optional[str] = None,
    ) -> list[dict[str, Any]]:
        where_clause = "WHERE promotion_status != 'SUPERSEDED_BY_RESET'"
        params = [bigquery.ScalarQueryParameter("limit", "INT64", int(limit))]
        if status_filter:
            where_clause += " AND promotion_status = @status_filter"
            params.append(bigquery.ScalarQueryParameter("status_filter", "STRING", status_filter))
        sql = f"""
        SELECT *
        FROM {self.staging_fqn('portal_change_requests')}
        {where_clause}
        ORDER BY submitted_at DESC
        LIMIT @limit
        """
        cfg = bigquery.QueryJobConfig(query_parameters=params)
        return [dict(r.items()) for r in self.client.query(sql, job_config=cfg).result()]

    def get_recent_changes(self, limit: int = 50, include_reverted: bool = False) -> list[dict[str, Any]]:
        where_clause = "" if include_reverted else "WHERE reverted = FALSE AND validation_status != 'DEMO_RESET'"
        sql = f"""
        SELECT
            change_id,
            request_id,
            changed_at,
            actor_persona,
            actor_token,
            entity_type,
            entity_id,
            field_changed,
            old_value,
            new_value,
            validation_status,
            rule_triggered,
            reverted,
            reverted_by,
            revert_reason
        FROM {self.staging_fqn('change_audit_log')}
        {where_clause}
        ORDER BY changed_at DESC
        LIMIT @limit
        """
        job_config = bigquery.QueryJobConfig(
            query_parameters=[bigquery.ScalarQueryParameter("limit", "INT64", int(limit))]
        )
        rows = [dict(r.items()) for r in self.client.query(sql, job_config=job_config).result()]
        for r in rows:
            r.setdefault("target_id", r.get("entity_id"))
            r.setdefault("dq_rule_id", r.get("rule_triggered"))
            r.setdefault("is_valid", r.get("validation_status") in {"VALID", "PROMOTED"})
        return rows

    def _insert_portal_change_request_row(self, row: dict[str, Any]) -> None:
        sql = f"""
        INSERT INTO {self.staging_fqn('portal_change_requests')} (
            request_id, submitted_at, actor_role, actor_id, target_table, record_id,
            age_band, choice_1, choice_2, choice_3, transfer_gap_days, shift_hours,
            preferred_latest_finish, was_green_box_protected, new_is_green_box_protected,
            cmp_3day_cleared, note, promotion_status, dq_rules_triggered,
            resolved_at, resolved_by, hr_reason
        ) VALUES (
            @request_id, @submitted_at, @actor_role, @actor_id, @target_table, @record_id,
            @age_band, @choice_1, @choice_2, @choice_3, @transfer_gap_days, @shift_hours,
            @preferred_latest_finish, @was_green_box_protected, @new_is_green_box_protected,
            @cmp_3day_cleared, @note, @promotion_status, @dq_rules_triggered,
            @resolved_at, @resolved_by, @hr_reason
        )
        """
        cfg = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("request_id", "STRING", row["request_id"]),
                bigquery.ScalarQueryParameter("submitted_at", "STRING", row["submitted_at"]),
                bigquery.ScalarQueryParameter("actor_role", "STRING", row["actor_role"]),
                bigquery.ScalarQueryParameter("actor_id", "STRING", row["actor_id"]),
                bigquery.ScalarQueryParameter("target_table", "STRING", row["target_table"]),
                bigquery.ScalarQueryParameter("record_id", "STRING", row["record_id"]),
                bigquery.ScalarQueryParameter("age_band", "STRING", row.get("age_band")),
                bigquery.ScalarQueryParameter("choice_1", "STRING", row.get("choice_1")),
                bigquery.ScalarQueryParameter("choice_2", "STRING", row.get("choice_2")),
                bigquery.ScalarQueryParameter("choice_3", "STRING", row.get("choice_3")),
                bigquery.ScalarQueryParameter("transfer_gap_days", "INT64", row.get("transfer_gap_days")),
                bigquery.ScalarQueryParameter("shift_hours", "FLOAT64", row.get("shift_hours")),
                bigquery.ScalarQueryParameter("preferred_latest_finish", "STRING", row.get("preferred_latest_finish")),
                bigquery.ScalarQueryParameter("was_green_box_protected", "BOOL", row.get("was_green_box_protected")),
                bigquery.ScalarQueryParameter("new_is_green_box_protected", "BOOL", row.get("new_is_green_box_protected")),
                bigquery.ScalarQueryParameter("cmp_3day_cleared", "BOOL", row.get("cmp_3day_cleared")),
                bigquery.ScalarQueryParameter("note", "STRING", row.get("note", "")),
                bigquery.ScalarQueryParameter("promotion_status", "STRING", row["promotion_status"]),
                bigquery.ScalarQueryParameter("dq_rules_triggered", "STRING", row.get("dq_rules_triggered", "NONE")),
                bigquery.ScalarQueryParameter("resolved_at", "STRING", row.get("resolved_at", "")),
                bigquery.ScalarQueryParameter("resolved_by", "STRING", row.get("resolved_by", "")),
                bigquery.ScalarQueryParameter("hr_reason", "STRING", row.get("hr_reason", "")),
            ]
        )
        self.client.query(sql, job_config=cfg).result()

    def _insert_change_audit_rows(self, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        for r in rows:
            sql = f"""
            INSERT INTO {self.staging_fqn('change_audit_log')} (
                change_id, request_id, changed_at, actor_persona, actor_token, entity_type, entity_id,
                field_changed, old_value, new_value, validation_status, rule_triggered,
                reverted, reverted_by, revert_reason
            ) VALUES (
                @change_id, @request_id, @changed_at, @actor_persona, @actor_token, @entity_type, @entity_id,
                @field_changed, @old_value, @new_value, @validation_status, @rule_triggered,
                @reverted, @reverted_by, @revert_reason
            )
            """
            cfg = bigquery.QueryJobConfig(
                query_parameters=[
                    bigquery.ScalarQueryParameter("change_id", "STRING", str(r["change_id"])),
                    bigquery.ScalarQueryParameter("request_id", "STRING", str(r.get("request_id", ""))),
                    bigquery.ScalarQueryParameter("changed_at", "STRING", str(r["changed_at"])),
                    bigquery.ScalarQueryParameter("actor_persona", "STRING", str(r["actor_persona"])),
                    bigquery.ScalarQueryParameter("actor_token", "STRING", str(r["actor_token"])),
                    bigquery.ScalarQueryParameter("entity_type", "STRING", str(r["entity_type"])),
                    bigquery.ScalarQueryParameter("entity_id", "STRING", str(r["entity_id"])),
                    bigquery.ScalarQueryParameter("field_changed", "STRING", str(r["field_changed"])),
                    bigquery.ScalarQueryParameter("old_value", "STRING", str(r["old_value"])),
                    bigquery.ScalarQueryParameter("new_value", "STRING", str(r["new_value"])),
                    bigquery.ScalarQueryParameter("validation_status", "STRING", str(r["validation_status"])),
                    bigquery.ScalarQueryParameter("rule_triggered", "STRING", str(r["rule_triggered"])),
                    bigquery.ScalarQueryParameter("reverted", "BOOL", bool(r["reverted"])),
                    bigquery.ScalarQueryParameter("reverted_by", "STRING", str(r.get("reverted_by", ""))),
                    bigquery.ScalarQueryParameter("revert_reason", "STRING", str(r.get("revert_reason", ""))),
                ]
            )
            self.client.query(sql, job_config=cfg).result()

    # =========================================================================
    # Portal Intake Writes (`PROMOTED` to Gold vs `QUARANTINED` in Staging)
    # =========================================================================
    def update_student_preferences(
        self,
        student_id: str,
        actor_token: str,
        choice_1_store_id: Optional[str] = None,
        choice_2_store_id: Optional[str] = None,
        choice_3_store_id: Optional[str] = None,
        available_days: Optional[str] = None,
        preferred_latest_finish: Optional[str] = None,
        target_transfer_date: Optional[str] = None,
        small_contract_opt_in: Optional[bool] = None,
        preferred_transfer_date: Optional[str] = None,
        wants_night_shift_ending_after_2200: Optional[bool] = None,
        transfer_gap_days: Optional[int] = None,
        note: str = "",
    ) -> dict[str, Any]:
        """Insert a student preference request into `portal_change_requests`.

        - If 100% compliant with DQ-01, DQ-02, and DQ-06 -> `PROMOTED` into `next_student_transfer_gold.students_gold`.
        - If any DQ rule fails -> `QUARANTINED` in `next_student_transfer_staging.portal_change_requests` (`students_gold` stays clean!).
        """
        current = self.get_student(student_id)
        if not current:
            raise ValueError(f"Student {student_id} not found in BigQuery students_gold.")

        if target_transfer_date is None and preferred_transfer_date is not None:
            target_transfer_date = preferred_transfer_date
        if preferred_latest_finish is None and wants_night_shift_ending_after_2200 is not None:
            preferred_latest_finish = "23:30" if wants_night_shift_ending_after_2200 else "21:30"

        new_c1 = choice_1_store_id if choice_1_store_id is not None else current["choice_1_store_id"]
        new_c2 = choice_2_store_id if choice_2_store_id is not None else (current.get("choice_2_store_id") or "")
        new_c3 = choice_3_store_id if choice_3_store_id is not None else (current.get("choice_3_store_id") or "")
        new_days = available_days if available_days is not None else current["available_days"]
        new_finish = preferred_latest_finish if preferred_latest_finish is not None else current["preferred_latest_finish"]
        new_dt = target_transfer_date if target_transfer_date is not None else current["target_transfer_date"]
        new_opt_in = (
            bool(small_contract_opt_in)
            if small_contract_opt_in is not None
            else bool(current["small_contract_opt_in"])
        )

        # Compute transfer_gap_days if not explicitly supplied (baseline home finish 2026-09-13)
        if transfer_gap_days is None:
            try:
                home_end = datetime.strptime("2026-09-13", "%Y-%m-%d").date()
                uni_start = datetime.strptime(new_dt[:10], "%Y-%m-%d").date()
                transfer_gap_days = (uni_start - home_end).days
            except Exception:
                transfer_gap_days = 14

        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        request_id = f"REQ-{uuid.uuid4().hex[:8].upper()}"

        dup_choices = (
            new_c1 == new_c2
            or new_c1 == new_c3
            or (new_c2 != "" and new_c2 == new_c3)
        )
        under18_violation = current["age_band"] == "UNDER_18" and new_finish > "22:00"
        date_window_violation = transfer_gap_days < 0 or transfer_gap_days > 21

        triggered_rules: list[str] = []
        if dup_choices:
            triggered_rules.append("DQ-01")
        if date_window_violation:
            triggered_rules.append("DQ-02")
        if under18_violation:
            triggered_rules.append("DQ-06")

        promotion_status = "QUARANTINED" if triggered_rules else "PROMOTED"
        dq_rules_str = ",".join(triggered_rules) if triggered_rules else "NONE"

        # 1. Always append to Bronze `portal_change_requests`
        self._insert_portal_change_request_row({
            "request_id": request_id,
            "submitted_at": now_iso,
            "actor_role": "STUDENT",
            "actor_id": actor_token,
            "target_table": "students_gold",
            "record_id": student_id,
            "age_band": str(current["age_band"]),
            "choice_1": new_c1,
            "choice_2": new_c2,
            "choice_3": new_c3,
            "transfer_gap_days": int(transfer_gap_days),
            "shift_hours": None,
            "preferred_latest_finish": new_finish,
            "was_green_box_protected": None,
            "new_is_green_box_protected": None,
            "cmp_3day_cleared": None,
            "note": note,
            "promotion_status": promotion_status,
            "dq_rules_triggered": dq_rules_str,
        })

        # 2. Only promote into `next_student_transfer_gold.students_gold` when zero DQ rules triggered!
        if promotion_status == "PROMOTED":
            sql = f"""
            UPDATE {self.gold_fqn('students_gold')}
            SET
                choice_1_store_id = @c1,
                choice_2_store_id = @c2,
                choice_3_store_id = @c3,
                available_days = @days,
                preferred_latest_finish = @finish,
                target_transfer_date = @dt,
                small_contract_opt_in = @opt_in,
                updated_at = @updated_at,
                updated_by_token = @actor
            WHERE student_id = @student_id
            """
            cfg = bigquery.QueryJobConfig(
                query_parameters=[
                    bigquery.ScalarQueryParameter("c1", "STRING", new_c1),
                    bigquery.ScalarQueryParameter("c2", "STRING", new_c2),
                    bigquery.ScalarQueryParameter("c3", "STRING", new_c3),
                    bigquery.ScalarQueryParameter("days", "STRING", new_days),
                    bigquery.ScalarQueryParameter("finish", "STRING", new_finish),
                    bigquery.ScalarQueryParameter("dt", "STRING", new_dt),
                    bigquery.ScalarQueryParameter("opt_in", "BOOL", new_opt_in),
                    bigquery.ScalarQueryParameter("updated_at", "STRING", now_iso),
                    bigquery.ScalarQueryParameter("actor", "STRING", actor_token),
                    bigquery.ScalarQueryParameter("student_id", "STRING", student_id),
                ]
            )
            self.client.query(sql, job_config=cfg).result()
            self.invalidate_cache()

        # 3. Append to `change_audit_log`
        candidates = [
            ("choice_1_store_id", str(current["choice_1_store_id"]), str(new_c1)),
            ("choice_2_store_id", str(current.get("choice_2_store_id") or ""), str(new_c2)),
            ("choice_3_store_id", str(current.get("choice_3_store_id") or ""), str(new_c3)),
            ("available_days", str(current["available_days"]), str(new_days)),
            ("preferred_latest_finish", str(current["preferred_latest_finish"]), str(new_finish)),
            ("target_transfer_date", str(current["target_transfer_date"]), str(new_dt)),
            ("small_contract_opt_in", str(current["small_contract_opt_in"]).lower(), str(new_opt_in).lower()),
        ]
        audit_rows: list[dict[str, Any]] = []
        for field_name, old_val, new_val in candidates:
            if old_val == new_val:
                continue
            rule_triggered = "NONE"
            val_status = "VALID"
            if field_name in {"choice_1_store_id", "choice_2_store_id", "choice_3_store_id"} and dup_choices:
                rule_triggered = "DQ-01"
                val_status = "QUARANTINED"
            elif field_name == "preferred_latest_finish" and under18_violation:
                rule_triggered = "DQ-06"
                val_status = "QUARANTINED"
            elif field_name == "target_transfer_date" and date_window_violation:
                rule_triggered = "DQ-02"
                val_status = "QUARANTINED"

            audit_rows.append({
                "change_id": f"CHG-{uuid.uuid4().hex[:8].upper()}",
                "request_id": request_id,
                "changed_at": now_iso,
                "actor_persona": "STUDENT",
                "actor_token": actor_token,
                "entity_type": "STUDENT",
                "entity_id": student_id,
                "field_changed": field_name,
                "old_value": old_val,
                "new_value": new_val,
                "validation_status": val_status,
                "rule_triggered": rule_triggered,
                "reverted": False,
                "reverted_by": "",
                "revert_reason": "",
            })

        self._insert_change_audit_rows(audit_rows)
        return {
            "status": promotion_status,
            "request_id": request_id,
            "student_id": student_id,
            "promotion_status": promotion_status,
            "dq_rules_triggered": triggered_rules,
            "changes_logged": len(audit_rows),
            "audit_entries": audit_rows,
        }

    def update_store_shift(
        self,
        shift_id: str,
        actor_token: str,
        weekly_hours: Optional[float] = None,
        is_green_box_protected: Optional[bool] = None,
        cmp_3day_cleared: Optional[bool] = None,
        day_of_week: Optional[str] = None,
        start_time: Optional[str] = None,
        end_time: Optional[str] = None,
        note: str = "",
    ) -> dict[str, Any]:
        """Insert a store shift update request into `portal_change_requests`.

        - Valid updates (e.g., SM-019 clearing 3-day CMP on SHF-9001..9003) promote immediately into `store_shifts_gold` (`PROMOTED`).
        - Invalid updates (DQ-03 `<4.0h` or `>12.0h`, DQ-05 unapproved Green Box unlock) stay `QUARANTINED` in `portal_change_requests` (`store_shifts_gold` stays clean!).
        """
        current = self.get_shift(shift_id)
        if not current:
            raise ValueError(f"Shift {shift_id} not found in BigQuery store_shifts_gold.")

        new_hours = round(float(weekly_hours), 1) if weekly_hours is not None else float(current["weekly_hours"])
        was_gb = bool(current["is_green_box_protected"])
        new_gb = bool(is_green_box_protected) if is_green_box_protected is not None else was_gb
        new_cmp = bool(cmp_3day_cleared) if cmp_3day_cleared is not None else bool(current["cmp_3day_cleared"])
        new_day = day_of_week if day_of_week is not None else current["day_of_week"]
        new_start = start_time if start_time is not None else current["start_time"]
        new_end = end_time if end_time is not None else current["end_time"]

        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        request_id = f"REQ-{uuid.uuid4().hex[:8].upper()}"

        triggered_rules: list[str] = []
        if new_hours < 4.0 or new_hours > 12.0:
            triggered_rules.append("DQ-03")
        if was_gb and not new_gb:
            triggered_rules.append("DQ-05")

        promotion_status = "QUARANTINED" if triggered_rules else "PROMOTED"
        dq_rules_str = ",".join(triggered_rules) if triggered_rules else "NONE"

        # 1. Always append to Bronze `portal_change_requests`
        self._insert_portal_change_request_row({
            "request_id": request_id,
            "submitted_at": now_iso,
            "actor_role": "STORE_MANAGER",
            "actor_id": actor_token,
            "target_table": "store_shifts_gold",
            "record_id": shift_id,
            "age_band": None,
            "choice_1": None,
            "choice_2": None,
            "choice_3": None,
            "transfer_gap_days": None,
            "shift_hours": new_hours,
            "preferred_latest_finish": None,
            "was_green_box_protected": was_gb,
            "new_is_green_box_protected": new_gb,
            "cmp_3day_cleared": new_cmp,
            "note": note,
            "promotion_status": promotion_status,
            "dq_rules_triggered": dq_rules_str,
        })

        # 2. Only promote into `next_student_transfer_gold.store_shifts_gold` when zero DQ rules triggered!
        if promotion_status == "PROMOTED":
            sql = f"""
            UPDATE {self.gold_fqn('store_shifts_gold')}
            SET
                weekly_hours = @hours,
                is_green_box_protected = @gb,
                cmp_3day_cleared = @cmp,
                day_of_week = @day,
                start_time = @start_t,
                end_time = @end_t,
                updated_at = @updated_at,
                updated_by_token = @actor
            WHERE shift_id = @shift_id
            """
            cfg = bigquery.QueryJobConfig(
                query_parameters=[
                    bigquery.ScalarQueryParameter("hours", "FLOAT64", new_hours),
                    bigquery.ScalarQueryParameter("gb", "BOOL", new_gb),
                    bigquery.ScalarQueryParameter("cmp", "BOOL", new_cmp),
                    bigquery.ScalarQueryParameter("day", "STRING", new_day),
                    bigquery.ScalarQueryParameter("start_t", "STRING", new_start),
                    bigquery.ScalarQueryParameter("end_t", "STRING", new_end),
                    bigquery.ScalarQueryParameter("updated_at", "STRING", now_iso),
                    bigquery.ScalarQueryParameter("actor", "STRING", actor_token),
                    bigquery.ScalarQueryParameter("shift_id", "STRING", shift_id),
                ]
            )
            self.client.query(sql, job_config=cfg).result()
            self.invalidate_cache()

        # 3. Append to `change_audit_log`
        candidates = [
            ("weekly_hours", f"{float(current['weekly_hours']):.1f}", f"{new_hours:.1f}"),
            ("is_green_box_protected", str(was_gb).lower(), str(new_gb).lower()),
            ("cmp_3day_cleared", str(bool(current["cmp_3day_cleared"])).lower(), str(new_cmp).lower()),
            ("day_of_week", str(current["day_of_week"]), str(new_day)),
            ("start_time", str(current["start_time"]), str(new_start)),
            ("end_time", str(current["end_time"]), str(new_end)),
        ]
        audit_rows: list[dict[str, Any]] = []
        for field_name, old_val, new_val in candidates:
            if old_val == new_val:
                continue
            rule_triggered = "NONE"
            val_status = "VALID"
            if field_name == "weekly_hours" and (new_hours < 4.0 or new_hours > 12.0):
                rule_triggered = "DQ-03"
                val_status = "QUARANTINED"
            elif field_name == "is_green_box_protected" and old_val == "true" and new_val == "false":
                rule_triggered = "DQ-05"
                val_status = "QUARANTINED"

            audit_rows.append({
                "change_id": f"CHG-{uuid.uuid4().hex[:8].upper()}",
                "request_id": request_id,
                "changed_at": now_iso,
                "actor_persona": "STORE_MANAGER",
                "actor_token": actor_token,
                "entity_type": "STORE_SHIFT",
                "entity_id": shift_id,
                "field_changed": field_name,
                "old_value": old_val,
                "new_value": new_val,
                "validation_status": val_status,
                "rule_triggered": rule_triggered,
                "reverted": False,
                "reverted_by": "",
                "revert_reason": "",
            })

        self._insert_change_audit_rows(audit_rows)
        return {
            "status": promotion_status,
            "request_id": request_id,
            "shift_id": shift_id,
            "store_id": current["store_id"],
            "promotion_status": promotion_status,
            "dq_rules_triggered": triggered_rules,
            "changes_logged": len(audit_rows),
            "audit_entries": audit_rows,
        }

    # =========================================================================
    # HR Quarantine Resolution (`REJECT` vs `OVERRIDE_PROMOTE` with HITL `confirm`)
    # =========================================================================
    def resolve_quarantined_change(
        self,
        identifier: str,
        decision: str = "REJECT",
        hr_reason: str = "Resolved by Central HR Specialist via Gemini Enterprise",
        resolved_by: str = "HR-SPECIALIST-GE",
        confirm: bool = False,
    ) -> dict[str, Any]:
        """Resolve a quarantined change in `portal_change_requests` (`REQ-XXXXXXXX` or `CHG-XXXXXXXX`).

        Enforces A2A-safe HITL: when `confirm=False`, performs zero BigQuery writes and returns a
        preview summary asking HR to reply `"Confirm"`. Executes the write only when `confirm=True`.
        """
        clean_id = identifier.strip().upper()
        decision_norm = decision.strip().upper()
        if decision_norm not in {"REJECT", "REJECTED_BY_HR", "OVERRIDE_PROMOTE", "OVERRIDDEN_BY_HR"}:
            decision_norm = "REJECT"

        # Look up by request_id or change_id
        req_row: Optional[dict[str, Any]] = None
        chg_row: Optional[dict[str, Any]] = None

        if clean_id.startswith("CHG-"):
            sql_c = f"SELECT * FROM {self.staging_fqn('change_audit_log')} WHERE change_id = @cid LIMIT 1"
            c_rows = [
                dict(r.items())
                for r in self.client.query(
                    sql_c,
                    job_config=bigquery.QueryJobConfig(
                        query_parameters=[bigquery.ScalarQueryParameter("cid", "STRING", clean_id)]
                    ),
                ).result()
            ]
            if c_rows:
                chg_row = c_rows[0]
                if chg_row.get("request_id"):
                    clean_id = str(chg_row["request_id"])

        sql_r = f"""
        SELECT * FROM {self.staging_fqn('portal_change_requests')}
        WHERE request_id = @rid
           OR (record_id = @rid AND promotion_status != 'SUPERSEDED_BY_RESET')
        ORDER BY
            CASE WHEN request_id = @rid THEN 0 WHEN promotion_status = 'QUARANTINED' THEN 1 ELSE 2 END,
            submitted_at DESC
        LIMIT 1
        """
        r_rows = [
            dict(r.items())
            for r in self.client.query(
                sql_r,
                job_config=bigquery.QueryJobConfig(
                    query_parameters=[bigquery.ScalarQueryParameter("rid", "STRING", clean_id)]
                ),
            ).result()
        ]
        if r_rows:
            req_row = r_rows[0]

        if not req_row and not chg_row:
            raise ValueError(f"Identifier {identifier} not found in portal_change_requests or change_audit_log.")

        target_request_id = req_row["request_id"] if req_row else str(chg_row.get("request_id") or "")
        entity_id = req_row["record_id"] if req_row else str(chg_row["entity_id"])
        dq_rules = req_row.get("dq_rules_triggered", "") if req_row else str(chg_row.get("rule_triggered", ""))
        new_status = "OVERRIDDEN_BY_HR" if "OVERRIDE" in decision_norm else "REJECTED_BY_HR"

        if not confirm:
            return {
                "status": "CONFIRMATION_REQUIRED",
                "confirm_required": True,
                "request_id": target_request_id or clean_id,
                "change_id": chg_row["change_id"] if chg_row else "",
                "entity_id": entity_id,
                "dq_rules_triggered": dq_rules,
                "proposed_decision": new_status,
                "hr_reason": hr_reason,
                "message": (
                    f"HITL Confirmation Required: Ready to mark `{target_request_id or clean_id}` "
                    f"(`{entity_id}`, rule `{dq_rules}`) as `{new_status}` with reason '{hr_reason}'. "
                    f"Please reply 'Confirm' to execute this update in BigQuery."
                ),
            }

        now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        # If OVERRIDDEN_BY_HR and we have a portal_change_requests row, promote to Gold
        if new_status == "OVERRIDDEN_BY_HR" and req_row:
            if req_row["target_table"] == "students_gold":
                upd_stu = f"""
                UPDATE {self.gold_fqn('students_gold')}
                SET
                    choice_1_store_id = COALESCE(@c1, choice_1_store_id),
                    choice_2_store_id = COALESCE(@c2, choice_2_store_id),
                    choice_3_store_id = COALESCE(@c3, choice_3_store_id),
                    preferred_latest_finish = COALESCE(@finish, preferred_latest_finish),
                    updated_at = @now_iso,
                    updated_by_token = @resolved_by
                WHERE student_id = @record_id
                """
                self.client.query(
                    upd_stu,
                    job_config=bigquery.QueryJobConfig(
                        query_parameters=[
                            bigquery.ScalarQueryParameter("c1", "STRING", req_row.get("choice_1")),
                            bigquery.ScalarQueryParameter("c2", "STRING", req_row.get("choice_2")),
                            bigquery.ScalarQueryParameter("c3", "STRING", req_row.get("choice_3")),
                            bigquery.ScalarQueryParameter("finish", "STRING", req_row.get("preferred_latest_finish")),
                            bigquery.ScalarQueryParameter("now_iso", "STRING", now_iso),
                            bigquery.ScalarQueryParameter("resolved_by", "STRING", resolved_by),
                            bigquery.ScalarQueryParameter("record_id", "STRING", req_row["record_id"]),
                        ]
                    ),
                ).result()
            elif req_row["target_table"] == "store_shifts_gold":
                upd_shf = f"""
                UPDATE {self.gold_fqn('store_shifts_gold')}
                SET
                    weekly_hours = COALESCE(@hours, weekly_hours),
                    is_green_box_protected = COALESCE(@gb, is_green_box_protected),
                    cmp_3day_cleared = COALESCE(@cmp, cmp_3day_cleared),
                    updated_at = @now_iso,
                    updated_by_token = @resolved_by
                WHERE shift_id = @record_id
                """
                self.client.query(
                    upd_shf,
                    job_config=bigquery.QueryJobConfig(
                        query_parameters=[
                            bigquery.ScalarQueryParameter("hours", "FLOAT64", req_row.get("shift_hours")),
                            bigquery.ScalarQueryParameter("gb", "BOOL", req_row.get("new_is_green_box_protected")),
                            bigquery.ScalarQueryParameter("cmp", "BOOL", req_row.get("cmp_3day_cleared")),
                            bigquery.ScalarQueryParameter("now_iso", "STRING", now_iso),
                            bigquery.ScalarQueryParameter("resolved_by", "STRING", resolved_by),
                            bigquery.ScalarQueryParameter("record_id", "STRING", req_row["record_id"]),
                        ]
                    ),
                ).result()

        if target_request_id:
            upd_req = f"""
            UPDATE {self.staging_fqn('portal_change_requests')}
            SET
                promotion_status = @new_status,
                resolved_at = @now_iso,
                resolved_by = @resolved_by,
                hr_reason = @hr_reason
            WHERE request_id = @rid
            """
            self.client.query(
                upd_req,
                job_config=bigquery.QueryJobConfig(
                    query_parameters=[
                        bigquery.ScalarQueryParameter("new_status", "STRING", new_status),
                        bigquery.ScalarQueryParameter("now_iso", "STRING", now_iso),
                        bigquery.ScalarQueryParameter("resolved_by", "STRING", resolved_by),
                        bigquery.ScalarQueryParameter("hr_reason", "STRING", hr_reason),
                        bigquery.ScalarQueryParameter("rid", "STRING", target_request_id),
                    ]
                ),
            ).result()

            upd_chg = f"""
            UPDATE {self.staging_fqn('change_audit_log')}
            SET
                validation_status = @new_status,
                reverted = @is_reverted,
                reverted_by = @resolved_by,
                revert_reason = @hr_reason
            WHERE request_id = @rid
            """
            self.client.query(
                upd_chg,
                job_config=bigquery.QueryJobConfig(
                    query_parameters=[
                        bigquery.ScalarQueryParameter("new_status", "STRING", new_status),
                        bigquery.ScalarQueryParameter("is_reverted", "BOOL", new_status == "REJECTED_BY_HR"),
                        bigquery.ScalarQueryParameter("resolved_by", "STRING", resolved_by),
                        bigquery.ScalarQueryParameter("hr_reason", "STRING", hr_reason),
                        bigquery.ScalarQueryParameter("rid", "STRING", target_request_id),
                    ]
                ),
            ).result()
        elif chg_row:
            upd_chg_only = f"""
            UPDATE {self.staging_fqn('change_audit_log')}
            SET
                validation_status = @new_status,
                reverted = @is_reverted,
                reverted_by = @resolved_by,
                revert_reason = @hr_reason
            WHERE change_id = @cid
            """
            self.client.query(
                upd_chg_only,
                job_config=bigquery.QueryJobConfig(
                    query_parameters=[
                        bigquery.ScalarQueryParameter("new_status", "STRING", new_status),
                        bigquery.ScalarQueryParameter("is_reverted", "BOOL", new_status == "REJECTED_BY_HR"),
                        bigquery.ScalarQueryParameter("resolved_by", "STRING", resolved_by),
                        bigquery.ScalarQueryParameter("hr_reason", "STRING", hr_reason),
                        bigquery.ScalarQueryParameter("cid", "STRING", chg_row["change_id"]),
                    ]
                ),
            ).result()

        self.invalidate_cache()
        return {
            "status": "REVERTED" if new_status == "REJECTED_BY_HR" else new_status,
            "resolution_status": new_status,
            "request_id": target_request_id,
            "change_id": chg_row["change_id"] if chg_row else "",
            "entity_type": req_row["target_table"] if req_row else chg_row["entity_type"],
            "entity_id": entity_id,
            "field_changed": chg_row["field_changed"] if chg_row else "portal_change_request",
            "restored_value": chg_row["old_value"] if chg_row else "GOLD_BASELINE_PRESERVED",
            "reverted_by": resolved_by,
            "revert_reason": hr_reason,
        }

    def revert_change(self, change_id: str, reverted_by: str, revert_reason: str, confirm: bool = True) -> dict[str, Any]:
        """Backwards-compatible wrapper around `resolve_quarantined_change(decision='REJECT')`."""
        return self.resolve_quarantined_change(
            identifier=change_id,
            decision="REJECT",
            hr_reason=revert_reason,
            resolved_by=reverted_by,
            confirm=confirm,
        )

    # =========================================================================
    # 4 1-Click Presenter Scenarios (App 1 Stage Dock + Async Dataplex Scan)
    # =========================================================================
    def run_demo_scenario(self, scenario_id: str) -> dict[str, Any]:
        """Execute one of the 4 1-Click Demo Scenarios in App 1 against live BigQuery + Dataplex."""
        norm = scenario_id.strip().upper()
        if norm in {"SCENARIO_1_VALID_SM_CMP", "SCENARIO_1_VALID_CLEARANCE", "1"}:
            scenario_id = "SCENARIO_1_VALID_SM_CMP"
        elif norm in {"SCENARIO_2_INVALID_STUDENT", "SCENARIO_2_STUDENT_DQ_VIOLATION", "2"}:
            scenario_id = "SCENARIO_2_INVALID_STUDENT"
        elif norm in {"SCENARIO_3_INVALID_SM", "SCENARIO_3_MANAGER_DQ_VIOLATION", "3"}:
            scenario_id = "SCENARIO_3_INVALID_SM"
        elif norm in {"SCENARIO_4_RESET", "RESET", "R", "4"}:
            scenario_id = "SCENARIO_4_RESET"

        if scenario_id == "SCENARIO_1_VALID_SM_CMP":
            results = []
            for sid in ["SHF-9001", "SHF-9002", "SHF-9003"]:
                res = self.update_store_shift(
                    shift_id=sid,
                    actor_token="SM-019",
                    cmp_3day_cleared=True,
                    note="3-day Internal Contract Marketplace check completed by SM-019.",
                )
                results.append(res)
            scan_trigger = self.trigger_dataplex_scan_async()
            return {
                "scenario_id": scenario_id,
                "summary": (
                    "SM-019 (STORE-019 Leeds Trinity) cleared the 3-day Contract Marketplace check on "
                    "SHF-9001, SHF-9002, and SHF-9003 -> PROMOTED to next_student_transfer_gold.store_shifts_gold."
                ),
                "updates": results,
                "dataplex_scan": scan_trigger,
                "active_dq_warnings": self.get_data_quality_warnings(),
            }

        if scenario_id == "SCENARIO_2_INVALID_STUDENT":
            r1 = self.update_student_preferences(
                student_id="STU-0412",
                actor_token="STU-0412",
                choice_2_store_id="STORE-042",
                note="Student selected STORE-042 as both 1st and 2nd preference.",
            )
            r2 = self.update_student_preferences(
                student_id="STU-0109",
                actor_token="STU-0109",
                preferred_latest_finish="23:30",
                note="Under-18 student requested 23:30 close shift finish.",
            )
            scan_trigger = self.trigger_dataplex_scan_async()
            return {
                "scenario_id": scenario_id,
                "summary": (
                    "Quarantined 2 invalid student requests in next_student_transfer_staging.portal_change_requests "
                    "(students_gold untouched): (1) STU-0412 duplicate store choice STORE-042 (DQ-01); "
                    "(2) STU-0109 (UNDER_18) requested 23:30 finish (>22:00 statutory curfew -> DQ-06)."
                ),
                "updates": [r1, r2],
                "dataplex_scan": scan_trigger,
                "active_dq_warnings": self.get_data_quality_warnings(),
            }

        if scenario_id == "SCENARIO_3_INVALID_SM":
            r1 = self.update_store_shift(
                shift_id="SHF-1028",
                actor_token="SM-012",
                weekly_hours=3.0,
                note="SM-012 reduced SHF-1028 weekly hours to 3.0h.",
            )
            r2 = self.update_store_shift(
                shift_id="SHF-1003",
                actor_token="SM-042",
                is_green_box_protected=False,
                note="SM-042 attempted to remove Green Box protection on SHF-1003.",
            )
            scan_trigger = self.trigger_dataplex_scan_async()
            return {
                "scenario_id": scenario_id,
                "summary": (
                    "Quarantined 2 invalid Store Manager requests in next_student_transfer_staging.portal_change_requests "
                    "(store_shifts_gold untouched): (1) SM-012 lowered SHF-1028 to 3.0h (<4.0h minimum -> DQ-03); "
                    "(2) SM-042 unlocked Green Box protected shift SHF-1003 without HR approval (-> DQ-05)."
                ),
                "updates": [r1, r2],
                "dataplex_scan": scan_trigger,
                "active_dq_warnings": self.get_data_quality_warnings(),
            }

        if scenario_id == "SCENARIO_4_RESET":
            now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            reset_req_id = f"REQ-RESET-{uuid.uuid4().hex[:6].upper()}"
            reset_statements = [
                # Restore trusted Gold tables from immutable Gold baselines
                f"""
                CREATE OR REPLACE TABLE {self.gold_fqn('students_gold')} AS
                SELECT * FROM {self.gold_fqn('students_gold_baseline')}
                """,
                f"""
                CREATE OR REPLACE TABLE {self.gold_fqn('store_shifts_gold')} AS
                SELECT * FROM {self.gold_fqn('store_shifts_gold_baseline')}
                """,
                # Mark prior staging requests as SUPERSEDED_BY_RESET (never DELETE/TRUNCATE)
                f"""
                UPDATE {self.staging_fqn('portal_change_requests')}
                SET
                    promotion_status = 'SUPERSEDED_BY_RESET',
                    resolved_at = '{now_iso}',
                    resolved_by = 'SYSTEM-DEMO-RESET',
                    hr_reason = 'Demo baseline reset'
                WHERE promotion_status != 'SUPERSEDED_BY_RESET'
                """,
                # Mark prior audit rows as reverted by reset and append an immutable DEMO_RESET event
                f"""
                UPDATE {self.staging_fqn('change_audit_log')}
                SET
                    reverted = TRUE,
                    reverted_by = 'SYSTEM-DEMO-RESET',
                    revert_reason = 'Demo baseline reset'
                WHERE reverted = FALSE AND validation_status != 'DEMO_RESET'
                """,
            ]
            for stmt in reset_statements:
                self.client.query(stmt).result()

            self._insert_change_audit_rows([{
                "change_id": f"CHG-{uuid.uuid4().hex[:8].upper()}",
                "request_id": reset_req_id,
                "changed_at": now_iso,
                "actor_persona": "SYSTEM_DEMO_HARNESS",
                "actor_token": "DEMO_RESET",
                "entity_type": "DEMO_STATE",
                "entity_id": "ALL_DEMO_RECORDS",
                "field_changed": "baseline_restore",
                "old_value": "MODIFIED_DEMO_STATE",
                "new_value": "CLEAN_GOLD_BASELINE",
                "validation_status": "DEMO_RESET",
                "rule_triggered": "NONE",
                "reverted": False,
                "reverted_by": "",
                "revert_reason": "Append-only DEMO_RESET marker",
            }])
            self.invalidate_cache()
            return {
                "scenario_id": scenario_id,
                "summary": (
                    "Restored next_student_transfer_gold (students_gold & store_shifts_gold) from baseline, "
                    "superseded active staging requests, and appended an immutable DEMO_RESET audit row."
                ),
                "active_dq_warnings": self.get_data_quality_warnings(),
            }

        raise ValueError(f"Unknown scenario_id: {scenario_id}")

    # =========================================================================
    # Gold HR Audit Log & Staged `@Gmail` Outreach Log (`next_student_transfer_gold`)
    # =========================================================================
    def log_hr_decision(
        self,
        student_id: str,
        reviewer_role: str,
        decision: str,
        reason_code: str,
        note: str = "",
    ) -> dict[str, Any]:
        row = {
            "audit_id": f"AUD-{uuid.uuid4().hex[:8].upper()}",
            "timestamp": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "student_id": student_id,
            "reviewer_role": reviewer_role,
            "decision": decision,
            "reason_code": reason_code,
            "note": note,
            "stage": "STAGE_1_100_PCT_REVIEW",
            "downstream_action": "STAGED_DO_NOT_POST",
        }
        sql = f"""
        INSERT INTO {self.gold_fqn('transfer_audit_log')} (
            audit_id, timestamp, student_id, reviewer_role, decision, reason_code, note, stage, downstream_action
        ) VALUES (
            @audit_id, @timestamp, @student_id, @reviewer_role, @decision, @reason_code, @note, @stage, @downstream_action
        )
        """
        cfg = bigquery.QueryJobConfig(
            query_parameters=[bigquery.ScalarQueryParameter(k, "STRING", str(v)) for k, v in row.items()]
        )
        self.client.query(sql, job_config=cfg).result()
        return row

    def get_hr_audit_logs(self, limit: int = 50) -> list[dict[str, Any]]:
        sql = f"""
        SELECT *
        FROM {self.gold_fqn('transfer_audit_log')}
        ORDER BY timestamp DESC
        LIMIT @limit
        """
        cfg = bigquery.QueryJobConfig(
            query_parameters=[bigquery.ScalarQueryParameter("limit", "INT64", int(limit))]
        )
        return [dict(r.items()) for r in self.client.query(sql, job_config=cfg).result()]

    def log_outreach_email(self, email_record: dict[str, Any]) -> dict[str, Any]:
        sql = f"""
        INSERT INTO {self.gold_fqn('outreach_email_log')} (
            email_id, sent_at, recipient_persona, recipient_token, recipient_email,
            subject, body_text, related_dq_rule, related_entity_id, delivery_status, gmail_message_id
        ) VALUES (
            @email_id, @sent_at, @recipient_persona, @recipient_token, @recipient_email,
            @subject, @body_text, @related_dq_rule, @related_entity_id, @delivery_status, @gmail_message_id
        )
        """
        cfg = bigquery.QueryJobConfig(
            query_parameters=[
                bigquery.ScalarQueryParameter("email_id", "STRING", str(email_record.get("email_id", ""))),
                bigquery.ScalarQueryParameter("sent_at", "STRING", str(email_record.get("sent_at", ""))),
                bigquery.ScalarQueryParameter("recipient_persona", "STRING", str(email_record.get("recipient_persona", ""))),
                bigquery.ScalarQueryParameter("recipient_token", "STRING", str(email_record.get("recipient_token", ""))),
                bigquery.ScalarQueryParameter("recipient_email", "STRING", str(email_record.get("recipient_email", ""))),
                bigquery.ScalarQueryParameter("subject", "STRING", str(email_record.get("subject", ""))),
                bigquery.ScalarQueryParameter("body_text", "STRING", str(email_record.get("body_text", ""))),
                bigquery.ScalarQueryParameter("related_dq_rule", "STRING", str(email_record.get("related_dq_rule", ""))),
                bigquery.ScalarQueryParameter("related_entity_id", "STRING", str(email_record.get("related_entity_id", ""))),
                bigquery.ScalarQueryParameter("delivery_status", "STRING", str(email_record.get("delivery_status", "STAGED_FOR_GE_GMAIL"))),
                bigquery.ScalarQueryParameter("gmail_message_id", "STRING", str(email_record.get("gmail_message_id", ""))),
            ]
        )
        self.client.query(sql, job_config=cfg).result()
        return email_record

    def stage_outreach_email_for_ge_gmail(
        self,
        recipient_persona: str,
        recipient_token: str,
        subject: str,
        body_text: str,
        related_dq_rule: str,
        related_entity_id: str,
    ) -> dict[str, Any]:
        """Deterministically build recipient address and stage outreach email in `outreach_email_log` for `@Gmail`."""
        recipient_email = build_tokenized_email_address(recipient_token)
        rec = {
            "email_id": f"EML-{uuid.uuid4().hex[:8].upper()}",
            "sent_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "recipient_persona": recipient_persona,
            "recipient_token": recipient_token.strip().upper(),
            "recipient_email": recipient_email,
            "subject": subject,
            "body_text": body_text,
            "related_dq_rule": related_dq_rule,
            "related_entity_id": related_entity_id,
            "delivery_status": "STAGED_FOR_GE_GMAIL",
            "gmail_message_id": "HANDOFF_TO_GE_NATIVE_GMAIL_CONNECTOR",
        }
        return self.log_outreach_email(rec)

    def get_outreach_emails(self, limit: int = 50) -> list[dict[str, Any]]:
        sql = f"""
        SELECT *
        FROM {self.gold_fqn('outreach_email_log')}
        ORDER BY sent_at DESC
        LIMIT @limit
        """
        cfg = bigquery.QueryJobConfig(
            query_parameters=[bigquery.ScalarQueryParameter("limit", "INT64", int(limit))]
        )
        rows = [dict(r.items()) for r in self.client.query(sql, job_config=cfg).result()]
        for r in rows:
            r.setdefault("recipient_alias", r.get("recipient_email"))
            r.setdefault("dq_rule_id", r.get("related_dq_rule"))
        return rows

    def get_recent_outreach_emails(self, limit: int = 15) -> list[dict[str, Any]]:
        return self.get_outreach_emails(limit=limit)


_DEFAULT_REPO: Optional[BigQueryRepository] = None


def get_bq_repository() -> BigQueryRepository:
    global _DEFAULT_REPO
    if _DEFAULT_REPO is None:
        _DEFAULT_REPO = BigQueryRepository()
    return _DEFAULT_REPO
