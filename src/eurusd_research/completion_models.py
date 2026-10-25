"""Training-only preprocessing and bounded sufficient-statistic Ridge fits."""

import numpy as np
import pandas as pd

from .completion_data import eligible, frames
from .regimes import METRIC_COLUMNS


def new_statistics(width):
    return {
        "n": 0, "mean": np.zeros(width), "y_mean": 0.0,
        "gram": np.zeros((width, width)), "cross": np.zeros(width),
    }


def update_statistics(state, x, y):
    if len(y) == 0:
        return
    x, y = np.asarray(x, float), np.asarray(y, float)
    n = len(y)
    mean, y_mean = x.mean(axis=0), float(y.mean())
    centered, centered_y = x - mean, y - y_mean
    gram, cross = centered.T @ centered, centered.T @ centered_y
    old = state["n"]
    if old:
        weight = old * n / (old + n)
        delta = mean - state["mean"]
        gram += np.outer(delta, delta) * weight
        cross += delta * (y_mean - state["y_mean"]) * weight
    state["gram"] += gram
    state["cross"] += cross
    state["mean"] += (mean - state["mean"]) * n / (old + n)
    state["y_mean"] += (y_mean - state["y_mean"]) * n / (old + n)
    state["n"] += n


def transform(values, fill):
    values = np.asarray(values, float)
    missing = ~np.isfinite(values)
    return np.column_stack([np.where(missing, fill, values), missing.astype(float)])


def finish_statistics(state, fill, indices, columns, alpha):
    indices = np.asarray(indices, dtype=int)
    variance = np.maximum(np.diag(state["gram"])[indices] / state["n"], 0)
    scale = np.sqrt(variance)
    scale[scale <= 1e-12] = 1.0
    gram = state["gram"][np.ix_(indices, indices)] / np.outer(scale, scale)
    gram.flat[::len(indices) + 1] += alpha
    beta = np.linalg.solve(gram, state["cross"][indices] / scale)
    if not np.isfinite(beta).all():
        raise RuntimeError("Nonfinite Ridge coefficients.")
    return {
        "columns": columns, "fill": np.asarray(fill).tolist(),
        "indices": indices.tolist(),
        "mean": state["mean"][indices].tolist(),
        "scale": scale.tolist(), "beta": beta.tolist(),
        "intercept": float(state["y_mean"]), "training_rows": state["n"],
        "alpha": alpha,
    }


def predict(model, frame):
    values = frame[model["columns"]].to_numpy(dtype=float)
    x = transform(values, np.asarray(model["fill"]))[:, model["indices"]]
    result = (
        (x - np.asarray(model["mean"])) / np.asarray(model["scale"])
    ) @ np.asarray(model["beta"]) + model["intercept"]
    if not np.isfinite(result).all():
        raise RuntimeError("Nonfinite Ridge forecast.")
    return result


def fit_fold(root, dataset, start, end, protocol):
    settings = protocol["model_settings"]
    columns = settings["price_features"] + settings["size_features"]
    width = len(columns)
    horizons = protocol["horizons_seconds"]
    sums = {h: np.zeros(width) for h in horizons}
    counts = {h: np.zeros(width, dtype=np.int64) for h in horizons}
    rows = {h: 0 for h in horizons}
    maximum_targets = {h: None for h in horizons}
    sample_parts = []
    maximum_frame_bytes = 0

    # First pass: exact finite-value means on each horizon's matched population.
    for _, frame in frames(root, dataset, start, end):
        maximum_frame_bytes = max(
            maximum_frame_bytes,
            int(frame.memory_usage(index=True, deep=True).sum()),
        )
        values = frame[columns].to_numpy(dtype=float)
        for h in horizons:
            _, common = eligible(frame, h, start, end, protocol)
            selected = values[common.to_numpy()]
            finite = np.isfinite(selected)
            sums[h] += np.where(finite, selected, 0).sum(axis=0)
            counts[h] += finite.sum(axis=0)
            rows[h] += len(selected)
            if common.any():
                target = frame.loc[common, f"target_timestamp_utc_{h}s"].max()
                maximum_targets[h] = max(
                    maximum_targets[h] or target, target
                )

        times = frame["feature_timestamp_utc"]
        sample_mask = times.dt.second.eq(0) & times.dt.minute.mod(5).eq(0)
        sample_parts.append(frame.loc[sample_mask].copy())

    if any(rows[h] < protocol["minimum_fit_rows"] for h in horizons):
        raise RuntimeError("Insufficient matched training observations.")

    sample = pd.concat(sample_parts, ignore_index=True)
    if len(sample) > protocol["maximum_training_sample_rows"]:
        raise RuntimeError("Training diagnostic sample exceeds its row guard.")
    del sample_parts

    fills = {
        h: np.divide(
            sums[h], counts[h], out=np.zeros(width), where=counts[h] > 0
        )
        for h in horizons
    }
    statistics = {h: new_statistics(width * 2) for h in horizons}

    # Second pass: stable centered covariance and cross-product accumulation.
    for _, frame in frames(root, dataset, start, end):
        values = frame[columns].to_numpy(dtype=float)
        for h in horizons:
            _, common = eligible(frame, h, start, end, protocol)
            update_statistics(
                statistics[h],
                transform(values[common.to_numpy()], fills[h]),
                frame.loc[common, f"label_{h}s_pips"].to_numpy(dtype=float),
            )

    training_metrics = sample.loc[sample["regime_metrics_complete"]]
    if len(training_metrics) < protocol["minimum_fit_rows"]:
        raise RuntimeError("Insufficient sampled training liquidity metrics.")
    low = protocol["regime_settings"]["low_quantile"]
    high = protocol["regime_settings"]["high_quantile"]
    thresholds = {
        "rule_version": "training_quantiles_five_minute_sample_v1",
        "settings": protocol["regime_settings"],
        "fit_start_utc": str(start), "fit_end_utc_exclusive": str(end),
        "training_metric_rows": len(training_metrics),
        "cutoffs": {
            "spread_high_pips": float(training_metrics[METRIC_COLUMNS[0]].quantile(high)),
            "volatility_high_pips": float(training_metrics[METRIC_COLUMNS[1]].quantile(high)),
            "depth_low_source_units": float(training_metrics[METRIC_COLUMNS[2]].quantile(low)),
            "activity_low_snapshots": float(training_metrics[METRIC_COLUMNS[3]].quantile(low)),
        },
    }

    result = {
        "training_start": str(start), "training_end_exclusive": str(end),
        "thresholds": thresholds, "horizons": {},
        "maximum_observed_input_frame_bytes": maximum_frame_bytes,
        "training_sample_rows": len(sample),
    }
    price_count = len(settings["price_features"])
    price_indices = list(range(price_count)) + list(
        range(width, width + price_count)
    )
    all_indices = list(range(width * 2))

    for h in horizons:
        fitted = {
            "ridge_price": finish_statistics(
                statistics[h], fills[h], price_indices, columns,
                settings["ridge_alpha"],
            ),
            "ridge_price_size": finish_statistics(
                statistics[h], fills[h], all_indices, columns,
                settings["ridge_alpha"],
            ),
        }
        _, common = eligible(sample, h, start, end, protocol)
        selected = sample.loc[common]
        if len(selected) < 100:
            raise RuntimeError("Too few training observations for forecast buckets.")
        cuts = {}
        for name, model in fitted.items():
            values = predict(model, selected)
            cuts[name] = np.unique(
                np.quantile(values, [.2, .4, .6, .8])
            ).tolist()
        cuts["zero"] = []
        cuts["trailing"] = np.unique(np.quantile(
            selected[f"mid_change_{h}s_pips"], [.2, .4, .6, .8]
        )).tolist()
        result["horizons"][str(h)] = {
            "models": fitted, "bucket_cuts": cuts,
            "training_rows": rows[h],
            "maximum_training_target_utc": maximum_targets[h].isoformat(),
            "all_missing_predictors": [
                name for name, count in zip(columns, counts[h]) if count == 0
            ],
        }
    return result
