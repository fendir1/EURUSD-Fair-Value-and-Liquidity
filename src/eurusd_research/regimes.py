"""Causal liquidity metrics and training-only transparent regime rules."""

from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from . import features as io
from .data import _sha256
from .validation import utc


REGIME_NAMES = ["unavailable", "unclassified", "stressed", "thin", "normal"]
METRIC_COLUMNS = [
    "metric_spread_mean_pips",
    "metric_volatility_pips",
    "metric_min_side_size_mean",
    "metric_quote_count",
]


def input_columns(window):
    return [
        "feature_timestamp_utc", "source_timestamp_utc",
        "price_valid", "size_pair_valid",
        "snapshot_staleness_seconds",
        "spread_pips", "bid_size", "ask_size",
        "price_path_break", "unusable_size_observed_1s",
        f"realized_vol_{window}s_pips",
        f"snapshot_count_{window}s",
    ]


def build_liquidity_metrics(frame, start, end, settings):
    start, end = utc(start), utc(end)
    window = settings["lookback_seconds"]
    if type(window) is not int or window < 1 or start >= end:
        raise ValueError("Invalid liquidity window or output range.")

    q = frame[input_columns(window)].copy().reset_index(drop=True)
    for name in ("feature_timestamp_utc", "source_timestamp_utc"):
        if not isinstance(q[name].dtype, pd.DatetimeTZDtype):
            raise ValueError("Input timestamps must explicitly carry a timezone.")
        q[name] = q[name].dt.tz_convert("UTC").astype("datetime64[us, UTC]")

    times = q["feature_timestamp_utc"]
    if (
        len(q) == 0 or times.isna().any() or not times.is_unique
        or not times.is_monotonic_increasing
        or not times.diff().iloc[1:].eq(pd.Timedelta(seconds=1)).all()
    ):
        raise ValueError("Liquidity inputs require a complete one-second grid.")
    if (q["source_timestamp_utc"] > times).any():
        raise ValueError("A future source entered liquidity inputs.")

    for name in (
        "price_valid", "size_pair_valid",
        "price_path_break", "unusable_size_observed_1s",
    ):
        if q[name].isna().any() or not pd.api.types.is_bool_dtype(q[name]):
            raise ValueError(f"{name} must contain nonmissing booleans.")

    continuous = (
        q["price_valid"].rolling(window + 1, min_periods=window + 1)
        .sum().eq(window + 1)
        & q["price_path_break"].rolling(window, min_periods=window)
        .sum().eq(0)
    )
    size_complete = (
        continuous
        & q["size_pair_valid"].rolling(window, min_periods=window)
        .sum().eq(window)
        & q["unusable_size_observed_1s"].rolling(window, min_periods=window)
        .sum().eq(0)
    )

    result = q[[
        "feature_timestamp_utc", "source_timestamp_utc",
        "price_valid", "size_pair_valid",
        "snapshot_staleness_seconds", "spread_pips", "bid_size", "ask_size",
    ]].copy()
    result["metric_lookback_seconds"] = window
    result["metric_spread_mean_pips"] = (
        q["spread_pips"].where(q["price_valid"])
        .rolling(window, min_periods=window).mean().where(continuous)
    )
    result["metric_volatility_pips"] = (
        q[f"realized_vol_{window}s_pips"].where(continuous)
    )
    minimum_size = pd.Series(
        np.minimum(q["bid_size"], q["ask_size"]), index=q.index
    ).where(q["size_pair_valid"])
    result["metric_min_side_size_mean"] = (
        minimum_size.rolling(window, min_periods=window)
        .mean().where(size_complete)
    )
    result["metric_quote_count"] = (
        q[f"snapshot_count_{window}s"].where(continuous)
    )

    values = result[METRIC_COLUMNS].to_numpy(dtype=float)
    result["regime_metrics_complete"] = (
        q["price_valid"]
        & np.isfinite(values).all(axis=1)
        & (values >= 0).all(axis=1)
    )

    result = result.loc[
        result["feature_timestamp_utc"].ge(start)
        & result["feature_timestamp_utc"].lt(end)
    ].reset_index(drop=True)
    if len(result) != int((end - start).total_seconds()):
        raise ValueError("Liquidity output row conservation failed.")
    return result


def fit_regime_thresholds(metrics, settings, start, end):
    start, end = utc(start), utc(end)
    low, high = settings["low_quantile"], settings["high_quantile"]
    if not 0 < low < high < 1:
        raise ValueError("Quantiles must satisfy 0 < low < high < 1.")

    selected = metrics.loc[
        metrics["feature_timestamp_utc"].ge(start)
        & metrics["feature_timestamp_utc"].lt(end)
        & metrics["regime_metrics_complete"]
    ]
    if len(selected) < settings["minimum_training_rows"]:
        raise ValueError("Too few complete training rows for regime thresholds.")
    if not selected["metric_lookback_seconds"].eq(
        settings["lookback_seconds"]
    ).all():
        raise ValueError("Metric and threshold lookback windows differ.")

    values = selected[METRIC_COLUMNS].to_numpy(dtype=float)
    if not np.isfinite(values).all() or (values < 0).any():
        raise ValueError("Complete training metrics must be finite and nonnegative.")

    return {
        "rule_version": "trailing_quantiles_v1",
        "settings": dict(settings),
        "fit_start_utc": start.isoformat(),
        "fit_end_utc_exclusive": end.isoformat(),
        "training_metric_rows": len(selected),
        "cutoffs": {
            "spread_high_pips": float(
                selected["metric_spread_mean_pips"].quantile(high)
            ),
            "volatility_high_pips": float(
                selected["metric_volatility_pips"].quantile(high)
            ),
            "depth_low_source_units": float(
                selected["metric_min_side_size_mean"].quantile(low)
            ),
            "activity_low_snapshots": float(
                selected["metric_quote_count"].quantile(low)
            ),
        },
    }


def classify_regimes(metrics, thresholds):
    result = metrics.copy()
    if not result["metric_lookback_seconds"].eq(
        thresholds["settings"]["lookback_seconds"]
    ).all():
        raise ValueError("Metric and fitted threshold windows differ.")

    cut = thresholds["cutoffs"]
    result["high_spread"] = result["metric_spread_mean_pips"].gt(
        cut["spread_high_pips"]
    )
    result["high_volatility"] = result["metric_volatility_pips"].gt(
        cut["volatility_high_pips"]
    )
    result["low_depth"] = result["metric_min_side_size_mean"].lt(
        cut["depth_low_source_units"]
    )
    result["low_activity"] = result["metric_quote_count"].lt(
        cut["activity_low_snapshots"]
    )
    result["zero_side_size"] = (
        result["bid_size"].eq(0) | result["ask_size"].eq(0)
    )

    result["liquidity_regime"] = pd.Series(np.select(
        [
            ~result["price_valid"].to_numpy(),
            ~result["regime_metrics_complete"].to_numpy(),
            (result["high_spread"] | result["high_volatility"]).to_numpy(),
            (
                result["low_depth"] | result["low_activity"]
                | result["zero_side_size"]
            ).to_numpy(),
        ],
        ["unavailable", "unclassified", "stressed", "thin"],
        default="normal",
    ), index=result.index, dtype="string")
    return result



def load_regime_inputs(state, repository_root, start, end,
                       settings, feature_settings):
    start, end = utc(start), utc(end)
    if state["status"] != "complete":
        raise ValueError("A completed feature generation is required.")

    available_start = utc(state["identity"]["start_utc"])
    available_end = utc(state["identity"]["end_utc"])
    if not available_start <= start < end <= available_end:
        raise ValueError("Requested scope exceeds feature coverage.")

    read_start = max(
        available_start,
        start - pd.Timedelta(seconds=settings["lookback_seconds"]),
    )
    expected = int((end - read_start).total_seconds())
    if expected > settings["max_rows_per_scope"]:
        raise ValueError("Liquidity input scope exceeds its row guard.")

    output = (
        Path(repository_root) / "data/processed/features" / state["job_id"]
    ).resolve()
    files = []
    for part in state["parts"]:
        if utc(part["start_utc"]) >= end or utc(part["end_utc"]) <= read_start:
            continue
        path = (output / part["path"]).resolve()
        if not path.is_relative_to(output):
            raise RuntimeError("A feature part escapes its generation directory.")
        if not path.is_file() or _sha256(path) != part["sha256"]:
            raise RuntimeError(f"Feature integrity failed: {path}")
        parquet = pq.ParquetFile(path)
        if (
            parquet.metadata.num_rows != part["rows"]
            or io._schema_hash(parquet.schema_arrow) != part["schema_sha256"]
        ):
            raise RuntimeError("Feature part schema or row count changed.")
        files.append(str(path))

    if not files:
        raise ValueError("No feature parts intersect the requested scope.")

    columns = input_columns(settings["lookback_seconds"])
    working = (
        Path(repository_root) / "artifacts/manifests/regimes/loading"
        / start.strftime("%Y%m%dT%H%M%S")
    )
    connection = io._connect(files, "features", working, feature_settings)
    try:
        frame = connection.execute(f"""
            SELECT {", ".join(columns)} FROM features
            WHERE feature_timestamp_utc >= CAST(? AS TIMESTAMPTZ)
              AND feature_timestamp_utc < CAST(? AS TIMESTAMPTZ)
            ORDER BY feature_timestamp_utc LIMIT ?
        """, [
            read_start.isoformat(), end.isoformat(),
            settings["max_rows_per_scope"] + 1,
        ]).fetchdf()
    finally:
        connection.close()

    frame = io.normalise_feature_types(frame)
    if len(frame) != expected:
        raise RuntimeError("Liquidity input row conservation failed.")
    observed_bytes = int(frame.memory_usage(index=True, deep=True).sum())
    if observed_bytes > settings["max_frame_mib"] * 1024**2:
        raise RuntimeError("Liquidity input exceeds its dataframe memory guard.")

    return frame, {
        "input_rows_including_context": len(frame),
        "selected_files_verified": len(files),
        "observed_input_frame_bytes": observed_bytes,
        "read_start_utc": read_start.isoformat(),
    }
