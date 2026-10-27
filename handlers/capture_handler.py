import re
import html
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

import database as db
from config import is_authorized
from utils import (
    safe_edit,
    send_clean_message,
    download_telegram_photo,
    download_telegram_document,
)

logger = logging.getLogger(__name__)


# ─── Photo Capture Handler ───────────────────────────────────────────────────

async def handle_photo_capture(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles incoming photos (questions, formula sheets, notes)."""
    user = update.effective_user
    if not user or not is_authorized(user.id):
        return

    chat_id = update.effective_chat.id
    message = update.message
    if not message or not message.photo:
        return

    # Download highest resolution photo
    photo_obj = message.photo[-1]
    rel_path = await download_telegram_photo(context.bot, photo_obj)

    caption = (message.caption or "").strip()
    active_folder_id = db.get_active_folder_id()

    # Smart auto-detection via hashtag
    detected_mode = None
    clean_caption = caption

    if any(tag in caption for tag in ["#ملخص", "#قانون", "#مراجعة", "#قواعد", "#حفظ"]):
        detected_mode = "direct"
        clean_caption = re.sub(r'#(?:ملخص|قانون|مراجعة|قواعد|حفظ)\b', '', clean_caption).strip()
    elif any(tag in caption for tag in ["#سؤال", "#مسألة", "#تجميع", "#تدريب"]):
        detected_mode = "qa"
        clean_caption = re.sub(r'#(?:سؤال|مسألة|تجميع|تدريب)\b', '', clean_caption).strip()

    # If no folders exist yet, prompt to create first folder immediately
    if active_folder_id is None:
        context.user_data["pending_capture"] = {
            "item_type": "photo",
            "content": rel_path,
            "title": clean_caption[:40] if clean_caption else "مسألة مصورة",
            "solution": clean_caption,
            "review_mode": detected_mode or "qa",
        }
        context.user_data["input_state"] = "awaiting_folder_name"
        context.user_data["folder_parent_id"] = None
        prompt = (
            "📸 <b>تم استلام الصورة بنجاح!</b>\n\n"
            "⚠️ <i>لا يوجد لديك أي مجلد حالياً لحفظ وترتيب مسائلك.</i>\n\n"
            "👇 <b>أرسل اسم أول مجلد في رسالة نصية الآن لحفظ الصورة بداخله فوراً:</b>\n"
            "<i>(مثال: قدرات كمي، أو تجميعات 1447)</i>"
        )
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="main_menu")]])
        sent_id = await send_clean_message(context, chat_id, prompt, update=update, reply_markup=kb)
        context.user_data["prompt_msg_id"] = sent_id
        return

    active_folder = db.get_folder(active_folder_id)
    folder_name = active_folder["name"] if active_folder else "عام"

    # If auto-detected via hashtag, save immediately with 0 extra clicks!
    if detected_mode:
        item_id = db.add_item(
            item_type="photo",
            content=rel_path,
            title=clean_caption[:40] if clean_caption else "مسألة مصورة",
            solution=clean_caption if clean_caption and detected_mode == "qa" else "",
            folder_id=active_folder_id,
            review_mode=detected_mode,
        )
        mode_label = "📖 مراجعة مباشرة" if detected_mode == "direct" else "❓ مسألة تفاعلية"
        ack = (
            f"✅ <b>تم الحفظ والجدولة بنجاح! 🧠</b>\n\n"
            f"📁 <b>المجلد:</b> {html.escape(folder_name)}\n"
            f"🏷️ <b>النمط:</b> {mode_label}\n"
            f"📅 <b>أول مراجعة:</b> غداً (المرحلة 1)"
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("📁 تغيير المجلد", callback_data=f"item_change_folder_{item_id}")],
            [InlineKeyboardButton("🏠 الرئيسية", callback_data="main_menu")],
        ])
        await send_clean_message(context, chat_id, ack, update=update, reply_markup=kb)
        return

    # If no hashtag, prompt with a fast 1-click confirmation
    context.user_data["pending_capture"] = {
        "item_type": "photo",
        "content": rel_path,
        "title": clean_caption[:40] if clean_caption else "مسألة مصورة",
        "solution": clean_caption,
        "folder_id": active_folder_id,
    }

    prompt = (
        f"📸 <b>تم استلام الصورة بنجاح!</b>\n"
        f"📁 ستُحفظ في: <b>{html.escape(folder_name)}</b>\n\n"
        f"👇 <b>حدد نمط مراجعة هذه الصورة بنقرة واحدة:</b>"
    )
    kb = InlineKeyboardMarkup([
        [
            InlineKeyboardButton("❓ مسألة (إخفاء الحل أولاً)", callback_data="confirm_cap_mode_qa"),
            InlineKeyboardButton("📖 مراجعة مباشرة (تثبيت)", callback_data="confirm_cap_mode_direct"),
        ],
        [InlineKeyboardButton("📁 حفظ في مجلد آخر", callback_data="capture_pick_other_folder")],
    ])
    await send_clean_message(context, chat_id, prompt, update=update, reply_markup=kb)


# ─── Text & URL Capture Handler ──────────────────────────────────────────────

async def handle_text_and_url_capture(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles text messages (folder inputs, questions, notes, formulas, or forms URLs)."""
    user = update.effective_user
    if not user or not is_authorized(user.id):
        return

    chat_id = update.effective_chat.id
    raw_text = (update.message.text or "").strip()
    if not raw_text:
        return

    # Check if waiting for specific state input
    state = context.user_data.get("input_state")

    # 1. State: Awaiting folder name (New Root or Subfolder)
    if state == "awaiting_folder_name":
        parent_id = context.user_data.pop("folder_parent_id", None)
        prompt_msg_id = context.user_data.pop("prompt_msg_id", None)
        context.user_data.pop("input_state", None)

        if update.message:
            try:
                await update.message.delete()
            except Exception:
                pass

        new_fid = db.create_folder(raw_text, icon="📁", parent_id=parent_id)
        db.set_active_folder_id(new_fid)

        # If a pending capture was waiting for folder creation:
        pending = context.user_data.pop("pending_capture", None)
        if pending:
            pending["folder_id"] = new_fid
            mode = pending.get("review_mode", "qa")
            item_id = db.add_item(
                item_type=pending.get("item_type", "photo"),
                content=pending.get("content", ""),
                title=pending.get("title", ""),
                solution=pending.get("solution", ""),
                folder_id=new_fid,
                review_mode=mode,
            )
            mode_lbl = "📖 مراجعة مباشرة" if mode == "direct" else "❓ مسألة تفاعلية"
            ack = (
                f"✅ <b>تم إنشاء المجلد وحفظ المادة بنجاح! 🧠</b>\n\n"
                f"📁 <b>المجلد:</b> {html.escape(raw_text)}\n"
                f"🏷️ <b>النمط:</b> {mode_lbl}\n"
                f"📅 <b>أول مراجعة:</b> غداً (المرحلة 1)"
            )
            kb = InlineKeyboardMarkup([
                [InlineKeyboardButton("📁 استعراض المجلد", callback_data=f"folder_view_{new_fid}")],
                [InlineKeyboardButton("🏠 الرئيسية", callback_data="main_menu")],
            ])
            if prompt_msg_id:
                try:
                    await context.bot.edit_message_text(chat_id=chat_id, message_id=prompt_msg_id, text=ack, reply_markup=kb, parse_mode="HTML")
                    return
                except Exception:
                    pass
            await send_clean_message(context, chat_id, ack, reply_markup=kb)
            return

        from handlers.folders_handler import build_folder_detail_view
        text, kb = build_folder_detail_view(new_fid)
        if prompt_msg_id:
            try:
                await context.bot.edit_message_text(chat_id=chat_id, message_id=prompt_msg_id, text=text, reply_markup=kb, parse_mode="HTML")
                return
            except Exception:
                pass
        await send_clean_message(context, chat_id, text, reply_markup=kb)
        return

    # 2. State: Awaiting folder rename
    if state == "awaiting_folder_rename":
        target_fid = context.user_data.pop("rename_folder_id", None)
        prompt_msg_id = context.user_data.pop("prompt_msg_id", None)
        context.user_data.pop("input_state", None)

        if update.message:
            try:
                await update.message.delete()
            except Exception:
                pass

        if target_fid:
            db.rename_folder(target_fid, raw_text)
            from handlers.folders_handler import build_folder_detail_view
            text, kb = build_folder_detail_view(target_fid)
            if prompt_msg_id:
                try:
                    await context.bot.edit_message_text(chat_id=chat_id, message_id=prompt_msg_id, text=text, reply_markup=kb, parse_mode="HTML")
                    return
                except Exception:
                    pass
            await send_clean_message(context, chat_id, text, reply_markup=kb)
            return

    active_folder_id = db.get_active_folder_id()

    # If no folders exist yet, prompt to create first folder
    if active_folder_id is None:
        context.user_data["pending_capture"] = {
            "item_type": "text",
            "content": raw_text,
            "title": raw_text[:40],
            "solution": "",
            "review_mode": "qa",
        }
        context.user_data["input_state"] = "awaiting_folder_name"
        context.user_data["folder_parent_id"] = None
        prompt = (
            "✍️ <b>تم استلام الملاحظة بنجاح!</b>\n\n"
            "⚠️ <i>لا يوجد لديك أي مجلد حالياً لحفظ مسائلك.</i>\n\n"
            "👇 <b>أرسل اسم أول مجلد في رسالة نصية الآن لإنشائه وحفظ هذه المسألة بداخله:</b>"
        )
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="main_menu")]])
        sent_id = await send_clean_message(context, chat_id, prompt, update=update, reply_markup=kb)
        context.user_data["prompt_msg_id"] = sent_id
        return

    active_folder = db.get_folder(active_folder_id)
    folder_name = active_folder["name"] if active_folder else "عام"

    # 1. URL Capture (Google Forms, Quiz URLs, Platforms)
    url_match = re.search(r'https?://[^\s]+', raw_text)
    if url_match:
        url = url_match.group(0)
        title_text = raw_text.replace(url, "").strip() or "رابط كويز / تدريب"
        item_id = db.add_item(
            item_type="link",
            content=url,
            title=title_text[:40],
            solution="",
            folder_id=active_folder_id,
            review_mode="link",
        )
        ack = (
            f"🔗 <b>تم حفظ رابط الاختبار وجدولته بنجاح! 🧠</b>\n\n"
            f"📝 <b>العنوان:</b> {html.escape(title_text[:40])}\n"
            f"📁 <b>المجلد:</b> {html.escape(folder_name)}\n"
            f"📅 <b>أول مراجعة:</b> غداً (المرحلة 1)"
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("📁 تغيير المجلد", callback_data=f"item_change_folder_{item_id}")],
            [InlineKeyboardButton("🏠 الرئيسية", callback_data="main_menu")],
        ])
        await send_clean_message(context, chat_id, ack, update=update, reply_markup=kb)
        return

    # 2. Text Question or Note Capture
    is_direct_note = any(tag in raw_text for tag in ["#ملخص", "#قانون", "#ملاحظة", "#قاعدة"])
    review_mode = "direct" if is_direct_note else "qa"
    mode_lbl = "📖 مراجعة مباشرة" if is_direct_note else "❓ مسألة / سؤال"

    lines = [l.strip() for l in raw_text.splitlines() if l.strip()]
    first_line = lines[0] if lines else "ملاحظة نصية"

    item_id = db.add_item(
        item_type="text",
        content=raw_text,
        title=first_line[:40],
        solution="",
        folder_id=active_folder_id,
        review_mode=review_mode,
    )

    ack = (
        f"✍️ <b>تم حفظ المسألة النصية وجدولتها بنجاح! 🧠</b>\n\n"
        f"📝 <b>العنوان:</b> {html.escape(first_line[:40])}\n"
        f"📁 <b>المجلد:</b> {html.escape(folder_name)}\n"
        f"🏷️ <b>النمط:</b> {mode_lbl}\n"
        f"📅 <b>أول مراجعة:</b> غداً (المرحلة 1)"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📁 تغيير المجلد", callback_data=f"item_change_folder_{item_id}")],
        [InlineKeyboardButton("🏠 الرئيسية", callback_data="main_menu")],
    ])
    await send_clean_message(context, chat_id, ack, update=update, reply_markup=kb)


# ─── Document / PDF Capture Handler ──────────────────────────────────────────

async def handle_document_capture(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handles incoming PDF files, summaries, and documents."""
    user = update.effective_user
    if not user or not is_authorized(user.id):
        return

    chat_id = update.effective_chat.id
    message = update.message
    if not message or not message.document:
        return

    doc = message.document
    rel_path, orig_name = await download_telegram_document(context.bot, doc)

    active_folder_id = db.get_active_folder_id()
    if active_folder_id is None:
        context.user_data["pending_capture"] = {
            "item_type": "pdf",
            "content": rel_path,
            "title": orig_name[:40],
            "solution": "",
            "review_mode": "direct",
        }
        context.user_data["input_state"] = "awaiting_folder_name"
        context.user_data["folder_parent_id"] = None
        prompt = (
            "📑 <b>تم استلام المستند بنجاح!</b>\n\n"
            "⚠️ <i>لا يوجد لديك أي مجلد حالياً لحفظ مسائلك.</i>\n\n"
            "👇 <b>أرسل اسم أول مجلد في رسالة نصية الآن لحفظ هذا الملف بداخله:</b>"
        )
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="main_menu")]])
        sent_id = await send_clean_message(context, chat_id, prompt, update=update, reply_markup=kb)
        context.user_data["prompt_msg_id"] = sent_id
        return

    active_folder = db.get_folder(active_folder_id)
    folder_name = active_folder["name"] if active_folder else "عام"

    item_id = db.add_item(
        item_type="pdf",
        content=rel_path,
        title=orig_name[:40],
        solution="",
        folder_id=active_folder_id,
        review_mode="direct",
    )

    ack = (
        f"📑 <b>تم حفظ ملف المستند وجدولته بنجاح! 🧠</b>\n\n"
        f"📄 <b>الملف:</b> {html.escape(orig_name)}\n"
        f"📁 <b>المجلد:</b> {html.escape(folder_name)}\n"
        f"📅 <b>أول مراجعة:</b> غداً (المرحلة 1)"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("📁 تغيير المجلد", callback_data=f"item_change_folder_{item_id}")],
        [InlineKeyboardButton("🏠 الرئيسية", callback_data="main_menu")],
    ])
    await send_clean_message(context, chat_id, ack, update=update, reply_markup=kb)


# ─── Capture Callbacks (Mode Confirmations & Folder Picking) ─────────────────

async def handle_capture_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    query = update.callback_query
    if not query:
        return False
    data = query.data

    # Mode confirmation for photo capture
    if data in ("confirm_cap_mode_qa", "confirm_cap_mode_direct"):
        pending = context.user_data.pop("pending_capture", None)
        if not pending:
            await query.answer("انتهت صلاحية الطلب أو تم الحفظ مسبقاً.")
            return True

        review_mode = "direct" if data == "confirm_cap_mode_direct" else "qa"
        mode_label = "📖 مراجعة مباشرة (تثبيت)" if review_mode == "direct" else "❓ مسألة تفاعلية (كشف الحل)"

        item_id = db.add_item(
            item_type=pending["item_type"],
            content=pending["content"],
            title=pending["title"],
            solution=pending.get("solution", ""),
            folder_id=pending["folder_id"],
            review_mode=review_mode,
        )

        active_folder = db.get_folder(pending["folder_id"])
        folder_name = active_folder["name"] if active_folder else "عام"

        ack = (
            f"✅ <b>تم الحفظ والجدولة بنجاح! 🧠</b>\n\n"
            f"📁 <b>المجلد:</b> {html.escape(folder_name)}\n"
            f"🏷️ <b>النمط:</b> {mode_label}\n"
            f"📅 <b>أول مراجعة:</b> غداً (المرحلة 1)"
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("📁 تغيير المجلد", callback_data=f"item_change_folder_{item_id}")],
            [InlineKeyboardButton("🏠 الرئيسية", callback_data="main_menu")],
        ])
        await safe_edit(query, ack, kb)
        return True

    # Pick other folder during capture
    if data == "capture_pick_other_folder":
        pending = context.user_data.get("pending_capture")
        if not pending:
            await query.answer("انتهت صلاحية الطلب.")
            return True
        all_folders = db.get_all_folders()
        kb = []
        for f in all_folders:
            path_str = db.get_folder_path_string(f["id"]) or f"{f['icon']} {f['name']}"
            kb.append([InlineKeyboardButton(path_str, callback_data=f"set_cap_folder_{f['id']}")])
        kb.append([InlineKeyboardButton("❌ إلغاء", callback_data="main_menu")])
        await safe_edit(query, "📁 <b>اختر المجلد لحفظ هذه المسألة:</b>", InlineKeyboardMarkup(kb))
        return True

    if data.startswith("set_cap_folder_"):
        new_fid = int(data.split("_")[-1])
        pending = context.user_data.get("pending_capture")
        if pending:
            pending["folder_id"] = new_fid
            db.set_active_folder_id(new_fid)
            f = db.get_folder(new_fid)
            f_name = f["name"] if f else ""
            prompt = (
                f"📸 <b>تم تحديد المجلد: {html.escape(f_name)}</b>\n\n"
                f"👇 <b>حدد نمط مراجعة هذه الصورة بنقرة واحدة:</b>"
            )
            kb = InlineKeyboardMarkup([
                [
                    InlineKeyboardButton("❓ مسألة (إخفاء الحل أولاً)", callback_data="confirm_cap_mode_qa"),
                    InlineKeyboardButton("📖 مراجعة مباشرة (تثبيت)", callback_data="confirm_cap_mode_direct"),
                ]
            ])
            await safe_edit(query, prompt, kb)
        return True

    # Folder changing after saving an item
    if data.startswith("item_change_folder_"):
        item_id = int(data.split("_")[-1])
        all_folders = db.get_all_folders()
        kb = []
        for f in all_folders:
            path_str = db.get_folder_path_string(f["id"]) or f"{f['icon']} {f['name']}"
            kb.append([InlineKeyboardButton(path_str, callback_data=f"do_move_item_{item_id}_{f['id']}")])
        kb.append([InlineKeyboardButton("🔙 إلغاء", callback_data="main_menu")])
        await safe_edit(query, "📁 <b>اختر المجلد الذي تريد نقل المسألة إليه:</b>", InlineKeyboardMarkup(kb))
        return True

    if data.startswith("do_move_item_"):
        parts = data.split("_")
        item_id = int(parts[3])
        new_fid = int(parts[4])
        db.update_item_folder(item_id, new_fid)
        folder = db.get_folder(new_fid)
        f_name = folder["name"] if folder else "المجلد"
        await query.answer(f"✅ تم نقل المسألة إلى {f_name} بنجاح!")
        from handlers.main_menu import build_main_dashboard
        text, kb = build_main_dashboard()
        await safe_edit(query, f"✅ تم النقل إلى <b>{html.escape(f_name)}</b> بنجاح!\n\n" + text, kb)
        return True

    return False
