"""Exact-horizon forward labels and bounded, resumable label storage."""

import hashlib
import json
import platform
from importlib.metadata import version
from pathlib import Path

import numpy as np
import pandas as pd

from . import features as io
from .data import _sha256, _writer_lock
from .validation import assign_split, utc, validate_plan


INPUT_COLUMNS = [
    "feature_timestamp_utc", "source_timestamp_utc",
    "source_file", "source_sha256", "source_row",
    "snapshot_staleness_seconds", "mid_price",
    "price_valid", "size_pair_valid", "price_path_break",
]


def make_forward_labels(frame, start, end, horizons, pip_size, max_age):
    start, end = utc(start), utc(end)
    if start >= end:
        raise ValueError("The output interval must have positive length.")
    if (
        not horizons
        or any(type(h) is not int or h < 1 for h in horizons)
        or len(set(horizons)) != len(horizons)
    ):
        raise ValueError("Horizons must be unique positive integer seconds.")
    if not np.isfinite(pip_size) or pip_size <= 0:
        raise ValueError("pip_size must be finite and positive.")
    if not np.isfinite(max_age) or max_age < 0:
        raise ValueError("max_age must be finite and nonnegative.")

    q = frame[INPUT_COLUMNS].copy().reset_index(drop=True)
    for name in ("feature_timestamp_utc", "source_timestamp_utc"):
        if not isinstance(q[name].dtype, pd.DatetimeTZDtype):
            raise ValueError(f"{name} must explicitly carry a timezone.")
        q[name] = q[name].dt.tz_convert("UTC").astype("datetime64[us, UTC]")
    for name in ("source_file", "source_sha256"):
        q[name] = q[name].astype("string")
    q["source_row"] = q["source_row"].astype("Int64")
    q["mid_price"] = q["mid_price"].astype("float64")

    for name in ("price_valid", "size_pair_valid", "price_path_break"):
        if q[name].isna().any() or not pd.api.types.is_bool_dtype(q[name]):
            raise ValueError(f"{name} must contain nonmissing booleans.")

    times = pd.DatetimeIndex(q["feature_timestamp_utc"])
    if (
        len(times) == 0 or times.hasnans or not times.is_unique
        or not times.is_monotonic_increasing
        or not times.to_series().diff().iloc[1:].eq(
            pd.Timedelta(seconds=1)
        ).all()
    ):
        raise ValueError("Input must be a complete, unique one-second UTC grid.")

    age = (q["feature_timestamp_utc"] - q["source_timestamp_utc"]).dt.total_seconds()
    if age.lt(0).any():
        raise ValueError("A future source snapshot entered the feature state.")
    if not np.allclose(
        age.to_numpy(dtype=float, na_value=np.nan),
        q["snapshot_staleness_seconds"].to_numpy(dtype=float, na_value=np.nan),
        equal_nan=True,
    ):
        raise ValueError("Stored snapshot ages disagree with source timestamps.")

    valid_state = (
        np.isfinite(q["mid_price"]) & q["mid_price"].gt(0)
        & age.between(0, max_age)
        & q["source_sha256"].notna() & q["source_row"].notna()
    )
    if (q["price_valid"] & ~valid_state).any():
        raise ValueError("A state marked price_valid violates the endpoint policy.")

    out = q.copy()
    prefix = np.r_[0, np.cumsum(q["price_path_break"].to_numpy(dtype=np.int64))]
    current_index = np.arange(len(q))

    for h in horizons:
        wanted = times + pd.Timedelta(seconds=h)
        positions = times.get_indexer(wanted)
        exists = positions >= 0
        target = q.reindex(positions).reset_index(drop=True)
        target_age = (
            pd.Series(wanted) - target["source_timestamp_utc"]
        ).dt.total_seconds()
        target_ok = (
            target["price_valid"].eq(True)
            & np.isfinite(target["mid_price"])
            & target["mid_price"].gt(0)
            & target_age.between(0, max_age)
        ).fillna(False)

        path_ok = np.zeros(len(q), dtype=bool)
        path_ok[exists] = (
            prefix[positions[exists] + 1] - prefix[current_index[exists] + 1]
        ) == 0
        change = (target["mid_price"] - q["mid_price"]) / pip_size
        valid = (
            q["price_valid"] & exists & target_ok
            & path_ok & np.isfinite(change)
        )

        suffix = f"{h}s"
        out[f"target_timestamp_utc_{suffix}"] = pd.Series(wanted)
        out[f"target_source_timestamp_utc_{suffix}"] = target["source_timestamp_utc"]
        out[f"target_source_sha256_{suffix}"] = target["source_sha256"].astype("string")
        out[f"target_source_row_{suffix}"] = target["source_row"].astype("Int64")
        out[f"target_snapshot_age_seconds_{suffix}"] = target_age
        out[f"target_mid_price_{suffix}"] = target["mid_price"]
        out[f"label_{suffix}_pips"] = change.where(valid)
        out[f"label_valid_{suffix}"] = valid.astype(bool)
        out[f"label_reason_{suffix}"] = pd.Series(np.select(
            [
                ~q["price_valid"].to_numpy(),
                ~exists,
                ~target_ok.to_numpy(dtype=bool),
                ~path_ok,
                ~np.isfinite(change.to_numpy()),
            ],
            [
                "invalid_feature_state", "missing_target",
                "invalid_target_state", "broken_price_path", "nonfinite_label",
            ],
            default="valid",
        ), dtype="string")
        out[f"same_source_record_{suffix}"] = (
            valid
            & q["source_sha256"].eq(target["source_sha256"])
            & q["source_row"].eq(target["source_row"])
        ).fillna(False).astype(bool)

    out = out.loc[
        out["feature_timestamp_utc"].ge(start)
        & out["feature_timestamp_utc"].lt(end)
    ].reset_index(drop=True)
    if len(out) != int((end - start).total_seconds()):
        raise ValueError("The requested output range is not fully represented.")
    return out



def _counts(series):
    return {str(key): int(value) for key, value in series.value_counts().items()}


def _statistics(frame, horizons):
    return {
        "rows": len(frame),
        "price_valid_rows": int(frame["price_valid"].sum()),
        "horizons": {
            str(h): {
                "valid_rows": int(frame[f"label_valid_{h}s"].sum()),
                "same_source_record_rows": int(frame[f"same_source_record_{h}s"].sum()),
                "reasons": _counts(frame[f"label_reason_{h}s"]),
                "splits": _counts(frame[f"split_{h}s"]),
            }
            for h in horizons
        },
    }


def build_label_dataset(feature_manifests, root, start, end, settings, plan):
    root = Path(root).resolve()
    start, end = utc(start), utc(end)
    validate_plan(plan)
    paths = [Path(path).resolve() for path in feature_manifests]
    if len(paths) != 2:
        raise ValueError("Expected the research and subsequent-context manifests.")
    sources = [json.loads(path.read_text(encoding="utf-8")) for path in paths]
    base, tail = sources
    horizons = settings["horizons_seconds"]
    feature_settings = base["identity"]["settings"]

    if base["job_id"] != plan["feature_job_id"]:
        raise ValueError("The split plan belongs to a different feature generation.")
    if tail["identity"]["settings"] != feature_settings:
        raise ValueError("Research and context feature policies differ.")
    base_start, base_end = (
        utc(base["identity"]["start_utc"]), utc(base["identity"]["end_utc"])
    )
    if not (
        base_start <= start < end <= base_end
        and utc(tail["identity"]["start_utc"]) == base_end
        and utc(tail["identity"]["end_utc"]) >= end + pd.Timedelta(seconds=max(horizons))
    ):
        raise ValueError("Research or future target coverage is insufficient.")

    for source in sources:
        if source["status"] != "complete":
            raise ValueError("Feature generations must be complete.")
        io._verify_feature_parts(source, io._blocks(
            source["identity"]["start_utc"], source["identity"]["end_utc"],
            source["identity"]["settings"]["block_hours"],
        ))

    hashes = [_sha256(path) for path in paths]
    implementation = hashlib.sha256(b"".join(
        Path(__file__).with_name(name).read_bytes()
        for name in ("labels.py", "validation.py", "features.py")
    )).hexdigest()
    identity = {
        "input_manifest_sha256": hashes,
        "input_job_ids": [source["job_id"] for source in sources],
        "start_utc": start.isoformat(), "end_utc": end.isoformat(),
        "settings": settings, "plan": plan,
        "implementation_sha256": implementation,
        "python": platform.python_version(),
        "versions": {
            name: version(name) for name in ("numpy", "pandas", "pyarrow", "duckdb")
        },
    }
    job_id = hashlib.sha256(
        json.dumps(identity, sort_keys=True).encode()
    ).hexdigest()[:24]
    output = root / "data/processed/labels" / job_id
    manifest_root = root / "artifacts/manifests/labels"
    manifest = manifest_root / f"job-{job_id}.json"
    blocks = io._blocks(start, end, settings["block_hours"])

    with _writer_lock(manifest_root):
        if manifest.exists():
            state = json.loads(manifest.read_text(encoding="utf-8"))
            if state["identity"] != identity:
                raise RuntimeError("Existing label generation is incompatible.")
            io._verify_feature_parts(state, blocks)
            if state["status"] == "complete":
                return {**state, "cache_hit": True}
        else:
            output.mkdir(parents=True, exist_ok=True)
            state = {
                "kind": "label_dataset", "job_id": job_id, "status": "running",
                "identity": identity,
                "input_manifests": [str(path) for path in paths],
                "output_dir": str(output), "manifest_path": str(manifest),
                "parts": [],
            }
            io.write_json(manifest, state)

        files = [
            str(Path(source["output_dir"]) / part["path"])
            for source in sources for part in source["parts"]
        ]
        connection = io._connect(files, "features", output, feature_settings)
        try:
            for number, (left, right) in enumerate(blocks):
                if number < len(state["parts"]):
                    continue
                future_end = right + pd.Timedelta(seconds=max(horizons))
                frame = connection.execute(f"""
                    SELECT {", ".join(INPUT_COLUMNS)} FROM features
                    WHERE feature_timestamp_utc >= CAST(? AS TIMESTAMPTZ)
                      AND feature_timestamp_utc < CAST(? AS TIMESTAMPTZ)
                    ORDER BY feature_timestamp_utc
                    LIMIT ?
                """, [
                    left.isoformat(), future_end.isoformat(),
                    settings["max_rows_per_block"] + 1,
                ]).fetchdf()
                if len(frame) > settings["max_rows_per_block"]:
                    raise RuntimeError("Label input exceeded its configured row limit.")
                if len(frame) != int((future_end - left).total_seconds()):
                    raise RuntimeError("A label block lacks complete target context.")

                result = make_forward_labels(
                    frame, left, right, horizons,
                    feature_settings["pip_size"],
                    feature_settings["max_staleness_seconds"],
                )
                for h in horizons:
                    result[f"split_{h}s"] = assign_split(result, plan, h)

                relative = (
                    f"year={left.year:04d}/month={left.month:02d}/"
                    f"part-{left.strftime('%Y%m%dT%H%M%S')}.parquet"
                )
                record = io._save_frame(result, output / relative)
                if state["parts"] and (
                    record["schema_sha256"] != state["parts"][0]["schema_sha256"]
                ):
                    raise RuntimeError("Label schema changed between blocks.")
                record.update({
                    "path": relative,
                    "start_utc": left.isoformat(), "end_utc": right.isoformat(),
                    "statistics": _statistics(result, horizons),
                    "input_rows_collected": len(frame),
                    "observed_frame_bytes": int(
                        frame.memory_usage(index=True, deep=True).sum()
                        + result.memory_usage(index=True, deep=True).sum()
                    ),
                })
                state["parts"].append(record)
                io.write_json(manifest, state)
                if (number + 1) % 8 == 0 or number + 1 == len(blocks):
                    print(f"Label blocks committed: {number + 1}/{len(blocks)}")
        finally:
            connection.close()

        if [_sha256(path) for path in paths] != hashes:
            raise RuntimeError("An input manifest changed during label construction.")
        for source in sources:
            io._verify_feature_parts(source, io._blocks(
                source["identity"]["start_utc"], source["identity"]["end_utc"],
                source["identity"]["settings"]["block_hours"],
            ))
        state["status"] = "complete"
        io._verify_feature_parts(state, blocks)
        io.write_json(manifest, state)
        return {**state, "cache_hit": False}


def load_label_rows(state, start, end, feature_settings, maximum_rows=100000):
    files = [
        str(Path(state["output_dir"]) / part["path"]) for part in state["parts"]
    ]
    connection = io._connect(files, "labels", state["output_dir"], feature_settings)
    try:
        frame = connection.execute("""
            SELECT * FROM labels
            WHERE feature_timestamp_utc >= CAST(? AS TIMESTAMPTZ)
              AND feature_timestamp_utc < CAST(? AS TIMESTAMPTZ)
            ORDER BY feature_timestamp_utc LIMIT ?
        """, [
            utc(start).isoformat(), utc(end).isoformat(), maximum_rows + 1
        ]).fetchdf()
        if len(frame) > maximum_rows:
            raise RuntimeError("Requested label excerpt exceeds its row limit.")
        for name in frame:
            if "timestamp_utc" in name:
                frame[name] = pd.to_datetime(frame[name], utc=True).astype(
                    "datetime64[us, UTC]"
                )
        return frame
    finally:
        connection.close()
