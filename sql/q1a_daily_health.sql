-- Deliverable 1(a): daily health, split by platform.
--
-- Denominator discipline:
--   * DAU is the count of players with a player_day row that date.
--   * The purchase share uses purchasers who were *also active that day*. A
--     numerator taken from every purchaser that date can exceed the denominator,
--     because some purchases belong to players with no activity row that day
--     (late/backfilled sessions). The `revenue_from_inactive` column measures
--     how much revenue those players contribute, since it still lands in ARPDAU.
--   * Revenue is net of refunds (228 negative rows); gross is shown alongside.

-- name: daily_health_by_platform
WITH dau AS (
    SELECT v.d, p.platform, count(*) AS dau
    FROM v_pd v
    JOIN player_profile p USING (device_id)
    GROUP BY 1, 2
),
rev AS (
    SELECT CAST(x.event_ts AS DATE) AS d, p.platform,
           sum(x.usd_amount) AS revenue_net,
           sum(CASE WHEN x.usd_amount > 0 THEN x.usd_amount ELSE 0 END) AS revenue_gross,
           count(*) AS transactions
    FROM v_purchase_clean x
    JOIN player_profile p USING (device_id)
    GROUP BY 1, 2
),
purchasers AS (
    SELECT CAST(x.event_ts AS DATE) AS d, p.platform,
           count(DISTINCT x.device_id) AS paying_players,
           count(DISTINCT CASE WHEN a.device_id IS NOT NULL THEN x.device_id END) AS paying_players_active,
           sum(CASE WHEN a.device_id IS NULL THEN x.usd_amount ELSE 0 END) AS revenue_from_inactive
    FROM v_purchase_clean x
    JOIN player_profile p USING (device_id)
    LEFT JOIN v_pd a ON a.device_id = x.device_id AND a.d = CAST(x.event_ts AS DATE)
    GROUP BY 1, 2
)
SELECT dau.d AS day,
       dau.platform,
       dau.dau AS daily_active_players,
       coalesce(pu.paying_players_active, 0) AS paying_players,
       round(100.0 * coalesce(pu.paying_players_active, 0) / dau.dau, 2) AS pct_dau_purchasing,
       round(coalesce(rv.revenue_gross, 0), 2) AS revenue_gross,
       round(coalesce(rv.revenue_net, 0), 2) AS revenue_net,
       coalesce(rv.transactions, 0) AS transactions,
       round(coalesce(rv.revenue_net, 0) / dau.dau, 4) AS arpdau_net,
       round(coalesce(pu.revenue_from_inactive, 0), 2) AS revenue_from_inactive
FROM dau
LEFT JOIN rev rv USING (d, platform)
LEFT JOIN purchasers pu USING (d, platform)
ORDER BY day, platform;

-- name: daily_health_all_platforms
WITH dau AS (
    SELECT d, count(*) AS dau FROM v_pd GROUP BY d
),
rev AS (
    SELECT CAST(event_ts AS DATE) AS d, sum(usd_amount) AS revenue_net,
           sum(CASE WHEN usd_amount > 0 THEN usd_amount ELSE 0 END) AS revenue_gross,
           count(*) AS transactions
    FROM v_purchase_clean GROUP BY 1
)
SELECT dau.d AS day, dau.dau AS daily_active_players,
       round(coalesce(rev.revenue_gross, 0), 2) AS revenue_gross,
       round(coalesce(rev.revenue_net, 0), 2) AS revenue_net,
       coalesce(rev.transactions, 0) AS transactions,
       round(coalesce(rev.revenue_net, 0) / dau.dau, 4) AS arpdau_net
FROM dau
LEFT JOIN rev USING (d)
ORDER BY day;

-- name: daily_health_period_summary
WITH per_day AS (
    SELECT v.d, p.platform, count(*) AS dau
    FROM v_pd v JOIN player_profile p USING (device_id)
    GROUP BY 1, 2
),
rev AS (
    SELECT CAST(x.event_ts AS DATE) AS d, p.platform, sum(x.usd_amount) AS revenue_net
    FROM v_purchase_clean x JOIN player_profile p USING (device_id)
    GROUP BY 1, 2
),
pay AS (
    SELECT CAST(x.event_ts AS DATE) AS d, p.platform,
           count(DISTINCT CASE WHEN a.device_id IS NOT NULL THEN x.device_id END) AS buyers
    FROM v_purchase_clean x
    JOIN player_profile p USING (device_id)
    LEFT JOIN v_pd a ON a.device_id = x.device_id AND a.d = CAST(x.event_ts AS DATE)
    GROUP BY 1, 2
),
joined AS (
    SELECT per_day.platform, per_day.d, per_day.dau,
           coalesce(rev.revenue_net, 0) AS revenue_net,
           coalesce(pay.buyers, 0) AS buyers
    FROM per_day
    LEFT JOIN rev USING (d, platform)
    LEFT JOIN pay USING (d, platform)
)
SELECT platform,
       round(avg(dau), 0) AS avg_dau,
       round(sum(revenue_net), 2) AS revenue_net,
       round(sum(revenue_net) / sum(dau), 4) AS arpdau_net,
       round(100.0 * sum(buyers) / sum(dau), 2) AS pct_dau_purchasing
FROM joined
GROUP BY platform
ORDER BY platform;
