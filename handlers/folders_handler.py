import os
import html
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

import database as db
from spaced_repetition import stage_label
from utils import safe_edit, resolve_media_path

logger = logging.getLogger(__name__)


# ─── Folders View Builders ───────────────────────────────────────────────────

def build_folders_menu() -> tuple[str, InlineKeyboardMarkup]:
    """Builds root folders listing view with item and subfolder indicators."""
    root_folders = db.get_folders(parent_id=None)
    stats = db.get_dashboard_stats()
    breakdown_map = {f["id"]: f for f in stats["folders_breakdown"]}
    active_fid = db.get_active_folder_id()

    if not root_folders:
        text = (
            "📁 <b>مجلداتي وأرشيف المذاكرة</b>\n\n"
            "لا توجد مجلدات بعد.\n"
            "أنشئ مجلدك الأول لتنظيم وحفظ مسائل وملاحظات القدرات بسهولة:"
        )
        kb = [
            [InlineKeyboardButton("➕ إنشاء أول مجلد", callback_data="folder_add_prompt_root")],
            [InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="main_menu")],
        ]
        return text, InlineKeyboardMarkup(kb)

    text_lines = [
        "📁 <b>مجلداتي وأرشيف المذاكرة</b>\n",
        "اختر مجلداً لاستعراض مسائله أو مجلداته الفرعية:\n",
    ]

    kb = []
    for f in root_folders:
        fid = f["id"]
        f_stat = breakdown_map.get(fid, {})
        total = f_stat.get("total_items", 0)
        due = f_stat.get("due_items", 0)
        sub_count = f_stat.get("subfolders_count", 0)

        active_badge = " 🎯" if fid == active_fid else ""
        due_badge = f" | 🔴 {due} مستحق" if due > 0 else ""
        sub_badge = f" [📂 {sub_count}]" if sub_count > 0 else ""

        label = f"{f['icon']} {f['name']} ({total}{due_badge}){sub_badge}{active_badge}"
        kb.append([InlineKeyboardButton(label, callback_data=f"folder_view_{fid}")])

    kb.append([InlineKeyboardButton("➕ إنشاء مجلد رئيسي جديد", callback_data="folder_add_prompt_root")])
    kb.append([InlineKeyboardButton("🏠 القائمة الرئيسية", callback_data="main_menu")])

    return "\n".join(text_lines), InlineKeyboardMarkup(kb)


def build_folder_detail_view(folder_id: int) -> tuple[str, InlineKeyboardMarkup]:
    """Builds the single folder control and action view with nested subfolder navigation."""
    folder = db.get_folder(folder_id)
    if not folder:
        return "المجلد غير موجود.", InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="folders_menu")]])

    path_str = db.get_folder_path_string(folder_id)
    subfolders = db.get_subfolders(folder_id)
    direct_items = db.get_items_by_folder(folder_id, include_subfolders=False)
    all_items = db.get_items_by_folder(folder_id, include_subfolders=True)
    due_items = db.get_due_items(folder_id, include_subfolders=True)

    active_fid = db.get_active_folder_id()
    is_active = (folder_id == active_fid)
    active_desc = "نشط ✅ (تُحفظ فيه المواد تلقائياً)" if is_active else "غير نشط"

    text = (
        f"📁 <b>مجلد: {folder['icon']} {html.escape(folder['name'])}</b>\n"
        f"📍 <b>المسار:</b> {html.escape(path_str)}\n\n"
        f"📊 <b>المواد المباشرة:</b> <b>{len(direct_items)}</b> مادة\n"
        f"📂 <b>المجلدات الفرعية:</b> <b>{len(subfolders)}</b> مجلد (إجمالي <b>{len(all_items)}</b> مادة)\n"
        f"🔔 <b>المستحق للمراجعة اليوم:</b> <b>{len(due_items)}</b> مادة\n"
        f"📌 <b>حالة الإيداع:</b> {active_desc}\n\n"
        f"اختر الإجراء المطلوب:"
    )

    kb = []

    # 1. Render Subfolders (if any)
    if subfolders:
        sub_buttons = []
        for sf in subfolders:
            sub_id = sf["id"]
            sf_items = db.get_items_by_folder(sub_id, include_subfolders=True)
            sub_buttons.append(InlineKeyboardButton(f"📂 {sf['name']} ({len(sf_items)})", callback_data=f"folder_view_{sub_id}"))
        for i in range(0, len(sub_buttons), 2):
            kb.append(sub_buttons[i:i + 2])

    # 2. Add Subfolder inside this folder
    kb.append([InlineKeyboardButton("➕ إضافة مجلد فرعي هنا", callback_data=f"folder_add_prompt_sub_{folder_id}")])

    # 3. Actions on this folder
    if len(due_items) > 0:
        kb.append([InlineKeyboardButton(f"▶️ ابدأ مراجعة المستحق ({len(due_items)})", callback_data=f"folder_start_review_{folder_id}")])

    if len(all_items) > 0:
        kb.append([InlineKeyboardButton(f"📋 استعراض المسائل المحفوظة ({len(all_items)})", callback_data=f"folder_browse_{folder_id}_0")])

    if not is_active:
        kb.append([InlineKeyboardButton("🎯 تعيين كمجلد نشط للإيداع", callback_data=f"folder_make_active_{folder_id}")])

    # 4. Edit & Delete
    kb.append([
        InlineKeyboardButton("✏️ تعديل الاسم", callback_data=f"folder_rename_prompt_{folder_id}"),
        InlineKeyboardButton("🗑️ حذف المجلد", callback_data=f"folder_del_confirm_{folder_id}"),
    ])

    # 5. Back Navigation
    back_row = []
    if folder.get("parent_id"):
        back_row.append(InlineKeyboardButton("🔙 رجوع للمجلد السابق", callback_data=f"folder_view_{folder['parent_id']}"))
    else:
        back_row.append(InlineKeyboardButton("🔙 قائمة المجلدات", callback_data="folders_menu"))
    back_row.append(InlineKeyboardButton("🏠 الرئيسية", callback_data="main_menu"))
    kb.append(back_row)

    return text, InlineKeyboardMarkup(kb)


# ─── Folders Callbacks Router ─────────────────────────────────────────────────

async def handle_folders_callback(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    query = update.callback_query
    if not query:
        return False
    data = query.data

    # Folders list
    if data == "folders_menu":
        text, kb = build_folders_menu()
        await safe_edit(query, text, kb)
        return True

    # Folder detail
    if data.startswith("folder_view_"):
        fid = int(data.split("_")[-1])
        text, kb = build_folder_detail_view(fid)
        await safe_edit(query, text, kb)
        return True

    # Set as active folder
    if data.startswith("folder_make_active_"):
        fid = int(data.split("_")[-1])
        db.set_active_folder_id(fid)
        f = db.get_folder(fid)
        f_name = f["name"] if f else "المجلد"
        await query.answer(f"🎯 تم تعيين «{f_name}» كمجلدك النشط للإيداع!", show_alert=True)
        text, kb = build_folder_detail_view(fid)
        await safe_edit(query, text, kb)
        return True

    # Start review for this folder
    if data.startswith("folder_start_review_"):
        fid = int(data.split("_")[-1])
        from handlers.review_handler import start_review_session
        await start_review_session(update, context, folder_id=fid)
        return True

    # Browse items in folder
    if data.startswith("folder_browse_"):
        parts = data.split("_")
        fid = int(parts[2])
        idx = int(parts[3])

        items = db.get_items_by_folder(fid, include_subfolders=True)
        if not items:
            await query.answer("لا توجد مسائل في هذا المجلد.")
            return True

        idx = max(0, min(idx, len(items) - 1))
        item = items[idx]
        total = len(items)
        stg_lbl = stage_label(item["stage"], bool(item["is_mastered"]))

        text = (
            f"📋 <b>أرشيف المسائل ({idx + 1} من {total})</b>\n"
            f"📁 <b>المجلد:</b> {html.escape(item.get('folder_name') or 'عام')}\n"
            f"📝 <b>العنوان:</b> {html.escape(item.get('title') or 'مسألة')}\n"
            f"📅 <b>المرحلة:</b> {stg_lbl}\n"
            f"🏷️ <b>النمط:</b> {'مراجعة مباشرة' if item.get('review_mode') == 'direct' else 'مسألة تفاعلية'}\n\n"
        )
        if item.get("solution"):
            text += f"💡 <b>الحل / الشرح:</b>\n<blockquote>{html.escape(item['solution'])}</blockquote>\n"

        nav = []
        if idx > 0:
            nav.append(InlineKeyboardButton("⬅️ السابق", callback_data=f"folder_browse_{fid}_{idx - 1}"))
        if idx < total - 1:
            nav.append(InlineKeyboardButton("التالي ➡️", callback_data=f"folder_browse_{fid}_{idx + 1}"))

        kb = []
        if nav:
            kb.append(nav)
        kb.append([InlineKeyboardButton("🗑️ حذف هذه المسألة", callback_data=f"folder_del_item_{item['id']}_{fid}_{idx}")])
        kb.append([InlineKeyboardButton("🔙 تفاصيل المجلد", callback_data=f"folder_view_{fid}")])

        # 1. Photo Item: send actual photo directly in chat
        if item.get("item_type") == "photo":
            full_path = resolve_media_path(item.get("content", ""))
            if full_path and os.path.exists(full_path):
                try:
                    with open(full_path, "rb") as pf:
                        new_photo_msg = await context.bot.send_photo(
                            chat_id=update.effective_chat.id,
                            photo=pf,
                            caption=text[:1024],
                            parse_mode="HTML",
                            reply_markup=InlineKeyboardMarkup(kb),
                        )
                    db.track_chat_message(update.effective_chat.id, new_photo_msg.message_id)
                    if query.message:
                        try:
                            await query.message.delete()
                        except Exception:
                            pass
                    return True
                except Exception as e:
                    logger.warning("Failed to send photo in browser: %s", e)

        # 2. PDF / Document Item: send actual document file directly in chat
        if item.get("item_type") == "pdf":
            full_path = resolve_media_path(item.get("content", ""))
            if full_path and os.path.exists(full_path):
                try:
                    with open(full_path, "rb") as df:
                        new_doc_msg = await context.bot.send_document(
                            chat_id=update.effective_chat.id,
                            document=df,
                            filename=os.path.basename(full_path),
                            caption=text[:1024],
                            parse_mode="HTML",
                            reply_markup=InlineKeyboardMarkup(kb),
                        )
                    db.track_chat_message(update.effective_chat.id, new_doc_msg.message_id)
                    if query.message:
                        try:
                            await query.message.delete()
                        except Exception:
                            pass
                    return True
                except Exception as e:
                    logger.warning("Failed to send document in browser: %s", e)

        await safe_edit(query, text, InlineKeyboardMarkup(kb))
        return True

    # Delete single item from browser
    if data.startswith("folder_del_item_"):
        parts = data.split("_")
        item_id = int(parts[3])
        fid = int(parts[4])
        idx = int(parts[5])
        db.delete_item(item_id)
        await query.answer("🗑️ تم حذف المسألة بنجاح.")
        query.data = f"folder_browse_{fid}_{max(0, idx - 1)}"
        return await handle_folders_callback(update, context)

    # Prompt new root folder creation
    if data == "folder_add_prompt_root":
        context.user_data["input_state"] = "awaiting_folder_name"
        context.user_data["folder_parent_id"] = None
        context.user_data["prompt_msg_id"] = query.message.message_id if query.message else None
        prompt = (
            "➕ <b>إنشاء مجلد رئيسي جديد</b> 📁\n\n"
            "أرسل اسم المجلد في رسالة نصية الآن:\n"
            "<i>(مثال: قدرات كمي، أو مراجعات النماذج)</i>"
        )
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء", callback_data="folders_menu")]])
        await safe_edit(query, prompt, kb)
        return True

    # Prompt new subfolder creation
    if data.startswith("folder_add_prompt_sub_"):
        parent_fid = int(data.split("_")[-1])
        context.user_data["input_state"] = "awaiting_folder_name"
        context.user_data["folder_parent_id"] = parent_fid
        context.user_data["prompt_msg_id"] = query.message.message_id if query.message else None
        p_folder = db.get_folder(parent_fid)
        p_name = p_folder["name"] if p_folder else "المجلد"
        prompt = (
            f"➕ <b>إضافة مجلد فرعي داخل «{html.escape(p_name)}»</b> 📂\n\n"
            f"أرسل اسم المجلد الفرعي في رسالة نصية الآن:\n"
            f"<i>(مثال: هندسة، أو دوائر وتناظر)</i>"
        )
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء", callback_data=f"folder_view_{parent_fid}")]])
        await safe_edit(query, prompt, kb)
        return True

    # Prompt folder rename
    if data.startswith("folder_rename_prompt_"):
        fid = int(data.split("_")[-1])
        context.user_data["input_state"] = "awaiting_folder_rename"
        context.user_data["rename_folder_id"] = fid
        context.user_data["prompt_msg_id"] = query.message.message_id if query.message else None
        prompt = "✏️ <b>أرسل الاسم الجديد للمجلد في رسالة نصية الآن:</b>"
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء", callback_data=f"folder_view_{fid}")]])
        await safe_edit(query, prompt, kb)
        return True

    # Confirm folder delete
    if data.startswith("folder_del_confirm_"):
        fid = int(data.split("_")[-1])
        f = db.get_folder(fid)
        sub_cnt = len(db.get_subfolders(fid))
        sub_info = f" ومجلداته الفرعية ({sub_cnt})" if sub_cnt > 0 else ""
        text = (
            f"⚠️ <b>تأكيد حذف المجلد!</b>\n\n"
            f"هل أنت متأكد من حذف مجلد «{html.escape(f['name'] if f else '')}»{sub_info}؟\n"
            f"<i>(ملاحظة: المسائل بداخل هذا المجلد لن تُحذف وستبقى محفوظة بأمان)</i>"
        )
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("🗑️ نعم، احذف المجلد", callback_data=f"folder_do_del_{fid}")],
            [InlineKeyboardButton("❌ تراجع", callback_data=f"folder_view_{fid}")],
        ])
        await safe_edit(query, text, kb)
        return True

    # Execute folder delete
    if data.startswith("folder_do_del_"):
        fid = int(data.split("_")[-1])
        f = db.get_folder(fid)
        parent_id = f.get("parent_id") if f else None
        db.delete_folder(fid)
        await query.answer("🗑️ تم حذف المجلد بنجاح.")
        if parent_id:
            text, kb = build_folder_detail_view(parent_id)
        else:
            text, kb = build_folders_menu()
        await safe_edit(query, text, kb)
        return True

    return False
