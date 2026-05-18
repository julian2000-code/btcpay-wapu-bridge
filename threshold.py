"""
Conversion threshold tracker.
Tracks how many sats have been converted to ARS this month.
Once the threshold is reached, payments stay in Lightning (BTCPay wallet).

Storage: simple JSON file — no database needed.
"""

import json
import os
from datetime import datetime
from pathlib import Path

THRESHOLD_FILE = Path("data/threshold.json")
_DEFAULT_THRESHOLD_SAT = int(os.getenv("CONVERT_THRESHOLD_SAT", 500000))


def _current_month() -> str:
    """Returns current month as YYYY-MM string."""
    return datetime.now().strftime("%Y-%m")


def _load() -> dict:
    """Load threshold data from disk."""
    if not THRESHOLD_FILE.exists():
        THRESHOLD_FILE.parent.mkdir(parents=True, exist_ok=True)
        return {"month": _current_month(), "threshold_sat": _DEFAULT_THRESHOLD_SAT, "converted_sat": 0, "payments": []}

    with open(THRESHOLD_FILE) as f:
        data = json.load(f)

    # Reset if it's a new month (keep the saved threshold setting)
    if data.get("month") != _current_month():
        data = {"month": _current_month(), "threshold_sat": data.get("threshold_sat", _DEFAULT_THRESHOLD_SAT), "converted_sat": 0, "payments": []}
        _save(data)

    # Backfill threshold_sat if missing from old file
    if "threshold_sat" not in data:
        data["threshold_sat"] = _DEFAULT_THRESHOLD_SAT
        _save(data)

    return data


def get_threshold_sat() -> int:
    """Returns the current threshold value (from file or env default)."""
    return _load().get("threshold_sat", _DEFAULT_THRESHOLD_SAT)


def update_threshold(new_threshold_sat: int):
    """Update the monthly conversion threshold and persist it."""
    data = _load()
    data["threshold_sat"] = new_threshold_sat
    _save(data)


def _save(data: dict):
    """Save threshold data to disk."""
    THRESHOLD_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(THRESHOLD_FILE, "w") as f:
        json.dump(data, f, indent=2)


def get_status() -> dict:
    """
    Returns current threshold status for this month.
    """
    data = _load()
    converted = data["converted_sat"]
    threshold = data.get("threshold_sat", _DEFAULT_THRESHOLD_SAT)
    remaining = max(0, threshold - converted)
    above_threshold = max(0, converted - threshold)

    return {
        "month": data["month"],
        "threshold_sat": threshold,
        "converted_sat": converted,
        "remaining_to_convert_sat": remaining,
        "above_threshold_sat": above_threshold,
        "threshold_reached": converted >= threshold,
        "payments": data["payments"]
    }


def should_convert(amount_sat: float) -> tuple[bool, float, float]:
    """
    Decides how much of a payment to convert vs keep in sats.

    Returns:
        (should_convert, convert_sat, keep_sat)
    """
    data = _load()
    converted = data["converted_sat"]
    threshold = data.get("threshold_sat", _DEFAULT_THRESHOLD_SAT)
    remaining = max(0, threshold - converted)

    if remaining <= 0:
        # Threshold already reached — keep everything in sats
        return False, 0, amount_sat

    if amount_sat <= remaining:
        # Entire payment fits under threshold — convert all
        return True, amount_sat, 0
    else:
        # Split: convert up to threshold, keep the rest
        return True, remaining, amount_sat - remaining


def record_conversion(amount_sat: float, ars_amount: float, invoice_id: str):
    """
    Record a successful conversion to ARS.
    """
    data = _load()
    data["converted_sat"] += amount_sat
    data["payments"].append({
        "date": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "invoice_id": invoice_id,
        "amount_sat": amount_sat,
        "ars_amount": ars_amount,
        "type": "converted"
    })
    _save(data)


def record_kept(amount_sat: float, invoice_id: str):
    """
    Record a payment kept in Lightning (above threshold).
    """
    data = _load()
    data["payments"].append({
        "date": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "invoice_id": invoice_id,
        "amount_sat": amount_sat,
        "ars_amount": 0,
        "type": "kept_in_lightning"
    })
    _save(data)
