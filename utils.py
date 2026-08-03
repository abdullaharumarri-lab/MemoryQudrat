from telegram import Update
from telegram.ext import ContextTypes
import database as db

async def send_clean_message(context: ContextTypes.DEFAULT_TYPE, chat_id: int, text: str, update: Update = None, reply_markup=None, parse_mode="HTML") -> int:
    """
    Sends a message and deletes the previous bot message (and user command message if possible).
    Returns the message_id of the newly sent message.
    """
    if update and update.effective_message and update.message:
        try:
            await update.message.delete()
        except Exception:
            pass

    last_msg_id = db.get_last_message_id(chat_id)
    if last_msg_id:
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=last_msg_id)
        except Exception:
            pass

    new_msg = await context.bot.send_message(
        chat_id=chat_id,
        text=text,
        reply_markup=reply_markup,
        parse_mode=parse_mode,
    )
    db.set_last_message_id(chat_id, new_msg.message_id)
    return new_msg.message_id
