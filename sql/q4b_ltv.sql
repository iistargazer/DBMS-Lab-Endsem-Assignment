-- Deliverable 4b (optional): the 90-day value of a new install.
--
-- The brief asks for 90-day revenue predicted from what is known at the end of
-- day 7, and warns that the hard part is not the modelling. It is not: it is that
-- a player needs 90 *observed* days to have a label, and activity stops on
-- 2026-04-30. So the labelable cohort is only the first ~12% of installs, and the
-- other 88% cannot be labelled at any model quality. That is a census problem,
-- not a modelling problem, and it has a consequence: the labelled cohort is
-- January, when paid_video was a small share of installs, while the installs UA
-- is buying today are overwhelmingly paid_video. `02_cohort_vs_population`
-- quantifies that gap rather than hiding it.
--
-- v_ltv_cohort (sql/00_views.sql) holds the cohort: features from D0-D6, label
-- from D0-D89. The model, its ranking metrics, the baselines it has to beat and
-- the transfer check on a later cohort are in code/05_ltv_model.py.

-- name: 01_labelling_window
-- The censoring rule, as arithmetic: a 90-day label needs install + 89 <= 2026-04-30.
-- Two things shrink the usable cohort, and only the first is obvious:
--   * installs after that date have no complete window (54,486 of them);
--   * installs before 2026-01-01 are old enough, but their purchases before
--     2026-01-01 were never extracted, so their 90-day revenue is undercounted.
-- What is left is 9,809 installs -- 12.2% of the player base.
SELECT DATE '2026-04-30' AS data_end,
       DATE '2026-04-30' - 89 AS last_labelable_install,
       count(*) AS all_profiles,
       count(*) FILTER (WHERE install_date <= DATE '2026-04-30' - 89) AS installs_90d_complete,
       count(*) FILTER (WHERE install_date < DATE '2026-01-01') AS left_truncated_excluded,
       count(*) FILTER (WHERE install_date BETWEEN DATE '2026-01-01' AND DATE '2026-04-30' - 89) AS usable_labelled_cohort,
       count(*) FILTER (WHERE install_date > DATE '2026-04-30' - 89) AS incomplete_window_excluded,
       round(100.0 * count(*) FILTER (WHERE install_date BETWEEN DATE '2026-01-01' AND DATE '2026-04-30' - 89)
             / count(*), 1) AS usable_pct_of_profiles
FROM player_profile;

-- name: 02_cohort_vs_population
-- Is the labelled cohort representative of the installs UA is buying now? No, and
-- this is the number that stops the model from being read as a bid rule.
WITH lab AS (
    SELECT acquisition_channel, country_tier, platform FROM v_ltv_cohort
),
pop AS (
    SELECT acquisition_channel, country_tier, platform
    FROM player_profile
    WHERE install_date BETWEEN DATE '2026-01-01' AND DATE '2026-04-30'
),
recent AS (
    SELECT acquisition_channel, country_tier, platform
    FROM player_profile
    WHERE install_date >= DATE '2026-04-01'
)
SELECT 'labelled cohort (D0-D89 complete)' AS population, count(*) AS installs,
       round(100.0 * count(*) FILTER (WHERE acquisition_channel = 'paid_video') / count(*), 1) AS paid_video_share_pct,
       round(100.0 * count(*) FILTER (WHERE country_tier = 4) / count(*), 1) AS tier4_share_pct,
       round(100.0 * count(*) FILTER (WHERE platform = 'iOS') / count(*), 1) AS ios_share_pct
FROM lab
UNION ALL
SELECT 'all 2026 installs', count(*),
       round(100.0 * count(*) FILTER (WHERE acquisition_channel = 'paid_video') / count(*), 1),
       round(100.0 * count(*) FILTER (WHERE country_tier = 4) / count(*), 1),
       round(100.0 * count(*) FILTER (WHERE platform = 'iOS') / count(*), 1)
FROM pop
UNION ALL
SELECT 'April 2026 installs (what UA buys now)', count(*),
       round(100.0 * count(*) FILTER (WHERE acquisition_channel = 'paid_video') / count(*), 1),
       round(100.0 * count(*) FILTER (WHERE country_tier = 4) / count(*), 1),
       round(100.0 * count(*) FILTER (WHERE platform = 'iOS') / count(*), 1)
FROM recent;

-- name: 03_revenue_window_split
-- How much of the 90-day label is already booked by day 7. It is not a leak -- at
-- day 7 UA can see it -- but it changes what "prediction" means, so it is stated.
SELECT count(*) AS installs,
       count(*) FILTER (WHERE revenue_90 > 0) AS payers_90,
       round(100.0 * count(*) FILTER (WHERE revenue_90 > 0) / count(*), 2) AS payer_pct,
       round(avg(revenue_90), 3) AS mean_revenue_90,
       round(median(revenue_90), 2) AS median_revenue_90,
       round(sum(revenue_90), 2) AS total_revenue_90,
       round(sum(revenue_7), 2) AS revenue_in_first_7d,
       round(100.0 * sum(revenue_7) / nullif(sum(revenue_90), 0), 1) AS pct_of_90d_booked_by_day7,
       round(avg(active_days_7), 2) AS mean_active_days_7,
       round(avg(active_days_90), 2) AS mean_active_days_90
FROM v_ltv_cohort;

-- name: 04_revenue_90_deciles
-- The shape that makes accuracy the wrong metric: most installs are worth
-- nothing, and the top tenth carries most of the money. Ranking is what matters.
WITH d AS (
    SELECT revenue_90, ntile(10) OVER (ORDER BY revenue_90) AS decile
    FROM v_ltv_cohort
)
SELECT decile,
       count(*) AS installs,
       round(avg(revenue_90), 2) AS mean_revenue_90,
       round(min(revenue_90), 2) AS min_revenue_90,
       round(max(revenue_90), 2) AS max_revenue_90,
       round(sum(revenue_90), 2) AS revenue_90,
       round(100.0 * sum(revenue_90) / sum(sum(revenue_90)) OVER (), 1) AS share_of_revenue_pct
FROM d
GROUP BY 1
ORDER BY 1;

-- name: 05_ltv_by_channel
-- Descriptive 90-day value by acquisition channel, in the labelled cohort only.
SELECT acquisition_channel,
       count(*) AS installs,
       round(100.0 * count(*) FILTER (WHERE revenue_90 > 0) / count(*), 2) AS payer_pct,
       round(avg(revenue_90), 2) AS mean_revenue_90,
       round(median(revenue_90), 2) AS median_revenue_90,
       round(sum(revenue_90) / nullif(count(*) FILTER (WHERE revenue_90 > 0), 0), 2) AS revenue_per_payer,
       round(100.0 * sum(revenue_90) / sum(sum(revenue_90)) OVER (), 1) AS share_of_revenue_pct
FROM v_ltv_cohort
GROUP BY 1
ORDER BY mean_revenue_90 DESC;

-- name: 06_ltv_by_tier
SELECT country_tier,
       count(*) AS installs,
       round(100.0 * count(*) FILTER (WHERE revenue_90 > 0) / count(*), 2) AS payer_pct,
       round(avg(revenue_90), 2) AS mean_revenue_90,
       round(median(revenue_90), 2) AS median_revenue_90
FROM v_ltv_cohort
GROUP BY 1
ORDER BY 1;
