"""Event normalization, event -> group mapping, and group-strength indices.

TFRRS event labels are messy ('100 Meters', '100m', '100 M', 'Shot Put',
'SP', '3000m Steeplechase', '5,000 Meters'...). normalize_event() collapses
them to the canonical labels used in config/scoring.yaml.
"""
from __future__ import annotations
import re
from functools import lru_cache

import yaml

FIELD_EVENTS = {
    "High Jump", "Pole Vault", "Long Jump", "Triple Jump",
    "Shot Put", "Weight Throw", "Discus", "Hammer", "Javelin",
}
MULTI_EVENTS = {"Heptathlon", "Pentathlon", "Decathlon"}


def load_group_map(scoring_yaml_path: str) -> dict:
    with open(scoring_yaml_path) as f:
        cfg = yaml.safe_load(f)
    event_to_group = {}
    for group, events in cfg["event_groups"].items():
        for e in events:
            event_to_group[e] = group
    return event_to_group


_ALIASES = [
    (r"^\s*60\s*m(eters)?\s*hurdles?", "60m Hurdles"),
    (r"^\s*100\s*m(eters)?\s*hurdles?", "100m Hurdles"),
    (r"^\s*110\s*m(eters)?\s*hurdles?", "110m Hurdles"),
    (r"^\s*400\s*m(eters)?\s*hurdles?", "400m Hurdles"),
    (r"^\s*60\s*m(eters)?$", "60m"),
    (r"^\s*100\s*m(eters)?$", "100m"),
    (r"^\s*200\s*m(eters)?$", "200m"),
    (r"^\s*400\s*m(eters)?$", "400m"),
    (r"^\s*800\s*m(eters)?$", "800m"),
    (r"^\s*1000\s*m(eters)?$", "1000m"),
    (r"^\s*1500\s*m(eters)?$", "1500m"),
    (r"^\s*mile", "Mile"),
    (r"^\s*3000\s*m(eters)?\s*steeple", "3000m Steeplechase"),
    (r"^\s*3000\s*m(eters)?$", "3000m"),
    (r"^\s*5[,]?000\s*m(eters)?$", "5000m"),
    (r"^\s*10[,]?000\s*m(eters)?$", "10000m"),
    (r"4\s*x\s*100", "4x100 Relay"),
    (r"4\s*x\s*400", "4x400 Relay"),
    (r"distance medley|^\s*dmr", "Distance Medley Relay"),
    (r"high jump|^\s*hj$", "High Jump"),
    (r"pole vault|^\s*pv$", "Pole Vault"),
    (r"long jump|^\s*lj$", "Long Jump"),
    (r"triple jump|^\s*tj$", "Triple Jump"),
    (r"shot put|^\s*sp$", "Shot Put"),
    (r"weight throw|^\s*wt$", "Weight Throw"),
    (r"discus", "Discus"),
    (r"hammer", "Hammer"),
    (r"javelin", "Javelin"),
    (r"heptathlon", "Heptathlon"),
    (r"pentathlon", "Pentathlon"),
    (r"decathlon", "Decathlon"),
    (r"8\s*k|8000", "XC 8k"),
    (r"6\s*k|6000", "XC 6k"),
    (r"10\s*k(?!.*m)|10000\s*.*xc", "XC 10k"),
]


@lru_cache(maxsize=4096)
def normalize_event(raw: str, sport: str = "") -> str:
    s = raw.strip().lower()
    for pat, canon in _ALIASES:
        if re.search(pat, s):
            return canon
    # XC generic
    if sport == "xc":
        return "XC 8k"
    return raw.strip()


def is_field_event(event_norm: str) -> bool:
    return event_norm in FIELD_EVENTS or event_norm in MULTI_EVENTS


def group_strength_index(marks_percentiles: list[float]) -> float:
    """Event-group strength index in [0,100].

    Input: list of each athlete's mark expressed as a *conference percentile*
    for that event (100 = conference best). We reward both TOP-END talent
    (the single best) and DEPTH (how many score-capable athletes), because
    conference meets are won by depth.

        strength = 0.5 * best + 0.5 * mean(top_k)

    where top_k defaults to the top 6 (roughly the scoring window). This keeps
    the index comparable across event groups and teams.
    """
    if not marks_percentiles:
        return 0.0
    s = sorted(marks_percentiles, reverse=True)
    best = s[0]
    top_k = s[:6]
    depth = sum(top_k) / len(top_k)
    return round(0.5 * best + 0.5 * depth, 1)
