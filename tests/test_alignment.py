import pandas as pd
import pytest

from eurusd_research.quality import normalise_quotes


def normalise(times, **overrides):
    raw = pd.DataFrame({
        "Time (EET)": times,
        "Ask": ["1.1002"] * len(times),
        "Bid": ["1.1000"] * len(times),
        "AskVolume": ["1"] * len(times),
        "BidVolume": ["3"] * len(times),
    })
    options = {
        "source_name": "synthetic.csv",
        "source_sha256": "test",
        "first_row": 1,
        "previous_utc": None,
        "timezone": "Europe/Helsinki",
        "timestamp_format": "%Y.%m.%d %H:%M:%S.%f",
        "pip_size": 0.0001,
        "gap_seconds": 30,
        "extreme_spread_pips": 20,
    }
    options.update(overrides)
    return normalise_quotes(raw, **options)


@pytest.mark.parametrize("source, expected", [
    ("2025.01.06 00:00:11.712", "2025-01-05T22:00:11.712Z"),
    ("2025.03.09 23:00:09.602", "2025-03-09T21:00:09.602Z"),
    ("2025.03.31 00:00:09.314", "2025-03-30T21:00:09.314Z"),
    ("2025.07.07 00:00:17.843", "2025-07-06T21:00:17.843Z"),
    ("2025.10.26 23:00:03.449", "2025-10-26T21:00:03.449Z"),
])
def test_historical_clock_conversion(source, expected):
    result = normalise([source])
    assert result.loc[0, "timestamp_utc"] == pd.Timestamp(expected)
    assert result.loc[0, "spread_pips"] == pytest.approx(2.0)


def test_dst_times_are_not_guessed():
    result = normalise([
        "2020.03.29 03:30:00.000",
        "2020.10.25 03:30:00.000",
        "not-a-date",
    ])
    assert result["timestamp_utc"].isna().all()
    assert result["flag_unresolved_local_time"].tolist() == [True, True, False]
    assert result["flag_unparseable_timestamp"].tolist() == [False, False, True]


def test_previous_batch_timestamp_is_used():
    result = normalise(
        ["2020.02.01 02:00:00.000"],
        previous_utc="2020-02-01T00:00:01Z",
    )
    assert bool(result.loc[0, "flag_backward_timestamp"])
