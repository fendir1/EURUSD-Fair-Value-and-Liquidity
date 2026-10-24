"""Bounded CSV ingestion, integrity manifests, and dataset diagnostics."""

import hashlib
import json
import os
import platform
import time
from contextlib import contextmanager
from importlib.metadata import version
from pathlib import Path

import duckdb
import pandas as pd
import pyarrow as pa
import pyarrow.csv as arrow_csv
import pyarrow.parquet as pq

from .quality import EXPECTED_COLUMNS, normalise_quotes


def _stamp(path):
    stat = Path(path).stat()
    return stat.st_size, stat.st_mtime_ns


def _sha256(path):
    before = _stamp(path)
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for block in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(block)
    if _stamp(path) != before:
        raise RuntimeError(f"File changed while hashing: {path}")
    return digest.hexdigest()


def _save_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(value, handle, indent=2, sort_keys=True, allow_nan=False)
        handle.write("\n")
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)


@contextmanager
def _writer_lock(directory):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "writer.lock").open("a+b") as handle:
        handle.seek(0, 2)
        if handle.tell() == 0:
            handle.write(b"0")
            handle.flush()
        handle.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise RuntimeError("Another ingestion writer is active.") from error

        try:
            yield
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _write_part(frame, output_dir, relative, kind):
    target = output_dir / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    temporary = target.with_name(target.name + ".tmp")
    table = pa.Table.from_pandas(frame, preserve_index=False)
    pq.write_table(table, temporary, compression="zstd")
    os.replace(temporary, target)
    return {
        "path": relative,
        "kind": kind,
        "rows": len(frame),
        "bytes": target.stat().st_size,
        "sha256": _sha256(target),
    }


def _verify_parts(state):
    totals = {"quotes": 0, "unassigned": 0}
    root = Path(state["output_dir"]).resolve()

    for part in state["parts"]:
        path = (root / part["path"]).resolve()
        if not path.is_relative_to(root):
            raise RuntimeError("Manifest output path escapes its generation.")
        if not path.is_file() or _sha256(path) != part["sha256"]:
            raise RuntimeError(f"Output checksum verification failed: {path}")
        if pq.ParquetFile(path).metadata.num_rows != part["rows"]:
            raise RuntimeError(f"Output row count changed: {path}")
        totals[part["kind"]] += part["rows"]

    if totals["quotes"] != state["rows_in_scope"]:
        raise RuntimeError("Canonical output row counts do not reconcile.")
    if totals["unassigned"] != state["rows_unassigned"]:
        raise RuntimeError("Unassigned output row counts do not reconcile.")


def _update_bounds(state, prefix, timestamps):
    valid = timestamps.dropna()
    if valid.empty:
        return
    for suffix, candidate, choose in (
        ("min", valid.min(), min),
        ("max", valid.max(), max),
    ):
        key = f"{prefix}_{suffix}"
        existing = state.get(key)
        value = candidate if existing is None else choose(
            candidate, pd.Timestamp(existing)
        )
        state[key] = value.isoformat()


def ingest_quotes(
    source_path,
    data_root,
    manifest_root,
    start,
    end,
    *,
    timezone,
    timestamp_format,
    pip_size,
    gap_seconds,
    extreme_spread_pips,
    block_bytes,
):
    source = Path(source_path).resolve()
    data_root = Path(data_root).resolve()
    manifest_root = Path(manifest_root).resolve()

    if not source.is_file():
        raise FileNotFoundError(source)

    start, end = pd.Timestamp(start), pd.Timestamp(end)
    if start.tzinfo is None or end.tzinfo is None:
        raise ValueError("Range boundaries must include a timezone.")
    start, end = start.tz_convert("UTC"), end.tz_convert("UTC")
    if start >= end or block_bytes < 128:
        raise ValueError("Invalid time range or CSV block size.")

    source_stamp = _stamp(source)
    source_hash = _sha256(source)

    implementation = hashlib.sha256(
        Path(__file__).read_bytes()
        + Path(__file__).with_name("quality.py").read_bytes()
    ).hexdigest()

    parameters = {
        "timezone": timezone,
        "timestamp_format": timestamp_format,
        "pip_size": pip_size,
        "gap_seconds": gap_seconds,
        "extreme_spread_pips": extreme_spread_pips,
        "block_bytes": block_bytes,
    }
    identity = {
        "source_path": os.path.normcase(str(source)),
        "source_sha256": source_hash,
        "source_bytes": source_stamp[0],
        "start_utc": start.isoformat(),
        "end_utc": end.isoformat(),
        "parameters": parameters,
        "implementation_sha256": implementation,
        "python": platform.python_version(),
        "versions": {
            name: version(name)
            for name in ("numpy", "pandas", "pyarrow", "duckdb", "tzdata")
        },
    }
    job_id = hashlib.sha256(
        json.dumps(identity, sort_keys=True).encode("utf-8")
    ).hexdigest()[:24]

    output_dir = data_root / job_id
    manifest_path = manifest_root / f"job-{job_id}.json"
    registry_key = hashlib.sha256(
        os.path.normcase(str(source)).encode("utf-8")
    ).hexdigest()[:24]
    registry_path = manifest_root / f"source-{registry_key}.json"

    with _writer_lock(manifest_root):
        if registry_path.exists():
            registered = json.loads(registry_path.read_text(encoding="utf-8"))
            if registered["source_sha256"] != source_hash:
                raise ValueError(
                    "Source content changed. Stop and review the replacement "
                    "before combining it with previous research outputs."
                )
        else:
            _save_json(registry_path, {
                "source_path": str(source),
                "source_sha256": source_hash,
                "source_bytes": source_stamp[0],
            })

        if manifest_path.exists():
            state = json.loads(manifest_path.read_text(encoding="utf-8"))
            if state["identity"] != identity:
                raise RuntimeError("Existing manifest identity is incompatible.")
            _verify_parts(state)
            if state["status"] == "complete":
                return {**state, "cache_hit": True}
        else:
            output_dir.mkdir(parents=True, exist_ok=True)
            state = {
                "job_id": job_id,
                "identity": identity,
                "manifest_path": str(manifest_path),
                "output_dir": str(output_dir),
                "status": "running",
                "next_batch": 0,
                "rows_scanned": 0,
                "rows_in_scope": 0,
                "rows_unassigned": 0,
                "rows_outside_scope": 0,
                "previous_utc": None,
                "source_flags": {},
                "scope_flags": {},
                "parts": [],
                "maximum_observed_batch_bytes": 0,
            }
            _save_json(manifest_path, state)

        committed_batches = state["next_batch"]
        row_offset = 0

        with source.open("rb") as handle:
            if handle.read(3) != b"\xef\xbb\xbf":
                handle.seek(0)

            reader = arrow_csv.open_csv(
                handle,
                read_options=arrow_csv.ReadOptions(
                    block_size=block_bytes,
                    use_threads=False,
                ),
                parse_options=arrow_csv.ParseOptions(
                    delimiter=",",
                    newlines_in_values=True,
                    ignore_empty_lines=False,
                ),
                convert_options=arrow_csv.ConvertOptions(
                    column_types={name: pa.string() for name in EXPECTED_COLUMNS},
                    strings_can_be_null=False,
                    check_utf8=True,
                ),
            )
            try:
                if reader.schema.names != EXPECTED_COLUMNS:
                    raise ValueError(
                        f"Unexpected source header: {reader.schema.names}"
                    )

                for batch_number, batch in enumerate(reader):
                    if batch_number < committed_batches:
                        row_offset += batch.num_rows
                        if (
                            batch_number + 1 == committed_batches
                            and row_offset != state["rows_scanned"]
                        ):
                            raise RuntimeError("Checkpoint replay row mismatch.")
                        continue

                    if (
                        batch_number != state["next_batch"]
                        or row_offset != state["rows_scanned"]
                    ):
                        raise RuntimeError("Checkpoint position mismatch.")

                    raw = batch.to_pandas()
                    frame = normalise_quotes(
                        raw,
                        source_name=source.name,
                        source_sha256=source_hash,
                        first_row=row_offset + 1,
                        previous_utc=state["previous_utc"],
                        **{key: value for key, value in parameters.items()
                           if key != "block_bytes"},
                    )

                    unassigned = frame["timestamp_utc"].isna()
                    in_scope = (
                        frame["timestamp_utc"].ge(start)
                        & frame["timestamp_utc"].lt(end)
                    )
                    selected = frame.loc[in_scope]
                    new_parts = []

                    if not selected.empty:
                        years = selected["timestamp_utc"].dt.year
                        months = selected["timestamp_utc"].dt.month
                        for (year, month), group in selected.groupby(
                            [years, months], sort=True
                        ):
                            relative = (
                                f"quotes/year={int(year):04d}/"
                                f"month={int(month):02d}/"
                                f"part-{batch_number:07d}.parquet"
                            )
                            new_parts.append(
                                _write_part(group, output_dir, relative, "quotes")
                            )

                    if unassigned.any():
                        relative = (
                            f"unassigned/part-{batch_number:07d}.parquet"
                        )
                        new_parts.append(_write_part(
                            frame.loc[unassigned],
                            output_dir,
                            relative,
                            "unassigned",
                        ))

                    flags = [name for name in frame if name.startswith("flag_")]
                    for key, subset in (
                        ("source_flags", frame),
                        ("scope_flags", selected),
                    ):
                        for name, count in subset[flags].sum().items():
                            state[key][name] = (
                                state[key].get(name, 0) + int(count)
                            )

                    state["parts"].extend(new_parts)
                    state["rows_scanned"] += len(frame)
                    state["rows_in_scope"] += len(selected)
                    state["rows_unassigned"] += int(unassigned.sum())
                    state["rows_outside_scope"] += (
                        len(frame) - len(selected) - int(unassigned.sum())
                    )
                    _update_bounds(state, "source_utc", frame["timestamp_utc"])
                    _update_bounds(state, "scope_utc", selected["timestamp_utc"])

                    if len(frame):
                        last = frame["timestamp_utc"].iloc[-1]
                        state["previous_utc"] = (
                            None if pd.isna(last) else last.isoformat()
                        )

                    observed_bytes = (
                        batch.nbytes
                        + int(frame.memory_usage(index=True, deep=True).sum())
                        + int(raw.memory_usage(index=True, deep=True).sum())
                    )
                    state["maximum_observed_batch_bytes"] = max(
                        state["maximum_observed_batch_bytes"],
                        observed_bytes,
                    )
                    row_offset += len(frame)
                    state["next_batch"] = batch_number + 1
                    _save_json(manifest_path, state)

                    if state["next_batch"] % 50 == 0:
                        print(
                            f"{source.name}: "
                            f"{state['rows_scanned']:,} records scanned; "
                            f"{state['rows_in_scope']:,} retained"
                        )
            finally:
                reader.close()

        if row_offset != state["rows_scanned"]:
            raise RuntimeError("Final source row count does not reconcile.")

        if (
            state["rows_scanned"]
            != state["rows_in_scope"]
            + state["rows_unassigned"]
            + state["rows_outside_scope"]
        ):
            raise RuntimeError("Source row conservation check failed.")

        if _stamp(source) != source_stamp or _sha256(source) != source_hash:
            raise RuntimeError("Source changed during ingestion; job not completed.")

        _verify_parts(state)
        state["status"] = "complete"
        _save_json(manifest_path, state)
        return {**state, "cache_hit": False}


def _quote_files(state):
    return [
        str(Path(state["output_dir"]) / part["path"])
        for part in state["parts"]
        if part["kind"] == "quotes"
    ]


def _quote_connection(state):
    if state["status"] != "complete" or not _quote_files(state):
        raise ValueError("A completed generation with in-range quotes is required.")
    temporary = Path(state["output_dir"]) / "duckdb_tmp"
    temporary.mkdir(parents=True, exist_ok=True)
    connection = duckdb.connect(config={
        "memory_limit": "256MB",
        "threads": 2,
        "temp_directory": str(temporary),
        "max_temp_directory_size": "4GB",
    })
    connection.execute("SET TimeZone = 'UTC'")
    connection.read_parquet(
        _quote_files(state), hive_partitioning=False
    ).create_view("quotes")
    return connection


def summarise_generation(state):
    connection = _quote_connection(state)
    try:
        count, minimum, maximum, repeated = connection.execute("""
            SELECT count(*), min(timestamp_utc), max(timestamp_utc),
                   count(*) - count(DISTINCT timestamp_utc)
            FROM quotes
        """).fetchone()

        duplicate_groups, duplicate_excess = connection.execute("""
            WITH duplicates AS (
                SELECT count(*) AS n
                FROM quotes
                GROUP BY timestamp_source, ask_raw, bid_raw,
                         ask_size_raw, bid_size_raw
                HAVING count(*) > 1
            )
            SELECT count(*), coalesce(sum(n - 1), 0)
            FROM duplicates
        """).fetchone()

        gap_count, maximum_gap = connection.execute("""
            WITH differences AS (
                SELECT epoch(
                    timestamp_utc
                    - lag(timestamp_utc) OVER (
                        ORDER BY timestamp_utc, source_row
                    )
                ) AS gap_seconds
                FROM quotes
            )
            SELECT count(*) FILTER (WHERE gap_seconds > ?),
                   max(gap_seconds)
            FROM differences
        """, [state["identity"]["parameters"]["gap_seconds"]]).fetchone()

        spread_min, spread_median, spread_max = connection.execute("""
            SELECT min(spread_pips),
                   approx_quantile(spread_pips, 0.5),
                   max(spread_pips)
            FROM quotes
            WHERE isfinite(spread_pips)
        """).fetchone()

        flag_names = sorted(state["scope_flags"])
        condition = " OR ".join(flag_names)
        example_columns = [
            "source_row", "timestamp_source", "timestamp_utc",
            "ask", "bid", "ask_size", "bid_size", "spread_pips",
            *flag_names,
        ]
        examples = connection.execute(
            "SELECT " + ", ".join(example_columns)
            + " FROM quotes WHERE " + condition
            + " ORDER BY source_row LIMIT 5"
        ).fetchdf()

        if int(count) != state["rows_in_scope"]:
            raise RuntimeError("DuckDB count disagrees with ingestion manifest.")

        return {
            "rows": int(count),
            "utc_min": minimum.isoformat(),
            "utc_max": maximum.isoformat(),
            "repeated_timestamp_excess_rows": int(repeated),
            "duplicate_field_groups": int(duplicate_groups),
            "duplicate_field_excess_rows": int(duplicate_excess),
            "chronological_gaps_above_threshold": int(gap_count),
            "maximum_chronological_gap_seconds": maximum_gap,
            "spread_pips": {
                "minimum": spread_min,
                "approximate_median": spread_median,
                "maximum": spread_max,
            },
            "scope_flags": state["scope_flags"],
            "flagged_examples": json.loads(
                examples.to_json(orient="records", date_format="iso")
            ),
        }
    finally:
        connection.close()


def compare_day_with_month(day, month, start, end):
    connection = _quote_connection(day)
    try:
        connection.read_parquet(
            _quote_files(month), hive_partitioning=False
        ).create_view("month_quotes")
        difference_rows = connection.execute("""
            WITH month_slice AS (
                SELECT * FROM month_quotes
                WHERE timestamp_utc >= CAST(? AS TIMESTAMPTZ)
                  AND timestamp_utc < CAST(? AS TIMESTAMPTZ)
            ),
            differences AS (
                (SELECT * FROM quotes
                 EXCEPT ALL SELECT * FROM month_slice)
                UNION ALL
                (SELECT * FROM month_slice
                 EXCEPT ALL SELECT * FROM quotes)
            )
            SELECT count(*) FROM differences
        """, [start, end]).fetchone()[0]
        return int(difference_rows)
    finally:
        connection.close()
