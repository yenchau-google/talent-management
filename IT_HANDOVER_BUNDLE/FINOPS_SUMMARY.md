# NEXT PLC — FinOps Cost & Token Quota Attestation (`finops-cost-estimator`)

**Runtime Model:** `gemini-2.5-flash` (Vertex AI `europe-west2`)  
**Compute Posture:** Cloud Run Serverless (`--min-instances=0 --max-instances=2 --cpu-throttling`)

---

## 1. Anti-Pattern Audit (`src/*.py`)

- **Static AST Loop Verification:** `PASSED` — Zero `generate_content`, `Runner`, or `LlmAgent` invocations inside `for` or `while` loops across `src/matcher.py`, `src/agent.py`, `src/model_armor_guard.py`, and `src/server.py`.
- **Vectorized In-Memory Matching (`O(N log N)`):** All `1,050` student applications and `1,093` store shifts across `470` UK stores are matched in `<50ms` of pure Python CPU time (**`0` LLM tokens** consumed for matching math).
- **Output Token Cap:** `max_output_tokens=1024` enforced on all conversational explanation turns in `src/agent.py`.

---

## 2. Monthly & Seasonal Cost Breakdown (`gemini-2.5-flash`)

| Workload Component | Peak Transfer Window (Aug–Sep / Dec) | Off-Peak Months (10 Months/Yr) | Governance Mechanism |
| :--- | :---: | :---: | :--- |
| **1,050-Student Batch Matching Engine (`src/matcher.py`)** | **$0.00** (Pure Python) | **$0.00** | Rule 02 ban on row-by-row LLM loops (`>25` rows) |
| **HR & SM Interactive Queries (~600 turns/window)** | **~$0.54 / month** (`~1.2M` input + `~0.3M` output tokens) | **~$0.05 / month** | `max_output_tokens <= 1024` + Cloud Run `--max-instances=2` |
| **Cloud Run Compute (`europe-west2`, scale-to-zero)** | **~$1.80 / month** (within free tier / active requests only) | **$0.00 / month** (`min-instances=0`) | `--min-instances=0 --max-instances=2 --cpu-throttling` |
| **Regional Model Armor (`europe-west2`)** | **~$1.20 / month** | **$0.00 / month** | Regional template `next-student-transfer-armor` |
| **Total Estimated Monthly Run-Rate** | **~$3.54 / month** | **~$0.05 / month** | Fits **4.2x inside a single `$15/seat/month`** Gemini Enterprise pooled developer allocation |
