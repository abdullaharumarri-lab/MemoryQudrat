from telegram import Update, InlineKeyboardMarkup
from telegram.ext import ContextTypes
import database as db

async def send_clean_message(context: ContextTypes.DEFAULT_TYPE, chat_id: int, text: str, update: Update = None, reply_markup: InlineKeyboardMarkup = None, parse_mode: str = "Markdown") -> int:
    """
    Sends a message while deleting the previous bot message to keep the chat clean.
    If update.message is present (user sent a command/text), it also deletes the user's message.
    """
    # 1. Delete user's message if it exists (skip for channel posts — can't delete)
    if update and update.effective_message and update.message:
        try:
            await update.message.delete()
        except Exception:
            pass


    # 2. Delete the last bot message
    last_msg_id = db.get_last_message_id(chat_id)
    if last_msg_id:
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=last_msg_id)
        except Exception:
            pass

    # 3. Send new message
    msg = await context.bot.send_message(
        chat_id=chat_id,
        text=text,
        reply_markup=reply_markup,
        parse_mode=parse_mode
    )

    # 4. Save new message ID
    db.set_last_message_id(chat_id, msg.message_id)
    return msg.message_id
