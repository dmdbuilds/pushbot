"""
Push Bot — FastAPI webhook server.

Endpoints:
  POST /nexus/event    — Nexus live event status webhook
  POST /nexus/match    — Nexus team-specific match webhook
  GET  /health         — Health check
"""
import asyncio
import logging
import os
import threading
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, Header, HTTPException, Request, status
from fastapi.responses import JSONResponse

import state
import scheduler
import slack_utils
from nexus import (
    parse_nexus_event_payload,
    parse_nexus_match_payload,
    nexus_label_to_tba_key,
    match_label_to_display,
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


# ──────────────────────────────────────────────
# Lifespan: start background tasks on startup
# ──────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Start TBA polling loop
    asyncio.create_task(scheduler.polling_loop())
    logger.info("TBA polling loop scheduled")

    # Start Slack Socket Mode in background thread
    try:
        from bot import start_socket_mode
        start_socket_mode()
    except Exception as e:
        logger.error("Failed to start Slack Socket Mode: %s", e)

    yield
    logger.info("Push Bot shutting down")


app = FastAPI(title="Push Bot", lifespan=lifespan)


# ──────────────────────────────────────────────
# Nexus token verification
# ──────────────────────────────────────────────

def verify_nexus_token(nexus_token: str = Header(alias="Nexus-Token", default=None)):
    expected = os.environ.get("NEXUS_TOKEN")
    if expected and nexus_token != expected:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid Nexus token")


# ──────────────────────────────────────────────
# Background processing helpers
# ──────────────────────────────────────────────

def _process_event_async(payload: dict) -> None:
    """Process a Nexus live event status payload in a background thread."""
    try:
        data = parse_nexus_event_payload(payload)
        event_key = data["event_key"] or os.environ.get("NEXUS_EVENT_KEY", "")
        data_time = data["data_as_of_time"]

        if not state.update_nexus_time(event_key, data_time):
            logger.debug("Stale/duplicate Nexus event payload for %s — skipping", event_key)
            return

        tba_event_key = os.environ.get("TBA_EVENT_KEY", event_key)
        now_queuing = data.get("now_queuing")
        on_deck = data.get("on_deck")
        matches = data.get("matches", [])

        # ── Handle "Now queuing" ──
        if now_queuing:
            _handle_now_queuing(now_queuing, tba_event_key, matches)

        # ── Handle "On deck" ──
        if on_deck:
            _handle_on_deck(on_deck, tba_event_key, matches)

    except Exception as e:
        logger.error("Error processing Nexus event payload: %s", e, exc_info=True)


def _handle_now_queuing(label: str, tba_event_key: str, matches: list) -> None:
    tba_key = nexus_label_to_tba_key(label, tba_event_key)
    if not tba_key:
        logger.error("Could not convert Nexus label to TBA key: %s", label)
        return

    if not state.mark_queuing(tba_key):
        logger.debug("Queuing already triggered for %s", tba_key)
        return

    logger.info("NOW QUEUING: %s → %s", label, tba_key)
    scheduler.send_queuing_alerts(label, tba_key)
    state.start_polling(tba_key)


def _handle_on_deck(label: str, tba_event_key: str, matches: list) -> None:
    tba_key = nexus_label_to_tba_key(label, tba_event_key)
    if not tba_key:
        return

    if not state.mark_ondeck(tba_key):
        logger.debug("On-deck already triggered for %s", tba_key)
        return

    logger.info("ON DECK: %s → %s", label, tba_key)
    scheduler.send_ondeck_alerts(label, tba_key)


def _process_match_async(payload: dict) -> None:
    """Process a Nexus team-specific match payload in a background thread."""
    try:
        data = parse_nexus_match_payload(payload)
        event_key = data["event_key"] or os.environ.get("NEXUS_EVENT_KEY", "")
        data_time = data["data_as_of_time"]
        match = data.get("match", {})

        label = match.get("label", "")
        match_status = match.get("status", "")

        if not label:
            return

        if not state.update_nexus_match_time(label, data_time):
            logger.debug("Stale/duplicate Nexus match payload for %s — skipping", label)
            return

        tba_event_key = os.environ.get("TBA_EVENT_KEY", event_key)
        tba_key = nexus_label_to_tba_key(label, tba_event_key)
        if not tba_key:
            return

        logger.info("Nexus match webhook: %s → %s (%s)", label, tba_key, match_status)

        if match_status == "Now queuing":
            if state.mark_queuing(tba_key):
                scheduler.send_queuing_alerts(label, tba_key)
                state.start_polling(tba_key)

        elif match_status == "On deck":
            if state.mark_ondeck(tba_key):
                scheduler.send_ondeck_alerts(label, tba_key)

    except Exception as e:
        logger.error("Error processing Nexus match payload: %s", e, exc_info=True)


# ──────────────────────────────────────────────
# Webhook endpoints
# ──────────────────────────────────────────────

@app.post("/nexus/event")
async def nexus_event_webhook(
    request: Request,
    nexus_token: str = Header(alias="Nexus-Token", default=None),
):
    """
    Nexus live event status webhook.
    Returns 200 immediately, processes payload in background.
    """
    verify_nexus_token(nexus_token)

    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    # Return 200 immediately — process async
    thread = threading.Thread(target=_process_event_async, args=(payload,), daemon=True)
    thread.start()

    return JSONResponse(content={"ok": True})


@app.post("/nexus/match")
async def nexus_match_webhook(
    request: Request,
    nexus_token: str = Header(alias="Nexus-Token", default=None),
):
    """
    Nexus team-specific match webhook.
    Returns 200 immediately, processes payload in background.
    """
    verify_nexus_token(nexus_token)

    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    thread = threading.Thread(target=_process_match_async, args=(payload,), daemon=True)
    thread.start()

    return JSONResponse(content={"ok": True})


@app.get("/health")
async def health():
    """Health check endpoint."""
    with state.lock:
        polling_count = len(state.polling_matches)
        queuing_count = len(state.triggered_queuing)
        done_count = len(state.triggered_done)

    return {
        "status": "ok",
        "polling_matches": polling_count,
        "queuing_alerts_sent": queuing_count,
        "results_alerts_sent": done_count,
    }


# ──────────────────────────────────────────────
# Entry point (for local dev)
# ──────────────────────────────────────────────

if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8080"))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
