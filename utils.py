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


async def delete_messages_bulk(context: ContextTypes.DEFAULT_TYPE, chat_id: int, message_ids: list[int]):
    """Safely delete a list of message IDs using Telegram's bulk delete API or fallback."""
    if not message_ids:
        return
    
    unique_ids = sorted(list(dict.fromkeys(int(m) for m in message_ids if m and int(m) > 0)))
    if not unique_ids:
        return

    # Delete in batches of 50 (Telegram limit per delete_messages call is 100)
    for i in range(0, len(unique_ids), 50):
        batch = unique_ids[i:i + 50]
        try:
            await context.bot.delete_messages(chat_id=chat_id, message_ids=batch)
        except Exception:
            # Fallback to individual deletion if batch delete encounters any un-deletable item
            for mid in batch:
                try:
                    await context.bot.delete_message(chat_id=chat_id, message_id=mid)
                except Exception:
                    pass


async def clean_entire_chat(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    keep_message_id: int = None,
    nearby_range: int = 40
):
    """
    Cleans ALL previous messages in the chat history, leaving at most keep_message_id intact.
    Runs asynchronously in the background.
    """
    if not chat_id:
        return

    # 1. Fetch all tracked message IDs from DB
    tracked = db.get_and_clear_chat_messages(chat_id, keep_message_id=keep_message_id)
    
    # 2. Add last known message ID if different from keep_message_id
    last_id = db.get_last_message_id(chat_id)
    if last_id and last_id != keep_message_id and last_id not in tracked:
        tracked.append(last_id)

    # 3. Add a range around known IDs to catch any untracked text/media messages
    all_to_del = set(tracked)
    ref_id = keep_message_id or last_id
    if ref_id:
        for mid in range(max(1, ref_id - nearby_range), ref_id + nearby_range + 1):
            if mid != keep_message_id:
                all_to_del.add(mid)

    # 4. Schedule background deletion
    if all_to_del:
        asyncio.create_task(delete_messages_bulk(context, chat_id, list(all_to_del)))


async def send_clean_message(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    text: str,
    update: Update = None,
    reply_markup=None,
    parse_mode="HTML"
) -> int:
    """
    Sends a message and cleans up prior messages for instant responsiveness and clean UI.
    Returns the message_id of the newly sent message.
    """
    # 1. Track and delete user command/input message in background
    user_msg_id = None
    if update:
        msg_to_del = update.message or update.effective_message
        if msg_to_del and not getattr(update, "callback_query", None):
            user_msg_id = msg_to_del.message_id
            db.track_chat_message(chat_id, user_msg_id)
            asyncio.create_task(_safe_delete_message(context, chat_id, user_msg_id))

    # 2. Track and delete previous bot message in background
    last_msg_id = db.get_last_message_id(chat_id)
    if last_msg_id:
        db.track_chat_message(chat_id, last_msg_id)
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
    db.track_chat_message(chat_id, new_msg.message_id)
    return new_msg.message_id


async def safe_edit(query, text: str, reply_markup=None, parse_mode="HTML", context=None):
    """
    Safely edit message text.
    If HTML parsing fails, it strips the tags so raw <b> or <i> never appear to the user.
    """
    if not query:
        return
    text = truncate_text(text, 3800)

    # Track message ID
    if query.message:
        db.set_last_message_id(query.message.chat_id, query.message.message_id)
        db.track_chat_message(query.message.chat_id, query.message.message_id)

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
    err_str = str(e).lower()
    if "timeout" in err_str or "timed out" in err_str or "readtimeout" in err_str:
        logger.warning("safe_edit dropping message instead of fallback due to timeout")
        return

    try:
        if query.message:
            sent = await query.message.reply_text(clean_text, reply_markup=reply_markup)
            db.set_last_message_id(sent.chat_id, sent.message_id)
            db.track_chat_message(sent.chat_id, sent.message_id)
        elif context and hasattr(query, "message") and query.message:
            sent = await context.bot.send_message(
                chat_id=query.message.chat_id,
                text=clean_text,
                reply_markup=reply_markup
            )
            db.set_last_message_id(sent.chat_id, sent.message_id)
            db.track_chat_message(sent.chat_id, sent.message_id)
    except Exception as e2:
        logger.error("safe_edit fallback reply_text failed: %s", e2)


