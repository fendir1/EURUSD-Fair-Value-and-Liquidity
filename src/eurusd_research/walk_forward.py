"""Chronological development folds, resumable fitting, and paired block intervals."""

import hashlib
import json
import math
import time
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow.parquet as pq

from . import features as io
from .data import _sha256, _writer_lock
from .evaluation import training_bucket_cuts
from .models import (
    fit_ridge, load_development_window, predict_ridge, save_pipeline,
)
from .regimes import (
    build_liquidity_metrics, classify_regimes,
    fit_regime_thresholds, load_regime_inputs,
)
from .validation import utc


MODEL_NAMES = ("zero", "trailing", "ridge_price", "ridge_price_size")


def validate_fold(fold, protected_start):
    train_start = utc(fold["train_start_utc"])
    train_end = utc(fold["train_end_utc"])
    evaluation_start = utc(fold["evaluation_start_utc"])
    evaluation_end = utc(fold["evaluation_end_utc"])
    if not (
        train_start < train_end <= evaluation_start
        < evaluation_end <= utc(protected_start)
    ):
        raise ValueError("Fold chronology or protected holdout boundary is invalid.")
    return fold


def make_fold_plan(daily, available_start, protected_start, settings):
    available_start, protected_start = utc(available_start), utc(protected_start)
    days = settings["training_calendar_days"]
    if type(days) is not int or days < 1:
        raise ValueError("training_calendar_days must be a positive integer.")
    if settings["evaluation_calendar_days"] != 1:
        raise ValueError("This pilot uses nonoverlapping one-day evaluation windows.")

    q = daily[["date_utc", "usable_price_rows"]].copy()
    q["date_utc"] = pd.to_datetime(q["date_utc"], utc=True).dt.normalize()
    if not q["date_utc"].is_unique:
        raise ValueError("Daily coverage contains duplicate dates.")
    q = q.loc[
        q["date_utc"].ge(available_start)
        & q["date_utc"].lt(protected_start)
    ].sort_values("date_utc")
    active = q.loc[q["usable_price_rows"].gt(0), "date_utc"]
    if active.empty:
        raise ValueError("No usable development dates are available.")

    first_evaluation = active.iloc[0] + pd.Timedelta(days=days)
    folds, skipped = [], []
    for date in pd.date_range(
        first_evaluation, protected_start, freq="D", inclusive="left"
    ):
        train_start = date - pd.Timedelta(days=days)
        evaluation_end = date + pd.Timedelta(days=1)
        if evaluation_end > protected_start:
            continue
        training_rows = int(q.loc[
            q["date_utc"].ge(train_start) & q["date_utc"].lt(date),
            "usable_price_rows",
        ].sum())
        evaluation_rows = int(q.loc[
            q["date_utc"].eq(date), "usable_price_rows"
        ].sum())
        reason = None
        if training_rows < settings["minimum_training_price_rows"]:
            reason = "insufficient_training_price_coverage"
        elif evaluation_rows < settings["minimum_evaluation_price_rows"]:
            reason = "insufficient_evaluation_price_coverage"
        if reason:
            skipped.append({"date_utc": date.isoformat(), "reason": reason})
            continue

        fold = {
            "fold_id": date.strftime("wf_%Y%m%d"),
            "train_start_utc": train_start.isoformat(),
            "train_end_utc": date.isoformat(),
            "evaluation_start_utc": date.isoformat(),
            "evaluation_end_utc": evaluation_end.isoformat(),
        }
        folds.append(validate_fold(fold, protected_start))

    if not folds:
        raise ValueError("No eligible walk-forward folds were found.")
    if len(folds) > settings["max_folds"]:
        raise ValueError("Fold count exceeds the explicitly configured run limit.")
    return {"folds": folds, "skipped_evaluation_dates": skipped}


def fold_eligibility(frame, horizon, start, end, required_size_columns):
    start, end = utc(start), utc(end)
    timestamp = frame["feature_timestamp_utc"]
    target = frame[f"target_timestamp_utc_{horizon}s"]
    if not target.eq(timestamp + pd.Timedelta(seconds=horizon)).all():
        raise ValueError("Stored target timestamps disagree with their horizon.")

    inside = timestamp.ge(start) & timestamp.lt(end)
    valid = (
        inside & frame["price_valid"] & frame[f"label_valid_{horizon}s"]
        & np.isfinite(frame[f"label_{horizon}s_pips"])
    )
    before_end = target.lt(end)
    trailing = np.isfinite(frame[f"mid_change_{horizon}s_pips"])
    price = valid & before_end & trailing
    size_available = (
        frame["size_pair_valid"]
        & np.isfinite(
            frame[required_size_columns].to_numpy(dtype=float)
        ).all(axis=1)
    )
    common = price & size_available
    return common, {
        "clock_rows": int(inside.sum()),
        "price_valid_rows": int((inside & frame["price_valid"]).sum()),
        "valid_label_rows": int((inside & frame[f"label_valid_{horizon}s"]).sum()),
        "boundary_purged_rows": int((valid & ~before_end).sum()),
        "price_eligible_rows": int(price.sum()),
        "matched_rows": int(common.sum()),
        "price_rows_without_size": int((price & ~size_available).sum()),
        "price_rows_missing_trailing": int((valid & before_end & ~trailing).sum()),
    }


def _record(root, path, kind, metadata=None, **extra):
    path = Path(path).resolve()
    if not path.is_relative_to(root):
        raise RuntimeError("An output path escapes the repository.")
    values = dict(metadata) if metadata is not None else {
        "bytes": path.stat().st_size, "sha256": _sha256(path)
    }
    return {
        **values, "kind": kind,
        "path": path.relative_to(root).as_posix(), **extra,
    }


def _verify_files(root, records):
    root = Path(root).resolve()
    for record in records:
        path = (root / record["path"]).resolve()
        if (
            not path.is_relative_to(root) or not path.is_file()
            or path.stat().st_size != record["bytes"]
            or _sha256(path) != record["sha256"]
        ):
            raise RuntimeError(f"Cached output integrity failed: {record['path']}")
        if "rows" in record and pq.ParquetFile(path).metadata.num_rows != record["rows"]:
            raise RuntimeError("Cached forecast row count changed.")



def run_fold(repository_root, feature_state, label_state, fold, run_id, protocol):
    root = Path(repository_root).resolve()
    manifest_dir = root / "artifacts/manifests/walk_forward" / run_id
    with _writer_lock(manifest_dir):
        return _run_fold(
            root, feature_state, label_state, fold, run_id, protocol, manifest_dir
        )


def _run_fold(root, feature_state, label_state, fold, run_id, protocol, manifest_dir):
    validate_fold(fold, protocol["protected_start_utc"])
    began = time.perf_counter()
    manifest = manifest_dir / f"{fold['fold_id']}.json"
    identity = {
        "run_id": run_id,
        "protocol_sha256": hashlib.sha256(
            json.dumps(protocol, sort_keys=True).encode()
        ).hexdigest(),
        "fold": fold,
    }
    if manifest.exists():
        previous = json.loads(manifest.read_text(encoding="utf-8"))
        if previous["identity"] != identity:
            raise RuntimeError("Existing fold identity differs.")
        if previous["status"] == "complete":
            _verify_files(root, previous["files"])
            return {**previous, "cache_hit": True}

    state = {
        "status": "running", "identity": identity,
        "fold_id": fold["fold_id"], "files": [],
        "coverage": [], "fit_records": [], "bucket_cuts": {},
        "manifest_path": manifest.relative_to(root).as_posix(),
    }
    io.write_json(manifest, state)

    model_settings = protocol["model_settings"]
    regime_settings = protocol["regime_settings"]
    feature_settings = protocol["feature_settings"]
    horizons = protocol["horizons_seconds"]
    price_columns = model_settings["price_features"]
    all_columns = price_columns + model_settings["size_features"]
    load_columns = list(dict.fromkeys(
        all_columns + ["london_session", "new_york_session"]
    ))
    scope_bounds = {
        "train": (fold["train_start_utc"], fold["train_end_utc"]),
        "evaluation": (
            fold["evaluation_start_utc"], fold["evaluation_end_utc"]
        ),
    }

    frames = {}
    for role, (start, end) in scope_bounds.items():
        frames[role] = load_development_window(
            feature_state, label_state, start, end, load_columns, horizons,
            feature_settings, model_settings["max_rows_per_split"],
            model_settings["max_frame_mib"],
            manifest_dir / "loading" / fold["fold_id"] / role,
        )
    frame_bytes = {
        role: int(frame.memory_usage(index=True, deep=True).sum())
        for role, frame in frames.items()
    }
    if sum(frame_bytes.values()) > model_settings["max_combined_frame_mib"] * 1024**2:
        raise RuntimeError("Combined fold frames exceed the memory guard.")

    regime_metrics = {}
    for role, (start, end) in scope_bounds.items():
        raw, _ = load_regime_inputs(
            feature_state, root, start, end, regime_settings, feature_settings
        )
        regime_metrics[role] = build_liquidity_metrics(
            raw, start, end, regime_settings
        )
        del raw
    thresholds = fit_regime_thresholds(
        regime_metrics["train"], regime_settings, *scope_bounds["train"]
    )
    if utc(thresholds["fit_end_utc_exclusive"]) > utc(fold["evaluation_start_utc"]):
        raise RuntimeError("Regime fitting reaches into evaluation.")
    evaluation_regimes = classify_regimes(regime_metrics["evaluation"], thresholds)
    regime_lookup = evaluation_regimes.set_index(
        "feature_timestamp_utc"
    )["liquidity_regime"]
    del regime_metrics

    threshold_path = manifest_dir / f"{fold['fold_id']}-thresholds.json"
    io.write_json(threshold_path, thresholds)
    state["files"].append(_record(root, threshold_path, "thresholds"))
    state["thresholds"] = thresholds
    state["observed_input_frame_bytes"] = frame_bytes

    model_dir = root / "artifacts/models/walk_forward" / run_id / fold["fold_id"]
    forecast_dir = root / "artifacts/forecasts/walk_forward" / run_id / fold["fold_id"]

    for h in horizons:
        masks = {}
        for role, bounds in scope_bounds.items():
            mask, counts = fold_eligibility(
                frames[role], h, *bounds,
                model_settings["required_current_size_features"],
            )
            masks[role] = mask
            state["coverage"].append({
                "fold_id": fold["fold_id"], "horizon_seconds": h,
                "role": role, **counts,
            })
        train = frames["train"].loc[masks["train"]]
        evaluation = frames["evaluation"].loc[masks["evaluation"]]
        if min(len(train), len(evaluation)) < model_settings["minimum_fit_rows"]:
            raise RuntimeError(
                f"Too few matched rows in {fold['fold_id']} at {h}s. "
                "Stop rather than silently dropping this fold."
            )

        y_train = train[f"label_{h}s_pips"].to_numpy(dtype=float)
        train_predictions = {
            "zero": np.zeros(len(train)),
            "trailing": train[f"mid_change_{h}s_pips"].to_numpy(dtype=float),
        }
        predictions = {
            "zero": np.zeros(len(evaluation)),
            "trailing": evaluation[f"mid_change_{h}s_pips"].to_numpy(dtype=float),
        }
        for name, columns in (
            ("ridge_price", price_columns),
            ("ridge_price_size", all_columns),
        ):
            fitted = fit_ridge(
                train, y_train, columns,
                alpha=model_settings["ridge_alpha"],
                minimum_rows=model_settings["minimum_fit_rows"],
            )
            train_predictions[name] = predict_ridge(fitted, train)
            predictions[name] = predict_ridge(fitted, evaluation)
            path = model_dir / f"{name}-{h}s.joblib"
            saved = save_pipeline(fitted, path)
            state["files"].append(_record(
                root, path, "model", saved, horizon_seconds=h, model=name
            ))

        output = evaluation[[
            "feature_timestamp_utc", "source_timestamp_utc",
            "snapshot_staleness_seconds", "spread_pips",
            f"target_timestamp_utc_{h}s", f"label_{h}s_pips",
            f"same_source_record_{h}s",
            "london_session", "new_york_session",
        ]].rename(columns={
            f"target_timestamp_utc_{h}s": "target_timestamp_utc",
            f"label_{h}s_pips": "actual_change_pips",
            f"same_source_record_{h}s": "same_source_record",
        }).reset_index(drop=True)

        output["fold_id"] = pd.Series(
            fold["fold_id"], index=output.index, dtype="string"
        )
        output["horizon_seconds"] = h
        output["year_utc"] = output["feature_timestamp_utc"].dt.year.astype("int64")
        london, new_york = output["london_session"], output["new_york_session"]
        output["session_label"] = pd.Series(np.select(
            [london & new_york, london, new_york],
            ["london_new_york_overlap", "london_only", "new_york_only"],
            default="outside_defined_sessions",
        ), dtype="string")
        age = output["snapshot_staleness_seconds"]
        output["snapshot_age_band"] = pd.Series(np.select(
            [age.le(1), age.le(5)],
            ["0_to_1s", "over_1_to_5s"],
            default="over_5_to_30s",
        ), dtype="string")
        output["liquidity_regime"] = output["feature_timestamp_utc"].map(
            regime_lookup
        ).astype("string")

        if (
            output["liquidity_regime"].isna().any()
            or output["liquidity_regime"].eq("unavailable").any()
            or age.lt(0).any()
            or age.gt(feature_settings["max_staleness_seconds"]).any()
            or (output["source_timestamp_utc"] > output["feature_timestamp_utc"]).any()
        ):
            raise RuntimeError("Forecast state or regime alignment failed.")

        for name in MODEL_NAMES:
            if not np.isfinite(predictions[name]).all():
                raise RuntimeError("Nonfinite forecast encountered.")
            output[f"prediction_{name}_pips"] = predictions[name]
            cuts = training_bucket_cuts(
                train_predictions[name], model_settings["forecast_buckets"]
            )
            output[f"bucket_{name}"] = np.searchsorted(
                cuts, predictions[name], side="right"
            ).astype("int64")
            state["bucket_cuts"][f"{h}s/{name}"] = cuts.tolist()

        state["fit_records"].append({
            "horizon_seconds": h,
            "training_rows": len(train),
            "evaluation_rows": len(evaluation),
            "training_max_target_utc": train[f"target_timestamp_utc_{h}s"].max().isoformat(),
            "evaluation_max_target_utc": evaluation[f"target_timestamp_utc_{h}s"].max().isoformat(),
        })
        path = forecast_dir / f"{h}s.parquet"
        saved = io._save_frame(output, path)
        state["files"].append(_record(
            root, path, "forecasts", saved, horizon_seconds=h
        ))
        io.write_json(manifest, state)
        del fitted, train, evaluation, output

    state["runtime_seconds_when_fitted"] = round(time.perf_counter() - began, 3)
    state["status"] = "complete"
    io.write_json(manifest, state)
    return {**state, "cache_hit": False}



def paired_block_interval(daily, block_dates=2, repetitions=1000,
                          confidence=0.95, seed=42, minimum_dates=10):
    columns = [
        "rows", "candidate_abs_sum", "reference_abs_sum",
        "candidate_sq_sum", "reference_sq_sum",
    ]
    q = daily.sort_values("date_utc").reset_index(drop=True)
    dates = pd.to_datetime(q["date_utc"], utc=True)
    values = q[columns].to_numpy(dtype=float)
    if (
        len(q) == 0 or dates.isna().any() or not dates.is_unique
        or not np.isfinite(values).all()
        or (values[:, 0] <= 0).any() or (values[:, 1:] < 0).any()
    ):
        raise ValueError("Valid paired daily loss totals are required.")
    if (
        type(block_dates) is not int or block_dates < 1
        or type(repetitions) is not int or repetitions < 20
        or not 0 < confidence < 1
    ):
        raise ValueError("Invalid bootstrap configuration.")

    total = values.sum(axis=0)
    point_mae = (total[1] - total[2]) / total[0]
    point_rmse = np.sqrt(total[3] / total[0]) - np.sqrt(total[4] / total[0])
    result = {
        "evaluation_dates": len(q),
        "block_dates": block_dates,
        "repetitions": repetitions,
        "confidence": confidence,
        "point_delta_mae_pips": float(point_mae),
        "point_delta_rmse_pips": float(point_rmse),
        "mae_interval_low_pips": None,
        "mae_interval_high_pips": None,
        "rmse_interval_low_pips": None,
        "rmse_interval_high_pips": None,
        "status": "insufficient_dates",
    }
    if len(q) < minimum_dates or len(q) < block_dates:
        return result

    rng = np.random.default_rng(seed)
    blocks_needed = math.ceil(len(q) / block_dates)
    starts = rng.integers(
        0, len(q) - block_dates + 1,
        size=(repetitions, blocks_needed),
    )
    indices = (
        starts[:, :, None] + np.arange(block_dates)
    ).reshape(repetitions, -1)[:, :len(q)]
    totals = values[indices].sum(axis=1)

    delta_mae = (totals[:, 1] - totals[:, 2]) / totals[:, 0]
    delta_rmse = (
        np.sqrt(totals[:, 3] / totals[:, 0])
        - np.sqrt(totals[:, 4] / totals[:, 0])
    )
    tail = (1 - confidence) / 2
    mae_bounds = np.quantile(delta_mae, [tail, 1 - tail])
    rmse_bounds = np.quantile(delta_rmse, [tail, 1 - tail])
    result.update({
        "mae_interval_low_pips": float(mae_bounds[0]),
        "mae_interval_high_pips": float(mae_bounds[1]),
        "rmse_interval_low_pips": float(rmse_bounds[0]),
        "rmse_interval_high_pips": float(rmse_bounds[1]),
        "status": "computed_exploratory",
    })
    return result
