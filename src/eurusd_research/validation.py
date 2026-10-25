"""Chronological pilot plans and horizon-specific label purging."""

import numpy as np
import pandas as pd


def utc(value):
    value = pd.Timestamp(value)
    if pd.isna(value) or value.tzinfo is None:
        raise ValueError("A valid timezone-aware timestamp is required.")
    value = value.tz_convert("UTC")
    if value.microsecond or value.nanosecond:
        raise ValueError("Split boundaries must use whole seconds.")
    return value


def validate_plan(plan):
    periods = plan["periods"]
    if [item["role"] for item in periods] != [
        "train", "validation", "pilot_holdout"
    ]:
        raise ValueError("Expected train, validation, and pilot_holdout.")
    previous_end = None
    for item in periods:
        start, end = utc(item["start_utc"]), utc(item["end_utc"])
        if start >= end or (previous_end is not None and start != previous_end):
            raise ValueError("Split periods must be positive and contiguous.")
        previous_end = end
    if plan.get("project_final_test") is not None:
        raise ValueError("This pilot does not define the full-history final test.")
    if not plan.get("feature_job_id"):
        raise ValueError("The plan must identify its feature generation.")
    return plan


def make_pilot_plan(daily, start, end, feature_job_id,
                    training_fraction=0.6, validation_fraction=0.2):
    start, end = utc(start), utc(end)
    if not (
        0 < training_fraction < 1
        and 0 < validation_fraction < 1
        and training_fraction + validation_fraction < 1
    ):
        raise ValueError("Invalid pilot fractions.")

    dates = pd.to_datetime(
        daily.loc[daily["usable_price_rows"].gt(0), "date_utc"], utc=True
    ).drop_duplicates().sort_values().tolist()
    if len(dates) < 10:
        raise ValueError("At least 10 dates with usable prices are required.")
    if any(date < start.normalize() or date >= end for date in dates):
        raise ValueError("Daily coverage lies outside the requested research range.")

    first_cut = int(np.floor(len(dates) * training_fraction))
    second_cut = int(np.floor(
        len(dates) * (training_fraction + validation_fraction)
    ))
    if min(first_cut, second_cut - first_cut, len(dates) - second_cut) < 2:
        raise ValueError("Each pilot segment needs at least two active dates.")

    boundaries = [start, dates[first_cut], dates[second_cut], end]
    roles = ["train", "validation", "pilot_holdout"]
    plan = {
        "stage": "january_development_pilot",
        "feature_job_id": feature_job_id,
        "selection_rule": "chronological fractions of UTC dates with usable prices",
        "training_fraction": training_fraction,
        "validation_fraction": validation_fraction,
        "active_utc_dates": len(dates),
        "project_final_test": None,
        "periods": [
            {
                "role": role,
                "start_utc": boundaries[i].isoformat(),
                "end_utc": boundaries[i + 1].isoformat(),
            }
            for i, role in enumerate(roles)
        ],
    }
    return validate_plan(plan)


def assign_split(labels, plan, horizon):
    validate_plan(plan)
    timestamp = labels["feature_timestamp_utc"]
    target = labels[f"target_timestamp_utc_{horizon}s"]
    valid = labels[f"label_valid_{horizon}s"]
    result = pd.Series("invalid_label", index=labels.index, dtype="string")
    result.loc[valid] = "outside_plan"

    for period in plan["periods"]:
        start, end = utc(period["start_utc"]), utc(period["end_utc"])
        inside = valid & timestamp.ge(start) & timestamp.lt(end)
        result.loc[inside] = period["role"]
        result.loc[inside & target.ge(end)] = "purged_" + period["role"]

    return result
