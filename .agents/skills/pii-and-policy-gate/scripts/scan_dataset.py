#!/usr/bin/env python3
"""Deterministic PII, UK Under-18 Policy & Prompt-Injection Pre-Flight Scanner."""

from __future__ import annotations

import csv
import json
import re
import sys
from pathlib import Path

FORBIDDEN_HEADERS = {
    "name",
    "full_name",
    "student_name",
    "first_name",
    "last_name",
    "surname",
    "email",
    "student_email",
    "phone",
    "mobile",
    "phone_number",
    "dob",
    "date_of_birth",
    "ni_number",
    "national_insurance",
    "address",
    "home_postcode",
}

UK_PHONE_RE = re.compile(r"(?:\+44\s?7\d{3}|\b07\d{3})\s?\d{3}\s?\d{3}\b")
EMAIL_RE = re.compile(r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b")
UK_NI_RE = re.compile(r"\b[A-CEGHJ-PR-TW-Z]{2}\s?\d{2}\s?\d{2}\s?\d{2}\s?[A-D]\b", re.I)
PROMPT_INJECTION_RE = re.compile(
    r"(ignore\s+(?:all\s+)?previous|system\s+override|bypass\s+eligibility|disregard\s+instructions|<script\b)",
    re.I,
)
STUDENT_TOKEN_RE = re.compile(r"^STU-\d{4,}$")


def _parse_hhmm(val: str) -> int | None:
    m = re.match(r"^(\d{1,2}):(\d{2})$", val.strip())
    if not m:
        return None
    return int(m.group(1)) * 60 + int(m.group(2))


def scan_csv(path: Path) -> dict:
    findings: list[dict] = []
    with path.open(newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        headers = reader.fieldnames or []
        for h in headers:
            if h.strip().lower() in FORBIDDEN_HEADERS:
                findings.append({
                    "severity": "CRITICAL",
                    "category": "FORBIDDEN_PII_COLUMN",
                    "row": 0,
                    "column": h,
                    "detail": f"Raw PII column '{h}' violates Rule 01 (must tokenize upstream to STU-XXXX / age_band).",
                })

        row_count = 0
        for idx, row in enumerate(reader, start=2):
            row_count += 1
            if "student_id" in row and not STUDENT_TOKEN_RE.match((row["student_id"] or "").strip()):
                findings.append({
                    "severity": "HIGH",
                    "category": "INVALID_STUDENT_TOKEN",
                    "row": idx,
                    "column": "student_id",
                    "detail": f"Invalid student_id '{row['student_id']}' (expected STU-XXXX).",
                })

            age_band = (row.get("age_band") or "").strip().upper()
            req_end = _parse_hhmm(row.get("preferred_latest_finish", "") or row.get("end_time", "") or "")
            if age_band == "UNDER_18" and req_end is not None and (req_end > 22 * 60 or req_end < 6 * 60):
                findings.append({
                    "severity": "CRITICAL",
                    "category": "UNDER_18_NIGHT_SHIFT_REQUEST",
                    "row": idx,
                    "column": "preferred_latest_finish",
                    "detail": (
                        f"UNDER_18 record requests finish past 22:00 ({row.get('preferred_latest_finish') or row.get('end_time')}) "
                        "— blocked under UK Young Worker regulations (https://www.gov.uk/rest-breaks-work/young-workers)."
                    ),
                })

            for col, raw_val in row.items():
                val = (raw_val or "").strip()
                if not val:
                    continue
                if UK_PHONE_RE.search(val):
                    findings.append({
                        "severity": "CRITICAL",
                        "category": "RAW_UK_PHONE_DETECTED",
                        "row": idx,
                        "column": col,
                        "detail": "Detected raw UK mobile number (07... / +447...).",
                    })
                if EMAIL_RE.search(val):
                    findings.append({
                        "severity": "CRITICAL",
                        "category": "RAW_EMAIL_DETECTED",
                        "row": idx,
                        "column": col,
                        "detail": "Detected raw email address.",
                    })
                if UK_NI_RE.search(val):
                    findings.append({
                        "severity": "CRITICAL",
                        "category": "RAW_UK_NI_NUMBER",
                        "row": idx,
                        "column": col,
                        "detail": "Detected UK National Insurance number pattern.",
                    })
                if PROMPT_INJECTION_RE.search(val):
                    findings.append({
                        "severity": "CRITICAL",
                        "category": "PROMPT_INJECTION_PAYLOAD",
                        "row": idx,
                        "column": col,
                        "detail": f"Detected untrusted prompt-injection string in '{col}' — quarantine before LLM/Model Armor.",
                    })

    status = "BLOCKED" if findings else "PASSED"
    return {
        "file": str(path),
        "rows_scanned": row_count,
        "status": status,
        "finding_count": len(findings),
        "findings": findings[:25],
    }


def main() -> int:
    if len(sys.argv) < 2:
        print("Usage: scan_dataset.py <csv_path> [<csv_path_2> ...]", file=sys.stderr)
        return 2
    exit_code = 0
    for arg in sys.argv[1:]:
        p = Path(arg)
        if not p.exists():
            print(json.dumps({"file": arg, "status": "ERROR", "detail": "File not found"}))
            exit_code = 2
            continue
        report = scan_csv(p)
        print(json.dumps(report, indent=2))
        if report["status"] != "PASSED":
            exit_code = 1
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
