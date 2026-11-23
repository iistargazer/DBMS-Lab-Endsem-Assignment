-- Deliverable 3: is one acquisition channel buying better players than another?
--
-- The naive comparison is not usable on its own. Two things break it:
--
--  CENSORING. April installs have not had 30 days to churn, pay or come back.
--  Comparing channels over whatever follow-up each happens to have would reward
--  whichever channel skews late in the period. v_install_cohort fixes the window
--  to D0-D30 from each player's own install date and admits only installs that
--  can be observed for all 31 dates (2026-01-01..2026-03-31).
--
--  TIER MIX. Channels deliver different platform and country-tier mixes, and
--  tier is the strongest single correlate of value in this data ($0.24 vs $0.49
--  ARPDAU between Android and iOS alone; tier 1 vs tier 4 is wider still). A
--  channel that happens to buy low-tier traffic will look bad for a reason that
--  has nothing to do with the channel. So every metric is computed inside
--  platform x country_tier cells and then re-weighted to one common mix.
--
-- Direct standardisation is used: the weights are each stratum's share of total
-- installs in the mature cohort, so the standardised figure answers "what would
-- this channel look like if it had delivered the cohort's overall mix?".
-- Variance is the weighted sum of within-cell variances, which assumes the cells
-- are independent samples -- true here, since a player sits in exactly one cell.
-- The point estimates and these analytic intervals are reproduced by a stratified
-- bootstrap in code/03_channel_ci.py, which also puts an interval on the
-- channel-vs-channel difference (the quantity the deck actually needs).

-- name: observation_window_audit
-- How much data the censoring rule costs, and which installs it keeps.
SELECT 'installs before 2026-01-01: D0-D30 unobservable' AS exclusion,
       count(*) AS installs,
       round(100.0 * count(*) / (SELECT count(*) FROM player_profile), 2) AS pct_of_all_profiles
FROM player_profile
WHERE install_date < DATE '2026-01-01'
UNION ALL
SELECT 'installs after 2026-03-31: 30-day window incomplete',
       count(*),
       round(100.0 * count(*) / (SELECT count(*) FROM player_profile), 2)
FROM player_profile
WHERE install_date > DATE '2026-03-31'
UNION ALL
SELECT 'mature cohort used in the comparison',
       count(*),
       round(100.0 * count(*) / (SELECT count(*) FROM player_profile), 2)
FROM player_profile
WHERE install_date BETWEEN DATE '2026-01-01' AND DATE '2026-03-31';

-- name: channel_mix_mature_vs_dropped
-- Does dropping April change the channel mix? If it did, the restriction would
-- itself be a mix confound; the two share columns below show it does not move
-- much, and `mix_shift_pp` quantifies whatever shift there is.
WITH m AS (
    SELECT acquisition_channel,
           count(*) FILTER (WHERE install_date BETWEEN DATE '2026-01-01' AND DATE '2026-03-31') AS mature_installs,
           count(*) FILTER (WHERE install_date > DATE '2026-03-31') AS april_installs
    FROM player_profile
    GROUP BY 1
)
SELECT acquisition_channel,
       mature_installs,
       april_installs,
       round(100.0 * mature_installs / sum(mature_installs) OVER (), 1) AS mature_share_pct,
       round(100.0 * april_installs / sum(april_installs) OVER (), 1) AS april_share_pct,
       round(100.0 * mature_installs / sum(mature_installs) OVER ()
             - 100.0 * april_installs / sum(april_installs) OVER (), 1) AS mix_shift_pp
FROM m
ORDER BY mature_installs DESC;

-- name: channel_mix_drift_by_month
-- The confound that makes crude period comparisons meaningless: paid_video's
-- share of installs climbs every month, and so does the share of tier 4.
WITH m AS (
    SELECT CAST(date_trunc('month', install_date) AS DATE) AS install_month,
           acquisition_channel,
           count(*) AS installs,
           count(*) FILTER (WHERE country_tier = 4) AS tier4_installs
    FROM player_profile
    WHERE install_date >= DATE '2026-01-01'
    GROUP BY 1, 2
)
SELECT install_month,
       acquisition_channel,
       installs,
       round(100.0 * installs / sum(installs) OVER (PARTITION BY install_month), 1) AS channel_share_pct,
       round(100.0 * tier4_installs / sum(installs) OVER (PARTITION BY install_month), 1) AS tier4_share_of_month_pct
FROM m
ORDER BY 1, 2;

-- name: channel_crude_rates
-- At-a-glance per channel, with no adjustment. These are the numbers the
-- standardisation moves; read them against `crude_vs_standardized`.
SELECT acquisition_channel AS channel,
       count(*) AS installs,
       round(100.0 * avg(d1::DOUBLE), 1)  AS d1_retention_pct,
       round(100.0 * avg(d7::DOUBLE), 1)  AS d7_retention_pct,
       round(100.0 * avg(d14::DOUBLE), 1) AS d14_retention_pct,
       round(100.0 * avg(d30::DOUBLE), 1) AS d30_retention_pct,
       count(*) FILTER (WHERE payer_30) AS payers_30,
       round(100.0 * avg(payer_30::DOUBLE), 2) AS payer_conversion_30_pct,
       round(avg(revenue_net_30), 2) AS revenue_per_install_30,
       round(sum(revenue_net_30) / nullif(count(*) FILTER (WHERE payer_30), 0), 2) AS revenue_per_payer_30,
       round(avg(active_days_30::DOUBLE), 2) AS active_days_30,
       round(sum(ads_completed_30)::DOUBLE / nullif(sum(active_days_30), 0), 2) AS ads_per_active_day_30,
       round(avg(ads_completed_30::DOUBLE), 2) AS ads_per_install_30
FROM v_install_cohort
GROUP BY 1
ORDER BY installs DESC;

-- name: channel_rates_by_stratum
-- The cells the standardisation is built from. `cell_is_thin` flags a cell with
-- fewer than 250 installs or 50 payers: those contribute a weight but their own
-- rate is noisy, which is why the bootstrap in code/03_channel_ci.py resamples
-- within cells instead of trusting a normal approximation on them. The sensitivity
-- drop in `standardized_excluding_thin_cells` uses the install floor alone, since
-- installs are what carry the weight.
SELECT platform,
       country_tier,
       acquisition_channel AS channel,
       count(*) AS installs,
       count(*) FILTER (WHERE payer_30) AS payers_30,
       round(100.0 * avg(d30::DOUBLE), 1) AS d30_retention_pct,
       round(100.0 * avg(payer_30::DOUBLE), 2) AS payer_conversion_30_pct,
       round(avg(revenue_net_30), 2) AS revenue_per_install_30,
       round(avg(active_days_30::DOUBLE), 2) AS active_days_30,
       (count(*) < 250 OR count(*) FILTER (WHERE payer_30) < 50) AS cell_is_thin
FROM v_install_cohort
GROUP BY 1, 2, 3
ORDER BY platform, country_tier, channel;

-- name: stratum_dominance
-- The tier-mix-free version of the question: inside each of the 8 cells, which
-- channel comes first? "Best in all 8" is a much harder claim to explain away
-- than "best on average".
WITH ranked AS (
    SELECT platform, country_tier, metric, channel,
           rank() OVER (PARTITION BY platform, country_tier, metric ORDER BY est DESC, channel) AS rnk
    FROM v_q3_metric_cell
)
SELECT metric,
       channel,
       count(*) AS strata_compared,
       count(*) FILTER (WHERE rnk = 1) AS times_best,
       count(*) FILTER (WHERE rnk = 3) AS times_worst,
       round(avg(rnk), 2) AS mean_rank
FROM ranked
GROUP BY 1, 2
ORDER BY metric, mean_rank, channel;

-- name: crude_vs_standardized
-- The headline table. `crude` is the pooled rate; `standardized` is the same
-- channel under the cohort's own platform/tier mix; `mix_effect` is what the
-- channel's mix was doing to its reputation (positive = its traffic flattered it).
WITH weights AS (
    SELECT platform, country_tier,
           count(*)::DOUBLE / sum(count(*)) OVER () AS weight
    FROM v_install_cohort
    GROUP BY 1, 2
),
std AS (
    SELECT m.channel, m.metric,
           sum(w.weight * m.est) AS std_est,
           sqrt(sum(w.weight * w.weight * m.var_est)) AS std_se,
           count(*) AS strata_used,
           round(sum(w.weight), 4) AS weight_covered,
           min(m.n) AS min_cell_installs
    FROM v_q3_metric_cell m
    JOIN weights w USING (platform, country_tier)
    GROUP BY 1, 2
),
crude AS (
    SELECT channel, metric,
           sum(est * n) / sum(n) AS crude_est,
           sqrt(sum(var_est * n * n)) / sum(n) AS crude_se,
           sum(n) AS installs
    FROM v_q3_metric_cell
    GROUP BY 1, 2
)
SELECT c.channel,
       c.metric,
       c.installs,
       round(c.crude_est, 4) AS crude,
       round(c.crude_est - 1.96 * c.crude_se, 4) AS crude_lo,
       round(c.crude_est + 1.96 * c.crude_se, 4) AS crude_hi,
       round(s.std_est, 4) AS standardized,
       round(s.std_est - 1.96 * s.std_se, 4) AS std_lo,
       round(s.std_est + 1.96 * s.std_se, 4) AS std_hi,
       round(c.crude_est - s.std_est, 4) AS mix_effect,
       s.strata_used,
       s.weight_covered,
       s.min_cell_installs
FROM crude c
JOIN std s USING (channel, metric)
ORDER BY c.metric, s.std_est DESC;

-- name: standardized_excluding_thin_cells
-- Sensitivity: drop cells under 250 installs, renormalise the weights over what
-- remains, and see whether the ordering survives. If the answer only exists
-- because of one noisy cell, this is where it shows up.
WITH weights AS (
    SELECT platform, country_tier,
           count(*)::DOUBLE / sum(count(*)) OVER () AS weight
    FROM v_install_cohort
    GROUP BY 1, 2
),
kept AS (
    SELECT * FROM v_q3_metric_cell WHERE n >= 250
),
std AS (
    SELECT m.channel, m.metric,
           sum(w.weight * m.est) / sum(w.weight) AS std_est
    FROM kept m
    JOIN weights w USING (platform, country_tier)
    GROUP BY 1, 2
),
all_cells AS (
    SELECT m.channel, m.metric,
           sum(w.weight * m.est) AS std_est
    FROM v_q3_metric_cell m
    JOIN weights w USING (platform, country_tier)
    GROUP BY 1, 2
)
SELECT f.channel, f.metric,
       round(f.std_est, 4) AS standardized_all_cells,
       round(s.std_est, 4) AS standardized_cells_250plus,
       -- + 0.0 folds IEEE negative zero into 0, so a rebuild cannot churn the
       -- file on style alone (`-0.0` vs `0.0`) when the difference is exactly nil.
       round(s.std_est - f.std_est, 4) + 0.0 AS thin_cell_influence
FROM all_cells f
JOIN std s ON s.channel = f.channel AND s.metric = f.metric
ORDER BY f.metric, f.std_est DESC, f.channel;

-- name: revenue_decomposition
-- Where a channel's revenue per install comes from: how many payers it produces
-- times what each payer spends. A channel can lose on one and win on the other,
-- and the two call for different responses.
WITH weights AS (
    SELECT platform, country_tier, count(*)::DOUBLE / sum(count(*)) OVER () AS weight
    FROM v_install_cohort GROUP BY 1, 2
),
conv_std AS (
    SELECT m.channel, sum(w.weight * m.est) AS conv_std
    FROM v_q3_metric_cell m
    JOIN weights w USING (platform, country_tier)
    WHERE m.metric = 'payer_conversion_30'
    GROUP BY 1
),
payer_cell AS (
    SELECT platform, country_tier, acquisition_channel AS channel,
           count(*) AS payers, avg(revenue_net_30) AS rpp,
           var_samp(revenue_net_30) / count(*) AS rpp_var
    FROM v_install_cohort
    WHERE payer_30
    GROUP BY 1, 2, 3
),
rpp_std AS (
    SELECT p.channel,
           sum(w.weight * p.rpp) AS rpp_std,
           sqrt(sum(w.weight * w.weight * p.rpp_var)) AS rpp_se,
           min(p.payers) AS min_cell_payers
    FROM payer_cell p
    JOIN weights w USING (platform, country_tier)
    GROUP BY 1
),
base AS (
    SELECT acquisition_channel AS channel,
           count(*) AS installs,
           count(*) FILTER (WHERE payer_30) AS payers_30,
           avg(payer_30::DOUBLE) AS conv,
           avg(revenue_net_30) AS rpi
    FROM v_install_cohort
    GROUP BY 1
)
SELECT b.channel,
       b.installs,
       b.payers_30,
       round(100.0 * b.conv, 2) AS conversion_pct,
       round(b.rpi / nullif(b.conv, 0), 2) AS revenue_per_payer,
       round(b.rpi, 2) AS revenue_per_install,
       round(100.0 * c.conv_std, 2) AS conversion_pct_standardized,
       round(r.rpp_std, 2) AS revenue_per_payer_standardized,
       round(r.rpp_std - 1.96 * r.rpp_se, 2) AS rpp_std_lo,
       round(r.rpp_std + 1.96 * r.rpp_se, 2) AS rpp_std_hi,
       r.min_cell_payers
FROM base b
JOIN conv_std c USING (channel)
JOIN rpp_std r USING (channel)
ORDER BY b.rpi DESC;

-- name: channel_installs_by_platform
-- How differently the three channels buy, which is the reason for all of the
-- above. Share of each channel's installs by platform and by tier.
WITH ch AS (
    SELECT acquisition_channel, count(*) AS installs FROM v_install_cohort GROUP BY 1
)
SELECT c.acquisition_channel AS channel,
       c.installs,
       round(100.0 * count(*) FILTER (WHERE platform = 'iOS') / c.installs, 1) AS ios_share_pct,
       round(100.0 * count(*) FILTER (WHERE country_tier = 1) / c.installs, 1) AS tier1_share_pct,
       round(100.0 * count(*) FILTER (WHERE country_tier = 4) / c.installs, 1) AS tier4_share_pct
FROM v_install_cohort
JOIN ch c USING (acquisition_channel)
GROUP BY 1, 2
ORDER BY c.installs DESC;
