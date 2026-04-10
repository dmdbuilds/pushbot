"""Google Sheets schedule reader with 10-minute in-memory cache."""
import json
import logging
import os
import re
import time
from typing import Optional

import gspread
from google.oauth2.service_account import Credentials

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/spreadsheets.readonly"]

# Cache
_cache_data: Optional[dict[str, dict]] = None   # match_label → {role: [names]}
_cache_time: float = 0.0
CACHE_TTL = 600  # 10 minutes


def _get_client() -> gspread.Client:
    creds_json = os.environ["SERVICE_ACCOUNT_JSON"]
    creds_info = json.loads(creds_json)
    creds = Credentials.from_service_account_info(creds_info, scopes=SCOPES)
    return gspread.authorize(creds)


def parse_names(cell_value: str) -> list[str]:
    """Parse a simple single-name cell (Rotating Pit, Pit Ambassador).
    Returns empty list if the cell looks like a note/instruction block."""
    if not cell_value:
        return []
    # Long cells or cells with arrows are notes/match-scout cells, not names
    if len(cell_value.strip()) > 60 or "\u2192" in cell_value or "|" in cell_value:
        return []
    names = []
    for line in str(cell_value).strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        if re.match(r"^[\d,\s]+$", line):
            continue
        if line.upper() in ("BREAK", "FLUID PIT SCOUTING", "X", "TBD", "N/A"):
            continue
        if len(line) > 30 or len(line.split()) > 3:
            continue
        if line[0].islower():
            continue
        names.append(line)
    return names


def parse_pit_scouts(cell_value: str) -> list[str]:
    """Parse Pit Scout column: names separated by ' - ' e.g. 'Neel - Noah - Muhammad'."""
    if not cell_value or "\u2192" in cell_value:
        return []
    names = []
    raw = cell_value.replace("\n", " - ")
    for part in raw.split(" - "):
        part = part.strip()
        if not part:
            continue
        if re.match(r"^[\d,\s]+$", part):
            continue
        if part.upper() in ("BREAK", "X", "TBD"):
            continue
        if part[0].islower():
            continue
        if len(part) > 30:
            continue
        names.append(part)
    return names


def parse_match_scouts(cell_value: str) -> list[str]:
    """Parse Match Scout column: 'Name → TXXXX [role] (for QMN) | Name → ...'
    Returns list of scout names."""
    if not cell_value or "\u2192" not in cell_value:
        return []
    names = []
    for entry in cell_value.split(" | "):
        entry = entry.strip()
        if "\u2192" not in entry:
            continue
        name = entry.split("\u2192")[0].strip()
        if name:
            names.append(name)
    return names


# Column indices verified against actual DCMP sheet structure
# Col 0: Estimated Time
# Col 1: Match Number
# Col 2: Rotating Pit     — single name, carry-forward
# Col 4: Permanent Pit    — instruction note blob, IGNORED
# Col 6: Pit Scout        — dash-separated names, carry-forward
# Col 8: Match Scout      — "Name → TXXXX [role] (for QMN) | ..." format
# Col 9: AllianceColor#   — metadata, not a scout name
# Col 10: Pit Ambassador  — single name, carry-forward
MATCH_COL = 1
ROT_PIT_COL = 2
PIT_SCOUT_COL = 6
MATCH_SCOUT_COL = 8
PIT_AMB_COL = 10
MATCH_SCOUT_ROLES = ["Match Scout"]  # kept for compatibility
ROLE_COLUMNS = {}  # not used in new loader


def _load_schedule() -> dict[str, dict]:
    """
    Load and parse the Google Sheet.
    Returns dict: match_label (e.g. 'QM1') → {role: [names]}
    """
    client = _get_client()
    spreadsheet_id = os.environ["SPREADSHEET_ID"]
    sheet_name = os.environ.get("SHEET_NAME", "Detailed Shifts")

    sh = client.open_by_key(spreadsheet_id)
    ws = sh.worksheet(sheet_name)
    rows = ws.get_all_values()

    schedule: dict[str, dict] = {}

    # Carry-forward state
    rot_pit_carry: list[str] = []
    pit_scout_carry: list[str] = []
    pit_amb_carry: list[str] = []

    for row in rows:
        while len(row) <= max(MATCH_SCOUT_COL, PIT_AMB_COL) + 1:
            row.append("")

        match_label_raw = row[MATCH_COL].strip() if len(row) > MATCH_COL else ""
        if not match_label_raw:
            continue
        match_label = match_label_raw.upper()
        if not re.match(r"^(QM|SF|F|PM)\d+", match_label):
            continue

        if match_label not in schedule:
            schedule[match_label] = {}

        # Col 2: Rotating Pit
        if row[ROT_PIT_COL].strip():
            rot_pit_carry = parse_names(row[ROT_PIT_COL])
        schedule[match_label].setdefault("Rotating Pit", [])
        for name in rot_pit_carry:
            if name not in schedule[match_label]["Rotating Pit"]:
                schedule[match_label]["Rotating Pit"].append(name)

        # Col 6: Pit Scout (dash-separated names)
        if row[PIT_SCOUT_COL].strip():
            pit_scout_carry = parse_pit_scouts(row[PIT_SCOUT_COL])
        schedule[match_label].setdefault("Pit Scout", [])
        for name in pit_scout_carry:
            if name not in schedule[match_label]["Pit Scout"]:
                schedule[match_label]["Pit Scout"].append(name)

        # Col 8: Match Scout (Name → team format)
        if row[MATCH_SCOUT_COL].strip():
            match_names = parse_match_scouts(row[MATCH_SCOUT_COL])
            schedule[match_label].setdefault("Match Scout", [])
            for name in match_names:
                if name not in schedule[match_label]["Match Scout"]:
                    schedule[match_label]["Match Scout"].append(name)

        # Col 10: Pit Ambassador
        if row[PIT_AMB_COL].strip():
            pit_amb_carry = parse_names(row[PIT_AMB_COL])
        schedule[match_label].setdefault("Pit Ambassador", [])
        for name in pit_amb_carry:
            if name not in schedule[match_label]["Pit Ambassador"]:
                schedule[match_label]["Pit Ambassador"].append(name)

    logger.info("Loaded schedule: %d match entries", len(schedule))
    return schedule


def get_schedule(force_refresh: bool = False) -> dict[str, dict]:
    """Return cached schedule, refreshing if stale or forced."""
    global _cache_data, _cache_time

    if force_refresh or _cache_data is None or (time.time() - _cache_time) > CACHE_TTL:
        try:
            _cache_data = _load_schedule()
            _cache_time = time.time()
        except Exception as e:
            logger.error("Failed to load schedule from Sheets: %s", e)
            if _cache_data is None:
                _cache_data = {}

    return _cache_data


def get_scouts_for_match(match_label: str, force_refresh: bool = False) -> dict[str, list[str]]:
    """
    Return all scouts assigned to a match.
    match_label: e.g. 'QM12' (normalized uppercase)
    Returns: {role: [names]}
    """
    schedule = get_schedule(force_refresh)
    label = match_label.upper()
    return schedule.get(label, {})


def get_all_scouts_for_match(match_label: str) -> list[tuple[str, str]]:
    """
    Return flat list of (name, role) for all scouts assigned to a match.
    """
    roles = get_scouts_for_match(match_label)
    result = []
    for role, names in roles.items():
        for name in names:
            result.append((name, role))
    return result


def get_match_scouts_only(match_label: str) -> list[tuple[str, str]]:
    """Return only the match scouts for a match."""
    roles = get_scouts_for_match(match_label)
    result = []
    for name in roles.get("Match Scout", []):
        result.append((name, "Match Scout"))
    return result


def get_shifts_for_scout(scout_name: str) -> list[tuple[str, str]]:
    """Return all matches a scout is assigned to as (match_label, role)."""
    schedule = get_schedule()
    shifts = []
    name_lower = scout_name.lower().strip()
    match_scout_roles = set(MATCH_SCOUT_ROLES)

    def sort_key(label):
        m = re.match(r"([A-Za-z]+)(\d+)", label)
        return (m.group(1), int(m.group(2))) if m else (label, 0)

    for match_label, roles in sorted(schedule.items(), key=lambda x: sort_key(x[0])):
        for role, names in roles.items():
            if role not in match_scout_roles:
                continue
            for name in names:
                n = name.lower().strip()
                if name_lower in n or n in name_lower:
                    shifts.append((match_label, role))
    return shifts

