"""Resumable matched-population evaluation and additive diagnostic totals."""

import json
import shutil
from pathlib import Path

import numpy as np
import pandas as pd

from . import features as io
from .completion_data import eligible, frames, save_frame, verify_file
from .completion_models import predict
from .regimes import classify_regimes, METRIC_COLUMNS


MODELS = ["zero", "trailing", "ridge_price", "ridge_price_size"]
SUM_COLUMNS = [
    "rows", "abs_sum", "sq_sum", "actual_sum", "forecast_sum",
    "forecast_sq_sum", "forecast_actual_sum", "spread_sum",
    "abs_forecast_sum", "ratio_sum", "positive_spread_rows",
]


def diagnostic_totals(frame, actual, prediction, dimensions, h, model):
    error = prediction - actual
    spread = frame["spread_pips"].to_numpy(dtype=float)
    positive = spread > 0
    values = pd.DataFrame({
        "rows": np.ones(len(frame), dtype=np.int64),
        "abs_sum": np.abs(error), "sq_sum": error**2,
        "actual_sum": actual, "forecast_sum": prediction,
        "forecast_sq_sum": prediction**2,
        "forecast_actual_sum": prediction * actual,
        "spread_sum": spread,
        "abs_forecast_sum": np.abs(prediction),
        "ratio_sum": np.divide(
            np.abs(prediction), spread,
            out=np.zeros(len(frame)), where=positive,
        ),
        "positive_spread_rows": positive.astype(np.int64),
    })
    outputs = []
    for kind, groups in dimensions.items():
        values["group"] = np.asarray(groups).astype(str)
        grouped = values.groupby("group", sort=False)[SUM_COLUMNS].sum().reset_index()
        grouped["kind"], grouped["horizon_seconds"], grouped["model"] = kind, h, model
        outputs.append(grouped)
    return pd.concat(outputs, ignore_index=True)


def add_totals(accumulator, table):
    # Iterate over small aggregate tables, never over historical quotes.
    for row in table.itertuples(index=False):
        key = (row.kind, row.group, int(row.horizon_seconds), row.model)
        values = np.asarray([getattr(row, name) for name in SUM_COLUMNS], float)
        accumulator[key] = accumulator.get(key, np.zeros(len(SUM_COLUMNS))) + values


def totals_frame(accumulator):
    records = []
    for (kind, group, h, model), values in accumulator.items():
        records.append({
            "kind": kind, "group": group, "horizon_seconds": h, "model": model,
            **dict(zip(SUM_COLUMNS, values.tolist())),
        })
    return pd.DataFrame(records)


def metrics(table):
    out = table.copy()
    n = out["rows"]
    out["mae_pips"] = out["abs_sum"] / n
    out["rmse_pips"] = np.sqrt(out["sq_sum"] / n)
    out["mean_actual_pips"] = out["actual_sum"] / n
    out["mean_forecast_pips"] = out["forecast_sum"] / n
    out["forecast_bias_pips"] = out["mean_forecast_pips"] - out["mean_actual_pips"]
    denominator = out["forecast_sq_sum"] - out["forecast_sum"]**2 / n
    numerator = out["forecast_actual_sum"] - out["forecast_sum"] * out["actual_sum"] / n
    out["calibration_slope"] = numerator / denominator.where(denominator > 1e-15)
    out["calibration_intercept_pips"] = (
        out["mean_actual_pips"] - out["calibration_slope"] * out["mean_forecast_pips"]
    )
    out["mean_spread_pips"] = out["spread_sum"] / n
    out["mean_abs_forecast_pips"] = out["abs_forecast_sum"] / n
    out["mean_abs_forecast_over_positive_spread"] = (
        out["ratio_sum"] / out["positive_spread_rows"].where(
            out["positive_spread_rows"] > 0
        )
    )
    return out


def score_block(frame, fitted, fold, protocol):
    regime_columns = [
        "price_valid", "bid_size", "ask_size", "metric_lookback_seconds",
        "regime_metrics_complete", *METRIC_COLUMNS,
    ]
    regimes = classify_regimes(
        frame[regime_columns], fitted["thresholds"]
    )["liquidity_regime"]
    times = frame["feature_timestamp_utc"]
    sessions = np.select(
        [frame["session_overlap"], frame["london_session"], frame["new_york_session"]],
        ["overlap", "london_only", "new_york_only"], default="other",
    )
    ages = np.select(
        [frame["snapshot_staleness_seconds"].le(1),
         frame["snapshot_staleness_seconds"].le(5)],
        ["0_to_1s", "over_1_to_5s"], default="over_5_to_30s",
    )
    forecast = frame[[
        "feature_timestamp_utc", "source_timestamp_utc", "source_sha256",
        "source_row", "snapshot_staleness_seconds", "spread_pips",
    ]].copy()
    forecast["liquidity_regime"] = regimes
    forecast["session"] = sessions
    keep = np.zeros(len(frame), dtype=bool)
    tables, coverage = [], []

    for h in protocol["horizons_seconds"]:
        price, common = eligible(
            frame, h, fold["evaluation_start"], fold["evaluation_end"], protocol
        )
        mask = common.to_numpy()
        keep |= mask
        forecast[f"eligible_{h}s"] = mask
        coverage.append({
            "horizon_seconds": h,
            "clock_rows": len(frame),
            "price_valid_rows": int(frame["price_valid"].sum()),
            "price_eligible_rows": int(price.sum()),
            "matched_rows": int(common.sum()),
            "price_eligible_without_required_size": int((price & ~common).sum()),
        })
        selected = frame.loc[common]
        info = fitted["horizons"][str(h)]
        actual = selected[f"label_{h}s_pips"].to_numpy(dtype=float)
        predictions = {
            "zero": np.zeros(len(selected)),
            "trailing": selected[f"mid_change_{h}s_pips"].to_numpy(dtype=float),
            **{
                name: predict(model, selected)
                for name, model in info["models"].items()
            },
        }
        for name in [f"actual_{h}s", *[f"{m}_{h}s" for m in MODELS]]:
            forecast[name] = np.nan
        forecast.loc[common, f"actual_{h}s"] = actual

        if not len(selected):
            continue
        dimensions = {
            "date": times.loc[common].dt.strftime("%Y-%m-%d").to_numpy(),
            "year": times.loc[common].dt.year.to_numpy(),
            "session": sessions[mask],
            "regime": regimes.loc[common].to_numpy(),
            "snapshot_age": ages[mask],
            "endpoint": np.where(
                selected[f"same_source_record_{h}s"],
                "same_source_record", "different_source_record",
            ),
        }
        for name, prediction in predictions.items():
            forecast.loc[common, f"{name}_{h}s"] = prediction
            buckets = np.searchsorted(
                info["bucket_cuts"][name], prediction, side="right"
            ) + 1
            tables.append(diagnostic_totals(
                selected, actual, prediction,
                {**dimensions, "bucket": buckets}, h, name,
            ))

    statistics = (
        pd.concat(tables, ignore_index=True)
        if tables else pd.DataFrame(columns=[
            "group", *SUM_COLUMNS, "kind", "horizon_seconds", "model"
        ])
    )
    return forecast.loc[keep].reset_index(drop=True), statistics, coverage


def evaluate_fold(root, dataset, fitted, fold, protocol, run_id):
    root = Path(root)
    directory = root / "artifacts/manifests/completion" / run_id / fold["id"]
    output = root / "artifacts/forecasts/completion" / run_id / fold["id"]
    accumulator, coverage_totals = {}, {}
    committed, reused, forecast_rows, forecast_bytes = 0, 0, 0, 0

    for source_part, frame in frames(
        root, dataset, fold["evaluation_start"], fold["evaluation_end"]
    ):
        if shutil.disk_usage(root).free < protocol["minimum_free_gib_during_run"] * 1024**3:
            raise RuntimeError("Free disk space fell below the configured reserve.")
        key = pd.Timestamp(source_part["start"]).strftime("%Y%m%dT%H%M%S")
        checkpoint = directory / "evaluation_parts" / f"{key}.json"
        if checkpoint.exists():
            saved = json.loads(checkpoint.read_text(encoding="utf-8"))
            if (
                saved["source_sha256"] != source_part["sha256"]
                or saved["fold"] != fold
                or saved["run_id"] != run_id
            ):
                raise RuntimeError("Incompatible evaluation checkpoint.")
            verify_file(root, saved["forecast"])
            stats_path = verify_file(root, saved["statistics"])
            statistics = pd.read_parquet(stats_path)
            reused += 1
        else:
            forecasts, statistics, counts = score_block(frame, fitted, fold, protocol)
            saved = {
                "run_id": run_id, "fold": fold,
                "source_sha256": source_part["sha256"],
                "forecast": save_frame(root, forecasts, output / f"{key}.parquet"),
                "statistics": save_frame(
                    root, statistics, directory / "statistics" / f"{key}.parquet"
                ),
                "coverage": counts,
            }
            io.write_json(checkpoint, saved)
            del forecasts

        add_totals(accumulator, statistics)
        for row in saved["coverage"]:
            h = row["horizon_seconds"]
            total = coverage_totals.setdefault(h, {
                name: 0 for name in row if name != "horizon_seconds"
            })
            for name, value in row.items():
                if name != "horizon_seconds":
                    total[name] += value
        forecast_rows += saved["forecast"]["rows"]
        forecast_bytes += saved["forecast"]["bytes"]
        committed += 1
        if committed % 120 == 0:
            print(f"{fold['id']}: evaluation blocks verified/committed {committed:,}")

    table = totals_frame(accumulator)
    if table.empty:
        raise RuntimeError("Evaluation fold has no eligible observations.")

    for h, counts in coverage_totals.items():
        for model in MODELS:
            observed = table.loc[
                table["kind"].eq("date")
                & table["horizon_seconds"].eq(h)
                & table["model"].eq(model), "rows"
            ].sum()
            if int(observed) != counts["matched_rows"]:
                raise RuntimeError("Forecast population reconciliation failed.")

    records = table.to_dict(orient="records")
    result = {
        "run_id": run_id, "fold": fold, "status": "complete",
        "statistics": records,
        "coverage": [
            {"horizon_seconds": h, **counts}
            for h, counts in coverage_totals.items()
        ],
        "forecast_rows": forecast_rows, "forecast_bytes": forecast_bytes,
        "evaluation_blocks": committed, "evaluation_blocks_reused": reused,
    }
    io.write_json(directory / "evaluation.json", result)
    print(f"Completed {fold['id']}: {committed:,} blocks; {forecast_rows:,} saved forecast rows.")
    return result
