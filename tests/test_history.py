import copy
import json
from pathlib import Path

import pandas as pd
import pytest

from eurusd_research import features as io
from eurusd_research.coverage_audit import audit_source
from eurusd_research.data import _sha256, _verify_parts, ingest_quotes
from eurusd_research.history import (
    build_collection, create_local_binding, ingest_audited_source,
    load_collection, quote_files, validate_audits, verify_collection,
)


@pytest.fixture
def built(tmp_path):
    root = tmp_path / "project"
    raw_root = root / "data/raw"
    raw_root.mkdir(parents=True)

    columns = ["Time (EET)", "Ask", "Bid", "AskVolume", "BidVolume"]
    first = pd.DataFrame([
        ["2020.01.31 23:59:59.000", "1.1002", "1.1000", "1", "2"],
        ["2020.02.01 00:00:01.000", "1.1003", "1.1001", "1", "2"],
    ], columns=columns)
    second = pd.DataFrame([
        ["2020.02.01 00:00:45.000", "1.1000", "1.1000", "0", "0"],
        ["2020.02.01 00:00:46.000", "1.0999", "1.1000", "1", "2"],
        ["2020.02.01 00:00:47.000", "1.1030", "1.1000", "1", "2"],
    ], columns=columns)
    first.to_csv(raw_root / "a.csv", index=False)
    second.to_csv(raw_root / "b.csv", index=False)
    params = {
        "timezone": "UTC", "timestamp_format": "%Y.%m.%d %H:%M:%S.%f",
        "pip_size": 0.0001, "gap_seconds": 30,
        "extreme_spread_pips": 20, "block_bytes": 1024,
    }
    audit_params = {
        **params, "checkpoint_batches": 1, "progress_batches": 100,
        "max_gap_examples": 5, "max_unassigned_examples": 5,
        "maximum_dataframe_mib": 20,
    }
    audits = [
        audit_source(raw_root / name, root, audit_params)
        for name in ("a.csv", "b.csv")
    ]
    states = [
        ingest_audited_source(raw_root, root, audit, params)
        for audit in audits
    ]
    audit_reference = root / "artifacts/test-audit.json"
    io.write_json(audit_reference, {"source_audits": [item["job_id"] for item in audits]})
    collection = build_collection(root, audits, states, params, audit_reference)
    return root, raw_root, audits, states, params, collection, first, second


def read_collection(root, collection):
    connection = io._connect(
        quote_files(root, collection), "quotes", root / "working",
        {"duckdb_memory_limit": "128MB", "duckdb_temp_limit": "1GB"},
    )
    try:
        return connection.execute(
            "SELECT * FROM quotes ORDER BY timestamp_utc, source_row"
        ).fetchdf()
    finally:
        connection.close()


def test_overlap_requires_review(built):
    _, _, audits, _, _, _, _, _ = built
    changed = copy.deepcopy(audits)
    changed[1]["utc_min"] = changed[0]["utc_max"]
    changed[1]["first_record"]["timestamp_utc"] = changed[0]["utc_max"]
    with pytest.raises(ValueError, match="overlap"):
        validate_audits(changed)


def test_unassigned_timestamp_requires_review(built):
    _, _, audits, _, _, _, _, _ = built
    changed = copy.deepcopy(audits)
    changed[0]["unassigned_timestamp_rows"] = 1
    with pytest.raises(ValueError):
        validate_audits(changed)


def test_boundary_gap_and_problematic_quotes_are_preserved(built):
    root, _, _, _, _, collection, _, _ = built
    frame = read_collection(root, collection)
    assert len(frame) == 5
    assert frame.loc[2, "source_gap_seconds"] == 44.0
    assert frame.loc[2, "flag_source_gap"]
    assert frame["flag_locked"].sum() == 1
    assert frame["flag_zero_total_size"].sum() == 1
    assert frame["flag_crossed"].sum() == 1
    assert frame["flag_extreme_spread"].sum() == 1


def test_split_and_combined_source_normalization_agree(built):
    root, raw_root, _, _, params, collection, first, second = built
    combined_path = raw_root / "combined.csv"
    pd.concat([first, second], ignore_index=True).to_csv(combined_path, index=False)
    combined = ingest_quotes(
        combined_path, root / "combined-data", root / "combined-manifests",
        "2020-01-31T00:00:00Z", "2020-02-02T00:00:00Z", **params,
    )
    files = [
        str(Path(combined["output_dir"]) / part["path"])
        for part in combined["parts"] if part["kind"] == "quotes"
    ]
    connection = io._connect(
        files, "quotes", root / "combined-working",
        {"duckdb_memory_limit": "128MB", "duckdb_temp_limit": "1GB"},
    )
    try:
        expected = connection.execute(
            "SELECT * FROM quotes ORDER BY timestamp_utc, source_row"
        ).fetchdf()
    finally:
        connection.close()
    actual = read_collection(root, collection)
    excluded = {"source_file", "source_sha256", "source_row"}
    columns = [name for name in expected if name not in excluded]
    pd.testing.assert_frame_equal(
        expected[columns], actual[columns],
        check_dtype=False, rtol=1e-10, atol=1e-12,
    )


def test_native_partition_is_unchanged(built):
    root, _, _, _, _, collection, _, _ = built
    patch = collection["boundary_patches"][0]
    original = root / patch["original_partition"]
    assert _sha256(original) == patch["original_partition_sha256"]


def test_binding_preserves_portable_manifest_and_legacy_integrity(built):
    root, _, _, _, _, collection, _, _ = built
    path = root / collection["manifest_path"]
    before = _sha256(path)
    binding_path = create_local_binding(root, path)
    binding = json.loads(binding_path.read_text(encoding="utf-8"))
    _verify_parts(binding)
    assert _sha256(path) == before
    assert binding["rows_in_scope"] == 5
    assert load_collection(root, path)["output_dir"] == str(root.resolve())


def test_changed_collection_partition_is_detected(built):
    root, _, _, _, _, collection, _, _ = built
    path = root / collection["parts"][0]["path"]
    path.write_bytes(b"changed")
    with pytest.raises(RuntimeError):
        verify_collection(root, collection)
