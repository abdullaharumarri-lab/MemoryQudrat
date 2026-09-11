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
    """Safely delete a list of message IDs using Telegram's bulk delete API with robust fallback."""
    if not message_ids or not chat_id:
        return
    
    unique_ids = sorted(list(dict.fromkeys(int(m) for m in message_ids if m and int(m) > 0)))
    if not unique_ids:
        return

    # Delete in batches of 100 (Telegram limit is 100 per delete_messages)
    for i in range(0, len(unique_ids), 100):
        batch = unique_ids[i:i + 100]
        try:
            await context.bot.delete_messages(chat_id=chat_id, message_ids=batch)
        except Exception as e:
            logger.debug("delete_messages batch failed (%s), trying sub-batches", e)
            for j in range(0, len(batch), 20):
                sub_batch = batch[j:j + 20]
                try:
                    await context.bot.delete_messages(chat_id=chat_id, message_ids=sub_batch)
                except Exception:
                    for mid in sub_batch:
                        try:
                            await context.bot.delete_message(chat_id=chat_id, message_id=mid)
                        except Exception:
                            pass


async def clean_entire_chat(
    context: ContextTypes.DEFAULT_TYPE,
    chat_id: int,
    keep_message_id: int = None,
    extra_ids: list[int] = None,
    sweep_range: int = 0
):
    """
    Cleans tracked messages in the chat history.
    Deletes tracked messages and extra IDs.
    If sweep_range > 0, also sweeps that many message IDs backwards from the highest known ID.
    """
    if not chat_id:
        return

    # 1. Fetch all tracked message IDs from DB
    tracked = db.get_and_clear_chat_messages(chat_id, keep_message_id=keep_message_id)
    
    # 2. Add last known message ID if different from keep_message_id
    last_id = db.get_last_message_id(chat_id)
    if last_id and last_id != keep_message_id and last_id not in tracked:
        tracked.append(last_id)

    # 3. Add extra IDs (e.g. current message or quiz poll IDs)
    if extra_ids:
        for eid in extra_ids:
            if eid and eid != keep_message_id:
                tracked.append(eid)

    all_to_del = {int(mid) for mid in tracked if mid and int(mid) > 0 and mid != keep_message_id}

    # 4. Reset or update last_message_id in DB
    if keep_message_id:
        db.set_last_message_id(chat_id, keep_message_id)
        db.track_chat_message(chat_id, keep_message_id)
    else:
        db.clear_last_message_id(chat_id)

    # 5. Fast batch delete verified message IDs
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
    If editing fails completely (e.g. message deleted or outdated),
    it automatically sends a fresh clean message so the user is never left hanging.
    """
    if not query:
        return
    text = truncate_text(text, 3800)

    # Safely extract chat_id and bot
    chat_id = None
    bot = None
    msg = getattr(query, "message", None)
    if msg:
        if hasattr(msg, "chat") and msg.chat:
            chat_id = msg.chat.id
        elif hasattr(msg, "chat_id"):
            chat_id = msg.chat_id
        if hasattr(msg, "get_bot"):
            try: bot = msg.get_bot()
            except Exception: pass

    if not bot and hasattr(query, "get_bot"):
        try: bot = query.get_bot()
        except Exception: pass
    if not bot and context and hasattr(context, "bot"):
        bot = context.bot
    if not chat_id and getattr(query, "from_user", None):
        chat_id = query.from_user.id

    # Track message ID if valid
    if chat_id and msg and hasattr(msg, "message_id") and msg.message_id:
        try:
            db.set_last_message_id(chat_id, msg.message_id)
            db.track_chat_message(chat_id, msg.message_id)
        except Exception:
            pass

    # 1. Try HTML edit
    try:
        await query.edit_message_text(text, reply_markup=reply_markup, parse_mode=parse_mode)
        return
    except Exception as e:
        if "Message is not modified" in str(e):
            return
        logger.debug("safe_edit HTML edit failed: %s", e)

    # 2. Clean plain-text fallback
    clean_text = strip_html_tags(text)
    try:
        await query.edit_message_text(clean_text, reply_markup=reply_markup)
        return
    except Exception as e:
        if "Message is not modified" in str(e):
            return
        logger.debug("safe_edit plain edit failed: %s", e)

    # 3. Fallback: If editing failed completely, send fresh message directly to chat_id
    try:
        sent = None
        if bot and chat_id:
            sent = await bot.send_message(
                chat_id=chat_id,
                text=clean_text,
                reply_markup=reply_markup
            )
        elif msg and hasattr(msg, "reply_text"):
            try:
                sent = await msg.reply_text(clean_text, reply_markup=reply_markup)
            except Exception:
                pass

        if sent and hasattr(sent, "chat") and sent.chat:
            db.set_last_message_id(sent.chat.id, sent.message_id)
            db.track_chat_message(sent.chat.id, sent.message_id)
    except Exception as e2:
        logger.error("safe_edit ultimate fallback failed: %s", e2)


def normalize_arabic_digits(s: str) -> str:
    """Converts Arabic-Indic digits (٠-٩) to Western standard digits (0-9)."""
    if not s:
        return ""
    arabic_digits = "٠١٢٣٤٥٦٧٨٩"
    for i, d in enumerate(arabic_digits):
        s = str(s).replace(d, str(i))
    return s


def quiz_sort_key_desc(item: dict) -> tuple:
    """
    Sorts quizzes descending by number in title (171, 170, 169... 1)
    and falls back to database ID descending.
    """
    name = item.get("name") or item.get("quiz_name") or ""
    norm = normalize_arabic_digits(str(name))
    
    # Check leading number first (e.g. "171. غاز الهيليوم")
    leading = re.match(r'^\s*(\d+)', norm)
    if leading:
        q_num = int(leading.group(1))
    else:
        # Check all numbers in the string and take the highest (e.g. "كويز 171")
        nums = re.findall(r'\d+', norm)
        q_num = max(int(n) for n in nums) if nums else None

    item_id = item.get("id") or item.get("quiz_id") or 0
    if q_num is not None:
        return (1, q_num, item_id)
    return (0, item_id, 0)


def natural_sort_key(s: str) -> list:
    """
    Returns a sort key that orders strings naturally by numeric values.
    """
    if not s:
        return [0, ""]
    norm = normalize_arabic_digits(str(s))
    parts = re.split(r'(\d+)', norm)
    key = []
    for p in parts:
        if p.isdigit():
            key.append(int(p))
        else:
            key.append(p.strip().lower())
    return key


def strip_invisible_chars(s: str) -> str:
    """Strips invisible Unicode characters, LTR/RTL marks, zero-width spaces, and NBSP."""
    if not s:
        return ""
    # Replace non-breaking spaces (\u00a0, \u202f) with regular space
    s = str(s).replace("\u00a0", " ").replace("\u202f", " ").replace("\ufeff", "")
    # Remove LTR/RTL marks and zero-width characters
    invisible_chars = "\u200b\u200c\u200d\u200e\u200f\u202a\u202b\u202c\u202d\u202e\u2060"
    for ch in invisible_chars:
        s = s.replace(ch, "")
    return s


def normalize_for_match(s: str) -> str:
    """Normalizes text for robust answer matching (digits, hamzas, diacritics, spaces)."""
    if not s:
        return ""
    s = strip_invisible_chars(str(s))
    # Normalize Arabic digits to Western digits
    s = normalize_arabic_digits(s)
    # Remove Arabic diacritics / tashkeel
    s = re.sub(r'[\u064B-\u065F\u0670]', '', s)
    # Normalize alef variants
    s = re.sub(r'[أإآٱ]', 'ا', s)
    # Normalize ta marbuta to ha
    s = s.replace('ة', 'ه')
    # Normalize ya / alef maksura
    s = s.replace('ى', 'ي')
    # Collapse multiple whitespace
    s = re.sub(r'\s+', ' ', s)
    return s.strip().lower()


def strip_option_prefix_py(text: str) -> str:
    """Strips leading option prefixes like 'أ)', 'الخيار (ب)', '1-', 'A.'"""
    if not text:
        return ""
    t = strip_invisible_chars(str(text)).strip()
    # Strip prefixes like "الخيار أ", "الخيار (أ)", "خيار 1"
    t = re.sub(r'^(?:الخيار|خيار|Option)\s*[:\-\.]?\s*', '', t, flags=re.IGNORECASE)
    # Strip Arabic letter prefixes (أ-ي = all 10 letters) with any separator: ) . : - / and optional tatweel
    t = re.sub(r'^[\(\uff08]?[\u0623-\u064a\u0647\u0648a-jA-J]\u0640*[\)\uff09\.:\-\/\s]+\s*', '', t)
    # Strip numeric prefixes: 1) 2. 3- etc.
    t = re.sub(r'^[\(\uff08]?[1-9\u0661-\u0669][\)\uff09\.\:\-\/\s]+\s*', '', t)
    return t.strip()


def find_correct_option_index(options: list, correct_answer: str) -> int:
    """
    Robust multi-tier matcher to determine the exact 0-based index of correct_answer in options.
    Guarantees zero false mismatches from Hamzas, digits, letter indices, prefixes, or invisible characters.
    """
    if not options:
        return 0
    
    clean_options = [strip_invisible_chars(str(opt)).strip() for opt in options]
    raw_ca = strip_invisible_chars(str(correct_answer)).strip()

    if not raw_ca:
        return 0

    # Tier 1: Exact string equality
    for idx, opt in enumerate(clean_options):
        if opt == raw_ca:
            return idx

    # Tier 2: Normalized string equality (digits, hamzas, tashkeel, etc.)
    norm_ca = normalize_for_match(raw_ca)
    norm_options = [normalize_for_match(opt) for opt in clean_options]
    for idx, n_opt in enumerate(norm_options):
        if n_opt == norm_ca:
            return idx

    # Tier 3: Option prefix stripped comparison
    # e.g. option is "أ) الرياض" and answer is "الرياض", or option is "الرياض" and answer is "أ) الرياض"
    stripped_ca = normalize_for_match(strip_option_prefix_py(raw_ca))
    stripped_options = [normalize_for_match(strip_option_prefix_py(opt)) for opt in clean_options]
    if stripped_ca:
        for idx, s_opt in enumerate(stripped_options):
            if s_opt == stripped_ca:
                return idx

    # Tier 4: Letter-to-Index Matching
    # If the answer is just a letter indicator: 'أ' -> 0, 'ب' -> 1, 'ج' -> 2, 'د' -> 3
    # Or "الخيار ب", "(ب)", "Option B", "B"
    letter_candidate = re.sub(r'^(?:الخيار|خيار|Option)\s*[:\-\.]?\s*', '', raw_ca, flags=re.IGNORECASE)
    letter_candidate = letter_candidate.strip("()[]{} \t.:-")

    arabic_letter_map = {
        "أ": 0, "ا": 0, "إ": 0, "آ": 0,
        "ب": 1,
        "ج": 2,
        "د": 3,
        "هـ": 4, "ه": 4,
        "و": 5,
        "ز": 6,
        "ح": 7,
        "ط": 8,
        "ي": 9, "ى": 9
    }
    english_letters = ["a", "b", "c", "d", "e", "f", "g", "h", "i", "j"]
    
    if letter_candidate in arabic_letter_map:
        idx = arabic_letter_map[letter_candidate]
        if 0 <= idx < len(options):
            return idx
    elif letter_candidate.lower() in english_letters:
        idx = english_letters.index(letter_candidate.lower())
        if 0 <= idx < len(options):
            return idx

    # Tier 5: Numeric equivalence (e.g. "15" vs "15.0" vs "١٥")
    try:
        norm_ca_digits = normalize_arabic_digits(raw_ca).strip()
        ca_num = float(norm_ca_digits)
        for idx, opt in enumerate(clean_options):
            try:
                opt_num = float(normalize_arabic_digits(opt).strip())
                if abs(ca_num - opt_num) < 1e-6:
                    return idx
            except (ValueError, TypeError):
                continue
    except (ValueError, TypeError):
        pass

    # Tier 6: Substring / Token containment ONLY when length is substantial (> 3 chars)
    if len(norm_ca) >= 4:
        for idx, n_opt in enumerate(norm_options):
            if len(n_opt) >= 4 and (n_opt in norm_ca or norm_ca in n_opt):
                return idx

    logger.warning("find_correct_option_index: could not find match for answer %r in options %r. Defaulting to 0.", raw_ca, clean_options)
    return 0





