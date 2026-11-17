const title = document.querySelector("#markdown-title");
const status = document.querySelector("#markdown-status");
const article = document.querySelector("#markdown-content");
const pdfLink = document.querySelector("#markdown-pdf-link");
const downloadLink = document.querySelector("#markdown-download");
const regenerateButton = document.querySelector("#markdown-regenerate");
const originalButton = document.querySelector("#markdown-language-original");
const chineseButton = document.querySelector("#markdown-language-zh");
const filename = decodeURIComponent(location.pathname.split("/").pop());
const api = `/api/papers/${encodeURIComponent(filename)}/markdown`;
let busy = false;
let retryAction = "load";
let language = new URLSearchParams(location.search).get("language") === "zh" ? "zh" : "original";
let loadSequence = 0;

pdfLink.href = `/read/${encodeURIComponent(filename)}`;

function languageApi(language) {
  return `${api}?language=${language}`;
}

function selectLanguage(nextLanguage) {
  language = nextLanguage;
  originalButton.setAttribute("aria-pressed", String(language === "original"));
  chineseButton.setAttribute("aria-pressed", String(language === "zh"));
  const url = new URL(location.href);
  if (language === "zh") url.searchParams.set("language", "zh");
  else url.searchParams.delete("language");
  history.replaceState(null, "", url);
}

async function readMarkdownResponse(response, fallback) {
  const text = await response.text();
  let result;
  try {
    result = JSON.parse(text);
  } catch {
    throw new Error(`${fallback}（HTTP ${response.status}），服务返回异常，请稍后重试。`);
  }
  if (!response.ok) {
    throw new Error(typeof result?.detail === "string" ? result.detail : `${fallback}（HTTP ${response.status}）`);
  }
  if (!result || typeof result !== "object" || !["ready", "generating", "not_generated"].includes(result.status)) {
    throw new Error(`${fallback}：服务返回格式无效，请稍后重试。`);
  }
  return result;
}

function showDocument(document) {
  title.textContent = document.title || "Markdown 阅读器";
  article.innerHTML = document.html || "";
  downloadLink.href = `${api}/download?language=${language}`;
  downloadLink.textContent = language === "zh" ? "下载中文 .md" : "下载原文 .md";
  downloadLink.hidden = document.status !== "ready";
  regenerateButton.hidden = document.status === "generating";
  regenerateButton.textContent = document.status === "ready" ? (language === "zh" ? "重新翻译" : "重新生成") : (language === "zh" ? "重试翻译" : "重试生成");
  retryAction = "generate";
  status.classList.remove("is-error");
  status.textContent = document.status === "generating" ? (language === "zh" ? "中文正在翻译…" : "Markdown 正在生成…") : "";
}

async function loadDocument() {
  const sequence = ++loadSequence;
  const currentLanguage = language;
  try {
    const response = await fetch(languageApi(currentLanguage));
    const document = await readMarkdownResponse(response, currentLanguage === "zh" ? "无法读取中文译文" : "无法读取 Markdown");
    if (sequence !== loadSequence) return;
    showDocument(document);
    if (document.status === "not_generated") {
      await generateDocument(false, sequence, currentLanguage);
    } else if (document.status === "generating") {
      window.setTimeout(() => {
        if (sequence === loadSequence) loadDocument();
      }, 2000);
    }
  } catch (error) {
    if (sequence !== loadSequence) return;
    status.classList.add("is-error");
    status.textContent = error.message;
    regenerateButton.hidden = false;
    regenerateButton.textContent = currentLanguage === "zh" ? "重试翻译" : "重试加载";
    retryAction = "load";
  }
}

async function generateDocument(force, sequence = loadSequence, currentLanguage = language) {
  if (busy) return;
  busy = true;
  retryAction = "generate";
  regenerateButton.disabled = true;
  regenerateButton.hidden = true;
  status.classList.remove("is-error");
  status.textContent = currentLanguage === "zh" ? "正在翻译论文，较长文档需要一些时间…" : "正在将完整 OCR 结果交给模型整理，论文较长时需要一些时间…";
  try {
    const response = await fetch(`${languageApi(currentLanguage)}${force ? "&force=true" : ""}`, { method: "POST" });
    const document = await readMarkdownResponse(response, currentLanguage === "zh" ? "中文翻译失败" : "Markdown 生成失败");
    if (sequence === loadSequence) showDocument(document);
  } catch (error) {
    if (sequence === loadSequence) {
      status.classList.add("is-error");
      status.textContent = error.message;
      regenerateButton.hidden = false;
      regenerateButton.textContent = currentLanguage === "zh" ? "重试翻译" : "重试生成";
    }
  } finally {
    busy = false;
    regenerateButton.disabled = false;
  }
}

originalButton.addEventListener("click", () => {
  if (language === "original") return;
  selectLanguage("original");
  article.replaceChildren();
  loadDocument();
});
chineseButton.addEventListener("click", () => {
  if (language === "zh") return;
  selectLanguage("zh");
  article.replaceChildren();
  loadDocument();
});
regenerateButton.addEventListener("click", () => {
  if (retryAction === "load") return loadDocument();
  return generateDocument(downloadLink.hidden === false);
});
selectLanguage(language);
loadDocument();
