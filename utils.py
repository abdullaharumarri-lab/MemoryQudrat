import logging
from telegram import Update
from telegram.ext import ContextTypes
import database as db

logger = logging.getLogger(__name__)

async def send_clean_message(context: ContextTypes.DEFAULT_TYPE, chat_id: int, text: str, update: Update = None, reply_markup=None, parse_mode="HTML") -> int:
    """
    Sends a message and deletes the previous bot message (and user command message if possible).
    Returns the message_id of the newly sent message.
    """
    # 1. Delete user command message if present (e.g. /start or /menu)
    if update:
        msg_to_del = update.message or update.effective_message
        if msg_to_del and not getattr(update, "callback_query", None):
            try:
                await msg_to_del.delete()
            except Exception as e:
                logger.debug("Could not delete user message: %s", e)

    # 2. Delete previous bot message
    last_msg_id = db.get_last_message_id(chat_id)
    if last_msg_id:
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=last_msg_id)
        except Exception as e:
            logger.debug("Could not delete previous bot message: %s", e)

    # 3. Send new message with fallback
    try:
        new_msg = await context.bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=reply_markup,
            parse_mode=parse_mode,
        )
    except Exception as e:
        logger.warning("send_clean_message HTML send failed: %s, falling back to plain text", e)
        new_msg = await context.bot.send_message(
            chat_id=chat_id,
            text=text,
            reply_markup=reply_markup,
        )

    db.set_last_message_id(chat_id, new_msg.message_id)
    return new_msg.message_id

