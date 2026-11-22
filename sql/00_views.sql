-- Shared views. Every query file builds on these so that the cleaning decisions
-- are stated once, in one place, instead of being re-implemented per question.
--
--  v_pd            player_day with activity_date (YYYYMMDD integer) exposed as a DATE
--  v_purchase_clean one row per purchase_id, with an is_refund flag
--  v_ad            ad_view with timestamps re-based from the +08:00 clock it was
--                  logged in to the UTC clock the other tables use
--  v_ad_day        one row per player per ad-day: peak count against the cap
--  v_sale_window   merged sale windows (the raw sale_flag also fires on refund rows)
--  v_sale_day      one row per calendar day that falls inside a sale window
--  v_day_facts     per-day revenue, buyers, DAU, ARPDAU and sale-window context

CREATE OR REPLACE VIEW v_pd AS
SELECT *, strptime(CAST(activity_date AS VARCHAR), '%Y%m%d')::DATE AS d
FROM player_day;

-- 122 duplicate purchase_id rows exist (same id, same device). Keep the earliest
-- observation of each id so revenue is not double counted.
CREATE OR REPLACE VIEW v_purchase_clean AS
SELECT purchase_id, device_id, event_ts, pack_name, usd_amount, currency_granted,
       sale_flag, usd_amount < 0 AS is_refund
FROM (
    SELECT *, row_number() OVER (PARTITION BY purchase_id ORDER BY event_ts, usd_amount) AS rn
    FROM purchase
) WHERE rn = 1;

-- ad_view peaks at 03:00 and has no events between 08:00 and 13:59; every other
-- event table peaks at 19:00 and has none between 00:00 and 05:59. The ad clock
-- is 8 hours ahead, which also pushes 7,732 rows onto 2026-05-01. Shift it back.
CREATE OR REPLACE VIEW v_ad AS
SELECT device_id, event_ts,
       CAST(event_ts - INTERVAL 8 HOUR AS TIMESTAMP) AS ts_utc,
       CAST(event_ts - INTERVAL 8 HOUR AS DATE) AS d_utc,
       status, placement, reward_type, reward_amount, ad_daily_count, ad_daily_cap
FROM ad_view;

CREATE OR REPLACE VIEW v_ad_day AS
SELECT device_id, d_utc,
       max(ad_daily_count) AS max_daily_count,
       max(ad_daily_cap) AS daily_cap,
       count(*) AS ad_views,
       sum(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) AS completed_views
FROM v_ad
GROUP BY device_id, d_utc;

CREATE OR REPLACE VIEW v_sale_window AS
WITH flagged AS (
    SELECT DISTINCT CAST(event_ts AS DATE) AS d
    FROM v_purchase_clean
    WHERE sale_flag = 1 AND usd_amount > 0   -- refunds carry sale_flag too
),
grouped AS (
    SELECT d, d - CAST(row_number() OVER (ORDER BY d) AS INTEGER) AS grp FROM flagged
)
SELECT row_number() OVER (ORDER BY min(d)) AS window_id, min(d) AS window_start, max(d) AS window_end
FROM grouped
GROUP BY grp;

CREATE OR REPLACE VIEW v_sale_day AS
SELECT w.window_id, CAST(g.d AS DATE) AS d
FROM v_sale_window w,
     UNNEST(generate_series(w.window_start, w.window_end, INTERVAL 1 DAY)) AS g(d);

CREATE OR REPLACE VIEW v_day_facts AS
WITH dau AS (
    SELECT d, count(*) AS dau FROM v_pd GROUP BY d
),
rev AS (
    SELECT CAST(event_ts AS DATE) AS d,
           sum(usd_amount) AS revenue,
           sum(CASE WHEN usd_amount > 0 THEN usd_amount ELSE 0 END) AS gross_revenue,
           count(*) AS txns,
           count(DISTINCT CASE WHEN usd_amount > 0 THEN device_id END) AS buyers
    FROM v_purchase_clean
    GROUP BY 1
)
SELECT dau.d,
       dau.dau,
       coalesce(rev.revenue, 0) AS revenue,
       coalesce(rev.gross_revenue, 0) AS gross_revenue,
       coalesce(rev.txns, 0) AS txns,
       coalesce(rev.buyers, 0) AS buyers,
       round(coalesce(rev.revenue, 0) / dau.dau, 4) AS arpdau,
       round(100.0 * coalesce(rev.buyers, 0) / dau.dau, 2) AS pct_dau_buying,
       s.d IS NOT NULL AS is_sale_day,
       s.window_id,
       date_diff('day', w.window_start, dau.d) AS day_of_window
FROM dau
LEFT JOIN rev USING (d)
LEFT JOIN v_sale_day s ON s.d = dau.d
LEFT JOIN v_sale_window w ON w.window_id = s.window_id;
