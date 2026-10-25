"""Bounded development samples and training-only Ridge pipelines."""

import os
import tempfile
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import pyarrow.parquet as pq
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Ridge
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from . import features as io
from .data import _sha256
from .validation import utc


def validate_predictors(columns):
    forbidden = (
        "target_", "label_", "split_", "same_source_",
        "source_", "reference_", "current_", "feature_timestamp",
    )
    if not columns or len(columns) != len(set(columns)):
        raise ValueError("Predictor names must be nonempty and unique.")
    for name in columns:
        if not isinstance(name, str) or not name.isidentifier():
            raise ValueError("Predictor names must be valid identifiers.")
        if name.startswith(forbidden):
            raise ValueError(f"Forbidden predictor: {name}")
    return list(columns)


def _selected_files(state, start, end):
    if state["status"] != "complete":
        raise ValueError("A completed dataset is required.")
    root = Path(state["output_dir"]).resolve()
    files = []
    for part in state["parts"]:
        if utc(part["start_utc"]) >= end or utc(part["end_utc"]) <= start:
            continue
        path = (root / part["path"]).resolve()
        if not path.is_relative_to(root):
            raise RuntimeError("A selected output escapes its generation directory.")
        if not path.is_file() or _sha256(path) != part["sha256"]:
            raise RuntimeError(f"Selected output integrity failed: {path}")
        parquet = pq.ParquetFile(path)
        if (
            parquet.metadata.num_rows != part["rows"]
            or io._schema_hash(parquet.schema_arrow) != part["schema_sha256"]
        ):
            raise RuntimeError("Selected output rows or schema changed.")
        files.append(str(path))
    if not files:
        raise ValueError("No dataset parts intersect the requested window.")
    return files


def load_development_window(feature_state, label_state, start, end, columns,
                            horizons, resource_settings, maximum_rows,
                            maximum_frame_mib, working_directory):
    start, end = utc(start), utc(end)
    columns = validate_predictors(columns)
    expected = int((end - start).total_seconds())
    if expected < 1 or expected > maximum_rows:
        raise ValueError("The development window exceeds its clock-row limit.")
    if any(type(h) is not int or h < 1 for h in horizons):
        raise ValueError("Horizons must be positive integer seconds.")

    feature_files = _selected_files(feature_state, start, end)
    label_files = _selected_files(label_state, start, end)
    connection = io._connect(
        feature_files, "features", working_directory, resource_settings
    )
    try:
        connection.read_parquet(
            label_files, hive_partitioning=False
        ).create_view("labels")
        selected = [
            "f.feature_timestamp_utc",
            "f.source_timestamp_utc",
            "f.source_row",
            "f.price_valid",
            "f.size_pair_valid",
            "f.mid_price AS current_mid_price",
            "l.mid_price AS reference_mid_price",
            "l.source_row AS reference_source_row",
        ]
        selected += [f"f.{name}" for name in columns]
        for h in horizons:
            selected += [
                f"l.target_timestamp_utc_{h}s",
                f"l.label_{h}s_pips",
                f"l.label_valid_{h}s",
                f"l.same_source_record_{h}s",
                f"l.split_{h}s",
            ]
        frame = connection.execute(f"""
            SELECT {", ".join(selected)}
            FROM features f
            JOIN labels l USING (feature_timestamp_utc)
            WHERE f.feature_timestamp_utc >= CAST(? AS TIMESTAMPTZ)
              AND f.feature_timestamp_utc < CAST(? AS TIMESTAMPTZ)
            ORDER BY f.feature_timestamp_utc
            LIMIT ?
        """, [start.isoformat(), end.isoformat(), maximum_rows + 1]).fetchdf()
    finally:
        connection.close()

    for name in frame:
        if "timestamp_utc" in name:
            frame[name] = pd.to_datetime(frame[name], utc=True).astype(
                "datetime64[us, UTC]"
            )
        elif name.startswith("split_"):
            frame[name] = frame[name].astype("category")

    times = frame["feature_timestamp_utc"]
    if (
        len(frame) != expected or not times.is_unique
        or times.iloc[0] != start
        or times.iloc[-1] != end - pd.Timedelta(seconds=1)
        or not times.diff().iloc[1:].eq(pd.Timedelta(seconds=1)).all()
    ):
        raise RuntimeError("Development grid conservation failed.")
    if (frame["source_timestamp_utc"] > times).any():
        raise RuntimeError("A future source entered development features.")
    if not np.allclose(
        frame["current_mid_price"], frame["reference_mid_price"], equal_nan=True
    ):
        raise RuntimeError("Feature and label current prices disagree.")
    if not frame["source_row"].astype("Int64").equals(
        frame["reference_source_row"].astype("Int64")
    ):
        raise RuntimeError("Feature and label source rows disagree.")

    observed_bytes = int(frame.memory_usage(index=True, deep=True).sum())
    if observed_bytes > maximum_frame_mib * 1024**2:
        raise RuntimeError("The collected frame exceeds its configured memory guard.")
    return frame


def eligibility(frame, horizon, role, window_end, required_size_columns):
    target = frame[f"target_timestamp_utc_{horizon}s"]
    valid = (
        frame["price_valid"]
        & frame[f"label_valid_{horizon}s"]
        & np.isfinite(frame[f"label_{horizon}s_pips"])
        & frame[f"split_{horizon}s"].eq(role)
    )
    inside_window = target.lt(utc(window_end))
    benchmark_available = np.isfinite(frame[f"mid_change_{horizon}s_pips"])
    price = valid & inside_window & benchmark_available

    size_values = frame[required_size_columns].to_numpy(dtype=float)
    size_available = (
        frame["size_pair_valid"] & np.isfinite(size_values).all(axis=1)
    )
    common = price & size_available

    counts = {
        "clock_rows": len(frame),
        "price_valid_rows": int(frame["price_valid"].sum()),
        "valid_label_rows": int(frame[f"label_valid_{horizon}s"].sum()),
        "role_eligible_before_window_purge": int(valid.sum()),
        "additional_window_boundary_purges": int((valid & ~inside_window).sum()),
        "price_eligible_rows": int(price.sum()),
        "matched_rows": int(common.sum()),
        "price_rows_without_required_size": int((price & ~size_available).sum()),
        "price_rows_missing_trailing_benchmark": int(
            (valid & inside_window & ~benchmark_available).sum()
        ),
    }
    return price, common, counts


def numeric_predictors(frame, columns):
    columns = validate_predictors(columns)
    return frame.loc[:, columns].astype("float64").replace(
        [np.inf, -np.inf], np.nan
    )


def fit_ridge(frame, y, columns, alpha=10.0, minimum_rows=1000):
    columns = validate_predictors(columns)
    y = np.asarray(y, dtype=float)
    if (
        y.ndim != 1 or len(y) != len(frame)
        or len(y) < minimum_rows or not np.isfinite(y).all()
    ):
        raise ValueError("Training targets must be finite and satisfy the row minimum.")
    if not np.isfinite(alpha) or alpha <= 0:
        raise ValueError("Ridge alpha must be finite and positive.")

    pipeline = Pipeline([
        ("impute", SimpleImputer(
            strategy="median", add_indicator=True, keep_empty_features=True
        )),
        ("scale", StandardScaler()),
        ("ridge", Ridge(alpha=alpha, solver="cholesky")),
    ])
    pipeline.fit(numeric_predictors(frame, columns), y)
    return pipeline


def predict_ridge(pipeline, frame):
    columns = pipeline.feature_names_in_.tolist()
    result = pipeline.predict(numeric_predictors(frame, columns))
    if not np.isfinite(result).all():
        raise RuntimeError("The model produced nonfinite forecasts.")
    return result


def save_pipeline(pipeline, path):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            joblib.dump(pipeline, handle, compress=3)
            handle.flush()
            os.fsync(handle.fileno())
        io._replace_path(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)
    return {
        "path": str(path),
        "bytes": path.stat().st_size,
        "sha256": _sha256(path),
    }
