import os
import sys
import html
import base64
import subprocess
import logging

logger = logging.getLogger(__name__)

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ASSETS_DIR = os.path.join(BASE_DIR, "assets")
OUTPUT_DIR = os.path.join(BASE_DIR, "data", "generated_schedules")
os.makedirs(OUTPUT_DIR, exist_ok=True)


def _find_chrome_executable() -> str | None:
    """Finds Chrome/Chromium executable across Windows and Linux."""
    candidates = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        "/usr/bin/google-chrome",
        "/usr/bin/chromium-browser",
        "/usr/bin/chromium",
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    # Try which / Get-Command
    import shutil
    for cmd in ["google-chrome", "chromium-browser", "chromium", "chrome"]:
        found = shutil.which(cmd)
        if found:
            return found
    return None


def _get_logo_base64() -> str:
    """Reads bot logo and returns base64 data uri."""
    logo_path = os.path.join(ASSETS_DIR, "logo_flat_2d.png")
    if not os.path.exists(logo_path):
        logo_path = os.path.join(ASSETS_DIR, "logo_geometric_v1.jpg")
    if os.path.exists(logo_path):
        with open(logo_path, "rb") as f:
            encoded = base64.b64encode(f.read()).decode("utf-8")
            ext = "png" if logo_path.endswith(".png") else "jpeg"
            return f"data:image/{ext};base64,{encoded}"
    return ""


def generate_schedule_html_a4(subject: dict, lessons: list[dict], user_name: str = "بطل القدرات") -> str:
    """Builds A4 landscape print-ready HTML for the schedule."""
    logo_b64 = _get_logo_base64()
    s_name = html.escape(subject["name"])
    total_l = len(lessons)
    target_d = subject["target_days"]

    # Group lessons by day
    days_map = {}
    for l in lessons:
        d = l["scheduled_day"]
        if d not in days_map:
            days_map[d] = []
        days_map[d].append(l)

    days_html = ""
    for d in range(1, target_d + 1):
        day_lessons = days_map.get(d, [])
        items_html = ""
        for dl in day_lessons:
            l_title = html.escape(dl["title"])
            items_html += f"""
            <div class="lesson-item">
                <span class="checkbox"></span>
                <span class="lesson-title">{l_title}</span>
            </div>
            """
        if not items_html:
            items_html = '<div class="rest-item">🌿 يوم راحة ومراجعة حرة</div>'

        days_html += f"""
        <div class="day-card">
            <div class="day-header">اليوم {d}</div>
            <div class="day-body">{items_html}</div>
        </div>
        """

    return f"""<!DOCTYPE html>
<html dir="rtl" lang="ar">
<head>
<meta charset="UTF-8">
<style>
    @import url('https://fonts.googleapis.com/css2?family=Tajawal:wght@400;500;700;800&display=swap');
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
        font-family: 'Tajawal', sans-serif;
        background: #0f172a;
        color: #f8fafc;
        width: 1400px;
        min-height: 950px;
        padding: 40px;
        display: flex;
        flex-direction: column;
    }}
    .header {{
        display: flex;
        justify-content: space-between;
        align-items: center;
        background: linear-gradient(135deg, #1e293b 0%, #334155 100%);
        padding: 24px 36px;
        border-radius: 20px;
        border: 1px solid #475569;
        margin-bottom: 30px;
    }}
    .header-left {{
        display: flex;
        align-items: center;
        gap: 20px;
    }}
    .logo-img {{
        width: 75px;
        height: 75px;
        border-radius: 18px;
        box-shadow: 0 4px 15px rgba(0,0,0,0.3);
    }}
    .header-title h1 {{
        font-size: 32px;
        font-weight: 800;
        color: #38bdf8;
    }}
    .header-title p {{
        font-size: 16px;
        color: #94a3b8;
    }}
    .stats-bar {{
        display: flex;
        gap: 25px;
    }}
    .stat-badge {{
        background: #0f172a;
        padding: 12px 20px;
        border-radius: 12px;
        text-align: center;
        border: 1px solid #334155;
    }}
    .stat-badge span {{
        display: block;
        font-size: 13px;
        color: #94a3b8;
    }}
    .stat-badge strong {{
        font-size: 20px;
        color: #10b981;
    }}
    .grid-container {{
        display: grid;
        grid-template-columns: repeat(5, 1fr);
        gap: 16px;
        flex-grow: 1;
    }}
    .day-card {{
        background: #1e293b;
        border-radius: 14px;
        border: 1px solid #334155;
        overflow: hidden;
        display: flex;
        flex-direction: column;
    }}
    .day-header {{
        background: #334155;
        padding: 8px 14px;
        font-weight: 700;
        font-size: 15px;
        color: #38bdf8;
    }}
    .day-body {{
        padding: 12px;
        display: flex;
        flex-direction: column;
        gap: 8px;
        flex-grow: 1;
    }}
    .lesson-item {{
        display: flex;
        align-items: center;
        gap: 8px;
        background: #0f172a;
        padding: 8px 10px;
        border-radius: 8px;
        border: 1px solid #1e293b;
    }}
    .checkbox {{
        width: 16px;
        height: 16px;
        border-radius: 4px;
        border: 2px solid #64748b;
        flex-shrink: 0;
    }}
    .lesson-title {{
        font-size: 13px;
        font-weight: 500;
        color: #e2e8f0;
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
    }}
    .rest-item {{
        font-size: 12px;
        color: #64748b;
        font-style: italic;
        text-align: center;
        padding: 10px 0;
    }}
    .footer {{
        margin-top: 25px;
        display: flex;
        justify-content: space-between;
        align-items: center;
        font-size: 13px;
        color: #64748b;
        border-top: 1px solid #1e293b;
        padding-top: 15px;
    }}
</style>
</head>
<body>
    <div class="header">
        <div class="header-left">
            <img class="logo-img" src="{logo_b64}" alt="Logo">
            <div class="header-title">
                <h1>خطة مذاكرة: {s_name} 🎯</h1>
                <p>خطة شخصية ذكية مُجدولة لـ <b>{html.escape(user_name)}</b> بواسطة بوت ذاكرة القدرات</p>
            </div>
        </div>
        <div class="stats-bar">
            <div class="stat-badge">
                <span>إجمالي الدروس</span>
                <strong>{total_l} درس</strong>
            </div>
            <div class="stat-badge">
                <span>المدة المستهدفة</span>
                <strong>{target_d} يوم</strong>
            </div>
            <div class="stat-badge">
                <span>المعدل اليومي</span>
                <strong>{max(1, round(total_l / max(1, target_d), 1))} درس/يوم</strong>
            </div>
        </div>
    </div>

    <div class="grid-container">
        {days_html}
    </div>

    <div class="footer">
        <div>💡 <b>نصيحة الإنجاز:</b> حدد وقتاً ثابتاً كل يوم، ومجرد إنهاء أي درس اضغط تم في البوت لتثبيت مراجعته تلقائياً!</div>
        <div>🧠 بوت ذاكرة القدرات — الذكاء في التكرار المتباعد</div>
    </div>
</body>
</html>"""


def generate_schedule_html_wallpaper(subject: dict, lessons: list[dict], user_name: str = "بطل القدرات") -> str:
    """Builds Mobile 9:16 Portrait Wallpaper HTML."""
    logo_b64 = _get_logo_base64()
    s_name = html.escape(subject["name"])
    total_l = len(lessons)
    target_d = subject["target_days"]

    days_map = {}
    for l in lessons:
        d = l["scheduled_day"]
        if d not in days_map:
            days_map[d] = []
        days_map[d].append(l)

    days_html = ""
    for d in range(1, target_d + 1):
        day_lessons = days_map.get(d, [])
        items_html = ""
        for dl in day_lessons:
            l_title = html.escape(dl["title"])
            items_html += f"""
            <div class="lesson-chip">
                <span class="dot"></span>
                <span>{l_title}</span>
            </div>
            """
        days_html += f"""
        <div class="w-day-row">
            <div class="w-day-label">اليوم {d}</div>
            <div class="w-day-items">{items_html or '<span class="rest-tag">استراحة ومراجعة</span>'}</div>
        </div>
        """

    return f"""<!DOCTYPE html>
<html dir="rtl" lang="ar">
<head>
<meta charset="UTF-8">
<style>
    @import url('https://fonts.googleapis.com/css2?family=Tajawal:wght@500;700;800&display=swap');
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{
        font-family: 'Tajawal', sans-serif;
        background: linear-gradient(180deg, #0f172a 0%, #1e1b4b 100%);
        color: #f8fafc;
        width: 1080px;
        height: 1920px;
        padding: 80px 60px;
        display: flex;
        flex-direction: column;
    }}
    .w-top {{
        text-align: center;
        margin-bottom: 40px;
    }}
    .w-logo {{
        width: 130px;
        height: 130px;
        border-radius: 30px;
        box-shadow: 0 10px 30px rgba(0,0,0,0.5);
        margin-bottom: 25px;
    }}
    .w-title {{
        font-size: 52px;
        font-weight: 800;
        color: #38bdf8;
        margin-bottom: 10px;
    }}
    .w-sub {{
        font-size: 26px;
        color: #94a3b8;
    }}
    .w-badges {{
        display: flex;
        justify-content: center;
        gap: 20px;
        margin-top: 25px;
    }}
    .w-badge {{
        background: rgba(30, 41, 59, 0.8);
        border: 1px solid #475569;
        border-radius: 16px;
        padding: 12px 28px;
        font-size: 22px;
        color: #10b981;
        font-weight: 700;
    }}
    .w-list {{
        flex-grow: 1;
        display: flex;
        flex-direction: column;
        gap: 14px;
        overflow: hidden;
    }}
    .w-day-row {{
        background: rgba(30, 41, 59, 0.7);
        border: 1px solid #334155;
        border-radius: 18px;
        padding: 16px 24px;
        display: flex;
        align-items: center;
        gap: 20px;
    }}
    .w-day-label {{
        background: #38bdf8;
        color: #0f172a;
        font-weight: 800;
        font-size: 20px;
        padding: 6px 16px;
        border-radius: 10px;
        flex-shrink: 0;
    }}
    .w-day-items {{
        display: flex;
        flex-wrap: wrap;
        gap: 10px;
        flex-grow: 1;
    }}
    .lesson-chip {{
        display: flex;
        align-items: center;
        gap: 8px;
        background: rgba(15, 23, 42, 0.8);
        border: 1px solid #475569;
        padding: 6px 14px;
        border-radius: 10px;
        font-size: 20px;
        color: #e2e8f0;
    }}
    .dot {{
        width: 10px;
        height: 10px;
        border-radius: 50%;
        background: #10b981;
    }}
    .rest-tag {{
        font-size: 18px;
        color: #94a3b8;
        font-style: italic;
    }}
    .w-bottom {{
        text-align: center;
        padding-top: 30px;
        font-size: 22px;
        color: #64748b;
        font-weight: 500;
    }}
</style>
</head>
<body>
    <div class="w-top">
        <img class="w-logo" src="{logo_b64}" alt="Logo">
        <div class="w-title">{s_name} 🎯</div>
        <div class="w-sub">خريطة الإنجاز اليومية لـ {html.escape(user_name)}</div>
        <div class="w-badges">
            <div class="w-badge">{total_l} درساً</div>
            <div class="w-badge">{target_d} يوماً</div>
            <div class="w-badge">🎯 هدف 100%</div>
        </div>
    </div>

    <div class="w-list">
        {days_html}
    </div>

    <div class="w-bottom">
        ⚡ حافظ على انضباطك اليومي، والدرجة الكاملة بيدك بإذن الله!
    </div>
</body>
</html>"""


def render_schedule_images(subject: dict, lessons: list[dict], user_name: str = "بطل القدرات") -> tuple[str | None, str | None]:
    """
    Renders A4 and Wallpaper images via Headless Chrome.
    Returns (path_to_a4_png, path_to_wallpaper_png).
    """
    chrome_bin = _find_chrome_executable()
    if not chrome_bin:
        logger.warning("Chrome executable not found. Image rendering skipped.")
        return None, None

    sub_id = subject["id"]
    a4_html_path = os.path.join(OUTPUT_DIR, f"schedule_{sub_id}_a4.html")
    wp_html_path = os.path.join(OUTPUT_DIR, f"schedule_{sub_id}_wallpaper.html")

    a4_png_path = os.path.join(OUTPUT_DIR, f"schedule_{sub_id}_a4.png")
    wp_png_path = os.path.join(OUTPUT_DIR, f"schedule_{sub_id}_wallpaper.png")

    with open(a4_html_path, "w", encoding="utf-8") as f:
        f.write(generate_schedule_html_a4(subject, lessons, user_name))

    with open(wp_html_path, "w", encoding="utf-8") as f:
        f.write(generate_schedule_html_wallpaper(subject, lessons, user_name))

    # Render A4 Landscape
    try:
        subprocess.run(
            [
                chrome_bin,
                "--headless=new",
                "--hide-scrollbars",
                "--window-size=1400,950",
                f"--screenshot={os.path.abspath(a4_png_path)}",
                f"file:///{os.path.abspath(a4_html_path).replace(os.sep, '/')}",
            ],
            check=True,
            timeout=15,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception as e:
        logger.error("Failed to render A4 schedule image: %s", e)
        a4_png_path = None

    # Render Wallpaper
    try:
        subprocess.run(
            [
                chrome_bin,
                "--headless=new",
                "--hide-scrollbars",
                "--window-size=1080,1920",
                f"--screenshot={os.path.abspath(wp_png_path)}",
                f"file:///{os.path.abspath(wp_html_path).replace(os.sep, '/')}",
            ],
            check=True,
            timeout=15,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except Exception as e:
        logger.error("Failed to render Wallpaper schedule image: %s", e)
        wp_png_path = None

    return (a4_png_path if a4_png_path and os.path.exists(a4_png_path) else None,
            wp_png_path if wp_png_path and os.path.exists(wp_png_path) else None)
