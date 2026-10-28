import re
import html
import os
import hashlib
import asyncio
import logging
from telegram import Update
from telegram.ext import ContextTypes
import database as db
from config import PHOTOS_DIR, DOCUMENTS_DIR

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
    """Safely delete a list of message IDs using Telegram's bulk delete API with robust fallback."""
    if not message_ids or not chat_id:
        return
    
    unique_ids = sorted(list(dict.fromkeys(int(m) for m in message_ids if m and int(m) > 0)))
    if not unique_ids:
        return

    for i in range(0, len(unique_ids), 100):
        batch = unique_ids[i:i + 100]
        try:
            await context.bot.delete_messages(chat_id=chat_id, message_ids=batch)
        except Exception:
            for mid in batch:
                try:
                    await context.bot.delete_message(chat_id=chat_id, message_id=mid)
                except Exception:
                    pass


async def clean_entire_chat(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    keep_message_id: int = None,
    extra_ids: list[int] = None,
):
    """Cleans tracked messages in the chat history for a distraction-free experience."""
    if not chat_id:
        return

    tracked = db.get_and_clear_chat_messages(chat_id, keep_message_id=keep_message_id)

    if extra_ids:
        for eid in extra_ids:
            if eid and eid != keep_message_id:
                tracked.append(eid)

    all_to_del = {int(mid) for mid in tracked if mid and int(mid) > 0 and mid != keep_message_id}

    if keep_message_id:
        db.track_chat_message(chat_id, keep_message_id)

    if all_to_del:
        await delete_messages_bulk(context, chat_id, list(all_to_del))


async def send_clean_message(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    text: str,
    update: Update = None,
    reply_markup=None,
    parse_mode="HTML"
) -> int:
    """
    Sends a message and cleans up prior user input and tracked bot messages for an ultra-clean UI.
    """
    if update:
        msg_to_del = update.message or update.effective_message
        if msg_to_del and not getattr(update, "callback_query", None):
            user_msg_id = msg_to_del.message_id
            db.track_chat_message(chat_id, user_msg_id)
            asyncio.create_task(_safe_delete_message(context, chat_id, user_msg_id))

    # Clean old bot messages so only the new message remains in the chat
    await clean_entire_chat(context, chat_id)

    text_to_send = truncate_text(text, 3800)
    try:
        new_msg = await context.bot.send_message(
            chat_id=chat_id,
            text=text_to_send,
            reply_markup=reply_markup,
            parse_mode=parse_mode,
        )
    except Exception as e:
        logger.warning("send_clean_message HTML failed: %s, falling back to plain text", e)
        clean_text = strip_html_tags(text_to_send)
        new_msg = await context.bot.send_message(
            chat_id=chat_id,
            text=clean_text,
            reply_markup=reply_markup,
        )

    db.track_chat_message(chat_id, new_msg.message_id)
    return new_msg.message_id


async def safe_edit(query, text: str, reply_markup=None, parse_mode="HTML", context=None):
    """Safely edit message text in place. If current message is media, cleanly replaces it."""
    if not query:
        return
    text = truncate_text(text, 3800)

    chat_id = None
    msg = getattr(query, "message", None)
    if msg and hasattr(msg, "chat") and msg.chat:
        chat_id = msg.chat.id
    elif getattr(query, "from_user", None):
        chat_id = query.from_user.id

    if not chat_id:
        return

    bot = context.bot if context and hasattr(context, "bot") else query.get_bot()

    # If the message currently has media (photo or document), Telegram cannot edit text in-place.
    # We must delete the media message and send a single text message in its place.
    if msg and (getattr(msg, "photo", None) or getattr(msg, "document", None)):
        try:
            await msg.delete()
        except Exception:
            pass
        try:
            new_msg = await bot.send_message(
                chat_id=chat_id,
                text=text,
                reply_markup=reply_markup,
                parse_mode=parse_mode,
            )
            db.track_chat_message(chat_id, new_msg.message_id)
            return
        except Exception:
            clean_text = strip_html_tags(text)
            new_msg = await bot.send_message(chat_id=chat_id, text=clean_text, reply_markup=reply_markup)
            db.track_chat_message(chat_id, new_msg.message_id)
            return

    if msg and hasattr(msg, "message_id") and msg.message_id:
        db.track_chat_message(chat_id, msg.message_id)

    # 1. Try HTML edit
    try:
        await query.edit_message_text(text=text, reply_markup=reply_markup, parse_mode=parse_mode)
        return
    except Exception as e:
        if "Message is not modified" in str(e):
            return
        logger.debug("safe_edit HTML failed: %s", e)

    # 2. Plain text fallback edit
    clean_text = strip_html_tags(text)
    try:
        await query.edit_message_text(text=clean_text, reply_markup=reply_markup)
        return
    except Exception as e:
        if "Message is not modified" in str(e):
            return

    # 3. Fresh message fallback (delete old message first to prevent clutter)
    try:
        if msg:
            try:
                await msg.delete()
            except Exception:
                pass
        sent = await bot.send_message(chat_id=chat_id, text=clean_text, reply_markup=reply_markup)
        db.track_chat_message(chat_id, sent.message_id)
    except Exception as e2:
        logger.error("safe_edit fallback send failed: %s", e2)


# ─── Media Downloader Helpers ─────────────────────────────────────────────────

async def download_telegram_photo(bot, photo_obj) -> str:
    """
    Downloads photo bytes from Telegram, computes MD5 hash for deduplication,
    saves to data/photos/photo_{hash}.jpg, and returns relative path.
    """
    file_info = await bot.get_file(photo_obj.file_id)
    byte_array = await file_info.download_as_bytearray()
    
    content_hash = hashlib.md5(byte_array).hexdigest()[:12]
    filename = f"photo_{content_hash}.jpg"
    target_path = os.path.join(PHOTOS_DIR, filename)

    if not os.path.exists(target_path):
        with open(target_path, "wb") as f:
            f.write(byte_array)

    rel_path = os.path.relpath(target_path, os.path.dirname(os.path.abspath(__file__))).replace("\\", "/")
    return rel_path


async def download_telegram_document(bot, doc_obj) -> tuple[str, str]:
    """
    Downloads document from Telegram, saves to data/documents/{filename},
    and returns (relative_path, original_file_name).
    """
    orig_name = doc_obj.file_name or "document.pdf"
    clean_name = re.sub(r'[^a-zA-Z0-9_\-\.\u0600-\u06FF]', '_', orig_name)
    file_info = await bot.get_file(doc_obj.file_id)
    byte_array = await file_info.download_as_bytearray()

    content_hash = hashlib.md5(byte_array).hexdigest()[:8]
    filename = f"{content_hash}_{clean_name}"
    target_path = os.path.join(DOCUMENTS_DIR, filename)

    if not os.path.exists(target_path):
        with open(target_path, "wb") as f:
            f.write(byte_array)

    rel_path = os.path.relpath(target_path, os.path.dirname(os.path.abspath(__file__))).replace("\\", "/")
    return rel_path, orig_name


def resolve_media_path(content: str) -> str:
    """
    Robustly resolves a stored media path into a valid existing absolute path.
    Checks relative paths, absolute paths, and standard media directories.
    """
    if not content:
        return ""

    if os.path.exists(content):
        return os.path.abspath(content)

    base_dir = os.path.dirname(os.path.abspath(__file__))
    candidate1 = os.path.join(base_dir, content)
    if os.path.exists(candidate1):
        return candidate1

    filename = os.path.basename(content)
    candidate_photo = os.path.join(PHOTOS_DIR, filename)
    if os.path.exists(candidate_photo):
        return candidate_photo

    candidate_doc = os.path.join(DOCUMENTS_DIR, filename)
    if os.path.exists(candidate_doc):
        return candidate_doc

    return ""
