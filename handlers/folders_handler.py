import os
import html
import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes

import database as db
from spaced_repetition import stage_label
from utils import safe_edit

logger = logging.getLogger(__name__)


# ─── Folders View Builders ───────────────────────────────────────────────────

def build_folders_menu() -> tuple[str, InlineKeyboardMarkup]:
    """Builds the folders listing view with item counts and due indicators."""
    stats = db.get_dashboard_stats()
    folders = stats["folders_breakdown"]
    active_fid = db.get_active_folder_id()

    text_lines = [
        "📁 <b>مجلداتي وأرشيف المذاكرة</b>\n",
        "اختر مجلداً لمراجعة مسائله المستحقة أو تصفح أرشيفه كاملاً في أي وقت:\n",
    ]

    kb = []
    for f in folders:
        fid = f["id"]
        total = f["total_items"]
        due = f["due_items"]
        active_badge = " 🎯" if fid == active_fid else ""
        due_badge = f" | 🔴 {due} مستحق" if due > 0 else " | 🟢 مكتمل"
        label = f"{f['icon']} {f['name']} ({total}{due_badge}){active_badge}"
        kb.append([InlineKeyboardButton(label, callback_data=f"folder_view_{fid}")])

    kb.append([InlineKeyboardButton("➕ إنشاء مجلد جديد", callback_data="folder_add_prompt")])
    kb.append([InlineKeyboardButton("🏠 العودة للقائمة الرئيسية", callback_data="main_menu")])

    return "\n".join(text_lines), InlineKeyboardMarkup(kb)


def build_folder_detail_view(folder_id: int) -> tuple[str, InlineKeyboardMarkup]:
    """Builds the single folder control and action view."""
    folder = db.get_folder(folder_id)
    if not folder:
        return "المجلد غير موجود.", InlineKeyboardMarkup([[InlineKeyboardButton("🔙 رجوع", callback_data="folders_menu")]])

    stats = db.get_dashboard_stats()
    f_stat = next((f for f in stats["folders_breakdown"] if f["id"] == folder_id), None)
    total = f_stat["total_items"] if f_stat else 0
    due = f_stat["due_items"] if f_stat else 0

    active_fid = db.get_active_folder_id()
    is_active = (folder_id == active_fid)
    active_desc = "✅ هذا هو المجلد النشط حالياً (تُحفظ فيه الصور والمواد تلقائياً)" if is_active else "غير نشط (اضغط الزر لتحديده كمجلد نشط للإيداع التلقائي)"

    text = (
        f"📁 <b>مجلد: {folder['icon']} {html.escape(folder['name'])}</b>\n\n"
        f"📊 <b>إجمالي المواد:</b> <b>{total}</b> مادة دراسية\n"
        f"🔔 <b>المستحق للمراجعة اليوم:</b> <b>{due}</b> مادة\n"
        f"📌 <b>حالة الإيداع:</b> {active_desc}\n\n"
        f"اختر الإجراء المطلوب:"
    )

    kb = []
    if due > 0:
        kb.append([InlineKeyboardButton(f"▶️ ابدأ مراجعة المستحق في هذا المجلد ({due})", callback_data=f"folder_start_review_{folder_id}")])

    if total > 0:
        kb.append([InlineKeyboardButton(f"👁️ تصفح أرشيف هذا المجلد ({total})", callback_data=f"folder_browse_{folder_id}_0")])

    if not is_active:
        kb.append([InlineKeyboardButton("🎯 تعيين كمجلد نشط للإيداع التلقائي", callback_data=f"folder_make_active_{folder_id}")])

    kb.append([
        InlineKeyboardButton("✏️ إعادة تسمية", callback_data=f"folder_rename_prompt_{folder_id}"),
        InlineKeyboardButton("🗑️ حذف المجلد", callback_data=f"folder_del_confirm_{folder_id}"),
    ])
    kb.append([InlineKeyboardButton("🔙 قائمة المجلدات", callback_data="folders_menu")])

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
        await query.answer(f"🎯 تم تعيين «{f_name}» كمجلدك النشط للإيداع التلقائي!", show_alert=True)
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

        items = db.get_items_by_folder(fid)
        if not items:
            await query.answer("لا توجد مسائل في هذا المجلد.")
            return True

        idx = max(0, min(idx, len(items) - 1))
        item = items[idx]
        total = len(items)
        stg_lbl = stage_label(item["stage"], bool(item["is_mastered"]))

        text = (
            f"👁️ <b>أرشيف المجلد ({idx + 1} من {total})</b>\n"
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

        # If it's a photo, send or edit
        if item.get("item_type") == "photo" and os.path.exists(item.get("content", "")):
            # For browsing photo, send photo with caption
            try:
                with open(item["content"], "rb") as pf:
                    await context.bot.send_photo(
                        chat_id=update.effective_chat.id,
                        photo=pf,
                        caption=text[:1024],
                        parse_mode="HTML",
                        reply_markup=InlineKeyboardMarkup(kb)
                    )
                if query.message:
                    await query.message.delete()
                return True
            except Exception:
                pass

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

    # Prompt new folder creation
    if data == "folder_add_prompt":
        context.user_data["input_state"] = "awaiting_new_folder_name"
        prompt = (
            "➕ <b>إنشاء مجلد مذاكرة جديد</b> 📁\n\n"
            "أرسل اسم المجلد الجديد في الشات الآن:\n"
            "<i>(مثال: تجميعات النموذج 105، أو قواعد الهندسة)</i>"
        )
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء", callback_data="folders_menu")]])
        await safe_edit(query, prompt, kb)
        return True

    # Prompt folder rename
    if data.startswith("folder_rename_prompt_"):
        fid = int(data.split("_")[-1])
        context.user_data["input_state"] = "awaiting_folder_rename"
        context.user_data["rename_folder_id"] = fid
        prompt = "✏️ <b>أرسل الاسم الجديد للمجلد في رسالة نصية الآن:</b>"
        kb = InlineKeyboardMarkup([[InlineKeyboardButton("❌ إلغاء", callback_data=f"folder_view_{fid}")]])
        await safe_edit(query, prompt, kb)
        return True

    # Confirm folder delete
    if data.startswith("folder_del_confirm_"):
        fid = int(data.split("_")[-1])
        f = db.get_folder(fid)
        text = (
            f"⚠️ <b>تأكيد حذف المجلد!</b>\n\n"
            f"هل أنت متأكد من حذف مجلد «{html.escape(f['name'] if f else '')}»؟\n"
            f"<i>(ملاحظة: المسائل بداخل هذا المجلد لن تُحذف، بل ستنتقل للمجلد العام)</i>"
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
        db.delete_folder(fid)
        await query.answer("🗑️ تم حذف المجلد بنجاح.")
        text, kb = build_folders_menu()
        await safe_edit(query, text, kb)
        return True

    return False
