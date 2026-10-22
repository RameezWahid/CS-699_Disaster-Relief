"""Urgency scoring (spec 4.4). Credibility and incident clustering arrive in Phase 2."""

import math

# Category severity C in [0, 1]. Hand-set: life-threatening first, information last.
CATEGORY_SEVERITY = {
    "rescue": 1.0,
    "medical": 0.9,
    "shelter": 0.6,
    "food_water": 0.5,
    "road_blocked": 0.4,
    "info_noise": 0.1,
}
# Starting weights from the spec, to be tuned on the replay scenarios. They must sum to 1.
URGENCY_WEIGHTS = {"category": 0.35, "headcount": 0.25, "vulnerability": 0.20, "waiting": 0.20}
HEADCOUNT_CAP = 10
WAITING_FULL_MIN = 120


def urgency(
    category: str, headcount: int | None, vulnerable: bool, waited_min: float, override: str | None
) -> dict:
    """S = w1*C + w2*min(N/Nmax, 1) + w3*V + w4*T; level = min(5, 1 + floor(5S)), 5 on override."""
    factors = {
        "category": CATEGORY_SEVERITY[category],
        "headcount": min((headcount or 0) / HEADCOUNT_CAP, 1.0),
        "vulnerability": 1.0 if vulnerable else 0.0,
        "waiting": min(waited_min / WAITING_FULL_MIN, 1.0),
    }
    score = sum(URGENCY_WEIGHTS[name] * value for name, value in factors.items())
    base_level = min(5, 1 + math.floor(5 * score))
    return {
        "S": round(score, 3),
        "factors": factors,
        "base_level": base_level,
        "level": 5 if override else base_level,
        "override": override,
    }


# Category groups for clustering: reports only merge within a group, and rescue never merges
# with supply (spec 4.4).
CATEGORY_GROUP = {
    "rescue": "rescue",
    "medical": "medical",
    "food_water": "supply",
    "shelter": "supply",
    "road_blocked": "road",
    "info_noise": "info",
}
# Signal reliabilities r_i from spec 4.4. Hand-set starting points, to be calibrated on scenarios.
SIGNAL_RELIABILITY = {
    "sender": 0.30,
    "gps": 0.15,
    "photo": 0.20,
    "heavy_rain": 0.20,
    "chronic_spot": 0.15,
    "official_alert": 0.25,
    "responder": 0.80,
}
# Copies are collapsed first and senders capped, because noisy-OR assumes independent signals.
MAX_COUNTED_SENDERS = 3
VERIFY_BELOW_CREDIBILITY = 0.5


def credibility(
    senders: int,
    gps: bool,
    photo: bool,
    heavy_rain: bool,
    chronic_spot: bool,
    official_alert: bool,
    responder: bool,
) -> dict:
    """Noisy-OR: R = 1 - prod(1 - r_i) over the signals present."""
    present = {
        "gps": gps,
        "photo": photo,
        "heavy_rain": heavy_rain,
        "chronic_spot": chronic_spot,
        "official_alert": official_alert,
        "responder": responder,
    }
    miss = (1 - SIGNAL_RELIABILITY["sender"]) ** min(senders, MAX_COUNTED_SENDERS)
    for name, is_present in present.items():
        if is_present:
            miss *= 1 - SIGNAL_RELIABILITY[name]
    return {"R": round(1 - miss, 3), "signals": {"senders": senders, **present}}
