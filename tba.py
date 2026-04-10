"""TBA API client with ETag caching."""
import logging
import os
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

TBA_BASE = "https://www.thebluealliance.com/api/v3"


def _headers(etag: Optional[str] = None) -> dict[str, str]:
    h = {"X-TBA-Auth-Key": os.environ["TBA_API_KEY"]}
    if etag:
        h["If-None-Match"] = etag
    return h


async def get_match(match_key: str, etag: Optional[str] = None) -> tuple[Optional[dict], Optional[str], int]:
    """
    Fetch a single match from TBA.

    Returns (match_data, new_etag, status_code).
    If 304 (not modified), returns (None, etag, 304).
    If error, returns (None, None, status_code).
    """
    url = f"{TBA_BASE}/match/{match_key}"
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.get(url, headers=_headers(etag))
            new_etag = resp.headers.get("ETag", etag)
            if resp.status_code == 304:
                return None, etag, 304
            if resp.status_code == 200:
                return resp.json(), new_etag, 200
            logger.warning("TBA GET %s → %d", url, resp.status_code)
            return None, etag, resp.status_code
        except httpx.RequestError as e:
            logger.error("TBA request error for %s: %s", match_key, e)
            return None, etag, 0


async def get_event_matches_simple(event_key: str, etag: Optional[str] = None) -> tuple[Optional[list], Optional[str], int]:
    """
    Fetch all matches for an event (simple form) from TBA.

    Returns (matches_list, new_etag, status_code).
    """
    url = f"{TBA_BASE}/event/{event_key}/matches/simple"
    async with httpx.AsyncClient(timeout=10.0) as client:
        try:
            resp = await client.get(url, headers=_headers(etag))
            new_etag = resp.headers.get("ETag", etag)
            if resp.status_code == 304:
                return None, etag, 304
            if resp.status_code == 200:
                return resp.json(), new_etag, 200
            logger.warning("TBA GET %s → %d", url, resp.status_code)
            return None, etag, resp.status_code
        except httpx.RequestError as e:
            logger.error("TBA request error for event %s: %s", event_key, e)
            return None, etag, 0


def match_is_done(match_data: dict) -> bool:
    """Return True if the match has posted results (most reliable signal)."""
    return match_data.get("post_result_time") is not None


def match_has_started(match_data: dict) -> bool:
    """Return True if the match has been played (actual_time populated)."""
    return match_data.get("actual_time") is not None


def get_match_sync(match_key: str) -> dict | None:
    """Synchronous wrapper around get_match for use in Slack commands."""
    import asyncio
    try:
        loop = asyncio.get_event_loop()
        if loop.is_running():
            import concurrent.futures
            with concurrent.futures.ThreadPoolExecutor() as pool:
                future = pool.submit(asyncio.run, get_match(match_key))
                data, _, status = future.result(timeout=10)
        else:
            data, _, status = loop.run_until_complete(get_match(match_key))
        return data if status == 200 else None
    except Exception as e:
        return None


def get_match_sync(match_key: str) -> dict | None:
    """Synchronous TBA match fetch for use in Slack slash commands."""
    import httpx as _httpx
    import os as _os
    url = f"https://www.thebluealliance.com/api/v3/match/{match_key}"
    headers = {"X-TBA-Auth-Key": _os.environ["TBA_API_KEY"]}
    try:
        r = _httpx.get(url, headers=headers, timeout=8.0)
        if r.status_code == 200:
            return r.json()
        return None
    except Exception:
        return None
