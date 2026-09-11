import json
import os
import asyncio
from google import genai
from google.genai import types
from config import GEMINI_API_KEY, GEMINI_MODEL

client = genai.Client(api_key=GEMINI_API_KEY)

EXTRACTION_PROMPT = """
أنت خبير ومعلم متخصص في اختبار القدرات العامة (الكمي واللفظي) ونماذج الاختبارات الإلكترونية (Google Forms / PDF).
استخرج جميع الأسئلة من هذا الملف بدقة 100% وأرجعها بصيغة JSON فقط بدون أي نص إضافي.

الصيغة المطلوبة بدقة:
{
  "quiz_name": "اسم الكويز المناسب",
  "wrong": [1, 3],
  "questions": [
    {
      "question": "نص السؤال كاملاً بدون أخطاء إملائية",
      "options": ["الخيار أ", "الخيار ب", "الخيار ج", "الخيار د"],
      "answer": "الإجابة الصحيحة الحقيقية (نفس نص الخيار حرفياً)",
      "explanation": "شرح طريقة الحل والوصول للناتج إن وجد"
    }
  ]
}

القواعد الأساسية:
1. حل كل مسألة كمية أو لفظية بنفسك للتأكد 100% من صحة الإجابة.
2. إذا كان الملف يحتوي على صفحة نتيجة Google Forms:
   - حدد الإجابة الصحيحة من صندوق التصحيح الأخضر أو من الخيار الصحيح.
   - إذا حصل الطالب على 0/1 أو علامة ❌ في سؤال معين، ضع رقم السؤال في قائمة "wrong" تلقائياً.
3. دقق النصوص إملائياً وصحح أي أخطاء مطبعية أو رموز مقطوعة.
4. حقل "answer" يجب أن يطابق تماماً وبنفس النص أحد عناصر "options".
5. أرجع JSON نقي فقط بدون أي كتل markdown وبدون أي كلام جانبي.
"""


async def extract_questions_from_pdf(pdf_path: str) -> dict:
    """
    Upload a PDF to Gemini and extract questions as structured JSON.
    Returns dict with 'quiz_name', 'questions' list, and optional 'wrong' list.
    """
    def _extract_sync():
        with open(pdf_path, "rb") as f:
            pdf_bytes = f.read()

        uploaded_file = client.files.upload(
            file=pdf_bytes,
            config=types.UploadFileConfig(
                mime_type="application/pdf",
                display_name="quiz_pdf",
            ),
        )

        try:
            response = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=[
                    types.Part.from_uri(
                        file_uri=uploaded_file.uri,
                        mime_type="application/pdf",
                    ),
                    EXTRACTION_PROMPT,
                ],
                config=types.GenerateContentConfig(
                    temperature=0.1,
                    max_output_tokens=8192,
                ),
            )

            raw = response.text.strip()
            if raw.startswith("```"):
                lines = raw.split("\n")
                if lines[0].startswith("```"):
                    lines = lines[1:]
                if lines and lines[-1].startswith("```"):
                    lines = lines[:-1]
                raw = "\n".join(lines).strip()

            try:
                return json.loads(raw)
            except json.JSONDecodeError as e:
                raise ValueError(f"Gemini returned non-JSON response. Parse error: {e}\nRaw: {raw[:200]}")
        finally:
            try:
                client.files.delete(name=uploaded_file.name)
            except Exception:
                pass

    return await asyncio.to_thread(_extract_sync)


async def extract_questions_from_text(raw_text: str) -> dict:
    """
    Extract questions from raw text or Google Forms text dump using Gemini.
    """
    def _extract_text_sync():
        prompt = f"{EXTRACTION_PROMPT}\n\nالنص المراد استخراج الأسئلة منه:\n{raw_text}"
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.1,
                max_output_tokens=8192,
            ),
        )
        raw = response.text.strip()
        if raw.startswith("```"):
            lines = raw.split("\n")
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            raw = "\n".join(lines).strip()

        try:
            return json.loads(raw)
        except json.JSONDecodeError as e:
            raise ValueError(f"Gemini returned non-JSON response: {e}\nRaw: {raw[:200]}")

    return await asyncio.to_thread(_extract_text_sync)


async def solve_missing_answers(questions: list) -> list:
    """
    Takes a list of question dicts that are missing answers and uses Gemini to solve them.
    Updates 'answer' and 'explanation' fields in place.
    """
    missing = [q for q in questions if not str(q.get("answer", "")).strip()]
    if not missing:
        return questions

    def _solve_sync():
        batch_to_solve = [
            {
                "index": idx,
                "question": q.get("question", ""),
                "options": q.get("options", []),
            }
            for idx, q in enumerate(missing)
        ]

        prompt = f"""أنت خبير قياس واختبار القدرات العامة (الكمي واللفظي).
لديك قائمة بالأسئلة التالية مع خياراتها.
قم بحل كل مسألة بدقة رياضية ولغوية 100%، وحدد الإجابة الصحيحة نصاً وحرفاً من بين الخيارات المعطاة لكل سؤال، واكتب شرحاً واضحاً وموجزاً.

الأسئلة:
{json.dumps(batch_to_solve, ensure_ascii=False, indent=2)}

أرجع JSON نقي فقط بنفس الترتيب بهذه الصيغة بدون أي كتل markdown:
[
  {{
    "index": 0,
    "answer": "نص الإجابة الصحيحة المطابق تماماً لأحد الخيارات",
    "explanation": "شرح طريقة الحل والوصول للناتج"
  }}
]
"""
        response = client.models.generate_content(
            model=GEMINI_MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.1,
                max_output_tokens=8192,
            ),
        )
        raw = response.text.strip()
        if raw.startswith("```"):
            lines = raw.split("\n")
            if lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            raw = "\n".join(lines).strip()

        solved_items = json.loads(raw)
        for item in solved_items:
            idx = item.get("index")
            if idx is not None and 0 <= idx < len(missing):
                ans = item.get("answer", "").strip()
                exp = item.get("explanation", "").strip()
                missing[idx]["answer"] = ans
                if exp:
                    missing[idx]["explanation"] = exp

    try:
        await asyncio.to_thread(_solve_sync)
    except Exception as e:
        import logging
        logging.getLogger(__name__).warning("Could not auto-solve missing answers via Gemini: %s", e)
    return questions


async def extract_and_solve_google_form(url: str) -> dict:
    """
    Directly extracts a Google Forms quiz (viewform or viewscore).
    Parses native FB_PUBLIC_LOAD_DATA_ tree, captures reading passages and images,
    and solves missing answers using Gemini AI.
    """
    import re
    import urllib.parse
    import requests

    def _fetch_and_parse_sync():
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ar,en-US;q=0.9,en;q=0.8",
        }

        resp = requests.get(url, headers=headers, timeout=15)
        resp.raise_for_status()
        html_text = resp.text

        m = re.search(r'var\s+FB_PUBLIC_LOAD_DATA_\s*=\s*(\[.*?\]);\s*(?:<\/script>|;|$)', html_text, re.DOTALL)
        if not m:
            raise ValueError("لم يتم العثور على بيانات نموذج Google Forms (FB_PUBLIC_LOAD_DATA_). تأكد من صحة الرابط.")

        raw_data = json.loads(m.group(1))
        quiz_title = "كويز بدون عنوان"
        if len(raw_data) > 1 and len(raw_data[1]) > 8 and raw_data[1][8]:
            quiz_title = str(raw_data[1][8]).strip().split('\n')[0].strip()

        items = raw_data[1][1] if (len(raw_data) > 1 and len(raw_data[1]) > 1 and isinstance(raw_data[1][1], list)) else []

        questions = []
        wrong_indices = []
        current_passage = None
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
                from utils import strip_option_prefix_py
                clean_options = [strip_option_prefix_py(o) for o in raw_options if o]
                options = clean_options if len(clean_options) >= 2 else (raw_options if len(raw_options) >= 2 else ["صح", "خطأ"])

                # Question image if embedded
                q_image = None
                item_str = json.dumps(item)
                img_match = re.search(r'https:\/\/(?:lh\d+\.googleusercontent\.com|docs\.google\.com\/forms\/d\/e\/[^"\'\\]+)', item_str)
                if img_match:
                    q_image = img_match.group(0)

                clean_q = re.sub(r'^[\d٠-٩]+[\s\.\:\-\)\/]+\s*', '', title).strip()
                if not clean_q:
                    clean_q = f"السؤال {len(questions) + 1}"

                # Attach reading passage if appropriate
                q_passage_text = None
                if current_passage:
                    # Check if analogy
                    is_analogy = (":" in clean_q or "：" in clean_q) and len(clean_q.split()) <= 6 and not any(w in clean_q for w in ["ما", "كيف", "لماذا", "القطعة", "النص"])
                    if not is_analogy:
                        q_passage_text = current_passage
                        if current_passage[:20] not in clean_q:
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

        # ── Parse Score / Answers from HTML if viewscore ──
        is_viewscore = "viewscore" in url or "عرض النتيجة" in html_text or "إجمالي النقاط" in html_text
        if is_viewscore:
            ca_pattern = re.compile(r'(?:الإجابة الصحيحة|الإجابات الصحيحة|الإجابة النموذجية|Correct answers?)\s*[:\n\-]?\s*([^\n<]+)', re.IGNORECASE)
            # Find cards in HTML using regex blocks
            cards = re.findall(r'(<div[^>]+role="listitem"[^>]*>.*?<\/div>\s*<\/div>\s*<\/div>)', html_text, re.DOTALL)
            if not cards:
                cards = re.findall(r'(<div[^>]+class="[^"]*Qr7Oae[^"]*"[^>]*>.*?<\/div>\s*<\/div>)', html_text, re.DOTALL)

            for idx, q in enumerate(questions):
                q_snippet = q["question"][:25]
                # Match card by snippet or position
                matched_card = None
                for c in cards:
                    if q_snippet in c:
                        matched_card = c
                        break
                if not matched_card and idx < len(cards):
                    matched_card = cards[idx]

                if matched_card:
                    # Strip html tags to get text
                    card_text = re.sub(r'<[^>]+>', ' ', matched_card)
                    card_text = re.sub(r'\s+', ' ', card_text).strip()

                    m_ca = ca_pattern.search(card_text)
                    if m_ca:
                        ca_raw = m_ca.group(1).strip()
                        from utils import strip_option_prefix_py, find_correct_option_index
                        q["answer"] = ca_raw
                        wrong_indices.append(idx + 1)
                    else:
                        # If no "correct answer" text, check if student answered correctly
                        has_zero = bool(re.search(r'\b0\s*\/\s*[1-9]|\b٠\s*\/\s*[١-٩]|غير صحيح|Incorrect', card_text))
                        if not has_zero:
                            # Student answered correctly, find selected option
                            m_checked = re.search(r'aria-checked="true"[^>]*>.*?<span[^>]*>(.*?)<\/span>', matched_card, re.DOTALL)
                            if m_checked:
                                raw_ans = re.sub(r'<[^>]+>', '', m_checked.group(1)).strip()
                                if raw_ans:
                                    q["answer"] = raw_ans

        return {
            "quiz_name": quiz_title,
            "wrong": wrong_indices,
            "questions": questions,
            "is_viewscore": is_viewscore,
        }

    parsed = await asyncio.to_thread(_fetch_and_parse_sync)
    
    # If any questions are missing answers (e.g. viewform or unrevealed answers), solve with Gemini!
    questions = parsed.get("questions", [])
    missing_count = sum(1 for q in questions if not str(q.get("answer", "")).strip())
    if missing_count > 0:
        await solve_missing_answers(questions)

    return parsed


async def extract_questions_from_image(image_bytes: bytes, mime_type: str = "image/png") -> dict:
    """
    Extracts and solves questions directly from an image or screenshot using Gemini Vision.
    """
    def _extract_image_sync():
        uploaded_file = client.files.upload(
            file=image_bytes,
            config=types.UploadFileConfig(
                mime_type=mime_type,
                display_name="quiz_image",
            ),
        )
        try:
            response = client.models.generate_content(
                model=GEMINI_MODEL,
                contents=[
                    types.Part.from_uri(
                        file_uri=uploaded_file.uri,
                        mime_type=mime_type,
                    ),
                    EXTRACTION_PROMPT,
                ],
                config=types.GenerateContentConfig(
                    temperature=0.1,
                    max_output_tokens=8192,
                ),
            )
            raw = response.text.strip()
            if raw.startswith("```"):
                lines = raw.split("\n")
                if lines[0].startswith("```"):
                    lines = lines[1:]
                if lines and lines[-1].startswith("```"):
                    lines = lines[:-1]
                raw = "\n".join(lines).strip()

            return json.loads(raw)
        finally:
            try:
                client.files.delete(name=uploaded_file.name)
            except Exception:
                pass

    return await asyncio.to_thread(_extract_image_sync)


