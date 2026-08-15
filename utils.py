import re
import html
import asyncio
import logging
from telegram import Update
from telegram.ext import ContextTypes
import database as db

logger = logging.getLogger(__name__)


def strip_html_tags(text: str) -> str:
    """Safely strip HTML tags for plain-text fallbacks without leaving raw tags."""
    if not text:
        return ""
    clean = re.sub(r'<[^>]+>', '', text)
    return html.unescape(clean)


def truncate_text(text: str, max_len: int = 3800) -> str:
    """Safely truncate text to avoid Telegram 4096 char limit."""
    if not text or len(text) <= max_len:
        return text
    return text[:max_len - 30] + "\n\n...(تم اختصار النص لطوله)"


async def _safe_delete_message(context: ContextTypes.DEFAULT_TYPE, chat_id: int, message_id: int):
    """Fire-and-forget background deletion helper."""
    try:
        await context.bot.delete_message(chat_id=chat_id, message_id=message_id)
    except Exception as e:
        logger.debug("Could not delete message %s: %s", message_id, e)


async def send_clean_message(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    text: str,
    update: Update = None,
    reply_markup=None,
    parse_mode="HTML"
) -> int:
    """
    Sends a message and deletes the previous bot message (and user command message if possible).
    Deletions are scheduled in the background for instant responsiveness.
    Returns the message_id of the newly sent message.
    """
    # 1. Delete user command message in background (e.g. /start or /menu)
    if update:
        msg_to_del = update.message or update.effective_message
        if msg_to_del and not getattr(update, "callback_query", None):
            try:
                asyncio.create_task(msg_to_del.delete())
            except Exception as e:
                logger.debug("Could not schedule user message deletion: %s", e)

    # 2. Delete previous bot message in background
    last_msg_id = db.get_last_message_id(chat_id)
    if last_msg_id:
        asyncio.create_task(_safe_delete_message(context, chat_id, last_msg_id))

    # 3. Send new message with clean fallback
    text_to_send = truncate_text(text, 3800)
    try:
        new_msg = await context.bot.send_message(
            chat_id=chat_id,
            text=text_to_send,
            reply_markup=reply_markup,
            parse_mode=parse_mode,
        )
    except Exception as e:
        logger.warning("send_clean_message HTML send failed: %s, falling back to clean plain text", e)
        clean_text = strip_html_tags(text_to_send)
        new_msg = await context.bot.send_message(
            chat_id=chat_id,
            text=clean_text,
            reply_markup=reply_markup,
        )

    db.set_last_message_id(chat_id, new_msg.message_id)
    return new_msg.message_id


async def safe_edit(query, text: str, reply_markup=None, parse_mode="HTML", context=None):
    """
    Safely edit message text.
    If HTML parsing fails, it strips the tags so raw <b> or <i> never appear to the user.
    """
    if not query:
        return
    text = truncate_text(text, 3800)

    # 1. Try HTML edit
    try:
        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
        return
    except Exception as e:
        if "Message is not modified" in str(e):
            return
        logger.warning("safe_edit HTML edit failed: %s", e)

    # 2. Clean plain-text fallback (Strip tags so user NEVER sees raw <b> or <i>)
    clean_text = strip_html_tags(text)
    try:
        await query.edit_message_text(clean_text, reply_markup=reply_markup)
        return
    except Exception as e:
        if "Message is not modified" in str(e):
            return
        logger.warning("safe_edit plain edit failed: %s", e)

    # 3. Fallback: send message if editing failed completely
    # But DO NOT fallback if it's a network timeout, otherwise repeated clicks send 10 messages!
    err_str = str(e).lower()
    if "timeout" in err_str or "timed out" in err_str or "readtimeout" in err_str:
        logger.warning("safe_edit dropping message instead of fallback due to timeout")
        return

    try:
        if query.message:
            await query.message.reply_text(clean_text, reply_markup=reply_markup)
        elif context and hasattr(query, "message") and query.message:
            await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=clean_text,
                reply_markup=reply_markup
            )
    except Exception as e2:
        logger.error("safe_edit fallback reply_text failed: %s", e2)


