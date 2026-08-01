import json
import os
from google import genai
from google.genai import types
from config import GEMINI_API_KEY, GEMINI_MODEL

client = genai.Client(api_key=GEMINI_API_KEY)

EXTRACTION_PROMPT = """
أنت مساعد لاستخراج الأسئلة من ملفات PDF.
استخرج جميع الأسئلة من هذا الملف وأرجعها بصيغة JSON فقط بدون أي نص إضافي.

الصيغة المطلوبة:
{
  "quiz_name": "اسم مناسب للكويز مستخرج من محتوى الملف",
  "questions": [
    {
      "question": "نص السؤال",
      "options": ["الخيار أ", "الخيار ب", "الخيار ج", "الخيار د"],
      "answer": "الإجابة الصحيحة (نفس نص الخيار بالضبط)",
      "explanation": "شرح مختصر للإجابة الصحيحة إن وجد، وإلا اتركه فارغاً"
    }
  ]
}

قواعد مهمة:
- استخرج جميع الأسئلة الموجودة في الملف
- الإجابة الصحيحة يجب أن تكون نفس نص الخيار حرفياً
- أرجع JSON فقط بدون أي markdown أو نص إضافي
- إذا كان السؤال ليس اختيار من متعدد، تجاهله
"""


async def extract_questions_from_pdf(pdf_path: str) -> dict:
    """
    Upload a PDF to Gemini and extract questions as structured JSON.
    Returns dict with 'quiz_name' and 'questions' list.
    """
    # Upload the PDF file
    with open(pdf_path, "rb") as f:
        pdf_bytes = f.read()

    uploaded_file = client.files.upload(
        file=pdf_bytes,
        config=types.UploadFileConfig(
            mime_type="application/pdf",
            display_name="quiz_pdf",
        ),
    )

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

    # Clean the response
    raw = response.text.strip()
    if raw.startswith("```"):
        lines = raw.split("\n")
        # Remove first and last lines (``` markers)
        raw = "\n".join(lines[1:-1]).strip()

    data = json.loads(raw)
    return data
