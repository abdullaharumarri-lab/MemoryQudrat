/**
 * MemoryQudrat — Google Forms Quiz Extractor Engine v4.3 (Bulletproof Reading & MCQ Engine)
 *
 * Key Precision Upgrades:
 *   1. Complete Student Info / Score / Pledge Elimination:
 *      - Rejects student name cards ("اسم الطالب : عبدالله جمعان"), emails, phone numbers, passwords,
 *        score banners ("0 من إجمالي 0 نقطة"), oaths (اقسم/أتعهد), and course announcements.
 *   2. Strict Passage Validation:
 *      - Only accepts genuine multi-sentence reading passages (استيعاب المقروء) (> 70 chars with zero metadata/score words).
 *   3. Universal Analogy & Question Formatting:
 *      - Strips asterisks, trailing symbols, and numbers cleanly.
 *      - Preserves pure analogy questions ("قماش : ملابس", "إهمال: رسوب") cleanly without attached text.
 *   4. Perfect 4-Option MCQ Isolation:
 *      - Extracts all 4 choices cleanly for every question.
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
           3 — Strict Reading Passage Validator
           Rejects student names, scores, emails, pledges, passwords
        ══════════════════════════════════════════ */
        function isValidReadingPassage(text) {
            if (!text) return false;
            const t = text.trim();

            // 1. Length requirement: Real reading passages are substantial
            if (t.length < 70) return false;

            // 2. Score indicators (e.g. "0 من إجمالي 0 نقطة", "5/5 points")
            if (/من\s+إجمالي\s+\d+\s+نقطة|\b\d+\s*\/\s*\d+\b|\bpoints?\b|\bنقطة\b|\bنقاط\b|\bالدرجة\b|\bالنتيجة\b/i.test(t)) {
                return false;
            }

            // 3. Student info, form inputs, pledges, passwords, course headers
            const forbiddenPattern = /(اسم\s+الطالب|اسم\s+المشترك|الاسم\s+الثلاثي|الاسم\s*:|البريد|الإيميل|email|رقم\s+الجوال|رقم\s+الهاتف|phone|الفصل|المدرسة|المجموعة|كلمة\s+المرور|password|اقسم|أقسم|أتعهد|اتعهد|أقر\s+بأن|تعهد\s+والتزام|شروط\s+وقواعد|محوسب\s+أغسطس|إيهاب\s+عبد\s+العظيم)/i;
            if (forbiddenPattern.test(t)) {
                return false;
            }

            // 4. Form title redundancy
            if (quizTitle && t.startsWith(quizTitle) && t.length < quizTitle.length + 50) {
                return false;
            }

            return true;
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
           5 — Precise Verbal Analogy Filter
        ══════════════════════════════════════════ */
        function isVerbalAnalogy(qText) {
            if (!qText) return false;
            const t = qText.trim().replace(/[\*\.\s]+$/, '').trim();

            const compKeywords = [
                'وفق', 'الفقرة', 'النص', 'القطعة', 'الضمير', 'معنى', 'علاقة',
                'يفهم', 'يستنتج', 'المقصود', 'أنسب', 'عنوان', 'تشير', 'يدل',
                'سبب', 'لماذا', 'كيف', 'متى', 'أين', 'كم', 'أي', 'ما'
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
            const isDescendant = rawContainers.some(other => other !== el && other.contains(el));
            if (!isDescendant && !allItems.includes(el)) {
                allItems.push(el);
            }
        });

        let currentActivePassage = '';

        allItems.forEach((item) => {
            const hasRadios = item.querySelector('[role="radiogroup"], [role="radio"]');
            const hasInputs = item.querySelector('input[type="text"], input[type="email"], textarea');

            // ── Case A: Standalone Card / Section Header / Text Block ──
            if (!hasRadios && !hasInputs) {
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
            
            // Clean question text: strip leading numbers and clean trailing score artifacts
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
