import numpy as np
import pandas as pd
import pytest

from eurusd_research.evaluation import (
    forecast_buckets, hourly_errors, regression_metrics, training_bucket_cuts,
)


def test_hand_calculated_errors():
    metrics = regression_metrics([1.0, -1.0], [0.0, 0.0])
    assert metrics["mae_pips"] == 1.0
    assert metrics["rmse_pips"] == 1.0
    assert metrics["calibration_slope"] is None


def test_calibration_on_known_linear_relation():
    metrics = regression_metrics([1.0, 3.0, 5.0], [0.0, 1.0, 2.0])
    assert metrics["calibration_slope"] == pytest.approx(2.0)
    assert metrics["calibration_intercept_pips"] == pytest.approx(1.0)


def test_buckets_use_training_boundaries():
    cuts = training_bucket_cuts([0.0, 1.0, 2.0, 3.0], buckets=2)
    np.testing.assert_allclose(cuts, [1.5])
    table = forecast_buckets([0.0, 0.0], [-100.0, 100.0], cuts)
    assert table["bucket"].tolist() == [0, 1]
    assert table["rows"].sum() == 2
    assert len(training_bucket_cuts([0.0, 0.0, 0.0])) == 0


def test_hourly_aggregation_preserves_error_totals():
    times = pd.to_datetime([
        "2020-01-01T00:00:00Z",
        "2020-01-01T00:00:01Z",
        "2020-01-01T01:00:00Z",
    ], format="ISO8601", utc=True)
    table = hourly_errors(times, [1.0, -1.0, 2.0], [0.0, 0.0, 0.0])
    assert table["rows"].tolist() == [2, 1]
    assert table["mae_pips"].tolist() == [1.0, 2.0]
    assert (table["rows"] * table["mae_pips"]).sum() == 4.0
