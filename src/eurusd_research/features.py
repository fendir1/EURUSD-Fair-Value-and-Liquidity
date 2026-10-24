"""Causal clock-time sampling, features, and bounded feature storage."""

import hashlib
import json
import os
import re
import tempfile
import time
from importlib.metadata import version
from pathlib import Path

import duckdb
import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from .data import _sha256, _verify_parts, _writer_lock

QUOTE_COLUMNS = [
    "timestamp_utc", "source_row", "source_file", "source_sha256",
    "bid", "ask", "bid_size", "ask_size", "source_gap_seconds",
    "flag_missing_size", "flag_invalid_size",
    "flag_zero_size", "flag_negative_size",
]


def _utc(value):
    value = pd.Timestamp(value)
    if value.tzinfo is None:
        raise ValueError("Timestamps must include a timezone.")
    value = value.tz_convert("UTC")
    if value.microsecond or value.nanosecond:
        raise ValueError("Processing boundaries must be aligned to whole seconds.")
    return value


def lookback_seconds(settings):
    windows = (
        settings["return_windows_seconds"]
        + settings["volatility_windows_seconds"]
        + settings["activity_windows_seconds"]
        + settings["change_windows_seconds"]
        + [settings["observed_price_change_window_seconds"]]
    )
    if not windows or any(type(value) is not int or value < 1 for value in windows):
        raise ValueError("Feature windows must be positive integer seconds.")
    if not (
        0 <= settings["max_staleness_seconds"]
        <= settings["substantial_gap_seconds"]
    ):
        raise ValueError("Staleness must be nonnegative and no larger than the gap limit.")
    return max(windows) + 1


def normalise_feature_types(frame):
    frame = frame.copy()
    time_columns = {"feature_timestamp_utc", "source_timestamp_utc"}
    string_columns = {"source_file", "source_sha256", "state_reason"}

    for name in frame:
        if name in time_columns:
            frame[name] = pd.to_datetime(frame[name], utc=True).astype(
                "datetime64[us, UTC]"
            )
        elif name in string_columns:
            frame[name] = frame[name].astype("string")
        elif name == "source_row":
            frame[name] = frame[name].astype("Int64")
        elif pd.api.types.is_bool_dtype(frame[name].dtype):
            frame[name] = frame[name].astype(bool)
        else:
            frame[name] = frame[name].astype("float64")
    return frame


def _event_bins(timestamps, grid, mask=None):
    if mask is not None:
        timestamps = timestamps.loc[mask]
    counts = timestamps.dt.ceil("s").value_counts(sort=False)
    return counts.reindex(grid, fill_value=0).to_numpy(dtype="int64")


def build_clock_features(quotes, start, end, settings, coverage_start):
    start, end = _utc(start), _utc(end)
    if start >= end:
        raise ValueError("The processing interval must have positive length.")

    context_start = start - pd.Timedelta(seconds=lookback_seconds(settings))
    grid = pd.date_range(
        context_start, end, freq="1s", inclusive="left"
    ).astype("datetime64[us, UTC]")

    if len(grid) > settings["max_grid_rows_per_block"]:
        raise ValueError("Grid exceeds the configured block limit.")

    q = quotes[QUOTE_COLUMNS].copy()
    q["timestamp_utc"] = pd.to_datetime(q["timestamp_utc"], utc=True).astype(
        "datetime64[us, UTC]"
    )
    if q["timestamp_utc"].isna().any():
        raise ValueError("Canonical input quotes must have usable UTC timestamps.")

    q = q.loc[q["timestamp_utc"].le(grid[-1])].sort_values(
        ["timestamp_utc", "source_row"], kind="stable"
    ).reset_index(drop=True)

    position = q["timestamp_utc"].searchsorted(context_start, side="left")
    q = q.iloc[max(0, position - 1):].reset_index(drop=True)

    usable_raw_price = (
        np.isfinite(q["bid"]) & np.isfinite(q["ask"])
        & q["bid"].gt(0) & q["ask"].gt(0)
        & q["ask"].ge(q["bid"])
    )
    raw_total_size = q["bid_size"] + q["ask_size"]
    usable_raw_size = (
        np.isfinite(q["bid_size"]) & np.isfinite(q["ask_size"])
        & q["bid_size"].ge(0) & q["ask_size"].ge(0)
        & np.isfinite(raw_total_size) & raw_total_size.gt(0)
    )
    raw_gap = q["source_gap_seconds"].gt(settings["substantial_gap_seconds"])
    comparable = (
        usable_raw_price
        & usable_raw_price.shift(fill_value=False)
        & ~raw_gap
    )
    knowledge_group = (~comparable).cumsum()

    for side in ("bid", "ask"):
        changed = comparable & q[side].ne(q[side].shift())
        event_time = q["timestamp_utc"].where(changed)
        q[f"_{side}_change_time"] = event_time.groupby(knowledge_group).ffill()

    right = q.rename(columns={"timestamp_utc": "source_timestamp_utc"})
    frame = pd.DataFrame({"feature_timestamp_utc": grid})
    sampled = pd.merge_asof(
        frame,
        right,
        left_on="feature_timestamp_utc",
        right_on="source_timestamp_utc",
        direction="backward",
        allow_exact_matches=True,
    )

    for name in ("source_timestamp_utc", "source_row", "source_file", "source_sha256"):
        frame[name] = sampled[name]

    age = (
        frame["feature_timestamp_utc"] - frame["source_timestamp_utc"]
    ).dt.total_seconds()
    has_source = frame["source_timestamp_utc"].notna()
    if age.loc[has_source].lt(0).any():
        raise RuntimeError("Future snapshot entered current features.")

    usable_price = (
        np.isfinite(sampled["bid"]) & np.isfinite(sampled["ask"])
        & sampled["bid"].gt(0) & sampled["ask"].gt(0)
        & sampled["ask"].ge(sampled["bid"])
    )
    stale = has_source & age.gt(settings["max_staleness_seconds"])
    price_valid = has_source & ~stale & usable_price

    frame["snapshot_staleness_seconds"] = age
    frame["price_valid"] = price_valid
    frame["state_reason"] = np.select(
        [~has_source, stale, ~usable_price],
        ["no_source_quote", "stale_snapshot", "invalid_latest_price"],
        default="usable_price",
    )

    frame["bid"] = sampled["bid"].where(price_valid)
    frame["ask"] = sampled["ask"].where(price_valid)
    frame["mid_price"] = 0.5 * frame["bid"] + 0.5 * frame["ask"]
    frame["spread_pips"] = (frame["ask"] - frame["bid"]) / settings["pip_size"]
    frame["locked_quote"] = price_valid & sampled["ask"].eq(sampled["bid"])
    frame["wide_spread_observation"] = frame["spread_pips"].gt(
        settings["wide_spread_review_pips"]
    )

    for side in ("bid", "ask"):
        size = sampled[f"{side}_size"]
        frame[f"{side}_size"] = size.where(
            price_valid & np.isfinite(size) & size.ge(0)
        )

    total_size = frame["bid_size"] + frame["ask_size"]
    size_valid = (
        price_valid & np.isfinite(total_size) & total_size.gt(0)
        & frame["bid_size"].notna() & frame["ask_size"].notna()
    )
    denominator = total_size.where(size_valid)
    frame["size_pair_valid"] = size_valid
    frame["size_imbalance"] = (
        frame["bid_size"] - frame["ask_size"]
    ) / denominator
    frame["size_weighted_quote_price"] = (
        frame["ask"] * frame["bid_size"]
        + frame["bid"] * frame["ask_size"]
    ) / denominator
    frame["size_weighted_minus_mid_pips"] = (
        frame["size_weighted_quote_price"] - frame["mid_price"]
    ) / settings["pip_size"]

    for name in (
        "flag_missing_size", "flag_invalid_size",
        "flag_zero_size", "flag_negative_size",
    ):
        frame[f"sampled_{name}"] = sampled[name].eq(True)

    timestamps = q["timestamp_utc"]
    raw_counts = pd.Series(_event_bins(timestamps, grid))
    gap_event = pd.Series(_event_bins(timestamps, grid, raw_gap)).gt(0)
    invalid_price_event = pd.Series(
        _event_bins(timestamps, grid, ~usable_raw_price)
    ).gt(0)
    invalid_size_event = pd.Series(
        _event_bins(timestamps, grid, ~usable_raw_size)
    ).gt(0)

    step_valid = (
        price_valid & price_valid.shift(fill_value=False)
        & ~gap_event & ~invalid_price_event
    )
    size_step_valid = (
        step_valid & size_valid & size_valid.shift(fill_value=False)
        & ~invalid_size_event
    )

    frame["source_gap_observed_1s"] = gap_event
    frame["invalid_price_observed_1s"] = invalid_price_event
    frame["unusable_size_observed_1s"] = invalid_size_event
    frame["price_path_break"] = ~step_valid

    pip_step = (
        frame["mid_price"].diff() / settings["pip_size"]
    ).where(step_valid)

    for horizon in settings["return_windows_seconds"]:
        complete = step_valid.rolling(horizon, min_periods=horizon).sum().eq(horizon)
        frame[f"mid_change_{horizon}s_pips"] = (
            (frame["mid_price"] - frame["mid_price"].shift(horizon))
            / settings["pip_size"]
        ).where(complete)
        frame[f"log_return_{horizon}s"] = np.log(
            frame["mid_price"] / frame["mid_price"].shift(horizon)
        ).where(complete)

    for window in settings["volatility_windows_seconds"]:
        frame[f"realized_vol_{window}s_pips"] = (
            pip_step.pow(2).rolling(window, min_periods=window).sum().pow(0.5)
        )

    coverage_start = pd.Timestamp(coverage_start).tz_convert("UTC")
    for window in settings["activity_windows_seconds"]:
        complete_history = (
            has_source
            & frame["feature_timestamp_utc"].sub(
                pd.Timedelta(seconds=window)
            ).ge(coverage_start)
        )
        frame[f"snapshot_count_{window}s"] = raw_counts.rolling(
            window, min_periods=window
        ).sum().where(complete_history)

    for horizon in settings["change_windows_seconds"]:
        price_complete = step_valid.rolling(
            horizon, min_periods=horizon
        ).sum().eq(horizon)
        size_complete = size_step_valid.rolling(
            horizon, min_periods=horizon
        ).sum().eq(horizon)

        frame[f"spread_change_{horizon}s_pips"] = (
            frame["spread_pips"].diff(horizon).where(price_complete)
        )
        for side in ("bid", "ask"):
            frame[f"{side}_size_change_{horizon}s"] = (
                frame[f"{side}_size"].diff(horizon).where(size_complete)
            )

    age_window = settings["observed_price_change_window_seconds"]
    for side in ("bid", "ask"):
        change_age = (
            frame["feature_timestamp_utc"] - sampled[f"_{side}_change_time"]
        ).dt.total_seconds()
        known_recent = price_valid & change_age.between(0, age_window)
        frame[f"{side}_price_change_age_seconds"] = change_age.where(known_recent)
        frame[f"{side}_price_change_recently_observed"] = known_recent
        frame[f"{side}_price_change_age_censored_or_unknown"] = (
            price_valid & ~known_recent
        )

    for prefix, zone in (
        ("utc", "UTC"),
        ("london", "Europe/London"),
        ("new_york", "America/New_York"),
    ):
        local = frame["feature_timestamp_utc"].dt.tz_convert(zone)
        seconds = local.dt.hour * 3600 + local.dt.minute * 60 + local.dt.second
        angle = 2 * np.pi * seconds / 86400
        frame[f"{prefix}_hour"] = local.dt.hour
        frame[f"{prefix}_time_sin"] = np.sin(angle)
        frame[f"{prefix}_time_cos"] = np.cos(angle)

        if prefix != "utc":
            frame[f"{prefix}_session"] = (
                local.dt.dayofweek.lt(5)
                & local.dt.hour.ge(settings[f"{prefix}_session_start_hour"])
                & local.dt.hour.lt(settings[f"{prefix}_session_end_hour"])
            )

    frame["utc_weekday"] = frame["feature_timestamp_utc"].dt.dayofweek
    london, new_york = frame["london_session"], frame["new_york_session"]
    frame["state_reason"] = frame["state_reason"].astype("string")
    frame["session_overlap"] = london & new_york

    output = frame.loc[
        frame["feature_timestamp_utc"].ge(start)
        & frame["feature_timestamp_utc"].lt(end)
    ].reset_index(drop=True)

    expected_rows = int((end - start).total_seconds())
    if len(output) != expected_rows:
        raise RuntimeError("Clock-grid row conservation failed.")

    return normalise_feature_types(output)


def _replace_path(temporary, target):
    delays = [0.1, 0.2, 0.4, 0.8, 1.6, 2.0, 2.0]
    for attempt in range(len(delays) + 1):
        try:
            os.replace(temporary, target)
            return
        except PermissionError as error:
            if (
                getattr(error, "winerror", None) not in {5, 32, 33}
                or attempt == len(delays)
            ):
                raise
            time.sleep(delays[attempt])


def write_json(path, value):
    path = Path(path)
    payload = json.dumps(
        value, indent=2, sort_keys=True, allow_nan=False
    ) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        descriptor, name = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
        )
        temporary = Path(name)
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        _replace_path(temporary, path)
    finally:
        if temporary is not None:
            try:
                temporary.unlink(missing_ok=True)
            except OSError:
                pass


def _schema_hash(schema):
    return hashlib.sha256(
        schema.remove_metadata().serialize().to_pybytes()
    ).hexdigest()


def _save_frame(frame, path):
    path.parent.mkdir(parents=True, exist_ok=True)
    table = pa.Table.from_pandas(frame, preserve_index=False)
    descriptor, name = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    temporary = Path(name)
    try:
        with os.fdopen(descriptor, "wb") as handle:
            pq.write_table(table, handle, compression="zstd")
            handle.flush()
            os.fsync(handle.fileno())
        _replace_path(temporary, path)
    finally:
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass

    return {
        "rows": len(frame),
        "bytes": path.stat().st_size,
        "sha256": _sha256(path),
        "schema_sha256": _schema_hash(table.schema),
    }


def _connect(files, view, working_directory, settings):
    temporary = Path(working_directory) / "duckdb_tmp"
    temporary.mkdir(parents=True, exist_ok=True)
    for name in ("duckdb_memory_limit", "duckdb_temp_limit"):
        if not re.fullmatch(r"\d+(MB|GB)", settings[name]):
            raise ValueError(f"Unsupported resource setting: {name}")

    connection = duckdb.connect(config={
        "memory_limit": settings["duckdb_memory_limit"],
        "threads": 2,
        "temp_directory": str(temporary),
    })
    try:
        # Set the spill cap after connecting; do not rely on connect(config=...).
        connection.execute(
            f"SET max_temp_directory_size = '{settings['duckdb_temp_limit']}'"
        )
        connection.execute("SET TimeZone = 'UTC'")
        connection.read_parquet(
            files, hive_partitioning=False
        ).create_view(view)
    except Exception:
        connection.close()
        raise
    return connection


def _source_files(source):
    root = Path(source["output_dir"])
    return [
        str(root / part["path"])
        for part in source["parts"] if part["kind"] == "quotes"
    ]


def _load_quotes(connection, start, end, settings):
    context = _utc(start) - pd.Timedelta(seconds=lookback_seconds(settings))
    last_grid = _utc(end) - pd.Timedelta(seconds=1)
    columns = ", ".join(QUOTE_COLUMNS)

    quotes = connection.execute(
        f"""
        SELECT {columns} FROM quotes
        WHERE timestamp_utc >= CAST(? AS TIMESTAMPTZ)
          AND timestamp_utc <= CAST(? AS TIMESTAMPTZ)
        ORDER BY timestamp_utc, source_row
        LIMIT ?
        """,
        [
            context.isoformat(), last_grid.isoformat(),
            settings["max_quotes_per_block"] + 1,
        ],
    ).fetchdf()

    if len(quotes) > settings["max_quotes_per_block"]:
        raise RuntimeError(
            "Quote block exceeded its row limit. Reduce block_hours "
            "before attempting a larger run."
        )

    predecessor = connection.execute(
        f"""
        SELECT {columns} FROM quotes
        WHERE timestamp_utc < CAST(? AS TIMESTAMPTZ)
        ORDER BY timestamp_utc DESC, source_row DESC
        LIMIT 1
        """,
        [context.isoformat()],
    ).fetchdf()

    return pd.concat([predecessor, quotes], ignore_index=True)


def _blocks(start, end, hours):
    if type(hours) is not int or not 1 <= hours <= 24:
        raise ValueError("block_hours must be an integer from 1 to 24.")
    left = _utc(start)
    end = _utc(end)
    output = []
    while left < end:
        right = min(
            left + pd.Timedelta(hours=hours),
            left.normalize() + pd.Timedelta(days=1),
            end,
        )
        output.append((left, right))
        left = right
    return output


def _frame_stats(frame, settings):
    return {
        "rows": len(frame),
        "price_valid_rows": int(frame["price_valid"].sum()),
        "size_pair_valid_rows": int(frame["size_pair_valid"].sum()),
        "wide_spread_observation_rows": int(
            frame["wide_spread_observation"].sum()
        ),
        "state_reasons": {
            str(key): int(value)
            for key, value in frame["state_reason"].value_counts().items()
        },
        "volatility_available_rows": {
            str(window): int(
                frame[f"realized_vol_{window}s_pips"].notna().sum()
            )
            for window in settings["volatility_windows_seconds"]
        },
    }


def _verify_feature_parts(state, expected_blocks):
    root = Path(state["output_dir"]).resolve()
    if len(state["parts"]) > len(expected_blocks):
        raise RuntimeError("Too many feature blocks in the checkpoint.")

    for part, (left, right) in zip(state["parts"], expected_blocks):
        if (part["start_utc"], part["end_utc"]) != (
            left.isoformat(), right.isoformat()
        ):
            raise RuntimeError("Feature checkpoint ranges are incompatible.")
        path = (root / part["path"]).resolve()
        if not path.is_relative_to(root):
            raise RuntimeError("Feature output escapes its generation directory.")
        if not path.is_file() or _sha256(path) != part["sha256"]:
            raise RuntimeError(f"Feature output checksum failed: {path}")
        parquet = pq.ParquetFile(path)
        if (
            parquet.metadata.num_rows != part["rows"]
            or part["rows"] != int((right - left).total_seconds())
            or _schema_hash(parquet.schema_arrow) != part["schema_sha256"]
        ):
            raise RuntimeError("Feature output schema or row count changed.")

    if state["status"] == "complete" and len(state["parts"]) != len(expected_blocks):
        raise RuntimeError("Completed feature generation has missing blocks.")


def build_feature_dataset(source_manifest_path, repository_root, start, end, settings):
    root = Path(repository_root).resolve()
    source_manifest_path = Path(source_manifest_path).resolve()
    source = json.loads(source_manifest_path.read_text(encoding="utf-8"))
    start, end = _utc(start), _utc(end)
    lookback_seconds(settings)

    if source["status"] != "complete" or not _source_files(source):
        raise ValueError("A completed ingestion generation is required.")
    if source.get("scope_flags", {}).get("flag_backward_timestamp", 0):
        raise ValueError("Resolve source-clock ordering reversals before features.")
    if not (
        pd.Timestamp(source["identity"]["start_utc"]) <= start
        < end <= pd.Timestamp(source["identity"]["end_utc"])
    ):
        raise ValueError("Feature range exceeds the selected ingestion range.")

    _verify_parts(source)
    for filename in _source_files(source):
        schema = pq.ParquetFile(filename).schema_arrow
        if any(name not in schema.names for name in QUOTE_COLUMNS):
            raise ValueError("Input Parquet lacks required quote columns.")
        field = schema.field("timestamp_utc")
        if not pa.types.is_timestamp(field.type) or field.type.tz != "UTC":
            raise ValueError("Input quote timestamps must explicitly carry UTC.")

    source_manifest_hash = _sha256(source_manifest_path)
    implementation = hashlib.sha256(
        Path(__file__).read_bytes()
        + Path(__file__).with_name("data.py").read_bytes()
    ).hexdigest()
    identity = {
        "input_manifest_sha256": source_manifest_hash,
        "input_job_id": source["job_id"],
        "start_utc": start.isoformat(),
        "end_utc": end.isoformat(),
        "settings": settings,
        "implementation_sha256": implementation,
        "versions": {
            name: version(name)
            for name in ("numpy", "pandas", "pyarrow", "duckdb", "pytz", "tzdata")
        },
    }
    job_id = hashlib.sha256(
        json.dumps(identity, sort_keys=True).encode("utf-8")
    ).hexdigest()[:24]
    output_dir = root / "data/processed/features" / job_id
    manifest_root = root / "artifacts/manifests/features"
    manifest_path = manifest_root / f"job-{job_id}.json"
    blocks = _blocks(start, end, settings["block_hours"])

    with _writer_lock(manifest_root):
        if manifest_path.exists():
            state = json.loads(manifest_path.read_text(encoding="utf-8"))
            if state["identity"] != identity:
                raise RuntimeError("Feature generation identity is incompatible.")
            _verify_feature_parts(state, blocks)
            if state["status"] == "complete":
                return {**state, "cache_hit": True}
        else:
            output_dir.mkdir(parents=True, exist_ok=True)
            state = {
                "kind": "feature_dataset",
                "job_id": job_id,
                "status": "running",
                "identity": identity,
                "input_manifest": str(source_manifest_path),
                "input_quote_rows": source["rows_in_scope"],
                "input_quote_utc_min": source["scope_utc_min"],
                "input_quote_utc_max": source["scope_utc_max"],
                "output_dir": str(output_dir),
                "manifest_path": str(manifest_path),
                "parts": [],
            }
            write_json(manifest_path, state)

        connection = _connect(
            _source_files(source), "quotes", output_dir, settings
        )
        try:
            for number, (left, right) in enumerate(blocks):
                if number < len(state["parts"]):
                    continue

                began = time.perf_counter()
                quotes = _load_quotes(connection, left, right, settings)
                frame = build_clock_features(
                    quotes, left, right, settings, source["scope_utc_min"]
                )

                future = (
                    frame["source_timestamp_utc"]
                    > frame["feature_timestamp_utc"]
                ).sum()
                if future:
                    raise RuntimeError("Future source observations entered features.")

                relative = (
                    f"year={left.year:04d}/month={left.month:02d}/"
                    f"part-{left.strftime('%Y%m%dT%H%M%S')}.parquet"
                )
                record = _save_frame(frame, output_dir / relative)
                if (
                    state["parts"]
                    and record["schema_sha256"]
                    != state["parts"][0]["schema_sha256"]
                ):
                    raise RuntimeError("Feature schema changed between blocks.")

                record.update({
                    "path": relative,
                    "start_utc": left.isoformat(),
                    "end_utc": right.isoformat(),
                    "statistics": _frame_stats(frame, settings),
                    "input_quotes_collected": len(quotes),
                    "observed_input_output_frame_bytes": int(
                        quotes.memory_usage(index=True, deep=True).sum()
                        + frame.memory_usage(index=True, deep=True).sum()
                    ),
                    "runtime_seconds": round(time.perf_counter() - began, 3),
                })
                state["parts"].append(record)
                write_json(manifest_path, state)

                if (number + 1) % 8 == 0 or number + 1 == len(blocks):
                    print(f"Feature blocks committed: {number + 1}/{len(blocks)}")
        finally:
            connection.close()

        if _sha256(source_manifest_path) != source_manifest_hash:
            raise RuntimeError("Input ingestion manifest changed during processing.")
        _verify_parts(source)
        _verify_feature_parts(state, blocks)
        state["status"] = "complete"
        write_json(manifest_path, state)
        return {**state, "cache_hit": False}


def load_feature_rows(state, start, end, settings, maximum_rows=100000):
    files = [
        str(Path(state["output_dir"]) / part["path"])
        for part in state["parts"]
    ]
    connection = _connect(files, "features", state["output_dir"], settings)
    try:
        frame = connection.execute("""
            SELECT * FROM features
            WHERE feature_timestamp_utc >= CAST(? AS TIMESTAMPTZ)
              AND feature_timestamp_utc < CAST(? AS TIMESTAMPTZ)
            ORDER BY feature_timestamp_utc
            LIMIT ?
        """, [
            _utc(start).isoformat(), _utc(end).isoformat(), maximum_rows + 1
        ]).fetchdf()
        if len(frame) > maximum_rows:
            raise RuntimeError("Requested pandas result exceeds its row limit.")
        return normalise_feature_types(frame)
    finally:
        connection.close()


def reference_feature_block(source, start, end, settings, working_directory):
    connection = _connect(
        _source_files(source), "quotes", working_directory, settings
    )
    try:
        quotes = _load_quotes(connection, start, end, settings)
        return build_clock_features(
            quotes, start, end, settings, source["scope_utc_min"]
        )
    finally:
        connection.close()


def summarise_features(state, settings):
    files = [
        str(Path(state["output_dir"]) / part["path"])
        for part in state["parts"]
    ]
    connection = _connect(files, "features", state["output_dir"], settings)
    try:
        rows, distinct_rows, minimum, maximum, future, stale_prices = (
            connection.execute("""
                SELECT count(*), count(DISTINCT feature_timestamp_utc),
                       min(feature_timestamp_utc), max(feature_timestamp_utc),
                       count(*) FILTER (
                           WHERE source_timestamp_utc > feature_timestamp_utc
                       ),
                       count(*) FILTER (
                           WHERE price_valid
                             AND snapshot_staleness_seconds > ?
                       )
                FROM features
            """, [settings["max_staleness_seconds"]]).fetchone()
        )
    finally:
        connection.close()

    expected = int((
        pd.Timestamp(state["identity"]["end_utc"])
        - pd.Timestamp(state["identity"]["start_utc"])
    ).total_seconds())

    if rows != expected or distinct_rows != rows or future or stale_prices:
        raise RuntimeError("Feature dataset validation failed.")

    totals = {
        "price_valid_rows": 0,
        "size_pair_valid_rows": 0,
        "wide_spread_observation_rows": 0,
    }
    reasons = {}
    volatility = {}
    for part in state["parts"]:
        statistics = part["statistics"]
        for key in totals:
            totals[key] += statistics[key]
        for key, value in statistics["state_reasons"].items():
            reasons[key] = reasons.get(key, 0) + value
        for key, value in statistics["volatility_available_rows"].items():
            volatility[key] = volatility.get(key, 0) + value

    return {
        "input_quote_rows": state["input_quote_rows"],
        "input_quote_utc_min": state["input_quote_utc_min"],
        "input_quote_utc_max": state["input_quote_utc_max"],
        "clock_rows": int(rows),
        "grid_utc_min": minimum.isoformat(),
        "grid_utc_max": maximum.isoformat(),
        **totals,
        "calendar_price_coverage_fraction": totals["price_valid_rows"] / rows,
        "state_reasons": reasons,
        "volatility_available_rows": volatility,
        "future_source_violations": int(future),
        "stale_prices_marked_usable": int(stale_prices),
        "parquet_bytes": sum(part["bytes"] for part in state["parts"]),
        "maximum_quotes_collected_per_block": max(
            part["input_quotes_collected"] for part in state["parts"]
        ),
        "maximum_observed_input_output_frame_bytes": max(
            part["observed_input_output_frame_bytes"] for part in state["parts"]
        ),
        "cache_hit": state["cache_hit"],
        "manifest": state["manifest_path"],
    }
