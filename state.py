"""Thread-safe shared state for Push Bot."""
import threading
from typing import Optional

lock = threading.Lock()

# Match keys that have already had alerts sent
triggered_queuing: set[str] = set()   # match keys → queuing alert sent
triggered_ondeck: set[str] = set()    # match keys → on-deck alert sent
triggered_done: set[str] = set()      # match keys → results alert sent

# Active TBA polling: match_key → {'etag': str, 'last_poll': float}
polling_matches: dict[str, dict] = {}

# Manual scout confirmations: 'QM12:Kaveesh' → True
confirmed_scouts: dict[str, bool] = {}

_CONFIRM_FILE = "/tmp/pushbot_confirmations.json"

def _load_confirmations() -> None:
    """Load persisted confirmations from disk on startup."""
    global confirmed_scouts
    import json, os
    if os.path.exists(_CONFIRM_FILE):
        try:
            with open(_CONFIRM_FILE, "r") as f:
                confirmed_scouts = json.load(f)
        except Exception:
            confirmed_scouts = {}

def _save_confirmations() -> None:
    """Persist confirmations to disk."""
    import json
    try:
        with open(_CONFIRM_FILE, "w") as f:
            json.dump(confirmed_scouts, f)
    except Exception:
        pass

_load_confirmations()

# Last processed dataAsOfTime per event key (for Nexus dedup)
last_nexus_time: dict[str, int] = {}

# Last processed dataAsOfTime per match label (for Nexus match webhook dedup)
last_nexus_match_time: dict[str, int] = {}


def mark_queuing(match_key: str) -> bool:
    """Returns True if this is the first time we're marking this match as queuing."""
    with lock:
        if match_key in triggered_queuing:
            return False
        triggered_queuing.add(match_key)
        return True


def mark_ondeck(match_key: str) -> bool:
    """Returns True if first time marking on-deck."""
    with lock:
        if match_key in triggered_ondeck:
            return False
        triggered_ondeck.add(match_key)
        return True


def mark_done(match_key: str) -> bool:
    """Returns True if first time marking done."""
    with lock:
        if match_key in triggered_done:
            return False
        triggered_done.add(match_key)
        return True


def start_polling(match_key: str) -> None:
    with lock:
        if match_key not in polling_matches:
            polling_matches[match_key] = {"etag": None, "last_poll": 0.0}


def stop_polling(match_key: str) -> None:
    with lock:
        polling_matches.pop(match_key, None)


def get_polling_keys() -> list[str]:
    with lock:
        return list(polling_matches.keys())


def get_etag(match_key: str) -> Optional[str]:
    with lock:
        entry = polling_matches.get(match_key)
        return entry["etag"] if entry else None


def set_etag(match_key: str, etag: str) -> None:
    with lock:
        if match_key in polling_matches:
            polling_matches[match_key]["etag"] = etag


def _normalize_name(name: str) -> str:
    """Lowercase and strip to first name only (first word)."""
    return name.strip().lower().split()[0] if name.strip() else ""


def confirm_scout(match_label: str, scout_name: str) -> None:
    key = f"{match_label.upper()}:{_normalize_name(scout_name)}"
    with lock:
        confirmed_scouts[key] = True
        _save_confirmations()


def is_confirmed(match_label: str, scout_name: str) -> bool:
    key = f"{match_label.upper()}:{_normalize_name(scout_name)}"
    with lock:
        return confirmed_scouts.get(key, False)


def update_nexus_time(event_key: str, data_as_of_time: int) -> bool:
    """Returns True if this payload is newer than the last processed one."""
    with lock:
        last = last_nexus_time.get(event_key, 0)
        if data_as_of_time <= last:
            return False
        last_nexus_time[event_key] = data_as_of_time
        return True


def update_nexus_match_time(match_label: str, data_as_of_time: int) -> bool:
    """Returns True if this match payload is newer than the last processed one."""
    with lock:
        last = last_nexus_match_time.get(match_label, 0)
        if data_as_of_time <= last:
            return False
        last_nexus_match_time[match_label] = data_as_of_time
        return True

# Tracks which matches have had their post-match summary posted
posted_summaries: set[str] = set()


def is_summary_posted(match_key: str) -> bool:
    with lock:
        return match_key in posted_summaries


def mark_summary_posted(match_key: str) -> None:
    with lock:
        posted_summaries.add(match_key)
