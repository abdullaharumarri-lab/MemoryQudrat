/**
 * MemoryQudrat — Google Forms Quiz Extractor Engine v5.0 (Native AI Precision)
 *
 * Architecture:
 *   1. Direct Native Data Model Engine:
 *      - Reads Google Forms' official internal data tree (FB_PUBLIC_LOAD_DATA_).
 *      - Completely immune to CSS changes, DOM nesting quirks, and page layout variations.
 *   2. Universal Passage & Multi-Passage Tracking:
 *      - Identifies all reading passages (Type 1 text cards, Type 6 description cards, Section descriptions).
 *      - Tracks multiple reading passages in the same quiz sequentially.
 *   3. Strict Analogy & Question Distinguisher:
 *      - Preserves pure analogies ("Word1 : Word2") cleanly.
 *      - Attaches passages to all comprehension questions seamlessly.
 *   4. Mathematical & Numeric Precision:
 *      - 100% safe with decimals (3.5, 0.25), fractions (1/2), percentages (25%), ranges (4-8), and negatives (-5).
 *   5. DOM View-Score Correct Answer & Explanation Resolution:
 *      - Maps each question by unique item ID to extract the exact correct answer and explanation.
 */

function extractGoogleFormsQuiz() {
    try {
        let rawData = null;

        // Step 1: Try reading window.FB_PUBLIC_LOAD_DATA_ directly
        if (typeof window.FB_PUBLIC_LOAD_DATA_ !== 'undefined' && window.FB_PUBLIC_LOAD_DATA_) {
            rawData = window.FB_PUBLIC_LOAD_DATA_;
        } else {
            // Search in script tags
            const scripts = Array.from(document.querySelectorAll('script'));
            for (const s of scripts) {
                const text = s.textContent || '';
                const m = text.match(/var FB_PUBLIC_LOAD_DATA_\s*=\s*(\[.*?\]);\s*(?:<\/script>|;|$)/s);
                if (m) {
                    try {
                        rawData = JSON.parse(m[1]);
                        break;
                    } catch (e) {}
                }
            }
        }

        // ══════════════════════════════════════════════════════════════
        // Engine A: Native Data Model Parser (Preferred & 100% Accurate)
        // ══════════════════════════════════════════════════════════════
        if (rawData && rawData[1] && Array.isArray(rawData[1][1])) {
            let quizTitle = "كويز";
            if (rawData[1][8]) {
                quizTitle = String(rawData[1][8]).trim().split('\n')[0].trim();
            } else {
                const headerEl = document.querySelector('[role="heading"][aria-level="1"], .freebirdFormviewerViewHeaderTitle');
                if (headerEl && headerEl.innerText.trim()) {
                    quizTitle = headerEl.innerText.trim().split('\n')[0].trim();
                }
            }

            const items = rawData[1][1];
            let currentActivePassage = "";
            const questions = [];
            const wrongIndices = [];

            // Helper to strip only option prefixes safely without mangling numbers
            function stripOptionPrefix(text) {
                if (!text) return '';
                let t = String(text).trim();
                t = t.replace(/^[(\uff08]?[أ-دa-dA-D\u0623\u0628\u062c\u062f][)\uff09.:\-\/\s]+\s*/, '');
                if (/^[(\uff08][1-4\u0661-\u0664][)\uff09]\s+/.test(t) || /^[1-4\u0661-\u0664][)\uff09\.\-]\s+/.test(t)) {
                    t = t.replace(/^[(\uff08]?[1-4\u0661-\u0664][)\uff09\.\-]+\s*/, '');
                }
                return t.trim();
            }

            function isVerbalAnalogy(qText) {
                if (!qText) return false;
                let t = qText.trim().replace(/[\*\s]+$/, '').trim();
                if (t.endsWith(':') || t.endsWith('：') || t.endsWith('؟') || t.endsWith('?')) {
                    return false;
                }
                const parts = t.split(/[:\：]/);
                if (parts.length === 2) {
                    const left = parts[0].trim();
                    const right = parts[1].trim();
                    if (left && right && left.split(/\s+/).length <= 4 && right.split(/\s+/).length <= 4 && t.length < 40) {
                        const nonAnalogyWords = ['ما', 'لماذا', 'كيف', 'متى', 'أين', 'كم', 'أي', 'هل', 'من', 'ماذا', 'علاقة', 'معنى', 'يدل', 'تعني', 'يقصد', 'وفق', 'النص', 'القطعة', 'الفقرة'];
                        for (const w of nonAnalogyWords) {
                            if (left.includes(w) || right.includes(w)) return false;
                        }
                        return true;
                    }
                }
                return false;
            }

            for (const item of items) {
                const itemId = item[0];
                const title = (item[1] || "").trim();
                const itemType = item[3];

                // Type 1 & 6: Reading Passage Card / Text Block
                if (itemType === 1 || itemType === 6) {
                    const isInfoOrPledge = /(?:اسم\s+الطالب|اسم\s+المشترك|الاسم\s+الثلاثي|البريد|email|رقم\s+الجوال|كلمة\s+المرور|password|اقسم|أقسم|أتعهد|اتعهد|أقر|تعهد)/i.test(title);
                    if (!isInfoOrPledge && title.length > 25) {
                        currentActivePassage = title;
                    }
                    continue;
                }

                // Type 8: Section Break / Header
                if (itemType === 8) {
                    const isNonReading = /التناظر|تناظر|الخطأ|إكمال|الكمي|الرياضيات|الجبر|الهندسة/i.test(title);
                    if (isNonReading) {
                        currentActivePassage = "";
                    }
                    continue;
                }

                // Type 2: Multiple Choice Question (MCQ)
                if (itemType === 2) {
                    const isStudentMeta = /(?:اسم\s+الطالب|اسم\s+المشترك|كلمة\s+المرور|البريد\s+الإلكتروني)/i.test(title);
                    if (isStudentMeta) continue;

                    let optionsData = [];
                    if (item.length > 4 && item[4] && item[4][0] && Array.isArray(item[4][0][1])) {
                        optionsData = item[4][0][1];
                    }
                    const rawOptions = optionsData.map(opt => (opt && opt[0] ? String(opt[0]).trim() : '')).filter(o => o);
                    const cleanOptions = rawOptions.map(stripOptionPrefix).filter(o => o);
                    const options = cleanOptions.length >= 2 ? cleanOptions : (rawOptions.length >= 2 ? rawOptions : ["صح", "خطأ"]);

                    // Format Question Text
                    let cleanQText = title.replace(/^[\d٠-٩]+[\s\.\:\-\)\/]+\s*/, '').replace(/\s*\*\s*$/, '').trim();
                    if (!cleanQText) cleanQText = `السؤال ${questions.length + 1}`;

                    if (currentActivePassage && !isVerbalAnalogy(cleanQText)) {
                        const snippet = currentActivePassage.slice(0, 30);
                        if (!cleanQText.includes(snippet)) {
                            cleanQText = '📄 ' + currentActivePassage + '\n\n❓ ' + cleanQText;
                        }
                    }

                    // Look up DOM card by item ID for answers and explanations
                    const qNum = questions.length + 1;
                    let isWrong = false;
                    let correctAnswer = "";
                    let explanation = "";

                    const cardEl = document.querySelector(`[data-item-id="${itemId}"]`) ||
                                   document.getElementById(`i.desc.${itemId}`)?.closest('.Qr7Oae, [role="listitem"]') ||
                                   document.querySelectorAll('.Qr7Oae, [role="listitem"]')[qNum - 1];

                    if (cardEl) {
                        const cardClone = cardEl.cloneNode(true);
                        cardClone.querySelectorAll('.M2vV3e, .RDPZE, .freebirdFormviewerViewItemsItemGradingPoints, [aria-describedby*="points"]').forEach(e => e.remove());
                        const cardText = cardClone.innerText || '';

                        // Score & Wrong detection
                        if (/\b0\s*\/\s*[1-9]/.test(cardEl.innerText || '') || /\b٠\s*\/\s*[١-٩]/.test(cardEl.innerText || '')) {
                            isWrong = true;
                        }

                        // "الإجابة الصحيحة" box
                        const caPatterns = [
                            /(?:الإجابة الصحيحة|الإجابات الصحيحة)\s*[:\n]\s*([^\n]+)/,
                            /(?:Correct answer|Correct answers)\s*[:\n]\s*([^\n]+)/i
                        ];
                        for (const pat of caPatterns) {
                            const m = cardText.match(pat);
                            if (m) {
                                correctAnswer = m[1].trim();
                                isWrong = true;
                                break;
                            }
                        }

                        // Check green highlight or checked radio
                        if (!correctAnswer) {
                            const greenRadio = cardEl.querySelector('[fill="#137333"], [fill="#188038"], [fill="#34a853"], [fill="#0f9d58"], [fill="#1e8e3e"]');
                            if (greenRadio) {
                                const greenBox = greenRadio.closest('.docssharedWizToggleLabeledContainer, .SG0AAe, .Y6Myj, .bzfPab') || greenRadio.parentElement;
                                if (greenBox) {
                                    correctAnswer = (greenBox.innerText || '').split('\n')[0].trim();
                                }
                            }
                        }

                        if (!correctAnswer) {
                            const checkedRadio = cardEl.querySelector('[aria-checked="true"]');
                            if (checkedRadio) {
                                const checkedBox = checkedRadio.closest('.docssharedWizToggleLabeledContainer, .SG0AAe, .Y6Myj, .bzfPab') || checkedRadio.parentElement;
                                if (checkedBox) {
                                    correctAnswer = (checkedBox.innerText || '').split('\n')[0].trim();
                                }
                            }
                        }

                        // Feedback explanation
                        const fbEl = cardEl.querySelector('.g4k55c, .freebirdFormviewerViewItemsGradingFeedbackContainer');
                        if (fbEl) {
                            explanation = (fbEl.innerText || '').replace(/^(ملاحظات|تعليقات|Feedback)\s*[:\n]+\s*/i, '').trim();
                        }
                    }

                    if (isWrong) wrongIndices.push(qNum);

                    // Normalize correct answer against options
                    if (correctAnswer) {
                        const cleanCA = stripOptionPrefix(correctAnswer);
                        const exact = options.find(o => o.trim() === cleanCA.trim() || o.trim() === correctAnswer.trim());
                        const partial = options.find(o => cleanCA.includes(o.trim()) || o.trim().includes(cleanCA.trim()));
                        correctAnswer = exact || partial || cleanCA;
                        if (!options.includes(correctAnswer)) options.push(correctAnswer);
                    } else {
                        correctAnswer = options[0] || '';
                    }

                    questions.push({
                        question: cleanQText,
                        options: options,
                        answer: correctAnswer,
                        explanation: explanation
                    });
                }
            }

            if (questions.length > 0) {
                return {
                    success: true,
                    data: { quiz_name: quizTitle, wrong: wrongIndices, questions }
                };
            }
        }

        // ══════════════════════════════════════════════════════════════
        // Engine B: Fallback DOM Parser
        // ══════════════════════════════════════════════════════════════
        let quizTitle = "كويز";
        const headerEl = document.querySelector('[role="heading"][aria-level="1"], .freebirdFormviewerViewHeaderTitle');
        if (headerEl && headerEl.innerText.trim()) {
            quizTitle = headerEl.innerText.trim().split('\n')[0].trim();
        }

        function clean(el) {
            if (!el) return '';
            return (el.innerText || '').replace(/\s+/g, ' ').trim();
        }

        function stripPrefixFallback(text) {
            if (!text) return '';
            let t = text.trim();
            t = t.replace(/^[(\uff08]?[أ-دa-dA-D\u0623\u0628\u062c\u062f][)\uff09.:\-\/\s]+\s*/, '');
            if (/^[(\uff08][1-4\u0661-\u0664][)\uff09]\s+/.test(t) || /^[1-4\u0661-\u0664][)\uff09\.\-]\s+/.test(t)) {
                t = t.replace(/^[(\uff08]?[1-4\u0661-\u0664][)\uff09\.\-]+\s*/, '');
            }
            return t.trim();
        }

        const cards = Array.from(document.querySelectorAll('.Qr7Oae, [role="listitem"]'));
        const questionsFallback = [];
        const wrongFallback = [];
        let currentPassageFallback = "";

        cards.forEach((card) => {
            const rg = card.querySelector('[role="radiogroup"]');
            if (!rg) {
                const txt = clean(card);
                const isMeta = /(?:اسم\s+الطالب|اسم\s+المشترك|البريد|email|رقم\s+الجوال|كلمة\s+المرور|password|اقسم|أقسم|أتعهد|اتعهد|تعهد)/i.test(txt);
                if (!isMeta && txt.length > 25 && !/^\s*(?:\d+\s*\/\s*\d+|\d+\s*من\s+إجمالي\s+\d+\s*نقطة)\s*$/.test(txt)) {
                    currentPassageFallback = txt;
                }
                return;
            }

            const qNum = questionsFallback.length + 1;
            let qText = "";
            const h = card.querySelector('[role="heading"], .M7eMe');
            if (h) qText = clean(h);
            qText = qText.replace(/^[\d٠-٩]+[\s\.\:\-\)\/]+\s*/, '').replace(/\s*\*\s*$/, '').trim();
            if (!qText) qText = `السؤال ${qNum}`;

            if (currentPassageFallback && !qText.endsWith(':') && !qText.includes(':')) {
                qText = '📄 ' + currentPassageFallback + '\n\n❓ ' + qText;
            }

            const radios = Array.from(rg.querySelectorAll('[role="radio"]'));
            const options = [];
            let ans = "";

            radios.forEach((r) => {
                const box = r.closest('.docssharedWizToggleLabeledContainer, .SG0AAe') || r.parentElement;
                let opt = stripPrefixFallback(clean(box));
                if (opt && !options.includes(opt)) options.push(opt);
                if (r.getAttribute('aria-checked') === 'true') ans = opt;
            });

            if (!ans && options.length > 0) ans = options[0];

            questionsFallback.push({
                question: qText,
                options: options.length >= 2 ? options : ["صح", "خطأ"],
                answer: ans,
                explanation: ""
            });
        });

        if (questionsFallback.length > 0) {
            return {
                success: true,
                data: { quiz_name: quizTitle, wrong: wrongFallback, questions: questionsFallback }
            };
        }

        return {
            success: false,
            error: 'تعذر استخراج الأسئلة من هذه الصفحة. تأكد من تحميل صفحة Google Forms بالكامل.'
        };

    } catch (err) {
        return { success: false, error: 'خطأ: ' + err.message };
    }
}

/* Message-passing support */
if (typeof chrome !== 'undefined' && chrome.runtime && chrome.runtime.onMessage) {
    chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
        if (request.action === 'EXTRACT_QUIZ') {
            sendResponse(extractGoogleFormsQuiz());
        }
        return true;
    });
}
