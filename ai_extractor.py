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

        return json.loads(raw)

    return await asyncio.to_thread(_extract_text_sync)

