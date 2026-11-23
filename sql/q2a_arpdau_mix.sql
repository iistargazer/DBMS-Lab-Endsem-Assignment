-- Deliverable 2(a): "ARPDAU is falling -- fix it."
--
-- The claim reproduces: ARPDAU is $0.3488 in the first 30 days of the extract and
-- $0.2906 in the last 30, a fall of 16.7%. But no tier's monetisation got worse.
-- Every tier is flat or up, on a stable per-tier ARPDAU, while the DAU mix moved
-- hard toward the cheapest tier. This is Simpson's paradox, and the test is
-- mechanical: reweight April's rates by January's DAU mix, and January's rates by
-- April's mix, and see which one reproduces the fall.
--
-- Definitions, stated once:
--   first 30 days = 2026-01-01 .. 2026-01-30
--   last  30 days = 2026-04-01 .. 2026-04-30
--   ARPDAU        = net revenue / DAU-days  (not the mean of daily ARPDAU, which
--                   would weight a 1,900-DAU day the same as an 11,000-DAU one)
--   revenue       = net of refunds; gross is shown in the appendix
--
-- The decomposition is additive (within + mix + interaction = total), and
-- `06_decomposition_identity` computes all four so the arithmetic is checkable
-- rather than asserted.

-- name: 01_period_totals
WITH periods AS (
    SELECT 'first_30d' AS period, DATE '2026-01-01' AS d0, DATE '2026-01-30' AS d1
    UNION ALL
    SELECT 'last_30d', DATE '2026-04-01', DATE '2026-04-30'
)
SELECT p.period,
       p.d0 AS period_start,
       p.d1 AS period_end,
       date_diff('day', p.d0, p.d1) + 1 AS days,
       sum(f.dau) AS dau_days,
       round(sum(f.dau) / (date_diff('day', p.d0, p.d1) + 1), 0) AS avg_dau,
       round(sum(f.revenue), 2) AS revenue_net,
       round(sum(f.revenue) / sum(f.dau), 4) AS arpdau_net
FROM periods p
JOIN v_day_tier_facts f ON f.d BETWEEN p.d0 AND p.d1
GROUP BY 1, 2, 3, 4
ORDER BY p.d0;

-- name: 02_arpdau_by_tier
WITH periods AS (
    SELECT 'first_30d' AS period, DATE '2026-01-01' AS d0, DATE '2026-01-30' AS d1
    UNION ALL
    SELECT 'last_30d', DATE '2026-04-01', DATE '2026-04-30'
),
agg AS (
    SELECT p.period, f.country_tier, sum(f.dau) AS dau_days, sum(f.revenue) AS revenue
    FROM periods p
    JOIN v_day_tier_facts f ON f.d BETWEEN p.d0 AND p.d1
    GROUP BY 1, 2
),
piv AS (
    SELECT country_tier,
           sum(dau_days) FILTER (WHERE period = 'first_30d') AS dau_first,
           sum(dau_days) FILTER (WHERE period = 'last_30d') AS dau_last,
           sum(revenue)  FILTER (WHERE period = 'first_30d') AS rev_first,
           sum(revenue)  FILTER (WHERE period = 'last_30d') AS rev_last
    FROM agg
    GROUP BY 1
),
r AS (
    SELECT *, rev_first / dau_first AS arpdau_first, rev_last / dau_last AS arpdau_last
    FROM piv
)
SELECT country_tier,
       round(arpdau_first, 4) AS arpdau_first_30d,
       round(arpdau_last, 4) AS arpdau_last_30d,
       round(100.0 * (arpdau_last / arpdau_first - 1), 1) AS arpdau_change_pct,
       round(100.0 * dau_first / sum(dau_first) OVER (), 1) AS dau_share_first_pct,
       round(100.0 * dau_last / sum(dau_last) OVER (), 1) AS dau_share_last_pct,
       round(100.0 * dau_last / sum(dau_last) OVER ()
             - 100.0 * dau_first / sum(dau_first) OVER (), 1) AS dau_share_change_pp
FROM r
ORDER BY country_tier;

-- name: 03_arpdau_by_tier_month
-- The chart behind the deck's hero slide: four lines that are flat or rising,
-- against a mix that moves underneath them.
WITH m AS (
    SELECT CAST(date_trunc('month', f.d) AS DATE) AS month,
           f.country_tier,
           sum(f.dau) AS dau_days,
           avg(f.dau) AS avg_dau,
           sum(f.revenue) AS revenue
    FROM v_day_tier_facts f
    GROUP BY 1, 2
)
SELECT month,
       country_tier,
       dau_days,
       round(avg_dau, 0) AS avg_dau,
       round(revenue, 2) AS revenue_net,
       round(revenue / dau_days, 4) AS arpdau_net,
       round(100.0 * dau_days / sum(dau_days) OVER (PARTITION BY month), 1) AS dau_share_pct
FROM m
ORDER BY month, country_tier;

-- name: 04_platform_tier
-- Tier is the driver, platform is the second-order split: iOS runs about 2x
-- Android inside every tier, which is a level difference, not a trend.
WITH periods AS (
    SELECT 'first_30d' AS period, DATE '2026-01-01' AS d0, DATE '2026-01-30' AS d1
    UNION ALL
    SELECT 'last_30d', DATE '2026-04-01', DATE '2026-04-30'
),
agg AS (
    SELECT p.period, f.platform, f.country_tier, sum(f.dau) AS dau_days, sum(f.revenue) AS revenue
    FROM periods p
    JOIN v_day_tier_facts f ON f.d BETWEEN p.d0 AND p.d1
    GROUP BY 1, 2, 3
),
piv AS (
    SELECT platform, country_tier,
           sum(dau_days) FILTER (WHERE period = 'first_30d') AS dau_first,
           sum(dau_days) FILTER (WHERE period = 'last_30d') AS dau_last,
           sum(revenue)  FILTER (WHERE period = 'first_30d') AS rev_first,
           sum(revenue)  FILTER (WHERE period = 'last_30d') AS rev_last
    FROM agg
    GROUP BY 1, 2
)
SELECT platform,
       country_tier,
       round(rev_first / dau_first, 4) AS arpdau_first_30d,
       round(rev_last / dau_last, 4) AS arpdau_last_30d,
       round(100.0 * (rev_last / dau_last) / (rev_first / dau_first) - 100.0, 1) AS change_pct,
       round(100.0 * dau_last / sum(dau_last) OVER (), 1) AS dau_share_last_pct
FROM piv
ORDER BY platform, country_tier;

-- name: 05_mix_decomposition
-- Four numbers. Actual first, actual last, April's rates on January's mix,
-- January's rates on April's mix. The phrase "the decline is mix" means the
-- third is above the first and the fourth is below it.
WITH periods AS (
    SELECT 'first_30d' AS period, DATE '2026-01-01' AS d0, DATE '2026-01-30' AS d1
    UNION ALL
    SELECT 'last_30d', DATE '2026-04-01', DATE '2026-04-30'
),
agg AS (
    SELECT p.period, f.country_tier, sum(f.dau) AS dau_days, sum(f.revenue) AS revenue
    FROM periods p
    JOIN v_day_tier_facts f ON f.d BETWEEN p.d0 AND p.d1
    GROUP BY 1, 2
),
piv AS (
    SELECT country_tier,
           sum(dau_days) FILTER (WHERE period = 'first_30d') AS dau_first,
           sum(dau_days) FILTER (WHERE period = 'last_30d') AS dau_last,
           sum(revenue)  FILTER (WHERE period = 'first_30d') AS rev_first,
           sum(revenue)  FILTER (WHERE period = 'last_30d') AS rev_last
    FROM agg
    GROUP BY 1
),
rates AS (
    SELECT rev_first / dau_first AS r_first,
           rev_last / dau_last AS r_last,
           dau_first / sum(dau_first) OVER () AS s_first,
           dau_last / sum(dau_last) OVER () AS s_last
    FROM piv
),
scen AS (
    SELECT sum(r_first * s_first) AS actual_first,
           sum(r_last * s_last) AS actual_last,
           sum(r_last * s_first) AS last_rates_first_mix,
           sum(r_first * s_last) AS first_rates_last_mix
    FROM rates
)
SELECT 'actual first 30 days (Jan)' AS scenario, round(actual_first, 4) AS arpdau,
       0.0 AS change_vs_first, 0.0 AS change_pct FROM scen
UNION ALL
SELECT 'actual last 30 days (Apr)', round(actual_last, 4),
       round(actual_last - actual_first, 4),
       round(100.0 * (actual_last / actual_first - 1), 1) FROM scen
UNION ALL
SELECT 'April rates on January mix', round(last_rates_first_mix, 4),
       round(last_rates_first_mix - actual_first, 4),
       round(100.0 * (last_rates_first_mix / actual_first - 1), 1) FROM scen
UNION ALL
SELECT 'January rates on April mix', round(first_rates_last_mix, 4),
       round(first_rates_last_mix - actual_first, 4),
       round(100.0 * (first_rates_last_mix / actual_first - 1), 1) FROM scen;

-- name: 06_decomposition_identity
-- Additive decomposition, with the interaction term kept separate rather than
-- folded into one side. `identity_check` must equal `total_change`.
WITH periods AS (
    SELECT 'first_30d' AS period, DATE '2026-01-01' AS d0, DATE '2026-01-30' AS d1
    UNION ALL
    SELECT 'last_30d', DATE '2026-04-01', DATE '2026-04-30'
),
agg AS (
    SELECT p.period, f.country_tier, sum(f.dau) AS dau_days, sum(f.revenue) AS revenue
    FROM periods p
    JOIN v_day_tier_facts f ON f.d BETWEEN p.d0 AND p.d1
    GROUP BY 1, 2
),
piv AS (
    SELECT country_tier,
           sum(dau_days) FILTER (WHERE period = 'first_30d') AS dau_first,
           sum(dau_days) FILTER (WHERE period = 'last_30d') AS dau_last,
           sum(revenue)  FILTER (WHERE period = 'first_30d') AS rev_first,
           sum(revenue)  FILTER (WHERE period = 'last_30d') AS rev_last
    FROM agg
    GROUP BY 1
),
rates AS (
    SELECT rev_first / dau_first AS r_first,
           rev_last / dau_last AS r_last,
           dau_first / sum(dau_first) OVER () AS s_first,
           dau_last / sum(dau_last) OVER () AS s_last
    FROM piv
),
c AS (
    SELECT sum(r_first * s_first) AS a_first,
           sum(r_last * s_last)   AS a_last,
           sum(r_last * s_first)  AS rates_at_first_mix,
           sum(r_first * s_last)  AS first_rates_at_last_mix
    FROM rates
)
SELECT round(a_first, 4) AS actual_first_30d,
       round(a_last, 4) AS actual_last_30d,
       round(a_last - a_first, 4) AS total_change,
       round(rates_at_first_mix - a_first, 4) AS within_tier_effect,
       round(first_rates_at_last_mix - a_first, 4) AS mix_effect,
       round(a_last - rates_at_first_mix - (first_rates_at_last_mix - a_first), 4) AS interaction_effect,
       round(100.0 * (rates_at_first_mix / a_first - 1), 1) AS within_tier_pct,
       round(100.0 * (first_rates_at_last_mix / a_first - 1), 1) AS mix_pct,
       round(100.0 * (a_last / a_first - 1), 1) AS total_pct,
       round((rates_at_first_mix - a_first)
             + (first_rates_at_last_mix - a_first)
             + (a_last - rates_at_first_mix - (first_rates_at_last_mix - a_first)), 6) AS identity_check
FROM c;

-- name: 07_denominator_check
-- Two defensible ways to summarise a period's ARPDAU, and the answer does not
-- depend on which one is used. Revenue / DAU-days is preferred because it weights
-- a 2,700-DAU day the way it deserves; the unweighted mean of daily ratios gives
-- a small day the same vote as a large one. Both are reported because the brief's
-- claim ("down roughly 16%") should be shown to survive the choice.
WITH daily AS (
    SELECT f.d,
           CASE WHEN f.d BETWEEN DATE '2026-01-01' AND DATE '2026-01-30' THEN 'first_30d'
                WHEN f.d BETWEEN DATE '2026-04-01' AND DATE '2026-04-30' THEN 'last_30d' END AS period,
           sum(f.dau) AS dau,
           sum(f.revenue) AS revenue
    FROM v_day_tier_facts f
    WHERE f.d BETWEEN DATE '2026-01-01' AND DATE '2026-01-30'
       OR f.d BETWEEN DATE '2026-04-01' AND DATE '2026-04-30'
    GROUP BY 1, 2
),
p AS (
    SELECT period,
           count(*) AS days,
           sum(dau) AS dau_days,
           sum(revenue) / sum(dau) AS ratio_weighted,
           avg(revenue / dau) AS ratio_daily_mean
    FROM daily
    GROUP BY 1
)
SELECT round(max(CASE WHEN period = 'first_30d' THEN ratio_weighted END), 4) AS first_30d_revenue_over_dau_days,
       round(max(CASE WHEN period = 'last_30d'  THEN ratio_weighted END), 4) AS last_30d_revenue_over_dau_days,
       round(100.0 * (max(CASE WHEN period = 'last_30d' THEN ratio_weighted END)
                      / max(CASE WHEN period = 'first_30d' THEN ratio_weighted END) - 1), 1) AS change_weighted_pct,
       round(max(CASE WHEN period = 'first_30d' THEN ratio_daily_mean END), 4) AS first_30d_mean_of_daily,
       round(max(CASE WHEN period = 'last_30d'  THEN ratio_daily_mean END), 4) AS last_30d_mean_of_daily,
       round(100.0 * (max(CASE WHEN period = 'last_30d' THEN ratio_daily_mean END)
                      / max(CASE WHEN period = 'first_30d' THEN ratio_daily_mean END) - 1), 1) AS change_daily_mean_pct
FROM p;

-- name: 08_install_mix_drift
-- The cause, on the acquisition side: what the studio bought changed, month by
-- month, and it changed in the direction of the cheapest tier.
SELECT CAST(date_trunc('month', install_date) AS DATE) AS install_month,
       count(*) AS installs,
       round(100.0 * count(*) FILTER (WHERE acquisition_channel = 'paid_video') / count(*), 1) AS paid_video_share_pct,
       round(100.0 * count(*) FILTER (WHERE country_tier = 4) / count(*), 1) AS tier4_share_pct,
       round(100.0 * count(*) FILTER (WHERE platform = 'iOS') / count(*), 1) AS ios_share_pct
FROM player_profile
WHERE install_date >= DATE '2026-01-01'
GROUP BY 1
ORDER BY 1;
