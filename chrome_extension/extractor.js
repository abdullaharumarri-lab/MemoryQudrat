/**
 * MemoryQudrat — Google Forms Quiz Extractor Engine v2.0
 *
 * Strategy:
 *  - Relies on ARIA roles and semantic HTML (never on obfuscated CSS class names for correctness logic)
 *  - Detects question type: only processes MCQ (radio / checkbox), skips text / short-answer fields
 *  - Correct answer detection priority:
 *      1. Explicit "الإجابة الصحيحة" / "Correct answer" label box  (shown only for wrong answers)
 *      2. Option whose ancestor has a green computed background or a green SVG fill
 *      3. The checked option when the student answered correctly (score NOT 0/N)
 *      4. Graceful fallback with a debug flag
 */

function extractGoogleFormsQuiz() {
    try {

        /* ══════════════════════════════════════════════════════
           STEP 1 — Quiz title
        ══════════════════════════════════════════════════════ */
        let quizTitle = "كويز";

        // Try the prominent header heading first
        const headerEl = document.querySelector(
            '[role="heading"][aria-level="1"], ' +
            '.freebirdFormviewerViewHeaderTitle, ' +
            '.F9iS2e, .ahS6Le, .v1CNqd, h1'
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

        /* ══════════════════════════════════════════════════════
           STEP 2 — Locate question containers
        ══════════════════════════════════════════════════════ */

        // Google Forms renders each question as a role="listitem" inside role="list"
        let containers = Array.from(document.querySelectorAll(
            '[role="listitem"], .Qr7Oae, .geS5n, .freebirdFormviewerViewItemsItemItem'
        ));

        // De-duplicate (some selectors overlap)
        containers = Array.from(new Set(containers));

        // Keep only MULTIPLE-CHOICE containers:
        //   • Must contain at least one role="radio" or role="checkbox"
        //   • Must NOT be a pure text/short-answer field (has an <input type="text"> or <textarea>)
        containers = containers.filter(el => {
            const hasMCQ = el.querySelector('[role="radio"], [role="checkbox"]') !== null;
            const hasTextInput = el.querySelector(
                'input[type="text"], input[type="email"], input[type="number"], ' +
                'textarea, .quantumWizTextinputPaperinputInput, .whsOnd, .exportInput'
            ) !== null;
            return hasMCQ && !hasTextInput;
        });

        // Emergency fallback: locate by radiogroup parents
        if (containers.length === 0) {
            const groups = document.querySelectorAll('[role="radiogroup"], [role="group"]');
            const parents = Array.from(groups).map(g =>
                g.closest('.Qr7Oae') || g.closest('[role="listitem"]') || g.parentElement
            ).filter(Boolean);
            containers = Array.from(new Set(parents)).filter(el =>
                el.querySelector('[role="radio"], [role="checkbox"]') !== null &&
                el.querySelector('input[type="text"], textarea') === null
            );
        }

        /* ══════════════════════════════════════════════════════
           STEP 3 — Helpers
        ══════════════════════════════════════════════════════ */

        /**
         * Returns true if the element (or any child) has a green background or fill.
         * We check computed style AND inline SVG fill attributes.
         */
        function isGreenHighlighted(el) {
            // SVG green fills used by Google Forms for correct indicators
            const GREEN_FILLS = ['#137333', '#188038', '#1e8e3e', '#34a853'];
            const svgs = el.querySelectorAll('svg');
            for (const svg of svgs) {
                for (const child of svg.querySelectorAll('[fill]')) {
                    if (GREEN_FILLS.includes(child.getAttribute('fill').toLowerCase())) return true;
                }
                if (GREEN_FILLS.includes((svg.getAttribute('fill') || '').toLowerCase())) return true;
            }

            // Computed background color — Google uses green-ish tints
            const bg = window.getComputedStyle(el).backgroundColor;
            if (bg && bg !== 'rgba(0, 0, 0, 0)' && bg !== 'transparent') {
                // Parse rgb(r, g, b) or rgba(r, g, b, a)
                const m = bg.match(/\d+/g);
                if (m && m.length >= 3) {
                    const [r, g, b] = m.map(Number);
                    // Green dominant: g significantly larger than r and b
                    if (g > 100 && g > r * 1.3 && g > b * 1.3) return true;
                }
            }
            return false;
        }

        /**
         * Returns true if the element (or any child) has a red/error highlight.
         */
        function isRedHighlighted(el) {
            const RED_FILLS = ['#d93025', '#c5221f', '#ea4335'];
            const svgs = el.querySelectorAll('svg');
            for (const svg of svgs) {
                for (const child of svg.querySelectorAll('[fill]')) {
                    if (RED_FILLS.includes(child.getAttribute('fill').toLowerCase())) return true;
                }
            }
            return false;
        }

        /**
         * Safely get inner text from element, collapsing whitespace.
         */
        function getText(el) {
            if (!el) return '';
            return el.innerText.replace(/\s+/g, ' ').trim();
        }

        /**
         * Strip leading option-label prefixes like "أ) ", "1. ", "A. " etc.
         * IMPORTANT: only strip when followed by a separator (. : - ) /)
         * NOT when Arabic letter is just the start of a real word.
         */
        function stripOptionPrefix(text) {
            // Match: letter/digit + separator character (., :, -, ), /) + optional space
            return text.replace(/^[أ-يa-zA-Z\d٠-٩](?=[.\:\-\)\/])\S?\s*/, '').trim();
        }

        /* ══════════════════════════════════════════════════════
           STEP 4 — Process each question
        ══════════════════════════════════════════════════════ */

        const questions = [];
        const wrongIndices = [];

        containers.forEach((qEl, idx) => {
            const qNum = idx + 1;

            /* ── 4A: Question text ── */
            let questionText = '';

            // The question title is usually the first heading inside the container
            const headingCandidates = qEl.querySelectorAll(
                '[role="heading"], .M7eMe, .HoN1Ob, .F3n8vf, ' +
                '.freebirdFormviewerViewItemsItemItemTitle'
            );
            if (headingCandidates.length > 0) {
                const clone = headingCandidates[0].cloneNode(true);
                // Remove score labels, required star, etc.
                clone.querySelectorAll('.DqBBlb, .R4nke, .freebirdFormviewerViewItemsItemRequiredAsterisk').forEach(e => e.remove());
                questionText = getText(clone);
            }

            // Fallback: first substantial text node
            if (!questionText) {
                for (const el of qEl.querySelectorAll('div, p, span')) {
                    const t = getText(el);
                    if (t.length > 5 && el.children.length === 0) {
                        questionText = t;
                        break;
                    }
                }
            }

            // Remove leading numeric indices "1. " "س1:" etc.
            questionText = questionText.replace(/^[\d٠-٩]+[\s\.\:\-\)\/]+\s*/, '').trim();
            if (!questionText) questionText = `السؤال ${qNum}`;

            /* ── 4B: Score / wrong detection ── */
            let isWrong = false;
            const fullText = qEl.innerText || '';

            // "0 / 1"  "0/1"  "٠ / ١" etc.
            if (/\b0\s*\/\s*[1-9]/.test(fullText) || /\b٠\s*\/\s*[١-٩]/.test(fullText)) {
                isWrong = true;
            }

            // Red icon anywhere in the container
            if (!isWrong && isRedHighlighted(qEl)) {
                isWrong = true;
            }

            // Explicit "الإجابة الصحيحة" / "Correct answer" text block
            // Google Forms only renders this box when the student answered WRONG
            let correctAnswerBoxText = '';
            // Search the whole container text for this pattern
            const caPattern = /(?:الإجابة الصحيحة|الإجابات الصحيحة|Correct answer|Correct answers)\s*[:\n]+\s*(.+?)(?:\n|$)/i;
            const caMatch = fullText.match(caPattern);
            if (caMatch) {
                correctAnswerBoxText = caMatch[1].trim()
                    .replace(/\s*\(\s*\d+[^)]*\)\s*$/, '') // strip "(1 نقطة)"
                    .trim();
                isWrong = true;
            }

            // Also try dedicated DOM elements for correct answer box
            if (!correctAnswerBoxText) {
                const caEl = qEl.querySelector(
                    '.c2gzEf, .R305vd, .i9L0be, .N3G8yb, .YMEQ1d, ' +
                    '[class*="CorrectAnswer"], [class*="correctAnswer"]'
                );
                if (caEl) {
                    let txt = getText(caEl);
                    txt = txt.replace(/^(الإجابة الصحيحة|الإجابات الصحيحة|Correct answer|Correct answers)\s*[:\n]+\s*/i, '').trim();
                    txt = txt.replace(/\s*\(\s*\d+[^)]*\)\s*$/, '').trim();
                    if (txt) {
                        correctAnswerBoxText = txt;
                        isWrong = true;
                    }
                }
            }

            if (isWrong) wrongIndices.push(qNum);

            /* ── 4C: Extract options ── */
            const optEls = Array.from(qEl.querySelectorAll('[role="radio"], [role="checkbox"]'));
            const options = [];
            let checkedOptionText = null;   // the option the student selected
            let greenOptionText = null;     // the option with a green highlight (correct)

            for (const optEl of optEls) {
                // Get option label text — try labeled span first, then full innerText
                const labelEl =
                    optEl.querySelector('.aDTYNe, .OvPDhc, .ulDsOb, [data-answer-value], [dir="auto"]') ||
                    optEl;

                let raw = getText(labelEl);

                // If raw contains multiple lines (e.g. label includes sub-elements), take first line
                raw = raw.split('\n')[0].trim();

                // Strip "أ) " "1. " prefixes — only when separator char follows immediately
                const clean = stripOptionPrefix(raw);
                const optText = clean || raw;

                if (optText && !options.includes(optText)) {
                    options.push(optText);
                }

                // Was this option selected by the student?
                const ariaChecked = optEl.getAttribute('aria-checked');
                const isChecked = ariaChecked === 'true' ||
                    optEl.querySelector('[aria-checked="true"]') !== null;
                if (isChecked && optText) checkedOptionText = optText;

                // Does this option have a green indicator?
                if (isGreenHighlighted(optEl) && optText) {
                    greenOptionText = optText;
                }
            }

            if (options.length === 0) options.push('نعم', 'لا');

            /* ── 4D: Determine correct answer ── */
            let correctAnswer = '';

            // Priority 1: explicit correct-answer text from the box
            if (correctAnswerBoxText) {
                // Try to match it to one of the extracted options
                const exactMatch = options.find(o => o.trim() === correctAnswerBoxText.trim());
                const partialMatch = options.find(o =>
                    correctAnswerBoxText.includes(o.trim()) || o.trim().includes(correctAnswerBoxText.trim())
                );
                if (exactMatch) {
                    correctAnswer = exactMatch;
                } else if (partialMatch) {
                    correctAnswer = partialMatch;
                } else {
                    // Add to options if not present
                    correctAnswer = correctAnswerBoxText;
                    if (!options.includes(correctAnswer)) options.push(correctAnswer);
                }
            }

            // Priority 2: green-highlighted option
            if (!correctAnswer && greenOptionText) {
                correctAnswer = greenOptionText;
            }

            // Priority 3: student answered correctly → checked option IS correct
            if (!correctAnswer && !isWrong && checkedOptionText) {
                correctAnswer = checkedOptionText;
            }

            // Fallback
            if (!correctAnswer) correctAnswer = options[0] || '';

            // Case-insensitive reconciliation with options list
            if (correctAnswer && !options.includes(correctAnswer)) {
                const ci = options.find(o => o.trim().toLowerCase() === correctAnswer.trim().toLowerCase());
                if (ci) correctAnswer = ci;
                else options.push(correctAnswer);
            }

            /* ── 4E: Feedback / explanation ── */
            let explanation = '';
            const fbEl = qEl.querySelector(
                '.g4k55c, ' +
                '.freebirdFormviewerViewItemsGradingFeedback, ' +
                '.freebirdFormviewerViewItemsGradingFeedbackContainer, ' +
                '[class*="Feedback"]'
            );
            if (fbEl) {
                explanation = getText(fbEl)
                    .replace(/^(ملاحظات|تعليقات|Feedback)\s*[:\n]+\s*/i, '')
                    .trim();
            }

            questions.push({
                question: questionText,
                options,
                answer: correctAnswer,
                explanation
            });
        });

        if (questions.length === 0) {
            return {
                success: false,
                error: 'لم يتم العثور على أسئلة (اختيار من متعدد) في هذه الصفحة.\n' +
                       'تأكد من:\n' +
                       '1. فتح صفحة "عرض النتيجة / View score" في Google Forms\n' +
                       '2. انتظار تحميل الصفحة بالكامل قبل الضغط على الإضافة'
            };
        }

        return {
            success: true,
            data: {
                quiz_name: quizTitle,
                wrong: wrongIndices,
                questions
            }
        };

    } catch (err) {
        return {
            success: false,
            error: 'خطأ غير متوقع: ' + err.message + '\n' + (err.stack || '')
        };
    }
}

// Message-passing support (for background scripts)
if (typeof chrome !== 'undefined' && chrome.runtime && chrome.runtime.onMessage) {
    chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
        if (request.action === 'EXTRACT_QUIZ') {
            sendResponse(extractGoogleFormsQuiz());
        }
        return true;
    });
}
