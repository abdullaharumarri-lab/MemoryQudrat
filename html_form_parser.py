# -*- coding: utf-8 -*-
"""
html_form_parser.py
MemoryQudrat — 100% Offline, Deterministic Google Forms HTML Parser.
Extracts questions, options, passages, correct answers, and wrong marks directly
from the page code (HTML source, FB_PUBLIC_LOAD_DATA_, and DOM) without internet or AI.
"""

import re
import json
import logging
from typing import Dict, Any, List, Optional
from utils import (
    strip_invisible_chars,
    normalize_arabic_digits,
    normalize_for_match,
    strip_option_prefix_py,
    find_correct_option_index,
)

logger = logging.getLogger(__name__)


def is_verbal_analogy_py(q_text: str) -> bool:
    """Detects if a question is a pure verbal analogy (e.g. 'سيف : مهند')."""
    if not q_text:
        return False
    t = q_text.strip().rstrip("*").strip()
    if t.endswith(":") or t.endswith("：") or t.endswith("؟") or t.endswith("?"):
        return False
    parts = re.split(r'[:\：]', t)
    if len(parts) == 2:
        left = parts[0].strip()
        right = parts[1].strip()
        if left and right and len(left.split()) <= 4 and len(right.split()) <= 4 and len(t) < 40:
            non_analogy_words = [
                'ما', 'لماذا', 'كيف', 'متى', 'أين', 'كم', 'أي', 'هل', 'من',
                'ماذا', 'علاقة', 'معنى', 'يدل', 'تعني', 'يقصد', 'وفق', 'النص',
                'القطعة', 'الفقرة'
            ]
            if not any(w in left or w in right for w in non_analogy_words):
                return True
    return False


def parse_google_form_html(html_content: str) -> Dict[str, Any]:
    """
    Parses a Google Forms HTML file (viewscore or viewform) directly from the page code.
    Runs 100% locally, with zero network calls and zero AI.
    """
    if not html_content or not isinstance(html_content, str):
        raise ValueError("محتوى كود الصفحة فارغ أو غير صالح.")

    # 1. Look for FB_PUBLIC_LOAD_DATA_ in <script>
    m_data = re.search(
        r'var\s+FB_PUBLIC_LOAD_DATA_\s*=\s*(\[.*?\]);\s*(?:<\/script>|;|$)',
        html_content,
        re.DOTALL,
    )
    raw_data = None
    if m_data:
        try:
            raw_data = json.loads(m_data.group(1))
        except Exception as e:
            logger.warning("Could not parse FB_PUBLIC_LOAD_DATA_ as JSON: %s", e)

    # 2. Extract Quiz Title
    quiz_title = "كويز بدون عنوان"
    if raw_data and len(raw_data) > 1 and len(raw_data[1]) > 8 and raw_data[1][8]:
        quiz_title = str(raw_data[1][8]).strip().split('\n')[0].strip()
    else:
        m_title = re.search(r'<title>(.*?)<\/title>', html_content, re.IGNORECASE)
        if m_title:
            t = m_title.group(1).strip()
            t = re.sub(r'[\s\-–—\|]+(?:Google Forms|نماذج Google|Google Form)$', '', t, flags=re.IGNORECASE).strip()
            if t and t != "Google Forms":
                quiz_title = t
        if quiz_title == "كويز بدون عنوان":
            m_h1 = re.search(r'role="heading"[^>]*aria-level="1"[^>]*>(.*?)<\/div>', html_content, re.IGNORECASE | re.DOTALL)
            if m_h1:
                clean_h1 = re.sub(r'<[^>]+>', '', m_h1.group(1)).strip()
                if clean_h1:
                    quiz_title = clean_h1

    # 3. Detect ViewScore mode (Graded / Score view)
    is_viewscore = bool(
        "viewscore" in html_content or
        "إجمالي النقاط" in html_content or
        "عرض النتيجة" in html_content or
        "Total points" in html_content or
        "View score" in html_content or
        "الإجابة الصحيحة" in html_content or
        "Correct answer" in html_content or
        re.search(r'\b[01]\s*\/\s*[1-9]', html_content) or
        re.search(r'\b[٠١]\s*\/\s*[١-٩]', html_content)
    )

    questions: List[Dict[str, Any]] = []
    wrong_indices: List[int] = []

    # ── PRIMARY ENGINE: FB_PUBLIC_LOAD_DATA_ ─────────────────────────────────
    if raw_data and len(raw_data) > 1 and len(raw_data[1]) > 1 and isinstance(raw_data[1][1], list):
        items = raw_data[1][1]
        current_passage: Optional[str] = None
        passage_counter = 0

        for item in items:
            item_id = item[0]
            title = (item[1] or "").strip()
            item_type = item[3]

            # Type 1 & 6: Reading Passage Card
            if item_type in (1, 6):
                desc = (item[2] or "").strip()
                passage_text = f"{title}\n\n{desc}".strip() if (title and desc and len(title) > 30) else (desc or title)
                if passage_text and len(passage_text) > 15:
                    is_meta = bool(re.search(r'(?:اسم\s+الطالب|اسم\s+المشترك|البريد|email|رقم\s+الجوال|كلمة\s+المرور|password|اقسم|أقسم|أتعهد|تعهد)', passage_text, re.IGNORECASE))
                    if not is_meta:
                        passage_counter += 1
                        current_passage = passage_text
                continue

            # Type 8: Section Break
            if item_type == 8:
                sec_desc = (item[2] or "").strip()
                if sec_desc and len(sec_desc) > 25:
                    is_meta = bool(re.search(r'(?:اسم\s+الطالب|بيانات|تسجيل|معلومات|تعليمات|درجات)', sec_desc, re.IGNORECASE))
                    if not is_meta:
                        passage_counter += 1
                        current_passage = sec_desc
                continue

            # Type 2: Multiple Choice Question (MCQ)
            if item_type == 2:
                is_student_meta = bool(re.search(r'(?:اسم\s+الطالب|اسم\s+المشترك|كلمة\s+المرور|البريد\s+الإلكتروني)', title, re.IGNORECASE))
                if is_student_meta:
                    continue

                options_data = []
                if len(item) > 4 and item[4] and item[4][0] and len(item[4][0]) > 1 and isinstance(item[4][0][1], list):
                    options_data = item[4][0][1]

                raw_options = [str(opt[0]).strip() for opt in options_data if opt and len(opt) > 0 and opt[0]]
                clean_options = [strip_option_prefix_py(o) for o in raw_options if o]
                options = clean_options if len(clean_options) >= 2 else (raw_options if len(raw_options) >= 2 else ["صح", "خطأ"])

                # Question image if embedded in item structure
                q_image = None
                try:
                    item_str = json.dumps(item)
                    img_match = re.search(r'https:\/\/(?:lh\d+\.googleusercontent\.com|docs\.google\.com\/forms\/d\/e\/[^"\'\\]+)', item_str)
                    if img_match:
                        q_image = img_match.group(0)
                except Exception:
                    pass

                clean_q = re.sub(r'^[\d٠-٩]+[\s\.\:\-\)\/]+\s*', '', title).rstrip("*").strip()
                if not clean_q:
                    clean_q = f"السؤال {len(questions) + 1}"

                # Attach reading passage if appropriate
                q_passage_text = None
                if current_passage:
                    if not is_verbal_analogy_py(clean_q):
                        q_passage_text = current_passage
                        snippet = current_passage[:25].strip()
                        if snippet not in clean_q:
                            clean_q = f"📄 {current_passage}\n\n❓ {clean_q}"

                questions.append({
                    "item_id": item_id,
                    "question": clean_q,
                    "options": options,
                    "answer": "",
                    "explanation": "",
                    "passage_text": q_passage_text,
                    "image": q_image,
                })

    # ── SECONDARY ENGINE: DOM Fallback if FB_PUBLIC_LOAD_DATA_ was missing ──
    if not questions:
        logger.info("FB_PUBLIC_LOAD_DATA_ not found or empty, using pure DOM parser.")
        card_blocks = re.findall(r'(<div[^>]+role="listitem"[^>]*>.*?<\/div>\s*<\/div>\s*<\/div>)', html_content, re.DOTALL)
        if not card_blocks:
            card_blocks = re.findall(r'(<div[^>]+class="[^"]*Qr7Oae[^"]*"[^>]*>.*?<\/div>\s*<\/div>)', html_content, re.DOTALL)

        current_dom_passage = None
        for block in card_blocks:
            clean_block_text = re.sub(r'<[^>]+>', ' ', block)
            clean_block_text = re.sub(r'\s+', ' ', clean_block_text).strip()

            has_radio = 'role="radio"' in block or 'role="radiogroup"' in block or 'aria-checked' in block
            if not has_radio:
                if len(clean_block_text) > 30 and not re.search(r'(?:اسم\s+الطالب|البريد|email|رقم\s+الجوال|تعهد)', clean_block_text, re.IGNORECASE):
                    current_dom_passage = clean_block_text
                continue

            m_qh = re.search(r'role="heading"[^>]*>(.*?)<\/div>', block, re.DOTALL | re.IGNORECASE)
            q_text = re.sub(r'<[^>]+>', '', m_qh.group(1)).strip() if m_qh else ""
            q_text = re.sub(r'^[\d٠-٩]+[\s\.\:\-\)\/]+\s*', '', q_text).rstrip("*").strip()
            if not q_text:
                q_text = f"السؤال {len(questions) + 1}"

            opt_matches = re.findall(r'<span[^>]*class="[^"]*M7eMe[^"]*"[^>]*>(.*?)<\/span>', block, re.DOTALL)
            if not opt_matches:
                opt_matches = re.findall(r'role="radio"[^>]*>.*?<span[^>]*>(.*?)<\/span>', block, re.DOTALL)

            raw_opts = [re.sub(r'<[^>]+>', '', o).strip() for o in opt_matches if re.sub(r'<[^>]+>', '', o).strip()]
            clean_opts = [strip_option_prefix_py(o) for o in raw_opts if o]
            options = clean_opts if len(clean_opts) >= 2 else (raw_opts if len(raw_opts) >= 2 else ["صح", "خطأ"])

            dom_img = None
            m_img = re.search(r'<img[^>]+src="([^">]+)"', block)
            if m_img and not m_img.group(1).endswith("cleardot.gif"):
                dom_img = m_img.group(1)

            q_passage = None
            if current_dom_passage and not is_verbal_analogy_py(q_text):
                q_passage = current_dom_passage
                if current_dom_passage[:25].strip() not in q_text:
                    q_text = f"📄 {current_dom_passage}\n\n❓ {q_text}"

            questions.append({
                "item_id": None,
                "question": q_text,
                "options": options,
                "answer": "",
                "explanation": "",
                "passage_text": q_passage,
                "image": dom_img,
            })

    if not questions:
        raise ValueError("لم يتم العثور على أي أسئلة داخل كود الصفحة. تأكد من حفظ كود الصفحة بالكامل.")

    # ── 4. EXTRACT CORRECT ANSWERS & SCORE FROM DOM ──────────────────────────
    cards = re.findall(r'(<div[^>]+role="listitem"[^>]*>.*?<\/div>\s*<\/div>\s*<\/div>)', html_content, re.DOTALL)
    if not cards:
        cards = re.findall(r'(<div[^>]+class="[^"]*Qr7Oae[^"]*"[^>]*>.*?<\/div>\s*<\/div>)', html_content, re.DOTALL)

    ca_regex = re.compile(
        r'(?:الإجابة الصحيحة|الإجابات الصحيحة|الإجابة النموذجية|Correct answers?)\s*[:\n\-]?\s*([^\n<]+)',
        re.IGNORECASE
    )

    for idx, q in enumerate(questions):
        q_num = idx + 1
        q_snippet = q["question"][:20].strip()
        matched_card = None

        if cards:
            for c in cards:
                if q_snippet and q_snippet in c:
                    matched_card = c
                    break
            if not matched_card and idx < len(cards):
                matched_card = cards[idx]

        if matched_card:
            card_text = re.sub(r'<[^>]+>', ' ', matched_card)
            card_text = re.sub(r'\s+', ' ', card_text).strip()

            is_wrong = False
            if (re.search(r'\b0\s*\/\s*[1-9]', card_text) or
                re.search(r'\b٠\s*\/\s*[١-٩]', card_text) or
                re.search(r'\b0\s*من\s+إجمالي', card_text) or
                re.search(r'\b٠\s*من\s+إجمالي', card_text) or
                re.search(r'غير صحيح|إجابة غير صحيحة|Incorrect', card_text, re.IGNORECASE)):
                is_wrong = True

            # 1. Search for explicit "الإجابة الصحيحة"
            m_ca = ca_regex.search(card_text)
            if m_ca:
                ca_raw = strip_option_prefix_py(m_ca.group(1).strip())
                ans_idx = find_correct_option_index(q["options"], ca_raw)
                q["answer"] = q["options"][ans_idx]
                is_wrong = True
            elif "الإجابة الصحيحة" in card_text or "Correct answer" in card_text:
                parts = card_text.split("الإجابة الصحيحة")
                if len(parts) > 1:
                    candidate = parts[1].strip().split()[0:4]
                    cand_text = strip_option_prefix_py(" ".join(candidate))
                    ans_idx = find_correct_option_index(q["options"], cand_text)
                    q["answer"] = q["options"][ans_idx]
                    is_wrong = True

            # 2. If student answered correctly (not marked wrong)
            if not q["answer"] and not is_wrong and is_viewscore:
                m_checked = re.search(r'aria-checked="true"[^>]*>.*?<span[^>]*>(.*?)<\/span>', matched_card, re.DOTALL)
                if m_checked:
                    raw_selected = re.sub(r'<[^>]+>', '', m_checked.group(1)).strip()
                    if raw_selected:
                        clean_sel = strip_option_prefix_py(raw_selected)
                        ans_idx = find_correct_option_index(q["options"], clean_sel)
                        q["answer"] = q["options"][ans_idx]

            # 3. Extract explanation / feedback
            m_exp = re.search(r'(?:ملاحظات|تعليقات|Feedback)\s*[:\n]+\s*([^<]+)', card_text, re.IGNORECASE)
            if m_exp:
                q["explanation"] = m_exp.group(1).strip()

            if is_wrong:
                wrong_indices.append(q_num)

    return {
        "quiz_name": quiz_title,
        "wrong": sorted(list(set(wrong_indices))),
        "questions": questions,
        "is_viewscore": is_viewscore,
    }
