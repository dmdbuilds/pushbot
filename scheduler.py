"""
TBA polling loop, alert sending, and confirmation tracking.

This module is responsible for:
- Starting/stopping TBA polling per active match
- Sending Slack DMs at each stage (queuing, on-deck, results posted)
- Running the confirmation timer and escalation to scouting lead
"""
import asyncio
import logging
import os
import time
from typing import Optional

import state
import tba
import sheets
import slack_utils
from nexus import match_label_to_display

logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
# Alert senders
# ──────────────────────────────────────────────

def _match_display(tba_key: str) -> str:
    """Extract a short display name from a TBA key, e.g. '2026cancmp_qm12' → 'QM12'."""
    suffix = tba_key.split("_", 1)[-1]  # 'qm12', 'sf1m2', etc.
    suffix = suffix.upper()
    return suffix


def send_queuing_alerts(match_label: str, tba_key: str) -> None:
    """DM all scouts for this match that it is now queuing."""
    display = match_label_to_display(match_label)
    if not display.upper().startswith("QM"):
        logger.info("Skipping non-qual match: %s", match_label)
        return
    scouts = sheets.get_match_scouts_only(display)

    if not scouts:
        logger.warning("No match scouts found for %s — queuing alert skipped", match_label)
        slack_utils.dm_lead(
            f":warning: *Push Bot* — No match scouts found in schedule for *{display}* (queuing). "
            f"Check the Google Sheet."
        )
        return

    for name, role in scouts:
        text = (
            f":bell: *Push Bot* — {display} is NOW QUEUING\n"
            f"Head to the field! :runner:\n"
            f"Submit Lovat when results post."
        )
        slack_utils.dm_scout(name, text)

    logger.info("Queuing alerts sent for %s to %d match scouts", display, len(scouts))


def send_ondeck_alerts(match_label: str, tba_key: str) -> None:
    """On-deck alerts disabled — match scouts only get queuing + results DMs."""
    logger.info("On-deck alert suppressed for %s", match_label)


def send_results_alerts(match_label: str, tba_key: str) -> None:
    """DM all scouts that results are posted and to submit Lovat."""
    display = match_label_to_display(match_label)
    scouts = sheets.get_match_scouts_only(display)

    if not scouts:
        logger.warning("No match scouts found for %s — results alert skipped", match_label)
        return

    for name, role in scouts:
        text = (
            f":clipboard: *Push Bot* — {display} results are posted!\n"
            f"Submit your Lovat report NOW :fire:\n"
            f"Go to Lovat → find {display} → submit your scouting data."
        )
        slack_utils.dm_scout(name, text)

    logger.info("Results alerts sent for %s to %d match scouts", display, len(scouts))


def _run_confirmation_followup(match_label: str, tba_key: str) -> None:
    """
    Run in a background thread. Wait CONFIRM_WAIT_MINUTES, then DM unconfirmed scouts.
    Wait ESCALATE_WAIT_MINUTES more, then escalate to scouting lead.
    """
    display = match_label_to_display(match_label)
    confirm_wait = int(os.environ.get("CONFIRM_WAIT_MINUTES", "7")) * 60
    escalate_wait = int(os.environ.get("ESCALATE_WAIT_MINUTES", "4")) * 60

    time.sleep(confirm_wait)

    display = match_label_to_display(match_label)
    scouts = sheets.get_match_scouts_only(display)
    unconfirmed = [name for name, role in scouts if not state.is_confirmed(display, name)]

    if not unconfirmed:
        logger.info("All scouts confirmed for %s", display)
        return

    # Follow-up DM to unconfirmed scouts
    for name in unconfirmed:
        text = (
            f":pushpin: *Push Bot* reminder — Have you submitted Lovat for {display}?\n"
            f"If you already submitted, ask your scouting lead to run `/confirm {display} {name}`."
        )
        slack_utils.dm_scout(name, text)

    logger.info(
        "Follow-up sent for %s. Unconfirmed: %s. Escalating in %d min.",
        display, unconfirmed, escalate_wait // 60
    )

    time.sleep(escalate_wait)

    # Re-check after escalation wait
    still_unconfirmed = [n for n in unconfirmed if not state.is_confirmed(match_label, n)]

    if not still_unconfirmed:
        logger.info("All scouts confirmed for %s after follow-up", display)
        return

    names_str = ", ".join(still_unconfirmed)
    escalation_msg = (
        f":rotating_light: *Push Bot escalation* — {display} Lovat submissions missing!\n"
        f"Scouts not confirmed: *{names_str}*\n"
        f"Please follow up manually."
    )
    slack_utils.dm_lead(escalation_msg)
    logger.warning("Escalated %s to scouting lead. Missing: %s", display, names_str)


# ──────────────────────────────────────────────
# TBA polling
# ──────────────────────────────────────────────

async def poll_match(match_key: str, match_label: str) -> None:
    """
    Poll TBA for a single match. Called periodically by the scheduler loop.
    If post_result_time is populated, fire results alerts and stop polling.
    """
    etag = state.get_etag(match_key)
    match_data, new_etag, status = await tba.get_match(match_key, etag)

    if status == 304:
        # Not modified — nothing to do
        return

    if status != 200 or match_data is None:
        logger.warning("TBA poll for %s returned %d", match_key, status)
        return

    # Update ETag
    if new_etag:
        state.set_etag(match_key, new_etag)

    if tba.match_is_done(match_data):
        if state.mark_done(match_key):
            logger.info("Match %s results detected — firing alerts", match_key)
            send_results_alerts(match_label, match_key)
            state.stop_polling(match_key)

            # Start confirmation timer in a thread
            import threading
            t = threading.Thread(
                target=_run_confirmation_followup,
                args=(match_label, match_key),
                daemon=True,
            )
            t.start()


async def polling_loop() -> None:
    """
    Background async loop: every TBA_POLL_INTERVAL_SECONDS, poll all active matches.
    This runs forever — start it as an asyncio task.
    """
    interval = int(os.environ.get("TBA_POLL_INTERVAL_SECONDS", "30"))
    logger.info("TBA polling loop started (interval=%ds)", interval)

    while True:
        await asyncio.sleep(interval)
        keys = state.get_polling_keys()
        if not keys:
            continue
        for match_key in keys:
            # Derive match_label from match_key: '2026cancmp_qm12' → 'QM12'
            suffix = match_key.split("_", 1)[-1]  # 'qm12'
            # Reconstruct a label for display — use the suffix uppercased
            match_label = suffix.upper()
            try:
                await poll_match(match_key, match_label)
            except Exception as e:
                logger.error("Error polling %s: %s", match_key, e)


# ──────────────────────────────────────────────
# Manual trigger helper
# ──────────────────────────────────────────────

def trigger_match_queuing(match_label: str, event_key: str) -> str:
    """
    Manually trigger queuing alerts for a match.
    Returns a status string for the slash command response.
    """
    from nexus import nexus_label_to_tba_key

    # match_label might be 'QM12' — convert to Nexus-style 'Qualification 12'
    label_upper = match_label.upper()
    # Accept 'QM12' or 'Qualification 12' style
    if label_upper.startswith("QM"):
        num = label_upper[2:]
        nexus_label = f"Qualification {num}"
    elif label_upper.startswith("PM"):
        num = label_upper[2:]
        nexus_label = f"Practice {num}"
    elif label_upper.startswith("SF"):
        num = label_upper[2:]
        nexus_label = f"Playoff {num}"
    elif label_upper.startswith("F"):
        num = label_upper[1:]
        nexus_label = f"Final {num}"
    else:
        nexus_label = match_label

    tba_key = nexus_label_to_tba_key(nexus_label, event_key)
    if not tba_key:
        return f":x: Could not convert `{match_label}` to a TBA key."

    display = match_label_to_display(nexus_label)

    if not state.mark_queuing(tba_key):
        return f":information_source: Queuing alerts for {display} were already sent."

    send_queuing_alerts(nexus_label, tba_key)
    state.start_polling(tba_key)
    return f":white_check_mark: Queuing alerts sent for {display}. TBA polling started."
