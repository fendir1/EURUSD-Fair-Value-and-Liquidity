"""Vectorized parsing and quote-quality flags."""

import numpy as np
import pandas as pd

EXPECTED_COLUMNS = ["Time (EET)", "Ask", "Bid", "AskVolume", "BidVolume"]


def normalise_quotes(
    raw,
    *,
    source_name,
    source_sha256,
    first_row,
    previous_utc,
    timezone,
    timestamp_format,
    pip_size,
    gap_seconds,
    extreme_spread_pips,
):
    if list(raw.columns) != EXPECTED_COLUMNS:
        raise ValueError(f"Unexpected CSV schema: {list(raw.columns)}")

    frame = raw.rename(columns={
        "Time (EET)": "timestamp_source",
        "Ask": "ask_raw",
        "Bid": "bid_raw",
        "AskVolume": "ask_size_raw",
        "BidVolume": "bid_size_raw",
    }).reset_index(drop=True).fillna("")

    source_text = frame["timestamp_source"].str.strip()
    parsed = pd.to_datetime(
        source_text, format=timestamp_format, errors="coerce", exact=True
    )
    utc = parsed.dt.tz_localize(
        timezone, ambiguous="NaT", nonexistent="NaT"
    ).dt.tz_convert("UTC")

    frame["timestamp_utc"] = utc
    frame["source_file"] = source_name
    frame["source_sha256"] = source_sha256
    frame["source_row"] = np.arange(
        first_row, first_row + len(frame), dtype=np.int64
    )

    for name in ("ask", "bid", "ask_size", "bid_size"):
        frame[name] = pd.to_numeric(
            frame[f"{name}_raw"].str.strip(), errors="coerce"
        ).to_numpy(dtype="float64", na_value=np.nan)

    ask, bid = frame["ask"], frame["bid"]
    ask_size, bid_size = frame["ask_size"], frame["bid_size"]
    valid_price = (
        np.isfinite(ask) & np.isfinite(bid) & ask.gt(0) & bid.gt(0)
    )
    ask_blank = frame["ask_size_raw"].str.strip().eq("")
    bid_blank = frame["bid_size_raw"].str.strip().eq("")

    frame["flag_missing_timestamp"] = source_text.eq("")
    frame["flag_unparseable_timestamp"] = source_text.ne("") & parsed.isna()
    frame["flag_unresolved_local_time"] = parsed.notna() & utc.isna()
    frame["flag_invalid_price"] = ~valid_price
    frame["flag_nonpositive_price"] = (
        (np.isfinite(ask) & ask.le(0))
        | (np.isfinite(bid) & bid.le(0))
    )
    frame["flag_crossed"] = valid_price & ask.lt(bid)
    frame["flag_locked"] = valid_price & ask.eq(bid)
    frame["flag_missing_size"] = ask_blank | bid_blank
    frame["flag_invalid_size"] = (
        (~np.isfinite(ask_size) & ~ask_blank)
        | (~np.isfinite(bid_size) & ~bid_blank)
    )
    frame["flag_zero_size"] = ask_size.eq(0) | bid_size.eq(0)
    frame["flag_negative_size"] = (
        (np.isfinite(ask_size) & ask_size.lt(0))
        | (np.isfinite(bid_size) & bid_size.lt(0))
    )
    frame["flag_zero_total_size"] = (
        np.isfinite(ask_size) & np.isfinite(bid_size)
        & ask_size.ge(0) & bid_size.ge(0)
        & (ask_size + bid_size).eq(0)
    )

    frame["spread_pips"] = ((ask - bid) / pip_size).where(valid_price)
    frame["flag_extreme_spread"] = frame["spread_pips"].gt(
        extreme_spread_pips
    )

    previous = utc.shift()
    if len(frame) and previous_utc is not None:
        previous.iloc[0] = pd.Timestamp(previous_utc)

    difference = (utc - previous).dt.total_seconds()
    frame["source_gap_seconds"] = difference
    frame["flag_backward_timestamp"] = difference.lt(0)
    frame["flag_adjacent_repeated_timestamp"] = (
        utc.notna() & previous.notna() & utc.eq(previous)
    )
    frame["flag_source_gap"] = difference.gt(gap_seconds)

    return frame
