# What changed

Drop-in replacements for `bot.py`, `ai.py`, `storage.py`, and `requirements.txt`
in your repo. No other files need to change.

## New admin commands ("plugins")

| Command | Does |
|---|---|
| `/panel` | Interactive dashboard — tap to toggle AI / Away mode, see live stats, refresh, close |
| `/setstart <text>` | Change the `/start` message without redeploying (`{first_name}`, `{username}` placeholders work). `/setstart reset` reverts. |
| `/setai key=... model=... [provider=...] [base_url=...]` | Change the AI key/model/provider at runtime. `/setai show` (masked) and `/setai reset` also work. |
| `/setrules <text>` / `/rules` | Editable rules text shown to users on request |
| `/userinfo <id>` | Card with name, block status, message count, last seen, notes |
| `/note <id> <text>` | Save a private note about a user (shows up in `/userinfo`) |
| `/addfaq trigger \| answer`, `/faqlist`, `/delfaq <id>` | Auto-reply when a user's message contains a keyword — checked before AI/away |
| `/remind <minutes> <text>` (reply) or `/remind <id> <minutes> <text>`, `/reminders` | Schedule a one-off message to a user; reminders survive restarts |
| `/exportusers` | Sends a CSV of all known users as a file |

All of the above are admin-only, same as the existing commands.

## UI / polish

- Every admin-facing panel (`/stats`, `/alive`, `/panel`, `/setai show`, `/userinfo`) now
  renders as a consistent branded "card": bold title, divider, `🫧🦋ʀᴏʜ4ɴ's Assistant` footer.
- The relay keyboard under each user message now has an **ℹ️ Info** button next to **🚫 Block**,
  showing a quick popup (name, id, message count, block status) without leaving the chat.
- When AI auto-chat is generating a reply, the bot now shows Telegram's live "typing…"
  indicator the whole time (it used to just go silent while Claude/OpenAI generated).
- Bot name is set once as `BOT_NAME` at the top of `bot.py` — change that single line to
  rebrand everywhere.

## Config precedence for AI (important)

`/setai` writes to the SQLite `settings` table and **overrides** the `ANTHROPIC_API_KEY` /
`AI_MODEL` / `AI_PROVIDER` / `AI_BASE_URL` env vars at runtime. If you ever want to fall back
to what's set in Render's environment, run `/setai reset`.

⚠️ On Render's free tier the SQLite file is on ephemeral disk (same caveat as your README
already notes for users/history) — a redeploy or idle-restart will wipe anything set via
`/setai` or `/setstart` back to your env vars / defaults. For that to persist permanently,
attach a persistent disk or move `storage.py` to Postgres.

## requirements.txt

Added the `job-queue` extra (needed for `/remind`):

```
python-telegram-bot[webhooks,job-queue]==21.6
```

## How to apply

1. Replace `bot.py`, `ai.py`, `storage.py`, `requirements.txt` in your repo with the attached versions.
2. Commit and push — Render will redeploy automatically if auto-deploy is on.
3. Once it's live, DM your bot `/panel` as the admin to see the new dashboard.
