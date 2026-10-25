import copy

import pandas as pd
import pytest

from eurusd_research.coverage_audit import (
    audit_source, consume_rows, new_audit, source_overlap_ranges,
)


def parameters():
    return {
        "timezone": "UTC",
        "timestamp_format": "%Y.%m.%d %H:%M:%S.%f",
        "pip_size": 0.0001, "gap_seconds": 30,
        "extreme_spread_pips": 20, "block_bytes": 256,
        "checkpoint_batches": 2, "progress_batches": 100,
        "max_gap_examples": 20, "max_unassigned_examples": 5,
        "maximum_dataframe_mib": 20,
    }


def quotes(times):
    return pd.DataFrame({
        "Time (EET)": times,
        "Ask": ["1.1002"] * len(times),
        "Bid": ["1.1000"] * len(times),
        "AskVolume": ["1"] * len(times),
        "BidVolume": ["3"] * len(times),
    })


def test_batch_partition_consistency():
    raw = quotes([
        "2020.01.31 23:59:59.000",
        "2020.02.01 00:00:00.000",
        "2020.02.01 00:00:00.000",
        "bad",
        "2020.02.01 00:00:45.000",
        "2020.02.01 00:00:44.000",
        "2020.02.01 00:00:46.000",
    ])
    whole = new_audit("synthetic.csv", "synthetic")
    consume_rows(whole, raw, parameters())

    blocked = new_audit("synthetic.csv", "synthetic")
    for left, right in ((0, 2), (2, 4), (4, 7)):
        consume_rows(blocked, raw.iloc[left:right], parameters())

    for state in (whole, blocked):
        state.pop("next_batch")
        state.pop("maximum_observed_dataframe_bytes")
    assert whole == blocked
    assert whole["adjacent_exact_export_duplicate_rows"] == 1
    assert whole["source_order_backsteps"] == 1
    assert whole["gaps_crossing_unassigned_rows"] == 1
    assert whole["maximum_source_sequence_gap_seconds"] == 45.0


def test_missing_zero_and_negative_sizes_are_distinct():
    raw = quotes([
        "2020.01.01 00:00:00.000",
        "2020.01.01 00:00:01.000",
        "2020.01.01 00:00:02.000",
    ])
    raw["AskVolume"] = ["", "0", "-1"]
    state = new_audit("synthetic.csv", "synthetic")
    consume_rows(state, raw, parameters())
    assert state["flags"]["flag_missing_size"] == 1
    assert state["flags"]["flag_zero_size"] == 1
    assert state["flags"]["flag_negative_size"] == 1
    assert state["size_eligible_quote_rows"] == 1


def test_unresolved_dst_times_are_not_assigned():
    raw = quotes([
        "2016.03.27 03:30:00.000",
        "2016.10.30 03:30:00.000",
    ])
    settings = parameters()
    settings["timezone"] = "Europe/Helsinki"
    state = new_audit("synthetic.csv", "synthetic")
    consume_rows(state, raw, settings)
    assert state["unassigned_timestamp_rows"] == 2
    assert state["flags"]["flag_unresolved_local_time"] == 2


def test_all_pairs_detect_nested_source_overlaps():
    states = [
        {"source_name": "a", "utc_min": "2020-01-01T00:00:00Z", "utc_max": "2020-01-10T00:00:00Z"},
        {"source_name": "b", "utc_min": "2020-01-02T00:00:00Z", "utc_max": "2020-01-03T00:00:00Z"},
        {"source_name": "c", "utc_min": "2020-01-05T00:00:00Z", "utc_max": "2020-01-06T00:00:00Z"},
    ]
    overlaps = source_overlap_ranges(states)
    assert len(overlaps) == 2
    assert all(not item["identical_records_proven"] for item in overlaps)


def test_completed_cache_and_changed_source_version(tmp_path):
    raw = quotes([
        "2020.01.01 00:00:00.000",
        "2020.01.01 00:00:01.000",
        "2020.01.01 00:00:02.000",
    ])
    source = tmp_path / "source.csv"
    root = tmp_path / "project"
    raw.to_csv(source, index=False)

    first = audit_source(source, root, parameters())
    second = audit_source(source, root, parameters())
    assert first["job_id"] == second["job_id"]
    assert second["cache_hit"]
    assert second["rows_scanned"] == 3

    raw.loc[0, "Ask"] = "1.1003"
    raw.to_csv(source, index=False)
    changed = audit_source(source, root, parameters())
    assert changed["job_id"] != first["job_id"]
    assert changed["recorded_source_content_versions"] == 2


def test_changed_audit_output_is_detected(tmp_path):
    source = tmp_path / "source.csv"
    quotes(["2020.01.01 00:00:00.000"]).to_csv(source, index=False)
    root = tmp_path / "project"
    state = audit_source(source, root, parameters())
    (root / state["daily_output"]["path"]).write_bytes(b"changed")
    with pytest.raises(RuntimeError, match="checksum"):
        audit_source(source, root, parameters())
