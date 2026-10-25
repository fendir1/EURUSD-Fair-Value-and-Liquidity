"""Regression diagnostics without model selection or execution assumptions."""

import numpy as np
import pandas as pd


def _arrays(actual, predicted):
    actual = np.asarray(actual, dtype=float)
    predicted = np.asarray(predicted, dtype=float)
    if (
        actual.ndim != 1 or predicted.ndim != 1
        or len(actual) != len(predicted) or len(actual) == 0
        or not np.isfinite(actual).all()
        or not np.isfinite(predicted).all()
    ):
        raise ValueError("Actual and predicted values must be finite matching vectors.")
    return actual, predicted


def regression_metrics(actual, predicted):
    actual, predicted = _arrays(actual, predicted)
    error = predicted - actual
    slope = intercept = None
    if np.ptp(predicted) > 1e-12:
        centered = predicted - predicted.mean()
        slope = float(
            np.dot(centered, actual - actual.mean())
            / np.dot(centered, centered)
        )
        intercept = float(actual.mean() - slope * predicted.mean())
    return {
        "rows": len(actual),
        "mae_pips": float(np.mean(np.abs(error))),
        "rmse_pips": float(np.sqrt(np.mean(error**2))),
        "mean_actual_pips": float(actual.mean()),
        "mean_forecast_pips": float(predicted.mean()),
        "forecast_bias_pips": float(error.mean()),
        "calibration_slope": slope,
        "calibration_intercept_pips": intercept,
    }


def training_bucket_cuts(predictions, buckets=5):
    predictions = np.asarray(predictions, dtype=float)
    if (
        predictions.ndim != 1 or len(predictions) == 0
        or not np.isfinite(predictions).all()
        or type(buckets) is not int or buckets < 2
    ):
        raise ValueError("Finite training predictions and at least two buckets required.")
    cuts = np.unique(np.quantile(
        predictions, np.linspace(0, 1, buckets + 1)[1:-1]
    ))
    return cuts[
        (cuts > predictions.min()) & (cuts < predictions.max())
    ]


def forecast_buckets(actual, predicted, cuts):
    actual, predicted = _arrays(actual, predicted)
    cuts = np.asarray(cuts, dtype=float)
    if (
        not np.isfinite(cuts).all()
        or (len(cuts) > 1 and not np.all(np.diff(cuts) > 0))
    ):
        raise ValueError("Bucket cuts must be finite and strictly increasing.")
    frame = pd.DataFrame({
        "bucket": np.searchsorted(cuts, predicted, side="right"),
        "forecast_pips": predicted,
        "actual_pips": actual,
    })
    return frame.groupby("bucket", as_index=False).agg(
        rows=("actual_pips", "size"),
        mean_forecast_pips=("forecast_pips", "mean"),
        mean_actual_pips=("actual_pips", "mean"),
    )


def hourly_errors(timestamps, actual, predicted):
    actual, predicted = _arrays(actual, predicted)
    times = pd.Series(pd.to_datetime(timestamps, utc=True)).reset_index(drop=True)
    if len(times) != len(actual) or times.isna().any():
        raise ValueError("Valid matching timestamps are required.")
    error = predicted - actual
    frame = pd.DataFrame({
        "hour_utc": times.dt.floor("h"),
        "absolute_error": np.abs(error),
        "squared_error": error**2,
    })
    result = frame.groupby("hour_utc", as_index=False).agg(
        rows=("absolute_error", "size"),
        mae_pips=("absolute_error", "mean"),
        mean_squared_error=("squared_error", "mean"),
    )
    result["rmse_pips"] = np.sqrt(result.pop("mean_squared_error"))
    return result
