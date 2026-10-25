"""Bounded, resumable full-source coverage and quote-quality auditing."""

import hashlib
import json
import platform
from importlib.metadata import version
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.csv as arrow_csv

from . import features as io
from .data import _sha256, _stamp, _writer_lock
from .quality import EXPECTED_COLUMNS, normalise_quotes


COUNT_COLUMNS = [
    "quote_rows", "price_eligible_quote_rows", "size_eligible_quote_rows",
    "invalid_price_rows", "crossed_quote_rows", "locked_quote_rows",
    "missing_size_rows", "invalid_size_rows", "negative_size_rows",
    "zero_size_rows", "wide_spread_rows",
    "adjacent_exact_export_duplicate_rows",
    "source_order_backsteps", "gaps_above_threshold",
]
GAP_LABELS = [
    "0", "(0,1]", "(1,5]", "(5,30]", "(30,60]",
    "(60,300]", "(300,3600]", "(3600,86400]", ">86400",
]


def new_audit(source_name, source_sha256):
    return {
        "source_name": source_name, "source_sha256": source_sha256,
        "rows_scanned": 0, "valid_timestamp_rows": 0,
        "unassigned_timestamp_rows": 0,
        "price_eligible_quote_rows": 0, "size_eligible_quote_rows": 0,
        "next_batch": 0, "previous_utc": None, "previous_raw": None,
        "previous_valid_utc": None, "previous_valid_row": None,
        "utc_min": None, "utc_max": None,
        "first_record": None, "last_record": None,
        "flags": {}, "daily": {},
        "adjacent_exact_export_duplicate_rows": 0,
        "source_order_backsteps": 0, "repeated_valid_timestamp_steps": 0,
        "gaps_above_threshold": 0, "gaps_crossing_unassigned_rows": 0,
        "maximum_source_sequence_gap_seconds": 0.0,
        "gap_histogram": [0] * len(GAP_LABELS),
        "largest_gaps": [], "unassigned_examples": [],
        "maximum_observed_dataframe_bytes": 0,
    }


def _time_bound(mapping, key, candidate, minimum):
    if pd.isna(candidate):
        return
    candidate = pd.Timestamp(candidate)
    existing = mapping.get(key)
    if existing is None or (
        candidate < pd.Timestamp(existing) if minimum
        else candidate > pd.Timestamp(existing)
    ):
        mapping[key] = candidate.isoformat()


def _number_bound(mapping, key, candidate, minimum):
    if pd.isna(candidate):
        return
    candidate = float(candidate)
    existing = mapping.get(key)
    if existing is None or (
        candidate < existing if minimum else candidate > existing
    ):
        mapping[key] = candidate


def consume_rows(state, raw, parameters):
    raw = raw.reset_index(drop=True).fillna("")
    if raw.empty:
        return
    frame = normalise_quotes(
        raw,
        source_name=state["source_name"],
        source_sha256=state["source_sha256"],
        first_row=state["rows_scanned"] + 1,
        previous_utc=state["previous_utc"],
        **{
            key: parameters[key] for key in (
                "timezone", "timestamp_format", "pip_size",
                "gap_seconds", "extreme_spread_pips",
            )
        },
    )
    observed_bytes = int(
        raw.memory_usage(index=True, deep=True).sum()
        + frame.memory_usage(index=True, deep=True).sum()
    )
    if observed_bytes > parameters["maximum_dataframe_mib"] * 1024**2:
        raise RuntimeError("Audit dataframes exceed their configured memory guard.")
    state["maximum_observed_dataframe_bytes"] = max(
        state["maximum_observed_dataframe_bytes"], observed_bytes
    )

    flag_names = [name for name in frame if name.startswith("flag_")]
    for name in flag_names:
        state["flags"][name] = state["flags"].get(name, 0) + int(frame[name].sum())

    duplicate = raw.eq(raw.shift()).all(axis=1)
    if state["previous_raw"] is not None:
        duplicate.iloc[0] = raw.iloc[0].tolist() == state["previous_raw"]

    times = frame["timestamp_utc"]
    assigned = times.notna()
    price = assigned & ~frame["flag_invalid_price"] & ~frame["flag_crossed"]
    total_size = frame["ask_size"] + frame["bid_size"]
    size = (
        price & np.isfinite(frame["ask_size"]) & np.isfinite(frame["bid_size"])
        & frame["ask_size"].ge(0) & frame["bid_size"].ge(0)
        & np.isfinite(total_size) & total_size.gt(0)
    )

    valid_times = times.loc[assigned]
    valid_rows = frame.loc[assigned, "source_row"]
    previous_times = valid_times.shift()
    previous_rows = valid_rows.shift()
    if len(valid_times) and state["previous_valid_utc"] is not None:
        previous_times.iloc[0] = pd.Timestamp(state["previous_valid_utc"])
        previous_rows.iloc[0] = state["previous_valid_row"]
    differences = (valid_times - previous_times).dt.total_seconds()
    gap = differences.gt(parameters["gap_seconds"])
    backstep = differences.lt(0)

    nonnegative = differences.loc[differences.ge(0)].to_numpy(dtype=float)
    bins = np.searchsorted(
        [0, 1, 5, 30, 60, 300, 3600, 86400], nonnegative, side="left"
    )
    histogram = np.bincount(bins, minlength=len(GAP_LABELS))
    state["gap_histogram"] = [
        int(a + b) for a, b in zip(state["gap_histogram"], histogram)
    ]
    state["source_order_backsteps"] += int(backstep.sum())
    state["repeated_valid_timestamp_steps"] += int(differences.eq(0).sum())
    state["gaps_above_threshold"] += int(gap.sum())
    crossing_unassigned = valid_rows.sub(previous_rows).gt(1)
    state["gaps_crossing_unassigned_rows"] += int((gap & crossing_unassigned).sum())
    if len(nonnegative):
        state["maximum_source_sequence_gap_seconds"] = max(
            state["maximum_source_sequence_gap_seconds"], float(nonnegative.max())
        )

    # Only bounded diagnostic examples are iterated.
    largest = differences.loc[gap].nlargest(parameters["max_gap_examples"])
    for index, seconds in largest.items():
        state["largest_gaps"].append({
            "start_utc": previous_times.loc[index].isoformat(),
            "end_utc": valid_times.loc[index].isoformat(),
            "start_source_row": int(previous_rows.loc[index]),
            "end_source_row": int(valid_rows.loc[index]),
            "seconds": float(seconds),
            "crosses_unassigned_rows": bool(crossing_unassigned.loc[index]),
        })
    state["largest_gaps"] = sorted(
        state["largest_gaps"],
        key=lambda item: (-item["seconds"], item["end_source_row"]),
    )[:parameters["max_gap_examples"]]

    remaining = parameters["max_unassigned_examples"] - len(state["unassigned_examples"])
    for index in frame.index[~assigned][:max(0, remaining)]:
        state["unassigned_examples"].append({
            "source_row": int(frame.loc[index, "source_row"]),
            "raw_fields": raw.loc[index].tolist(),
            "reasons": [
                name for name in (
                    "flag_missing_timestamp", "flag_unparseable_timestamp",
                    "flag_unresolved_local_time",
                ) if bool(frame.loc[index, name])
            ],
        })

    data = pd.DataFrame({
        "date_utc": times.dt.floor("D"),
        "timestamp_utc": times,
        "quote_rows": np.ones(len(frame), dtype=np.int64),
        "price_eligible_quote_rows": price,
        "size_eligible_quote_rows": size,
        "invalid_price_rows": frame["flag_invalid_price"],
        "crossed_quote_rows": frame["flag_crossed"],
        "locked_quote_rows": frame["flag_locked"],
        "missing_size_rows": frame["flag_missing_size"],
        "invalid_size_rows": frame["flag_invalid_size"],
        "negative_size_rows": frame["flag_negative_size"],
        "zero_size_rows": frame["flag_zero_size"],
        "wide_spread_rows": frame["flag_extreme_spread"],
        "adjacent_exact_export_duplicate_rows": duplicate,
        "source_order_backsteps": backstep.reindex(frame.index, fill_value=False),
        "gaps_above_threshold": gap.reindex(frame.index, fill_value=False),
        "spread_pips": frame["spread_pips"],
        "gap_seconds": differences.where(differences.ge(0)).reindex(frame.index),
    }).dropna(subset=["date_utc"])

    aggregates = {
        name: (name, "sum") for name in COUNT_COLUMNS
    }
    aggregates.update({
        "first_quote_utc": ("timestamp_utc", "min"),
        "last_quote_utc": ("timestamp_utc", "max"),
        "min_spread_pips": ("spread_pips", "min"),
        "max_spread_pips": ("spread_pips", "max"),
        "max_gap_seconds": ("gap_seconds", "max"),
    })
    grouped = data.groupby("date_utc", sort=False).agg(**aggregates).reset_index()
    for record in grouped.to_dict(orient="records"):
        date = record["date_utc"].date().isoformat()
        day = state["daily"].setdefault(date, {
            "date_utc": date,
            **{name: 0 for name in COUNT_COLUMNS},
            "first_quote_utc": None, "last_quote_utc": None,
            "min_spread_pips": None, "max_spread_pips": None,
            "max_gap_seconds": None,
        })
        for name in COUNT_COLUMNS:
            day[name] += int(record[name])
        _time_bound(day, "first_quote_utc", record["first_quote_utc"], True)
        _time_bound(day, "last_quote_utc", record["last_quote_utc"], False)
        _number_bound(day, "min_spread_pips", record["min_spread_pips"], True)
        _number_bound(day, "max_spread_pips", record["max_spread_pips"], False)
        _number_bound(day, "max_gap_seconds", record["max_gap_seconds"], False)

    if len(valid_times):
        _time_bound(state, "utc_min", valid_times.min(), True)
        _time_bound(state, "utc_max", valid_times.max(), False)
        state["previous_valid_utc"] = valid_times.iloc[-1].isoformat()
        state["previous_valid_row"] = int(valid_rows.iloc[-1])

    def edge(index):
        timestamp = times.iloc[index]
        return {
            "source_row": int(frame["source_row"].iloc[index]),
            "timestamp_utc": None if pd.isna(timestamp) else timestamp.isoformat(),
            "raw_fields": raw.iloc[index].tolist(),
        }

    if state["first_record"] is None:
        state["first_record"] = edge(0)
    state["last_record"] = edge(-1)
    state["previous_raw"] = raw.iloc[-1].tolist()
    state["previous_utc"] = None if pd.isna(times.iloc[-1]) else times.iloc[-1].isoformat()
    state["rows_scanned"] += len(frame)
    state["valid_timestamp_rows"] += int(assigned.sum())
    state["unassigned_timestamp_rows"] += int((~assigned).sum())
    state["price_eligible_quote_rows"] += int(price.sum())
    state["size_eligible_quote_rows"] += int(size.sum())
    state["adjacent_exact_export_duplicate_rows"] += int(duplicate.sum())
    state["next_batch"] += 1



def daily_frame(state):
    columns = [
        "date_utc", *COUNT_COLUMNS,
        "first_quote_utc", "last_quote_utc",
        "min_spread_pips", "max_spread_pips", "max_gap_seconds",
    ]
    frame = pd.DataFrame(list(state["daily"].values()), columns=columns)
    for name in ("date_utc", "first_quote_utc", "last_quote_utc"):
        frame[name] = pd.to_datetime(
            frame[name], format="ISO8601", utc=True
        ).astype("datetime64[us, UTC]")
    for name in COUNT_COLUMNS:
        frame[name] = frame[name].astype("int64")
    for name in ("min_spread_pips", "max_spread_pips", "max_gap_seconds"):
        frame[name] = frame[name].astype("float64")
    frame["source_file"] = pd.Series(
        state["source_name"], index=frame.index, dtype="string"
    )
    return frame.sort_values("date_utc").reset_index(drop=True)


def _verify_completed(state, root):
    path = (root / state["daily_output"]["path"]).resolve()
    if (
        not path.is_relative_to(root) or not path.is_file()
        or _sha256(path) != state["daily_output"]["sha256"]
    ):
        raise RuntimeError("Daily audit output checksum failed.")
    daily = pd.read_parquet(path)
    if (
        len(daily) != state["daily_output"]["rows"]
        or int(daily["quote_rows"].sum()) != state["valid_timestamp_rows"]
        or state["rows_scanned"] != (
            state["valid_timestamp_rows"] + state["unassigned_timestamp_rows"]
        )
        or int(daily["price_eligible_quote_rows"].sum())
            != state["price_eligible_quote_rows"]
    ):
        raise RuntimeError("Source audit row conservation failed.")
    unassigned_flags = sum(state["flags"].get(name, 0) for name in (
        "flag_missing_timestamp", "flag_unparseable_timestamp",
        "flag_unresolved_local_time",
    ))
    if unassigned_flags != state["unassigned_timestamp_rows"]:
        raise RuntimeError("Unassigned timestamp flags do not reconcile.")


def _register_source(manifest_root, state):
    key = hashlib.sha256(state["source_name"].encode()).hexdigest()[:24]
    path = manifest_root / f"source-{key}.json"
    registry = (
        json.loads(path.read_text(encoding="utf-8")) if path.exists()
        else {"source_name": state["source_name"], "completed_audits": []}
    )
    entry = {
        "job_id": state["job_id"],
        "source_sha256": state["source_sha256"],
    }
    if entry not in registry["completed_audits"]:
        registry["completed_audits"].append(entry)
        io.write_json(path, registry)
    return len({
        item["source_sha256"] for item in registry["completed_audits"]
    })


def audit_source(source_path, repository_root, parameters):
    source = Path(source_path).resolve()
    root = Path(repository_root).resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    if (
        parameters["block_bytes"] < 128
        or parameters["checkpoint_batches"] < 1
        or parameters["progress_batches"] < 1
        or parameters["max_gap_examples"] < 1
        or parameters["max_unassigned_examples"] < 0
    ):
        raise ValueError("Invalid audit resource settings.")

    stamp = _stamp(source)
    source_hash = _sha256(source)
    implementation = hashlib.sha256(b"".join(
        Path(__file__).with_name(name).read_bytes()
        for name in ("coverage_audit.py", "quality.py", "data.py", "features.py")
    )).hexdigest()
    identity = {
        "source_name": source.name,
        "source_sha256": source_hash,
        "source_bytes": stamp[0],
        "parameters": parameters,
        "implementation_sha256": implementation,
        "python": platform.python_version(),
        "versions": {
            name: version(name)
            for name in ("numpy", "pandas", "pyarrow", "tzdata", "pytz")
        },
    }
    job_id = hashlib.sha256(
        json.dumps(identity, sort_keys=True).encode()
    ).hexdigest()[:24]
    manifest_root = root / "artifacts/manifests/coverage_audit"
    manifest = manifest_root / f"job-{job_id}.json"

    with _writer_lock(manifest_root):
        if manifest.exists():
            state = json.loads(manifest.read_text(encoding="utf-8"))
            if state["identity"] != identity:
                raise RuntimeError("Existing source audit identity differs.")
            if state["status"] == "complete":
                _verify_completed(state, root)
                if _stamp(source) != stamp:
                    raise RuntimeError("Source changed while validating its cached audit.")
                versions_seen = _register_source(manifest_root, state)
                return {
                    **state, "cache_hit": True,
                    "recorded_source_content_versions": versions_seen,
                }
        else:
            state = {
                **new_audit(source.name, source_hash),
                "job_id": job_id, "status": "running", "identity": identity,
                "manifest_path": manifest.relative_to(root).as_posix(),
            }
            io.write_json(manifest, state)

        committed_batches = state["next_batch"]
        row_offset = 0
        with source.open("rb") as handle:
            if handle.read(3) != b"\xef\xbb\xbf":
                handle.seek(0)
            reader = arrow_csv.open_csv(
                handle,
                read_options=arrow_csv.ReadOptions(
                    block_size=parameters["block_bytes"], use_threads=False
                ),
                parse_options=arrow_csv.ParseOptions(
                    delimiter=",", newlines_in_values=True,
                    ignore_empty_lines=False,
                ),
                convert_options=arrow_csv.ConvertOptions(
                    column_types={name: pa.string() for name in EXPECTED_COLUMNS},
                    strings_can_be_null=False, check_utf8=True,
                ),
            )
            try:
                if reader.schema.names != EXPECTED_COLUMNS:
                    raise ValueError(f"Unexpected source schema: {reader.schema.names}")
                for number, batch in enumerate(reader):
                    if number < committed_batches:
                        row_offset += batch.num_rows
                        if number + 1 == committed_batches and row_offset != state["rows_scanned"]:
                            raise RuntimeError("Checkpoint replay row count differs.")
                        continue
                    if number != state["next_batch"] or row_offset != state["rows_scanned"]:
                        raise RuntimeError("Audit checkpoint position differs.")

                    consume_rows(state, batch.to_pandas(), parameters)
                    row_offset += batch.num_rows

                    if state["next_batch"] % parameters["checkpoint_batches"] == 0:
                        io.write_json(manifest, state)
                    if state["next_batch"] % parameters["progress_batches"] == 0:
                        print(f"{source.name}: {state['rows_scanned']:,} rows audited")
            finally:
                reader.close()

        if row_offset != state["rows_scanned"] or state["rows_scanned"] == 0:
            raise RuntimeError("Final audit row count is invalid.")
        if _stamp(source) != stamp or _sha256(source) != source_hash:
            raise RuntimeError("Source changed during auditing; review before resuming.")

        daily = daily_frame(state)
        output = root / "data/interim/coverage_audit" / job_id / "daily.parquet"
        record = io._save_frame(daily, output)
        state["daily_output"] = {
            **record, "path": output.relative_to(root).as_posix()
        }
        state["status"] = "complete"
        _verify_completed(state, root)
        io.write_json(manifest, state)
        versions_seen = _register_source(manifest_root, state)
        return {
            **state, "cache_hit": False,
            "recorded_source_content_versions": versions_seen,
        }


def source_overlap_ranges(states):
    records = []
    usable = [
        state for state in states
        if state["utc_min"] is not None and state["utc_max"] is not None
    ]
    for i, left in enumerate(usable):
        for right in usable[i + 1:]:
            start = max(pd.Timestamp(left["utc_min"]), pd.Timestamp(right["utc_min"]))
            end = min(pd.Timestamp(left["utc_max"]), pd.Timestamp(right["utc_max"]))
            if start <= end:
                records.append({
                    "left_source": left["source_name"],
                    "right_source": right["source_name"],
                    "overlap_start_utc": start.isoformat(),
                    "overlap_end_utc": end.isoformat(),
                    "overlap_seconds": float((end - start).total_seconds()),
                    "shared_boundary_timestamp_only": start == end,
                    "identical_records_proven": False,
                })
    return records
