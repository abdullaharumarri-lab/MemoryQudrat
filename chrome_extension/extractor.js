/**
 * MemoryQudrat — Google Forms Quiz Extractor Engine v4.0 (Enhanced Passage & Option Engine)
 *
 * Core Enhancements (v4.0):
 *   • Multi-question Reading Passage Tracking: Tracks the active reading passage (استيعاب المقروء)
 *     and attaches it to ALL consecutive questions referencing that passage, not just the first one!
 *   • Section & Description Card Support: Captures passages from section headers, description blocks, and cards.
 *   • Robust Option Row Detection: Traverses parent option containers to extract labels from sibling spans,
 *     .docssharedWizToggleLabeledLabelText, .aDTYNe, aria-label, etc.
 *   • Accurate Correct Answer Resolution: Supports green SVG highlights, score boxes, checked options, and feedback.
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
            if (!el) return false;
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
           Strips "أ) " "1. " "A- " (letter + separator), NOT "أ " alone
        ══════════════════════════════════════════ */
        function stripPrefix(text) {
            if (!text) return '';
            return text.replace(/^[أ-يa-zA-Z\d٠-٩][.\:\-\)\/]\s*/, '').trim();
        }

        /* ══════════════════════════════════════════
           5 — Top-down Passage & Question Extractor
        ══════════════════════════════════════════ */
        const questions = [];
        const wrongIndices = [];

        // Find all question items and passage items in DOM order
        const allItems = Array.from(document.querySelectorAll(
            '[role="listitem"], .Qr7Oae, .geS5n, .freebirdFormviewerViewItemsItemItem, .freebirdFormviewerViewHeaderHeader'
        ));

        let currentActivePassage = '';

        // Process all cards in DOM order to maintain active reading passage state
        allItems.forEach((item) => {
            const hasRadios = item.querySelector('[role="radiogroup"], [role="radio"]');
            const hasInputs = item.querySelector('input[type="text"], input[type="email"], textarea');

            // ── Case A: Standalone Text Block / Reading Passage Card ──
            if (!hasRadios && !hasInputs) {
                const text = clean(item);
                // Exclude quiz title banners, short labels, and score banners (e.g. "50 / 50")
                if (text.length > 35 && !/^\d+\s*\/\s*\d+/.test(text)) {
                    // Filter out form description headers if identical to quiz title
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

            // 1. Extract question text
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
            // Strip leading numbering e.g. "1. " "س1: "
            questionText = questionText.replace(/^[\d٠-٩]+[\s\.\:\-\)\/]+\s*/, '').trim();
            if (!questionText) questionText = `السؤال ${qNum}`;

            // Attach active passage if present
            if (currentActivePassage) {
                const snippet = currentActivePassage.slice(0, 30);
                if (!questionText.includes(snippet)) {
                    questionText = '📄 ' + currentActivePassage + '\n\n❓ ' + questionText;
                }
            }

            // 2. Wrong / score detection
            let isWrong = false;
            const blockText = item.innerText || '';

            if (/\b0\s*\/\s*[1-9]/.test(blockText) || /\b٠\s*\/\s*[١-٩]/.test(blockText)) {
                isWrong = true;
            }

            // "الإجابة الصحيحة" box
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

            // 3. Extract options
            const radios = Array.from(rg.querySelectorAll('[role="radio"]'));
            const options = [];
            let checkedOptText = null;
            let greenOptText = null;

            for (const radio of radios) {
                const rowContainer = radio.closest('.docssharedWizToggleLabeledContainer, .SG0AAe, .Y6Myj, [role="listitem"]') || radio.parentElement || radio;

                let optText = '';

                // Try label containers in row
                const labelEl = rowContainer.querySelector('.docssharedWizToggleLabeledLabelText, .aDTYNe, .ulDsOb, .OvPDhc, .M7eMe, .jLM23c, .NPEfkd, .WpHeLc');
                if (labelEl) {
                    optText = clean(labelEl).split('\n')[0].trim();
                }

                // Try radio aria-label or data-value
                if (!optText) {
                    optText = (radio.getAttribute('aria-label') || radio.getAttribute('data-value') || '').trim();
                }

                // Try container innerText
                if (!optText) {
                    const lines = (rowContainer.innerText || '').split('\n').map(l => l.trim()).filter(l => l);
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

                if (hasGreenHighlight(rowContainer) || hasGreenHighlight(radio)) {
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

            // Ensure at least 2 options
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
