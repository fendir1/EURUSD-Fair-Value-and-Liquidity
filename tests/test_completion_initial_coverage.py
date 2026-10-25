"""Regression checks for intervals without source observations."""

import pandas as pd

from test_completion import policy, quotes
from eurusd_research import features as io
from eurusd_research.completion_data import (
    assemble_block, read_quotes, source_index,
)


def source_fixture(tmp_path):
    q = quotes()
    path = tmp_path / "quotes.parquet"
    q[io.QUOTE_COLUMNS].to_parquet(path, index=False)
    collection = {
        "parts": [{
            "path": "quotes.parquet",
            "min_utc": q["timestamp_utc"].iloc[0].isoformat(),
            "max_utc": q["timestamp_utc"].iloc[-1].isoformat(),
        }]
    }
    return q, collection, source_index(collection)


def test_before_first_quote_remains_unavailable(tmp_path):
    q, collection, index = source_fixture(tmp_path)
    protocol = policy()
    start = q["timestamp_utc"].iloc[0] - pd.Timedelta(hours=1)
    end = start + pd.Timedelta(seconds=3)

    empty = read_quotes(
        tmp_path, collection, index, start, end,
        protocol["feature_settings"], tmp_path / "working",
    )

    assert empty.empty
    assert list(empty.columns) == io.QUOTE_COLUMNS
    assert str(empty["timestamp_utc"].dt.tz) == "UTC"

    output, _ = assemble_block(
        empty, start, end, protocol, q["timestamp_utc"].iloc[0]
    )
    assert len(output) == 3
    assert output["source_timestamp_utc"].isna().all()
    assert output["state_reason"].eq("no_source_quote").all()
    assert not output["price_valid"].any()
    for h in protocol["horizons_seconds"]:
        assert not output[f"label_valid_{h}s"].any()


def test_gap_after_history_retains_only_past_predecessor(tmp_path):
    q, collection, index = source_fixture(tmp_path)
    protocol = policy()
    settings = {
        **protocol["feature_settings"],
        "max_quotes_per_block": 10000,
        "duckdb_memory_limit": "64MB",
        "duckdb_temp_limit": "1GB",
    }
    start = q["timestamp_utc"].iloc[-1] + pd.Timedelta(hours=1)
    end = start + pd.Timedelta(seconds=3)

    previous = read_quotes(
        tmp_path, collection, index, start, end,
        settings, tmp_path / "working",
    )

    assert len(previous) == 1
    assert previous["timestamp_utc"].iloc[0] == q["timestamp_utc"].iloc[-1]
    assert previous["timestamp_utc"].lt(start).all()

    output, _ = assemble_block(
        previous, start, end, protocol, q["timestamp_utc"].iloc[0]
    )
    assert not output["price_valid"].any()
    assert output["state_reason"].eq("stale_snapshot").all()
