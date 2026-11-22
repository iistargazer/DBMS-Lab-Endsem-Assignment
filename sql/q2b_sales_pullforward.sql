-- Deliverable 2(b) stress test: are the promotions incremental, or are they
-- moving purchases that would have happened anyway?
--
-- The first fact to establish is that the promotions carry no price incentive:
-- sale_flag does not change usd_amount or currency_granted for any pack (see
-- dq09). So the lift cannot be a discount effect, and the question becomes
-- purely about timing and payer acquisition.
--
-- Tests below, in the order the argument needs them:
--   01  the sale windows themselves
--   02  sale days vs normal days on traffic-adjusted measures
--   03  the offset curve: is there a trough before/after a window?
--   04  per-window trough and recovery, and whether it is intensifying (the
--       "players learned the schedule" story)
--   05  who buys on a sale day: new payers or existing ones?
--   06  what happens to a player's next purchase after buying in a sale
--   07  do sale-acquired payers repeat at the same rate as normal-day ones?
--   08  front-loading inside the window
--   09  excess revenue against a local counterfactual
--   10  pack mix on sale vs normal days

-- name: 01_sale_windows
SELECT w.window_id,
       w.window_start,
       w.window_end,
       date_diff('day', w.window_start, w.window_end) + 1 AS window_days,
       round(sum(f.revenue), 2) AS sale_revenue,
       sum(f.txns) AS transactions,
       round(avg(f.buyers), 0) AS avg_daily_buyers,
       round(avg(f.dau), 0) AS avg_dau
FROM v_sale_window w
JOIN v_day_facts f ON f.d BETWEEN w.window_start AND w.window_end
GROUP BY 1, 2, 3
ORDER BY 1;

-- name: 02_sale_vs_normal_days
SELECT is_sale_day,
       count(*) AS days,
       round(avg(dau), 0) AS avg_dau,
       round(avg(revenue), 2) AS avg_daily_revenue,
       round(avg(buyers), 1) AS avg_daily_buyers,
       round(100.0 * avg(buyers) / avg(dau), 2) AS pct_dau_buying,
       round(avg(revenue) / avg(dau), 4) AS arpdau,
       round(sum(revenue) / nullif(sum(buyers), 0), 2) AS revenue_per_buyer,
       round(sum(txns) * 1.0 / nullif(sum(buyers), 0), 2) AS txns_per_buyer
FROM v_day_facts
GROUP BY 1
ORDER BY 1 DESC;

-- name: 03_offset_curve
-- Revenue and buyers by distance from a window's first day, each window indexed
-- to its OWN mid-cycle days (t = -7..-5 and +5..+7). Indexing to a single
-- pooled "normal day" average would be wrong here: DAU grows six-fold across
-- the window, so pooled non-sale days are dominated by the early, small months
-- and every late-window day would look above average for reasons unrelated to
-- promotions. Per-window indexing cancels that trend.
WITH offsets AS (SELECT unnest(generate_series(-7, 7)) AS k),
grid AS (
    SELECT w.window_id, o.k, CAST(w.window_start + CAST(o.k AS INTEGER) AS DATE) AS d
    FROM v_sale_window w CROSS JOIN offsets o
),
joined AS (
    SELECT g.window_id, g.k, f.revenue, f.buyers
    FROM grid g JOIN v_day_facts f ON f.d = g.d
),
baseline AS (
    SELECT window_id, avg(revenue) AS base_revenue, avg(buyers) AS base_buyers
    FROM joined
    WHERE k IN (-7, -6, -5, 5, 6, 7)
    GROUP BY 1
),
indexed AS (
    SELECT j.k, j.revenue / b.base_revenue AS revenue_index, j.buyers / b.base_buyers AS buyers_index
    FROM joined j JOIN baseline b USING (window_id)
)
SELECT k AS offset_days_from_window_start,
       count(*) AS windows_observed,
       round(avg(revenue_index), 2) AS revenue_index_vs_midcycle,
       round(avg(buyers_index), 2) AS buyers_index_vs_midcycle
FROM indexed
GROUP BY 1
ORDER BY 1;

-- name: 04_lull_per_window
-- Three days before the window vs inside it vs three days after.
WITH pre AS (
    SELECT w.window_id, avg(f.revenue) AS pre_avg_revenue
    FROM v_sale_window w
    JOIN v_day_facts f ON f.d BETWEEN w.window_start - 3 AND w.window_start - 1
    GROUP BY 1
),
post AS (
    SELECT w.window_id, avg(f.revenue) AS post_avg_revenue
    FROM v_sale_window w
    JOIN v_day_facts f ON f.d BETWEEN w.window_end + 1 AND w.window_end + 3
    GROUP BY 1
),
inside AS (
    SELECT w.window_id, avg(f.revenue) AS window_avg_revenue, sum(f.revenue) AS window_revenue
    FROM v_sale_window w
    JOIN v_day_facts f ON f.d BETWEEN w.window_start AND w.window_end
    GROUP BY 1
)
SELECT w.window_id, w.window_start,
       round(i.window_revenue, 0) AS window_revenue,
       round(i.window_avg_revenue, 0) AS window_avg_day,
       round(pre.pre_avg_revenue, 0) AS pre3_avg_day,
       round(post.post_avg_revenue, 0) AS post3_avg_day,
       round(i.window_avg_revenue / pre.pre_avg_revenue, 2) AS window_vs_pre,
       round(post.post_avg_revenue / pre.pre_avg_revenue, 2) AS post_vs_pre
FROM v_sale_window w
JOIN inside i USING (window_id)
JOIN pre USING (window_id)
JOIN post USING (window_id)
ORDER BY 1;

-- name: 05_buyer_composition
-- If promotions only pull purchases forward, sale-day transactions should look
-- like ordinary transactions: mostly existing payers. If they acquire payers,
-- the first-purchase share should rise.
WITH paid AS (
    SELECT device_id, CAST(event_ts AS DATE) AS d
    FROM v_purchase_clean
    WHERE usd_amount > 0
),
firsts AS (
    SELECT device_id, min(d) AS first_paid_day FROM paid GROUP BY 1
),
flagged AS (
    SELECT p.device_id, p.d, fs.first_paid_day, f.is_sale_day,
           (SELECT count(*) FROM paid q
             WHERE q.device_id = p.device_id AND q.d BETWEEN p.d - 14 AND p.d - 1) AS prior_14d_txns
    FROM paid p
    JOIN firsts fs USING (device_id)
    JOIN v_day_facts f ON f.d = p.d
)
SELECT is_sale_day,
       count(*) AS transactions,
       count(DISTINCT device_id) AS buyers,
       round(count(*) * 1.0 / count(DISTINCT device_id), 2) AS txns_per_buyer,
       round(100.0 * count(*) FILTER (WHERE d = first_paid_day) / count(*), 1) AS pct_txns_are_first_purchase,
       round(100.0 * count(DISTINCT CASE WHEN d = first_paid_day THEN device_id END)
             / count(DISTINCT device_id), 1) AS pct_buyers_new_to_paying,
       round(100.0 * count(*) FILTER (WHERE prior_14d_txns > 0) / count(*), 1) AS pct_txns_from_prior_14d_buyers
FROM flagged
GROUP BY 1
ORDER BY 1 DESC;

-- name: 06_next_purchase_gap_after_sale
-- Buying in a sale should defer the next purchase if revenue is being pulled
-- forward. A shorter gap after sale purchases says the opposite.
WITH sequence AS (
    SELECT device_id,
           CAST(event_ts AS TIMESTAMP) AS ts,
           CAST(event_ts AS DATE) AS d,
           lead(CAST(event_ts AS TIMESTAMP)) OVER (PARTITION BY device_id ORDER BY event_ts) AS next_ts
    FROM v_purchase_clean
    WHERE usd_amount > 0
)
SELECT f.is_sale_day,
       count(*) AS purchases_with_a_next_purchase,
       round(avg(date_diff('day', ts, next_ts)), 1) AS mean_gap_days,
       round(quantile_cont(date_diff('day', ts, next_ts), 0.50), 0) AS median_gap_days,
       round(100.0 * count(*) FILTER (WHERE date_diff('day', ts, next_ts) <= 14) / count(*), 1) AS pct_next_within_14d
FROM sequence s
JOIN v_day_facts f ON f.d = s.d
WHERE next_ts IS NOT NULL
GROUP BY 1
ORDER BY 1 DESC;

-- name: 07_sale_acquired_payer_quality
-- Do payers whose first purchase landed in a sale come back? Restricted to
-- first purchases with a full 30 days of follow-up.
WITH paid AS (
    SELECT device_id, CAST(event_ts AS DATE) AS d
    FROM v_purchase_clean WHERE usd_amount > 0
),
firsts AS (
    SELECT device_id, min(d) AS first_paid_day FROM paid GROUP BY 1
),
info AS (
    SELECT fs.device_id, fs.first_paid_day, f.is_sale_day,
           (SELECT count(*) FROM paid q
             WHERE q.device_id = fs.device_id
               AND q.d > fs.first_paid_day AND q.d <= fs.first_paid_day + 30) AS repeats_30d
    FROM firsts fs
    JOIN v_day_facts f ON f.d = fs.first_paid_day
    WHERE date_diff('day', fs.first_paid_day, DATE '2026-04-30') >= 30
)
SELECT is_sale_day,
       count(*) AS first_time_payers,
       sum(repeats_30d) AS repeat_transactions_in_30d,
       round(avg(repeats_30d), 3) AS avg_repeat_txns,
       round(100.0 * avg(CASE WHEN repeats_30d > 0 THEN 1 ELSE 0 END), 1) AS pct_who_buy_again_within_30d
FROM info
GROUP BY 1
ORDER BY 1 DESC;

-- name: 08_frontloading
SELECT f.day_of_window,
       count(DISTINCT f.d) AS days,
       round(avg(f.revenue), 0) AS avg_revenue,
       round(avg(f.buyers), 1) AS avg_buyers,
       round(avg(f.dau), 0) AS avg_dau
FROM v_day_facts f
WHERE f.is_sale_day
GROUP BY 1
ORDER BY 1;

-- name: 09_sale_window_counterfactual
-- For each window: what the two sale days earned against a mid-cycle
-- counterfactual, what the four days before it gave up, what the three days
-- after it gave up, and the net. This is the test of Finance's claim: pure
-- pull-forward shows a big excess that is cancelled by the troughs.
--
-- Counterfactual = mean revenue on the same window's mid-cycle days
-- (t = -7..-5 and +5..+7), i.e. days far enough from the promotion to be
-- uninfluenced by it, but close enough in time to share its traffic level.
WITH offsets AS (SELECT unnest(generate_series(-7, 10)) AS k),
grid AS (
    SELECT w.window_id, o.k, CAST(w.window_start + CAST(o.k AS INTEGER) AS DATE) AS d
    FROM v_sale_window w CROSS JOIN offsets o
),
joined AS (
    SELECT g.window_id, g.k, f.revenue
    FROM grid g JOIN v_day_facts f ON f.d = g.d
),
baseline AS (
    SELECT window_id, avg(revenue) AS base_revenue
    FROM joined WHERE k IN (-7, -6, -5, 5, 6, 7)
    GROUP BY 1
),
parts AS (
    SELECT b.window_id, b.base_revenue,
           sum(CASE WHEN j.k IN (0, 1) THEN j.revenue END) AS sale_revenue,
           sum(CASE WHEN j.k IN (-4, -3, -2, -1) THEN j.revenue END) AS pre4_revenue,
           sum(CASE WHEN j.k IN (2, 3, 4) THEN j.revenue END) AS post3_revenue
    FROM baseline b JOIN joined j USING (window_id)
    GROUP BY 1, 2
)
SELECT p.window_id,
       w.window_start,
       round(p.base_revenue, 0) AS counterfactual_day_revenue,
       round(p.sale_revenue, 0) AS sale_day_revenue,
       round(p.sale_revenue - 2 * p.base_revenue, 0) AS excess_revenue,
       round(4 * p.base_revenue - p.pre4_revenue, 0) AS pre_window_shortfall,
       round(3 * p.base_revenue - p.post3_revenue, 0) AS post_window_shortfall,
       round((p.sale_revenue - 2 * p.base_revenue)
             - (4 * p.base_revenue - p.pre4_revenue)
             - (3 * p.base_revenue - p.post3_revenue), 0) AS net_revenue
FROM parts p
JOIN v_sale_window w USING (window_id)
ORDER BY p.window_id;

-- name: 10_counterfactual_total
WITH offsets AS (SELECT unnest(generate_series(-7, 10)) AS k),
grid AS (
    SELECT w.window_id, o.k, CAST(w.window_start + CAST(o.k AS INTEGER) AS DATE) AS d
    FROM v_sale_window w CROSS JOIN offsets o
),
joined AS (
    SELECT g.window_id, g.k, f.revenue
    FROM grid g JOIN v_day_facts f ON f.d = g.d
),
baseline AS (
    SELECT window_id, avg(revenue) AS base_revenue
    FROM joined WHERE k IN (-7, -6, -5, 5, 6, 7) GROUP BY 1
),
parts AS (
    SELECT b.window_id, b.base_revenue,
           sum(CASE WHEN j.k IN (0, 1) THEN j.revenue END) AS sale_revenue,
           sum(CASE WHEN j.k IN (-4, -3, -2, -1) THEN j.revenue END) AS pre4_revenue,
           sum(CASE WHEN j.k IN (2, 3, 4) THEN j.revenue END) AS post3_revenue
    FROM baseline b JOIN joined j USING (window_id)
    GROUP BY 1, 2
)
SELECT round(sum(sale_revenue), 0) AS sale_day_revenue,
       round(sum(2 * base_revenue), 0) AS counterfactual_revenue,
       round(sum(sale_revenue - 2 * base_revenue), 0) AS excess_revenue,
       round(sum(4 * base_revenue - pre4_revenue), 0) AS pre_window_shortfall,
       round(sum(3 * base_revenue - post3_revenue), 0) AS post_window_shortfall,
       round(sum((sale_revenue - 2 * base_revenue)
                 - (4 * base_revenue - pre4_revenue)
                 - (3 * base_revenue - post3_revenue)), 0) AS net_revenue,
       round(100.0 * sum(sale_revenue) / (SELECT sum(revenue) FROM v_day_facts), 1) AS pct_of_total_revenue
FROM parts;

-- name: 11_pack_mix_sale_vs_normal
WITH per_pack AS (
    SELECT c.pack_name, f.is_sale_day, sum(c.usd_amount) AS revenue, count(*) AS purchases
    FROM v_purchase_clean c
    JOIN v_day_facts f ON f.d = CAST(c.event_ts AS DATE)
    WHERE c.usd_amount > 0
    GROUP BY 1, 2
)
SELECT pack_name,
       round(max(CASE WHEN is_sale_day THEN purchases END), 0) AS purchases_on_sale,
       round(max(CASE WHEN NOT is_sale_day THEN purchases END), 0) AS purchases_normal,
       round(max(CASE WHEN is_sale_day THEN revenue END) / nullif(max(CASE WHEN NOT is_sale_day THEN revenue END), 0), 2) AS sale_to_normal_revenue_ratio
FROM per_pack
GROUP BY 1
ORDER BY 1;

-- name: 12_new_payer_counterfactual
-- The same three-part decomposition for first-time payers. If promotions only
-- shift timing, the days before and after should produce correspondingly fewer
-- new payers.
WITH paid AS (
    SELECT device_id, min(CAST(event_ts AS DATE)) AS first_paid_day
    FROM v_purchase_clean WHERE usd_amount > 0 GROUP BY 1
),
daily_new AS (
    SELECT first_paid_day AS d, count(*) AS new_payers FROM paid GROUP BY 1
),
offsets AS (SELECT unnest(generate_series(-7, 10)) AS k),
grid AS (
    SELECT w.window_id, o.k, CAST(w.window_start + CAST(o.k AS INTEGER) AS DATE) AS d
    FROM v_sale_window w CROSS JOIN offsets o
),
joined AS (
    SELECT g.window_id, g.k, coalesce(n.new_payers, 0) AS new_payers
    FROM grid g
    LEFT JOIN daily_new n ON n.d = g.d
),
baseline AS (
    SELECT window_id, avg(new_payers) AS base_new_payers
    FROM joined WHERE k IN (-7, -6, -5, 5, 6, 7) GROUP BY 1
),
parts AS (
    SELECT b.window_id, b.base_new_payers,
           sum(CASE WHEN j.k IN (0, 1) THEN j.new_payers END) AS sale_new_payers,
           sum(CASE WHEN j.k IN (-4, -3, -2, -1) THEN j.new_payers END) AS pre4_new_payers,
           sum(CASE WHEN j.k IN (2, 3, 4) THEN j.new_payers END) AS post3_new_payers
    FROM baseline b JOIN joined j USING (window_id)
    GROUP BY 1, 2
)
SELECT window_id,
       round(base_new_payers, 1) AS counterfactual_new_payers_per_day,
       sale_new_payers,
       round(sale_new_payers - 2 * base_new_payers, 1) AS excess_new_payers,
       round(4 * base_new_payers - pre4_new_payers, 1) AS pre_window_shortfall,
       round(3 * base_new_payers - post3_new_payers, 1) AS post_window_shortfall,
       round((sale_new_payers - 2 * base_new_payers)
             - (4 * base_new_payers - pre4_new_payers)
             - (3 * base_new_payers - post3_new_payers), 1) AS net_new_payers
FROM parts
ORDER BY window_id;

-- name: 13_new_payer_counterfactual_total
WITH paid AS (
    SELECT device_id, min(CAST(event_ts AS DATE)) AS first_paid_day
    FROM v_purchase_clean WHERE usd_amount > 0 GROUP BY 1
),
daily_new AS (
    SELECT first_paid_day AS d, count(*) AS new_payers FROM paid GROUP BY 1
),
offsets AS (SELECT unnest(generate_series(-7, 10)) AS k),
grid AS (
    SELECT w.window_id, o.k, CAST(w.window_start + CAST(o.k AS INTEGER) AS DATE) AS d
    FROM v_sale_window w CROSS JOIN offsets o
),
joined AS (
    SELECT g.window_id, g.k, coalesce(n.new_payers, 0) AS new_payers
    FROM grid g LEFT JOIN daily_new n ON n.d = g.d
),
baseline AS (
    SELECT window_id, avg(new_payers) AS base_new_payers
    FROM joined WHERE k IN (-7, -6, -5, 5, 6, 7) GROUP BY 1
),
parts AS (
    SELECT b.window_id, b.base_new_payers,
           sum(CASE WHEN j.k IN (0, 1) THEN j.new_payers END) AS sale_new_payers,
           sum(CASE WHEN j.k IN (-4, -3, -2, -1) THEN j.new_payers END) AS pre4_new_payers,
           sum(CASE WHEN j.k IN (2, 3, 4) THEN j.new_payers END) AS post3_new_payers
    FROM baseline b JOIN joined j USING (window_id)
    GROUP BY 1, 2
)
SELECT sum(sale_new_payers) AS sale_day_new_payers,
       round(sum(2 * base_new_payers), 0) AS counterfactual_new_payers,
       round(sum(sale_new_payers - 2 * base_new_payers), 0) AS excess_new_payers,
       round(sum(4 * base_new_payers - pre4_new_payers), 0) AS pre_window_shortfall,
       round(sum(3 * base_new_payers - post3_new_payers), 0) AS post_window_shortfall,
       round(sum((sale_new_payers - 2 * base_new_payers)
                 - (4 * base_new_payers - pre4_new_payers)
                 - (3 * base_new_payers - post3_new_payers)), 0) AS net_new_payers
FROM parts;

-- name: 14_schedule_learning_trend
-- Regress each window's pre-window trough ratio on window order. A positive
-- slope means the pre-sale lull is deepening as players learn the schedule.
WITH pre AS (
    SELECT w.window_id, avg(f.revenue) AS pre_avg
    FROM v_sale_window w
    JOIN v_day_facts f ON f.d BETWEEN w.window_start - 3 AND w.window_start - 1
    GROUP BY 1
),
inside AS (
    SELECT w.window_id, avg(f.revenue) AS window_avg
    FROM v_sale_window w
    JOIN v_day_facts f ON f.d BETWEEN w.window_start AND w.window_end
    GROUP BY 1
),
ratios AS (
    SELECT w.window_id, pre.pre_avg / inside.window_avg AS pre_to_window_ratio
    FROM v_sale_window w JOIN pre USING (window_id) JOIN inside USING (window_id)
)
SELECT count(*) AS windows,
       round(avg(pre_to_window_ratio), 3) AS mean_pre_to_window_ratio,
       round(regr_slope(pre_to_window_ratio, window_id), 4) AS slope_per_window,
       round(regr_r2(pre_to_window_ratio, window_id), 3) AS r_squared
FROM ratios;
