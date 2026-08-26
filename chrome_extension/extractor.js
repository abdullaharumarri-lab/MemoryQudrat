/**
 * MemoryQudrat — Google Forms Quiz Extractor Engine v4.4 (Master Edition)
 *
 * Core Fixes:
 *   1. Precise Reading Passage & Text Extraction:
 *      - Captures passages regardless of card type (Section description, Standalone text, or Short Answer text).
 *      - Eliminates false-positive score/keyword exclusions (allows normal words like نقطة, درجة, النتيجة, fractions).
 *      - Strictly ignores student info and pledge cards only.
 *   2. Bulletproof Number & Math Option Handling:
 *      - Strips only choice identifiers (أ, ب, ج, د, A, B, C, D, 1), (1)) without touching numbers.
 *      - Completely protects decimals (3.5, 0.25), fractions (1/2, 3/4), ranges (4-8), percentages (25%), and negatives (-5).
 *   3. Intelligent Section & Passage Propagation:
 *      - Propagates reading passages to comprehension questions seamlessly.
 *      - Preserves pure analogy, sentence completion, and odd-one-out questions cleanly without attached text.
 *   4. Rock-Solid Option Isolation:
 *      - Extracts all 4 options accurately with correct answers and explanations.
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

        /* ══════════════════════════════════════════
           3 — Clean Prefix Filter (Math & Number Safe)
           Strips only option labels (أ, ب, ج, د, A, B, C, D)
           Preserves 3.5, 1/2, 0.25, -5, 4-8 perfectly!
        ══════════════════════════════════════════ */
        function stripPrefix(text) {
            if (!text) return '';
            let t = text.trim();
            // Match (أ), أ), أ -, أ:, أ. , (A), A), A.
            t = t.replace(/^[(\uff08]?[أ-دa-dA-D\u0623\u0628\u062c\u062f][)\uff09.:\-\/\s]+\s*/, '');
            // Match (1), 1), (2), 2)
            t = t.replace(/^[(\uff08][\d\u0660-\u0669]+[)\uff09]\s*/, '');
            t = t.replace(/^[1-4\u0661-\u0664][)\uff09]\s*/, '');
            return t.trim();
        }

        /* ══════════════════════════════════════════
           4 — Student Info & Pledge Filter
        ══════════════════════════════════════════ */
        function isStudentOrPledge(text) {
            if (!text) return true;
            const t = text.trim();
            const pattern = /(?:اسم\s+الطالب|اسم\s+المشترك|الاسم\s+الثلاثي|البريد\s+الإلكتروني|email|رقم\s+الجوال|رقم\s+الهاتف|phone|الفصل|المدرسة|المجموعة|كلمة\s+المرور|password|اقسم\s+انني|أقسم\s+بالله|أتعهد\s+بأن|اتعهد\s+بان|أقر\s+بأن|تعهد\s+والتزام|شروط\s+وقواعد\s+الاختبار)/i;
            return pattern.test(t);
        }

        function isScoreOnly(text) {
            if (!text) return true;
            const t = text.trim();
            return /^\s*(?:\d+\s*\/\s*\d+|\d+\s*من\s+إجمالي\s+\d+\s*نقطة|\d+\s*من\s+\d+\s*نقطة)\s*$/.test(t);
        }

        function isValidReadingPassage(text) {
            if (!text) return false;
            const t = text.trim();
            if (t.length < 60) return false;
            if (isStudentOrPledge(t)) return false;
            if (isScoreOnly(t)) return false;
            if (quizTitle && t.startsWith(quizTitle) && t.length < quizTitle.length + 50) {
                return false;
            }
            return true;
        }

        /* ══════════════════════════════════════════
           5 — Non-Reading Section Detector
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
           6 — Precise Verbal Analogy Filter
        ══════════════════════════════════════════ */
        function isVerbalAnalogy(qText) {
            if (!qText) return false;
            const t = qText.trim().replace(/[\*\.\s]+$/, '').trim();

            const compKeywords = [
                'وفق', 'الفقرة', 'النص', 'القطعة', 'الضمير', 'معنى', 'علاقة',
                'يفهم', 'يستنتج', 'المقصود', 'أنسب', 'عنوان', 'تشير', 'يدل',
                'سبب', 'لماذا', 'كيف', 'متى', 'أين', 'كم', 'أي', 'ما', 'مضمون'
            ];
            for (const kw of compKeywords) {
                if (t.includes(kw)) return false;
            }

            if (t.length < 50 && /^[\u0600-\u06FF\s]+\s*[:\：]\s*[\u0600-\u06FF\s]+$/.test(t)) {
                return true;
            }
            return false;
        }

        /* ══════════════════════════════════════════
           7 — Top-down Page Scanner & Extractor
        ══════════════════════════════════════════ */
        const questions = [];
        const wrongIndices = [];

        // Collect all top-level card containers in DOM order
        const allItems = Array.from(document.querySelectorAll('.Qr7Oae, [role="listitem"]'));
        
        // Fallback if Qr7Oae / listitem not found
        if (allItems.length === 0) {
            allItems.push(...Array.from(document.querySelectorAll('.geS5n, .freebirdFormviewerViewItemsItemItem')));
        }

        // Deduplicate nested containers
        const topContainers = [];
        allItems.forEach((el) => {
            const isDescendant = allItems.some(other => other !== el && other.contains(el));
            if (!isDescendant && !topContainers.includes(el)) {
                topContainers.push(el);
            }
        });

        let currentActivePassage = '';

        topContainers.forEach((item) => {
            const rg = item.querySelector('[role="radiogroup"]');
            const hasRadios = !!rg || !!item.querySelector('[role="radio"]');

            // ── Case A: Standalone Card / Section Header / Passage ──
            if (!hasRadios) {
                const text = clean(item);

                // Clear active passage if non-reading section begins
                if (isNonReadingSection(text)) {
                    currentActivePassage = '';
                    return;
                }

                // If this is a valid reading passage, store it
                if (isValidReadingPassage(text)) {
                    currentActivePassage = text;
                }
                return;
            }

            // ── Case B: MCQ Question Item ──
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

            // Clean question text: strip leading numbers and asterisks
            questionText = questionText.replace(/^[\d٠-٩]+[\s\.\:\-\)\/]+\s*/, '').trim();
            questionText = questionText.replace(/\s*\*\s*$/, '').trim();
            if (!questionText) questionText = `السؤال ${qNum}`;

            // Attach active passage ONLY to comprehension questions (never analogies)
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
                const cleanedBoxAns = stripPrefix(correctAnswerFromBox);
                const exact = options.find(o => o.trim() === cleanedBoxAns.trim() || o.trim() === correctAnswerFromBox.trim());
                const partial = options.find(o =>
                    cleanedBoxAns.includes(o.trim()) || o.trim().includes(cleanedBoxAns.trim())
                );
                correctAnswer = exact || partial || cleanedBoxAns;
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
           8 — Return result
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
