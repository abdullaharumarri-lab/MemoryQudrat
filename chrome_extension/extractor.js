/**
 * MemoryQudrat — Google Forms Quiz Extractor Engine v3.0
 *
 * Core strategy (v3 rewrite):
 *   • Start from [role="radiogroup"] — the ONLY reliable anchor in Google Forms MCQ
 *   • Never filter by text-input presence (avoids false exclusions of sibling questions)
 *   • Option text: use radio's own innerText first line, never [dir="auto"] (too broad)
 *   • Correct answer: (1) regex on full block text for "الإجابة الصحيحة", (2) green SVG fill,
 *     (3) checked option when student answered correctly, (4) first option as last resort
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
           2 — Helper: clean inner text
        ══════════════════════════════════════════ */
        function clean(el) {
            if (!el) return '';
            return (el.innerText || '').replace(/\s+/g, ' ').trim();
        }

        /* ══════════════════════════════════════════
           3 — Helper: detect green (correct) highlight
        ══════════════════════════════════════════ */
        const GREEN_FILLS = ['#137333', '#188038', '#1e8e3e', '#34a853', '#0f9d58'];

        function hasGreenHighlight(el) {
            // Check SVG fills
            for (const node of el.querySelectorAll('[fill]')) {
                if (GREEN_FILLS.includes((node.getAttribute('fill') || '').toLowerCase())) return true;
            }
            // Check computed background
            const bg = window.getComputedStyle(el).backgroundColor;
            const m = bg && bg.match(/\d+/g);
            if (m && m.length >= 3) {
                const [r, g, b] = [+m[0], +m[1], +m[2]];
                if (g > 80 && g > r * 1.4 && g > b * 1.4) return true;
            }
            return false;
        }

        /* ══════════════════════════════════════════
           4 — Helper: strip option-letter prefix
           Only strips "أ) " "1. " "A- " (letter + separator), NOT "أ " alone
        ══════════════════════════════════════════ */
        function stripPrefix(text) {
            return text.replace(/^[أ-يa-zA-Z\d٠-٩][.\:\-\)\/]\s*/, '').trim();
        }

        /* ══════════════════════════════════════════
           5 — Helper: find a preceding text passage block
           Google Forms sometimes puts a reading passage (فقرة) in a
           standalone card BEFORE the MCQ question that references it.
           We detect it as: the immediately preceding sibling container
           that has NO radiogroup and NO input, but has substantial text.
        ══════════════════════════════════════════ */
        function findPrecedingPassage(container) {
            const parent = container.parentElement;
            if (!parent) return '';

            const siblings = Array.from(parent.children);
            const myIdx = siblings.indexOf(container);
            if (myIdx <= 0) return '';

            // Walk backwards through preceding siblings
            for (let i = myIdx - 1; i >= 0; i--) {
                const prev = siblings[i];

                // Skip if it is another MCQ question
                if (prev.querySelector('[role="radiogroup"]')) break;

                // Skip if it is a text-answer question
                if (prev.querySelector('input[type="text"], input[type="email"], textarea')) break;

                // If it has substantial text it is a passage / section header / description
                const txt = (prev.innerText || '').replace(/\s+/g, ' ').trim();
                if (txt.length > 30) {
                    // Exclude score summary banners (contain "/" between numbers)
                    if (/^\d+\s*\/\s*\d+/.test(txt)) break;
                    return txt;
                }
                // If the sibling is empty / too short, keep looking
            }
            return '';
        }

        /* ══════════════════════════════════════════
           6 — Main: iterate over every radiogroup
           Each [role="radiogroup"] = one MCQ question
        ══════════════════════════════════════════ */
        const radioGroups = Array.from(document.querySelectorAll('[role="radiogroup"]'));

        const questions   = [];
        const wrongIndices = [];


        radioGroups.forEach((rg, idx) => {
            const qNum = idx + 1;

            /* ── 5A: Find the enclosing question container ── */
            // Walk up at most 10 levels to find a listitem or known container class
            let container = rg.parentElement;
            for (let i = 0; i < 10; i++) {
                if (!container || container === document.body) break;
                if (
                    container.getAttribute('role') === 'listitem' ||
                    container.classList.contains('Qr7Oae') ||
                    container.classList.contains('geS5n') ||
                    container.dataset.itemId !== undefined ||
                    container.getAttribute('data-item-id') !== null
                ) break;
                container = container.parentElement;
            }
            if (!container || container === document.body) container = rg.parentElement;

            /* ── 5B: Extract question text ── */
            // Find the heading that comes BEFORE the radiogroup in the DOM
            let questionText = '';
            const allHeadings = Array.from(container.querySelectorAll(
                '[role="heading"], .M7eMe, .HoN1Ob, .F3n8vf, ' +
                '.freebirdFormviewerViewItemsItemItemTitle'
            ));
            for (const h of allHeadings) {
                // DOCUMENT_POSITION_PRECEDING means h appears before rg
                if (rg.compareDocumentPosition(h) & Node.DOCUMENT_POSITION_PRECEDING) {
                    const clone = h.cloneNode(true);
                    // Remove sub-labels: score display, required asterisk
                    clone.querySelectorAll('.R4nke, .DqBBlb, .freebirdFormviewerViewItemsItemRequiredAsterisk').forEach(e => e.remove());
                    questionText = clean(clone);
                    if (questionText) break;
                }
            }
            // Fallback: first heading in container regardless of position
            if (!questionText && allHeadings.length > 0) {
                const clone = allHeadings[0].cloneNode(true);
                clone.querySelectorAll('.R4nke, .DqBBlb').forEach(e => e.remove());
                questionText = clean(clone);
            }
            // Strip leading number "1. " "س1: " etc.
            questionText = questionText.replace(/^[\d٠-٩]+[\s\.\:\-\)\/]+\s*/, '').trim();
            if (!questionText) questionText = `السؤال ${qNum}`;

            // ── Attach preceding reading passage (فقرة) if present ──
            // Some Google Forms tests put a shared text block before the question.
            // We prepend it so the question is self-contained and meaningful.
            const passage = findPrecedingPassage(container);
            if (passage) {
                // Avoid duplicating the passage text if it is already in the question
                if (!questionText.includes(passage.slice(0, 40))) {
                    questionText = '📄 ' + passage + '\n\n❓ ' + questionText;
                }
            }

            /* ── 5C: Wrong / score detection ── */
            let isWrong = false;
            const blockText = container.innerText || '';

            // Score pattern: "0 / 1", "0/2", "٠ / ١"
            if (/\b0\s*\/\s*[1-9]/.test(blockText) || /\b٠\s*\/\s*[١-٩]/.test(blockText)) {
                isWrong = true;
            }

            // "الإجابة الصحيحة" box — only rendered for wrong answers
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
            // Also check dedicated DOM elements
            if (!correctAnswerFromBox) {
                const caEl = container.querySelector('.c2gzEf, .R305vd, .i9L0be, .N3G8yb, .YMEQ1d');
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

            /* ── 5D: Extract options from radio elements ── */
            const radios = Array.from(rg.querySelectorAll('[role="radio"]'));
            const options = [];
            let checkedOptText = null;
            let greenOptText   = null;

            for (const radio of radios) {
                /* Get option text — THREE strategies, in order of reliability:
                   1. Known Google Forms label class inside the radio
                   2. First non-empty text line of the radio element itself
                   3. aria-label attribute
                */
                let optText = '';

                // Strategy 1: specific label classes (Google Forms label containers)
                const labelEl = radio.querySelector('.ulDsOb, .OvPDhc, .aDTYNe, .WpHeLc, .Y6Myj');
                if (labelEl) {
                    optText = clean(labelEl).split('\n')[0].trim();
                }

                // Strategy 2: first line of radio innerText
                if (!optText) {
                    const lines = (radio.innerText || '').split('\n').map(l => l.trim()).filter(l => l);
                    if (lines.length > 0) optText = lines[0];
                }

                // Strategy 3: aria-label
                if (!optText) {
                    optText = (radio.getAttribute('aria-label') || '').trim();
                }

                // Strip "أ) " "1. " prefixes
                optText = stripPrefix(optText);

                if (!optText || options.includes(optText)) continue;
                options.push(optText);

                // Was this radio selected by the student?
                const isChecked =
                    radio.getAttribute('aria-checked') === 'true' ||
                    !!radio.querySelector('[aria-checked="true"]') ||
                    radio.getAttribute('aria-selected') === 'true';
                if (isChecked) checkedOptText = optText;

                // Does this radio have a green highlight (correct answer indicator)?
                if (hasGreenHighlight(radio)) greenOptText = optText;
            }

            /* ── 5E: Determine correct answer ── */
            let correctAnswer = '';

            // Priority 1: "الإجابة الصحيحة" box text
            if (correctAnswerFromBox) {
                const exact   = options.find(o => o.trim() === correctAnswerFromBox.trim());
                const partial = options.find(o =>
                    correctAnswerFromBox.includes(o.trim()) || o.trim().includes(correctAnswerFromBox.trim())
                );
                correctAnswer = exact || partial || correctAnswerFromBox;
                if (!options.includes(correctAnswer)) options.push(correctAnswer);
            }

            // Priority 2: option with green highlight
            if (!correctAnswer && greenOptText) {
                correctAnswer = greenOptText;
            }

            // Priority 3: student answered correctly → checked option IS correct
            if (!correctAnswer && !isWrong && checkedOptText) {
                correctAnswer = checkedOptText;
            }

            // Last resort
            if (!correctAnswer) correctAnswer = options[0] || '';

            // Case-insensitive reconciliation
            if (correctAnswer && !options.includes(correctAnswer)) {
                const ci = options.find(o => o.toLowerCase() === correctAnswer.toLowerCase());
                if (ci) correctAnswer = ci;
            }

            /* ── 5F: Explanation / feedback ── */
            let explanation = '';
            const fbEl = container.querySelector('.g4k55c, .freebirdFormviewerViewItemsGradingFeedbackContainer');
            if (fbEl) {
                explanation = clean(fbEl)
                    .replace(/^(ملاحظات|تعليقات|Feedback)\s*[:\n]+\s*/i, '')
                    .trim();
            }

            questions.push({ question: questionText, options, answer: correctAnswer, explanation });
        });

        /* ══════════════════════════════════════════
           6 — Return result
        ══════════════════════════════════════════ */
        if (questions.length === 0) {
            return {
                success: false,
                error:
                    'لم يتم العثور على أسئلة اختيار من متعدد (MCQ) في هذه الصفحة.\n\n' +
                    'تأكد من:\n' +
                    '• فتح صفحة "عرض النتيجة / View score" في Google Forms\n' +
                    '• انتظار تحميل الصفحة بالكامل قبل الضغط على الإضافة\n' +
                    '• أن الاختبار يحتوي على أسئلة اختيار من متعدد (radio buttons)'
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
