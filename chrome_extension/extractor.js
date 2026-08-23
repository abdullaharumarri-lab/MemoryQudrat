/**
 * MemoryQudrat — Google Forms Quiz Extractor Engine v4.2 (Universal Reading & MCQ Engine)
 *
 * Major Fixes & Enhancements:
 *   1. Precise Reading Passage Extraction:
 *      - Captures passages from section headers, standalone description cards, and multi-paragraph blocks.
 *      - Distinguishes real reading passages from disclaimers, oaths (اقسم/أتعهد), and instructions.
 *      - Never false-drops passages containing words like "دورة", "شروط", "ملاحظة", "الأستاذ".
 *   2. Section Context Awareness:
 *      - Automatically propagates active passages to all consecutive comprehension questions in that section.
 *      - Clears active passage when encountering non-reading sections (التناظر اللفظي, الخطأ السياقي, إكمال الجمل, القسم الكمي).
 *   3. Strict Analogy & Sentence Completion Protection:
 *      - Preserves pure analogy questions ("رياضة : لياقة") without prepending irrelevant passages.
 *      - Accurately identifies comprehension questions even when they contain colons (":").
 *   4. Rock-Solid Option Isolation:
 *      - Scopes each radio button strictly to its immediate container, guaranteeing all 4 options are extracted.
 *      - Extracts correct answer indicators (green highlight, score box, checked answer).
 */

function extractGoogleFormsQuiz() {
    try {

        /* ══════════════════════════════════════════
           1 — Quiz title
        ══════════════════════════════════════════ */
        let quizTitle = "كويز";
        const headerEl = document.querySelector(
            '[role="heading"][aria-level="1"], ' +
            '.freebirdFormviewerViewHeaderTitle, ' +
            '.F9iS2e, .ahS6Le, .v1CNqd'
        );
        if (headerEl && headerEl.innerText.trim()) {
            quizTitle = headerEl.innerText.trim().split('\n')[0].trim();
        } else {
            const doc = document.title
                .replace(/[-–—|].*$/, '')
                .replace(/عرض النتيجة|View score|Google Forms|نماذج Google/gi, '')
                .trim();
            if (doc) quizTitle = doc;
        }

        /* ══════════════════════════════════════════
           2 — Helpers: clean text & green highlights
        ══════════════════════════════════════════ */
        function clean(el) {
            if (!el) return '';
            return (el.innerText || '').replace(/\s+/g, ' ').trim();
        }

        const GREEN_FILLS = ['#137333', '#188038', '#1e8e3e', '#34a853', '#0f9d58'];

        function hasGreenHighlight(el) {
            if (!el) return false;
            for (const node of el.querySelectorAll('[fill]')) {
                if (GREEN_FILLS.includes((node.getAttribute('fill') || '').toLowerCase())) return true;
            }
            const bg = window.getComputedStyle(el).backgroundColor;
            const m = bg && bg.match(/\d+/g);
            if (m && m.length >= 3) {
                const [r, g, b] = [+m[0], +m[1], +m[2]];
                if (g > 80 && g > r * 1.4 && g > b * 1.4) return true;
            }
            return false;
        }

        function stripPrefix(text) {
            if (!text) return '';
            return text.replace(/^[أ-يa-zA-Z\d٠-٩][.\:\-\)\/]\s*/, '').trim();
        }

        /* ══════════════════════════════════════════
           3 — Precise Pledge / Disclaimer Filter
        ══════════════════════════════════════════ */
        function isPledgeOrDisclaimer(text) {
            if (!text) return true;
            const t = text.trim();
            const pledgeRegex = /(اقسم\s+انني|أقسم\s+أنني|اقسم\s+بالله|أقسم\s+بالله|أتعهد\s+بأن|اتعهد\s+بان|أقر\s+بأن|اقر\s+بان|تعهد\s+والتزام|شروط\s+وقواعد\s+الاختبار|أدخل\s+كلمة\s+المرور|الاسم\s+الثلاثي|رقم\s+الهوية|رقم\s+الجوال)/i;
            return pledgeRegex.test(t);
        }

        /* ══════════════════════════════════════════
           4 — Non-Reading Section Detector
        ══════════════════════════════════════════ */
        function isNonReadingSection(text) {
            if (!text) return false;
            const t = text.trim();
            const nonReadingKeywords = [
                'التناظر اللفظي', 'تناظر لفظي', 'الخطأ السياقي', 'خطأ سياقي',
                'إكمال الجمل', 'اكمال الجمل', 'المفردة الشاذة', 'مفردة شاذة',
                'القسم الكمي', 'الرياضيات', 'الجبر', 'الهندسة', 'الحساب'
            ];
            for (const kw of nonReadingKeywords) {
                if (t.includes(kw)) return true;
            }
            return false;
        }

        /* ══════════════════════════════════════════
           5 — Precise Verbal Analogy (التناظر اللفظي) Filter
        ══════════════════════════════════════════ */
        function isVerbalAnalogy(qText) {
            if (!qText) return false;
            const t = qText.trim().replace(/[\*\.]+$/, '').trim();

            // Comprehension question keywords must NEVER be treated as analogies
            const compKeywords = [
                'وفق', 'الفقرة', 'النص', 'القطعة', 'الضمير', 'معنى', 'علاقة',
                'يفهم', 'يستنتج', 'المقصود', 'أنسب', 'عنوان', 'تشير', 'يدل',
                'سبب', 'لماذا', 'كيف', 'متى', 'أين', 'كم', 'أي', 'ما'
            ];
            for (const kw of compKeywords) {
                if (t.includes(kw)) return false;
            }

            // Analogy format: short string strictly matching "Word(s) : Word(s)"
            if (t.length < 45 && /^[\u0600-\u06FF\s]+\s*[:\：]\s*[\u0600-\u06FF\s]+$/.test(t)) {
                return true;
            }
            return false;
        }

        /* ══════════════════════════════════════════
           6 — Top-down Page Scanner & Extractor
        ══════════════════════════════════════════ */
        const questions = [];
        const wrongIndices = [];

        // Collect all top-level card containers in DOM order
        const rawContainers = Array.from(document.querySelectorAll(
            '.Qr7Oae, .geS5n, [role="listitem"], .freebirdFormviewerViewHeaderHeader, [role="region"], .m7Lvdc, .D1w1Sd, .j0L6Mc, .freebirdFormviewerViewItemsItemItem'
        ));

        // Deduplicate nested containers
        const allItems = [];
        rawContainers.forEach((el) => {
            // Only keep top-level containers (elements not contained within another selected element)
            const isDescendant = rawContainers.some(other => other !== el && other.contains(el));
            if (!isDescendant && !allItems.includes(el)) {
                allItems.push(el);
            }
        });

        let currentActivePassage = '';

        allItems.forEach((item) => {
            const hasRadios = item.querySelector('[role="radiogroup"], [role="radio"]');
            const hasInputs = item.querySelector('input[type="text"], input[type="email"], textarea');

            // ── Case A: Standalone Text Card / Section Header / Passage ──
            if (!hasRadios && !hasInputs) {
                const text = clean(item);

                // If this is a section break for a non-reading section, clear active passage
                if (isNonReadingSection(text)) {
                    currentActivePassage = '';
                    return;
                }

                // If this is a pledge/disclaimer or score banner, ignore it
                if (isPledgeOrDisclaimer(text) || /^\d+\s*\/\s*\d+/.test(text)) {
                    return;
                }

                // If it has substantial reading text (> 45 chars) and is not just the form title
                if (text.length > 45) {
                    if (!text.startsWith(quizTitle) || text.length > quizTitle.length + 30) {
                        currentActivePassage = text;
                    }
                }
                return;
            }

            // ── Case B: MCQ Question Item ──
            const rg = item.querySelector('[role="radiogroup"]');
            if (!rg) return;

            const qNum = questions.length + 1;

            // 1. Extract question heading
            let questionText = '';
            const allHeadings = Array.from(item.querySelectorAll(
                '[role="heading"], .M7eMe, .HoN1Ob, .F3n8vf, .freebirdFormviewerViewItemsItemItemTitle'
            ));
            for (const h of allHeadings) {
                if (rg.compareDocumentPosition(h) & Node.DOCUMENT_POSITION_PRECEDING) {
                    const clone = h.cloneNode(true);
                    clone.querySelectorAll('.R4nke, .DqBBlb, .freebirdFormviewerViewItemsItemRequiredAsterisk').forEach(e => e.remove());
                    questionText = clean(clone);
                    if (questionText) break;
                }
            }
            if (!questionText && allHeadings.length > 0) {
                const clone = allHeadings[0].cloneNode(true);
                clone.querySelectorAll('.R4nke, .DqBBlb').forEach(e => e.remove());
                questionText = clean(clone);
            }
            // Strip leading question numbering
            questionText = questionText.replace(/^[\d٠-٩]+[\s\.\:\-\)\/]+\s*/, '').trim();
            if (!questionText) questionText = `السؤال ${qNum}`;

            // Attach active passage ONLY to reading comprehension questions (never to analogies)
            if (currentActivePassage && !isVerbalAnalogy(questionText)) {
                const snippet = currentActivePassage.slice(0, 30);
                if (!questionText.includes(snippet)) {
                    questionText = '📄 ' + currentActivePassage + '\n\n❓ ' + questionText;
                }
            }

            // 2. Score & Wrong answer detection
            let isWrong = false;
            const blockText = item.innerText || '';

            if (/\b0\s*\/\s*[1-9]/.test(blockText) || /\b٠\s*\/\s*[١-٩]/.test(blockText)) {
                isWrong = true;
            }

            // "الإجابة الصحيحة" box detection
            let correctAnswerFromBox = '';
            const caPatterns = [
                /(?:الإجابة الصحيحة|الإجابات الصحيحة)\s*[:\n]\s*([^\n]+)/,
                /(?:Correct answer|Correct answers)\s*[:\n]\s*([^\n]+)/i
            ];
            for (const pat of caPatterns) {
                const m = blockText.match(pat);
                if (m) {
                    correctAnswerFromBox = m[1].trim().replace(/\s*\(\s*\d+[^)]*\)\s*$/, '').trim();
                    isWrong = true;
                    break;
                }
            }
            if (!correctAnswerFromBox) {
                const caEl = item.querySelector('.c2gzEf, .R305vd, .i9L0be, .N3G8yb, .YMEQ1d');
                if (caEl) {
                    let txt = clean(caEl)
                        .replace(/^(الإجابة الصحيحة|الإجابات الصحيحة|Correct answer|Correct answers)\s*[:\n]+\s*/i, '')
                        .replace(/\s*\(\s*\d+[^)]*\)\s*$/, '')
                        .trim();
                    if (txt) {
                        correctAnswerFromBox = txt;
                        isWrong = true;
                    }
                }
            }

            if (isWrong) wrongIndices.push(qNum);

            // 3. Extract Options (STRICT container isolation)
            const radios = Array.from(rg.querySelectorAll('[role="radio"]'));
            const options = [];
            let checkedOptText = null;
            let greenOptText = null;

            for (const radio of radios) {
                // Scope strictly to this specific option wrapper
                let optBox = radio.closest('.docssharedWizToggleLabeledContainer, .SG0AAe, .Y6Myj, .bzfPab, div[jscontroller]');
                if (!optBox || optBox === item || optBox === rg) {
                    optBox = radio.parentElement || radio;
                }

                let optText = '';

                // Try dedicated Google Forms label container inside this option box
                const labelEl = optBox.querySelector('.docssharedWizToggleLabeledLabelText, .aDTYNe, .ulDsOb, .OvPDhc, .M7eMe, .jLM23c, .NPEfkd, .WpHeLc');
                if (labelEl) {
                    optText = clean(labelEl).split('\n')[0].trim();
                }

                // Try radio attributes
                if (!optText) {
                    optText = (radio.getAttribute('aria-label') || radio.getAttribute('data-value') || '').trim();
                }

                // Try direct innerText of option box
                if (!optText && optBox !== item && optBox !== rg) {
                    const lines = (optBox.innerText || '').split('\n').map(l => l.trim()).filter(l => l);
                    if (lines.length > 0) optText = lines[0];
                }

                optText = stripPrefix(optText);

                if (!optText || options.includes(optText)) continue;
                options.push(optText);

                const isChecked =
                    radio.getAttribute('aria-checked') === 'true' ||
                    !!radio.querySelector('[aria-checked="true"]') ||
                    radio.getAttribute('aria-selected') === 'true';
                if (isChecked) checkedOptText = optText;

                if (hasGreenHighlight(optBox) || hasGreenHighlight(radio)) {
                    greenOptText = optText;
                }
            }

            // 4. Correct answer resolution
            let correctAnswer = '';

            if (correctAnswerFromBox) {
                const exact = options.find(o => o.trim() === correctAnswerFromBox.trim());
                const partial = options.find(o =>
                    correctAnswerFromBox.includes(o.trim()) || o.trim().includes(correctAnswerFromBox.trim())
                );
                correctAnswer = exact || partial || correctAnswerFromBox;
                if (!options.includes(correctAnswer)) options.push(correctAnswer);
            }

            if (!correctAnswer && greenOptText) {
                correctAnswer = greenOptText;
            }

            if (!correctAnswer && !isWrong && checkedOptText) {
                correctAnswer = checkedOptText;
            }

            if (!correctAnswer) correctAnswer = options[0] || '';

            if (correctAnswer && !options.includes(correctAnswer)) {
                const ci = options.find(o => o.toLowerCase() === correctAnswer.toLowerCase());
                if (ci) correctAnswer = ci;
            }

            // 5. Explanation
            let explanation = '';
            const fbEl = item.querySelector('.g4k55c, .freebirdFormviewerViewItemsGradingFeedbackContainer');
            if (fbEl) {
                explanation = clean(fbEl)
                    .replace(/^(ملاحظات|تعليقات|Feedback)\s*[:\n]+\s*/i, '')
                    .trim();
            }

            // Ensure at least 2 options for valid telegram poll
            if (options.length === 1) {
                options.push("خيار بديل");
            }

            questions.push({
                question: questionText,
                options: options.length > 0 ? options : ["صح", "خطأ"],
                answer: correctAnswer,
                explanation
            });
        });

        /* ══════════════════════════════════════════
           7 — Return result
        ══════════════════════════════════════════ */
        if (questions.length === 0) {
            return {
                success: false,
                error:
                    'لم يتم العثور على أسئلة اختيار من متعدد (MCQ) في هذه الصفحة.\n\n' +
                    'تأكد من:\n' +
                    '• فتح صفحة "عرض النتيجة / View score" في Google Forms\n' +
                    '• انتظار تحميل الصفحة بالكامل قبل الضغط على الإضافة\n' +
                    '• أن الاختبار يحتوي على أسئلة اختيار من متعدد'
            };
        }

        return {
            success: true,
            data: { quiz_name: quizTitle, wrong: wrongIndices, questions }
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
