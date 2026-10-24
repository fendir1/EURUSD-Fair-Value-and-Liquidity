from pathlib import Path

import pandas as pd
import pytest

from eurusd_research import data


@pytest.fixture
def source(tmp_path):
    sample = Path(__file__).resolve().parents[1] / "data/sample/synthetic_quotes.csv"
    target = tmp_path / "quotes.csv"
    target.write_bytes(sample.read_bytes())
    return target


def run(source, root, block_bytes=128, start="2020-01-31T00:00:00Z"):
    return data.ingest_quotes(
        source, root / "data", root / "manifests",
        start, "2020-02-02T00:00:00Z",
        timezone="Europe/Helsinki",
        timestamp_format="%Y.%m.%d %H:%M:%S.%f",
        pip_size=0.0001,
        gap_seconds=30,
        extreme_spread_pips=20,
        block_bytes=block_bytes,
    )


def rows(state):
    frames = [
        pd.read_parquet(Path(state["output_dir"]) / part["path"])
        for part in state["parts"] if part["kind"] == "quotes"
    ]
    return pd.concat(frames, ignore_index=True).sort_values(
        "source_row"
    ).reset_index(drop=True)


def test_batch_consistency_and_quality(source, tmp_path):
    small = run(source, tmp_path / "small", 128)
    large = run(source, tmp_path / "large", 65536)
    pd.testing.assert_frame_equal(rows(small), rows(large))

    assert small["rows_scanned"] == 14
    assert small["rows_in_scope"] == 11
    assert small["rows_unassigned"] == 3
    assert small["source_flags"] == large["source_flags"]

    report = data.summarise_generation(small)
    assert report["duplicate_field_excess_rows"] == 1
    assert report["repeated_timestamp_excess_rows"] == 2
    assert report["scope_flags"]["flag_crossed"] == 1
    assert report["scope_flags"]["flag_locked"] == 1
    assert report["scope_flags"]["flag_missing_size"] == 1
    assert report["scope_flags"]["flag_negative_size"] == 1
    assert report["scope_flags"]["flag_zero_total_size"] == 1


def test_completed_rerun_and_changed_source(source, tmp_path):
    root = tmp_path / "run"
    first = run(source, root)
    repeated = run(source, root)
    assert repeated["cache_hit"]
    assert first["manifest_path"] == repeated["manifest_path"]
    pd.testing.assert_frame_equal(rows(first), rows(repeated))

    source.write_bytes(source.read_bytes() + b"\n")
    with pytest.raises(ValueError, match="Source content changed"):
        run(source, root)


def test_damaged_output_is_detected(source, tmp_path):
    root = tmp_path / "run"
    state = run(source, root)
    part = next(part for part in state["parts"] if part["kind"] == "quotes")
    (Path(state["output_dir"]) / part["path"]).write_bytes(b"damaged")
    with pytest.raises(RuntimeError, match="Output checksum"):
        run(source, root)


def test_interrupted_empty_output_batch_resumes(source, tmp_path, monkeypatch):
    original = data._save_json
    interrupted = False

    def save_then_interrupt(path, value):
        nonlocal interrupted
        original(path, value)
        if (
            Path(path).name.startswith("job-")
            and value.get("next_batch") == 1
            and value.get("status") == "running"
            and not interrupted
        ):
            interrupted = True
            raise RuntimeError("simulated interruption")

    monkeypatch.setattr(data, "_save_json", save_then_interrupt)
    root = tmp_path / "resume"

    with pytest.raises(RuntimeError, match="simulated interruption"):
        run(source, root, start="2020-02-01T00:00:00Z")

    resumed = run(source, root, start="2020-02-01T00:00:00Z")
    reference = run(
        source, tmp_path / "reference", 65536,
        start="2020-02-01T00:00:00Z",
    )
    pd.testing.assert_frame_equal(rows(resumed), rows(reference))
