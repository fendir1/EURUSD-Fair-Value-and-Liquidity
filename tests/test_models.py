import joblib
import numpy as np
import pandas as pd
import pytest

from eurusd_research import features as io
from eurusd_research.models import (
    eligibility, fit_ridge, load_development_window,
    predict_ridge, save_pipeline, validate_predictors,
)


def test_outcome_predictors_rejected():
    for name in (
        "target_mid_price_1s", "label_1s_pips",
        "split_1s", "same_source_record_1s", "source_row",
    ):
        with pytest.raises(ValueError, match="Forbidden"):
            validate_predictors(["spread_pips", name])


def test_preprocessing_is_fitted_on_training_only():
    train = pd.DataFrame({"x": [1.0, 3.0, np.nan, 5.0]})
    model = fit_ridge(train, [0, 1, 0, 1], ["x"], minimum_rows=2)
    imputer_before = model.named_steps["impute"].statistics_.copy()
    scaler_before = model.named_steps["scale"].mean_.copy()

    predict_ridge(model, pd.DataFrame({"x": [1000000.0, np.nan]}))

    np.testing.assert_array_equal(
        model.named_steps["impute"].statistics_, imputer_before
    )
    np.testing.assert_array_equal(
        model.named_steps["scale"].mean_, scaler_before
    )
    assert imputer_before[0] == 3.0


def test_all_missing_training_feature_is_supported():
    train = pd.DataFrame({"x": [1.0, 2.0, 3.0], "empty": [np.nan] * 3})
    model = fit_ridge(train, [0, 1, 2], ["x", "empty"], minimum_rows=2)
    result = predict_ridge(
        model, pd.DataFrame({"x": [2.0], "empty": [999.0]})
    )
    assert np.isfinite(result).all()


def test_eligibility_keeps_price_and_size_coverage_separate():
    times = pd.date_range("2020-01-01", periods=2, freq="s", tz="UTC")
    frame = pd.DataFrame({
        "price_valid": [True, True],
        "size_pair_valid": [True, False],
        "target_timestamp_utc_1s": times + pd.Timedelta(seconds=1),
        "label_1s_pips": [0.0, 1.0],
        "label_valid_1s": [True, True],
        "split_1s": ["train", "train"],
        "mid_change_1s_pips": [0.0, 0.0],
        "bid_size": [1.0, np.nan],
    })
    price, common, counts = eligibility(
        frame, 1, "train", times[-1] + pd.Timedelta(seconds=2), ["bid_size"]
    )
    assert price.tolist() == [True, True]
    assert common.tolist() == [True, False]
    assert counts["price_rows_without_required_size"] == 1

    price, _, counts = eligibility(
        frame, 1, "train", times[-1] + pd.Timedelta(seconds=1), ["bid_size"]
    )
    assert price.tolist() == [True, False]
    assert counts["additional_window_boundary_purges"] == 1


def test_saved_pipeline_preserves_predictions(tmp_path):
    train = pd.DataFrame({"x": [1.0, 2.0, 3.0]})
    model = fit_ridge(train, [0, 1, 2], ["x"], minimum_rows=2)
    path = tmp_path / "own_model.joblib"
    save_pipeline(model, path)
    restored = joblib.load(path)
    np.testing.assert_allclose(
        predict_ridge(model, train), predict_ridge(restored, train)
    )


def test_loader_does_not_open_holdout_parts(tmp_path):
    times = pd.date_range("2020-01-01", periods=2, freq="s", tz="UTC")
    features = pd.DataFrame({
        "feature_timestamp_utc": times,
        "source_timestamp_utc": times,
        "source_row": [1, 2],
        "price_valid": [True, True],
        "size_pair_valid": [True, True],
        "mid_price": [1.1, 1.1001],
        "spread_pips": [1.0, 1.0],
    })
    labels = pd.DataFrame({
        "feature_timestamp_utc": times,
        "source_row": [1, 2],
        "mid_price": [1.1, 1.1001],
        "target_timestamp_utc_1s": times + pd.Timedelta(seconds=1),
        "label_1s_pips": [1.0, 0.0],
        "label_valid_1s": [True, True],
        "same_source_record_1s": [False, False],
        "split_1s": ["train", "train"],
    })
    start, end = times[0], times[-1] + pd.Timedelta(seconds=1)

    def dataset(frame, name):
        folder = tmp_path / name
        record = io._save_frame(frame, folder / "part.parquet")
        record.update({
            "path": "part.parquet",
            "start_utc": start.isoformat(), "end_utc": end.isoformat(),
        })
        return {
            "status": "complete", "output_dir": str(folder),
            "parts": [
                record,
                {
                    "path": "holdout_file_must_not_be_opened.parquet",
                    "start_utc": end.isoformat(),
                    "end_utc": (end + pd.Timedelta(seconds=1)).isoformat(),
                },
            ],
        }

    result = load_development_window(
        dataset(features, "features"), dataset(labels, "labels"),
        start, end, ["spread_pips"], [1],
        {"duckdb_memory_limit": "128MB", "duckdb_temp_limit": "1GB"},
        10, 10, tmp_path / "working",
    )
    assert len(result) == 2
