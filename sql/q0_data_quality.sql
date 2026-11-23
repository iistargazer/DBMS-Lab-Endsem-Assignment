-- Data-quality register. Each block is one issue: its size, and the check that
-- justifies the handling described in the write-up.

-- name: dq01_row_counts_and_ranges
SELECT 'player_profile' AS table_name, count(*) AS rows, min(install_date::VARCHAR) AS min_key, max(install_date::VARCHAR) AS max_key
FROM player_profile
UNION ALL SELECT 'player_day', count(*), min(activity_date::VARCHAR), max(activity_date::VARCHAR) FROM player_day
UNION ALL SELECT 'purchase', count(*), min(event_ts::VARCHAR), max(event_ts::VARCHAR) FROM purchase
UNION ALL SELECT 'currency_spend', count(*), min(event_ts::VARCHAR), max(event_ts::VARCHAR) FROM currency_spend
UNION ALL SELECT 'ad_view', count(*), min(event_ts::VARCHAR), max(event_ts::VARCHAR) FROM ad_view;

-- name: dq02_duplicate_purchase_ids
SELECT count(*) AS duplicate_id_groups,
       sum(n) - count(*) AS extra_rows,
       sum(CASE WHEN n_distinct_amounts = 1 AND n_distinct_devices = 1 THEN 1 ELSE 0 END) AS identical_duplicates
FROM (
    SELECT purchase_id, count(*) AS n,
           count(DISTINCT usd_amount) AS n_distinct_amounts,
           count(DISTINCT device_id) AS n_distinct_devices
    FROM purchase
    GROUP BY 1
    HAVING count(*) > 1
);

-- name: dq03_refund_rows
-- Counted on the analysis table (v_purchase_clean), because refunds are netted out
-- of every revenue figure the deck reports and the two must agree. The raw table
-- holds 228 negative rows; two of them are duplicate purchase_ids removed by dq02.
SELECT count(*) AS refund_rows,
       round(sum(usd_amount), 2) AS refund_usd,
       sum(currency_granted) AS refund_currency,
       count(DISTINCT device_id) AS players_affected
FROM v_purchase_clean
WHERE usd_amount < 0;

-- name: dq04_purchases_before_install
SELECT count(*) AS purchase_rows_before_install,
       count(DISTINCT x.device_id) AS players_affected,
       round(sum(x.usd_amount), 2) AS usd_affected
FROM purchase x
JOIN player_profile p USING (device_id)
WHERE CAST(x.event_ts AS DATE) < p.install_date;

-- name: dq05_ad_timestamp_offset
-- Peak trading hour per table. ad_view peaks 8 hours earlier than everything
-- else, and has no events between 08:00 and 13:59 where the others have none
-- between 00:00 and 05:59.
SELECT 'purchase' AS table_name,
       (SELECT extract(hour FROM CAST(event_ts AS TIMESTAMP)) AS h
        FROM purchase GROUP BY 1 ORDER BY count(*) DESC LIMIT 1) AS peak_hour,
       (SELECT count(DISTINCT extract(hour FROM CAST(event_ts AS TIMESTAMP))) FROM purchase) AS hours_present
UNION ALL
SELECT 'currency_spend',
       (SELECT extract(hour FROM CAST(event_ts AS TIMESTAMP)) FROM currency_spend GROUP BY 1 ORDER BY count(*) DESC LIMIT 1),
       (SELECT count(DISTINCT extract(hour FROM CAST(event_ts AS TIMESTAMP))) FROM currency_spend)
UNION ALL
SELECT 'ad_view (raw)',
       (SELECT extract(hour FROM CAST(event_ts AS TIMESTAMP)) FROM ad_view GROUP BY 1 ORDER BY count(*) DESC LIMIT 1),
       (SELECT count(DISTINCT extract(hour FROM CAST(event_ts AS TIMESTAMP))) FROM ad_view)
UNION ALL
SELECT 'ad_view (after -8h)',
       (SELECT extract(hour FROM ts_utc) FROM v_ad GROUP BY 1 ORDER BY count(*) DESC LIMIT 1),
       (SELECT count(DISTINCT extract(hour FROM ts_utc)) FROM v_ad);

-- name: dq06_ad_rows_outside_window
SELECT count(*) AS rows_after_window_end,
       min(event_ts::VARCHAR) AS first_such_ts,
       max(event_ts::VARCHAR) AS last_such_ts
FROM ad_view
WHERE CAST(event_ts AS DATE) > DATE '2026-04-30';

-- name: dq07_currency_value_outliers
WITH per_type AS (
    SELECT currency_type,
           quantile_cont(currency_value, 0.50) AS median,
           quantile_cont(currency_value, 0.99) AS p99,
           quantile_cont(currency_value, 0.999) AS p999,
           max(currency_value) AS max_value,
           count(*) FILTER (WHERE currency_value = 0) AS zero_value_rows
    FROM currency_spend
    GROUP BY 1
),
outliers AS (
    SELECT s.currency_type, count(*) AS rows_above_100x_p999
    FROM currency_spend s
    JOIN per_type t USING (currency_type)
    WHERE s.currency_value > 100 * t.p999
    GROUP BY 1
)
SELECT t.currency_type,
       round(t.median, 0) AS median,
       round(t.p99, 0) AS p99,
       round(t.p999, 0) AS p999,
       t.max_value,
       t.zero_value_rows,
       coalesce(o.rows_above_100x_p999, 0) AS rows_above_100x_p999
FROM per_type t
LEFT JOIN outliers o USING (currency_type)
ORDER BY 1;

-- name: dq08_sale_flag_on_refund_days
-- sale_flag also fires on refund rows on days outside any sale window, which
-- inflates a naive count of "sale days" from 16 to 58.
SELECT count(DISTINCT CAST(event_ts AS DATE)) AS days_with_any_sale_flag,
       (SELECT count(*) FROM v_sale_day) AS days_inside_a_real_window,
       count(*) FILTER (WHERE usd_amount < 0) AS sale_flagged_refund_rows
FROM purchase
WHERE sale_flag = 1;

-- name: dq09_sale_price_equivalence
-- The promotion changes neither the price nor the currency granted.
SELECT pack_name, sale_flag, count(*) AS purchases,
       round(avg(usd_amount), 2) AS avg_usd, round(avg(currency_granted), 0) AS avg_currency_granted
FROM v_purchase_clean
WHERE usd_amount > 0
GROUP BY 1, 2
ORDER BY 1, 2;

-- name: dq10_activity_coverage
SELECT (SELECT count(*) FROM player_profile) AS profiles,
       (SELECT count(*) FROM player_profile p ANTI JOIN player_day d ON d.device_id = p.device_id) AS profiles_without_activity,
       (SELECT count(*) FROM player_day d ANTI JOIN player_profile p ON p.device_id = d.device_id) AS activity_without_profile;

-- name: dq11_device_links
SELECT count(*) FILTER (WHERE original_device_id IS NOT NULL) AS linked_devices,
       count(*) FILTER (WHERE original_device_id = device_id) AS self_links,
       count(*) FILTER (WHERE original_device_id IS NOT NULL AND original_device_id NOT IN (SELECT device_id FROM player_profile)) AS links_to_unknown_device
FROM player_profile;

-- name: dq12_country_tier_nullability
-- The brief states country_tier is null if the player never purchased. It never is.
SELECT count(*) AS rows_total,
       count(*) FILTER (WHERE country_tier IS NULL) AS null_country_tier,
       count(*) FILTER (WHERE first_purchase_date IS NULL) AS never_purchased,
       count(*) FILTER (WHERE first_purchase_date IS NULL AND country_tier IS NULL) AS never_purchased_and_null_tier
FROM player_profile;

-- name: dq13_revenue_without_activity
-- Purchases whose player has no activity row that day: they still enter ARPDAU's
-- numerator but not its denominator.
SELECT count(*) AS purchase_rows,
       count(DISTINCT x.device_id) AS players,
       round(sum(x.usd_amount), 2) AS usd,
       round(100.0 * sum(x.usd_amount) / (SELECT sum(usd_amount) FROM v_purchase_clean), 2) AS pct_of_revenue
FROM v_purchase_clean x
ANTI JOIN v_pd a ON a.device_id = x.device_id AND a.d = CAST(x.event_ts AS DATE);

-- name: dq14_first_purchase_date_consistency
SELECT count(*) AS players,
       count(*) FILTER (WHERE p.first_purchase_date IS NOT NULL) AS with_first_purchase_date,
       count(*) FILTER (WHERE p.first_purchase_date IS NOT NULL AND p.first_purchase_date <> f.first_day) AS mismatched_with_transactions,
       count(*) FILTER (WHERE p.first_purchase_date IS NULL AND f.first_day IS NOT NULL) AS paid_but_null_date
FROM player_profile p
LEFT JOIN (
    SELECT device_id, min(CAST(event_ts AS DATE)) AS first_day
    FROM purchase WHERE usd_amount > 0 GROUP BY 1
) f USING (device_id);

-- name: dq15_activity_bounds
SELECT count(*) FILTER (WHERE d < DATE '2026-01-01' OR d > DATE '2026-04-30') AS activity_outside_window,
       count(*) FILTER (WHERE date_diff('day', p.install_date, v.d) < 0) AS activity_before_install,
       count(DISTINCT v.device_id) AS active_players
FROM v_pd v JOIN player_profile p USING (device_id);

-- name: dq16_sale_day_activity_gap
-- On promotion days, 41% of transactions belong to players with no player_day
-- row that date; on ordinary days the figure is exactly zero, and none of the
-- affected players are dormant (all were active within a week). This is a
-- session-logging gap around promotions, not a lapsed-player effect, and it
-- matters because those purchases enter the ARPDAU numerator but their players
-- are missing from the DAU denominator.
WITH p AS (
    SELECT x.device_id, CAST(x.event_ts AS DATE) AS d, f.is_sale_day
    FROM v_purchase_clean x
    JOIN v_day_facts f ON f.d = CAST(x.event_ts AS DATE)
    WHERE x.usd_amount > 0
)
SELECT is_sale_day,
       count(*) AS transactions,
       count(*) FILTER (WHERE a.device_id IS NULL) AS transaction_without_activity_row,
       round(100.0 * count(*) FILTER (WHERE a.device_id IS NULL) / count(*), 2) AS pct_without_activity_row,
       count(*) FILTER (WHERE a.device_id IS NULL
                        AND EXISTS (SELECT 1 FROM v_pd z
                                    WHERE z.device_id = p.device_id AND z.d BETWEEN p.d - 7 AND p.d + 7)) AS of_which_active_within_7d
FROM p
LEFT JOIN v_pd a ON a.device_id = p.device_id AND a.d = p.d
GROUP BY 1
ORDER BY 1 DESC;
