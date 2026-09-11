"""
LivegramBot-style relay bot, with extras.

Core: users message the bot -> messages are relayed to the ADMIN chat.
      Admin replies to a relayed message -> reply is sent back to that user.

Extras:
- Block/unblock users
- Canned quick-reply buttons under each relayed message
- Message history (/history)
- Stats dashboard (/stats)
- Away-mode auto-reply (/away)
- AI auto-chat via the Anthropic API (/ai) — needs ANTHROPIC_API_KEY set

Built with python-telegram-bot (async, v20+).
"""

import logging
import os
import time
from datetime import datetime, timezone

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
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

START_TIME = time.time()

WEBHOOK_URL = os.environ.get("WEBHOOK_URL")  # e.g. https://your-app.onrender.com
PORT = int(os.environ.get("PORT", "8080"))
WEBHOOK_SECRET_PATH = os.environ.get("WEBHOOK_SECRET_PATH", BOT_TOKEN)

START_TIME = datetime.now(timezone.utc)

DEFAULT_AWAY_MESSAGE = "I'm away right now, but I'll get back to you soon."

DEFAULT_START_MESSAGE = """𓂃🌷𓂃  𝑫𝑴 𝑪𝒐𝒓𝒏𝒆𝒓𓂃🌷𓂃
             
👋 𝑯𝒆𝒚, {first_name}
𝒴𝑜𝓊'𝓇𝑒 𝓌𝑒𝓁𝒸𝑜𝓂𝑒 𝒽𝑒𝓇𝑒 ✨

💌 𝑺𝒆𝒏𝒅 𝑴𝒆 𝑨 𝑴𝒆𝒔𝒔𝒂𝒈𝒆
𝒲𝒽𝒶𝓉𝑒𝓋𝑒𝓇 𝓎𝑜𝓊 𝓌𝒶𝓃𝓉 𝓉𝑜 𝓈𝒶𝓎, 𝒿𝓊𝓈𝓉
𝓉𝓎𝓅𝑒 𝒾𝓉 𝒷𝑒𝓁𝑜𝓌. 💭
🌷 𝒯𝒽𝒶𝓃𝓀 𝓎𝑜𝓊 𝒻𝑜𝓇 𝓇𝑒𝒶𝒸𝒽𝒾𝓃𝑔 𝑜𝓊𝓉 ♡ ❞
{username} owner ɪs ᴏғʟɪɴᴇ ᴡᴇ  ғᴇᴛᴄʜ ʏᴏᴜʀ ʀᴇǫᴜᴇsᴛ shortly"""

# Customize via the START_MESSAGE env var (use {first_name} and {username}
# as placeholders). Falls back to DEFAULT_START_MESSAGE above if unset.
START_MESSAGE_TEMPLATE = os.environ.get("START_MESSAGE", DEFAULT_START_MESSAGE)


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
    rows.append([InlineKeyboardButton("🚫 Block", callback_data=f"blk_{admin_msg_id}")])
    return InlineKeyboardMarkup(rows)


# --------------------------------------------------------------------------
# User-facing handlers
# --------------------------------------------------------------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    storage.upsert_user(user.id, user.username, user.first_name)

    if user.id == ADMIN_ID:
        await update.message.reply_text(
            "👋 Welcome back, admin.\n\n"
            "Every message a user sends me will be relayed here, with quick "
            "reply buttons attached. Reply directly to a relayed message and "
            "I'll deliver your reply back to that user.\n\n"
            "Commands:\n"
            "/users - user count\n"
            "/stats - stats dashboard\n"
            "/history <id> - view a user's recent messages (or reply)\n"
            "/block <id> / /unblock <id> - manage access (or reply)\n"
            "/addcanned Label | Text - add a quick-reply button\n"
            "/delcanned <id> / /cannedlist - manage quick replies\n"
            "/away <text> / /away off - auto-reply while you're away\n"
            "/ai on / /ai off - let AI auto-chat with users\n"
            "/broadcast <text> - message every known user"
        )
        return

    display_username = f"@{user.username}" if user.username else user.first_name
    start_text = START_MESSAGE_TEMPLATE.format(
        first_name=user.first_name or "there",
        username=display_username,
    )
    await update.message.reply_text(start_text)


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Just send a message and it will be forwarded. "
        "You'll receive the reply right here in this chat."
    )


async def ping_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    start = time.monotonic()
    sent = await update.message.reply_text("🏓 Pinging...")
    elapsed_ms = (time.monotonic() - start) * 1000
    await sent.edit_text(f"🏓 Pong! {elapsed_ms:.0f} ms")


async def alive_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if _is_admin(update):
        ai_state = "ON" if storage.get_setting("ai_mode") == "1" else "OFF"
        away_state = "ON" if storage.get_setting("away_mode") == "1" else "OFF"
        await update.message.reply_text(
            "✅ <b>I'm alive and running.</b>\n\n"
            f"⏱ Uptime: {_format_uptime()}\n"
            f"🤖 AI chat: {ai_state}\n"
            f"🌙 Away mode: {away_state}",
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

    # Decide how (if at all) to respond automatically: AI takes priority
    # over away-mode, which takes priority over the plain ack.
    handled = False
    if storage.get_setting("ai_mode") == "1":
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


async def _maybe_ai_reply(context: ContextTypes.DEFAULT_TYPE, user_id: int) -> bool:
    """Try to auto-answer with Claude. Returns True if a reply was sent."""
    rows = storage.get_recent_messages(user_id, limit=10)
    history = [{"role": "user" if d == "in" else "assistant", "content": t} for d, t in rows if t]
    if not history:
        return False

    import ai  # imported lazily so the app still runs without the package installed

    try:
        reply_text = ai.generate_reply(history)
    except Exception as exc:  # noqa: BLE001
        logger.exception("AI generation failed for user %s", user_id)
        await context.bot.send_message(
            chat_id=ADMIN_ID, text=f"⚠️ AI reply failed for user {user_id}: {exc}"
        )
        return False

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
        "📊 <b>Stats</b>\n\n"
        f"👥 Users: {s['total_users']} ({s['blocked']} blocked)\n"
        f"💬 Messages today: {s['today_messages']}\n"
        f"📨 Messages total: {s['total_messages']}\n"
        f"🤖 AI chat: {ai_state}\n"
        f"🌙 Away mode: {away_state}",
        parse_mode=ParseMode.HTML,
    )


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
            f"Usage: /ai on | /ai off\n\nCurrently: {'ON' if state == '1' else 'OFF'}"
        )
        return
    if arg == "on":
        import ai  # imported lazily so the app still runs without either package installed

        if not ai.is_configured():
            await update.message.reply_text(
                "⚠️ AI chat isn't configured yet. Set ANTHROPIC_API_KEY (for "
                "Anthropic), or AI_PROVIDER=openai_compatible plus AI_API_KEY "
                "and AI_BASE_URL (for Groq/Gemini/OpenRouter/etc.) in Render's "
                "environment settings first."
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
# Inline button callbacks
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


def build_application() -> Application:
    storage.init_db()

    application = Application.builder().token(BOT_TOKEN).build()

    application.add_handler(CommandHandler("start", start))
    application.add_handler(CommandHandler("help", help_command))
    application.add_handler(CommandHandler("ping", ping_command))
    application.add_handler(CommandHandler("alive", alive_command))
    application.add_handler(CommandHandler("users", users_count))
    application.add_handler(CommandHandler("stats", stats_command))
    application.add_handler(CommandHandler("block", block_command))
    application.add_handler(CommandHandler("unblock", unblock_command))
    application.add_handler(CommandHandler("history", history_command))
    application.add_handler(CommandHandler("away", away_command))
    application.add_handler(CommandHandler("ai", ai_command))
    application.add_handler(CommandHandler("addcanned", addcanned_command))
    application.add_handler(CommandHandler("delcanned", delcanned_command))
    application.add_handler(CommandHandler("cannedlist", cannedlist_command))
    application.add_handler(CommandHandler("broadcast", broadcast))

    application.add_handler(CallbackQueryHandler(canned_button_callback, pattern=r"^cnd_"))
    application.add_handler(CallbackQueryHandler(block_button_callback, pattern=r"^blk_"))

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
