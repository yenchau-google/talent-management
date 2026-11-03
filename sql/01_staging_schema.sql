-- =============================================================================
-- 01_staging_schema.sql
-- NEXT PLC Student Transfer Lighthouse — Bronze Intake & DQ Quarantine Dataset
-- Dataset: talent-management-513520.next_student_transfer_staging (europe-west2)
-- =============================================================================

CREATE TABLE IF NOT EXISTS `talent-management-513520.next_student_transfer_staging.portal_change_requests` (
  request_id STRING NOT NULL,
  submitted_at STRING NOT NULL,
  actor_role STRING NOT NULL,          -- STUDENT | STORE_MANAGER
  actor_id STRING NOT NULL,            -- STU-XXXX | SM-XXX
  target_table STRING NOT NULL,        -- students_gold | store_shifts_gold
  record_id STRING NOT NULL,           -- STU-XXXX | SHF-XXXX
  age_band STRING,                     -- UNDER_18 | ADULT_18_PLUS
  choice_1 STRING,
  choice_2 STRING,
  choice_3 STRING,
  transfer_gap_days INT64,
  shift_hours FLOAT64,
  preferred_latest_finish STRING,
  was_green_box_protected BOOL,
  new_is_green_box_protected BOOL,
  cmp_3day_cleared BOOL,
  note STRING,
  promotion_status STRING NOT NULL,    -- PROMOTED | QUARANTINED | REJECTED_BY_HR | OVERRIDDEN_BY_HR | SUPERSEDED_BY_RESET
  dq_rules_triggered STRING,           -- Comma-separated DQ rule IDs or NONE
  resolved_at STRING,
  resolved_by STRING,
  hr_reason STRING
);

CREATE TABLE IF NOT EXISTS `talent-management-513520.next_student_transfer_staging.change_audit_log` (
  change_id STRING NOT NULL,
  request_id STRING,
  changed_at STRING NOT NULL,
  actor_persona STRING NOT NULL,       -- STUDENT | STORE_MANAGER | HR_COLLEAGUE | SYSTEM_DEMO_HARNESS
  actor_token STRING NOT NULL,         -- STU-XXXX | SM-XXX | HR-SPECIALIST-GE | DEMO_RESET
  entity_type STRING NOT NULL,         -- STUDENT | STORE_SHIFT | DEMO_STATE
  entity_id STRING NOT NULL,
  field_changed STRING NOT NULL,
  old_value STRING,
  new_value STRING,
  validation_status STRING NOT NULL,   -- VALID | QUARANTINED | REJECTED_BY_HR | OVERRIDDEN_BY_HR | DEMO_RESET
  rule_triggered STRING,
  reverted BOOL NOT NULL,
  reverted_by STRING,
  revert_reason STRING
);

-- Real-time 0ms SQL View evaluating DQ-01..DQ-06 directly on active portal_change_requests
CREATE OR REPLACE VIEW `talent-management-513520.next_student_transfer_staging.v_data_quality_warnings` AS
WITH active_requests AS (
  SELECT *
  FROM `talent-management-513520.next_student_transfer_staging.portal_change_requests`
  WHERE promotion_status = 'QUARANTINED'
)
-- DQ-01: Duplicate Store Choice on Student Preference Request
SELECT
  request_id,
  'DQ-01' AS rule_id,
  'DQ-01' AS dq_rule_id,
  'WARNING' AS severity,
  'STUDENT' AS entity_type,
  record_id AS entity_id,
  actor_id AS owner_token,
  actor_id AS actor_token,
  CONCAT(
    'Student ', record_id, ' submitted duplicate store choices (Choice 1=', IFNULL(choice_1, ''),
    ', Choice 2=', IFNULL(choice_2, ''), ', Choice 3=', IFNULL(choice_3, ''), ').'
  ) AS issue_summary,
  CONCAT(
    'Duplicate store selection across preference slots (', IFNULL(choice_1, ''), ' / ', IFNULL(choice_2, ''), ').'
  ) AS dq_reason,
  CONCAT(
    'Contact ', actor_id, ' via @Gmail to select distinct 1st, 2nd, and 3rd preference stores before batch matching.'
  ) AS recommended_comms_action,
  submitted_at AS updated_at,
  promotion_status
FROM active_requests
WHERE target_table = 'students_gold'
  AND (
    (choice_1 IS NOT NULL AND choice_2 IS NOT NULL AND choice_1 = choice_2)
    OR (choice_1 IS NOT NULL AND choice_3 IS NOT NULL AND choice_1 = choice_3)
    OR (choice_2 IS NOT NULL AND choice_3 IS NOT NULL AND choice_2 != '' AND choice_2 = choice_3)
  )

UNION ALL

-- DQ-02: Transfer Continuity Gap Exceeds 21 Calendar Days
SELECT
  request_id,
  'DQ-02' AS rule_id,
  'DQ-02' AS dq_rule_id,
  'WARNING' AS severity,
  'STUDENT' AS entity_type,
  record_id AS entity_id,
  actor_id AS owner_token,
  actor_id AS actor_token,
  CONCAT(
    'Student ', record_id, ' requested a ', CAST(transfer_gap_days AS STRING),
    '-day gap between home store finish and university store start (> 21-day continuity threshold).'
  ) AS issue_summary,
  CONCAT(
    'Transfer gap of ', CAST(transfer_gap_days AS STRING), ' days exceeds 21-day continuous service rule.'
  ) AS dq_reason,
  CONCAT(
    'Contact ', actor_id, ' and Home Store Manager via @Gmail to align release/start dates within 21 days.'
  ) AS recommended_comms_action,
  submitted_at AS updated_at,
  promotion_status
FROM active_requests
WHERE target_table = 'students_gold'
  AND transfer_gap_days IS NOT NULL
  AND (transfer_gap_days > 21 OR transfer_gap_days < 0)

UNION ALL

-- DQ-03: Store Shift Hours Outside Statutory/Contract Bounds (< 4.0h or > 12.0h)
SELECT
  request_id,
  'DQ-03' AS rule_id,
  'DQ-03' AS dq_rule_id,
  'BLOCKING' AS severity,
  'STORE_SHIFT' AS entity_type,
  record_id AS entity_id,
  actor_id AS owner_token,
  actor_id AS actor_token,
  CONCAT(
    'Store Manager ', actor_id, ' set shift ', record_id, ' weekly_hours to ',
    CAST(shift_hours AS STRING), 'h (violates 4.0h minimum / 12.0h maximum contract bounds).'
  ) AS issue_summary,
  CONCAT(
    'Shift ', record_id, ' set to ', CAST(shift_hours AS STRING), 'h (< 4.0h minimum contract floor).'
  ) AS dq_reason,
  CONCAT(
    'Contact Store Manager ', actor_id, ' via @Gmail to restore shift ', record_id, ' to >= 4.0h (or extend to >= 5.5h).'
  ) AS recommended_comms_action,
  submitted_at AS updated_at,
  promotion_status
FROM active_requests
WHERE target_table = 'store_shifts_gold'
  AND shift_hours IS NOT NULL
  AND (shift_hours < 4.0 OR shift_hours > 12.0)

UNION ALL

-- DQ-04: Sub-5.5h Single Shift (4.0h <= shift_hours < 5.5h) Flagged for SM Extension Review
SELECT
  request_id,
  'DQ-04' AS rule_id,
  'DQ-04' AS dq_rule_id,
  'WARNING' AS severity,
  'STORE_SHIFT' AS entity_type,
  record_id AS entity_id,
  actor_id AS owner_token,
  actor_id AS actor_token,
  CONCAT(
    'Shift ', record_id, ' updated by ', actor_id, ' has ', CAST(shift_hours AS STRING),
    'h (< 5.5h standard contract completion floor unless paired or 4-star small-contract opted in).'
  ) AS issue_summary,
  CONCAT(
    'Shift ', record_id, ' is ', CAST(shift_hours AS STRING), 'h (< 5.5h standard contract completion floor).'
  ) AS dq_reason,
  CONCAT(
    'Ask Store Manager ', actor_id, ' via @Gmail if shift ', record_id, ' can be extended by +1.5h to 5.5h.'
  ) AS recommended_comms_action,
  submitted_at AS updated_at,
  promotion_status
FROM active_requests
WHERE target_table = 'store_shifts_gold'
  AND shift_hours IS NOT NULL
  AND shift_hours >= 4.0 AND shift_hours < 5.5
  AND NOT (IFNULL(was_green_box_protected, FALSE) = TRUE AND IFNULL(new_is_green_box_protected, TRUE) = FALSE)

UNION ALL

-- DQ-05: Unapproved Unlock of Green Box Protected Shift
SELECT
  request_id,
  'DQ-05' AS rule_id,
  'DQ-05' AS dq_rule_id,
  'BLOCKING' AS severity,
  'STORE_SHIFT' AS entity_type,
  record_id AS entity_id,
  actor_id AS owner_token,
  actor_id AS actor_token,
  CONCAT(
    'Store Manager ', actor_id, ' attempted to unlock Green Box protected vacancy ', record_id,
    ' (was_green_box_protected=TRUE -> new_is_green_box_protected=FALSE) without Central HR sign-off.'
  ) AS issue_summary,
  CONCAT(
    'Unapproved Green Box protection removal on shift ', record_id, ' by ', actor_id, '.'
  ) AS dq_reason,
  CONCAT(
    'Contact Store Manager ', actor_id, ' via @Gmail to confirm whether returning student forfeited ',
    record_id, ' before HR overrides or rejects this change.'
  ) AS recommended_comms_action,
  submitted_at AS updated_at,
  promotion_status
FROM active_requests
WHERE target_table = 'store_shifts_gold'
  AND IFNULL(was_green_box_protected, FALSE) = TRUE
  AND IFNULL(new_is_green_box_protected, TRUE) = FALSE

UNION ALL

-- DQ-06: UK Young Worker (UNDER_18) Statutory Night Curfew Violation (> 22:00)
SELECT
  request_id,
  'DQ-06' AS rule_id,
  'DQ-06' AS dq_rule_id,
  'BLOCKING' AS severity,
  'STUDENT' AS entity_type,
  record_id AS entity_id,
  actor_id AS owner_token,
  actor_id AS actor_token,
  CONCAT(
    'UK Young Worker statutory violation (gov.uk/rest-breaks-work/young-workers): ',
    record_id, ' (age_band=UNDER_18) requested preferred_latest_finish=', IFNULL(preferred_latest_finish, ''),
    ' (shifts ending after 22:00 or starting before 06:00 are strictly prohibited for Under-18s).'
  ) AS issue_summary,
  CONCAT(
    'Under-18 colleague ', record_id, ' requested late-night finish ', IFNULL(preferred_latest_finish, ''), ' (> 22:00 curfew).'
  ) AS dq_reason,
  CONCAT(
    'Notify ', actor_id, ' via @Gmail that UK Young Worker regulations cap shifts at 22:00 and reject the late-finish update.'
  ) AS recommended_comms_action,
  submitted_at AS updated_at,
  promotion_status
FROM active_requests
WHERE target_table = 'students_gold'
  AND age_band = 'UNDER_18'
  AND preferred_latest_finish > '22:00';
