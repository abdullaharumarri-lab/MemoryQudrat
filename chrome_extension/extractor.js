/**
 * MemoryQudrat - Google Forms Quiz Extractor Engine
 * Extracts quiz questions, options, correct answers, and wrong indices from Google Forms "View score" or Quiz page.
 */

function extractGoogleFormsQuiz() {
    try {
        // 1. Quiz Title
        let quizTitle = "كويز بدون عنوان";
        const titleEl = document.querySelector('.F9iS2e, .freebirdFormviewerViewHeaderTitle, .ahS6Le, .v1CNqd, [role="heading"][aria-level="1"], h1');
        if (titleEl && titleEl.innerText.trim()) {
            quizTitle = titleEl.innerText.trim();
        } else {
            // Document title fallback
            const docTitle = document.title.replace(/[-–—|].*$/, '').replace(/عرض النتيجة|View score|Google Forms|نماذج Google/gi, '').trim();
            if (docTitle) quizTitle = docTitle;
        }

        // 2. Identify all Question Containers
        let questionElements = Array.from(document.querySelectorAll('[role="listitem"], .Qr7Oae, .geS5n'));
        
        // Filter out non-question containers (like header banner, score summary header, etc.)
        questionElements = questionElements.filter(el => {
            const hasOptions = el.querySelectorAll('[role="radio"], [role="checkbox"], .docssharedWizToggleLabeledContainer, .SG0tKc, .nWQ3Re, .d7L4cf').length > 0;
            const hasHeading = el.querySelector('[role="heading"], .M7eMe, .HoN1Ob, .F3n8vf') !== null;
            return hasOptions && hasHeading;
        });

        // Fallback: If no role="listitem" matches, query by radio/checkbox parents
        if (questionElements.length === 0) {
            const radioGroups = Array.from(document.querySelectorAll('[role="radiogroup"], [role="group"]'));
            questionElements = radioGroups.map(rg => rg.closest('.Qr7Oae') || rg.closest('.geS5n') || rg.parentElement).filter(Boolean);
            questionElements = Array.from(new Set(questionElements));
        }

        const questions = [];
        const wrongIndices = [];

        questionElements.forEach((qEl, index) => {
            const qNum = index + 1;

            // --- A. Extract Question Text ---
            let questionText = "";
            const headingEl = qEl.querySelector('[role="heading"], .M7eMe, .HoN1Ob, .F3n8vf');
            if (headingEl) {
                const clone = headingEl.cloneNode(true);
                clone.querySelectorAll('.R4nke, .DqBBlb, .vRMGwf').forEach(e => e.remove());
                questionText = clone.innerText.trim();
            }

            if (!questionText) {
                const textNodes = Array.from(qEl.querySelectorAll('div, span')).filter(d => d.children.length === 0 && d.innerText.trim().length > 3);
                if (textNodes.length > 0) {
                    questionText = textNodes[0].innerText.trim();
                }
            }

            // Remove leading question numbers like "1. ", "س1: ", "1- "
            questionText = questionText.replace(/^[\d٠-٩]+[\s\.\:\-\)\/]+\s*/, '').trim();
            if (!questionText) {
                questionText = `السؤال ${qNum}`;
            }

            // --- B. Check if answer was marked Wrong / Points score ---
            let isWrong = false;
            const pointEl = qEl.querySelector('.DqBBlb, .R4nke, .c2gzEf, [aria-label*="نقطة"], [aria-label*="point"]');
            const qTextAll = qEl.innerText || "";
            
            // Check for 0/1 or 0 / 1 or ٠/١ or wrong icons
            const zeroPointMatch = qTextAll.match(/\b0\s*[\/|\\]\s*[1-9]\b/) || qTextAll.match(/\b٠\s*[\/|\\]\s*[١-٩]\b/);
            const wrongIcon = qEl.querySelector('.vRMGwf[data-is-correct="false"], svg[fill="#d93025"], .M9Bg4d');
            
            if (zeroPointMatch || wrongIcon) {
                isWrong = true;
            } else if (pointEl) {
                const ptText = pointEl.innerText.trim();
                if (ptText.startsWith("0") || ptText.startsWith("٠")) {
                    isWrong = true;
                }
            }

            // Also check if a "Correct Answer" box is visible (Google Forms displays this only for wrong answers)
            const correctBox = qEl.querySelector('.c2gzEf, .R305vd, .i9L0be, .N3G8yb, .YMEQ1d');
            if (correctBox && (correctBox.innerText.includes("الإجابة الصحيحة") || correctBox.innerText.toLowerCase().includes("correct answer"))) {
                isWrong = true;
            }

            if (isWrong) {
                wrongIndices.push(qNum);
            }

            // --- C. Extract Options ---
            const optionElements = Array.from(qEl.querySelectorAll('[role="radio"], [role="checkbox"], .docssharedWizToggleLabeledContainer, .SG0tKc, .nWQ3Re, .d7L4cf'));
            const options = [];
            let checkedOptionText = null;
            let visuallyCorrectOptionText = null;

            optionElements.forEach(optEl => {
                let optText = "";
                const labelEl = optEl.querySelector('.aDTYNe, .OvPDhc, .ulDsOb, [dir="auto"]') || optEl;
                if (labelEl) {
                    optText = labelEl.innerText.trim();
                }

                // Strip leading option letters like "أ- ", "1. "
                optText = optText.replace(/^[أ-يA-Za-z\d٠-٩][\s\.\:\-\)\/]+\s*/, '').trim();

                if (optText && !options.includes(optText)) {
                    options.push(optText);
                }

                const isChecked = optEl.getAttribute('aria-checked') === 'true' || 
                                  optEl.querySelector('[aria-checked="true"]') !== null ||
                                  optEl.classList.contains('N2RpBe') ||
                                  optEl.querySelector('.u3bW4e') !== null;

                if (isChecked && optText) {
                    checkedOptionText = optText;
                }

                const hasGreen = optEl.querySelector('svg[fill="#137333"], svg[fill="#188038"], .freebirdFormviewerViewItemsRadioCorrectIcon');
                if (hasGreen && optText) {
                    visuallyCorrectOptionText = optText;
                }
            });

            if (options.length === 0) {
                options.push("نعم", "لا");
            }

            // --- D. Extract Correct Answer ---
            let correctAnswer = "";

            if (correctBox) {
                let boxText = correctBox.innerText.trim();
                boxText = boxText.replace(/^(الإجابة الصحيحة|الإجابات الصحيحة|Correct answer|Correct answers)[\:\s\n]*/i, '').trim();
                if (boxText) {
                    const matched = options.find(o => o.trim() === boxText || boxText.includes(o.trim()) || o.trim().includes(boxText));
                    if (matched) {
                        correctAnswer = matched;
                    } else {
                        correctAnswer = boxText;
                        if (!options.includes(correctAnswer)) {
                            options.push(correctAnswer);
                        }
                    }
                }
            }

            if (!correctAnswer && visuallyCorrectOptionText) {
                correctAnswer = visuallyCorrectOptionText;
            }

            if (!correctAnswer) {
                if (!isWrong && checkedOptionText) {
                    correctAnswer = checkedOptionText;
                } else if (checkedOptionText) {
                    correctAnswer = checkedOptionText;
                } else {
                    correctAnswer = options[0];
                }
            }

            // Ensure correctAnswer is in options
            if (!options.includes(correctAnswer)) {
                const match = options.find(o => o.trim().toLowerCase() === correctAnswer.trim().toLowerCase());
                if (match) {
                    correctAnswer = match;
                } else {
                    options.push(correctAnswer);
                }
            }

            // --- E. Extract Explanation / Feedback ---
            let explanation = "";
            const feedbackEl = qEl.querySelector('.g4k55c, .freebirdFormviewerViewItemsGradingFeedback, .freebirdFormviewerViewItemsGradingFeedbackContainer');
            if (feedbackEl) {
                explanation = feedbackEl.innerText.replace(/^(ملاحظات|تعليقات|Feedback)[\:\s\n]*/i, '').trim();
            }

            questions.push({
                question: questionText,
                options: options,
                answer: correctAnswer,
                explanation: explanation || ""
            });
        });

        if (questions.length === 0) {
            return {
                success: false,
                error: "لم يتم العثور على أسئلة في هذه الصفحة. يرجى التأكد من فتح صفحة (عرض النتيجة / View score) في Google Forms."
            };
        }

        return {
            success: true,
            data: {
                quiz_name: quizTitle,
                wrong: wrongIndices,
                questions: questions
            }
        };

    } catch (err) {
        return {
            success: false,
            error: "حدث خطأ أثناء قراءة كود الصفحة: " + err.message
        };
    }
}

// Support execution via message passing
if (typeof chrome !== 'undefined' && chrome.runtime && chrome.runtime.onMessage) {
    chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
        if (request.action === "EXTRACT_QUIZ") {
            const res = extractGoogleFormsQuiz();
            sendResponse(res);
        }
        return true;
    });
}
