let extractedData = null;

document.addEventListener("DOMContentLoaded", () => {
  initExtractor();

  // Attach Event Listeners
  document.getElementById("download-excel-btn").addEventListener("click", downloadExcel);
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
    
    if (!tab || !tab.url) {
      showState("not-forms");
      return;
    }

    // Must be on Google Forms domain
    if (!tab.url.includes("docs.google.com/forms/")) {
      showState("not-forms");
      return;
    }

    // Step 1: Inject extractor.js into the tab
    await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      files: ["extractor.js"]
    });

    // Step 2: Call the extraction function (already injected above)
    const execResults = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      func: () => {
        if (typeof extractGoogleFormsQuiz === "function") {
          return extractGoogleFormsQuiz();
        }
        return { success: false, error: "دالة الاستخراج غير موجودة — يرجى إعادة تحميل الصفحة وحاول مرة أخرى." };
      }
    });

    if (!execResults || !execResults[0] || execResults[0].result === undefined) {
      showError("لم يتم الحصول على نتيجة من الصفحة. تأكد من فتح صفحة (عرض النتيجة) في Google Forms.");
      return;
    }

    const response = execResults[0].result;

    if (response && response.success && response.data) {
      extractedData = response.data;

      // Capture high-res passage screenshots if any passages were detected
      await capturePassageScreenshots(tab, extractedData);

      renderQuizData(extractedData);
      showState("content");
    } else {
      showError(response && response.error ? response.error : "تعذر العثور على أسئلة في هذه الصفحة.");
    }

  } catch (err) {
    // Handle common permission errors
    const msg = err.message || "";
    if (msg.includes("Cannot access") || msg.includes("permission")) {
      showError("لا يمكن الوصول إلى هذه الصفحة. تأكد من أن الصفحة محملة بالكامل ثم أعد المحاولة.");
    } else {
      showError("خطأ غير متوقع: " + msg);
    }
  }
}

async function capturePassageScreenshots(tab, data) {
  if (!data || !data.questions) return;
  
  const passageIds = [...new Set(data.questions.map(q => q.passage_id).filter(Boolean))];
  if (passageIds.length === 0) return;

  const passageImages = {};

  for (const pid of passageIds) {
    try {
      // 1. Locate element and scroll into view
      const execRes = await chrome.scripting.executeScript({
        target: { tabId: tab.id },
        func: (id) => {
          const el = document.querySelector(`[data-qudrat-passage-id="${id}"]`);
          if (!el) return null;
          el.scrollIntoView({ behavior: 'instant', block: 'start' });
          const r = el.getBoundingClientRect();
          return {
            x: r.left,
            y: r.top,
            width: r.width,
            height: r.height,
            dpr: window.devicePixelRatio || 1,
            vh: window.innerHeight
          };
        },
        args: [pid]
      });

      if (!execRes || !execRes[0] || !execRes[0].result) continue;
      const rect = execRes[0].result;

      // Small delay for scroll and repaint to settle
      await new Promise(r => setTimeout(r, 180));

      // 2. Capture the visible tab
      const tabDataUrl = await chrome.tabs.captureVisibleTab(tab.windowId, { format: 'png' });

      // 3. Crop to the element's bounding rect
      const croppedUrl = await cropImage(tabDataUrl, rect);
      if (croppedUrl) {
        passageImages[pid] = croppedUrl;
      }
    } catch (err) {
      console.warn("Failed to capture screenshot for passage", pid, err);
    }
  }

  // 4. Attach cropped screenshot to questions
  data.questions.forEach(q => {
    if (q.passage_id && passageImages[q.passage_id]) {
      q.passage_image = passageImages[q.passage_id];
    }
  });
}

function cropImage(dataUrl, rect) {
  return new Promise((resolve) => {
    const img = new Image();
    img.onload = () => {
      try {
        const dpr = rect.dpr || 1;
        const canvas = document.createElement('canvas');
        
        // Element dimensions on canvas
        const w = Math.max(10, Math.round(rect.width * dpr));
        const maxH = Math.round((rect.vh || window.innerHeight) * dpr);
        const h = Math.max(10, Math.min(Math.round(rect.height * dpr), maxH - Math.max(0, Math.round(rect.y * dpr))));

        canvas.width = w;
        canvas.height = h;

        const ctx = canvas.getContext('2d');
        const sx = Math.max(0, Math.round(rect.x * dpr));
        const sy = Math.max(0, Math.round(rect.y * dpr));

        ctx.drawImage(img, sx, sy, w, h, 0, 0, w, h);
        resolve(canvas.toDataURL('image/png'));
      } catch (e) {
        resolve(null);
      }
    };
    img.onerror = () => resolve(null);
    img.src = dataUrl;
  });
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

      // Render passage screenshot preview if available
      if (q.passage_image) {
        const pImgEl = document.createElement("div");
        pImgEl.style.margin = "6px 0";
        pImgEl.style.padding = "4px";
        pImgEl.style.border = "1px solid #3b82f6";
        pImgEl.style.borderRadius = "6px";
        pImgEl.style.background = "#1e293b";
        pImgEl.innerHTML = `
          <div style="font-size: 10px; color: #60a5fa; margin-bottom: 4px; display: flex; align-items: center; gap: 4px;">
            <span>📸</span> <b>لقطة شاشة لقطعة القراءة:</b>
          </div>
          <img src="${q.passage_image}" style="max-width: 100%; max-height: 120px; object-fit: contain; border-radius: 4px; border: 1px solid #475569;" alt="Passage Screenshot" />
        `;
        itemEl.appendChild(pImgEl);
      }

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

function downloadExcel() {
  if (!extractedData || !extractedData.questions) return;

  const headers = ["السؤال", "الخيار (أ)", "الخيار (ب)", "الخيار (ج)", "الخيار (د)", "الإجابة الصحيحة", "الشرح", "خطأ (1/0)"];
  const rows = [headers];

  const wrongSet = new Set(extractedData.wrong || []);

  extractedData.questions.forEach((q, idx) => {
    const qNum = idx + 1;
    const isWrong = wrongSet.has(qNum) ? "1" : "0";
    const opts = q.options || [];
    const optA = opts[0] || "";
    const optB = opts[1] || "";
    const optC = opts[2] || "";
    const optD = opts[3] || "";
    const ans = q.answer || "";
    const exp = q.explanation || "";

    rows.push([q.question, optA, optB, optC, optD, ans, exp, isWrong]);
  });

  const csvContent = rows.map(r => 
    r.map(cell => {
      let str = String(cell || "").replace(/"/g, '""');
      if (str.includes(",") || str.includes("\n") || str.includes('"')) {
        return `"${str}"`;
      }
      return str;
    }).join(",")
  ).join("\r\n");

  const blob = new Blob(["\uFEFF" + csvContent], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);

  let cleanName = (extractedData.quiz_name || "quiz")
    .replace(/[\\\/\:\*\?\"\<\>\|]/g, "_")
    .replace(/\s+/g, "_");

  if (!cleanName.endsWith(".csv")) {
    cleanName += ".csv";
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
