"""
Slack Socket Mode handler — all slash commands.

Slash commands:
  /scouting-status [MATCH]   — show scouts and confirmation status
  /my-shift [NAME]           — list all shifts for a scout
  /push MATCH                — manually trigger queuing alerts
  /confirm MATCH NAME        — manually mark a scout as confirmed
  /refresh-schedule          — force reload Google Sheets schedule
  /nexus-status              — show current Nexus event state
"""
import logging
import os

from slack_bolt import App
from slack_bolt.adapter.socket_mode import SocketModeHandler

import state
import sheets
import slack_utils
import scheduler
from nexus import match_label_to_display

logger = logging.getLogger(__name__)

app = App(token=os.environ["SLACK_BOT_TOKEN"])


# ──────────────────────────────────────────────
# /scouting-status [MATCH]
# ──────────────────────────────────────────────

@app.command("/scouting-status")
def cmd_scouting_status(ack, body, respond):
    ack()
    text = body.get("text", "").strip().upper()

    if text:
        # Status for a specific match
        match_label = text
        roles = sheets.get_scouts_for_match(match_label)
        if not roles:
            respond(f":x: No scouts found for *{match_label}* in the schedule.", response_type="ephemeral")
            return

        lines = [f":mag: *Scouting status for {match_label}*"]
        for role, names in roles.items():
            for name in names:
                confirmed = state.is_confirmed(match_label, name)
                icon = ":white_check_mark:" if confirmed else ":hourglass:"
                lines.append(f"  {icon} {role}: {name}")

        respond("\n".join(lines), response_type="ephemeral")

    else:
        # Overview: show all matches currently being polled or recently triggered
        with state.lock:
            queuing = set(state.triggered_queuing)
            ondeck = set(state.triggered_ondeck)
            done = set(state.triggered_done)
            polling = set(state.polling_matches.keys())

        if not queuing and not polling:
            respond(":zzz: No active matches right now.", response_type="ephemeral")
            return

        lines = [":clipboard: *Active Match Overview*"]
        all_keys = queuing | polling
        for key in sorted(all_keys):
            suffix = key.split("_", 1)[-1].upper()
            status_parts = []
            if key in queuing:
                status_parts.append("queuing alert sent")
            if key in ondeck:
                status_parts.append("on-deck alert sent")
            if key in done:
                status_parts.append("results sent")
            elif key in polling:
                status_parts.append("polling TBA")
            lines.append(f"  • *{suffix}*: {', '.join(status_parts) or 'tracked'}")

        respond("\n".join(lines), response_type="ephemeral")


# ──────────────────────────────────────────────
# /my-shift NAME
# ──────────────────────────────────────────────

@app.command("/my-shift")
def cmd_my_shift(ack, body, respond):
    ack()
    name = body.get("text", "").strip()

    if not name:
        # Default to the caller if possible — but we only have Slack user ID
        respond(":x: Usage: `/my-shift Name` — e.g. `/my-shift Kaveesh`", response_type="ephemeral")
        return

    shifts = sheets.get_shifts_for_scout(name)
    if not shifts:
        respond(f":x: No shifts found for *{name}* in the schedule.", response_type="ephemeral")
        return

    lines = [f":calendar: *Shifts for {name}*"]
    for match_label, role in shifts:
        confirmed = state.is_confirmed(match_label, name)
        icon = ":white_check_mark:" if confirmed else ":radio_button:"
        lines.append(f"  {icon} *{match_label}* — {role}")

    respond("\n".join(lines), response_type="ephemeral")


# ──────────────────────────────────────────────
# /match-stats MATCH
# ──────────────────────────────────────────────
@app.command("/match-stats")
def cmd_match_stats(ack, body, respond):
    ack()
    import threading
    def work():
        try:
            label = body.get("text", "").strip().upper()
            user_id = body.get("user_id", "")
            channel_id = body.get("channel_id", "")
            if not label:
                app.client.chat_postEphemeral(channel=channel_id, user=user_id, text=":x: Usage: `/match-stats QM12`")
                return
            import httpx
            TBA_EVENT = os.environ.get("TBA_EVENT_KEY", "2026cancmp")
            tba_key = f"{TBA_EVENT}_{label.lower()}"
            headers = {"X-TBA-Auth-Key": os.environ["TBA_API_KEY"]}
            r = httpx.get(f"https://www.thebluealliance.com/api/v3/match/{tba_key}", headers=headers, timeout=8.0)
            match_info = r.json() if r.status_code == 200 else None
            if not match_info or not match_info.get("alliances"):
                app.client.chat_postEphemeral(channel=channel_id, user=user_id, text=f":x: No data found for *{label}*. Match may not exist or TBA hasn't posted it yet.")
                return
            red_teams = match_info["alliances"]["red"]["team_keys"]
            blue_teams = match_info["alliances"]["blue"]["team_keys"]
            red_str = " | ".join(t.replace("frc", "") for t in red_teams)
            blue_str = " | ".join(t.replace("frc", "") for t in blue_teams)
            red_score = match_info["alliances"]["red"].get("score", -1)
            blue_score = match_info["alliances"]["blue"].get("score", -1)
            if red_score >= 0 and blue_score >= 0:
                bd = match_info.get("score_breakdown") or {}
                r2 = bd.get("red", {})
                b2 = bd.get("blue", {})
                winner_red = " :trophy:" if red_score > blue_score else ""
                winner_blue = " :trophy:" if blue_score > red_score else ""
                lines = [
                    f":checkered_flag: *{label} — Final*",
                    f":red_circle: Red:  {red_str} — *{red_score} pts*{winner_red}",
                    f":large_blue_circle: Blue: {blue_str} — *{blue_score} pts*{winner_blue}",
                ]
                if r2 and b2:
                    r_auto = r2.get("totalAutoPoints", r2.get("autoPoints", 0))
                    b_auto = b2.get("totalAutoPoints", b2.get("autoPoints", 0))
                    r_teleop = r2.get("totalTeleopPoints", r2.get("teleopPoints", 0))
                    b_teleop = b2.get("totalTeleopPoints", b2.get("teleopPoints", 0))
                    r_end = (r2.get("hubScore") or {}).get("endgamePoints", r2.get("endGameTowerPoints", 0))
                    b_end = (b2.get("hubScore") or {}).get("endgamePoints", b2.get("endGameTowerPoints", 0))
                    lines.append(f"Auto: Red {r_auto} | Blue {b_auto}")
                    lines.append(f"Teleop: Red {r_teleop} | Blue {b_teleop}")
                    lines.append(f"Endgame: Red {r_end} | Blue {b_end}")
            else:
                lines = [
                    f":clock1: *{label} — Not played yet*",
                    f":red_circle: Red:  {red_str}",
                    f":large_blue_circle: Blue: {blue_str}",
                ]
            scout_assignments = sheets.get_match_scouts_only(label)
            if scout_assignments:
                scout_str = " | ".join(f"{name} ({role})" for name, role in scout_assignments)
                lines.append(f":clipboard: Scouting: {scout_str}")
            app.client.chat_postEphemeral(channel=channel_id, user=user_id, text="\n".join(lines))
        except Exception as e:
            logger.error("match-stats error: %s", e)
            try:
                app.client.chat_postEphemeral(channel=body.get("channel_id",""), user=body.get("user_id",""), text=f":x: Error: {e}")
            except:
                pass
    threading.Thread(target=work).start()


@app.command("/post-match")
def cmd_post_match(ack, body, respond):
    ack()
    import threading
    def work():
        try:
            user_id = body.get("user_id", "")
            channel_id = body.get("channel_id", "")
            lead_id = os.environ.get("SCOUTING_LEAD_SLACK_ID", "")
            if user_id != lead_id:
                app.client.chat_postEphemeral(channel=channel_id, user=user_id, text=":x: Only the scouting lead can use this command.")
                return
            label = body.get("text", "").strip().upper()
            if not label:
                app.client.chat_postEphemeral(channel=channel_id, user=user_id, text=":x: Usage: `/post-match QM12`")
                return
            import httpx
            TBA_EVENT = os.environ.get("TBA_EVENT_KEY", "2026cancmp")
            tba_key = f"{TBA_EVENT}_{label.lower()}"
            headers = {"X-TBA-Auth-Key": os.environ["TBA_API_KEY"]}
            r = httpx.get(f"https://www.thebluealliance.com/api/v3/match/{tba_key}", headers=headers, timeout=8.0)
            match_info = r.json() if r.status_code == 200 else None
            if not match_info or not match_info.get("alliances"):
                app.client.chat_postEphemeral(channel=channel_id, user=user_id, text=f":x: No results yet for *{label}*.")
                return
            red_teams = match_info["alliances"]["red"]["team_keys"]
            blue_teams = match_info["alliances"]["blue"]["team_keys"]
            red_score = match_info["alliances"]["red"].get("score", -1)
            blue_score = match_info["alliances"]["blue"].get("score", -1)
            if red_score < 0:
                app.client.chat_postEphemeral(channel=channel_id, user=user_id, text=f":x: *{label}* hasn't been played yet.")
                return
            red_str = " | ".join(t.replace("frc", "") for t in red_teams)
            blue_str = " | ".join(t.replace("frc", "") for t in blue_teams)
            we_red = "frc7419" in red_teams
            our_score = red_score if we_red else blue_score
            opp_score = blue_score if we_red else red_score
            result = "WIN :white_check_mark:" if our_score > opp_score else ("TIE :arrow_right:" if our_score == opp_score else "LOSS :x:")
            bd = match_info.get("score_breakdown") or {}
            ours = bd.get("red" if we_red else "blue", {})
            if not ours:
                app.client.chat_postEphemeral(channel=channel_id, user=user_id, text=f":x: Score breakdown not ready yet for *{label}* — try again in 30 seconds.")
                return
            # 2026 RECON field names
            auto_pts = ours.get("totalAutoPoints", ours.get("autoPoints", 0))
            teleop_pts = ours.get("totalTeleopPoints", ours.get("teleopPoints", 0))
            hub = ours.get("hubScore") or {}
            endgame_pts = hub.get("endgamePoints", ours.get("endGameTowerPoints", ours.get("endgamePoints", 0)))
            lines = [
                f":robot_face: *{label} Result — Team 7419*",
                f":red_circle: Red:  {red_str} — {red_score} pts",
                f":large_blue_circle: Blue: {blue_str} — {blue_score} pts",
                f"",
                f"*7419: {result}* ({our_score} – {opp_score})",
                f"Auto: {auto_pts} | Teleop: {teleop_pts} | Endgame: {endgame_pts}",
            ]
            app.client.chat_postMessage(channel="district-championships", text="\n".join(lines))
            app.client.chat_postEphemeral(channel=channel_id, user=user_id, text=":white_check_mark: Posted to #district-championships.")
        except Exception as e:
            logger.error("post-match error: %s", e)
            try:
                app.client.chat_postEphemeral(channel=body.get("channel_id",""), user=body.get("user_id",""), text=f":x: Error: {e}")
            except:
                pass
    threading.Thread(target=work).start()


@app.command("/push")
def cmd_push(ack, body, respond):
    ack()
    match_label = body.get("text", "").strip()

    if not match_label:
        respond(":x: Usage: `/push QM12`", response_type="ephemeral")
        return

    event_key = os.environ.get("TBA_EVENT_KEY", "2026cancmp")
    result = scheduler.trigger_match_queuing(match_label, event_key)
    respond(result, response_type="ephemeral")


# ──────────────────────────────────────────────
# /confirm MATCH NAME
# ──────────────────────────────────────────────

@app.command("/confirm")
def cmd_confirm(ack, body, respond):
    ack()
    text = body.get("text", "").strip()
    parts = text.split(None, 1)

    if len(parts) < 2:
        respond(":x: Usage: `/confirm QM12 Kaveesh`", response_type="ephemeral")
        return

    match_label, scout_name = parts[0].upper(), parts[1].strip()
    state.confirm_scout(match_label, scout_name)
    respond(
        f":white_check_mark: Marked *{scout_name}* as confirmed for *{match_label}*.",
        response_type="ephemeral"
    )
    logger.info("Manual confirm: %s for %s", scout_name, match_label)


# ──────────────────────────────────────────────
# /refresh-schedule
# ──────────────────────────────────────────────

@app.command("/refresh-schedule")
def cmd_refresh_schedule(ack, body, respond):
    ack()
    try:
        schedule = sheets.get_schedule(force_refresh=True)
        respond(
            f":arrows_counterclockwise: Schedule refreshed. *{len(schedule)}* match entries loaded.",
            response_type="ephemeral"
        )
    except Exception as e:
        logger.error("Schedule refresh failed: %s", e)
        respond(f":x: Failed to refresh schedule: {e}", response_type="ephemeral")


# ──────────────────────────────────────────────
# /nexus-status
# ──────────────────────────────────────────────

@app.command("/nexus-status")
def cmd_nexus_status(ack, body, respond):
    ack()
    import httpx

    event_key = os.environ.get("NEXUS_EVENT_KEY", "2026cancmp")
    nexus_api_key = os.environ.get("NEXUS_API_KEY", "")
    url = f"https://frc.nexus/api/v1/event/{event_key}"

    try:
        resp = httpx.get(url, headers={"Nexus-Api-Key": nexus_api_key}, timeout=10.0)
        if resp.status_code != 200:
            respond(f":x: Nexus API returned {resp.status_code}", response_type="ephemeral")
            return

        data = resp.json()
        now_queuing = data.get("nowQueuing", "None")
        on_deck = data.get("onDeck", "None")
        matches = data.get("matches", [])
        active = [m for m in matches if m.get("status") in ("Now queuing", "On deck", "On field")]

        lines = [
            f":satellite: *Nexus Status — {event_key}*",
            f"  Now Queuing: *{now_queuing}*",
            f"  On Deck: *{on_deck}*",
        ]
        if active:
            lines.append("  Active matches:")
            for m in active:
                lines.append(f"    • {m['label']}: {m['status']}")

        respond("\n".join(lines), response_type="ephemeral")

    except Exception as e:
        logger.error("Nexus status pull failed: %s", e)
        respond(f":x: Could not reach Nexus API: {e}", response_type="ephemeral")


# ──────────────────────────────────────────────
# Start Socket Mode
# ──────────────────────────────────────────────

def start_socket_mode():
    """Start the Slack Socket Mode handler in a daemon thread."""
    import threading

    handler = SocketModeHandler(app, os.environ["SLACK_APP_TOKEN"])

    def _run():
        logger.info("Starting Slack Socket Mode handler")
        handler.start()

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    logger.info("Slack Socket Mode handler started in background thread")
