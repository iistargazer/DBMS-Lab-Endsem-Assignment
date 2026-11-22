-- Deliverable 4: the 14-day churn model.
--
-- Churn is defined mechanically, not tuned to a result:
--
--     a player active at least once in the 7 days ending on a snapshot date has
--     churned if they are active zero times in the 14 days after that date.
--
-- v_churn_panel (sql/00_views.sql) builds the panel: one row per player per
-- weekly snapshot, 2026-02-16 .. 2026-04-13, with every feature measured from
-- data available on or before the snapshot and the label measured strictly after
-- it. The grid stops at 2026-04-13 so that no label window runs past the
-- 2026-04-30 end of the extract; `panel_audit` shows that nothing is scored on a
-- partly observed label.
--
-- These blocks are the descriptive half -- who churns, and how stable the label
-- is across snapshots. The model itself (logistic regression, feature
-- coefficients, ROC/AUC, calibration, threshold and cost curves) is fitted by
-- code/04_churn_model.py on exactly this view, so the SQL and the model cannot
-- drift apart.

-- name: panel_audit
SELECT count(*) AS panel_rows,
       count(DISTINCT device_id) AS players,
       count(DISTINCT snap_d) AS snapshots,
       min(snap_d) AS first_snapshot,
       max(snap_d) AS last_snapshot,
       round(100.0 * avg(churned::DOUBLE), 2) AS churn_rate_pct,
       round(avg(active_days_7), 2) AS mean_active_days_seven,
       count(*) FILTER (WHERE snap_d + 14 > DATE '2026-04-30') AS rows_with_partial_label,
       count(*) FILTER (WHERE active_days_next_14 = 0) AS churned_rows
FROM v_churn_panel;

-- name: censoring_audit
-- What the censoring rules remove, stated as counts rather than prose.
SELECT 'installs before 2026-01-01: tenure unobservable, excluded' AS exclusion,
       count(*) AS units
FROM player_profile
WHERE install_date < DATE '2026-01-01'
UNION ALL
SELECT 'weekly snapshots past the 14-day label horizon (2026-04-20, 2026-04-27)',
       count(*)
FROM generate_series(DATE '2026-04-20', DATE '2026-04-30', INTERVAL 7 DAY) AS t(g)
UNION ALL
SELECT 'installs in the panel base (2026-01-01 or later)',
       count(*)
FROM player_profile
WHERE install_date >= DATE '2026-01-01';

-- name: churn_rate_by_snapshot
-- If the label drifts across snapshots, a model trained on early ones is not
-- describing the same population as one scored on late ones. This is the check.
SELECT snap_d,
       snap_d + 14 AS label_window_end,
       count(*) AS players,
       count(*) FILTER (WHERE churned) AS churners,
       round(100.0 * avg(churned::DOUBLE), 2) AS churn_rate_pct,
       round(avg(tenure_days), 1) AS mean_tenure_days,
       round(avg(active_days_7), 2) AS mean_active_days_seven
FROM v_churn_panel
GROUP BY 1, 2
ORDER BY 1;

-- name: eligibility_audit
-- The panel keeps only players active in the prior 7 days. That is the right
-- population for a churn model (a player who is already gone cannot be
-- retained), but it is a selection, and this is its size.
WITH snaps AS (
    SELECT CAST(g AS DATE) AS snap_d
    FROM generate_series(DATE '2026-02-16', DATE '2026-04-13', INTERVAL 7 DAY) AS t(g)
),
installed AS (
    SELECT s.snap_d, count(*) AS installed_by_snapshot
    FROM snaps s
    JOIN player_profile p
      ON p.install_date >= DATE '2026-01-01' AND p.install_date <= s.snap_d
    GROUP BY 1
),
eligible AS (
    SELECT snap_d, count(*) AS eligible_players
    FROM v_churn_panel
    GROUP BY 1
)
SELECT i.snap_d,
       i.installed_by_snapshot,
       e.eligible_players,
       round(100.0 * e.eligible_players / i.installed_by_snapshot, 1) AS pct_active_in_prior_7d,
       round(100.0 * e.eligible_players / max(i.installed_by_snapshot) OVER (), 1) AS pct_of_latest_base
FROM installed i
LEFT JOIN eligible e USING (snap_d)
ORDER BY 1;

-- name: churn_base_rates_by_segment
-- Univariate churn with Wilson intervals, plus lift against the pooled base
-- rate. This is also the evidence for the two confounds the model has to handle:
-- channel and country tier each move churn on their own, and they are correlated
-- with each other.
WITH u AS (
    SELECT 1 AS dim_order, 'acquisition_channel' AS dimension,
           acquisition_channel AS level, count(*) AS n, avg(churned::DOUBLE) AS p
    FROM v_churn_panel GROUP BY 2, 3
    UNION ALL
    SELECT 2, 'platform', platform, count(*), avg(churned::DOUBLE)
    FROM v_churn_panel GROUP BY 2, 3
    UNION ALL
    SELECT 3, 'country_tier', 'tier ' || CAST(country_tier AS VARCHAR), count(*), avg(churned::DOUBLE)
    FROM v_churn_panel GROUP BY 2, country_tier
    UNION ALL
    SELECT 4, 'tenure_days',
           CASE WHEN tenure_days <= 7 THEN '0-7'
                WHEN tenure_days <= 14 THEN '8-14'
                WHEN tenure_days <= 30 THEN '15-30'
                WHEN tenure_days <= 60 THEN '31-60'
                ELSE '61+' END,
           count(*), avg(churned::DOUBLE)
    FROM v_churn_panel GROUP BY 2, 3
    UNION ALL
    SELECT 5, 'active_days_7', CAST(active_days_7 AS VARCHAR) || ' of 7', count(*), avg(churned::DOUBLE)
    FROM v_churn_panel GROUP BY 2, 3
    UNION ALL
    SELECT 6, 'has_ever_paid', CASE WHEN spend_to_date > 0 THEN 'yes' ELSE 'no' END,
           count(*), avg(churned::DOUBLE)
    FROM v_churn_panel GROUP BY 2, 3
    UNION ALL
    SELECT 7, 'ads_completed_7',
           CASE WHEN ads_completed_7 = 0 THEN '0'
                WHEN ads_completed_7 <= 7 THEN '1-7'
                ELSE '8+' END,
           count(*), avg(churned::DOUBLE)
    FROM v_churn_panel GROUP BY 2, 3
),
w AS (
    SELECT *,
           (p + 1.9208 / n - 1.96 * sqrt(p * (1 - p) / n + 0.9604 / (n * n))) / (1 + 3.8416 / n) AS lo,
           (p + 1.9208 / n + 1.96 * sqrt(p * (1 - p) / n + 0.9604 / (n * n))) / (1 + 3.8416 / n) AS hi
    FROM u
),
overall AS (
    SELECT avg(churned::DOUBLE) AS base FROM v_churn_panel
)
SELECT w.dimension,
       w.level,
       w.n,
       round(100.0 * w.p, 1) AS churn_rate_pct,
       round(100.0 * w.lo, 1) AS wilson_lo,
       round(100.0 * w.hi, 1) AS wilson_hi,
       round(100.0 * (w.p / overall.base - 1), 1) AS lift_vs_base_pct
FROM w
CROSS JOIN overall
ORDER BY w.dim_order, w.p DESC;

-- name: churn_by_segment_cross
-- Channel x tier, the interaction the model needs: does the channel gap survive
-- inside a tier? Base rates only -- the model does the controlled version.
SELECT acquisition_channel AS channel,
       country_tier,
       count(*) AS players,
       round(100.0 * avg(churned::DOUBLE), 1) AS churn_rate_pct,
       round(avg(active_days_7), 2) AS mean_active_days_seven,
       round(100.0 * avg((spend_to_date > 0)::DOUBLE), 2) AS pct_ever_paid
FROM v_churn_panel
GROUP BY 1, 2
ORDER BY 1, 2;
