# Push Bot

A Slack bot for FRC Team 7419 that automates scouting coordination at competitions. It listens for [Nexus](https://frc.nexus) match status webhooks, alerts scouts when their match is queuing or on deck, polls [The Blue Alliance](https://www.thebluealliance.com) for results, and reminds scouts to submit their [Lovat](https://lovat.app) reports.

---

## Features

- **Automated queuing & on-deck alerts** — DMs assigned match scouts the moment Nexus reports a match is queuing or on deck
- **TBA result polling** — polls TBA every 30 seconds and fires a results alert as soon as match data posts
- **Lovat confirmation tracking** — follows up with unconfirmed scouts 7 minutes after results, escalates to the scouting lead after 4 more minutes
- **Auto match summary** — posts a score breakdown to a results channel after every 7419 match
- **Slash commands** for scouts and leads (see below)
- **Statbotics win probability** — shows predicted scores and win percentages for unplayed matches
- **EPA lookup** — shows team EPA stats from a bundled event CSV

---

## Slash Commands

### Match Info
| Command | Description |
|---|---|
| `/next-match` | 7419's next unplayed match, alliance partners, estimated time, and win probability |
| `/standings` | Current rank, W/L/T record, and RP at the event |
| `/match-stats QM12` | Full score breakdown for any match (auto/teleop/endgame) |
| `/lookup 254` | Team rank, record, RP, EPA breakdown, and next match |

### Scouting
| Command | Description |
|---|---|
| `/whos-scouting QM12` | Who is assigned to scout a specific qual match |
| `/scouting-status QM12` | Match scouts with Lovat confirmation status |
| `/my-shift Name` | All match scouting shifts for a given scout |
| `/confirm QM12 Aarav` | Mark a scout as confirmed for Lovat |

### Operations (scouting lead only)
| Command | Description |
|---|---|
| `/push QM12` | Manually trigger queuing alerts for a match |
| `/post-match QM12` | Manually post a match result to `#district-championships` |
| `/refresh-schedule` | Force reload the Google Sheets scouting schedule |
| `/nexus-status` | Show what Nexus currently has queuing / on deck |

> **Tip:** `QM12` and `QM 12` both work — spaces are stripped automatically. Names are case-insensitive and first-name only.

---

## Architecture

```
Nexus Webhook ──→ POST /nexus/event  ─→ background thread
                  POST /nexus/match  ─→ background thread
                                           │
                                    parse label → TBA key
                                    check dedup state
                                           │
                              ┌────────────┼────────────┐
                              ▼            ▼            ▼
                        send_queuing  send_ondeck  (from TBA poll)
                           alerts      alerts      send_results_alerts
                              │                         │
                        start TBA poll             confirmation timer
                              │                    → follow-up DM (7 min)
                    async loop every 30s           → escalate to lead (4 min)
                    GET /match/{key} w/ ETag
                    → detect post_result_time
```

- **FastAPI** serves the Nexus webhook endpoints (`/nexus/event`, `/nexus/match`) and health check (`/health`)
- **Slack Bolt / Socket Mode** handles all slash commands over a persistent WebSocket — no inbound HTTP required for Slack
- **TBA polling** runs as an asyncio task in the FastAPI event loop
- All Nexus payloads return HTTP 200 immediately and process in background threads

### Module overview
| File | Role |
|---|---|
| `main.py` | FastAPI app, webhook endpoints, lifespan startup |
| `bot.py` | Slack slash command handlers |
| `scheduler.py` | Alert senders, TBA polling loop, confirmation timer |
| `tba.py` | TBA API client (async, ETag-aware) |
| `nexus.py` | Nexus payload parsing, label → TBA key conversion |
| `sheets.py` | Google Sheets schedule reader |
| `slack_utils.py` | Slack DM helpers |
| `state.py` | In-memory dedup state (queuing, on-deck, done, ETags) |

---

## Setup

See [SETUP.md](SETUP.md) for the full step-by-step guide covering:
1. Creating the Slack app and slash commands
2. Getting a TBA API key
3. Registering Nexus webhooks
4. Setting up a Google Sheets service account
5. Collecting scout Slack IDs
6. Deploying to Railway

---

## Environment Variables

Copy `.env.example` to `.env` and fill in your values.

| Variable | Description |
|---|---|
| `SLACK_BOT_TOKEN` | Bot User OAuth Token (`xoxb-...`) |
| `SLACK_APP_TOKEN` | App-Level Token for Socket Mode (`xapp-...`) |
| `NEXUS_TOKEN` | Webhook verification token from frc.nexus |
| `NEXUS_API_KEY` | Nexus API key from frc.nexus |
| `NEXUS_EVENT_KEY` | Event key, e.g. `2026cancmp` |
| `TBA_API_KEY` | The Blue Alliance read API key |
| `TBA_EVENT_KEY` | TBA event key, e.g. `2026cancmp` |
| `SCOUTING_LEAD_SLACK_ID` | Slack User ID for the scouting lead (receives escalations) |
| `SPREADSHEET_ID` | Google Sheet ID from the URL |
| `SHEET_NAME` | Sheet tab name, e.g. `Detailed Shifts` |
| `SERVICE_ACCOUNT_JSON` | Full JSON content of the Google service account key |
| `SCOUT_IDS` | `Name=SlackUserID,...` mapping for DMs |
| `CONFIRM_WAIT_MINUTES` | Minutes after results before first Lovat follow-up (default: `7`) |
| `ESCALATE_WAIT_MINUTES` | Additional minutes before escalating to lead (default: `4`) |
| `TBA_POLL_INTERVAL_SECONDS` | How often to poll TBA for match results (default: `30`) |
| `RESULTS_CHANNEL_ID` | Slack channel ID where auto match summaries are posted |
| `PORT` | HTTP server port (default: `8080`) |

---

## Running Locally

```bash
pip install -r requirements.txt
cp .env.example .env  # fill in your values
python main.py
```

Health check:
```bash
curl http://localhost:8080/health
```

---

## Deployment

The repo includes a `Procfile` for Railway:
```
web: uvicorn main:app --host 0.0.0.0 --port $PORT
```

Push to GitHub and connect the repo in Railway. Set all environment variables in the Railway project dashboard. See [SETUP.md](SETUP.md) for details.
