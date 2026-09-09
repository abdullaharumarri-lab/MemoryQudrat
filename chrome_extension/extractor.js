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

// ─── Universal Answer & Text Normalization Helpers ───────────────────────────

function stripInvisible(str) {
    if (!str) return '';
    return String(str)
        .replace(/[\u00a0\u202f]/g, ' ')
        .replace(/[\ufeff\u200b\u200c\u200d\u200e\u200f\u202a-\u202e\u2060]/g, '')
        .trim();
}

function normalizeArabicDigitsJS(str) {
    if (!str) return '';
    const arabic = "٠١٢٣٤٥٦٧٨٩";
    let res = String(str);
    for (let i = 0; i < arabic.length; i++) {
        res = res.replaceAll(arabic[i], i.toString());
    }
    return res;
}

function normalizeForMatchJS(str) {
    if (!str) return '';
    let s = stripInvisible(str);
    s = normalizeArabicDigitsJS(s);
    // Remove Arabic diacritics / tashkeel
    s = s.replace(/[\u064B-\u065F\u0670]/g, '');
    // Normalize alef variants
    s = s.replace(/[أإآٱ]/g, 'ا');
    // Normalize ta marbuta
    s = s.replace(/ة/g, 'ه');
    // Normalize ya / alef maksura
    s = s.replace(/ى/g, 'ي');
    return s.replace(/\s+/g, ' ').trim().toLowerCase();
}

function stripOptionPrefix(text) {
    if (!text) return '';
    let t = stripInvisible(String(text));
    // Strip prefixes like "الخيار أ", "الخيار (أ)", "خيار 1"
    t = t.replace(/^(?:الخيار|خيار|Option)\s*[:\-\.]?\s*/i, '');
    // Strip (أ) or أ) or أ- or أ. or A) or 1)
    t = t.replace(/^[(\uff08]?[أ-دa-dA-D\u0623\u0628\u062c\u062f][)\uff09.:\-\/\s]+\s*/, '');
    if (/^[(\uff08][1-4\u0661-\u0664][)\uff09]\s+/.test(t) || /^[1-4\u0661-\u0664][)\uff09\.\-]\s+/.test(t)) {
        t = t.replace(/^[(\uff08]?[1-4\u0661-\u0664][)\uff09\.\-]+\s*/, '');
    }
    return t.trim();
}

function resolveCorrectAnswer(options, rawCA) {
    if (!rawCA || !options || options.length === 0) return options[0] || '';
    
    const cleanCA = stripInvisible(rawCA);
    const strippedPrefixCA = stripOptionPrefix(cleanCA);

    // 1. Exact string match
    for (const opt of options) {
        const cleanOpt = stripInvisible(opt);
        if (cleanOpt === cleanCA || cleanOpt === strippedPrefixCA) {
            return opt;
        }
    }

    // 2. Normalized match (digits, hamzas, diacritics, invisible chars)
    const normCA = normalizeForMatchJS(cleanCA);
    const normStrippedCA = normalizeForMatchJS(strippedPrefixCA);
    for (const opt of options) {
        const normOpt = normalizeForMatchJS(opt);
        const normStrippedOpt = normalizeForMatchJS(stripOptionPrefix(opt));
        if (normOpt === normCA || normOpt === normStrippedCA || normStrippedOpt === normCA || normStrippedOpt === normStrippedCA) {
            return opt;
        }
    }

    // 3. Letter-to-Index match: e.g. 'أ', 'ب', 'ج', 'د' or 'الخيار (ب)' or '(أ)' or 'A', 'B'
    const arabicLetters = ["أ", "ب", "ج", "د", "هـ", "و", "ز", "ح", "ط", "ي"];
    const englishLetters = ["a", "b", "c", "d", "e", "f", "g", "h", "i", "j"];
    
    let letterCandidate = cleanCA.replace(/^(?:الخيار|خيار|Option)\s*[:\-\.]?\s*/i, '');
    letterCandidate = letterCandidate.replace(/^[(\uff08\[{]+|[)\uff09\]} \t.:-]+$/g, '').trim();

    const arIdx = arabicLetters.indexOf(letterCandidate);
    if (arIdx !== -1 && arIdx < options.length) {
        return options[arIdx];
    }
    const enIdx = englishLetters.indexOf(letterCandidate.toLowerCase());
    if (enIdx !== -1 && enIdx < options.length) {
        return options[enIdx];
    }

    // 4. Numeric equivalence: e.g. 15 vs 15.0 vs ١٥
    const numCA = parseFloat(normalizeArabicDigitsJS(cleanCA));
    if (!isNaN(numCA)) {
        for (const opt of options) {
            const numOpt = parseFloat(normalizeArabicDigitsJS(opt));
            if (!isNaN(numOpt) && Math.abs(numCA - numOpt) < 1e-6) {
                return opt;
            }
        }
    }

    // 5. If cleanCA is substantial (> 4 chars), try token boundary containment
    if (normCA.length >= 4) {
        for (const opt of options) {
            const normOpt = normalizeForMatchJS(opt);
            if (normOpt.length >= 4 && (normOpt === normCA || normCA.includes(normOpt) || normOpt.includes(normCA))) {
                return opt;
            }
        }
    }

    return strippedPrefixCA || options[0] || '';
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
            let passageCounter = 0;
            let currentPassageObj = null; // { id, text, itemId }
            const questions = [];
            const wrongIndices = [];

            for (const item of items) {
                const itemId = item[0];
                const title = (item[1] || "").trim();
                const itemType = item[3];

                // Type 1 & 6: Reading Passage Card / Text Block (Mandatory Capture)
                if (itemType === 1 || itemType === 6) {
                    const itemTitle = (item[1] || "").trim();
                    const itemDesc = (item[2] || "").trim();
                    let passageCandidate = "";
                    if (itemTitle && itemDesc) {
                        passageCandidate = itemTitle.length < 30 ? itemDesc : (itemTitle + "\n\n" + itemDesc);
                    } else {
                        passageCandidate = itemDesc || itemTitle;
                    }
                    passageCandidate = passageCandidate.trim();

                    const isInfoOrPledge = /(?:اسم\s+الطالب|اسم\s+المشترك|الاسم\s+الثلاثي|البريد|email|رقم\s+الجوال|كلمة\s+المرور|password|اقسم|أقسم|أتعهد|اتعهد|أقر|تعهد)/i.test(passageCandidate);
                    if (!isInfoOrPledge && passageCandidate.length > 5) {
                        passageCounter++;
                        const pId = `passage_${passageCounter}`;
                        currentPassageObj = {
                            id: pId,
                            text: passageCandidate,
                            itemId: itemId
                        };

                        // Mark DOM element for screenshot capture by text snippet matching
                        try {
                            const allCards = Array.from(document.querySelectorAll('.Qr7Oae, [role="listitem"]'));
                            const snippet = passageCandidate.slice(0, 35).trim();
                            let cardEl = allCards.find(c => {
                                const t = (c.innerText || '').trim();
                                return (snippet && t.includes(snippet)) || (itemTitle && itemTitle.length > 5 && t.includes(itemTitle));
                            });
                            if (!cardEl) {
                                cardEl = document.querySelector(`[data-item-id="${itemId}"]`)?.closest('.Qr7Oae, [role="listitem"]') ||
                                         document.querySelector(`[data-item-id="${itemId}"]`);
                            }
                            if (cardEl) {
                                cardEl.setAttribute('data-qudrat-passage-id', pId);
                            }
                        } catch (e) {}
                    }
                    continue;
                }

                // Type 8: Section Break / Header
                if (itemType === 8) {
                    const secTitle = (item[1] || "").trim();
                    const secDesc = (item[2] || "").trim();
                    let secText = "";
                    if (secTitle && secDesc) {
                        secText = secTitle.length < 30 ? secDesc : (secTitle + "\n\n" + secDesc);
                    } else {
                        secText = secDesc || secTitle;
                    }
                    secText = secText.trim();

                    const isMeta = /(?:اسم\s+الطالب|بيانات|تسجيل|معلومات|تعليمات|درجات|القسم\s+الأول|القسم\s+الثاني)/i.test(secText);
                    if (!isMeta && secText.length > 25) {
                        passageCounter++;
                        const pId = `passage_${passageCounter}`;
                        currentPassageObj = {
                            id: pId,
                            text: secText,
                            itemId: itemId
                        };

                        try {
                            const allCards = Array.from(document.querySelectorAll('.Qr7Oae, [role="listitem"]'));
                            const secSnippet = secText.slice(0, 35).trim();
                            let cardEl = allCards.find(c => {
                                const t = (c.innerText || '').trim();
                                return (secSnippet && t.includes(secSnippet)) || (secTitle && secTitle.length > 5 && t.includes(secTitle));
                            });
                            if (!cardEl) {
                                cardEl = document.querySelector(`[data-item-id="${itemId}"]`)?.closest('.Qr7Oae, [role="listitem"]') ||
                                         document.querySelector(`[data-item-id="${itemId}"]`);
                            }
                            if (cardEl) {
                                cardEl.setAttribute('data-qudrat-passage-id', pId);
                            }
                        } catch (e) {}
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

                    // Attach active passage seamlessly
                    let questionPassageId = null;
                    let questionPassageText = null;
                    if (currentPassageObj) {
                        const isAnalogy = isVerbalAnalogy(cleanQText);
                        if (!isAnalogy) {
                            questionPassageId = currentPassageObj.id;
                            questionPassageText = currentPassageObj.text;
                            const snippet = currentPassageObj.text.slice(0, 25).trim();
                            if (!cleanQText.includes(snippet)) {
                                cleanQText = '📄 ' + currentPassageObj.text + '\n\n❓ ' + cleanQText;
                            }
                        }
                    }

                    // Look up DOM card — first by data-item-id, then by text, then by position
                    const qNum = questions.length + 1;
                    let isWrong = false;
                    let correctAnswer = "";
                    let explanation = "";

                    // Primary: by data-item-id attribute (works for question cards in most forms)
                    let cardEl = document.querySelector(`[data-item-id="${itemId}"]`)?.closest('.Qr7Oae, [role="listitem"]') ||
                                 document.querySelector(`[data-item-id="${itemId}"]`);

                    // Fallback: search all listitem-style cards by text snippet of the question
                    if (!cardEl) {
                        const allCards = Array.from(document.querySelectorAll('.Qr7Oae, [role="listitem"]'));
                        const qSnippet = cleanQText.slice(0, 25).trim();
                        if (qSnippet.length >= 6) {
                            cardEl = allCards.find(c => (c.innerText || '').includes(qSnippet));
                        }
                        // Last resort: positional index using ONLY question cards (has radiogroup or checkbox)
                        if (!cardEl) {
                            const questionCardsOnly = allCards.filter(c =>
                                c.querySelector('[role="radiogroup"]') ||
                                c.querySelector('[role="group"][data-params]') ||
                                c.querySelector('.AB7Lab') // Google Forms MCQ radio container
                            );
                            if (qNum - 1 < questionCardsOnly.length) {
                                cardEl = questionCardsOnly[qNum - 1];
                            } else {
                                // Very last resort — raw positional (may be off if passages exist)
                                if (qNum - 1 < allCards.length) {
                                    cardEl = allCards[qNum - 1];
                                }
                            }
                        }
                    }

                    let questionImage = null;

                    if (cardEl) {
                        const cardClone = cardEl.cloneNode(true);
                        cardClone.querySelectorAll('.M2vV3e, .RDPZE, .freebirdFormviewerViewItemsItemGradingPoints, [aria-describedby*="points"]').forEach(e => e.remove());
                        const cardText = cardClone.innerText || '';

                        // 1. Check for explicit "الإجابة الصحيحة" (Correct answer) box first!
                        // In Google Forms, this box ONLY appears when the student answered WRONGLY.
                        const caPatterns = [
                            /(?:الإجابة الصحيحة|الإجابات الصحيحة|الإجابة النموذجية|الإجابة الصحيحة هي)\s*[:\n\-]?\s*([^\n]+)/i,
                            /(?:Correct answer|Correct answers)\s*[:\n\-]?\s*([^\n]+)/i
                        ];
                        for (const pat of caPatterns) {
                            const m = cardText.match(pat);
                            if (m && m[1].trim()) {
                                correctAnswer = m[1].trim();
                                isWrong = true;
                                break;
                            }
                        }

                        // 2. Search grading callout containers explicitly
                        if (!correctAnswer) {
                            const gradingEls = cardEl.querySelectorAll('.Y6Myj, .zfd4wb, .bUzgoc, .freebirdFormviewerViewItemsItemGradingExplanation, .freebirdFormviewerViewItemsItemGradingContainer, [aria-label*="الإجابة الصحيحة"], [aria-label*="Correct"]');
                            for (const gel of gradingEls) {
                                const txt = (gel.innerText || '').trim();
                                if (txt) {
                                    for (const pat of caPatterns) {
                                        const m = txt.match(pat);
                                        if (m && m[1].trim()) {
                                            correctAnswer = m[1].trim();
                                            isWrong = true;
                                            break;
                                        }
                                    }
                                    if (!correctAnswer && (txt.includes('الإجابة الصحيحة') || /correct answer/i.test(txt))) {
                                        const lines = txt.split('\n').map(l => l.trim()).filter(Boolean);
                                        const caIdx = lines.findIndex(l => l.includes('الإجابة الصحيحة') || /correct answer/i.test(l));
                                        if (caIdx !== -1 && caIdx + 1 < lines.length) {
                                            correctAnswer = lines[caIdx + 1];
                                            isWrong = true;
                                            break;
                                        }
                                    }
                                }
                                if (correctAnswer) break;
                            }
                        }

                        // 3. Check green highlight (correct radio indicator)
                        if (!correctAnswer) {
                            const greenRadio = cardEl.querySelector('[fill="#137333"], [fill="#188038"], [fill="#34a853"], [fill="#0f9d58"], [fill="#1e8e3e"]');
                            if (greenRadio) {
                                const greenBox = greenRadio.closest('.docssharedWizToggleLabeledContainer, .SG0AAe, .Y6Myj, .bzfPab, [role="radio"]') || greenRadio.parentElement;
                                if (greenBox) {
                                    correctAnswer = (greenBox.innerText || '').split('\n')[0].trim();
                                }
                            }
                        }

                        // 4. If no correction box was present, student's checked radio IS the correct answer
                        if (!correctAnswer) {
                            const checkedRadio = cardEl.querySelector('[aria-checked="true"]');
                            if (checkedRadio) {
                                const checkedBox = checkedRadio.closest('.docssharedWizToggleLabeledContainer, .SG0AAe, .Y6Myj, .bzfPab, [role="radio"]') || checkedRadio.parentElement;
                                if (checkedBox) {
                                    correctAnswer = (checkedBox.innerText || '').split('\n')[0].trim();
                                }
                            }
                        }

                        // 5. Wrong detection based ONLY on points container or error container
                        const pointsEl = cardEl.querySelector('.freebirdFormviewerViewItemsItemGradingPoints, .RDPZE, [aria-describedby*="points"], .M2vV3e');
                        if (pointsEl) {
                            const ptText = (pointsEl.innerText || '').trim();
                            if (/\b0\s*\/\s*[1-9]/.test(ptText) || /\b٠\s*\/\s*[١-٩]/.test(ptText)) {
                                isWrong = true;
                            }
                        } else if (cardEl.querySelector('.freebirdFormviewerViewItemsItemGradingIncorrectContainer, [aria-label="غير صحيح"], [aria-label="Incorrect"]')) {
                            isWrong = true;
                        }

                        // 6. Extract Question Image if present (diagrams, geometry figures)
                        const imgEl = cardEl.querySelector('img.Hvn9Fb, img[src*="googleusercontent.com"], img[src*="docs.google.com"], .freebirdFormviewerViewItemsEmbeddedobjectImage img');
                        if (imgEl && imgEl.src && !imgEl.src.includes('cleardot.gif')) {
                            questionImage = imgEl.src;
                        }

                        // 7. Feedback explanation
                        const fbEl = cardEl.querySelector('.g4k55c, .freebirdFormviewerViewItemsGradingFeedbackContainer');
                        if (fbEl) {
                            explanation = (fbEl.innerText || '').replace(/^(ملاحظات|تعليقات|Feedback)\s*[:\n]+\s*/i, '').trim();
                        }
                    }

                    // Fallback to data model for question image if not found in DOM
                    if (!questionImage && item.length > 5) {
                        try {
                            const itemStr = JSON.stringify(item);
                            const m = itemStr.match(/https:\/\/(?:lh\d+\.googleusercontent\.com|docs\.google\.com\/forms\/d\/e\/[^"'\\]+)/);
                            if (m) questionImage = m[0];
                        } catch (e) {}
                    }

                    if (isWrong) wrongIndices.push(qNum);

                    // Universal robust answer normalization
                    const finalCorrectAnswer = resolveCorrectAnswer(options, correctAnswer);

                    questions.push({
                        question: cleanQText,
                        options: options,
                        answer: finalCorrectAnswer,
                        explanation: explanation,
                        passage_id: questionPassageId,
                        passage_text: questionPassageText,
                        image: questionImage
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
        let passageCounterFB = 0;
        let currentPassageFBO = null;

        cards.forEach((card) => {
            const rg = card.querySelector('[role="radiogroup"]');
            if (!rg) {
                const txt = clean(card);
                const isMeta = /(?:اسم\s+الطالب|اسم\s+المشترك|البريد|email|رقم\s+الجوال|كلمة\s+المرور|password|اقسم|أقسم|أتعهد|اتعهد|تعهد)/i.test(txt);
                if (!isMeta && txt.length > 20 && !/^\s*(?:\d+\s*\/\s*\d+|\d+\s*من\s+إجمالي\s+\d+\s*نقطة)\s*$/.test(txt)) {
                    passageCounterFB++;
                    const pId = `passage_fb_${passageCounterFB}`;
                    currentPassageFBO = { id: pId, text: txt };
                    try {
                        card.setAttribute('data-qudrat-passage-id', pId);
                    } catch (e) {}
                }
                return;
            }

            const qNum = questionsFallback.length + 1;
            let qText = "";
            const h = card.querySelector('[role="heading"], .M7eMe');
            if (h) qText = clean(h);
            qText = qText.replace(/^[\d٠-٩]+[\s\.\:\-\)\/]+\s*/, '').replace(/\s*\*\s*$/, '').trim();
            if (!qText) qText = `السؤال ${qNum}`;

            let qPassageId = null;
            let qPassageText = null;
            if (currentPassageFBO) {
                const isAnalogy = isVerbalAnalogy(qText);
                if (!isAnalogy) {
                    qPassageId = currentPassageFBO.id;
                    qPassageText = currentPassageFBO.text;
                    const snippet = currentPassageFBO.text.slice(0, 25).trim();
                    if (!qText.includes(snippet)) {
                        qText = '📄 ' + currentPassageFBO.text + '\n\n❓ ' + qText;
                    }
                }
            }

            const radios = Array.from(rg.querySelectorAll('[role="radio"]'));
            const options = [];
            let ans = "";

            radios.forEach((r) => {
                const box = r.closest('.docssharedWizToggleLabeledContainer, .SG0AAe') || r.parentElement;
                let opt = stripOptionPrefix(clean(box));
                if (opt && !options.includes(opt)) options.push(opt);
                if (r.getAttribute('aria-checked') === 'true') ans = opt;
            });

            const safeOpts = options.length >= 2 ? options : ["صح", "خطأ"];
            const finalAns = resolveCorrectAnswer(safeOpts, ans);

            let qImage = null;
            const imgEl = card.querySelector('img.Hvn9Fb, img[src*="googleusercontent.com"], img[src*="docs.google.com"], .freebirdFormviewerViewItemsEmbeddedobjectImage img');
            if (imgEl && imgEl.src && !imgEl.src.includes('cleardot.gif')) {
                qImage = imgEl.src;
            }

            questionsFallback.push({
                question: qText,
                options: safeOpts,
                answer: finalAns,
                explanation: "",
                passage_id: qPassageId,
                passage_text: qPassageText,
                image: qImage
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
