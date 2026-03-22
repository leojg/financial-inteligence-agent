# n8n Integration

Self-hosted workflow automation connecting the Finance Intelligence Agent API to Telegram via [n8n](https://n8n.io/).

## Workflow Templates

**Scheduled Insights Digest** (`workflows/scheduled_insights.json`) — Cron trigger → POST `/insights` → format aggregations, habits, and suggestions → Telegram message. Default schedule: every minute (adjust to weekly/monthly before activating in production).

**Chat via Telegram** (`workflows/chat.json`) — Telegram bot message → POST `/chat` with conversation persistence → Telegram reply. Each Telegram user gets persistent conversation history via the chat ID as `conversation_id`.

## Setup

### Prerequisites

- Docker Compose running with `--profile api --profile n8n`
- A Telegram bot token (create via [@BotFather](https://t.me/BotFather))
- Your Telegram chat ID (send any message to your bot, then visit `https://api.telegram.org/bot<TOKEN>/getUpdates` and look for `"chat": {"id": ...}`)

### Configuration

Add to your `.env`:

```env
# Telegram
N8N_TELEGRAM_BOT_TOKEN=your-bot-token
N8N_TELEGRAM_CHAT_ID=your-chat-id

# Dev only — ngrok tunnel for Telegram webhooks
NGROK_AUTHTOKEN=your-ngrok-authtoken
WEBHOOK_URL=https://your-domain.ngrok-free.app/
```

### Start

```bash
# Production
docker compose --profile api --profile n8n up

# Dev (with ngrok tunnel for Telegram webhooks)
docker compose --profile api --profile n8n --profile dev up
```

Workflows are auto-imported on first boot via the entrypoint script. Telegram credentials are injected from environment variables — no manual UI configuration needed.

### Dev Tunnel

Telegram webhooks require a public URL. In development, ngrok provides a tunnel to your local n8n instance. Claim a free static domain at https://dashboard.ngrok.com/domains to avoid URL changes on restart.

The ngrok inspection UI is available at `http://localhost:4040` for debugging incoming webhook traffic.

## Customization

### Changing the schedule

Open the Scheduled Insights workflow in n8n (`http://localhost:5678`), click the Schedule Trigger node, and adjust the interval. Export the updated JSON to overwrite the template if you want the change committed.

### Formatting

The insights digest uses a JavaScript Code node to format the API response for Telegram (Markdown). Edit the Code node to change the message layout, add/remove sections, or adjust emoji.

### Adding workflows

The API exposes three endpoints that any n8n HTTP Request node can call:

| Endpoint | Method | Purpose |
|---|---|---|
| `/reconciliation` | POST | Upload files and run reconciliation |
| `/insights` | POST | Run spending analysis |
| `/insights/latest` | GET | Return cached insights |
| `/chat` | POST | Send a message to the finance chat agent |

Build new workflows in the n8n UI, export as JSON, sanitize credentials and IDs, and add to `workflows/`.