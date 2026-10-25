import numpy as np
import pandas as pd
import pytest

from eurusd_research import features as io
from eurusd_research.regimes import (
    build_liquidity_metrics, classify_regimes,
    fit_regime_thresholds, load_regime_inputs,
)


def settings():
    return {
        "lookback_seconds": 60,
        "high_quantile": 0.95,
        "low_quantile": 0.10,
        "minimum_training_rows": 3,
        "max_rows_per_scope": 1000,
        "max_frame_mib": 20,
    }


def inputs(n=240):
    times = pd.date_range(
        "2020-01-31T23:58:00Z", periods=n, freq="s"
    )
    return pd.DataFrame({
        "feature_timestamp_utc": times,
        "source_timestamp_utc": times,
        "price_valid": np.ones(n, dtype=bool),
        "size_pair_valid": np.ones(n, dtype=bool),
        "snapshot_staleness_seconds": np.zeros(n),
        "spread_pips": np.arange(n, dtype=float),
        "bid_size": np.full(n, 2.0),
        "ask_size": np.full(n, 3.0),
        "price_path_break": np.r_[True, np.zeros(n - 1, dtype=bool)],
        "unusable_size_observed_1s": np.zeros(n, dtype=bool),
        "realized_vol_60s_pips": np.ones(n),
        "snapshot_count_60s": np.full(n, 60.0),
    })


def metric_rows(n=20):
    times = pd.date_range("2020-01-01", periods=n, freq="s", tz="UTC")
    return pd.DataFrame({
        "feature_timestamp_utc": times,
        "price_valid": np.ones(n, dtype=bool),
        "regime_metrics_complete": np.ones(n, dtype=bool),
        "metric_lookback_seconds": np.full(n, 60),
        "metric_spread_mean_pips": np.arange(1, n + 1, dtype=float),
        "metric_volatility_pips": np.arange(1, n + 1, dtype=float),
        "metric_min_side_size_mean": np.arange(1, n + 1, dtype=float),
        "metric_quote_count": np.arange(1, n + 1, dtype=float),
        "bid_size": np.ones(n),
        "ask_size": np.ones(n),
    })


def test_thresholds_ignore_rows_outside_training():
    q = metric_rows()
    start = q["feature_timestamp_utc"].iloc[0]
    end = q["feature_timestamp_utc"].iloc[10]
    original = fit_regime_thresholds(q, settings(), start, end)

    changed = q.copy()
    changed.loc[10:, [
        "metric_spread_mean_pips", "metric_volatility_pips",
        "metric_min_side_size_mean", "metric_quote_count",
    ]] = 1000000.0
    assert original == fit_regime_thresholds(changed, settings(), start, end)
    assert original["training_metric_rows"] == 10


def test_precedence_missingness_zero_size_and_strict_ties():
    q = metric_rows(6)
    for name in (
        "metric_spread_mean_pips", "metric_volatility_pips",
        "metric_min_side_size_mean", "metric_quote_count",
    ):
        q[name] = 1.0
    threshold = {
        "settings": settings(),
        "cutoffs": {
            "spread_high_pips": 1.0, "volatility_high_pips": 1.0,
            "depth_low_source_units": 1.0, "activity_low_snapshots": 1.0,
        },
    }
    q.loc[0, "metric_spread_mean_pips"] = 2.0
    q.loc[0, "metric_min_side_size_mean"] = 0.5
    q.loc[1, "metric_min_side_size_mean"] = 0.5
    q.loc[2, "regime_metrics_complete"] = False
    q.loc[3, "price_valid"] = False
    q.loc[4, "bid_size"] = 0.0

    assert classify_regimes(q, threshold)["liquidity_regime"].tolist() == [
        "stressed", "thin", "unclassified", "unavailable", "thin", "normal"
    ]


def test_trailing_mean_is_hand_checkable():
    q = inputs()
    start = q["feature_timestamp_utc"].iloc[60]
    result = build_liquidity_metrics(
        q, start, start + pd.Timedelta(seconds=1), settings()
    )
    assert result.loc[0, "metric_spread_mean_pips"] == pytest.approx(30.5)
    assert result.loc[0, "metric_min_side_size_mean"] == 2.0
    assert result.loc[0, "regime_metrics_complete"]


def test_future_append_does_not_change_past_metrics():
    q = inputs()
    start = q["feature_timestamp_utc"].iloc[60]
    end = q["feature_timestamp_utc"].iloc[120]
    before = build_liquidity_metrics(q.iloc[:120], start, end, settings())
    after = build_liquidity_metrics(q, start, end, settings())
    pd.testing.assert_frame_equal(before, after)


def test_partition_consistency_across_midnight_and_breaks():
    q = inputs()
    q.loc[110, "price_valid"] = False
    q.loc[[110, 111], "price_path_break"] = True
    start = q["feature_timestamp_utc"].iloc[60]
    middle = q["feature_timestamp_utc"].iloc[120]
    end = q["feature_timestamp_utc"].iloc[180]

    whole = build_liquidity_metrics(q, start, end, settings())
    left = build_liquidity_metrics(q.iloc[:120], start, middle, settings())
    right = build_liquidity_metrics(q.iloc[60:180], middle, end, settings())
    pd.testing.assert_frame_equal(
        whole, pd.concat([left, right], ignore_index=True)
    )


def test_invalid_size_event_preserves_price_metrics():
    q = inputs()
    q.loc[70, "unusable_size_observed_1s"] = True
    start = q["feature_timestamp_utc"].iloc[80]
    result = build_liquidity_metrics(
        q, start, start + pd.Timedelta(seconds=1), settings()
    )
    assert np.isfinite(result.loc[0, "metric_spread_mean_pips"])
    assert np.isfinite(result.loc[0, "metric_volatility_pips"])
    assert pd.isna(result.loc[0, "metric_min_side_size_mean"])
    assert not result.loc[0, "regime_metrics_complete"]


def test_loader_resolves_moved_generation_and_skips_holdout(tmp_path):
    q = inputs(180)
    output = tmp_path / "data/processed/features/synthetic"
    record = io._save_frame(q, output / "part.parquet")
    first = q["feature_timestamp_utc"].iloc[0]
    last = q["feature_timestamp_utc"].iloc[-1] + pd.Timedelta(seconds=1)
    record.update({
        "path": "part.parquet",
        "start_utc": first.isoformat(), "end_utc": last.isoformat(),
    })
    state = {
        "status": "complete",
        "job_id": "synthetic",
        "output_dir": "an_old_location_that_must_not_be_used",
        "identity": {
            "start_utc": first.isoformat(), "end_utc": last.isoformat(),
        },
        "parts": [
            record,
            {
                "path": "holdout_must_not_be_opened.parquet",
                "start_utc": last.isoformat(),
                "end_utc": (last + pd.Timedelta(seconds=1)).isoformat(),
            },
        ],
    }
    start = first + pd.Timedelta(seconds=60)
    end = first + pd.Timedelta(seconds=120)
    loaded, diagnostic = load_regime_inputs(
        state, tmp_path, start, end, settings(),
        {"duckdb_memory_limit": "128MB", "duckdb_temp_limit": "1GB"},
    )
    assert len(loaded) == 120
    assert diagnostic["selected_files_verified"] == 1
