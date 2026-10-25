"""Bounded full-research feature, label, and liquidity-metric generation."""

from bisect import bisect_left
import json
import shutil
import time
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from . import features as io
from .labels import make_forward_labels
from .regimes import build_liquidity_metrics
from .data import _sha256


def safe_path(root, relative):
    root = Path(root).resolve()
    path = (root / relative).resolve()
    if not path.is_relative_to(root):
        raise RuntimeError("An output path escapes the repository.")
    return path


def verify_file(root, record):
    path = safe_path(root, record["path"])
    if (
        not path.is_file()
        or path.stat().st_size != record["bytes"]
        or _sha256(path) != record["sha256"]
    ):
        raise RuntimeError(f"Artifact integrity failed: {path.name}")
    parquet = pq.ParquetFile(path)
    if (
        parquet.metadata.num_rows != record["rows"]
        or io._schema_hash(parquet.schema_arrow) != record["schema_sha256"]
    ):
        raise RuntimeError("Artifact schema or row count changed.")
    return path


def save_frame(root, frame, path):
    metadata = io._save_frame(frame, path)
    return {
        **metadata,
        "path": path.relative_to(root).as_posix(),
        "mtime_ns": path.stat().st_mtime_ns,
    }


def source_index(collection):
    return {
        "parts": collection["parts"],
        "starts": [pd.Timestamp(p["min_utc"]).value for p in collection["parts"]],
        "ends": [pd.Timestamp(p["max_utc"]).value for p in collection["parts"]],
    }


def read_quotes(root, collection, index, start, end, settings, work):
    context = start - pd.Timedelta(seconds=io.lookback_seconds(settings))
    first = bisect_left(index["ends"], context.value)
    stop = bisect_left(index["starts"], end.value)
    selected = index["parts"][max(0, first - 1):stop]
    if not selected:
        if stop == 0 and index["parts"]:
            # Before the first source observation, preserve an unavailable
            # clock interval using schema only. Do not read future quote values.
            first_path = safe_path(root, index["parts"][0]["path"])
            source_schema = pq.ParquetFile(first_path).schema_arrow
            empty_schema = pa.schema([
                source_schema.field(name) for name in io.QUOTE_COLUMNS
            ])
            return pa.Table.from_batches(
                [], schema=empty_schema
            ).to_pandas()
        raise RuntimeError("No source partitions available for the requested block.")
    files = [str(safe_path(root, item["path"])) for item in selected]
    connection = io._connect(files, "quotes", work, settings)
    try:
        return io._load_quotes(connection, start, end, settings)
    finally:
        connection.close()


def assemble_block(quotes, start, end, protocol, coverage_start):
    fs = protocol["feature_settings"]
    rs = protocol["regime_settings"]
    left = start - pd.Timedelta(seconds=rs["lookback_seconds"])
    right = end + pd.Timedelta(seconds=max(protocol["horizons_seconds"]))

    features = io.build_clock_features(
        quotes, left, right, fs, coverage_start=coverage_start
    )
    labels = make_forward_labels(
        features, start, end, protocol["horizons_seconds"],
        fs["pip_size"], fs["max_staleness_seconds"],
    )
    metrics = build_liquidity_metrics(features, start, end, rs)
    selected = features.loc[
        features["feature_timestamp_utc"].ge(start)
        & features["feature_timestamp_utc"].lt(end)
    ].reset_index(drop=True)

    if not selected["feature_timestamp_utc"].equals(
        labels["feature_timestamp_utc"]
    ) or not selected["feature_timestamp_utc"].equals(
        metrics["feature_timestamp_utc"]
    ):
        raise RuntimeError("Feature, label, and liquidity grids do not align.")

    out = pd.concat([
        selected,
        labels[[name for name in labels if name not in selected]],
        metrics[[name for name in metrics if name not in selected]],
    ], axis=1)

    future = out["source_timestamp_utc"].gt(out["feature_timestamp_utc"])
    stale = (
        out["price_valid"]
        & out["snapshot_staleness_seconds"].gt(fs["max_staleness_seconds"])
    )
    if future.any() or stale.any():
        raise RuntimeError("Current-state temporal correctness failed.")

    for h in protocol["horizons_seconds"]:
        valid = out[f"label_valid_{h}s"]
        expected = out["feature_timestamp_utc"] + pd.Timedelta(seconds=h)
        if not out[f"target_timestamp_utc_{h}s"].equals(expected):
            raise RuntimeError("A label has an incorrect target timestamp.")
        if (
            valid
            & out[f"target_source_timestamp_utc_{h}s"].gt(expected)
        ).any():
            raise RuntimeError("A target uses a future source quote.")

    measured = sum(
        int(frame.memory_usage(index=True, deep=True).sum())
        for frame in (quotes, features, labels, metrics, out)
    )
    if measured > protocol["maximum_observed_frame_mib"] * 1024**2:
        raise RuntimeError(
            "Block exceeds the dataframe memory guard. "
            "Reduce feature_settings.block_hours before a new run."
        )
    return out, measured


def make_dataset(root, collection, protocol, run_id):
    root = Path(root)
    start = pd.Timestamp(collection["identity"]["start_utc"])
    end = pd.Timestamp(protocol["final_test"]["end_utc_exclusive"])
    work = root / "artifacts/manifests/completion" / run_id
    output = root / "data/processed/completion" / run_id
    index = source_index(collection)
    blocks = io._blocks(start, end, protocol["feature_settings"]["block_hours"])
    records, reused = [], 0
    began = time.perf_counter()

    for number, (left, right) in enumerate(blocks, 1):
        if shutil.disk_usage(root).free < (
            protocol["minimum_free_gib_during_run"] * 1024**3
        ):
            raise RuntimeError("Free disk space fell below the configured reserve.")

        key = left.strftime("%Y%m%dT%H%M%S")
        metadata_path = work / "dataset_parts" / f"{key}.json"
        if metadata_path.exists():
            record = json.loads(metadata_path.read_text(encoding="utf-8"))
            if (
                record["run_id"] != run_id
                or record["start"] != left.isoformat()
                or record["end"] != right.isoformat()
            ):
                raise RuntimeError("Existing dataset block has incompatible metadata.")
            verify_file(root, record)
            reused += 1
        else:
            rs = protocol["regime_settings"]
            extended_start = left - pd.Timedelta(seconds=rs["lookback_seconds"])
            extended_end = right + pd.Timedelta(
                seconds=max(protocol["horizons_seconds"])
            )
            quotes = read_quotes(
                root, collection, index, extended_start, extended_end,
                protocol["feature_settings"], work / "reading",
            )
            frame, measured = assemble_block(
                quotes, left, right, protocol, collection["scope_utc_min"]
            )
            path = (
                output / f"year={left.year:04d}/month={left.month:02d}"
                / f"part-{key}.parquet"
            )
            record = {
                **save_frame(root, frame, path),
                "run_id": run_id,
                "start": left.isoformat(), "end": right.isoformat(),
                "price_valid_rows": int(frame["price_valid"].sum()),
                "size_valid_rows": int(frame["size_pair_valid"].sum()),
                "label_valid_rows": {
                    str(h): int(frame[f"label_valid_{h}s"].sum())
                    for h in protocol["horizons_seconds"]
                },
                "observed_frame_bytes": measured,
            }
            io.write_json(metadata_path, record)
            del quotes, frame

        if record["rows"] != int((right - left).total_seconds()):
            raise RuntimeError("Dataset block row conservation failed.")
        records.append(record)
        if number % 120 == 0 or number == len(blocks):
            print(f"Research blocks verified/committed: {number:,}/{len(blocks):,}")

    state = {
        "run_id": run_id, "status": "complete",
        "start": start.isoformat(), "end": end.isoformat(),
        "parts": records,
        "rows": sum(item["rows"] for item in records),
        "bytes": sum(item["bytes"] for item in records),
        "blocks_reused": reused,
        "runtime_seconds_this_execution": round(time.perf_counter() - began, 3),
    }
    if state["rows"] != int((end - start).total_seconds()):
        raise RuntimeError("Full dataset row conservation failed.")
    io.write_json(work / "dataset.json", state)
    return state


def frames(root, state, start, end, columns=None):
    """Yield bounded frames in chronological order."""
    start, end = pd.Timestamp(start), pd.Timestamp(end)
    for part in state["parts"]:
        if pd.Timestamp(part["end"]) <= start or pd.Timestamp(part["start"]) >= end:
            continue
        path = safe_path(root, part["path"])
        stamp = path.stat()
        if stamp.st_size != part["bytes"] or stamp.st_mtime_ns != part["mtime_ns"]:
            raise RuntimeError("A verified research partition changed.")
        frame = pq.ParquetFile(path).read(columns=columns).to_pandas()
        times = frame["feature_timestamp_utc"]
        yield part, frame.loc[times.ge(start) & times.lt(end)].reset_index(drop=True)


def eligible(frame, h, start, end, protocol):
    times = frame["feature_timestamp_utc"]
    target = frame[f"target_timestamp_utc_{h}s"]
    price = (
        times.ge(pd.Timestamp(start)) & times.lt(pd.Timestamp(end))
        & frame["price_valid"] & frame[f"label_valid_{h}s"]
        & target.eq(times + pd.Timedelta(seconds=h))
        & target.lt(pd.Timestamp(end))
        & np.isfinite(frame[f"label_{h}s_pips"])
        & np.isfinite(frame[f"mid_change_{h}s_pips"])
    )
    for interval in protocol["excluded_signal_and_label_intervals"]:
        price &= ~(
            times.lt(pd.Timestamp(interval["end"]))
            & target.ge(pd.Timestamp(interval["start"]))
        )
    required = protocol["model_settings"]["required_current_size_features"]
    size = frame["size_pair_valid"] & np.isfinite(
        frame[required].to_numpy(dtype=float)
    ).all(axis=1)
    return price, price & size
