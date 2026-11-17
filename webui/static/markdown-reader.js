const title = document.querySelector("#markdown-title");
const status = document.querySelector("#markdown-status");
const article = document.querySelector("#markdown-content");
const pdfLink = document.querySelector("#markdown-pdf-link");
const downloadLink = document.querySelector("#markdown-download");
const regenerateButton = document.querySelector("#markdown-regenerate");
const filename = decodeURIComponent(location.pathname.split("/").pop());
const api = `/api/papers/${encodeURIComponent(filename)}/markdown`;
let busy = false;
let retryAction = "load";

pdfLink.href = `/read/${encodeURIComponent(filename)}`;

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
  downloadLink.href = `${api}/download`;
  downloadLink.hidden = document.status !== "ready";
  regenerateButton.hidden = document.status === "generating";
  regenerateButton.textContent = document.status === "ready" ? "重新生成" : "重试生成";
  retryAction = "generate";
  status.classList.remove("is-error");
  status.textContent = document.status === "generating" ? "Markdown 正在生成…" : "";
}

async function loadDocument() {
  try {
    const response = await fetch(api);
    const document = await readMarkdownResponse(response, "无法读取 Markdown");
    showDocument(document);
    if (document.status === "not_generated") {
      await generateDocument(false);
    } else if (document.status === "generating") {
      window.setTimeout(loadDocument, 2000);
    }
  } catch (error) {
    status.classList.add("is-error");
    status.textContent = error.message;
    regenerateButton.hidden = false;
    regenerateButton.textContent = "重试加载";
    retryAction = "load";
  }
}

async function generateDocument(force) {
  if (busy) return;
  busy = true;
  retryAction = "generate";
  regenerateButton.disabled = true;
  regenerateButton.hidden = true;
  status.classList.remove("is-error");
  status.textContent = "正在将完整 OCR 结果交给模型整理，论文较长时需要一些时间…";
  try {
    const response = await fetch(`${api}${force ? "?force=true" : ""}`, { method: "POST" });
    const document = await readMarkdownResponse(response, "Markdown 生成失败");
    showDocument(document);
  } catch (error) {
    status.classList.add("is-error");
    status.textContent = error.message;
    regenerateButton.hidden = false;
    regenerateButton.textContent = "重试生成";
  } finally {
    busy = false;
    regenerateButton.disabled = false;
  }
}

regenerateButton.addEventListener("click", () => {
  if (retryAction === "load") return loadDocument();
  return generateDocument(downloadLink.hidden === false);
});
loadDocument();
