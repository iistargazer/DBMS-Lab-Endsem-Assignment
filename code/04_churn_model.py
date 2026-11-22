"""14-day churn model (deliverable 4).

Reads v_churn_panel -- the same view sql/q4_churn.sql reports on, so the model and
the SQL cannot drift -- and fits a logistic regression on features measured at the
snapshot to predict whether the player is active at all in the following 14 days.

Design decisions that matter more than the model choice:

  LABEL. Churned = zero activity in the 14 days after the snapshot, among players
  with at least one active day in the 7 days before it. Written once, in
  sql/00_views.sql, and not tuned afterwards.

  LEAKAGE. Every feature is measured on or before the snapshot; the label is
  measured only after it. The 14-day label never extends past the extract, so no
  row is scored on a partly observed outcome.

  SPLIT. Train on snapshots up to 2026-03-23, test on 2026-03-30 and later, with
  one row per player in the test set (their most recent eligible snapshot). That is
  out-of-time. It is not fully out-of-player, because a retained player appears in
  both periods; that is unavoidable -- dropping from training every player present
  in the test period would remove precisely the retained players and bias the
  model towards churners. So the leak-free check is reported alongside: AUC on the
  test players who never appear in training.

  FEATURES. Frequency (days active in the last 7 and the previous 23), depth
  (playtime and sessions per active day), tenure, ad engagement and money. Three
  candidates were dropped because they are exact functions of the ones kept
  (engagement_trend, log_sessions_7, playtime_per_session); `level_now` was dropped
  on out-of-time evidence -- it adds 0.011 AUC (0.752 vs 0.741) but flips the
  frequency coefficient positive, because given a fixed level, having needed more
  active days to reach it reads as slower progress. A coefficient that contradicts
  the univariate table in q4_churn.sql is worse than 0.011 of AUC.

  PENALTY. L2 with a fixed, un-tuned strength (1.0 on standardised features). No
  cross-validated search: the point of the exercise is an interpretable coefficient
  table and an honest out-of-time number, not the last decimal of AUC.

Outputs (all in outputs/tables/):
  q4_churn_model__model_summary.csv    sample sizes, AUC, Brier, baselines
  q4_churn_model__coefficients.csv     odds ratios per 1 SD, with 95% intervals
  q4_churn_model__calibration.csv      predicted vs observed by risk decile
  q4_churn_model__threshold_curve.csv  confusion-matrix consequences by threshold
  q4_churn_model__value_sensitivity.csv  best threshold under different offer economics
  q4_churn_model__auc_by_snapshot.csv  out-of-time stability, snapshot by snapshot

Usage:
    python code/04_churn_model.py [--reps 500] [--seed 20261122]
"""
from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

# One BLAS thread, set before numpy loads: a threaded dgemm sums in whatever order
# the scheduler gives it, and the fitted coefficients then differ in the last
# digits. Harmless numerically, but it makes `make all` produce a diff on a file
# that is supposed to be a deterministic output of committed inputs.
for _var in ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS",
             "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS"):
    os.environ.setdefault(_var, "1")

import duckdb
import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "warehouse.duckdb"
OUT = ROOT / "outputs" / "tables"

TRAIN_END = pd.Timestamp("2026-03-23")
TEST_START = pd.Timestamp("2026-03-30")
RIDGE = 1.0

# Economics of acting on the score. Assumptions, not measurements: the data has no
# marketing cost and no save rate. Stated here so the cost curve can be read and
# re-run with the numbers the business actually uses.
CONTACT_COST = 0.50
SAVE_RATE = 0.25
VALUE_PER_SAVED = 12.00

# Deliberately non-redundant. Three candidates were dropped because they are
# (near-)exact functions of features already here, which makes the coefficients
# unstable without adding information:
#   engagement_trend    = active_days_7/7 - active_days_prior_23/23   (exact)
#   playtime_per_session = playtime_7 / sessions_7                  (implied by
#                         playtime_per_active_day and sessions_per_active_day)
#   log_sessions_7      = log(active_days_7 * sessions_per_active_day)
#   level_now           -- not collinear, but see the FEATURES note above
NUMERIC = [
    "tenure_days", "active_days_7", "active_days_prior_23",
    "playtime_per_active_day", "sessions_per_active_day",
    "ad_days_7", "ads_completed_7", "is_payer",
    "log_spend_to_date", "log_spend_30", "purchases_30", "days_since_purchase",
]
CATEGORICAL = ["platform", "country_tier", "acquisition_channel"]
BINARY = {"is_payer"}


def load_panel() -> pd.DataFrame:
    if not DB.exists():
        sys.exit(f"ERROR: {DB} not found -- run code/01_build_warehouse.py first")
    con = duckdb.connect(str(DB), read_only=True)
    df = con.execute("""
        SELECT snap_d, device_id, tenure_days, platform, country_tier,
               acquisition_channel, active_days_7, sessions_7, playtime_7,
               active_days_prior_23, ad_days_7, ads_completed_7,
               purchases_30, spend_30, last_purchase_before, spend_to_date,
               active_days_next_14, churned
        FROM v_churn_panel
    """).df()
    con.close()
    return df


def engineer(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["snap_d"] = pd.to_datetime(df["snap_d"])
    df["last_purchase_before"] = pd.to_datetime(df["last_purchase_before"])
    # depth, expressed per active day, so it does not simply restate frequency
    df["playtime_per_active_day"] = df["playtime_7"] / df["active_days_7"]
    df["sessions_per_active_day"] = df["sessions_7"] / df["active_days_7"]
    df["is_payer"] = (df["spend_to_date"] > 0).astype(float)
    gap = (df["snap_d"] - df["last_purchase_before"]).dt.days
    df["days_since_purchase"] = np.where(
        df["last_purchase_before"].isna(), 120.0, np.clip(gap, 0, 120)
    )
    df["log_spend_30"] = np.log1p(df["spend_30"].clip(lower=0))
    df["log_spend_to_date"] = np.log1p(df["spend_to_date"].clip(lower=0))
    df["churned"] = df["churned"].astype(int)
    return df


def design(df: pd.DataFrame) -> pd.DataFrame:
    dummies = pd.get_dummies(
        df[CATEGORICAL].astype({c: str for c in CATEGORICAL}),
        drop_first=True, dtype=float,
    )
    X = pd.concat([df[NUMERIC].astype(float), dummies], axis=1)
    X.insert(0, "const", 1.0)
    return X


def expit(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-np.clip(x, -35, 35)))


def fit_logistic(X: np.ndarray, y: np.ndarray, lam: float = RIDGE,
                 max_iter: int = 100, tol: float = 1e-10) -> tuple[np.ndarray, np.ndarray]:
    """Newton-Raphson (IRLS) with an L2 penalty on everything but the intercept."""
    n, k = X.shape
    pen = np.full(k, lam)
    pen[0] = 0.0
    beta = np.zeros(k)

    def nll(b: np.ndarray) -> float:
        eta = X @ b
        return float(np.sum(np.logaddexp(0.0, eta) - y * eta) + 0.5 * np.sum(pen * b * b))

    cur = nll(beta)
    for _ in range(max_iter):
        p = expit(X @ beta)
        W = np.clip(p * (1 - p), 1e-9, None)
        grad = X.T @ (y - p) - pen * beta
        H = X.T @ (X * W[:, None]) + np.diag(pen)
        step = np.linalg.solve(H, grad)
        t = 1.0
        for _ in range(40):                      # step-halving keeps the penalised
            cand = beta + t * step               # likelihood monotone
            c = nll(cand)
            if c <= cur + 1e-12:
                break
            t *= 0.5
        if t < 1e-10:
            break
        beta, cur = cand, c
        if np.max(np.abs(t * step)) < tol:
            break

    p = expit(X @ beta)
    W = np.clip(p * (1 - p), 1e-9, None)
    H = X.T @ (X * W[:, None]) + np.diag(pen)
    cov = np.linalg.inv(H)
    return beta, np.sqrt(np.diag(cov))


def auc_score(y: np.ndarray, score: np.ndarray) -> float:
    """Rank-based AUC, ties averaged (equivalent to the Mann-Whitney statistic)."""
    y = np.asarray(y, dtype=float)
    ranks = pd.Series(score).rank(method="average").to_numpy()
    n1 = y.sum()
    n0 = len(y) - n1
    if n1 == 0 or n0 == 0:
        return float("nan")
    return float((ranks[y == 1].sum() - n1 * (n1 + 1) / 2) / (n1 * n0))


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--reps", type=int, default=500, help="bootstrap replicates for the AUC interval")
    ap.add_argument("--seed", type=int, default=20261122)
    args = ap.parse_args()
    t0 = time.time()

    df = engineer(load_panel())
    X_all = design(df)

    train_mask = df["snap_d"] <= TRAIN_END
    train = df[train_mask]
    Xtr = X_all[train_mask].to_numpy()
    ytr = train["churned"].to_numpy()

    # Test: out-of-time, one row per player. Their *first* eligible snapshot on or
    # after 2026-03-30, not their last: taking the last snapshot would select, for
    # every player who stopped, the week their activity ended, and the label would
    # be churned by construction (test churn would read ~2x the true rate).
    late = df[df["snap_d"] >= TEST_START].sort_values(["snap_d", "device_id"])
    test = late.groupby("device_id", as_index=False).head(1)
    Xte = X_all.loc[test.index].to_numpy()
    yte = test["churned"].to_numpy()

    train_players = set(train["device_id"])
    independent = ~test["device_id"].isin(train_players)

    # standardise on training statistics only
    cols = list(X_all.columns)
    mu = Xtr.mean(axis=0)
    sd = Xtr.std(axis=0, ddof=0)
    scale_idx = [i for i, c in enumerate(cols) if c != "const" and c not in BINARY
                 and not c.startswith(("platform_", "country_tier_", "acquisition_channel_"))]
    mu[0], sd[0] = 0.0, 1.0
    sd[sd == 0] = 1.0
    for i in range(len(cols)):
        if i not in scale_idx:
            mu[i], sd[i] = 0.0, 1.0

    Xtr_s = (Xtr - mu) / sd
    Xte_s = (Xte - mu) / sd
    # collinearity check: a condition number this side of 1e3 means no feature is
    # reproducing another one, so the coefficient table can be read feature by
    # feature rather than as a joint artefact.
    cond = float(np.linalg.cond(Xtr_s[:, 1:]))

    beta, se = fit_logistic(Xtr_s, ytr)
    p_te = expit(Xte_s @ beta)
    p_tr = expit(Xtr_s @ beta)

    auc = auc_score(yte, p_te)
    brier = float(np.mean((p_te - yte) ** 2))
    base_auc = auc_score(yte, -test["active_days_7"].to_numpy())
    auc_ind = auc_score(yte[independent], p_te[independent])

    rng = np.random.default_rng(args.seed)
    boot = np.empty(args.reps)
    for r in range(args.reps):
        idx = rng.integers(0, len(yte), len(yte))
        boot[r] = auc_score(yte[idx], p_te[idx])
    auc_lo, auc_hi = np.percentile(boot, [2.5, 97.5])

    # operating point: flag the riskiest 10% of players
    cut = np.quantile(p_te, 0.90)
    flag = p_te >= cut
    prec_top10 = float(yte[flag].mean())
    rec_top10 = float(yte[flag].sum() / yte.sum())

    break_even = CONTACT_COST / (SAVE_RATE * VALUE_PER_SAVED)

    # ---- threshold curve ---------------------------------------------------
    curve = []
    for t in [0.0] + list(np.round(np.arange(0.05, 0.96, 0.05), 2)):
        f = p_te >= t
        n_flag = int(f.sum())
        if n_flag == 0:
            continue
        prec = float(yte[f].mean())
        recall = float(yte[f].sum() / yte.sum())
        pct_flag = n_flag / len(yte)
        net = 1000.0 * (prec * SAVE_RATE * VALUE_PER_SAVED - CONTACT_COST) * pct_flag
        curve.append({
            "threshold": t,
            "players_flagged": n_flag,
            "pct_flagged": pct_flag,
            "precision": prec,
            "recall_capture": recall,
            "churners_caught_per_1000_contacted": round(prec * 1000, 1),
            "lift_vs_base": prec / float(yte.mean()),
            "net_per_1000_players": net,
            "above_break_even": prec > break_even,
        })
    curve_df = pd.DataFrame(curve)

    # ---- value sensitivity -------------------------------------------------
    sens = []
    for cost in [0.25, 0.50, 1.00, 2.00]:
        for saved_value in [1.0, 3.0, 6.0, 10.0]:
            best = None
            for t in [0.0] + list(np.round(np.arange(0.05, 0.96, 0.05), 2)):
                f = p_te >= t
                if not f.any():
                    continue
                prec = float(yte[f].mean())
                pct = f.mean()
                net = 1000.0 * (prec * saved_value - cost) * pct
                if best is None or net > best[0]:
                    best = (net, t, prec, pct)
            net, t, prec, pct = best
            sens.append({
                "contact_cost": cost,
                "value_per_saved_player": saved_value,
                "break_even_precision": cost / saved_value,
                "best_threshold": t,
                "precision_at_best": prec,
                "pct_flagged_at_best": pct,
                "net_per_1000_players": net,
                "contact_everyone_net_per_1000": 1000.0
                * (float(yte.mean()) * saved_value - cost),
                "gain_vs_contact_everyone": net - 1000.0
                * (float(yte.mean()) * saved_value - cost),
            })
    sens_df = pd.DataFrame(sens)

    # ---- coefficients ------------------------------------------------------
    coef = pd.DataFrame({
        "feature": cols,
        "beta_per_sd": beta,
        "std_error": se,
        "odds_ratio": np.exp(beta),
        "or_lo": np.exp(beta - 1.96 * se),
        "or_hi": np.exp(beta + 1.96 * se),
        "scaled_by_sd": [c in [cols[i] for i in scale_idx] for c in cols],
    })
    coef["abs_beta"] = coef["beta_per_sd"].abs()
    coef = coef.sort_values("abs_beta", ascending=False).drop(columns="abs_beta")

    # ---- calibration -------------------------------------------------------
    calib = pd.DataFrame({
        "predicted": p_te, "observed": yte,
        "decile": pd.qcut(p_te, 10, labels=False, duplicates="drop") + 1,
    }).groupby("decile").agg(
        players=("observed", "size"),
        mean_predicted=("predicted", "mean"),
        observed_churn_rate=("observed", "mean"),
    ).reset_index()
    calib["gap"] = calib["observed_churn_rate"] - calib["mean_predicted"]

    # ---- AUC by snapshot ---------------------------------------------------
    by_snap = []
    for d, g in df[df["snap_d"] >= TEST_START].groupby("snap_d"):
        idx = df.index[df["snap_d"] == d]
        ps = expit(((X_all.loc[idx].to_numpy() - mu) / sd) @ beta)
        by_snap.append({
            "snapshot": d.date(),
            "players": len(g),
            "churn_rate_pct": 100 * float(g["churned"].mean()),
            "auc": auc_score(g["churned"].to_numpy(), ps),
        })
    snap_df = pd.DataFrame(by_snap)

    # ---- summary -----------------------------------------------------------
    summary = [
        ("train_rows", len(train)),
        ("train_players", train["device_id"].nunique()),
        ("train_churn_rate_pct", 100 * float(ytr.mean())),
        ("test_players", len(test)),
        ("test_churn_rate_pct", 100 * float(yte.mean())),
        ("auc_test", auc),
        ("auc_lo", auc_lo),
        ("auc_hi", auc_hi),
        ("auc_bootstrap_reps", args.reps),
        ("brier_test", brier),
        ("auc_baseline_active_days_7_only", base_auc),
        ("independent_test_players", int(independent.sum())),
        ("auc_independent_players", auc_ind),
        ("precision_top_decile", prec_top10),
        ("recall_top_decile", rec_top10),
        ("break_even_precision", break_even),
        ("contact_cost", CONTACT_COST),
        ("save_rate", SAVE_RATE),
        ("value_per_saved_player", VALUE_PER_SAVED),
        ("net_per_1000_at_default_thresholds",
         float(curve_df.loc[(curve_df.precision > break_even), "net_per_1000_players"].max())
         if (curve_df.precision > break_even).any() else float("nan")),
        ("features", len(cols) - 1),
        ("design_condition_number", cond),
        ("ridge_penalty", RIDGE),
    ]
    summary_df = pd.DataFrame(summary, columns=["metric", "value"])

    OUT.mkdir(parents=True, exist_ok=True)
    outputs = {
        "model_summary": summary_df,
        "coefficients": coef,
        "calibration": calib,
        "threshold_curve": curve_df,
        "value_sensitivity": sens_df,
        "auc_by_snapshot": snap_df,
    }
    for name, frame in outputs.items():
        frame.round(4).to_csv(OUT / f"q4_churn_model__{name}.csv", index=False)

    print(f"train {len(train):,} rows / {train['device_id'].nunique():,} players, "
          f"churn {100 * ytr.mean():.2f}%  (design condition number {cond:.1f})")
    print(f"test  {len(test):,} players, churn {100 * yte.mean():.2f}%, "
          f"AUC {auc:.3f} [{auc_lo:.3f}, {auc_hi:.3f}]  (active-days-only AUC {base_auc:.3f})")
    print(f"independent test players {independent.sum():,}, AUC {auc_ind:.3f}")
    print(f"top decile: precision {prec_top10:.3f}, captures {100 * rec_top10:.1f}% of churners")
    print(f"\n{'feature':<38}{'OR per SD':>10}{'95% interval':>22}")
    for _, row in coef.head(8).iterrows():
        print(f"{row.feature:<38}{row.odds_ratio:>10.2f}"
              f"{f'[{row.or_lo:.2f}, {row.or_hi:.2f}]':>22}")
    print(f"\nwrote {len(outputs)} CSVs to {OUT.relative_to(ROOT)}/ ({time.time() - t0:.1f}s)")


if __name__ == "__main__":
    main()
