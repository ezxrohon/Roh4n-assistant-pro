"""
LivegramBot-style relay bot, with extras.

Core: users message the bot -> messages are relayed to the ADMIN chat.
Admin replies to a relayed message -> reply is sent back to that user.

Extras:
- Block/unblock users
- Canned quick-reply buttons under each relayed message
- Message history (/history), per-user notes (/note), user info (/userinfo)
- Stats dashboard (/stats) and an interactive control panel (/panel)
- Away-mode auto-reply (/away)
- AI auto-chat via Claude/OpenAI-compatible APIs (/ai), configurable at
  runtime with /setai (no redeploy needed), with a live typing indicator
- Editable start message (/setstart) and rules text (/rules, /setrules)
- FAQ auto-responder (/addfaq, /faqlist, /delfaq)
- Scheduled reminders to users (/remind, /reminders)
- CSV export of known users (/exportusers)

Built with python-telegram-bot (async, v20+).
"""

import asyncio
import contextlib
import csv
import io
import logging
import os
import time
from datetime import datetime, timedelta, timezone

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatAction, ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

import storage

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ["BOT_TOKEN"]
ADMIN_ID = int(os.environ["ADMIN_ID"])

WEBHOOK_URL = os.environ.get("WEBHOOK_URL")  # e.g. https://your-app.onrender.com
PORT = int(os.environ.get("PORT", "8080"))
WEBHOOK_SECRET_PATH = os.environ.get("WEBHOOK_SECRET_PATH", BOT_TOKEN)

START_TIME = datetime.now(timezone.utc)

BOT_NAME = "🫧🦋ʀᴏʜ4ɴ's Assistant"
DIVIDER = "───────────────"

DEFAULT_AWAY_MESSAGE = "I'm away right now, but I'll get back to you soon."

DEFAULT_START_MESSAGE = """𓂃🌷𓂃 𝑫𝑴 𝑪𝒐𝒓𝒏𝒆𝒓𓂃🌷𓂃

👋 𝑯𝒆𝒚, {first_name}

𝒴𝑜𝓊'𝓇𝑒 𝓌𝑒𝓁𝒸𝑜𝓂𝑒 𝒽𝑒𝓇𝑒 ✨

💌 𝑺𝒆𝒏𝒅 𝑴𝒆 𝑨 𝑴𝒆𝒔𝒔𝒂𝒈𝒆
𝒲𝒽𝒶𝓉𝑒𝓋𝑒𝓇 𝓎𝑜𝓊 𝓌𝒶𝓃𝓉 𝓉𝑜 𝓈𝒶𝓎, 𝒿𝓊𝓈𝓉
𝓉𝓎𝓅𝑒 𝒾𝓉 𝒷𝑒𝓁𝑜𝓌. 💭

🌷 𝒯𝒽𝒶𝓃𝓀 𝓎𝑜𝓊 𝒻𝑜𝓇 𝓇𝑒𝒶𝒸𝒽𝒾𝓃𝑔 𝑜𝓊𝓉 ♡ ❞

{username} owner ɪs ᴏғʟɪɴᴇ ᴡᴇ ғᴇᴛᴄʜ ʏᴏᴜʀ ʀᴇǫᴜᴇsᴛ shortly"""

DEFAULT_RULES_MESSAGE = (
    "📜 A couple of quick notes before you message:\n\n"
    "• Be respectful — abusive messages get you blocked.\n"
    "• You'll usually hear back here within a day.\n"
    "• Don't share passwords, OTPs, or card details in chat."
)

# Customize via the START_MESSAGE env var (use {first_name} and {username}
# as placeholders), or at runtime with /setstart. DB setting wins over env,
# which wins over DEFAULT_START_MESSAGE above.
ENV_START_MESSAGE = os.environ.get("START_MESSAGE", DEFAULT_START_MESSAGE)


def _get_start_message() -> str:
    return storage.get_setting("start_message", ENV_START_MESSAGE)


def _get_rules_message() -> str:
    return storage.get_setting("rules_message", DEFAULT_RULES_MESSAGE)


def _message_text(message) -> str:
    return message.text or message.caption or "[media]"


def _format_uptime() -> str:
    delta = datetime.now(timezone.utc) - START_TIME
    total_seconds = int(delta.total_seconds())
    days, rem = divmod(total_seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, seconds = divmod(rem, 60)
    parts = []
    if days:
        parts.append(f"{days}d")
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    parts.append(f"{seconds}s")
    return " ".join(parts)


def _is_admin(update: Update) -> bool:
    return update.effective_user is not None and update.effective_user.id == ADMIN_ID


def _card(title: str, body: str) -> str:
    """A small branded 'card' used for admin panel-style messages."""
    return f"<b>{title}</b>\n{DIVIDER}\n{body}\n{DIVIDER}\n<i>{BOT_NAME}</i>"


async def _resolve_target_user(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Find the user a command refers to: an explicit id arg, or the user
    behind a message the admin is replying to."""
    if context.args:
        try:
            return int(context.args[0])
        except ValueError:
            return None
    replied = update.message.reply_to_message
    if replied:
        mapping = storage.get_relay(replied.message_id)
        if mapping:
            return mapping[0]
    return None


def build_relay_keyboard(admin_msg_id: int) -> InlineKeyboardMarkup:
    canned = storage.list_canned()[:6]
    rows, row = [], []
    for cid, label, _text in canned:
        row.append(InlineKeyboardButton(label, callback_data=f"cnd_{admin_msg_id}_{cid}"))
        if len(row) == 2:
            rows.append(row)
            row = []
    if row:
        rows.append(row)
    rows.append(
        [
            InlineKeyboardButton("ℹ️ Info", callback_data=f"inf_{admin_msg_id}"),
            InlineKeyboardButton("🚫 Block", callback_data=f"blk_{admin_msg_id}"),
        ]
    )
    return InlineKeyboardMarkup(rows)


# --------------------------------------------------------------------------
# User-facing handlers
# --------------------------------------------------------------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    storage.upsert_user(user.id, user.username, user.first_name)

    if user.id == ADMIN_ID:
        await update.message.reply_text(
            _card(
              https://graph.org/file/8c25e37e7290462f63234-c7692ef2b821d2b158.jpg
                f"👋 Welcome back, {first_name}",
                "Every message a user sends me will be relayed here, with quick "
                "reply buttons attached. Reply directly to a relayed message and "
                "I'll deliver your reply back to that user.\n\n"
                "🎛 <b>/panel</b> — one-tap dashboard (AI, away mode, stats)\n\n"
                "<b>Users</b>\n"
                "/users /stats /userinfo &lt;id&gt; /history &lt;id&gt;\n"
                "/block &lt;id&gt; /unblock &lt;id&gt; /note &lt;id&gt; text\n\n"
                "<b>Replies</b>\n"
                "/addcanned Label | Text /cannedlist /delcanned &lt;id&gt;\n"
                "/away &lt;text&gt; / off /ai on|off /setai ...\n\n"
                "<b>Content</b>\n"
                "/setstart &lt;text&gt; /setrules &lt;text&gt;\n"
                "/addfaq trigger | answer /faqlist /delfaq &lt;id&gt;\n\n"
                "<b>Other</b>\n"
                "/remind &lt;id&gt; &lt;minutes&gt; text /reminders\n"
                "/exportusers /broadcast &lt;text&gt;",
            ),
            parse_mode=ParseMode.HTML,
        )
        return

    display_username = f"@{user.username}" if user.username else user.first_name
    start_text = _get_start_message().format(
        first_name=user.first_name or "there",
        username=display_username,
    )
    await update.message.reply_text(start_text)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if _is_admin(update):
        await start(update, context)
        return
    await update.message.reply_text(
        "Just send a message and it will be forwarded. "
        "You'll receive the reply right here in this chat.\n\n"
        "/rules — a couple of quick notes before you message."
    )


async def rules_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(_get_rules_message())


async def ping_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    start_t = time.monotonic()
    sent = await update.message.reply_text("🏓 Pinging...")
    elapsed_ms = (time.monotonic() - start_t) * 1000
    await sent.edit_text(f"🏓 Pong! {elapsed_ms:.0f} ms")


async def alive_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if _is_admin(update):
        ai_state = "ON" if storage.get_setting("ai_mode") == "1" else "OFF"
        away_state = "ON" if storage.get_setting("away_mode") == "1" else "OFF"
        await update.message.reply_text(
            _card(
                "✅ I'm alive and running",
                f"⏱ Uptime: {_format_uptime()}\n"
                f"🤖 AI chat: {ai_state}\n"
                f"🌙 Away mode: {away_state}",
            ),
            parse_mode=ParseMode.HTML,
        )
        return
    await update.message.reply_text(
        f"✅ I'm alive and running.\n⏱ Uptime: {_format_uptime()}"
    )


async def relay_user_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Forward any message from a normal user to the admin chat."""
    message = update.effective_message
    user = update.effective_user
    storage.upsert_user(user.id, user.username, user.first_name)

    if storage.is_blocked(user.id):
        await message.reply_text("🚫 You've been blocked from using this bot.")
        return

    storage.log_message(user.id, "in", "user", _message_text(message))

    header = (
        f"✉️ From {user.mention_html()} (id: <code>{user.id}</code>"
        f"{', @' + user.username if user.username else ''})"
    )
    header_msg = await context.bot.send_message(
        chat_id=ADMIN_ID, text=header, parse_mode=ParseMode.HTML
    )
    await header_msg.edit_reply_markup(reply_markup=build_relay_keyboard(header_msg.message_id))
    storage.save_relay(header_msg.message_id, user.id, message.chat_id)

    copied = await message.copy(chat_id=ADMIN_ID)
    storage.save_relay(copied.message_id, user.id, message.chat_id)

    # Priority for automatic responses: FAQ match > AI > away-mode > plain ack.
    handled = False

    faq_answer = storage.match_faq(_message_text(message))
    if faq_answer:
        await message.reply_text(faq_answer)
        storage.log_message(user.id, "out", "faq", faq_answer)
        handled = True

    if not handled and storage.get_setting("ai_mode") == "1":
        handled = await _maybe_ai_reply(context, user.id)

    if not handled and storage.get_setting("away_mode") == "1":
        if storage.should_send_away_notice(user.id):
            away_text = storage.get_setting("away_message", DEFAULT_AWAY_MESSAGE)
            await message.reply_text(away_text)
            storage.log_message(user.id, "out", "system", away_text)
            storage.mark_away_notice_sent(user.id)
            handled = True

    if not handled:
        await message.reply_text("✅ Sent. You'll hear back here.")


async def _typing_loop(context: ContextTypes.DEFAULT_TYPE, chat_id: int):
    """Keeps Telegram's 'typing...' indicator alive (it fades after ~5s)."""
    try:
        while True:
            await context.bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
            await asyncio.sleep(4)
    except asyncio.CancelledError:
        pass


async def _maybe_ai_reply(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> bool:
    """Try to auto-answer with the configured AI provider. Returns True if a
    reply was sent. Shows a live typing indicator while generating."""
    rows = storage.get_recent_messages(user_id, limit=10)
    history = [{"role": "user" if d == "in" else "assistant", "content": t} for d, t in rows if t]
    if not history:
        return False

    import ai  # imported lazily so the app still runs without the package installed

    typing_task = asyncio.create_task(_typing_loop(context, user_id))
    try:
        reply_text = await asyncio.to_thread(ai.generate_reply, history)
    except Exception as exc:  # noqa: BLE001
        logger.exception("AI generation failed for user %s", user_id)
        await context.bot.send_message(
            chat_id=ADMIN_ID, text=f"⚠️ AI reply failed for user {user_id}: {exc}"
        )
        return False
    finally:
        typing_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await typing_task

    await context.bot.send_message(chat_id=user_id, text=reply_text)
    storage.log_message(user_id, "out", "ai", reply_text)
    await context.bot.send_message(
        chat_id=ADMIN_ID, text=f"🤖 AI replied to {user_id}:\n\n{reply_text}"
    )
    return True


# --------------------------------------------------------------------------
# Admin-facing handlers
# --------------------------------------------------------------------------

async def admin_reply(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """When the admin replies to a relayed message, deliver it to the user."""
    message = update.effective_message
    replied = message.reply_to_message
    if replied is None:
        return
    mapping = storage.get_relay(replied.message_id)
    if mapping is None:
        await message.reply_text(
            "⚠️ I can't tell which user this belongs to "
            "(reply directly to the relayed message)."
        )
        return
    user_id, _user_chat_id = mapping
    try:
        await message.copy(chat_id=user_id)
        storage.log_message(user_id, "out", "admin", _message_text(message))
        await message.reply_text("✅ Delivered.")
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to deliver reply to %s", user_id)
        await message.reply_text(f"❌ Couldn't deliver: {exc}")


async def users_count(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        return
    await update.message.reply_text(f"👥 {storage.count_users()} user(s) have messaged the bot.")


async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        return
    s = storage.get_stats()
    ai_state = "ON" if storage.get_setting("ai_mode") == "1" else "OFF"
    away_state = "ON" if storage.get_setting("away_mode") == "1" else "OFF"
    await update.message.reply_text(
        _card(
            "📊 Stats",
            f"👥 Users: {s['total_users']} ({s['blocked']} blocked)\n"
            f"💬 Messages today: {s['today_messages']}\n"
            f"📨 Messages total: {s['total_messages']}\n"
            f"❓ FAQ entries: {s['faq_count']}\n"
            f"⏰ Pending reminders: {s['pending_reminders']}\n"
            f"🤖 AI chat: {ai_state}\n"
            f"🌙 Away mode: {away_state}",
        ),
        parse_mode=ParseMode.HTML,
    )


async def userinfo_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        return
    target = await _resolve_target_user(update, context)
    if target is None:
        await update.message.reply_text(
            "Usage: /userinfo <user_id>, or reply to a relayed message with /userinfo"
        )
        return
    record = storage.get_user_record(target)
    if record is None:
        await update.message.reply_text("No record for that user id yet.")
        return
    username, first_name, blocked, last_seen = record
    msg_count = storage.get_user_message_count(target)
    notes = storage.get_notes(target)
    notes_text = "\n".join(f"• {t}" for t, _ in notes[:5]) or "—"
    await update.message.reply_text(
        _card(
            f"ℹ️ User {target}",
            f"👤 {first_name or '—'}"
            f"{' (@' + username + ')' if username else ''}\n"
            f"🚫 Blocked: {'yes' if blocked else 'no'}\n"
            f"💬 Messages: {msg_count}\n"
            f"🕓 Last seen: {last_seen}\n\n"
            f"📝 Notes:\n{notes_text}",
        ),
        parse_mode=ParseMode.HTML,
    )


async def note_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        return
    replied = update.message.reply_to_message
    target = None
    text_args = context.args
    if replied:
        mapping = storage.get_relay(replied.message_id)
        if mapping:
            target = mapping[0]
    if target is None and context.args:
        try:
            target = int(context.args[0])
            text_args = context.args[1:]
        except ValueError:
            target = None
    if target is None or not text_args:
        await update.message.reply_text(
            "Usage: /note <user_id> <text>, or reply to a relayed message with /note <text>"
        )
        return
    text = " ".join(text_args)
    storage.add_note(target, text)
    await update.message.reply_text(f"📝 Note saved for user {target}.")


async def block_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        return
    target = await _resolve_target_user(update, context)
    if target is None:
        await update.message.reply_text(
            "Usage: /block <user_id>, or reply to a relayed message with /block"
        )
        return
    storage.set_blocked(target, True)
    await update.message.reply_text(f"🚫 Blocked user {target}.")


async def unblock_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        return
    target = await _resolve_target_user(update, context)
    if target is None:
        await update.message.reply_text(
            "Usage: /unblock <user_id>, or reply to a relayed message with /unblock"
        )
        return
    storage.set_blocked(target, False)
    await update.message.reply_text(f"✅ Unblocked user {target}.")


async def history_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        return
    target = await _resolve_target_user(update, context)
    if target is None:
        await update.message.reply_text(
            "Usage: /history <user_id>, or reply to a relayed message with /history"
        )
        return
    rows = storage.get_recent_messages(target, limit=20)
    if not rows:
        await update.message.reply_text("No history for that user yet.")
        return
    lines = []
    for direction, text in rows:
        prefix = "👤" if direction == "in" else "🧑‍💼"
        lines.append(f"{prefix} {(text or '')[:200]}")
    await update.message.reply_text(
        f"🗂 Last {len(rows)} message(s) with {target}:\n\n" + "\n".join(lines)
    )


async def away_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        return
    if not context.args:
        state = storage.get_setting("away_mode", "0")
        msg = storage.get_setting("away_message", DEFAULT_AWAY_MESSAGE)
        extra = f"\nMessage: {msg}" if state == "1" else ""
        await update.message.reply_text(
            "Usage:\n/away <message> - turn on with that auto-reply text\n"
            "/away off - turn off\n\n"
            f"Currently: {'ON' if state == '1' else 'OFF'}{extra}"
        )
        return
    if context.args[0].lower() == "off":
        storage.set_setting("away_mode", "0")
        await update.message.reply_text("🌙 Away mode turned OFF.")
        return
    text = " ".join(context.args)
    storage.set_setting("away_mode", "1")
    storage.set_setting("away_message", text)
    await update.message.reply_text(f"🌙 Away mode turned ON. Users will get:\n\n{text}")


async def ai_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        return
    arg = context.args[0].lower() if context.args else None
    if arg not in ("on", "off"):
        state = storage.get_setting("ai_mode", "0")
        await update.message.reply_text(
            f"Usage: /ai on | /ai off\n\nCurrently: {'ON' if state == '1' else 'OFF'}\n"
            "Use /setai to change the provider, model, or key."
        )
        return
    if arg == "on":
        import ai  # imported lazily so the app still runs without either package installed

        if not ai.is_configured():
            await update.message.reply_text(
                "⚠️ AI chat isn't configured yet.\n\n"
                "Set it with /setai key=<your_key> model=<model_name>, or set "
                "ANTHROPIC_API_KEY (or AI_PROVIDER=openai_compatible plus AI_API_KEY "
                "and AI_BASE_URL) in Render's environment settings."
            )
            return
        storage.set_setting("ai_mode", "1")
        await update.message.reply_text(
            "🤖 AI chat turned ON. I'll auto-reply to users; you can still jump "
            "in manually anytime by replying to a relayed message."
        )
    else:
        storage.set_setting("ai_mode", "0")
        await update.message.reply_text("🤖 AI chat turned OFF.")


async def setai_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Change the AI provider/model/key at runtime, no redeploy needed.

    Usage:
      /setai show
      /setai reset
      /setai key=sk-ant-... model=claude-sonnet-4-5 provider=anthropic
      /setai key=gsk_... model=llama-3.3-70b-versatile provider=openai_compatible base_url=https://api.groq.com/openai/v1
    """
    if not _is_admin(update):
        return
    import ai  # imported lazily

    if not context.args or context.args[0].lower() == "show":
        cfg = ai.current_config_summary()
        await update.message.reply_text(
            _card(
                "🔧 AI config",
                f"Provider: {cfg['provider']}\n"
                f"Model: {cfg['model']}\n"
                f"Base URL: {cfg['base_url']}\n"
                f"API key: {cfg['api_key']}\n\n"
                "Change with:\n"
                "/setai key=&lt;key&gt; model=&lt;model&gt; "
                "[provider=anthropic|openai_compatible] [base_url=&lt;url&gt;]\n"
                "/setai reset — clear overrides, fall back to env vars",
            ),
            parse_mode=ParseMode.HTML,
        )
        return

    if context.args[0].lower() == "reset":
        for key in ("ai_provider", "ai_model", "ai_api_key", "ai_base_url", "ai_system_prompt"):
            storage.delete_setting(key)
        await update.message.reply_text("🔧 AI config reset. Falling back to environment variables.")
        return

    updates = {}
    for token in context.args:
        if "=" not in token:
            continue
        key, _, value = token.partition("=")
        key = key.strip().lower()
        value = value.strip()
        if not value:
            continue
        if key == "key":
            updates["ai_api_key"] = value
        elif key == "model":
            updates["ai_model"] = value
        elif key == "provider":
            updates["ai_provider"] = value
        elif key == "base_url":
            updates["ai_base_url"] = value
        elif key == "prompt":
            updates["ai_system_prompt"] = value

    if not updates:
        await update.message.reply_text(
            "Nothing to update. Usage: /setai key=<key> model=<model> "
            "[provider=anthropic|openai_compatible] [base_url=<url>]"
        )
        return

    for key, value in updates.items():
        storage.set_setting(key, value)

    changed = ", ".join(k.replace("ai_", "") for k in updates)
    await update.message.reply_text(f"🔧 Updated: {changed}.\nRun /setai show to confirm.")


async def setstart_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        return
    if not context.args:
        await update.message.reply_text(
            "Usage: /setstart <text> — use {first_name} and {username} as "
            "placeholders.\n/setstart reset — revert to the default.\n\n"
            f"Current:\n\n{_get_start_message()}"
        )
        return
    if context.args[0].lower() == "reset":
        storage.delete_setting("start_message")
        await update.message.reply_text("↩️ Start message reset to default.")
        return
    text = update.message.text.partition(" ")[2]
    storage.set_setting("start_message", text)
    await update.message.reply_text("✅ Start message updated. Preview:\n\n" + text)


async def setrules_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        return
    if not context.args:
        await update.message.reply_text(
            "Usage: /setrules <text>\n/setrules reset — revert to default.\n\n"
            f"Current:\n\n{_get_rules_message()}"
        )
        return
    if context.args[0].lower() == "reset":
        storage.delete_setting("rules_message")
        await update.message.reply_text("↩️ Rules reset to default.")
        return
    text = update.message.text.partition(" ")[2]
    storage.set_setting("rules_message", text)
    await update.message.reply_text("✅ Rules updated. Preview:\n\n" + text)


async def addcanned_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        return
    raw = " ".join(context.args)
    if "|" not in raw:
        await update.message.reply_text("Usage: /addcanned Label | The reply text")
        return
    label, text = (part.strip() for part in raw.split("|", 1))
    if not label or not text:
        await update.message.reply_text("Usage: /addcanned Label | The reply text")
        return
    canned_id = storage.add_canned(label, text)
    await update.message.reply_text(f"✅ Added canned reply #{canned_id}: {label}")


async def delcanned_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        return
    if not context.args:
        await update.message.reply_text("Usage: /delcanned <id> (see /cannedlist)")
        return
    try:
        canned_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Usage: /delcanned <id>")
        return
    storage.delete_canned(canned_id)
    await update.message.reply_text(f"🗑 Deleted canned reply #{canned_id}.")


async def cannedlist_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        return
    rows = storage.list_canned()
    if not rows:
        await update.message.reply_text("No canned replies yet. Add one with /addcanned Label | Text")
        return
    lines = [f"#{cid} {label} → {text}" for cid, label, text in rows]
    await update.message.reply_text("📋 Canned replies:\n\n" + "\n".join(lines))


async def addfaq_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        return
    raw = update.message.text.partition(" ")[2]
    if "|" not in raw:
        await update.message.reply_text("Usage: /addfaq trigger keyword | The answer text")
        return
    trigger, answer = (part.strip() for part in raw.split("|", 1))
    if not trigger or not answer:
        await update.message.reply_text("Usage: /addfaq trigger keyword | The answer text")
        return
    faq_id = storage.add_faq(trigger, answer)
    await update.message.reply_text(
        f"✅ Added FAQ #{faq_id}: any message containing \"{trigger}\" auto-replies with that answer."
    )


async def delfaq_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        return
    if not context.args:
        await update.message.reply_text("Usage: /delfaq <id> (see /faqlist)")
        return
    try:
        faq_id = int(context.args[0])
    except ValueError:
        await update.message.reply_text("Usage: /delfaq <id>")
        return
    storage.delete_faq(faq_id)
    await update.message.reply_text(f"🗑 Deleted FAQ #{faq_id}.")


async def faqlist_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        return
    rows = storage.list_faq()
    if not rows:
        await update.message.reply_text("No FAQ entries yet. Add one with /addfaq trigger | answer")
        return
    lines = [f"#{fid} \"{trigger}\" → {answer[:60]}" for fid, trigger, answer in rows]
    await update.message.reply_text("❓ FAQ auto-replies:\n\n" + "\n".join(lines))


async def remind_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Usage:
      Reply to a relayed message with: /remind <minutes> <text>
      Or standalone:                    /remind <user_id> <minutes> <text>
    """
    if not _is_admin(update):
        return
    if context.job_queue is None:
        await update.message.reply_text(
            "⚠️ Reminders need the job-queue extra. Add it to requirements.txt: "
            "python-telegram-bot[webhooks,job-queue]"
        )
        return

    replied = update.message.reply_to_message
    target = None
    args = context.args
    if replied:
        mapping = storage.get_relay(replied.message_id)
        if mapping:
            target = mapping[0]
    if target is None and args:
        try:
            target = int(args[0])
            args = args[1:]
        except ValueError:
            target = None

    if target is None or len(args) < 2:
        await update.message.reply_text(
            "Usage: /remind <minutes> <text> (as a reply to a relayed message)\n"
            "or: /remind <user_id> <minutes> <text>"
        )
        return

    try:
        minutes = float(args[0])
    except ValueError:
        await update.message.reply_text("Minutes must be a number, e.g. /remind 30 Don't forget to reply!")
        return

    text = " ".join(args[1:])
    remind_at = datetime.now(timezone.utc) + timedelta(minutes=minutes)
    reminder_id = storage.add_reminder(target, remind_at.isoformat(), text)
    context.job_queue.run_once(
        _reminder_job_callback,
        when=timedelta(minutes=minutes),
        data={"reminder_id": reminder_id, "user_id": target, "text": text},
        name=f"reminder_{reminder_id}",
    )
    await update.message.reply_text(
        f"⏰ Reminder #{reminder_id} set for user {target} in {minutes:g} minute(s)."
    )


async def reminders_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        return
    rows = storage.list_pending_reminders()
    if not rows:
        await update.message.reply_text("No pending reminders.")
        return
    lines = [f"#{rid} → user {uid} at {at} — {text[:60]}" for rid, uid, at, text in rows]
    await update.message.reply_text("⏰ Pending reminders:\n\n" + "\n".join(lines))


async def _reminder_job_callback(context: ContextTypes.DEFAULT_TYPE):
    data = context.job.data
    try:
        await context.bot.send_message(chat_id=data["user_id"], text=f"⏰ Reminder: {data['text']}")
        storage.log_message(data["user_id"], "out", "system", f"[reminder] {data['text']}")
    except Exception as exc:  # noqa: BLE001
        logger.exception("Failed to deliver reminder %s", data.get("reminder_id"))
        await context.bot.send_message(
            chat_id=ADMIN_ID, text=f"❌ Reminder #{data.get('reminder_id')} failed: {exc}"
        )
    finally:
        storage.mark_reminder_sent(data["reminder_id"])


async def exportusers_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        return
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(["user_id", "username", "first_name", "blocked", "last_seen", "message_count"])
    for uid in storage.all_user_ids():
        record = storage.get_user_record(uid)
        if record is None:
            continue
        username, first_name, blocked, last_seen = record
        writer.writerow([uid, username or "", first_name or "", blocked, last_seen, storage.get_user_message_count(uid)])
    buf.seek(0)
    data = io.BytesIO(buf.getvalue().encode("utf-8"))
    data.name = "users.csv"
    await update.message.reply_document(document=data, filename="users.csv", caption="👥 User export")


async def broadcast(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        return
    text = " ".join(context.args) if context.args else None
    if not text and not update.message.reply_to_message:
        await update.message.reply_text(
            "Usage: /broadcast <text>, or reply to a message with /broadcast "
            "to send that message (including media) to everyone."
        )
        return
    sent, failed = 0, 0
    for uid in storage.all_user_ids():
        try:
            if update.message.reply_to_message and not text:
                await update.message.reply_to_message.copy(chat_id=uid)
            else:
                await context.bot.send_message(chat_id=uid, text=text)
            sent += 1
        except Exception:  # noqa: BLE001
            failed += 1
    await update.message.reply_text(f"📣 Broadcast done. Sent: {sent}, failed: {failed}.")


# --------------------------------------------------------------------------
# /panel — interactive admin dashboard
# --------------------------------------------------------------------------

def _panel_text() -> str:
    s = storage.get_stats()
    ai_on = storage.get_setting("ai_mode") == "1"
    away_on = storage.get_setting("away_mode") == "1"
    return _card(
        "🎛 Control Panel",
        f"👥 Users: {s['total_users']} ({s['blocked']} blocked)\n"
        f"💬 Messages today: {s['today_messages']}\n"
        f"🤖 AI chat: {'🟢 ON' if ai_on else '🔴 OFF'}\n"
        f"🌙 Away mode: {'🟢 ON' if away_on else '🔴 OFF'}",
    )


def _panel_keyboard() -> InlineKeyboardMarkup:
    ai_on = storage.get_setting("ai_mode") == "1"
    away_on = storage.get_setting("away_mode") == "1"
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    f"🤖 AI: {'ON' if ai_on else 'OFF'}", callback_data="panel_toggle_ai"
                ),
                InlineKeyboardButton(
                    f"🌙 Away: {'ON' if away_on else 'OFF'}", callback_data="panel_toggle_away"
                ),
            ],
            [
                InlineKeyboardButton("🔄 Refresh", callback_data="panel_refresh"),
                InlineKeyboardButton("❌ Close", callback_data="panel_close"),
            ],
        ]
    )


async def panel_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _is_admin(update):
        return
    await update.message.reply_text(
        _panel_text(), parse_mode=ParseMode.HTML, reply_markup=_panel_keyboard()
    )


async def panel_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != ADMIN_ID:
        await query.answer("⛔ Not for you.", show_alert=True)
        return

    action = query.data

    if action == "panel_close":
        await query.answer()
        await query.message.delete()
        return

    if action == "panel_toggle_ai":
        turning_on = storage.get_setting("ai_mode") != "1"
        if turning_on:
            import ai  # imported lazily

            if not ai.is_configured():
                await query.answer(
                    "⚠️ AI isn't configured. Use /setai key=<key> first.", show_alert=True
                )
                return
        storage.set_setting("ai_mode", "1" if turning_on else "0")
        await query.answer(f"AI chat turned {'ON' if turning_on else 'OFF'}.")

    elif action == "panel_toggle_away":
        turning_on = storage.get_setting("away_mode") != "1"
        if turning_on and not storage.get_setting("away_message"):
            storage.set_setting("away_message", DEFAULT_AWAY_MESSAGE)
        storage.set_setting("away_mode", "1" if turning_on else "0")
        await query.answer(f"Away mode turned {'ON' if turning_on else 'OFF'}.")

    elif action == "panel_refresh":
        await query.answer("Refreshed.")

    else:
        await query.answer()
        return

    with contextlib.suppress(Exception):
        await query.edit_message_text(
            _panel_text(), parse_mode=ParseMode.HTML, reply_markup=_panel_keyboard()
        )


# --------------------------------------------------------------------------
# Inline button callbacks (relay keyboard)
# --------------------------------------------------------------------------

async def canned_button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != ADMIN_ID:
        await query.answer("⛔ Not for you.", show_alert=True)
        return
    _, admin_msg_id, canned_id = query.data.split("_")
    mapping = storage.get_relay(int(admin_msg_id))
    if mapping is None:
        await query.answer("Couldn't find that user.", show_alert=True)
        return
    user_id, _ = mapping
    canned = storage.get_canned(int(canned_id))
    if canned is None:
        await query.answer("That canned reply no longer exists.", show_alert=True)
        return
    label, text = canned
    try:
        await context.bot.send_message(chat_id=user_id, text=text)
        storage.log_message(user_id, "out", "canned", text)
        await query.answer(f"Sent: {label}")
    except Exception as exc:  # noqa: BLE001
        await query.answer(f"Failed: {exc}", show_alert=True)


async def block_button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != ADMIN_ID:
        await query.answer("⛔ Not for you.", show_alert=True)
        return
    admin_msg_id = int(query.data.split("_", 1)[1])
    mapping = storage.get_relay(admin_msg_id)
    if mapping is None:
        await query.answer("Couldn't find that user.", show_alert=True)
        return
    user_id, _ = mapping
    storage.set_blocked(user_id, True)
    await query.answer("🚫 User blocked.", show_alert=True)


async def info_button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.from_user.id != ADMIN_ID:
        await query.answer("⛔ Not for you.", show_alert=True)
        return
    admin_msg_id = int(query.data.split("_", 1)[1])
    mapping = storage.get_relay(admin_msg_id)
    if mapping is None:
        await query.answer("Couldn't find that user.", show_alert=True)
        return
    user_id, _ = mapping
    record = storage.get_user_record(user_id)
    if record is None:
        await query.answer("No record for that user.", show_alert=True)
        return
    username, first_name, blocked, last_seen = record
    count = storage.get_user_message_count(user_id)
    await query.answer(
        f"{first_name or '—'} ({'@' + username if username else 'no username'})\n"
        f"id {user_id} · {count} msgs · {'blocked' if blocked else 'active'}\n"
        f"last seen {last_seen}",
        show_alert=True,
    )


# --------------------------------------------------------------------------
# Routing
# --------------------------------------------------------------------------

async def route_incoming(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Single entry point that decides: admin reply vs. user message."""
    if update.effective_user is None or update.effective_message is None:
        return
    if update.effective_user.id == ADMIN_ID:
        if update.effective_message.reply_to_message is not None:
            await admin_reply(update, context)
        return
    await relay_user_message(update, context)


async def _reschedule_reminders(application: Application):
    """On startup, re-arm any pending reminders that were saved before a
    restart (e.g. a Render redeploy) so they still fire."""
    if application.job_queue is None:
        return
    now = datetime.now(timezone.utc)
    for reminder_id, user_id, remind_at, text in storage.list_pending_reminders():
        try:
            when = datetime.fromisoformat(remind_at)
        except ValueError:
            continue
        delay = when - now
        if delay.total_seconds() <= 0:
            delay = timedelta(seconds=5)  # fire almost immediately if we missed it
        application.job_queue.run_once(
            _reminder_job_callback,
            when=delay,
            data={"reminder_id": reminder_id, "user_id": user_id, "text": text},
            name=f"reminder_{reminder_id}",
        )


def build_application() -> Application:
    storage.init_db()
    application = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(_reschedule_reminders)
        .build()
    )

    # User + shared commands
    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("rules", rules_command))
    application.add_handler(CommandHandler("ping", ping_command))
    application.add_handler(CommandHandler("alive", alive_command))

    # Admin dashboard
    application.add_handler(CommandHandler("panel", panel_command))

    # Users / moderation
    application.add_handler(CommandHandler("users", users_count))
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(CommandHandler("userinfo", userinfo_command))
    application.add_handler(CommandHandler("note", note_command))
    application.add_handler(CommandHandler("block", block_command))
    application.add_handler(CommandHandler("unblock", unblock_command))
    application.add_handler(CommandHandler("history", history_command))

    # Replies
    application.add_handler(CommandHandler("away", away_command))
    application.add_handler(CommandHandler("ai", ai_command))
    application.add_handler(CommandHandler("setai", setai_command))
    application.add_handler(CommandHandler("addcanned", addcanned_command))
    application.add_handler(CommandHandler("delcanned", delcanned_command))
    application.add_handler(CommandHandler("cannedlist", cannedlist_command))

    # Content
    application.add_handler(CommandHandler("setstart", setstart_command))
    application.add_handler(CommandHandler("setrules", setrules_command))
    application.add_handler(CommandHandler("addfaq", addfaq_command))
    application.add_handler(CommandHandler("delfaq", delfaq_command))
    application.add_handler(CommandHandler("faqlist", faqlist_command))

    # Reminders + export + broadcast
    application.add_handler(CommandHandler("remind", remind_command))
    application.add_handler(CommandHandler("reminders", reminders_command))
    application.add_handler(CommandHandler("exportusers", exportusers_command))
    application.add_handler(CommandHandler("broadcast", broadcast))

    # Callback buttons
    application.add_handler(CallbackQueryHandler(canned_button_callback, pattern=r"^cnd_"))
    application.add_handler(CallbackQueryHandler(block_button_callback, pattern=r"^blk_"))
    application.add_handler(CallbackQueryHandler(info_button_callback, pattern=r"^inf_"))
    application.add_handler(CallbackQueryHandler(panel_callback, pattern=r"^panel_"))

    # Catch-all for normal messages (text, photos, docs, voice, etc.)
    application.add_handler(MessageHandler(filters.ALL & ~filters.COMMAND, route_incoming))

    return application


def main():
    application = build_application()

    if WEBHOOK_URL:
        logger.info("Starting in webhook mode on port %s", PORT)
        application.run_webhook(
            listen="0.0.0.0",
            port=PORT,
            url_path=WEBHOOK_SECRET_PATH,
            webhook_url=f"{WEBHOOK_URL.rstrip('/')}/{WEBHOOK_SECRET_PATH}",
        )
    else:
        logger.info("WEBHOOK_URL not set - starting in polling mode (local dev)")
        application.run_polling()


if __name__ == "__main__":
    main()
