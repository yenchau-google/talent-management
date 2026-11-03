#!/usr/bin/env python3
"""Provision BigQuery Gold Dataset, Audit Tables, Real-Time DQ View, and Dataplex DataScan.

Single Source of Truth:
  - Project:  talent-management-513520 (from ARGOLIS_PROJECT_ID in .env)
  - Location: europe-west2 (London)
  - Dataset:  next_student_transfer
  - Tables:
      1. students_gold       (1,050 tokenized STU-XXXX records)
      2. store_shifts_gold   (470 UK stores, ~1,173 shift slots)
      3. change_audit_log    (immutable before/after log of Student & Store Manager edits)
      4. placements_staging  (Stage 1 HITL 'Do Not Post' staging output)
      5. transfer_audit_log  (HR Stage 1 exception approvals & overrides)
      6. outreach_email_log  (Google Workspace / Gmail tokenized outreach log)
  - Views:
      1. v_data_quality_warnings (Real-time evaluation of DQ-01 .. DQ-06)
  - Dataplex Data Quality Scan:
      projects/<PROJECT_ID>/locations/europe-west2/dataScans/next-student-transfer-dq-scan
"""

from __future__ import annotations

import json
import os
import random
import subprocess
import urllib.error
import urllib.request
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from google.cloud import bigquery

SEED = 20261104

UNI_HUB_STORES = [
    ("STORE-012", "Manchester Arndale", "North West", True),
    ("STORE-013", "Manchester Trafford Centre", "North West", True),
    ("STORE-014", "Manchester Fort", "North West", True),
    ("STORE-019", "Leeds Trinity", "Yorkshire", True),
    ("STORE-020", "Leeds White Rose", "Yorkshire", True),
    ("STORE-021", "Headingley Retail Park", "Yorkshire", True),
    ("STORE-042", "Nottingham Victoria", "East Midlands", True),
    ("STORE-043", "Nottingham Giltbrook", "East Midlands", True),
    ("STORE-044", "Derby Derbion", "East Midlands", True),
    ("STORE-051", "Edinburgh St James", "Scotland", True),
    ("STORE-052", "Edinburgh Straiton", "Scotland", True),
    ("STORE-061", "Birmingham Bullring", "West Midlands", True),
    ("STORE-062", "Birmingham Fort", "West Midlands", True),
    ("STORE-071", "Bristol Cabot Circus", "South West", True),
    ("STORE-072", "Bristol Cribbs Causeway", "South West", True),
    ("STORE-081", "Sheffield Meadowhall", "Yorkshire", True),
    ("STORE-091", "Liverpool ONE", "North West", True),
    ("STORE-101", "Newcastle Eldon Square", "North East", True),
    ("STORE-111", "Cardiff St Davids", "Wales", True),
    ("STORE-121", "London Stratford City", "London & South East", True),
]

REGIONS = [
    "London & South East",
    "North West",
    "Yorkshire",
    "East Midlands",
    "West Midlands",
    "South West",
    "Scotland",
    "North East",
    "Wales",
    "East of England",
]

DAYS = ["MON", "TUE", "WED", "THU", "FRI", "SAT", "SUN"]


def load_dotenv_file() -> None:
    env_path = Path(__file__).resolve().parents[1] / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip())


def build_stores(rng: random.Random) -> list[dict[str, Any]]:
    stores: list[dict[str, Any]] = []
    used_ids = {s[0] for s in UNI_HUB_STORES}
    for sid, sname, region, is_hub in UNI_HUB_STORES:
        idx_num = int(sid.split("-")[1])
        stores.append({
            "store_id": sid,
            "store_name": sname,
            "region": region,
            "manager_token": f"SM-{idx_num:03d}",
            "is_uni_hub": is_hub,
        })

    n = 1
    while len(stores) < 470:
        sid = f"STORE-{n:03d}"
        n += 1
        if sid in used_ids:
            continue
        used_ids.add(sid)
        region = REGIONS[len(stores) % len(REGIONS)]
        stores.append({
            "store_id": sid,
            "store_name": f"NEXT Regional Store {sid.split('-')[1]}",
            "region": region,
            "manager_token": f"SM-{int(sid.split('-')[1]):03d}",
            "is_uni_hub": False,
        })
    stores.sort(key=lambda x: x["store_id"])
    return stores


def build_shifts(rng: random.Random, stores: list[dict[str, Any]]) -> list[dict[str, Any]]:
    shifts: list[dict[str, Any]] = []
    shf_seq = 1001
    now_iso = "2026-09-01T08:00:00Z"

    def add_shift(
        store_id: str,
        store_name: str,
        region: str,
        manager_token: str,
        day: str,
        start: str,
        end: str,
        hours: float,
        avail_from: str = "2026-09-21",
        green_box: bool = False,
        cmp_cleared: bool = True,
        batch_wave: str = "BATCH_1",
        shift_id_override: str | None = None,
    ) -> None:
        nonlocal shf_seq
        sid = shift_id_override or f"SHF-{shf_seq:04d}"
        if not shift_id_override:
            shf_seq += 1
        shifts.append({
            "shift_id": sid,
            "store_id": store_id,
            "store_name": store_name,
            "region": region,
            "manager_token": manager_token,
            "day_of_week": day,
            "start_time": start,
            "end_time": end,
            "weekly_hours": round(float(hours), 1),
            "available_from": avail_from,
            "is_green_box_protected": bool(green_box),
            "cmp_3day_cleared": bool(cmp_cleared),
            "batch_wave": batch_wave,
            "updated_at": now_iso,
            "updated_by_token": "SYSTEM-SEED",
        })

    store_map = {s["store_id"]: s for s in stores}

    # 1. Curated hero store shifts
    # STORE-042 Nottingham Victoria: SHF-1001 (SAT 7.5h), SHF-1002 (SUN 6.0h), SHF-1003 (THU 4.0h Green Box protected)
    s42 = store_map["STORE-042"]
    add_shift(s42["store_id"], s42["store_name"], s42["region"], s42["manager_token"], "SAT", "09:00", "16:30", 7.5)
    add_shift(s42["store_id"], s42["store_name"], s42["region"], s42["manager_token"], "SUN", "10:00", "16:00", 6.0)
    add_shift(
        s42["store_id"], s42["store_name"], s42["region"], s42["manager_token"],
        "THU", "17:00", "21:00", 4.0, green_box=True, cmp_cleared=True,
    )

    # STORE-043 Nottingham Giltbrook
    s43 = store_map["STORE-043"]
    for day, st, en, hr in [
        ("SAT", "09:30", "17:00", 7.5),
        ("SUN", "10:00", "16:30", 6.5),
        ("SAT", "12:00", "18:00", 6.0),
        ("WED", "17:00", "21:00", 4.0),
        ("FRI", "17:00", "21:00", 4.0),
    ] + [("SAT", "09:30", "17:00", 7.5), ("SUN", "10:00", "16:30", 6.5)] * 10:
        add_shift(s43["store_id"], s43["store_name"], s43["region"], s43["manager_token"], day, st, en, hr)

    # STORE-012 Manchester Arndale
    s12 = store_map["STORE-012"]
    add_shift(s12["store_id"], s12["store_name"], s12["region"], s12["manager_token"], "FRI", "18:00", "23:00", 5.0)
    add_shift(s12["store_id"], s12["store_name"], s12["region"], s12["manager_token"], "SAT", "10:00", "16:00", 6.0)
    for _ in range(8):
        d = rng.choice(["SAT", "SUN"])
        add_shift(s12["store_id"], s12["store_name"], s12["region"], s12["manager_token"], d, "09:00", "16:00", 7.0)
    for _ in range(18):
        add_shift(s12["store_id"], s12["store_name"], s12["region"], s12["manager_token"], "TUE", "17:00", "21:00", 4.0)

    # STORE-019 Leeds Trinity
    s19 = store_map["STORE-019"]
    for _ in range(7):
        d = rng.choice(["SAT", "SUN"])
        add_shift(s19["store_id"], s19["store_name"], s19["region"], s19["manager_token"], d, "09:30", "16:30", 7.0)
    for _ in range(16):
        add_shift(s19["store_id"], s19["store_name"], s19["region"], s19["manager_token"], "THU", "17:00", "21:00", 4.0)
    add_shift(
        s19["store_id"], s19["store_name"], s19["region"], s19["manager_token"],
        "SAT", "09:00", "16:30", 7.5, cmp_cleared=False, batch_wave="BATCH_2_PENDING_CMP", shift_id_override="SHF-9001",
    )
    add_shift(
        s19["store_id"], s19["store_name"], s19["region"], s19["manager_token"],
        "SUN", "10:00", "16:30", 6.5, cmp_cleared=False, batch_wave="BATCH_2_PENDING_CMP", shift_id_override="SHF-9002",
    )
    add_shift(
        s19["store_id"], s19["store_name"], s19["region"], s19["manager_token"],
        "SAT", "11:00", "17:00", 6.0, cmp_cleared=False, batch_wave="BATCH_2_PENDING_CMP", shift_id_override="SHF-9003",
    )

    # STORE-051 Edinburgh St James
    s51 = store_map["STORE-051"]
    add_shift(
        s51["store_id"], s51["store_name"], s51["region"], s51["manager_token"],
        "SAT", "09:30", "17:00", 7.5, avail_from="2026-10-12",
    )
    for _ in range(6):
        add_shift(
            s51["store_id"], s51["store_name"], s51["region"], s51["manager_token"],
            rng.choice(["SAT", "SUN"]), "10:00", "16:30", 6.5, avail_from="2026-09-21",
        )

    curated_ids = {"STORE-042", "STORE-043", "STORE-012", "STORE-019", "STORE-051"}
    shift_templates = [
        ("SAT", "09:00", "16:30", 7.5),
        ("SAT", "10:00", "16:00", 6.0),
        ("SUN", "10:30", "16:30", 6.0),
        ("WED", "17:00", "21:00", 4.0),
        ("THU", "17:00", "21:00", 4.0),
        ("FRI", "18:00", "22:30", 4.5),
    ]
    for s in stores:
        if s["store_id"] in curated_ids:
            continue
        num_shifts = rng.randint(6, 10) if s["is_uni_hub"] else rng.randint(1, 3)
        for _ in range(num_shifts):
            day, st, en, hr = rng.choice(shift_templates)
            gb = rng.random() < 0.06
            cmp_ok = False if (not gb and rng.random() < 0.05) else True
            avail = "2026-10-12" if rng.random() < 0.06 else "2026-09-21"
            add_shift(
                s["store_id"], s["store_name"], s["region"], s["manager_token"],
                day, st, en, hr, avail_from=avail, green_box=gb, cmp_cleared=cmp_ok,
            )

    return shifts


def build_students(rng: random.Random, stores: list[dict[str, Any]]) -> list[dict[str, Any]]:
    hub_ids = [s[0] for s in UNI_HUB_STORES]
    non_hub_ids = [s["store_id"] for s in stores if not s["is_uni_hub"]]
    students: dict[str, dict[str, Any]] = {}
    now_iso = "2026-09-01T08:00:00Z"

    def make_stu(
        student_id: str,
        age_band: str,
        contract_type: str,
        star_rating: int,
        review_up_to_date: bool,
        do_not_make_leaver: bool,
        home_store_id: str,
        worked_at_choice_1_before: bool,
        choice_1_store_id: str,
        choice_2_store_id: str,
        choice_3_store_id: str,
        available_days: str,
        preferred_latest_finish: str,
        target_transfer_date: str,
        form_submitted_at: str,
        small_contract_opt_in: bool,
        home_store_release_status: str = "PENDING_DESTINATION_ACCEPT",
    ) -> dict[str, Any]:
        return {
            "student_id": student_id,
            "age_band": age_band,
            "contract_type": contract_type,
            "star_rating": int(star_rating),
            "review_up_to_date": bool(review_up_to_date),
            "do_not_make_leaver": bool(do_not_make_leaver),
            "home_store_id": home_store_id,
            "worked_at_choice_1_before": bool(worked_at_choice_1_before),
            "choice_1_store_id": choice_1_store_id,
            "choice_2_store_id": choice_2_store_id,
            "choice_3_store_id": choice_3_store_id,
            "available_days": available_days,
            "preferred_latest_finish": preferred_latest_finish,
            "target_transfer_date": target_transfer_date,
            "form_submitted_at": form_submitted_at,
            "small_contract_opt_in": bool(small_contract_opt_in),
            "home_store_release_status": home_store_release_status,
            "updated_at": now_iso,
            "updated_by_token": "SYSTEM-SEED",
        }

    students["STU-0018"] = make_stu(
        "STU-0018", "ADULT_18_PLUS", "PERM", 4, True, True, "STORE-210", True,
        "STORE-042", "STORE-043", "STORE-044", "SAT|SUN", "21:00", "2026-09-21",
        "2026-07-01T08:12:00Z", False,
    )
    students["STU-0049"] = make_stu(
        "STU-0049", "ADULT_18_PLUS", "PERM", 4, True, True, "STORE-215", False,
        "STORE-042", "STORE-043", "STORE-044", "SAT|SUN", "21:00", "2026-09-21",
        "2026-07-01T09:05:00Z", False,
    )
    students["STU-0412"] = make_stu(
        "STU-0412", "ADULT_18_PLUS", "PERM", 4, True, True, "STORE-302", False,
        "STORE-042", "STORE-043", "STORE-044", "SAT|SUN", "21:00", "2026-09-21",
        "2026-07-03T14:22:00Z", True,
    )
    students["STU-0109"] = make_stu(
        "STU-0109", "UNDER_18", "PERM", 4, True, True, "STORE-180", True,
        "STORE-012", "STORE-013", "STORE-014", "FRI|SAT", "21:30", "2026-09-21",
        "2026-07-01T07:45:00Z", False,
    )
    students["STU-0221"] = make_stu(
        "STU-0221", "ADULT_18_PLUS", "PERM", 4, True, True, "STORE-250", False,
        "STORE-012", "STORE-013", "STORE-014", "TUE", "21:30", "2026-09-21",
        "2026-07-02T10:15:00Z", True,
    )
    students["STU-0222"] = make_stu(
        "STU-0222", "ADULT_18_PLUS", "PERM", 3, True, True, "STORE-260", False,
        "STORE-019", "STORE-020", "STORE-021", "THU", "21:30", "2026-09-21",
        "2026-07-04T11:30:00Z", False,
    )
    students["STU-0305"] = make_stu(
        "STU-0305", "ADULT_18_PLUS", "PERM", 4, True, True, "STORE-330", True,
        "STORE-051", "STORE-052", "STORE-101", "SAT", "21:00", "2026-09-28",
        "2026-07-01T08:00:00Z", False,
    )

    base_dt = date(2026, 7, 1)
    for i in range(1, 1051):
        sid = f"STU-{i:04d}"
        if sid in students:
            continue

        if rng.random() < 0.72:
            c1 = rng.choice(hub_ids)
            other_hubs = [h for h in hub_ids if h != c1]
            c2 = rng.choice(other_hubs)
            c3 = rng.choice(non_hub_ids)
        else:
            c1, c2, c3 = rng.sample(non_hub_ids, 3)

        home = rng.choice([h for h in non_hub_ids if h not in {c1, c2, c3}])
        age_band = "UNDER_18" if rng.random() < 0.08 else "ADULT_18_PLUS"

        r_elig = rng.random()
        if r_elig < 0.42:
            star, ctype, rev_ok = 4, "PERM", True
        elif r_elig < 0.88:
            star, ctype, rev_ok = 3, "PERM", True
        elif r_elig < 0.94:
            star, ctype, rev_ok = int(rng.choice(["1", "2"])), "PERM", True
        elif r_elig < 0.98:
            star, ctype, rev_ok = 3, "TEMP", True
        else:
            star, ctype, rev_ok = 3, "PERM", False

        if c1 in {"STORE-012", "STORE-019"} and star == 4 and rng.random() < 0.35:
            avail_days = "TUE" if c1 == "STORE-012" else "THU"
        else:
            day_pool = rng.sample(DAYS, k=rng.randint(2, 4))
            if "SAT" not in day_pool and rng.random() < 0.65:
                day_pool[0] = "SAT"
            avail_days = "|".join(sorted(set(day_pool), key=DAYS.index))

        latest_finish = (
            rng.choice(["20:00", "21:00", "21:30"])
            if age_band == "UNDER_18"
            else rng.choice(["21:00", "22:00", "23:00"])
        )
        sub_day = base_dt + timedelta(days=rng.randint(0, 28))
        sub_ts = f"{sub_day.isoformat()}T{rng.randint(7, 21):02d}:{rng.randint(0, 59):02d}:00Z"
        transfer_dt = rng.choice(["2026-09-21", "2026-09-28", "2026-10-05"])

        students[sid] = make_stu(
            sid,
            age_band,
            ctype,
            star,
            rev_ok,
            True,
            home,
            rng.random() < 0.18,
            c1,
            c2,
            c3,
            avail_days,
            latest_finish,
            transfer_dt,
            sub_ts,
            rng.random() < 0.55,
        )

    return [students[f"STU-{i:04d}"] for i in range(1, 1051)]


def generate_seed_rows() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    rng = random.Random(SEED)
    stores = build_stores(rng)
    shifts = build_shifts(rng, stores)
    students = build_students(rng, stores)
    return students, shifts


def provision_bigquery(project_id: str, dataset_id: str, location: str) -> None:
    client = bigquery.Client(project=project_id, location=location)
    ds_ref = bigquery.Dataset(f"{project_id}.{dataset_id}")
    ds_ref.location = location
    ds_ref.description = (
        "NEXT Student Transfer Single Source of Truth (Zero Raw PII, Tokenized STU-XXXX & SM-XXX)"
    )
    client.create_dataset(ds_ref, exists_ok=True)
    print(f"[BQ] Dataset ready: {project_id}.{dataset_id} ({location})")

    students, shifts = generate_seed_rows()

    students_schema = [
        bigquery.SchemaField("student_id", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("age_band", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("contract_type", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("star_rating", "INT64", mode="REQUIRED"),
        bigquery.SchemaField("review_up_to_date", "BOOL", mode="REQUIRED"),
        bigquery.SchemaField("do_not_make_leaver", "BOOL", mode="REQUIRED"),
        bigquery.SchemaField("home_store_id", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("worked_at_choice_1_before", "BOOL", mode="REQUIRED"),
        bigquery.SchemaField("choice_1_store_id", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("choice_2_store_id", "STRING", mode="NULLABLE"),
        bigquery.SchemaField("choice_3_store_id", "STRING", mode="NULLABLE"),
        bigquery.SchemaField("available_days", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("preferred_latest_finish", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("target_transfer_date", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("form_submitted_at", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("small_contract_opt_in", "BOOL", mode="REQUIRED"),
        bigquery.SchemaField("home_store_release_status", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("updated_at", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("updated_by_token", "STRING", mode="REQUIRED"),
    ]
    shifts_schema = [
        bigquery.SchemaField("shift_id", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("store_id", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("store_name", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("region", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("manager_token", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("day_of_week", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("start_time", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("end_time", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("weekly_hours", "FLOAT64", mode="REQUIRED"),
        bigquery.SchemaField("available_from", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("is_green_box_protected", "BOOL", mode="REQUIRED"),
        bigquery.SchemaField("cmp_3day_cleared", "BOOL", mode="REQUIRED"),
        bigquery.SchemaField("batch_wave", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("updated_at", "STRING", mode="REQUIRED"),
        bigquery.SchemaField("updated_by_token", "STRING", mode="REQUIRED"),
    ]

    job_cfg_stu = bigquery.LoadJobConfig(
        schema=students_schema,
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
    )
    client.load_table_from_json(
        students, f"{project_id}.{dataset_id}.students_gold", job_config=job_cfg_stu
    ).result()
    print(f"[BQ] Seeded {len(students)} rows -> {project_id}.{dataset_id}.students_gold")

    job_cfg_shf = bigquery.LoadJobConfig(
        schema=shifts_schema,
        write_disposition=bigquery.WriteDisposition.WRITE_TRUNCATE,
    )
    client.load_table_from_json(
        shifts, f"{project_id}.{dataset_id}.store_shifts_gold", job_config=job_cfg_shf
    ).result()
    print(f"[BQ] Seeded {len(shifts)} rows -> {project_id}.{dataset_id}.store_shifts_gold")

    ddl_statements = [
        f"""
        CREATE TABLE IF NOT EXISTS `{project_id}.{dataset_id}.change_audit_log` (
            change_id STRING NOT NULL,
            changed_at STRING NOT NULL,
            actor_persona STRING NOT NULL,
            actor_token STRING NOT NULL,
            entity_type STRING NOT NULL,
            entity_id STRING NOT NULL,
            field_changed STRING NOT NULL,
            old_value STRING,
            new_value STRING,
            validation_status STRING NOT NULL,
            rule_triggered STRING,
            reverted BOOL NOT NULL,
            reverted_by STRING,
            revert_reason STRING
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS `{project_id}.{dataset_id}.placements_staging` (
            batch_run_id STRING NOT NULL,
            staged_at STRING NOT NULL,
            student_id STRING NOT NULL,
            placed_store_id STRING NOT NULL,
            placed_store_name STRING NOT NULL,
            choice_rank INT64 NOT NULL,
            matched_shift_ids STRING NOT NULL,
            total_weekly_hours FLOAT64 NOT NULL,
            earliest_shift_date STRING NOT NULL,
            queue_status STRING NOT NULL,
            home_store_id STRING NOT NULL,
            home_store_release_trigger STRING NOT NULL,
            downstream_post_mode STRING NOT NULL
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS `{project_id}.{dataset_id}.transfer_audit_log` (
            audit_id STRING NOT NULL,
            timestamp STRING NOT NULL,
            student_id STRING NOT NULL,
            reviewer_role STRING NOT NULL,
            decision STRING NOT NULL,
            reason_code STRING NOT NULL,
            note STRING,
            stage STRING NOT NULL,
            downstream_action STRING NOT NULL
        )
        """,
        f"""
        CREATE TABLE IF NOT EXISTS `{project_id}.{dataset_id}.outreach_email_log` (
            email_id STRING NOT NULL,
            sent_at STRING NOT NULL,
            recipient_persona STRING NOT NULL,
            recipient_token STRING NOT NULL,
            recipient_email STRING NOT NULL,
            subject STRING NOT NULL,
            body_text STRING NOT NULL,
            related_dq_rule STRING,
            related_entity_id STRING,
            delivery_status STRING NOT NULL,
            gmail_message_id STRING
        )
        """,
        f"""
        CREATE OR REPLACE VIEW `{project_id}.{dataset_id}.v_data_quality_warnings` AS
        -- DQ-01: Student duplicate store choices
        SELECT
            'DQ-01' AS rule_id,
            'VALIDITY' AS dimension,
            'WARNING' AS severity,
            'STUDENT' AS entity_type,
            student_id AS entity_id,
            student_id AS owner_token,
            CONCAT(
                'Student ', student_id,
                ' selected duplicate store choices (Choice 1=', choice_1_store_id,
                ', Choice 2=', IFNULL(choice_2_store_id, ''),
                ', Choice 3=', IFNULL(choice_3_store_id, ''), ').'
            ) AS issue_summary,
            'Email student to pick 3 distinct store choices before Pass 2/3 matching.' AS recommended_comms_action,
            updated_at
        FROM `{project_id}.{dataset_id}.students_gold`
        WHERE choice_1_store_id = choice_2_store_id
           OR choice_1_store_id = choice_3_store_id
           OR (choice_2_store_id IS NOT NULL AND choice_2_store_id != '' AND choice_2_store_id = choice_3_store_id)

        UNION ALL

        -- DQ-02: Student target transfer date outside autumn window
        SELECT
            'DQ-02' AS rule_id,
            'VALIDITY' AS dimension,
            'WARNING' AS severity,
            'STUDENT' AS entity_type,
            student_id AS entity_id,
            student_id AS owner_token,
            CONCAT(
                'Student ', student_id,
                ' requested target_transfer_date=', target_transfer_date,
                ' outside standard autumn intake window (2026-09-07 to 2026-10-19).'
            ) AS issue_summary,
            'Email student and home Store Manager to confirm term start date and release window.' AS recommended_comms_action,
            updated_at
        FROM `{project_id}.{dataset_id}.students_gold`
        WHERE SAFE.PARSE_DATE('%Y-%m-%d', target_transfer_date) IS NULL
           OR SAFE.PARSE_DATE('%Y-%m-%d', target_transfer_date) NOT BETWEEN DATE '2026-09-07' AND DATE '2026-10-19'

        UNION ALL

        -- DQ-06: UK Young Worker UNDER_18 late finish request (>22:00)
        SELECT
            'DQ-06' AS rule_id,
            'CONFORMITY' AS dimension,
            'BLOCKING' AS severity,
            'STUDENT' AS entity_type,
            student_id AS entity_id,
            student_id AS owner_token,
            CONCAT(
                'UK Young Worker violation: ', student_id,
                ' (UNDER_18) requested preferred_latest_finish=', preferred_latest_finish,
                ' (>22:00 statutory limit).'
            ) AS issue_summary,
            'Hard-block late shifts >22:00 for this student and email student confirming statutory 22:00 curfews apply.' AS recommended_comms_action,
            updated_at
        FROM `{project_id}.{dataset_id}.students_gold`
        WHERE age_band = 'UNDER_18' AND preferred_latest_finish > '22:00'

        UNION ALL

        -- DQ-03: Store shift weekly hours outside [4.0, 12.0] floor/ceiling
        SELECT
            'DQ-03' AS rule_id,
            'VALIDITY' AS dimension,
            'BLOCKING' AS severity,
            'STORE_SHIFT' AS entity_type,
            shift_id AS entity_id,
            manager_token AS owner_token,
            CONCAT(
                'Store shift ', shift_id, ' at ', store_id, ' (', store_name,
                ') has invalid weekly_hours=', CAST(weekly_hours AS STRING),
                'h (outside 4.0h minimum contract floor / 12.0h ceiling).'
            ) AS issue_summary,
            CONCAT(
                'Hold shift ', shift_id, ' from matching and email Store Manager ',
                manager_token, ' to restore shift hours to >=4.0h.'
            ) AS recommended_comms_action,
            updated_at
        FROM `{project_id}.{dataset_id}.store_shifts_gold`
        WHERE weekly_hours < 4.0 OR weekly_hours > 12.0

        UNION ALL

        -- DQ-05: Unapproved Green Box protection unlock detected in change_audit_log
        SELECT
            'DQ-05' AS rule_id,
            'GOVERNANCE' AS dimension,
            'BLOCKING' AS severity,
            'STORE_SHIFT' AS entity_type,
            c.entity_id AS entity_id,
            c.actor_token AS owner_token,
            CONCAT(
                'Unapproved Green Box unlock on shift ', c.entity_id,
                ' by ', c.actor_token, ' (changed is_green_box_protected from true to false without Regional HR sign-off).'
            ) AS issue_summary,
            CONCAT(
                'Keep Green Box lock active on ', c.entity_id,
                ', revert unauthorized flag change, and email Store Manager ', c.actor_token, '.'
            ) AS recommended_comms_action,
            c.changed_at AS updated_at
        FROM `{project_id}.{dataset_id}.change_audit_log` c
        JOIN `{project_id}.{dataset_id}.store_shifts_gold` s
          ON c.entity_id = s.shift_id
        WHERE c.field_changed = 'is_green_box_protected'
          AND LOWER(c.old_value) = 'true'
          AND LOWER(c.new_value) = 'false'
          AND c.reverted = FALSE
          AND s.is_green_box_protected = FALSE
        """,
    ]

    for stmt in ddl_statements:
        client.query(stmt).result()
    print(f"[BQ] Audit tables & real-time view v_data_quality_warnings ready in {project_id}.{dataset_id}")


def provision_dataplex_datascan(project_id: str, dataset_id: str, location: str) -> None:
    scan_id = "next-student-transfer-dq-scan"
    token = subprocess.check_output(["gcloud", "auth", "print-access-token"], text=True).strip()
    parent_url = f"https://dataplex.googleapis.com/v1/projects/{project_id}/locations/{location}/dataScans"
    scan_url = f"{parent_url}/{scan_id}"

    payload = {
        "description": "NEXT Student Transfer Declarative Data Quality Gate (DQ-01, DQ-02, DQ-06)",
        "displayName": "NEXT Student Transfer Data Quality Scan",
        "data": {
            "resource": f"//bigquery.googleapis.com/projects/{project_id}/datasets/{dataset_id}/tables/students_gold"
        },
        "dataQualitySpec": {
            "rules": [
                {
                    "name": "dq01-no-duplicate-store-choices",
                    "dimension": "VALIDITY",
                    "description": "DQ-01: Choice 1, Choice 2, and Choice 3 store IDs must be distinct.",
                    "rowConditionExpectation": {
                        "sqlExpression": (
                            "choice_1_store_id != choice_2_store_id "
                            "AND choice_1_store_id != choice_3_store_id "
                            "AND (choice_2_store_id != choice_3_store_id OR choice_2_store_id = '' OR choice_3_store_id = '')"
                        )
                    },
                },
                {
                    "name": "dq02-valid-transfer-date-window",
                    "dimension": "VALIDITY",
                    "description": "DQ-02: Target transfer date must fall within the autumn student intake window.",
                    "rowConditionExpectation": {
                        "sqlExpression": "SAFE.PARSE_DATE('%Y-%m-%d', target_transfer_date) BETWEEN DATE '2026-09-07' AND DATE '2026-10-19'"
                    },
                },
                {
                    "name": "dq06-under18-working-hours-guardrail",
                    "dimension": "CONFORMITY",
                    "description": "DQ-06: UK Young Worker statutory rule — UNDER_18 preferred_latest_finish must not exceed 22:00.",
                    "rowConditionExpectation": {
                        "sqlExpression": "NOT (age_band = 'UNDER_18' AND preferred_latest_finish > '22:00')"
                    },
                },
            ]
        },
    }

    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    # Check if DataScan already exists
    req_get = urllib.request.Request(scan_url, headers=headers, method="GET")
    exists = False
    try:
        with urllib.request.urlopen(req_get, timeout=15) as resp:
            if resp.status == 200:
                exists = True
    except urllib.error.HTTPError as e:
        if e.code != 404:
            print(f"[Dataplex] GET check status {e.code}: {e.read().decode('utf-8', errors='ignore')}")

    if not exists:
        create_url = f"{parent_url}?dataScanId={scan_id}"
        req_create = urllib.request.Request(
            create_url,
            data=json.dumps(payload).encode("utf-8"),
            headers=headers,
            method="POST",
        )
        try:
            with urllib.request.urlopen(req_create, timeout=20) as resp:
                body = json.loads(resp.read().decode("utf-8"))
                print(f"[Dataplex] Created DataScan {scan_id} in {location}: operation={body.get('name')}")
        except urllib.error.HTTPError as e:
            print(f"[Dataplex] Create returned {e.code}: {e.read().decode('utf-8', errors='ignore')}")
    else:
        print(f"[Dataplex] DataScan {scan_id} already exists in {location}.")


def main() -> None:
    load_dotenv_file()
    project_id = os.environ.get("ARGOLIS_PROJECT_ID", "talent-management-513520")
    dataset_id = os.environ.get("BQ_DATASET_ID", "next_student_transfer")
    location = os.environ.get("GCP_REGION", "europe-west2")
    provision_bigquery(project_id, dataset_id, location)
    provision_dataplex_datascan(project_id, dataset_id, location)


if __name__ == "__main__":
    main()
