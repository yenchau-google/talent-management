-- =============================================================================
-- 03_matching_and_simulation_procs.sql
-- NEXT PLC Student Transfer Lighthouse — BigQuery SQL Matching & What-If Procs
-- Dataset: ${GOOGLE_CLOUD_PROJECT}.next_student_transfer_gold (europe-west2)
-- =============================================================================

-- View: Eligible Shifts strictly enforcing Green Box (is_green_box_protected = FALSE),
-- 3-Day CMP clearance (cmp_3day_cleared = TRUE), and >= 4.0h contract bounds.
CREATE OR REPLACE VIEW `${GOOGLE_CLOUD_PROJECT}.next_student_transfer_gold.v_eligible_shifts_sql` AS
SELECT
  shift_id,
  store_id,
  store_name,
  region,
  manager_token,
  day_of_week,
  start_time,
  end_time,
  weekly_hours,
  available_from,
  is_green_box_protected,
  cmp_3day_cleared,
  -- UK Young Worker (UNDER_18) statutory shift eligibility flag (06:00 <= start, end <= 22:00, <= 8.0h/day)
  (start_time >= '06:00' AND end_time <= '22:00' AND weekly_hours <= 8.0) AS is_under18_safe
FROM `${GOOGLE_CLOUD_PROJECT}.next_student_transfer_gold.store_shifts_gold`
WHERE is_green_box_protected = FALSE
  AND cmp_3day_cleared = TRUE
  AND weekly_hours >= 4.0
  AND weekly_hours <= 12.0;

-- Stored Procedure: Parameterized BigQuery SQL What-If Simulator & Store Capacity Summary
CREATE OR REPLACE PROCEDURE `${GOOGLE_CLOUD_PROJECT}.next_student_transfer_gold.sp_simulate_store_capacity`(
  IN min_hours_for_4star FLOAT64,
  IN city_filter_1 STRING,
  IN city_filter_2 STRING
)
BEGIN
  SELECT
    sh.store_id,
    ANY_VALUE(sh.store_name) AS store_name,
    ANY_VALUE(sh.manager_token) AS manager_token,
    COUNT(DISTINCT sh.shift_id) AS open_cleared_shifts,
    ROUND(SUM(sh.weekly_hours), 1) AS total_open_hours,
    COUNTIF(sh.weekly_hours >= 5.5) AS standard_5_5h_plus_shifts,
    COUNTIF(sh.weekly_hours >= min_hours_for_4star AND sh.weekly_hours < 5.5) AS unlocked_small_contract_shifts,
    (
      SELECT COUNT(1)
      FROM `${GOOGLE_CLOUD_PROJECT}.next_student_transfer_gold.students_gold` st
      WHERE st.contract_type = 'PERM'
        AND st.star_rating IN (3, 4)
        AND st.choice_1_store_id = sh.store_id
    ) AS choice_1_eligible_demand
  FROM `${GOOGLE_CLOUD_PROJECT}.next_student_transfer_gold.v_eligible_shifts_sql` sh
  WHERE LOWER(sh.store_name) LIKE CONCAT('%', LOWER(city_filter_1), '%')
     OR LOWER(sh.store_name) LIKE CONCAT('%', LOWER(city_filter_2), '%')
  GROUP BY sh.store_id
  ORDER BY choice_1_eligible_demand DESC, sh.store_id ASC;
END;
