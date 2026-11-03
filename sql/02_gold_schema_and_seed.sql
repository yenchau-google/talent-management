-- =============================================================================
-- 02_gold_schema_and_seed.sql
-- NEXT PLC Student Transfer Lighthouse — Trusted Gold Dataset & Seed
-- Dataset: talent-management-513520.next_student_transfer_gold (europe-west2)
-- =============================================================================

-- 1. Reset source dataset to clean baseline first before copying into _gold
UPDATE `talent-management-513520.next_student_transfer.students_gold`
SET choice_2_store_id = 'STORE-043', updated_by_token = 'SYSTEM-SEED'
WHERE student_id = 'STU-0412';

UPDATE `talent-management-513520.next_student_transfer.students_gold`
SET preferred_latest_finish = '21:30', updated_by_token = 'SYSTEM-SEED'
WHERE student_id = 'STU-0109';

UPDATE `talent-management-513520.next_student_transfer.store_shifts_gold`
SET weekly_hours = 4.0, updated_by_token = 'SYSTEM-SEED'
WHERE shift_id = 'SHF-1028';

UPDATE `talent-management-513520.next_student_transfer.store_shifts_gold`
SET is_green_box_protected = TRUE, updated_by_token = 'SYSTEM-SEED'
WHERE shift_id = 'SHF-1003';

UPDATE `talent-management-513520.next_student_transfer.store_shifts_gold`
SET cmp_3day_cleared = FALSE, updated_by_token = 'SYSTEM-SEED'
WHERE shift_id IN ('SHF-9001', 'SHF-9002', 'SHF-9003');

-- 2. Seed immutable baseline snapshots + live Gold tables (1,050 students & 1,093 shifts)
CREATE OR REPLACE TABLE `talent-management-513520.next_student_transfer_gold.students_gold_baseline` AS
SELECT * FROM `talent-management-513520.next_student_transfer.students_gold`;

CREATE OR REPLACE TABLE `talent-management-513520.next_student_transfer_gold.store_shifts_gold_baseline` AS
SELECT * FROM `talent-management-513520.next_student_transfer.store_shifts_gold`;

CREATE OR REPLACE TABLE `talent-management-513520.next_student_transfer_gold.students_gold` AS
SELECT * FROM `talent-management-513520.next_student_transfer_gold.students_gold_baseline`;

CREATE OR REPLACE TABLE `talent-management-513520.next_student_transfer_gold.store_shifts_gold` AS
SELECT * FROM `talent-management-513520.next_student_transfer_gold.store_shifts_gold_baseline`;

-- 3. Gold Governance & Outbox Tables
CREATE TABLE IF NOT EXISTS `talent-management-513520.next_student_transfer_gold.placements_staging` (
  batch_id STRING NOT NULL,
  staged_at STRING NOT NULL,
  nep_staging_mode STRING NOT NULL,
  student_id STRING NOT NULL,
  age_band STRING NOT NULL,
  star_rating INT64 NOT NULL,
  home_store_id STRING NOT NULL,
  destination_store_id STRING NOT NULL,
  destination_store_name STRING NOT NULL,
  manager_token STRING NOT NULL,
  pass_matched INT64 NOT NULL,
  assigned_shift_ids STRING NOT NULL,
  assigned_weekly_hours FLOAT64 NOT NULL,
  effective_transfer_date STRING NOT NULL,
  home_store_release_status STRING NOT NULL,
  hitl_stage STRING NOT NULL
);

CREATE TABLE IF NOT EXISTS `talent-management-513520.next_student_transfer_gold.transfer_audit_log` (
  audit_id STRING NOT NULL,
  timestamp STRING NOT NULL,
  student_id STRING NOT NULL,
  reviewer_role STRING NOT NULL,
  decision STRING NOT NULL,
  reason_code STRING NOT NULL,
  note STRING,
  stage STRING NOT NULL,
  downstream_action STRING NOT NULL
);

CREATE TABLE IF NOT EXISTS `talent-management-513520.next_student_transfer_gold.outreach_email_log` (
  email_id STRING NOT NULL,
  sent_at STRING NOT NULL,
  recipient_persona STRING NOT NULL,
  recipient_token STRING NOT NULL,
  recipient_email STRING NOT NULL,
  subject STRING NOT NULL,
  body_text STRING NOT NULL,
  related_dq_rule STRING NOT NULL,
  related_entity_id STRING NOT NULL,
  delivery_status STRING NOT NULL,     -- STAGED_FOR_GE_GMAIL
  gmail_message_id STRING
);
