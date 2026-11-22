"""90-day value of an install, predicted from the first seven days (deliverable 4b, optional).

The brief's warning is the whole exercise: "think carefully about which players you
are able to label at all". A player needs 90 observed days to have a target, and the
extract ends 2026-04-30, so only installs up to 2026-01-31 can be labelled -- 9,809
of 80,295 profiles. Worse, that cohort is January, when paid_video was 15% of
installs; the installs UA is buying now are 69% paid_video. So this is a demonstration
of method on the only cohort that can carry a 90-day label, not a bid rule for today's
traffic. `sql/q4b_ltv.sql` quantifies that gap rather than hiding it.

Model shape. 78% of installs are worth exactly $0 over 90 days, so a single regression
on revenue spends its effort fitting zeros and compresses the top: it ranks *worse*
than the free heuristic "sort by what they spent in the first 7 days" (Spearman 0.35
vs 0.47, see the results table). The fix is the standard one for zero-inflated spend --
two parts:

    score = P(pays within 90 days)  x  E[90-day revenue | pays]

a logistic for the first factor and a ridge regression on log1p revenue fitted on the
payers only for the second. That is both simpler to explain to a UA team ("likelihood
they convert, times what a converter is worth") and better at the only thing that
matters here, which is putting the expensive installs at the top.

Evaluation follows the brief: ranking, not average error. Spearman correlation, and how
much of the cohort's revenue the top decile of scores actually contains. Three
baselines are reported, including the free day-7-spend heuristic, which is hard to beat.
A transfer check scores a *later* cohort (February) against a 30-day outcome and
compares it with the same 30-day relationship inside January: levels do not transfer,
ranks do.

The log target makes exp(prediction) a biased estimate of the mean, so the dollar
guidance in the decile table comes from realised revenue per band, not from the model's
predicted level.

Outputs (outputs/tables/):
  q4b_ltv__model_summary.csv   sample sizes, ranking metrics, baselines, transfer check
  q4b_ltv__decile_lift.csv     realised revenue per predicted decile, and bid headroom
  q4b_ltv__coefficients.csv    both components, per standard deviation

Usage:
    python code/05_ltv_model.py [--lambda 1.0]
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# One BLAS thread, set before numpy loads. A threaded dgemm sums in whatever order
# the scheduler gives it, which perturbs the ridge solution in the last digits;
# that is harmless for a coefficient and not harmless here, because the top-decile
# cut is a hard boundary through a heavy-tailed revenue column -- two players
# swapping places at that boundary moved the captured share by points between two
# runs of identical code. One thread, so a rebuild reproduces the deck.
for _var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
             "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
    os.environ.setdefault(_var, "1")

import duckdb
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "warehouse.duckdb"
OUT = ROOT / "outputs" / "tables"

TRAIN_END = pd.Timestamp("2026-01-20")
TEST_START = pd.Timestamp("2026-01-21")
RIDGE = 1.0

NUMERIC = [
    "active_days_7", "sessions_7", "log_playtime_7", "level_max_7",
    "sessions_per_active_day", "ad_days_7", "ads_completed_7",
    "purchases_7", "log_revenue_7",
]
CATEGORICAL = ["platform", "country_tier", "acquisition_channel"]
DUMMY_PREFIX = ("platform_", "country_tier_", "acquisition_channel_")


def cohort_sql(start: str, end: str, horizon_days: int) -> str:
    """Features from D0-D6, outcome revenue over D0..D{horizon-1}.

    Mirrors v_ltv_cohort; the caller asserts the two agree on the January cohort so
    the transfer check cannot silently drift from the view the SQL reports on.
    """
    last = horizon_days - 1
    return f"""
        WITH inst AS (
            SELECT device_id, install_date, platform, country_tier, acquisition_channel
            FROM player_profile
            WHERE install_date BETWEEN DATE '{start}' AND DATE '{end}'
        ),
        act AS (
            SELECT i.device_id,
                   count(v.d) FILTER (WHERE v.d <= i.install_date + 6) AS active_days_7,
                   coalesce(sum(v.session_count) FILTER (WHERE v.d <= i.install_date + 6), 0) AS sessions_7,
                   coalesce(sum(v.playtime_minutes) FILTER (WHERE v.d <= i.install_date + 6), 0) AS playtime_7,
                   max(v.player_level) FILTER (WHERE v.d <= i.install_date + 6) AS level_max_7
            FROM inst i
            LEFT JOIN v_pd v ON v.device_id = i.device_id
                            AND v.d BETWEEN i.install_date AND i.install_date + {last}
            GROUP BY 1
        ),
        pay AS (
            SELECT i.device_id,
                   count(x.purchase_id) FILTER (WHERE CAST(x.event_ts AS DATE) <= i.install_date + 6) AS purchases_7,
                   coalesce(sum(x.usd_amount) FILTER (WHERE CAST(x.event_ts AS DATE) <= i.install_date + 6), 0) AS revenue_7,
                   coalesce(sum(x.usd_amount), 0) AS revenue_outcome
            FROM inst i
            LEFT JOIN v_purchase_clean x ON x.device_id = i.device_id
                                        AND CAST(x.event_ts AS DATE) BETWEEN i.install_date
                                                                         AND i.install_date + {last}
            GROUP BY 1
        ),
        ads AS (
            SELECT i.device_id,
                   count(a.d_utc) AS ad_days_7,
                   coalesce(sum(a.completed_views), 0) AS ads_completed_7
            FROM inst i
            LEFT JOIN v_ad_day a ON a.device_id = i.device_id
                                AND a.d_utc BETWEEN i.install_date AND i.install_date + 6
            GROUP BY 1
        )
        SELECT i.device_id, i.install_date, i.platform, i.country_tier, i.acquisition_channel,
               coalesce(act.active_days_7, 0) AS active_days_7,
               coalesce(act.sessions_7, 0) AS sessions_7,
               coalesce(act.playtime_7, 0) AS playtime_7,
               act.level_max_7,
               coalesce(ad.ad_days_7, 0) AS ad_days_7,
               coalesce(ad.ads_completed_7, 0) AS ads_completed_7,
               coalesce(p.purchases_7, 0) AS purchases_7,
               coalesce(p.revenue_7, 0) AS revenue_7,
               coalesce(p.revenue_outcome, 0) AS revenue_outcome
        FROM inst i
        LEFT JOIN act ON act.device_id = i.device_id
        LEFT JOIN pay p ON p.device_id = i.device_id
        LEFT JOIN ads ad ON ad.device_id = i.device_id
        ORDER BY i.device_id
    """


def design(df: pd.DataFrame) -> pd.DataFrame:
    d = df.copy()
    d["level_max_7"] = d["level_max_7"].fillna(0.0)
    d["sessions_per_active_day"] = (
        d["sessions_7"] / d["active_days_7"].replace(0, np.nan)
    ).fillna(0.0)
    d["log_playtime_7"] = np.log1p(d["playtime_7"].clip(lower=0))
    d["log_revenue_7"] = np.log1p(d["revenue_7"].clip(lower=0))
    dummies = pd.get_dummies(
        d[CATEGORICAL].astype({c: str for c in CATEGORICAL}), drop_first=True, dtype=float
    )
    X = pd.concat([d[NUMERIC].astype(float), dummies], axis=1)
    X.insert(0, "const", 1.0)
    return X


def fit_ridge_ols(X: np.ndarray, y: np.ndarray, lam: float) -> tuple[np.ndarray, np.ndarray]:
    k = X.shape[1]
    pen = np.full(k, lam)
    pen[0] = 0.0
    A = X.T @ X + np.diag(pen)
    beta = np.linalg.solve(A, X.T @ y)
    resid = y - X @ beta
    sigma2 = float(resid @ resid) / max(1, len(y) - k)
    se = np.sqrt(np.diag(np.linalg.inv(A)) * sigma2)
    return beta, se


def expit(z: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(z, -35, 35)))


def fit_logistic(X: np.ndarray, y: np.ndarray, lam: float = RIDGE,
                 max_iter: int = 100) -> tuple[np.ndarray, np.ndarray]:
    """IRLS with an L2 penalty on everything but the intercept."""
    k = X.shape[1]
    pen = np.full(k, lam)
    pen[0] = 0.0
    beta = np.zeros(k)

    def nll(b: np.ndarray) -> float:
        eta = X @ b
        return float(np.sum(np.logaddexp(0.0, eta) - y * eta) + 0.5 * np.sum(pen * b * b))

    cur = nll(beta)
    for _ in range(max_iter):
        p = expit(X @ beta)
        w = np.clip(p * (1 - p), 1e-9, None)
        grad = X.T @ (y - p) - pen * beta
        H = X.T @ (X * w[:, None]) + np.diag(pen)
        step = np.linalg.solve(H, grad)
        t = 1.0
        for _ in range(40):
            cand = beta + t * step
            c = nll(cand)
            if c <= cur + 1e-12:
                break
            t *= 0.5
        if t < 1e-10:
            break
        beta, cur = cand, c
        if np.max(np.abs(t * step)) < 1e-10:
            break

    p = expit(X @ beta)
    w = np.clip(p * (1 - p), 1e-9, None)
    H = X.T @ (X * w[:, None]) + np.diag(pen)
    return beta, np.sqrt(np.diag(np.linalg.inv(H)))


def standardise(Xtr: np.ndarray, other: list[np.ndarray], cols: list[str]):
    mu = np.zeros(len(cols))
    sd = np.ones(len(cols))
    for i, c in enumerate(cols):
        if c != "const" and not c.startswith(DUMMY_PREFIX):
            mu[i] = Xtr[:, i].mean()
            s = Xtr[:, i].std()
            sd[i] = s if s > 0 else 1.0
    return [(X - mu) / sd for X in [Xtr, *other]], mu, sd


def spearman(x, y) -> float:
    """Rank correlation as Pearson on average ranks -- pandas' spearman path wants
    scipy, and this repo keeps its dependencies to duckdb/pandas/numpy."""
    rx = pd.Series(np.asarray(x, dtype=float)).rank().to_numpy()
    ry = pd.Series(np.asarray(y, dtype=float)).rank().to_numpy()
    if np.std(rx) == 0 or np.std(ry) == 0:
        return float("nan")
    return float(np.corrcoef(rx, ry)[0, 1])


def capture_at(y: np.ndarray, score: np.ndarray, frac: float) -> float:
    """Share of total revenue held by the top `frac` of scores.

    The sort is stable and the cohort arrives in device_id order, because 78% of
    these installs score exactly zero under the day-7 spending baseline: which
    zeros fill the last few seats of the cut is arbitrary, so it has to at least
    be arbitrary in a fixed way, or the same build produces a different number.
    """
    k = max(1, int(round(frac * len(y))))
    idx = np.argsort(-np.asarray(score, dtype=float), kind="stable")[:k]
    total = y.sum()
    return float(y[idx].sum() / total) if total > 0 else float("nan")


def top_decile(y: np.ndarray, score: np.ndarray) -> tuple[float, float]:
    """(share of total revenue in the top 10% of scores, mean revenue there)."""
    share = capture_at(y, score, 0.10)
    k = max(1, int(round(0.10 * len(y))))
    idx = np.argsort(-np.asarray(score, dtype=float))[:k]
    return share, float(y[idx].mean())


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--lambda", dest="lam", type=float, default=RIDGE)
    args = ap.parse_args()

    if not DB.exists():
        sys.exit(f"ERROR: {DB} not found -- run code/01_build_warehouse.py first")
    con = duckdb.connect(str(DB), read_only=True)
    # One thread for the same reason as the BLAS pin above: a parallel float sum
    # adds its partials in a scheduler-dependent order, which is enough to move the
    # last digit of a feature and cascade into the fit and the ranking metrics.
    con.execute("PRAGMA threads=1")

    df = con.execute(cohort_sql("2026-01-01", "2026-01-31", 90)).df()
    # Belt and braces on the ORDER BY inside cohort_sql: Spearman sums tied ranks,
    # so row order decides the last printed digit.
    df = df.sort_values("device_id").reset_index(drop=True)
    view = con.execute("SELECT device_id, revenue_7, revenue_90 FROM v_ltv_cohort").df()
    chk = df.merge(view, on="device_id", suffixes=("", "_view"))
    assert len(chk) == len(df), "cohort row count drift"
    assert abs(chk["revenue_outcome"] - chk["revenue_90"]).max() < 1e-6, "label drift"
    assert abs(chk["revenue_7"] - chk["revenue_7_view"]).max() < 1e-6, "feature drift"

    df["install_date"] = pd.to_datetime(df["install_date"])
    df["y_log"] = np.log1p(df["revenue_outcome"].clip(lower=0))
    df["y_payer"] = (df["revenue_outcome"] > 0).astype(float)

    train = df[df["install_date"] <= TRAIN_END]
    test = df[df["install_date"] >= TEST_START]

    Xtr_df, Xte_df = design(train), design(test)
    cols = list(Xtr_df.columns)
    (Xtr_s, Xte_s), mu, sd = standardise(
        Xtr_df.to_numpy(dtype=float), [Xte_df.to_numpy(dtype=float)], cols
    )

    y_tr_log = train["y_log"].to_numpy()
    y_te = test["revenue_outcome"].to_numpy()

    # one-part benchmark: a single regression on log1p revenue, all installs
    b_one, _ = fit_ridge_ols(Xtr_s, y_tr_log, args.lam)
    score_one = Xte_s @ b_one

    # two-part: P(pays at all) x E[revenue | pays]
    b_payer, se_payer = fit_logistic(Xtr_s, train["y_payer"].to_numpy(), args.lam)
    p_te = expit(Xte_s @ b_payer)

    payers = (train["revenue_outcome"] > 0).to_numpy()
    b_level, se_level = fit_ridge_ols(Xtr_s[payers], y_tr_log[payers], args.lam)
    level_te = np.clip(np.expm1(Xte_s @ b_level), 0, None)
    score_two = p_te * level_te

    # in-sample R2 of the level model, for the record (not the headline)
    resid = y_tr_log[payers] - Xtr_s[payers] @ b_level
    r2_level = 1 - float(resid @ resid) / float(
        np.sum((y_tr_log[payers] - y_tr_log[payers].mean()) ** 2)
    )

    # ---- ranking metrics vs baselines -------------------------------------
    rankers = {
        "two_part_model (recommended)": score_two,
        "one_part_log_regression": score_one,
        "baseline: day-7 revenue": test["revenue_7"].to_numpy(),
        "baseline: day-7 active days": test["active_days_7"].to_numpy(),
        "baseline: day-7 ad views": test["ads_completed_7"].to_numpy(),
    }
    rows = []
    for name, s in rankers.items():
        share, mean_top = top_decile(y_te, np.asarray(s, dtype=float))
        rows.append({
            "ranker": name,
            "spearman_vs_90d_revenue": spearman(s, y_te),
            "top_decile_share_of_revenue": share,
            "top_decile_mean_revenue_90": mean_top,
            "top_decile_lift_vs_mean": mean_top / y_te.mean(),
        })
    rank_df = pd.DataFrame(rows)
    rank_df.round(4).to_csv(OUT / "q4b_ltv__ranking_comparison.csv", index=False)

    # Spearman over the whole cohort is dominated by the 78% of installs worth
    # nothing, where a model can only add noise to ties. Concentration at the top is
    # the metric a bid rule actually needs, so both are reported side by side.
    fracs = [0.05, 0.10, 0.20, 0.30, 0.50]
    capture = {"top_share_of_installs": fracs}
    for name, s in rankers.items():
        capture[name] = [capture_at(y_te, np.asarray(s, dtype=float), f) for f in fracs]
    capture_df = pd.DataFrame(capture)
    capture_df.round(4).to_csv(OUT / "q4b_ltv__capture_curve.csv", index=False)

    # ---- transfer check: February cohort, 30-day outcome -------------------
    feb = con.execute(cohort_sql("2026-02-01", "2026-02-28", 30)).df()
    Xfeb_s = (design(feb).to_numpy(dtype=float) - mu) / sd
    transfer_feb = spearman(expit(Xfeb_s @ b_payer) * np.clip(np.expm1(Xfeb_s @ b_level), 0, None),
                            feb["revenue_outcome"].to_numpy())

    jan30 = con.execute(cohort_sql("2026-01-01", "2026-01-31", 30)).df()
    Xjan30_s = (design(jan30).to_numpy(dtype=float) - mu) / sd
    transfer_jan = spearman(expit(Xjan30_s @ b_payer) * np.clip(np.expm1(Xjan30_s @ b_level), 0, None),
                            jan30["revenue_outcome"].to_numpy())

    # ---- decile table on the recommended score ----------------------------
    dec = pd.DataFrame({"score": score_two, "actual": y_te})
    dec["decile"] = pd.qcut(dec["score"].rank(method="first"), 10, labels=False) + 1
    lift = dec.groupby("decile").agg(
        installs=("actual", "size"),
        mean_revenue_90=("actual", "mean"),
        median_revenue_90=("actual", "median"),
        revenue_90=("actual", "sum"),
    ).reset_index()
    lift["share_of_revenue_pct"] = 100 * lift["revenue_90"] / lift["revenue_90"].sum()
    lift["share_in_decile_or_higher_pct"] = (
        lift.sort_values("decile", ascending=False)["share_of_revenue_pct"].cumsum().values[::-1]
    )
    # what UA can afford to pay per install in a band: realised revenue is the
    # ceiling, halved for a 50% margin. Realised, not predicted -- see the docstring.
    lift["break_even_bid_per_install"] = lift["mean_revenue_90"]
    lift["bid_at_50pct_margin"] = 0.5 * lift["mean_revenue_90"]
    lift = lift.rename(columns={"decile": "predicted_decile"})

    # ---- summary ----------------------------------------------------------
    two = rank_df.iloc[0]
    one = rank_df.iloc[1]
    base7 = rank_df.iloc[2]
    summary = [
        ("labelled_cohort_installs", len(df)),
        ("cohort_pct_of_profiles", 100 * len(df) / 80295),
        ("mean_revenue_90_in_cohort", float(df["revenue_outcome"].mean())),
        ("pct_installs_with_zero_revenue", 100 * float((df["revenue_outcome"] == 0).mean())),
        ("train_installs", len(train)),
        ("train_payers", int(payers.sum())),
        ("test_installs", len(test)),
        ("test_mean_revenue_90", float(y_te.mean())),
        ("spearman_two_part", float(two["spearman_vs_90d_revenue"])),
        ("spearman_one_part", float(one["spearman_vs_90d_revenue"])),
        ("spearman_baseline_day7_revenue", float(base7["spearman_vs_90d_revenue"])),
        ("two_part_top_decile_revenue_share", float(two["top_decile_share_of_revenue"])),
        ("one_part_top_decile_revenue_share", float(one["top_decile_share_of_revenue"])),
        ("day7_baseline_top_decile_revenue_share", float(base7["top_decile_share_of_revenue"])),
        ("two_part_top_decile_mean_revenue_90", float(two["top_decile_mean_revenue_90"])),
        ("two_part_top_decile_lift", float(two["top_decile_lift_vs_mean"])),
        ("level_model_r2_on_log_train_payers", r2_level),
        ("mean_predicted_dollars_naive", float(np.mean(score_two))),
        ("mean_actual_dollars_test", float(y_te.mean())),
        ("transfer_spearman_jan_30d", transfer_jan),
        ("transfer_spearman_feb_30d", transfer_feb),
        ("feb_cohort_installs", len(feb)),
        ("ridge_lambda", args.lam),
    ]
    summary_df = pd.DataFrame(summary, columns=["metric", "value"])

    coef = pd.concat([
        pd.DataFrame({
            "component": "payer probability (log-odds per SD)",
            "feature": cols, "coefficient": b_payer, "std_error": se_payer,
        }),
        pd.DataFrame({
            "component": "spend level | payer (log points per SD)",
            "feature": cols, "coefficient": b_level, "std_error": se_level,
        }),
    ], ignore_index=True)
    coef["abs"] = coef["coefficient"].abs()
    coef = coef.sort_values(["component", "abs"], ascending=[True, False]).drop(columns="abs")

    OUT.mkdir(parents=True, exist_ok=True)
    summary_df.round(4).to_csv(OUT / "q4b_ltv__model_summary.csv", index=False)
    lift.round(4).to_csv(OUT / "q4b_ltv__decile_lift.csv", index=False)
    coef.round(4).to_csv(OUT / "q4b_ltv__coefficients.csv", index=False)

    print(f"cohort {len(df):,} installs ({100 * len(df) / 80295:.1f}% of profiles), "
          f"mean 90-day revenue ${df['revenue_outcome'].mean():.2f}, "
          f"{100 * float((df['revenue_outcome'] == 0).mean()):.1f}% worth nothing")
    print(f"train {len(train):,} (to Jan 20) / test {len(test):,} (Jan 21-31)\n")
    print(rank_df.round(3).to_string(index=False))
    print("\nshare of 90-day revenue captured at the top of each ranking:")
    print(capture_df.round(3).to_string(index=False))
    print(f"\ntransfer: spearman vs 30-day revenue, Jan {transfer_jan:.3f} vs Feb {transfer_feb:.3f}")
    print(f"wrote 5 CSVs to {OUT.relative_to(ROOT)}/")


if __name__ == "__main__":
    main()
