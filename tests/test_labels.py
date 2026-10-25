import numpy as np
import pandas as pd
import pytest

from eurusd_research.labels import make_forward_labels


def states(n=80, start="2020-01-31T23:59:30Z"):
    times = pd.date_range(start, periods=n, freq="s")
    return pd.DataFrame({
        "feature_timestamp_utc": times,
        "source_timestamp_utc": times,
        "source_file": ["synthetic.csv"] * n,
        "source_sha256": ["synthetic-content"] * n,
        "source_row": pd.Series(np.arange(n), dtype="Int64"),
        "snapshot_staleness_seconds": np.zeros(n),
        "mid_price": 1.1 + np.arange(n) * 0.0001,
        "price_valid": np.ones(n, dtype=bool),
        "size_pair_valid": np.ones(n, dtype=bool),
        "price_path_break": np.r_[True, np.zeros(n - 1, dtype=bool)],
    })


def run(q, start=None, end=None, horizons=(1, 5, 30, 60)):
    start = q["feature_timestamp_utc"].iloc[0] if start is None else start
    end = q["feature_timestamp_utc"].iloc[-1] + pd.Timedelta(seconds=1) if end is None else end
    return make_forward_labels(q, start, end, list(horizons), 0.0001, 30)


def test_exact_horizon_and_pips():
    result = run(states())
    for h in (1, 5, 30, 60):
        assert result.loc[0, f"label_{h}s_pips"] == pytest.approx(h)
        assert (
            result.loc[0, f"target_timestamp_utc_{h}s"]
            - result.loc[0, "feature_timestamp_utc"]
        ).total_seconds() == h


def test_carried_snapshot_does_not_use_next_quote():
    q = states(4)
    q.loc[1, "source_timestamp_utc"] = q.loc[0, "source_timestamp_utc"]
    q.loc[1, "source_row"] = q.loc[0, "source_row"]
    q.loc[1, "mid_price"] = q.loc[0, "mid_price"]
    q.loc[1, "snapshot_staleness_seconds"] = 1.0
    result = run(q, horizons=(1,))
    assert result.loc[0, "label_1s_pips"] == 0
    assert result.loc[0, "same_source_record_1s"]


def test_same_timestamp_different_record_is_not_same_record():
    q = states(3)
    q.loc[1, "source_timestamp_utc"] = q.loc[0, "source_timestamp_utc"]
    q.loc[1, "snapshot_staleness_seconds"] = 1.0
    result = run(q, horizons=(1,))
    assert not result.loc[0, "same_source_record_1s"]


def test_missing_target():
    result = run(states(3), horizons=(5,))
    assert not result["label_valid_5s"].any()
    assert result["label_reason_5s"].eq("missing_target").all()


def test_stale_target_rejected():
    q = states(3)
    q.loc[1, "source_timestamp_utc"] = (
        q.loc[1, "feature_timestamp_utc"] - pd.Timedelta(seconds=31)
    )
    q.loc[1, "snapshot_staleness_seconds"] = 31.0
    q.loc[1, "price_valid"] = False
    result = run(q, horizons=(1,))
    assert not result.loc[0, "label_valid_1s"]
    assert result.loc[0, "label_reason_1s"] == "invalid_target_state"


def test_break_between_valid_endpoints():
    q = states(8)
    q.loc[3, "price_path_break"] = True
    result = run(q, horizons=(5,))
    assert q.loc[0, "price_valid"] and q.loc[5, "price_valid"]
    assert result.loc[0, "label_reason_5s"] == "broken_price_path"


def test_new_state_after_break_can_start_new_label():
    q = states(5)
    q.loc[2, "price_path_break"] = True
    result = run(q, horizons=(1,))
    assert not result.loc[1, "label_valid_1s"]
    assert result.loc[2, "label_valid_1s"]


def test_future_source_and_missing_grid_rejected():
    q = states(5)
    q.loc[1, "source_timestamp_utc"] += pd.Timedelta(seconds=1)
    with pytest.raises(ValueError, match="future"):
        run(q)
    with pytest.raises(ValueError, match="one-second"):
        run(states(5).drop(index=2))


def test_partition_and_future_append_consistency():
    q = states(180)
    start = q["feature_timestamp_utc"].iloc[0]
    middle, end = start + pd.Timedelta(seconds=40), start + pd.Timedelta(seconds=100)
    whole = run(q, start, end)
    left = run(q.iloc[:100], start, middle)
    right = run(q.iloc[40:160], middle, end)
    combined = pd.concat([left, right], ignore_index=True)
    pd.testing.assert_frame_equal(whole, combined)

    before = run(q.iloc[:160], start, end)
    after = run(q, start, end)
    pd.testing.assert_frame_equal(before, after)
