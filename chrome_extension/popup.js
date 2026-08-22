let extractedData = null;

document.addEventListener("DOMContentLoaded", () => {
  initExtractor();

  // Attach Event Listeners
  document.getElementById("download-btn").addEventListener("click", downloadJSON);
  document.getElementById("copy-btn").addEventListener("click", copyJSON);
  document.getElementById("retry-btn").addEventListener("click", initExtractor);
  document.getElementById("toggle-preview").addEventListener("click", togglePreview);
  
  document.getElementById("quiz-name-input").addEventListener("input", (e) => {
    if (extractedData) {
      extractedData.quiz_name = e.target.value.trim() || "كويز بدون عنوان";
    }
  });
});

async function initExtractor() {
  showState("loading");

  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    
    if (!tab || !tab.url || !tab.url.includes("docs.google.com/forms/")) {
      showState("not-forms");
      return;
    }

    // Inject extractor.js into active tab
    const results = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      files: ["extractor.js"]
    });

    // Execute extractGoogleFormsQuiz in the tab context
    const execResults = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      func: () => {
        if (typeof extractGoogleFormsQuiz === "function") {
          return extractGoogleFormsQuiz();
        }
        return { success: false, error: "دالة الاستخراج غير معرّفة" };
      }
    });

    if (execResults && execResults[0] && execResults[0].result) {
      const response = execResults[0].result;
      if (response.success && response.data) {
        extractedData = response.data;
        renderQuizData(extractedData);
        showState("content");
      } else {
        showError(response.error || "تعذر العثور على أسئلة في هذه الصفحة.");
      }
    } else {
      showError("تعذر تنفيذ سكريبت الاستخراج على هذه الصفحة.");
    }

  } catch (err) {
    showError("حدث خطأ أثناء التواصل مع الصفحة: " + err.message);
  }
}

function renderQuizData(data) {
  document.getElementById("quiz-name-input").value = data.quiz_name || "";
  document.getElementById("total-count").innerText = data.questions ? data.questions.length : 0;
  
  const wrongCount = data.wrong ? data.wrong.length : 0;
  document.getElementById("wrong-count").innerText = wrongCount;
  document.getElementById("preview-count").innerText = data.questions ? data.questions.length : 0;

  // Render Preview
  const previewList = document.getElementById("preview-list");
  previewList.innerHTML = "";

  if (data.questions && data.questions.length > 0) {
    data.questions.forEach((q, idx) => {
      const qNum = idx + 1;
      const isWrong = data.wrong && data.wrong.includes(qNum);

      const itemEl = document.createElement("div");
      itemEl.className = `preview-item ${isWrong ? "is-wrong" : ""}`;

      const titleEl = document.createElement("div");
      titleEl.className = "q-title";
      titleEl.innerHTML = `<b>س${qNum}:</b> ${escapeHtml(q.question)} ${isWrong ? '<span style="color:#f87171; font-size:10px;">(خاطئ ❌)</span>' : ''}`;
      itemEl.appendChild(titleEl);

      const optList = document.createElement("div");
      optList.className = "opt-list";

      q.options.forEach(opt => {
        const isAnswer = opt.trim() === q.answer.trim();
        const optEl = document.createElement("div");
        optEl.className = `opt-item ${isAnswer ? "is-answer" : ""}`;
        optEl.innerHTML = `${isAnswer ? '✓ ' : '• '} ${escapeHtml(opt)}`;
        optList.appendChild(optEl);
      });

      itemEl.appendChild(optList);

      if (q.explanation) {
        const expEl = document.createElement("div");
        expEl.style.fontSize = "10px";
        expEl.style.color = "#94a3b8";
        expEl.style.marginTop = "4px";
        expEl.innerHTML = `💡 <i>${escapeHtml(q.explanation)}</i>`;
        itemEl.appendChild(expEl);
      }

      previewList.appendChild(itemEl);
    });
  }
}

function downloadJSON() {
  if (!extractedData) return;

  const jsonString = JSON.stringify(extractedData, null, 2);
  const blob = new Blob([jsonString], { type: "application/json;charset=utf-8" });
  const url = URL.createObjectURL(blob);

  let cleanName = (extractedData.quiz_name || "quiz")
    .replace(/[\\\/\:\*\?\"\<\>\|]/g, "_")
    .replace(/\s+/g, "_");

  if (!cleanName.endsWith(".json")) {
    cleanName += ".json";
  }

  const a = document.createElement("a");
  a.href = url;
  a.download = cleanName;
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
}

function copyJSON() {
  if (!extractedData) return;

  const jsonString = JSON.stringify(extractedData, null, 2);
  navigator.clipboard.writeText(jsonString).then(() => {
    const feedback = document.getElementById("copy-feedback");
    feedback.classList.remove("hidden");
    setTimeout(() => {
      feedback.classList.add("hidden");
    }, 2500);
  });
}

function togglePreview() {
  const previewList = document.getElementById("preview-list");
  const arrow = document.getElementById("preview-arrow");
  const isHidden = previewList.classList.contains("hidden");

  if (isHidden) {
    previewList.classList.remove("hidden");
    arrow.innerText = "▲";
  } else {
    previewList.classList.add("hidden");
    arrow.innerText = "▼";
  }
}

function showState(stateName) {
  document.getElementById("loading").classList.add("hidden");
  document.getElementById("not-forms").classList.add("hidden");
  document.getElementById("error-box").classList.add("hidden");
  document.getElementById("content").classList.add("hidden");

  if (stateName === "loading") document.getElementById("loading").classList.remove("hidden");
  if (stateName === "not-forms") document.getElementById("not-forms").classList.remove("hidden");
  if (stateName === "error") document.getElementById("error-box").classList.remove("hidden");
  if (stateName === "content") document.getElementById("content").classList.remove("hidden");
}

function showError(msg) {
  showState("error");
  document.getElementById("error-message").innerText = msg;
}

function escapeHtml(text) {
  if (!text) return "";
  return String(text)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#039;");
}
