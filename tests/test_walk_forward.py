import numpy as np
import pandas as pd
import pytest

from eurusd_research.walk_forward import (
    _record, _verify_files, fold_eligibility,
    make_fold_plan, paired_block_interval, validate_fold,
)


def test_fold_dates_are_chronological_and_protected():
    dates = pd.date_range("2020-01-01", periods=15, tz="UTC")
    daily = pd.DataFrame({"date_utc": dates, "usable_price_rows": 200})
    settings = {
        "training_calendar_days": 3, "evaluation_calendar_days": 1,
        "minimum_training_price_rows": 10,
        "minimum_evaluation_price_rows": 10, "max_folds": 31,
    }
    protected = pd.Timestamp("2020-01-12T00:00:00Z")
    plan = make_fold_plan(daily, dates[0], protected, settings)
    assert len(plan["folds"]) == 8
    previous_end = None
    for fold in plan["folds"]:
        assert pd.Timestamp(fold["train_end_utc"]) == pd.Timestamp(
            fold["evaluation_start_utc"]
        )
        assert pd.Timestamp(fold["evaluation_end_utc"]) <= protected
        if previous_end is not None:
            assert pd.Timestamp(fold["evaluation_start_utc"]) >= previous_end
        previous_end = pd.Timestamp(fold["evaluation_end_utc"])


def test_overlapping_or_holdout_fold_rejected():
    fold = {
        "train_start_utc": "2020-01-01T00:00:00Z",
        "train_end_utc": "2020-01-03T00:00:00Z",
        "evaluation_start_utc": "2020-01-02T00:00:00Z",
        "evaluation_end_utc": "2020-01-04T00:00:00Z",
    }
    with pytest.raises(ValueError):
        validate_fold(fold, "2020-01-05T00:00:00Z")


def test_new_fold_purging_does_not_use_original_split_names():
    times = pd.date_range("2020-01-01", periods=2, freq="s", tz="UTC")
    frame = pd.DataFrame({
        "feature_timestamp_utc": times,
        "target_timestamp_utc_1s": times + pd.Timedelta(seconds=1),
        "price_valid": [True, True],
        "size_pair_valid": [True, True],
        "label_valid_1s": [True, True],
        "label_1s_pips": [0.0, 1.0],
        "mid_change_1s_pips": [0.0, 0.0],
        "bid_size": [1.0, 1.0],
        "split_1s": ["validation", "purged_train"],
    })
    mask, counts = fold_eligibility(
        frame, 1, times[0], times[-1] + pd.Timedelta(seconds=1), ["bid_size"]
    )
    assert mask.tolist() == [True, False]
    assert counts["boundary_purged_rows"] == 1


def test_cache_integrity_detects_changed_output(tmp_path):
    path = tmp_path / "generated.bin"
    path.write_bytes(b"original")
    record = _record(tmp_path, path, "test")
    _verify_files(tmp_path, [record])
    path.write_bytes(b"modified")
    with pytest.raises(RuntimeError, match="integrity"):
        _verify_files(tmp_path, [record])


def daily_losses(n=12):
    return pd.DataFrame({
        "date_utc": pd.date_range("2020-01-01", periods=n, tz="UTC"),
        "rows": np.full(n, 10),
        "candidate_abs_sum": np.full(n, 5.0),
        "reference_abs_sum": np.full(n, 10.0),
        "candidate_sq_sum": np.full(n, 5.0),
        "reference_sq_sum": np.full(n, 20.0),
    })


def test_constant_paired_differences_have_known_intervals():
    result = paired_block_interval(daily_losses(), repetitions=100)
    expected_rmse = np.sqrt(0.5) - np.sqrt(2.0)
    assert result["point_delta_mae_pips"] == pytest.approx(-0.5)
    assert result["mae_interval_low_pips"] == pytest.approx(-0.5)
    assert result["mae_interval_high_pips"] == pytest.approx(-0.5)
    assert result["point_delta_rmse_pips"] == pytest.approx(expected_rmse)


def test_bootstrap_point_estimate_uses_row_weights():
    q = daily_losses(2)
    q["rows"] = [1, 9]
    q["candidate_abs_sum"] = [0.0, 0.0]
    q["candidate_sq_sum"] = [0.0, 0.0]
    q["reference_abs_sum"] = [1.0, 27.0]
    q["reference_sq_sum"] = [1.0, 81.0]
    result = paired_block_interval(
        q, block_dates=1, repetitions=100, minimum_dates=2
    )
    assert result["point_delta_mae_pips"] == pytest.approx(-2.8)
    assert result["point_delta_rmse_pips"] == pytest.approx(-np.sqrt(8.2))


def test_resampling_is_reproducible():
    q = daily_losses()
    q["candidate_abs_sum"] += np.arange(len(q)) * 0.1
    first = paired_block_interval(q, repetitions=100, seed=42)
    second = paired_block_interval(q, repetitions=100, seed=42)
    assert first == second


def test_too_few_dates_does_not_invent_an_interval():
    result = paired_block_interval(daily_losses(3), repetitions=100)
    assert result["status"] == "insufficient_dates"
    assert result["mae_interval_low_pips"] is None
    assert result["rmse_interval_high_pips"] is None
