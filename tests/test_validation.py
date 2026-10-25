import numpy as np
import pandas as pd
import pytest

from eurusd_research.validation import (
    assign_split, make_pilot_plan, validate_plan,
)


def plan():
    return {
        "feature_job_id": "synthetic",
        "project_final_test": None,
        "periods": [
            {"role": "train", "start_utc": "2020-01-01T00:00:00Z", "end_utc": "2020-01-02T00:00:00Z"},
            {"role": "validation", "start_utc": "2020-01-02T00:00:00Z", "end_utc": "2020-01-03T00:00:00Z"},
            {"role": "pilot_holdout", "start_utc": "2020-01-03T00:00:00Z", "end_utc": "2020-01-04T00:00:00Z"},
        ],
    }


def test_boundary_equality_is_purged():
    timestamps = pd.to_datetime([
        "2020-01-01T23:59:58Z",
        "2020-01-01T23:59:59Z",
        "2020-01-02T23:59:59Z",
        "2020-01-03T12:00:00Z",
    ], format="ISO8601", utc=True)
    labels = pd.DataFrame({
        "feature_timestamp_utc": timestamps,
        "target_timestamp_utc_1s": timestamps + pd.Timedelta(seconds=1),
        "label_valid_1s": [True, True, True, False],
    })
    assert assign_split(labels, plan(), 1).tolist() == [
        "train", "purged_train", "purged_validation", "invalid_label"
    ]


def test_dates_come_from_observed_coverage():
    dates = pd.date_range("2020-01-01", periods=20, tz="UTC")
    daily = pd.DataFrame({
        "date_utc": dates,
        "usable_price_rows": np.ones(20, dtype=int),
    })
    selected = make_pilot_plan(
        daily, dates[0], dates[-1] + pd.Timedelta(days=1), "synthetic"
    )
    assert pd.Timestamp(selected["periods"][1]["start_utc"]) == dates[12]
    assert pd.Timestamp(selected["periods"][2]["start_utc"]) == dates[16]
    assert selected["project_final_test"] is None


def test_overlapping_and_naive_periods_rejected():
    overlapping = plan()
    overlapping["periods"][1]["start_utc"] = "2020-01-01T12:00:00Z"
    with pytest.raises(ValueError):
        validate_plan(overlapping)

    naive = plan()
    naive["periods"][0]["start_utc"] = "2020-01-01"
    with pytest.raises(ValueError, match="timezone"):
        validate_plan(naive)
