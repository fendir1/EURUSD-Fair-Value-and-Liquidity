import json
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from eurusd_research import features as f


def settings(**changes):
    result = {
        "pip_size": 0.0001,
        "max_staleness_seconds": 5,
        "substantial_gap_seconds": 30,
        "return_windows_seconds": [1, 5],
        "volatility_windows_seconds": [5],
        "activity_windows_seconds": [1, 5],
        "change_windows_seconds": [1, 5],
        "observed_price_change_window_seconds": 10,
        "wide_spread_review_pips": 20,
        "london_session_start_hour": 8,
        "london_session_end_hour": 17,
        "new_york_session_start_hour": 8,
        "new_york_session_end_hour": 17,
        "block_hours": 6,
        "max_quotes_per_block": 10000,
        "max_grid_rows_per_block": 100000,
        "duckdb_memory_limit": "256MB",
        "duckdb_temp_limit": "4GB",
    }
    result.update(changes)
    return result


def quotes(times, bids=None, asks=None):
    times = pd.to_datetime(times, format="ISO8601", utc=True)
    n = len(times)
    bids = np.asarray(
        bids if bids is not None else 1.1 + np.arange(n) * 0.00001,
        dtype=float,
    )
    asks = np.asarray(
        asks if asks is not None else bids + 0.0002,
        dtype=float,
    )
    result = pd.DataFrame({
        "timestamp_utc": times,
        "source_row": np.arange(1, n + 1),
        "source_file": ["synthetic"] * n,
        "source_sha256": ["synthetic"] * n,
        "bid": bids,
        "ask": asks,
        "bid_size": np.full(n, 3.0),
        "ask_size": np.full(n, 1.0),
    })
    result["source_gap_seconds"] = result["timestamp_utc"].diff().dt.total_seconds()
    for name in (
        "flag_missing_size", "flag_invalid_size",
        "flag_zero_size", "flag_negative_size",
    ):
        result[name] = False
    return result


def test_backward_alignment_ties_and_units():
    q = quotes([
        "2020-02-01T00:00:00.200Z",
        "2020-02-01T00:00:01.200Z",
        "2020-02-01T00:00:02Z",
        "2020-02-01T00:00:02Z",
    ], [1.1, 1.1001, 1.1002, 1.1004])
    result = f.build_clock_features(
        q, "2020-02-01T00:00:01Z", "2020-02-01T00:00:03Z",
        settings(), q["timestamp_utc"].min(),
    )
    assert result.loc[0, "source_row"] == 1
    assert result.loc[1, "source_row"] == 4
    assert result.loc[0, "snapshot_staleness_seconds"] == pytest.approx(0.8)
    assert result.loc[0, "spread_pips"] == pytest.approx(2)
    assert result.loc[0, "size_imbalance"] == pytest.approx(0.5)
    assert result.loc[0, "size_weighted_minus_mid_pips"] == pytest.approx(0.5)
    assert result.loc[1, "snapshot_count_1s"] == 3


def test_invalid_latest_is_not_replaced_with_older_quote():
    q = quotes(
        ["2020-02-01T00:00:00Z", "2020-02-01T00:00:01Z"],
        [1.1, 1.101], [1.1002, 1.1005],
    )
    result = f.build_clock_features(
        q, "2020-02-01T00:00:01Z", "2020-02-01T00:00:02Z",
        settings(), q["timestamp_utc"].min(),
    )
    assert result.loc[0, "source_row"] == 2
    assert not result.loc[0, "price_valid"]
    assert pd.isna(result.loc[0, "mid_price"])


def test_stale_state_and_gap_break_returns():
    q = quotes(
        ["2020-02-01T00:00:00Z", "2020-02-01T00:00:31Z"]
    )
    result = f.build_clock_features(
        q, "2020-02-01T00:00:30Z", "2020-02-01T00:00:33Z",
        settings(max_staleness_seconds=30),
        q["timestamp_utc"].min(),
    )
    assert result.loc[0, "price_valid"]
    assert result.loc[1, "price_valid"]
    assert result.loc[1, "source_gap_observed_1s"]
    assert pd.isna(result.loc[1, "mid_change_1s_pips"])

    stale = f.build_clock_features(
        q, "2020-02-01T00:00:06Z", "2020-02-01T00:00:07Z",
        settings(), q["timestamp_utc"].min(),
    )
    assert stale.loc[0, "state_reason"] == "stale_snapshot"
    assert pd.isna(stale.loc[0, "bid"])


def test_intrasecond_invalid_quote_breaks_price_path():
    q = quotes([
        "2020-02-01T00:00:00Z",
        "2020-02-01T00:00:00.500Z",
        "2020-02-01T00:00:00.800Z",
    ], [1.1, 1.101, 1.1001], [1.1002, 1.1005, 1.1003])
    result = f.build_clock_features(
        q, "2020-02-01T00:00:01Z", "2020-02-01T00:00:02Z",
        settings(), q["timestamp_utc"].min(),
    )
    assert result.loc[0, "price_valid"]
    assert result.loc[0, "invalid_price_observed_1s"]
    assert result.loc[0, "price_path_break"]
    assert pd.isna(result.loc[0, "mid_change_1s_pips"])


def test_locked_prices_and_zero_denominator():
    q = quotes(["2020-02-01T00:00:00Z"], [1.1], [1.1])
    q[["bid_size", "ask_size"]] = 0.0
    q["flag_zero_size"] = True
    result = f.build_clock_features(
        q, "2020-02-01T00:00:00Z", "2020-02-01T00:00:01Z",
        settings(), q["timestamp_utc"].min(),
    )
    assert result.loc[0, "price_valid"]
    assert result.loc[0, "locked_quote"]
    assert not result.loc[0, "size_pair_valid"]
    assert pd.isna(result.loc[0, "size_imbalance"])


def test_change_age_at_and_beyond_cap():
    q = quotes([
        "2020-02-01T00:00:00Z",
        "2020-02-01T00:00:01Z",
        "2020-02-01T00:00:11Z",
        "2020-02-01T00:00:12Z",
    ], [1.1, 1.1001, 1.1001, 1.1001])
    result = f.build_clock_features(
        q, "2020-02-01T00:00:11Z", "2020-02-01T00:00:13Z",
        settings(), q["timestamp_utc"].min(),
    )
    assert result.loc[0, "bid_price_change_age_seconds"] == 10
    assert pd.isna(result.loc[1, "bid_price_change_age_seconds"])
    assert result.loc[1, "bid_price_change_age_censored_or_unknown"]


def test_future_append_and_partition_consistency():
    times = pd.date_range("2020-01-31T23:55:00Z", periods=1000, freq="1s")
    q = quotes(times)
    cfg = settings()
    start, boundary, end = (
        "2020-01-31T23:58:00Z",
        "2020-02-01T00:00:00Z",
        "2020-02-01T00:06:00Z",
    )
    coverage = q["timestamp_utc"].min()
    full = f.build_clock_features(q, start, end, cfg, coverage)
    pieces = pd.concat([
        f.build_clock_features(q, start, boundary, cfg, coverage),
        f.build_clock_features(q, boundary, end, cfg, coverage),
    ], ignore_index=True)
    pd.testing.assert_frame_equal(full, pieces, rtol=1e-10, atol=1e-12)

    cutoff = pd.Timestamp(boundary)
    earlier_input = q.loc[q["timestamp_utc"].lt(cutoff)]
    before = f.build_clock_features(
        earlier_input, start, boundary, cfg, coverage
    )
    after = f.build_clock_features(q, start, boundary, cfg, coverage)
    pd.testing.assert_frame_equal(before, after)


@pytest.mark.parametrize("timestamp, london_hour, new_york_hour", [
    ("2025-03-03T12:00:00Z", 12, 7),
    ("2025-03-17T12:00:00Z", 12, 8),
    ("2025-03-31T12:00:00Z", 13, 8),
])
def test_local_time_features(timestamp, london_hour, new_york_hour):
    q = quotes([timestamp])
    end = (pd.Timestamp(timestamp) + pd.Timedelta(seconds=1)).isoformat()
    result = f.build_clock_features(
        q, timestamp, end, settings(), q["timestamp_utc"].min()
    )
    assert result.loc[0, "london_hour"] == london_hour
    assert result.loc[0, "new_york_hour"] == new_york_hour


def test_checkpoint_retries_and_preserves_previous_value(tmp_path, monkeypatch):
    path = tmp_path / "checkpoint.json"
    f.write_json(path, {"version": 1})
    actual_replace = f.os.replace
    calls = 0

    def transient_lock(source, target):
        nonlocal calls
        calls += 1
        if calls == 1:
            error = PermissionError("simulated sharing error")
            error.winerror = 32
            raise error
        return actual_replace(source, target)

    monkeypatch.setattr(f.os, "replace", transient_lock)
    monkeypatch.setattr(f.time, "sleep", lambda seconds: None)
    f.write_json(path, {"version": 2})
    assert json.loads(path.read_text()) == {"version": 2}
    assert calls == 2

    def persistent_lock(source, target):
        error = PermissionError("simulated persistent sharing error")
        error.winerror = 32
        raise error

    monkeypatch.setattr(f.os, "replace", persistent_lock)
    with pytest.raises(PermissionError):
        f.write_json(path, {"version": 3})
    assert json.loads(path.read_text()) == {"version": 2}


def test_feature_job_resume_and_corruption(tmp_path, monkeypatch):
    q = quotes(pd.date_range(
        "2020-01-31T23:55:00Z", periods=1000, freq="1s"
    ))
    input_dir = tmp_path / "input"
    input_dir.mkdir()
    parquet = input_dir / "quotes.parquet"
    pq.write_table(pa.Table.from_pandas(q, preserve_index=False), parquet)
    source = {
        "job_id": "synthetic-source",
        "status": "complete",
        "output_dir": str(input_dir),
        "identity": {
            "start_utc": "2020-01-31T00:00:00Z",
            "end_utc": "2020-02-02T00:00:00Z",
        },
        "rows_in_scope": len(q),
        "rows_unassigned": 0,
        "scope_utc_min": q["timestamp_utc"].min().isoformat(),
        "scope_utc_max": q["timestamp_utc"].max().isoformat(),
        "scope_flags": {"flag_backward_timestamp": 0},
        "parts": [{
            "path": parquet.name,
            "kind": "quotes",
            "rows": len(q),
            "bytes": parquet.stat().st_size,
            "sha256": f._sha256(parquet),
        }],
    }
    manifest = input_dir / "manifest.json"
    f.write_json(manifest, source)
    cfg = settings()
    start, end = "2020-01-31T23:58:00Z", "2020-02-01T00:06:00Z"

    original_write = f.write_json
    interrupted = False

    def save_then_interrupt(path, value):
        nonlocal interrupted
        original_write(path, value)
        if (
            value.get("kind") == "feature_dataset"
            and value["status"] == "running"
            and len(value["parts"]) == 1
            and not interrupted
        ):
            interrupted = True
            raise RuntimeError("simulated interruption")

    monkeypatch.setattr(f, "write_json", save_then_interrupt)
    with pytest.raises(RuntimeError, match="simulated interruption"):
        f.build_feature_dataset(manifest, tmp_path / "run", start, end, cfg)

    resumed = f.build_feature_dataset(
        manifest, tmp_path / "run", start, end, cfg
    )
    reference = f.reference_feature_block(
        source, start, end, cfg, tmp_path / "reference"
    )
    stored = f.load_feature_rows(resumed, start, end, cfg)
    pd.testing.assert_frame_equal(
        reference, stored, check_dtype=False, rtol=1e-10, atol=1e-12
    )

    repeated = f.build_feature_dataset(
        manifest, tmp_path / "run", start, end, cfg
    )
    assert repeated["cache_hit"]

    part = Path(resumed["output_dir"]) / resumed["parts"][0]["path"]
    part.write_bytes(b"damaged")
    with pytest.raises(RuntimeError, match="checksum"):
        f.build_feature_dataset(manifest, tmp_path / "run", start, end, cfg)
