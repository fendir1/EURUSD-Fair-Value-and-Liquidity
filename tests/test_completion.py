import numpy as np
import pandas as pd
from sklearn.linear_model import Ridge
from sklearn.preprocessing import StandardScaler

from eurusd_research.completion_models import (
    new_statistics, update_statistics, finish_statistics, predict, transform,
)
from eurusd_research.completion_data import assemble_block, eligible


def test_batched_ridge_matches_sklearn():
    rng = np.random.default_rng(42)
    x = rng.normal(size=(500, 4))
    x[::7, 0] = np.nan
    x[:, 3] = 2.0
    y = rng.normal(size=500)
    fill = np.nanmean(x, axis=0)
    z = transform(x, fill)
    state = new_statistics(z.shape[1])
    for left in range(0, len(y), 37):
        update_statistics(state, z[left:left + 37], y[left:left + 37])
    model = finish_statistics(state, fill, list(range(8)), list("abcd"), 10.0)
    scaler = StandardScaler()
    reference = Ridge(alpha=10.0, solver="cholesky").fit(
        scaler.fit_transform(z), y
    )
    actual = predict(model, pd.DataFrame(x, columns=list("abcd")))
    np.testing.assert_allclose(
        actual, reference.predict(scaler.transform(z)), atol=1e-9, rtol=1e-8
    )


def policy():
    return {
        "feature_settings": {
            "pip_size": .0001, "max_staleness_seconds": 30,
            "substantial_gap_seconds": 30,
            "return_windows_seconds": [1, 5, 30, 60],
            "volatility_windows_seconds": [60, 300],
            "activity_windows_seconds": [1, 5, 30, 60, 300],
            "change_windows_seconds": [1, 5],
            "observed_price_change_window_seconds": 300,
            "wide_spread_review_pips": 20,
            "london_session_start_hour": 8, "london_session_end_hour": 17,
            "new_york_session_start_hour": 8, "new_york_session_end_hour": 17,
            "max_grid_rows_per_block": 100000,
        },
        "regime_settings": {"lookback_seconds": 60},
        "horizons_seconds": [1, 5, 30, 60],
        "maximum_observed_frame_mib": 512,
        "model_settings": {"required_current_size_features": ["bid_size", "ask_size"]},
        "excluded_signal_and_label_intervals": [],
    }


def quotes():
    times = pd.date_range("2020-01-01", periods=1200, freq="s", tz="UTC")
    return pd.DataFrame({
        "timestamp_utc": times,
        "source_row": np.arange(1, len(times) + 1),
        "source_file": "synthetic.csv", "source_sha256": "synthetic",
        "bid": 1.1 + np.sin(np.arange(len(times)) / 17) * .0001,
        "ask": 1.1002 + np.sin(np.arange(len(times)) / 17) * .0001,
        "bid_size": 2.0, "ask_size": 3.0, "source_gap_seconds": 1.0,
        "flag_missing_size": False, "flag_invalid_size": False,
        "flag_zero_size": False, "flag_negative_size": False,
    })


def test_joint_features_labels_partition_consistency():
    q, p = quotes(), policy()
    start = q["timestamp_utc"].iloc[600]
    middle, end = start + pd.Timedelta(seconds=60), start + pd.Timedelta(seconds=120)
    whole, _ = assemble_block(q, start, end, p, q["timestamp_utc"].iloc[0])
    first, _ = assemble_block(q, start, middle, p, q["timestamp_utc"].iloc[0])
    second, _ = assemble_block(q, middle, end, p, q["timestamp_utc"].iloc[0])
    pd.testing.assert_frame_equal(
        whole, pd.concat([first, second], ignore_index=True),
        check_exact=False, atol=1e-9, rtol=1e-9,
    )


def test_future_append_does_not_change_features():
    q, p = quotes(), policy()
    start = q["timestamp_utc"].iloc[600]
    end = start + pd.Timedelta(seconds=120)
    first, _ = assemble_block(q.iloc[:900], start, end, p, q["timestamp_utc"].iloc[0])
    second, _ = assemble_block(q, start, end, p, q["timestamp_utc"].iloc[0])
    pd.testing.assert_frame_equal(first, second)


def test_target_at_evaluation_boundary_is_purged():
    p = policy()
    t = pd.date_range("2020-01-01", periods=3, freq="s", tz="UTC")
    frame = pd.DataFrame({
        "feature_timestamp_utc": t,
        "target_timestamp_utc_1s": t + pd.Timedelta(seconds=1),
        "price_valid": True, "label_valid_1s": True,
        "label_1s_pips": 1.0, "mid_change_1s_pips": 0.0,
        "size_pair_valid": True, "bid_size": 1.0, "ask_size": 1.0,
    })
    _, common = eligible(frame, 1, t[0], t[-1] + pd.Timedelta(seconds=1), p)
    assert common.tolist() == [True, True, False]
