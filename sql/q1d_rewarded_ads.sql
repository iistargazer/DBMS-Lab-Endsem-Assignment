-- Deliverable 1(d): rewarded ad engagement.
--
-- Two definitions matter here.
--
--  * "Player-day" means one player on one ad-day. ad_view timestamps are logged
--    on a clock 8 hours ahead of the rest of the data (see sql/00_views.sql), so
--    days are re-based to UTC via v_ad before anything is grouped.
--
--  * "Hit the cap" means the player's peak ad_daily_count that day reached their
--    ad_daily_cap. Two cap values exist, so the share is always split by cap.
--    The split is not a per-player setting: about 25% of player-days run at cap 8
--    on every single day of the window (weekday-balanced, no drift), while the
--    same player flips between values across days. That is the signature of a
--    standing 75/25 player-day experiment, which is what makes comparing the two
--    groups meaningful -- and it is checked in `cap_split_over_time` below.

-- name: cap_saturation_by_cap
WITH per_player_day AS (
    SELECT device_id, d_utc, max_daily_count, daily_cap, completed_views
    FROM v_ad_day
)
SELECT daily_cap AS cap,
       count(*) AS player_days,
       sum(CASE WHEN max_daily_count >= daily_cap THEN 1 ELSE 0 END) AS player_days_at_cap,
       round(100.0 * sum(CASE WHEN max_daily_count >= daily_cap THEN 1 ELSE 0 END) / count(*), 2) AS pct_player_days_at_cap,
       sum(CASE WHEN completed_views >= daily_cap THEN 1 ELSE 0 END) AS player_days_completed_at_cap,
       round(100.0 * sum(CASE WHEN completed_views >= daily_cap THEN 1 ELSE 0 END) / count(*), 2) AS pct_player_days_completed_at_cap,
       round(avg(completed_views), 2) AS avg_completed_views_per_player_day,
       round(avg(max_daily_count), 2) AS avg_peak_count,
       round(100.0 * sum(CASE WHEN max_daily_count >= daily_cap THEN 1 ELSE 0 END)
             / nullif((SELECT count(*) FROM v_ad_day), 0), 2) AS pct_of_all_player_days
FROM per_player_day
GROUP BY 1
ORDER BY 1;

-- name: cap_saturation_all
SELECT count(*) AS player_days,
       sum(CASE WHEN max_daily_count >= daily_cap THEN 1 ELSE 0 END) AS player_days_at_cap,
       round(100.0 * sum(CASE WHEN max_daily_count >= daily_cap THEN 1 ELSE 0 END) / count(*), 2) AS pct_at_cap,
       round(avg(completed_views), 2) AS avg_completed_views,
       round(quantile_cont(completed_views, 0.50), 0) AS median_completed_views,
       round(quantile_cont(completed_views, 0.90), 0) AS p90_completed_views
FROM v_ad_day;

-- name: ad_status_mix
SELECT status, count(*) AS events,
       round(100.0 * count(*) / sum(count(*)) OVER (), 2) AS pct_of_events,
       count(DISTINCT device_id) AS players
FROM v_ad
GROUP BY 1
ORDER BY events DESC;

-- name: daily_ad_views
WITH ads AS (
    SELECT d_utc AS d,
           count(*) AS ad_events,
           sum(CASE WHEN status = 'completed' THEN 1 ELSE 0 END) AS completed,
           sum(CASE WHEN status = 'abandoned' THEN 1 ELSE 0 END) AS abandoned,
           sum(CASE WHEN status = 'started' THEN 1 ELSE 0 END) AS started_only,
           count(DISTINCT device_id) AS viewers
    FROM v_ad
    GROUP BY 1
),
dau AS (
    SELECT d, count(*) AS dau FROM v_pd GROUP BY d
)
SELECT ads.d AS day,
       ads.ad_events,
       ads.completed,
       ads.abandoned,
       ads.viewers,
       dau.dau AS daily_active_players,
       round(100.0 * ads.viewers / dau.dau, 1) AS pct_dau_watching_ads,
       round(ads.ad_events * 1.0 / nullif(ads.viewers, 0), 2) AS ad_events_per_viewer
FROM ads
JOIN dau USING (d)
WHERE ads.d BETWEEN DATE '2026-01-01' AND DATE '2026-04-30'
ORDER BY ads.d;

-- name: ad_days_missing
-- Days in the window with no ad events at all, against the activity on the days
-- either side. Two consecutive blank days is an outage, not seasonality.
WITH spine AS (
    SELECT CAST(unnest(generate_series(DATE '2026-01-01', DATE '2026-04-30', INTERVAL 1 DAY)) AS DATE) AS d
),
dau AS (SELECT d, count(*) AS dau FROM v_pd GROUP BY 1),
ads AS (SELECT d_utc AS d, count(*) AS ad_events FROM v_ad GROUP BY 1)
SELECT spine.d AS missing_day,
       (SELECT ad_events FROM ads WHERE ads.d = spine.d - 1) AS ad_events_previous_day,
       (SELECT ad_events FROM ads WHERE ads.d = spine.d + 1) AS ad_events_next_day,
       (SELECT dau FROM dau WHERE dau.d = spine.d) AS daily_active_players,
       (SELECT dau FROM dau WHERE dau.d = spine.d - 1) AS dau_previous_day
FROM spine
ANTI JOIN ads ON ads.d = spine.d
WHERE spine.d BETWEEN DATE '2026-01-01' AND DATE '2026-04-30'
ORDER BY 1;

-- name: ad_volume_anomalies
-- Day-over-day moves large enough to be an incident rather than noise. Built on
-- a complete date spine so that a day with zero ad events is detected as a
-- -100% move instead of silently disappearing from the comparison.
WITH spine AS (
    SELECT CAST(unnest(generate_series(DATE '2026-01-01', DATE '2026-04-30', INTERVAL 1 DAY)) AS DATE) AS d
),
daily AS (
    SELECT s.d, count(a.device_id) AS ad_events
    FROM spine s
    LEFT JOIN v_ad a ON a.d_utc = s.d
    GROUP BY 1
),
changes AS (
    SELECT d, ad_events, lag(ad_events) OVER (ORDER BY d) AS prev_events
    FROM daily
)
SELECT d AS day, prev_events, ad_events,
       CASE WHEN prev_events = 0 THEN NULL
            ELSE round(100.0 * (ad_events - prev_events) / prev_events, 1) END AS pct_change
FROM changes
WHERE prev_events IS NOT NULL
  AND (prev_events = 0 OR ad_events = 0
       OR abs((ad_events - prev_events) * 1.0 / prev_events) >= 0.10)
ORDER BY abs(coalesce((ad_events - prev_events) * 1.0 / nullif(prev_events, 0), 99)) DESC;

-- name: ad_reward_by_type
SELECT reward_type,
       count(*) AS events,
       round(avg(reward_amount), 2) AS avg_reward,
       round(quantile_cont(reward_amount, 0.50), 0) AS median_reward,
       min(reward_amount) AS min_reward,
       max(reward_amount) AS max_reward
FROM v_ad
WHERE status = 'completed'
GROUP BY 1
ORDER BY events DESC;

-- name: cap_assignment_stability
-- How many distinct caps does a single player ever see?
SELECT distinct_caps_seen, count(*) AS players
FROM (
    SELECT device_id, count(DISTINCT ad_daily_cap) AS distinct_caps_seen
    FROM v_ad
    GROUP BY 1
)
GROUP BY 1
ORDER BY 1;

-- name: cap_split_over_time
-- The 25/75 split is constant week to week and day of week, so the cap-5 and
-- cap-8 groups are not separated by time or by weekday.
SELECT date_trunc('week', d_utc) AS week_start,
       count(*) AS player_days,
       round(100.0 * avg(CASE WHEN daily_cap = 8 THEN 1 ELSE 0 END), 1) AS pct_player_days_cap8
FROM v_ad_day
GROUP BY 1
ORDER BY 1;

-- name: cap_split_by_weekday
SELECT dayname(d_utc) AS weekday,
       round(100.0 * avg(CASE WHEN daily_cap = 8 THEN 1 ELSE 0 END), 1) AS pct_player_days_cap8
FROM v_ad_day
GROUP BY 1
ORDER BY 2 DESC;
