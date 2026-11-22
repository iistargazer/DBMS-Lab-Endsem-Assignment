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
--  v_install_cohort one row per mature install with D0-D30 metrics (deliverable 3)
--  v_q3_metric_cell the same metrics per platform x tier x channel, long form
--  v_churn_panel   snapshot panel with a 14-day churn label (deliverable 4)

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


-- ===========================================================================
-- Deliverable 3: like-for-like acquisition-channel comparison
-- ===========================================================================
--
-- The two confounds this pair of views is built to neutralise:
--
--  1. CENSORING. Activity is observed from 2026-01-01 to 2026-04-30. An install
--     dated 2026-03-31 has day 30 observed (2026-04-30); an install dated
--     2026-04-01 does not. So the comparison admits only installs in
--     2026-01-01..2026-03-31, and measures every one of them over exactly the
--     same window -- 31 activity dates, D0..D30 -- counted from that player's
--     own install date. No player is credited with more follow-up than another,
--     and no cell is left blank for "not yet observed". The cost is that April
--     installs (27k of them) are excluded; q3 audits whether that changes the
--     channel mix.
--
--  2. TIER MIX. Channels do not deliver the same platform/country mix, and
--     country tier moves ARPDAU more than anything else in the data. So every
--     rate is also computed within platform x country_tier cells and then
--     re-weighted to one common mix (see sql/q3_channel_quality.sql).
--
-- Metrics are per *install*, not per returning or paying player: a channel that
-- brings players who never come back is penalised rather than quietly dropped
-- from the denominator. The 1,114 installs with no player_day row at all stay in
-- the denominator with zeros, which is what "never played" means.

CREATE OR REPLACE VIEW v_install_cohort AS
WITH inst AS (
    SELECT device_id, install_date, platform, country_tier, acquisition_channel
    FROM player_profile
    WHERE install_date BETWEEN DATE '2026-01-01' AND DATE '2026-03-31'
),
act AS (
    SELECT i.device_id,
           count(v.d) AS active_days_30,
           max(CASE WHEN date_diff('day', i.install_date, v.d) = 1  THEN 1 ELSE 0 END) AS d1,
           max(CASE WHEN date_diff('day', i.install_date, v.d) = 7  THEN 1 ELSE 0 END) AS d7,
           max(CASE WHEN date_diff('day', i.install_date, v.d) = 14 THEN 1 ELSE 0 END) AS d14,
           max(CASE WHEN date_diff('day', i.install_date, v.d) = 30 THEN 1 ELSE 0 END) AS d30,
           sum(v.session_count) AS sessions_30,
           sum(v.playtime_minutes) AS playtime_30,
           max(v.player_level) AS level_max_30
    FROM inst i
    LEFT JOIN v_pd v
           ON v.device_id = i.device_id
          AND v.d BETWEEN i.install_date AND i.install_date + 30
    GROUP BY 1
),
pay AS (
    SELECT i.device_id,
           sum(x.usd_amount) AS revenue_net_30,
           sum(CASE WHEN x.usd_amount > 0 THEN x.usd_amount ELSE 0 END) AS revenue_gross_30,
           count(x.purchase_id) FILTER (WHERE x.usd_amount > 0) AS purchases_30
    FROM inst i
    LEFT JOIN v_purchase_clean x
           ON x.device_id = i.device_id
          AND CAST(x.event_ts AS DATE) BETWEEN i.install_date AND i.install_date + 30
    GROUP BY 1
),
ads AS (
    SELECT i.device_id,
           count(a.d_utc) AS ad_days_30,
           sum(a.completed_views) AS ads_completed_30
    FROM inst i
    LEFT JOIN v_ad_day a
           ON a.device_id = i.device_id
          AND a.d_utc BETWEEN i.install_date AND i.install_date + 30
    GROUP BY 1
)
SELECT i.device_id,
       i.install_date,
       i.platform,
       i.country_tier,
       i.acquisition_channel,
       a.active_days_30,
       a.d1, a.d7, a.d14, a.d30,
       coalesce(a.sessions_30, 0) AS sessions_30,
       coalesce(a.playtime_30, 0) AS playtime_30,
       a.level_max_30,
       coalesce(p.revenue_net_30, 0) AS revenue_net_30,
       coalesce(p.revenue_gross_30, 0) AS revenue_gross_30,
       coalesce(p.purchases_30, 0) AS purchases_30,
       coalesce(p.purchases_30, 0) > 0 AS payer_30,
       coalesce(ad.ad_days_30, 0) AS ad_days_30,
       coalesce(ad.ads_completed_30, 0) AS ads_completed_30,
       CASE WHEN a.active_days_30 > 0
            THEN coalesce(ad.ads_completed_30, 0)::DOUBLE / a.active_days_30 END AS ads_per_active_day_30
FROM inst i
JOIN act a USING (device_id)
LEFT JOIN pay p USING (device_id)
LEFT JOIN ads ad USING (device_id);

-- The same metrics in long form, one row per (cell, metric), carrying the
-- variance the standardisation needs. Binary metrics use p(1-p)/n; continuous
-- ones use var/n. `est * n` is the cell total, which is all the crude pooled
-- rate needs.
CREATE OR REPLACE VIEW v_q3_metric_cell AS
WITH m AS (
    SELECT platform, country_tier, acquisition_channel AS channel, 'd1_retention' AS metric,
           count(*) AS n, avg(d1::DOUBLE) AS est,
           avg(d1::DOUBLE) * (1 - avg(d1::DOUBLE)) / count(*) AS var_est
    FROM v_install_cohort GROUP BY 1, 2, 3
    UNION ALL
    SELECT platform, country_tier, acquisition_channel, 'd7_retention',
           count(*), avg(d7::DOUBLE),
           avg(d7::DOUBLE) * (1 - avg(d7::DOUBLE)) / count(*)
    FROM v_install_cohort GROUP BY 1, 2, 3
    UNION ALL
    SELECT platform, country_tier, acquisition_channel, 'd14_retention',
           count(*), avg(d14::DOUBLE),
           avg(d14::DOUBLE) * (1 - avg(d14::DOUBLE)) / count(*)
    FROM v_install_cohort GROUP BY 1, 2, 3
    UNION ALL
    SELECT platform, country_tier, acquisition_channel, 'd30_retention',
           count(*), avg(d30::DOUBLE),
           avg(d30::DOUBLE) * (1 - avg(d30::DOUBLE)) / count(*)
    FROM v_install_cohort GROUP BY 1, 2, 3
    UNION ALL
    SELECT platform, country_tier, acquisition_channel, 'payer_conversion_30',
           count(*), avg(payer_30::DOUBLE),
           avg(payer_30::DOUBLE) * (1 - avg(payer_30::DOUBLE)) / count(*)
    FROM v_install_cohort GROUP BY 1, 2, 3
    UNION ALL
    SELECT platform, country_tier, acquisition_channel, 'revenue_per_install_30',
           count(*), avg(revenue_net_30), var_samp(revenue_net_30) / count(*)
    FROM v_install_cohort GROUP BY 1, 2, 3
    UNION ALL
    SELECT platform, country_tier, acquisition_channel, 'active_days_30',
           count(*), avg(active_days_30::DOUBLE), var_samp(active_days_30::DOUBLE) / count(*)
    FROM v_install_cohort GROUP BY 1, 2, 3
    UNION ALL
    SELECT platform, country_tier, acquisition_channel, 'ads_completed_per_install_30',
           count(*), avg(ads_completed_30::DOUBLE), var_samp(ads_completed_30::DOUBLE) / count(*)
    FROM v_install_cohort GROUP BY 1, 2, 3
)
SELECT platform, country_tier, channel, metric, n, est, var_est FROM m;


-- ===========================================================================
-- Deliverable 4: 14-day churn panel
-- ===========================================================================
--
-- One row per (player, weekly snapshot) for players active at least once in the
-- 7 days ending on the snapshot. The label is deliberately mechanical:
--
--     churned = no player_day row in the 14 days AFTER the snapshot
--
-- which is the definition in the brief ("no activity in 14 days after the
-- observation date, among players active in the prior 7 days").
--
-- Censoring controls, in the order they bite:
--
--  * The snapshot grid stops at 2026-04-13, so the label window ends no later
--    than 2026-04-27 and is fully inside the activity extract. No row is scored
--    on a partly observed label; q4 reports the count that would have been lost
--    had the grid run to 2026-04-30.
--  * The 23-day feature window before each snapshot starts no earlier than
--    2026-01-18, so `active_days_prior_23` is never truncated by the left edge
--    of the extract.
--  * Installs before 2026-01-01 are excluded. Their tenure is unobservable (it
--    would begin before the extract) and they are survivors of an unobserved
--    period, so including them would bias both tenure and churn. q4 reports how
--    many rows that removes.
--  * Because every player in the panel installed on or after 2026-01-01 and
--    purchases are recorded from 2026-01-01, `spend_to_date` is a complete
--    lifetime figure for these players -- there is no left truncation to fix.

CREATE OR REPLACE VIEW v_churn_panel AS
WITH snaps AS (
    SELECT CAST(g AS DATE) AS snap_d
    FROM generate_series(DATE '2026-02-16', DATE '2026-04-13', INTERVAL 7 DAY) AS t(g)
),
base AS (
    SELECT s.snap_d,
           p.device_id,
           p.install_date,
           p.platform,
           p.country_tier,
           p.acquisition_channel,
           date_diff('day', p.install_date, s.snap_d) AS tenure_days
    FROM snaps s
    JOIN player_profile p
      ON p.install_date >= DATE '2026-01-01'
     AND p.install_date <= s.snap_d
),
pd AS (
    SELECT b.snap_d, b.device_id,
           count(*)                FILTER (WHERE v.d BETWEEN b.snap_d - 6 AND b.snap_d) AS active_days_7,
           sum(v.session_count)    FILTER (WHERE v.d BETWEEN b.snap_d - 6 AND b.snap_d) AS sessions_7,
           sum(v.playtime_minutes) FILTER (WHERE v.d BETWEEN b.snap_d - 6 AND b.snap_d) AS playtime_7,
           max(v.player_level)     FILTER (WHERE v.d BETWEEN b.snap_d - 6 AND b.snap_d) AS level_now,
           count(*)                FILTER (WHERE v.d BETWEEN b.snap_d - 29 AND b.snap_d - 7) AS active_days_prior_23,
           count(*)                FILTER (WHERE v.d > b.snap_d AND v.d <= b.snap_d + 14) AS active_days_next_14
    FROM base b
    LEFT JOIN v_pd v
           ON v.device_id = b.device_id
          AND v.d BETWEEN b.snap_d - 29 AND b.snap_d + 14
    GROUP BY 1, 2
),
ad AS (
    SELECT b.snap_d, b.device_id,
           count(a.d_utc) FILTER (WHERE a.d_utc BETWEEN b.snap_d - 6 AND b.snap_d) AS ad_days_7,
           sum(a.completed_views) FILTER (WHERE a.d_utc BETWEEN b.snap_d - 6 AND b.snap_d) AS ads_completed_7
    FROM base b
    LEFT JOIN v_ad_day a
           ON a.device_id = b.device_id
          AND a.d_utc BETWEEN b.snap_d - 6 AND b.snap_d
    GROUP BY 1, 2
),
pay AS (
    SELECT b.snap_d, b.device_id,
           count(x.purchase_id) FILTER (
               WHERE CAST(x.event_ts AS DATE) BETWEEN b.snap_d - 29 AND b.snap_d
                 AND x.usd_amount > 0) AS purchases_30,
           sum(CASE WHEN x.usd_amount > 0 THEN x.usd_amount ELSE 0 END) FILTER (
               WHERE CAST(x.event_ts AS DATE) BETWEEN b.snap_d - 29 AND b.snap_d) AS spend_30,
           max(CAST(x.event_ts AS DATE)) FILTER (
               WHERE CAST(x.event_ts AS DATE) <= b.snap_d) AS last_purchase_before,
           sum(CASE WHEN x.usd_amount > 0 THEN x.usd_amount ELSE 0 END) AS spend_to_date
    FROM base b
    LEFT JOIN v_purchase_clean x
           ON x.device_id = b.device_id
          AND CAST(x.event_ts AS DATE) <= b.snap_d
    GROUP BY 1, 2
)
SELECT b.snap_d, b.device_id, b.install_date, b.platform, b.country_tier,
       b.acquisition_channel, b.tenure_days,
       p.active_days_7,
       coalesce(p.sessions_7, 0) AS sessions_7,
       coalesce(p.playtime_7, 0) AS playtime_7,
       p.level_now,
       p.active_days_prior_23,
       coalesce(a.ad_days_7, 0) AS ad_days_7,
       coalesce(a.ads_completed_7, 0) AS ads_completed_7,
       coalesce(y.purchases_30, 0) AS purchases_30,
       coalesce(y.spend_30, 0) AS spend_30,
       y.last_purchase_before,
       coalesce(y.spend_to_date, 0) AS spend_to_date,
       p.active_days_next_14,
       p.active_days_next_14 = 0 AS churned
FROM base b
JOIN pd p ON p.snap_d = b.snap_d AND p.device_id = b.device_id
LEFT JOIN ad a ON a.snap_d = b.snap_d AND a.device_id = b.device_id
LEFT JOIN pay y ON y.snap_d = b.snap_d AND y.device_id = b.device_id
WHERE p.active_days_7 >= 1;
