-- Deliverable 1(c): spender behaviour.
--
-- Concentration is reported twice, because the two are both defensible and give
-- very different answers: ranking *payers only* answers "how top-heavy is the
-- paying base", ranking *everyone* answers "how concentrated is revenue across
-- the whole player base". The population is named in the output.

-- name: revenue_concentration
WITH payer_totals AS (
    SELECT device_id, sum(usd_amount) AS revenue
    FROM v_purchase_clean
    GROUP BY 1
    HAVING sum(usd_amount) > 0
),
all_totals AS (
    SELECT p.device_id, coalesce(t.revenue, 0) AS revenue
    FROM player_profile p
    LEFT JOIN payer_totals t USING (device_id)
),
populations AS (
    SELECT 'payers only' AS population, device_id, revenue FROM payer_totals
    UNION ALL
    SELECT 'all players' AS population, device_id, revenue FROM all_totals
),
ranked AS (
    SELECT population, revenue,
           row_number() OVER (PARTITION BY population ORDER BY revenue DESC) AS rk,
           count(*) OVER (PARTITION BY population) AS n,
           sum(revenue) OVER (PARTITION BY population) AS total
    FROM populations
),
bounds AS (
    SELECT population, n, total,
           greatest(1, CAST(n * 0.01 AS INTEGER)) AS k1,
           greatest(1, CAST(n * 0.10 AS INTEGER)) AS k10,
           greatest(1, CAST(n * 0.50 AS INTEGER)) AS k50
    FROM ranked
    WHERE rk = 1
),
tagged AS (
    SELECT r.rk, r.revenue, b.population, b.n, b.total, b.k1, b.k10, b.k50
    FROM ranked r JOIN bounds b USING (population)
)
SELECT population,
       max(n) AS population_size,
       round(max(total), 2) AS total_revenue,
       round(100.0 * sum(CASE WHEN rk <= k1 THEN revenue ELSE 0 END) / max(total), 1) AS top_1pct_share,
       round(100.0 * sum(CASE WHEN rk <= k10 THEN revenue ELSE 0 END) / max(total), 1) AS top_10pct_share,
       round(100.0 * sum(CASE WHEN rk <= k50 THEN revenue ELSE 0 END) / max(total), 1) AS top_50pct_share
FROM tagged
GROUP BY population
ORDER BY population DESC;

-- name: time_to_first_purchase_distribution
-- Restricted to players who installed inside the window: purchases are only
-- recorded from 2026-01-01, so for a 2025 install the observed "time to first
-- purchase" would measure the extract start, not the player. Players with less
-- than 30 days of follow-up are dropped for the same reason.
WITH base AS (
    SELECT p.device_id, p.install_date, p.first_purchase_date,
           date_diff('day', p.install_date, p.first_purchase_date) AS days_to_first,
           date_diff('day', p.install_date, DATE '2026-04-30') AS follow_up_days
    FROM player_profile p
    WHERE p.install_date >= DATE '2026-01-01'
),
eligible AS (
    SELECT * FROM base WHERE follow_up_days >= 30
),
bucketed AS (
    SELECT CASE WHEN first_purchase_date IS NULL THEN 'never paid'
                WHEN days_to_first <= 0 THEN '0 (install day)'
                WHEN days_to_first = 1 THEN '1'
                WHEN days_to_first <= 3 THEN '2-3'
                WHEN days_to_first <= 7 THEN '4-7'
                WHEN days_to_first <= 14 THEN '8-14'
                WHEN days_to_first <= 30 THEN '15-30'
                ELSE '31+' END AS bucket,
           days_to_first
    FROM eligible
)
SELECT bucket,
       count(*) AS players,
       round(100.0 * count(*) / sum(count(*)) OVER (), 1) AS pct_of_eligible
FROM bucketed
GROUP BY bucket
ORDER BY min(CASE WHEN days_to_first IS NULL THEN 99999 ELSE days_to_first END);

-- name: time_to_first_purchase_summary
WITH base AS (
    SELECT p.first_purchase_date,
           date_diff('day', p.install_date, p.first_purchase_date) AS days_to_first
    FROM player_profile p
    WHERE p.install_date >= DATE '2026-01-01'
      AND date_diff('day', p.install_date, DATE '2026-04-30') >= 30
)
SELECT count(*) AS eligible_players,
       count(first_purchase_date) AS players_who_paid,
       round(100.0 * count(first_purchase_date) / count(*), 2) AS pct_who_ever_paid,
       round(100.0 * (count(*) - count(first_purchase_date)) / count(*), 2) AS pct_never_paid,
       round(avg(days_to_first), 1) AS mean_days_to_first,
       round(quantile_cont(days_to_first, 0.25), 0) AS p25_days,
       round(quantile_cont(days_to_first, 0.50), 0) AS median_days,
       round(quantile_cont(days_to_first, 0.75), 0) AS p75_days
FROM base;

-- name: repeat_purchase_gap
-- Window function rather than a self-join: one ordered pass per player.
WITH sequence AS (
    SELECT device_id,
           CAST(event_ts AS TIMESTAMP) AS ts,
           lag(CAST(event_ts AS TIMESTAMP)) OVER (PARTITION BY device_id ORDER BY event_ts) AS prev_ts
    FROM v_purchase_clean
    WHERE usd_amount > 0
),
gaps AS (
    SELECT device_id, date_diff('day', prev_ts, ts) AS gap_days
    FROM sequence
    WHERE prev_ts IS NOT NULL
)
SELECT count(DISTINCT device_id) AS repeat_buyers,
       count(*) AS gaps_measured,
       round(avg(gap_days), 1) AS mean_days,
       round(quantile_cont(gap_days, 0.25), 0) AS p25_days,
       round(quantile_cont(gap_days, 0.50), 0) AS median_days,
       round(quantile_cont(gap_days, 0.75), 0) AS p75_days,
       round(quantile_cont(gap_days, 0.90), 0) AS p90_days,
       round(100.0 * count(*) FILTER (WHERE gap_days <= 1) / count(*), 1) AS pct_gap_within_1_day,
       round(100.0 * count(*) FILTER (WHERE gap_days <= 7) / count(*), 1) AS pct_gap_within_7_days
FROM gaps;

-- name: spend_distribution_by_payer
-- Context for the concentration numbers: how many purchases a payer makes.
WITH per_player AS (
    SELECT device_id, count(*) AS purchases, sum(usd_amount) AS revenue
    FROM v_purchase_clean
    WHERE usd_amount > 0
    GROUP BY 1
)
SELECT CASE WHEN purchases = 1 THEN '1'
            WHEN purchases <= 3 THEN '2-3'
            WHEN purchases <= 10 THEN '4-10'
            ELSE '11+' END AS purchase_count_band,
       count(*) AS payers,
       round(100.0 * count(*) / sum(count(*)) OVER (), 1) AS pct_of_payers,
       round(sum(revenue), 2) AS revenue,
       round(100.0 * sum(revenue) / sum(sum(revenue)) OVER (), 1) AS pct_of_revenue
FROM per_player
GROUP BY 1
ORDER BY min(purchases);
