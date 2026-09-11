/**
 * MemoryQudrat — Bookmarklet (مفضلة المتصفح السحرية 🪄)
 * استخراج كويزات Google Forms بنقرة واحدة مباشرة من كود الصفحة (100% Offline وبدون ذكاء اصطناعي وبدون إضافات).
 * 
 * كيفية الاستخدام:
 * 1. انسخ الكود الموجود بالأسفل (المبدأ بـ javascript:).
 * 2. أضف صفحة جديدة إلى شريط المفضلة في متصفحك (Bookmarks).
 * 3. ضع في خانة الرابط (URL) هذا الكود.
 * 4. عند فتح أي اختبار Google Forms (عرض النتيجة أو الأسئلة)، اضغط على المفضلة ليتم استخراج الكويز وتحميله فوراً!
 */

javascript:(function(){
    try {
        let rawData = null;
        if (typeof window.FB_PUBLIC_LOAD_DATA_ !== 'undefined' && window.FB_PUBLIC_LOAD_DATA_) {
            rawData = window.FB_PUBLIC_LOAD_DATA_;
        } else {
            const scripts = Array.from(document.querySelectorAll('script'));
            for (const s of scripts) {
                const text = s.textContent || '';
                const m = text.match(/var\s+FB_PUBLIC_LOAD_DATA_\s*=\s*(\[.*?\]);\s*(?:<\/script>|;|$)/s);
                if (m) {
                    try { rawData = JSON.parse(m[1]); break; } catch(e){}
                }
            }
        }

        let quizTitle = "كويز قدرات";
        if (rawData && rawData[1] && rawData[1][8]) {
            quizTitle = String(rawData[1][8]).trim().split('\n')[0].trim();
        } else {
            const h1 = document.querySelector('[role="heading"][aria-level="1"], .freebirdFormviewerViewHeaderTitle');
            if (h1 && h1.innerText.trim()) quizTitle = h1.innerText.trim().split('\n')[0].trim();
        }

        const isViewScore = window.location.href.includes('viewscore') ||
                            document.body.innerText.includes('إجمالي النقاط') ||
                            document.body.innerText.includes('عرض النتيجة') ||
                            document.body.innerText.includes('View score');

        function cleanPrefix(t) {
            if (!t) return '';
            let s = String(t).replace(/[\u00a0\u202f]/g, ' ').replace(/[\ufeff\u200b-\u200f]/g, '').trim();
            s = s.replace(/^(?:الخيار|خيار|Option)\s*[:\-\.]?\s*/i, '');
            s = s.replace(/^[(\uff08]?[\u0623-\u064a\u0647\u0648a-jA-J]\u0640*[)\uff09.:\-\/\s]+\s*/, '');
            s = s.replace(/^[(\uff08]?[1-9\u0661-\u0669][)\uff09\.\-]+\s*/, '');
            return s.trim();
        }

        const questions = [];
        const wrongIndices = [];
        let currentPassage = null;

        if (rawData && rawData[1] && Array.isArray(rawData[1][1])) {
            const items = rawData[1][1];
            for (const item of items) {
                const title = (item[1] || "").trim();
                const itemType = item[3];

                if (itemType === 1 || itemType === 6) {
                    const desc = (item[2] || "").trim();
                    const pText = (title && desc && title.length > 30) ? `${title}\n\n${desc}` : (desc || title);
                    if (pText && pText.length > 15 && !/(?:اسم\s+الطالب|البريد|email|رقم\s+الجوال|تعهد)/i.test(pText)) {
                        currentPassage = pText;
                    }
                    continue;
                }

                if (itemType === 8) {
                    const secDesc = (item[2] || "").trim();
                    if (secDesc && secDesc.length > 25 && !/(?:اسم\s+الطالب|بيانات|تسجيل)/i.test(secDesc)) {
                        currentPassage = secDesc;
                    }
                    continue;
                }

                if (itemType === 2) {
                    if (/(?:اسم\s+الطالب|اسم\s+المشترك|كلمة\s+المرور|البريد\s+الإلكتروني)/i.test(title)) continue;

                    let optionsData = [];
                    if (item.length > 4 && item[4] && item[4][0] && Array.isArray(item[4][0][1])) {
                        optionsData = item[4][0][1];
                    }
                    const rawOptions = optionsData.map(o => (o && o[0] ? String(o[0]).trim() : '')).filter(Boolean);
                    const cleanOpts = rawOptions.map(cleanPrefix).filter(Boolean);
                    const options = cleanOpts.length >= 2 ? cleanOpts : (rawOptions.length >= 2 ? rawOptions : ["صح", "خطأ"]);

                    let qText = title.replace(/^[\d٠-٩]+[\s\.\:\-\)\/]+\s*/, '').replace(/\s*\*\s*$/, '').trim();
                    if (!qText) qText = `السؤال ${questions.length + 1}`;

                    let qPassageText = null;
                    if (currentPassage) {
                        const isAnalogy = (qText.includes(':') || qText.includes('：')) && qText.split(/\s+/).length <= 5 && !qText.includes('ما');
                        if (!isAnalogy) {
                            qPassageText = currentPassage;
                            if (!qText.includes(currentPassage.slice(0, 20))) {
                                qText = '📄 ' + currentPassage + '\n\n❓ ' + qText;
                            }
                        }
                    }

                    const qNum = questions.length + 1;
                    let isWrong = false;
                    let correctAnswer = "";

                    // Find DOM card
                    const allCards = Array.from(document.querySelectorAll('.Qr7Oae, [role="listitem"]'));
                    const qSnippet = qText.slice(0, 20).trim();
                    let cardEl = allCards.find(c => (c.innerText || '').includes(qSnippet));
                    if (!cardEl && qNum - 1 < allCards.length) cardEl = allCards[qNum - 1];

                    if (cardEl) {
                        const cardText = (cardEl.innerText || '').trim();
                        if (/\b0\s*\/\s*[1-9]/.test(cardText) || /\b٠\s*\/\s*[١-٩]/.test(cardText) || /\b0\s*من\s+إجمالي/.test(cardText) || /غير صحيح|Incorrect/i.test(cardText)) {
                            isWrong = true;
                        }

                        const caMatch = cardText.match(/(?:الإجابة الصحيحة|الإجابات الصحيحة|الإجابة النموذجية|Correct answers?)\s*[:\n\-]?\s*([^\n]+)/i);
                        if (caMatch && caMatch[1].trim()) {
                            correctAnswer = cleanPrefix(caMatch[1].trim());
                            isWrong = true;
                        } else if (cardText.includes('الإجابة الصحيحة') || /correct answer/i.test(cardText)) {
                            const lines = cardText.split('\n').map(l => l.trim()).filter(Boolean);
                            const idx = lines.findIndex(l => /(?:الإجابة الصحيحة|Correct answers?)/i.test(l));
                            if (idx !== -1 && idx + 1 < lines.length) {
                                correctAnswer = cleanPrefix(lines[idx + 1]);
                                isWrong = true;
                            }
                        }

                        if (!correctAnswer && !isWrong && isViewScore) {
                            const checkedRadio = cardEl.querySelector('[aria-checked="true"]');
                            if (checkedRadio) {
                                const box = checkedRadio.closest('.docssharedWizToggleLabeledContainer, [role="radio"]') || checkedRadio.parentElement;
                                if (box) correctAnswer = cleanPrefix((box.innerText || '').split('\n')[0].trim());
                            }
                        }
                    }

                    if (isWrong) wrongIndices.push(qNum);

                    // Match correctAnswer into options
                    let matchedAns = options[0] || "";
                    if (correctAnswer) {
                        const found = options.find(o => cleanPrefix(o) === cleanPrefix(correctAnswer) || o === correctAnswer);
                        if (found) matchedAns = found;
                    }

                    questions.push({
                        question: qText,
                        options: options,
                        answer: matchedAns,
                        explanation: "",
                        passage_text: qPassageText
                    });
                }
            }
        }

        if (questions.length === 0) {
            alert("⚠️ تعذر العثور على أسئلة في هذه الصفحة. تأكد من فتح اختبار Google Forms.");
            return;
        }

        const quizData = {
            quiz_name: quizTitle,
            wrong: wrongIndices,
            questions: questions
        };

        const jsonStr = JSON.stringify(quizData, null, 2);
        const blob = new Blob([jsonStr], { type: "application/json;charset=utf-8" });
        const url = URL.createObjectURL(blob);
        const a = document.createElement("a");
        a.href = url;
        a.download = (quizTitle.replace(/[\/\\:*?"<>|]/g, '_') || 'quiz') + '.json';
        document.body.appendChild(a);
        a.click();
        document.body.removeChild(a);
        URL.revokeObjectURL(url);

        // Visual notification
        const banner = document.createElement('div');
        banner.style.cssText = 'position:fixed;top:20px;left:50%;transform:translateX(-50%);background:#10b981;color:#fff;padding:16px 24px;border-radius:12px;font-size:16px;font-weight:bold;z-index:999999;box-shadow:0 10px 25px rgba(0,0,0,0.3);direction:rtl;text-align:center;';
        banner.innerHTML = `✅ <b>تم استخراج ${questions.length} سؤال بنجاح!</b><br><span style="font-size:13px;font-weight:normal;">تم تحميل ملف JSON تلقائياً. أرسله الآن لبوت تيليجرام 🧠</span>`;
        document.body.appendChild(banner);
        setTimeout(() => banner.remove(), 4000);

    } catch (err) {
        alert("حدث خطأ أثناء الاستخراج: " + err.message);
    }
})();
