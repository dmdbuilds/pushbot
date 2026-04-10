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
    """Extract scout names from a cell value, ignoring team numbers and keywords."""
    if not cell_value:
        return []
    names = []
    for line in str(cell_value).strip().split("\n"):
        line = line.strip()
        if not line:
            continue
        if re.match(r"^[\d,\s]+$", line):
            continue  # skip team numbers
        if line.upper() in ("BREAK", "FLUID PIT SCOUTING", "X"):
            continue
        names.append(line)
    return names


# Column indices (0-based) and their roles
ROLE_COLUMNS = {
    2: "Rotating Pit",    # C
    4: "Permanent Pit",   # E
    6: "Pit Scout",       # G
    # 8: "Match Scout",   # I — handled specially (multiple scouts)
    10: "Pit Ambassador", # K
}
MATCH_SCOUT_COL = 8  # I — 6 scouts in order: blue1, blue2, blue3, red1, red2, red3
MATCH_SCOUT_ROLES = ["Blue Scout 1", "Blue Scout 2", "Blue Scout 3",
                     "Red Scout 1", "Red Scout 2", "Red Scout 3"]

# Match number column
MATCH_COL = 1   # B — "QM1", "QM2", etc.


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

    # Carry-forward state for block-fill columns
    carry: dict[int, list[str]] = {col: [] for col in ROLE_COLUMNS}
    match_scout_carry: list[list[str]] = [[] for _ in range(6)]

    for row in rows:
        # Ensure row is long enough
        while len(row) <= max(max(ROLE_COLUMNS.keys()), MATCH_SCOUT_COL + 5):
            row.append("")

        match_label_raw = row[MATCH_COL].strip() if len(row) > MATCH_COL else ""
        if not match_label_raw:
            continue

        # Normalize match label: "QM1", "QM2", "SF1", etc.
        match_label = match_label_raw.upper()

        if match_label not in schedule:
            schedule[match_label] = {}

        # Parse single-name role columns with carry-forward
        for col, role in ROLE_COLUMNS.items():
            if col < len(row) and row[col].strip():
                names = parse_names(row[col])
                carry[col] = names
            schedule[match_label].setdefault(role, [])
            for name in carry[col]:
                if name not in schedule[match_label][role]:
                    schedule[match_label][role].append(name)

        # Parse match scouts (up to 6 columns starting at MATCH_SCOUT_COL)
        for i in range(6):
            col_idx = MATCH_SCOUT_COL + i
            if col_idx < len(row) and row[col_idx].strip():
                names = parse_names(row[col_idx])
                match_scout_carry[i] = names
            role = MATCH_SCOUT_ROLES[i]
            schedule[match_label].setdefault(role, [])
            for name in match_scout_carry[i]:
                if name not in schedule[match_label][role]:
                    schedule[match_label][role].append(name)

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
    """Return only the 6 match scouts (Blue/Red Scout 1/2/3) for a match."""
    roles = get_scouts_for_match(match_label)
    result = []
    for role in MATCH_SCOUT_ROLES:
        for name in roles.get(role, []):
            result.append((name, role))
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

