#!/usr/bin/env python3
"""Provisions the 2-Dataset BigQuery Medallion Architecture & Dataplex Auto Data Quality Scan in europe-west2.

Datasets created:
  1. `${GOOGLE_CLOUD_PROJECT}.next_student_transfer_staging` (Bronze intake, audit log, & 0ms DQ SQL view)
  2. `${GOOGLE_CLOUD_PROJECT}.next_student_transfer_gold` (Trusted Gold tables, audit logs, & stored procedures)

Executes the parameterized SQL DDL scripts in `sql/`:
  - `sql/01_staging_schema.sql`
  - `sql/02_gold_schema_and_seed.sql`
  - `sql/03_matching_and_simulation_procs.sql`
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from google.cloud import bigquery

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / ".env")

PROJECT_ID = os.environ.get("GOOGLE_CLOUD_PROJECT", "next-student-transfer-prod")
LOCATION = os.environ.get("GCP_REGION", "europe-west2")
STAGING_DATASET_ID = os.environ.get("BQ_STAGING_DATASET_ID", "next_student_transfer_staging")
GOLD_DATASET_ID = os.environ.get("BQ_GOLD_DATASET_ID", "next_student_transfer_gold")

SQL_FILES = [
    ROOT / "sql" / "01_staging_schema.sql",
    ROOT / "sql" / "02_gold_schema_and_seed.sql",
    ROOT / "sql" / "03_matching_and_simulation_procs.sql",
]


def ensure_datasets(client: bigquery.Client) -> None:
    """Create the Bronze Staging and Trusted Gold BigQuery datasets in europe-west2 if they do not exist."""
    for ds_id, desc in [
        (
            STAGING_DATASET_ID,
            "NEXT PLC Student Transfer — Bronze Portal Intake, Change Audit Log & Real-Time DQ Quarantine View",
        ),
        (
            GOLD_DATASET_ID,
            "NEXT PLC Student Transfer — Trusted Gold Colleague & Shift Tables, Placements Staging & HITL Audit Log",
        ),
    ]:
        ds_ref = bigquery.Dataset(f"{PROJECT_ID}.{ds_id}")
        ds_ref.location = LOCATION
        ds_ref.description = desc
        client.create_dataset(ds_ref, exists_ok=True)
        print(f"[OK] BigQuery dataset ensured: {PROJECT_ID}.{ds_id} ({LOCATION})")


def execute_sql_scripts(client: bigquery.Client) -> None:
    """Render `${GOOGLE_CLOUD_PROJECT}` in each SQL file and execute in BigQuery."""
    for sql_path in SQL_FILES:
        raw_sql = sql_path.read_text(encoding="utf-8")
        rendered_sql = raw_sql.replace("${GOOGLE_CLOUD_PROJECT}", PROJECT_ID)
        print(f"[RUN] Executing {sql_path.relative_to(ROOT)} ...")
        client.query(rendered_sql).result()
        print(f"[OK] Completed {sql_path.name}")


def main() -> None:
    client = bigquery.Client(project=PROJECT_ID, location=LOCATION)
    ensure_datasets(client)
    execute_sql_scripts(client)
    print(f"[DONE] BigQuery Medallion provisioning complete in {PROJECT_ID} ({LOCATION}).")


if __name__ == "__main__":
    main()
