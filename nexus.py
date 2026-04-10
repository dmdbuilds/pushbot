"""Nexus payload parsing and label → TBA key conversion."""
import re
import logging
from typing import Optional

logger = logging.getLogger(__name__)


def nexus_label_to_tba_key(label: str, event_key: str) -> Optional[str]:
    """
    Convert a Nexus human-readable match label to a TBA match key.

    Examples:
      "Qualification 12"        → "2026cancmp_qm12"
      "Qualification 12 Replay" → "2026cancmp_qm12r"
      "Practice 4"              → "2026cancmp_pm4"
      "Playoff 8"               → "2026cancmp_sf1m8"  (approximate)
      "Final 1"                 → "2026cancmp_f1m1"
    """
    label = label.strip()

    replay = label.endswith(" Replay")
    if replay:
        label = label[:-7].strip()

    parts = label.rsplit(" ", 1)
    if len(parts) != 2:
        logger.warning("Cannot parse Nexus label: %s", label)
        return None

    level_word, num_str = parts[0].strip(), parts[1].strip()

    if not num_str.isdigit():
        logger.warning("Non-numeric match number in label: %s", label)
        return None

    num = int(num_str)

    level_map = {
        "Qualification": "qm",
        "Practice": "pm",
        "Playoff": "sf",
        "Final": "f",
    }
    level = level_map.get(level_word)
    if level is None:
        logger.warning("Unknown match level word: %s", level_word)
        return None

    suffix = "r" if replay else ""

    if level == "f":
        # Finals: "Final 1" → "2026cancmp_f1m1"
        return f"{event_key}_f1m{num}{suffix}"
    elif level == "sf":
        # Playoffs: "Playoff 8" → "2026cancmp_sf1m8" (approximate — set_number always 1 for now)
        return f"{event_key}_sf1m{num}{suffix}"
    else:
        return f"{event_key}_{level}{num}{suffix}"


def match_label_to_display(label: str) -> str:
    """Return a short display label like QM12, PM4, SF1, F1."""
    label = label.strip()
    replay = label.endswith(" Replay")
    if replay:
        label = label[:-7].strip()
    parts = label.rsplit(" ", 1)
    if len(parts) != 2:
        return label
    level_word, num = parts[0].strip(), parts[1].strip()
    abbrev_map = {
        "Qualification": "QM",
        "Practice": "PM",
        "Playoff": "SF",
        "Final": "F",
    }
    abbrev = abbrev_map.get(level_word, level_word[:2].upper())
    suffix = " Replay" if replay else ""
    return f"{abbrev}{num}{suffix}"


def parse_nexus_event_payload(payload: dict) -> dict:
    """
    Extract relevant fields from a Nexus live event status webhook payload.

    Returns a normalized dict with:
      event_key, data_as_of_time, now_queuing, on_deck, matches
    """
    return {
        "event_key": payload.get("eventKey", ""),
        "data_as_of_time": payload.get("dataAsOfTime", 0),
        "now_queuing": payload.get("nowQueuing"),
        "on_deck": payload.get("onDeck"),
        "matches": payload.get("matches", []),
        "announcements": payload.get("announcements", []),
    }


def parse_nexus_match_payload(payload: dict) -> dict:
    """
    Extract relevant fields from a Nexus team-specific match webhook payload.

    Returns a normalized dict with:
      event_key, data_as_of_time, match
    """
    return {
        "event_key": payload.get("eventKey", ""),
        "data_as_of_time": payload.get("dataAsOfTime", 0),
        "match": payload.get("match", {}),
    }


def find_match_in_payload(matches: list[dict], label: str) -> Optional[dict]:
    """Find a match by label in the matches list from a Nexus payload."""
    for m in matches:
        if m.get("label", "").strip() == label.strip():
            return m
    return None
