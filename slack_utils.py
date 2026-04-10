"""Slack DM helper and name → Slack ID lookup."""
import logging
import os
from typing import Optional

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

logger = logging.getLogger(__name__)

_client: Optional[WebClient] = None


def get_client() -> WebClient:
    global _client
    if _client is None:
        _client = WebClient(token=os.environ["SLACK_BOT_TOKEN"])
    return _client


def _build_name_id_map() -> dict[str, str]:
    """Parse SCOUT_IDS env var into {name: slack_user_id}."""
    raw = os.environ.get("SCOUT_IDS", "")
    mapping: dict[str, str] = {}
    for pair in raw.split(","):
        pair = pair.strip()
        if "=" not in pair:
            continue
        name, uid = pair.split("=", 1)
        mapping[name.strip()] = uid.strip()
    return mapping


_name_id_map: Optional[dict[str, str]] = None


def get_name_id_map() -> dict[str, str]:
    global _name_id_map
    if _name_id_map is None:
        _name_id_map = _build_name_id_map()
    return _name_id_map


def get_slack_id(name: str) -> Optional[str]:
    """Look up a scout's Slack user ID by name."""
    return get_name_id_map().get(name)


def send_dm(user_id: str, text: str, blocks: Optional[list] = None) -> bool:
    """Send a DM to a Slack user. Returns True on success."""
    client = get_client()
    try:
        # Open DM channel
        resp = client.conversations_open(users=[user_id])
        channel = resp["channel"]["id"]
        kwargs = {"channel": channel, "text": text}
        if blocks:
            kwargs["blocks"] = blocks
        client.chat_postMessage(**kwargs)
        return True
    except SlackApiError as e:
        logger.error("Failed to DM %s: %s", user_id, e.response["error"])
        return False


def dm_scout(name: str, text: str) -> bool:
    """
    DM a scout by name. If no Slack ID found, log and DM the scouting lead.
    Returns True if DM was sent to the scout.
    """
    uid = get_slack_id(name)
    if uid:
        return send_dm(uid, text)
    else:
        logger.warning("No Slack ID found for scout: %s", name)
        _alert_lead_missing_id(name)
        return False


def dm_lead(text: str) -> bool:
    """DM the scouting lead."""
    lead_id = os.environ.get("SCOUTING_LEAD_SLACK_ID")
    if not lead_id:
        logger.error("SCOUTING_LEAD_SLACK_ID not set")
        return False
    return send_dm(lead_id, text)


def _alert_lead_missing_id(name: str) -> None:
    """Notify the scouting lead that a scout's Slack ID is missing."""
    lead_id = os.environ.get("SCOUTING_LEAD_SLACK_ID")
    if not lead_id:
        return
    msg = (
        f":warning: *Push Bot* — No Slack ID found for scout *{name}*.\n"
        f"Please add `{name}=U...` to the `SCOUT_IDS` env var, or manually ping them."
    )
    send_dm(lead_id, msg)


def post_ephemeral(channel: str, user_id: str, text: str) -> bool:
    """Post an ephemeral message visible only to the user."""
    client = get_client()
    try:
        client.chat_postEphemeral(channel=channel, user=user_id, text=text)
        return True
    except SlackApiError as e:
        logger.error("Failed to post ephemeral to %s: %s", user_id, e.response["error"])
        return False
