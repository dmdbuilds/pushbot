# Push Bot Setup Guide
**Team 7419 Tech Support | DCMP 2026**

---

## Prerequisites
- Python 3.11 installed locally (for testing)
- A GitHub account (for Railway deployment)
- Access to Team 7419's Slack workspace (admin or app-install permissions)
- Access to the scouting Google Sheet

---

## Step 1 — Create the Slack App

1. Go to **https://api.slack.com/apps** and click **Create New App**
2. Choose **From scratch**, name it `Push Bot`, select your workspace

### 1a. Enable Socket Mode
1. In the left sidebar, click **Socket Mode**
2. Toggle **Enable Socket Mode** → On
3. When prompted for an App-Level Token, click **Generate** with scope `connections:write`
4. Name it `pushbot-socket`, copy the token → this is your **`SLACK_APP_TOKEN`** (`xapp-...`)

### 1b. Set OAuth Scopes (Bot Token)
1. Left sidebar → **OAuth & Permissions**
2. Under **Bot Token Scopes**, add:
   - `chat:write` — send messages
   - `im:write` — open DM channels
   - `im:history` — read DM history (for future features)
   - `commands` — register slash commands
   - `users:read` — look up user info
3. Click **Install to Workspace** → **Allow**
4. Copy the **Bot User OAuth Token** → this is your **`SLACK_BOT_TOKEN`** (`xoxb-...`)

### 1c. Create Slash Commands
1. Left sidebar → **Slash Commands** → **Create New Command** (repeat for each):

| Command | Request URL | Description |
|---|---|---|
| `/scouting-status` | `https://your-app.railway.app/slack/events` | Show scout status for a match |
| `/my-shift` | `https://your-app.railway.app/slack/events` | List your scouting shifts |
| `/push` | `https://your-app.railway.app/slack/events` | Manually trigger queuing alerts |
| `/confirm` | `https://your-app.railway.app/slack/events` | Mark a scout as confirmed |
| `/refresh-schedule` | `https://your-app.railway.app/slack/events` | Reload the Google Sheet schedule |
| `/nexus-status` | `https://your-app.railway.app/slack/events` | Show current Nexus event state |

> **Note:** With Socket Mode enabled, the Request URL is not actually called — Slack uses the WebSocket connection instead. You can put any HTTPS URL as a placeholder.

### 1d. Enable Event Subscriptions (optional, for future features)
1. Left sidebar → **Event Subscriptions** → Toggle **Enable Events** → On
2. Under **Subscribe to bot events**, add `message.im`
3. Save changes

---

## Step 2 — Get a TBA API Key

1. Go to **https://www.thebluealliance.com/account** (log in or create a free account)
2. Scroll to **Read API Keys** → click **Add New Key**
3. Name it `Push Bot`, copy the key → this is your **`TBA_API_KEY`**

> Keys are free and instant. No approval needed.

---

## Step 3 — Register Nexus Webhooks

> Do this after deploying to Railway so you have a real URL.

1. Go to **https://frc.nexus/api** and log in with your FRC account
2. You'll see your **Nexus API Key** (→ `NEXUS_API_KEY`) and **Nexus Token** (→ `NEXUS_TOKEN`)
3. Register two webhooks:

**Webhook 1 — Live Event Status:**
- URL: `https://your-app.railway.app/nexus/event`
- This fires on any match status change (queuing, on deck, etc.)

**Webhook 2 — Team-Specific Match Status:**
- URL: `https://your-app.railway.app/nexus/match`
- Team number: `7419`
- This fires when a match involving Team 7419 changes status

4. Copy both the **Nexus Token** (for header verification) and **Nexus API Key** (for pull requests)

---

## Step 4 — Set Up Google Sheets Service Account

### 4a. Create a Google Cloud Project
1. Go to **https://console.cloud.google.com**
2. Click the project dropdown → **New Project** → name it `push-bot` → **Create**

### 4b. Enable the Sheets API
1. In the left sidebar → **APIs & Services** → **Library**
2. Search for **Google Sheets API** → click it → **Enable**

### 4c. Create a Service Account
1. Left sidebar → **APIs & Services** → **Credentials**
2. Click **Create Credentials** → **Service Account**
3. Name: `pushbot-sheets`, ID: `pushbot-sheets` → **Create and Continue**
4. Role: **Viewer** (or skip role assignment) → **Done**
5. Click the service account you just created → **Keys** tab
6. **Add Key** → **Create New Key** → **JSON** → **Create**
7. A `.json` file downloads — this is your **`SERVICE_ACCOUNT_JSON`**

### 4d. Share the Google Sheet
1. Open the service account key file, find the `"client_email"` field
   (looks like `pushbot-sheets@push-bot.iam.gserviceaccount.com`)
2. Open the scouting Google Sheet
3. Click **Share** → paste the service account email → set to **Viewer** → **Share**

### 4e. Prepare the JSON for the env var
The entire JSON file contents become the `SERVICE_ACCOUNT_JSON` env var.
On Railway, paste the whole JSON as-is (Railway handles multiline env vars fine).

---

## Step 5 — Collect Scout Slack IDs

Send this message in your team's Slack:

> Hey scouts! Push Bot needs your Slack Member ID to send you match notifications.
> Here's how to find it:
> 1. Click your profile picture in the top-right corner
> 2. Click **Profile**
> 3. Click the **⋮** (three dots) menu
> 4. Click **Copy Member ID**
> 5. Reply here with your name and the ID (looks like `U0123456789`)

Collect all IDs and format them for the `SCOUT_IDS` env var:
```
SCOUT_IDS=Kaveesh=U0123456789,Vedant=U9876543210,Neel=U1111111111,...
```

---

## Step 6 — Deploy to Railway

### 6a. Push to GitHub
1. In the `pushbot/` directory, initialize a git repo:
   ```bash
   git init
   git add .
   git commit -m "Initial Push Bot deploy"
   ```
2. Create a new GitHub repo (e.g., `team7419-pushbot`) and push:
   ```bash
   git remote add origin https://github.com/your-org/team7419-pushbot.git
   git push -u origin main
   ```

### 6b. Deploy on Railway
1. Go to **https://railway.app** → **New Project** → **Deploy from GitHub repo**
2. Select your `team7419-pushbot` repo
3. Railway will auto-detect the `Procfile` and deploy

### 6c. Set Environment Variables
In Railway, go to your project → **Variables** → add all of the following:

| Variable | Value |
|---|---|
| `SLACK_BOT_TOKEN` | `xoxb-...` |
| `SLACK_APP_TOKEN` | `xapp-...` |
| `NEXUS_TOKEN` | from frc.nexus/api |
| `NEXUS_API_KEY` | from frc.nexus/api |
| `NEXUS_EVENT_KEY` | `2026cancmp` |
| `TBA_API_KEY` | from thebluealliance.com |
| `TBA_EVENT_KEY` | `2026cancmp` |
| `SCOUTING_LEAD_SLACK_ID` | Damodar's Slack User ID |
| `SPREADSHEET_ID` | from Google Sheet URL |
| `SHEET_NAME` | `Detailed Shifts` |
| `SERVICE_ACCOUNT_JSON` | full JSON content of service account key |
| `SCOUT_IDS` | `Kaveesh=U...,Vedant=U...,...` |
| `CONFIRM_WAIT_MINUTES` | `7` |
| `ESCALATE_WAIT_MINUTES` | `4` |
| `TBA_POLL_INTERVAL_SECONDS` | `30` |
| `PORT` | `8080` |

### 6d. Get your Railway URL
1. In Railway → your project → **Settings** → **Domains** → **Generate Domain**
2. Your app URL will be something like `https://team7419-pushbot.up.railway.app`
3. Go back and update the Nexus webhook URLs with this real URL (Step 3)

### 6e. Verify Deployment
```bash
curl https://your-app.railway.app/health
```
Expected response:
```json
{"status": "ok", "polling_matches": 0, "queuing_alerts_sent": 0, "results_alerts_sent": 0}
```

---

## Step 7 — Testing Checklist

Run these checks before the event to confirm everything works:

### 7a. Manual push trigger
In Slack, type:
```
/push QM1
```
Expected: You (and assigned scouts for QM1) receive a DM: `🔔 Push Bot — QM1 is NOW QUEUING`

### 7b. Scouting status
```
/scouting-status QM1
```
Expected: Ephemeral message listing all scouts for QM1 with ⏳ (unconfirmed) status

### 7c. Manual confirm
```
/confirm QM1 Kaveesh
```
Expected: Ephemeral message: `✅ Marked Kaveesh as confirmed for QM1`

Then run `/scouting-status QM1` again — Kaveesh should show ✅

### 7d. My shift lookup
```
/my-shift Kaveesh
```
Expected: Ephemeral list of all matches Kaveesh is assigned to

### 7e. Nexus status pull
```
/nexus-status
```
Expected: Shows current Nexus event state (requires Nexus API key and an active event)

### 7f. Health check confirms poller is running
```bash
curl https://your-app.railway.app/health
```
The `polling_matches` count increases after `/push QM1`

### 7g. Simulate a TBA result (manual test)
After running `/push QM1`, the bot starts polling TBA for QM1.
When QM1 actually completes at the event, results alerts will fire automatically.
To test the full flow pre-event: you can temporarily set `TBA_POLL_INTERVAL_SECONDS=5`
and manually check that polling is working via the `/health` endpoint.

---

## Troubleshooting

**Bot isn't sending DMs:**
- Check `SLACK_BOT_TOKEN` is correct (`xoxb-...`)
- Verify bot was invited to the workspace and has `chat:write`, `im:write` scopes
- Check Railway logs for errors

**Schedule not loading:**
- Verify the service account email has Viewer access to the sheet
- Check `SPREADSHEET_ID` matches the sheet URL
- Check `SHEET_NAME` matches exactly (case-sensitive)
- Run `/refresh-schedule` to force a reload and check Railway logs

**Nexus webhooks not firing:**
- Verify `NEXUS_TOKEN` matches what frc.nexus shows
- Check Railway logs for 401 errors on `/nexus/event`
- Make sure your Railway URL is publicly accessible (not just internal)

**TBA polling not detecting results:**
- Verify `TBA_API_KEY` is correct
- Check `TBA_EVENT_KEY` matches the event (e.g., `2026cancmp`)
- Check Railway logs for TBA API errors

**Scout not receiving DMs:**
- Their Slack ID may be wrong or missing from `SCOUT_IDS`
- Scouting lead will receive a warning DM when an ID is missing
- Ask the scout to re-share their Member ID (Profile → ⋮ → Copy Member ID)

---

## Architecture Overview

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
                              │                    → follow-up DM
                    async loop every 30s           → escalate to lead
                    GET /match/{key} w/ ETag
                    → detect post_result_time
```

All Nexus payloads return HTTP 200 immediately and process in background threads.
TBA polling runs as an asyncio task in the FastAPI event loop.
Slack slash commands run via Socket Mode (WebSocket — no inbound HTTP needed).
