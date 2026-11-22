-- Deliverable 1(b): retention by install-week cohort.
--
-- Two boundary rules, both deliberate:
--
--  1. LEFT EDGE. Player activity is only recorded from 2026-01-01, so a player
--     who installed in 2025 has no observable day 1, 7, 14 or 30 -- those days
--     happened before the extract begins. Cohorts before 2026-01-01 are
--     therefore excluded from the grid (their count is reported separately by
--     `cohorts_excluded_pre_window`) rather than shown as 0% retained.
--
--  2. RIGHT EDGE. A cell is shown only if every player in the cohort has had a
--     full N days of observation by 2026-04-30. A cohort whose latest install
--     is 2026-04-20 cannot yet have a day-30 answer, so the cell is NULL -- a
--     blank, never a 0%. Filling it with 0% would read as catastrophic churn
--     when it is simply unobserved.
--
-- Retention on day N means active on exactly install_date + N days.

-- name: retention_grid
WITH installs AS (
    SELECT device_id, install_date, CAST(date_trunc('week', install_date) AS DATE) AS cohort_week
    FROM player_profile
    WHERE install_date >= DATE '2026-01-01'
),
per_player AS (
    SELECT i.cohort_week, i.device_id, i.install_date,
           max(CASE WHEN date_diff('day', i.install_date, v.d) = 1 THEN 1 ELSE 0 END) AS d1,
           max(CASE WHEN date_diff('day', i.install_date, v.d) = 7 THEN 1 ELSE 0 END) AS d7,
           max(CASE WHEN date_diff('day', i.install_date, v.d) = 14 THEN 1 ELSE 0 END) AS d14,
           max(CASE WHEN date_diff('day', i.install_date, v.d) = 30 THEN 1 ELSE 0 END) AS d30
    FROM installs i
    LEFT JOIN v_pd v ON v.device_id = i.device_id
    GROUP BY 1, 2, 3
),
agg AS (
    SELECT cohort_week,
           count(*) AS cohort_size,
           max(install_date) AS last_install,
           avg(d1) AS d1, avg(d7) AS d7, avg(d14) AS d14, avg(d30) AS d30
    FROM per_player
    GROUP BY 1
)
SELECT cohort_week,
       cohort_size,
       date_diff('day', last_install, DATE '2026-04-30') AS days_observable_for_cohort,
       CASE WHEN date_diff('day', last_install, DATE '2026-04-30') >= 1
            THEN round(100.0 * d1, 1) END AS d1_pct,
       CASE WHEN date_diff('day', last_install, DATE '2026-04-30') >= 7
            THEN round(100.0 * d7, 1) END AS d7_pct,
       CASE WHEN date_diff('day', last_install, DATE '2026-04-30') >= 14
            THEN round(100.0 * d14, 1) END AS d14_pct,
       CASE WHEN date_diff('day', last_install, DATE '2026-04-30') >= 30
            THEN round(100.0 * d30, 1) END AS d30_pct
FROM agg
ORDER BY cohort_week;

-- name: retention_curve_all_cohorts
-- Day-by-day survival pooled over in-window cohorts with enough observation,
-- for the shape the grid cannot show.
WITH installs AS (
    SELECT device_id, install_date
    FROM player_profile
    WHERE install_date >= DATE '2026-01-01' AND install_date <= DATE '2026-03-31'
),
cohort AS (
    SELECT count(*) AS cohort_size FROM installs
),
activity AS (
    SELECT i.device_id, date_diff('day', i.install_date, v.d) AS day_n
    FROM installs i
    JOIN v_pd v ON v.device_id = i.device_id
),
per_day AS (
    SELECT day_n, count(DISTINCT device_id) AS players_active
    FROM activity
    WHERE day_n BETWEEN 0 AND 30
    GROUP BY 1
)
SELECT days.day_n AS days_since_install,
       cohort.cohort_size,
       coalesce(per_day.players_active, 0) AS players_active,
       round(100.0 * coalesce(per_day.players_active, 0) / cohort.cohort_size, 1) AS pct_retained
FROM (SELECT unnest(generate_series(0, 30)) AS day_n) days
CROSS JOIN cohort
LEFT JOIN per_day USING (day_n)
ORDER BY 1;

-- name: cohort_boundary_notes
SELECT 'installs before 2026-01-01 (excluded: day-N unobservable)' AS note,
       count(*) AS players
FROM player_profile WHERE install_date < DATE '2026-01-01'
UNION ALL
SELECT 'installs in final 30 days (d30 cells left blank)', count(*)
FROM player_profile WHERE install_date > DATE '2026-04-30' - 30
UNION ALL
SELECT 'profiles with no player_day rows at all (counted as churned)', count(*)
FROM player_profile p
ANTI JOIN player_day d ON d.device_id = p.device_id;
