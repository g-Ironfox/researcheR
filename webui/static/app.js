const form = document.querySelector("#upload-form");
const fileInput = document.querySelector("#file-input");
const message = document.querySelector("#message");
const paperCount = document.querySelector("#paper-count");
const paperList = document.querySelector("#paper-list");
const starFilter = document.querySelector("#star-filter");
const titleSearch = document.querySelector("#title-search");
const pendingList = document.querySelector("#pending-list");
const fileName = document.querySelector(".file-copy strong");
const paperDialog = document.querySelector("#paper-dialog");
const paperForm = document.querySelector("#paper-form");
const paperTitle = document.querySelector("#paper-title");
const paperVenue = document.querySelector("#paper-venue");
const paperAbstract = document.querySelector("#paper-abstract");
const paperReference = document.querySelector("#paper-reference");
const paperPreview = document.querySelector("#paper-preview");
const dialogMessage = document.querySelector("#dialog-message");
const deleteButton = document.querySelector("#delete-paper");
const saveButton = document.querySelector("#save-paper");
const paperPagination = document.querySelector("#paper-pagination");
const previousPage = document.querySelector("#previous-page");
const nextPage = document.querySelector("#next-page");
const pageStatus = document.querySelector("#page-status");
let selectedFilename = "";
let papers = { items: [], total: 0, page: 1, page_count: 1 };
let currentPage = 1;
let pageCount = 1;
let papersRequestId = 0;
let ocrRefreshTimer;
const pendingOcr = new Set();

function renderPapers() {
  paperList.replaceChildren();
  paperCount.textContent = papers.total;

  if (papers.items.length === 0) {
    paperPagination.hidden = true;
    const empty = document.createElement("li");
    empty.className = "empty";
    empty.textContent = titleSearch.value.trim()
      ? "没有找到匹配标题的论文。"
      : starFilter.getAttribute("aria-pressed") === "true"
        ? "还没有星标论文。"
        : "还没有论文，上传一篇 PDF 开始吧。";
    paperList.append(empty);
    return;
  }

  currentPage = papers.page;
  pageCount = papers.page_count;
  pageStatus.textContent = `${currentPage} / ${pageCount}`;
  paperPagination.hidden = pageCount <= 1;
  previousPage.setAttribute("aria-disabled", String(currentPage === 1));
  nextPage.setAttribute("aria-disabled", String(currentPage === pageCount));

  papers.items.forEach((paper) => {
    const item = document.createElement("li");
    const openButton = document.createElement("button");
    openButton.type = "button";
    openButton.className = "paper-button";
    openButton.textContent = paper.name;
    openButton.addEventListener("click", () => openPaper(paper.filename));
    const details = document.createElement("div");
    details.className = "paper-details";
    details.append(openButton);
    if (paper.venue?.trim()) {
      const venue = document.createElement("span");
      venue.className = "paper-venue";
      venue.textContent = paper.venue;
      details.append(venue);
    }
    const readLink = document.createElement("a");
    readLink.className = "read-link";
    readLink.href = `/read/${encodeURIComponent(paper.filename)}`;
    readLink.target = "_blank";
    readLink.rel = "noopener";
    readLink.textContent = "阅读";
    const markdownLink = document.createElement("a");
    markdownLink.className = "markdown-link";
    markdownLink.href = `/markdown/${encodeURIComponent(paper.filename)}`;
    markdownLink.textContent = paper.markdown_available ? "Markdown" : "生成 Markdown";
    markdownLink.setAttribute("aria-label", `打开《${paper.name}》的 Markdown 阅读器`);
    const size = document.createElement("span");
    size.textContent = `${paper.size_kb} KB`;
    const actions = document.createElement("div");
    actions.className = "paper-actions";
    const starButton = document.createElement("button");
    starButton.type = "button";
    starButton.className = "star-button";
    starButton.innerHTML = '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="m12 3 2.7 5.5 6.1.9-4.4 4.3 1 6.1-5.4-2.9-5.4 2.9 1-6.1-4.4-4.3 6.1-.9L12 3Z"/></svg>';
    starButton.classList.toggle("is-starred", paper.starred);
    starButton.title = paper.starred ? "取消星标" : "添加星标";
    starButton.setAttribute("aria-label", starButton.title);
    starButton.setAttribute("aria-pressed", String(paper.starred));
    starButton.addEventListener("click", async () => {
      if (starButton.dataset.busy === "true") return;
      starButton.dataset.busy = "true";
      try {
        const response = await fetch(`/api/papers/${encodeURIComponent(paper.filename)}/star`, {
          method: "PUT",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ starred: !paper.starred }),
        });
        if (!response.ok) throw new Error("更新星标失败");
        paper.starred = (await response.json()).starred;
        starButton.classList.toggle("is-starred", paper.starred);
        starButton.title = paper.starred ? "取消星标" : "添加星标";
        starButton.setAttribute("aria-label", starButton.title);
        starButton.setAttribute("aria-pressed", String(paper.starred));
        loadPapers(currentPage).catch((error) => { message.textContent = error.message; });
      } catch (error) {
        message.textContent = error.message;
      } finally {
        starButton.dataset.busy = "false";
      }
    });
    const ocrInfo = document.createElement("span");
    ocrInfo.className = "ocr-info";
    const ocrStatus = document.createElement("span");
    const ocrLabels = {
      processing: "OCR 识别中…",
      completed: "OCR 完成",
      partial: "OCR 不完整",
      failed: "OCR 失败",
      not_configured: "OCR 未配置",
      not_started: "未 OCR",
    };
    const processing = pendingOcr.has(paper.filename) || paper.ocr_status === "processing";
    ocrStatus.textContent = ocrLabels[processing ? "processing" : paper.ocr_status] || "未 OCR";
    ocrStatus.title = processing ? ocrStatus.textContent : paper.ocr_error || ocrStatus.textContent;
    ocrInfo.append(ocrStatus);
    if (paper.ocr_downloadable) {
      const ocrLink = document.createElement("a");
      ocrLink.className = "read-link";
      ocrLink.href = "/api/papers/" + encodeURIComponent(paper.filename) + "/ocr";
      ocrLink.download = "";
      ocrLink.textContent = "JSON";
      ocrLink.title = "下载 OCR JSON";
      ocrLink.setAttribute("aria-label", "下载《" + paper.name + "》的 OCR JSON");
      ocrInfo.append(ocrLink);
    }
    if (processing || paper.ocr_status !== "completed") {
      const ocrButton = document.createElement("button");
      ocrButton.type = "button";
      ocrButton.className = "ocr-button";
      ocrButton.textContent = processing ? "识别中…" : paper.ocr_status === "not_started" ? "开始 OCR" : "重新 OCR";
      ocrButton.disabled = processing;
      ocrButton.setAttribute("aria-label", `${ocrButton.textContent}\uFF1A\u300A${paper.name}\u300B`);
      ocrButton.addEventListener("click", () => retryOcr(paper));
      ocrInfo.append(ocrButton);
    }
    actions.append(starButton, readLink);
    if (["completed", "partial"].includes(paper.ocr_status)) actions.append(markdownLink);
    actions.append(ocrInfo, size);
    item.append(details, actions);
    paperList.append(item);
  });
}

async function retryOcr(paper) {
  if (pendingOcr.has(paper.filename) || paper.ocr_status === "processing") return;
  pendingOcr.add(paper.filename);
  renderPapers();
  message.textContent = `正在为《${paper.name}》逐页 OCR，请等待识别完成…`;
  try {
    const response = await fetch(`/api/papers/${encodeURIComponent(paper.filename)}/ocr`, { method: "POST" });
    const result = await response.json();
    if (!response.ok) throw new Error(result.detail || "OCR 请求失败");
    Object.assign(paper, result);
    message.textContent = result.ocr_status === "completed"
      ? `《${paper.name}》OCR 完成，可下载 JSON。`
      : `《${paper.name}》OCR 未完成：${result.ocr_error || "请稍后重试"}`;
  } catch (error) {
    message.textContent = error.message;
  } finally {
    pendingOcr.delete(paper.filename);
    renderPapers();
    loadPapers(currentPage).catch((error) => { message.textContent = error.message; });
  }
}

async function openPaper(filename) {
  selectedFilename = filename;
  paperForm.reset();
  dialogMessage.textContent = "正在加载...";
  paperPreview.hidden = true;
  paperPreview.removeAttribute("src");
  paperPreview.onload = () => { paperPreview.hidden = false; };
  paperPreview.src = `/api/papers/${encodeURIComponent(filename)}/preview`;
  paperDialog.showModal();
  try {
    const response = await fetch(`/api/papers/${encodeURIComponent(filename)}`);
    if (!response.ok) throw new Error("无法加载论文信息");
    const metadata = await response.json();
    if (selectedFilename !== filename || !paperDialog.open) return;
    paperTitle.value = metadata.title;
    paperVenue.value = metadata.venue;
    paperAbstract.value = metadata.abstract;
    paperReference.value = metadata.reference;
    dialogMessage.textContent = "";
  } catch (error) {
    if (selectedFilename === filename && paperDialog.open) dialogMessage.textContent = error.message;
  }
}

async function loadPapers(page = currentPage) {
  const requestId = ++papersRequestId;
  const params = new URLSearchParams({
    page: String(page),
    q: titleSearch.value.trim(),
    starred: String(starFilter.getAttribute("aria-pressed") === "true"),
  });
  const response = await fetch(`/api/papers?${params}`);
  if (!response.ok) throw new Error("无法加载论文列表");
  const result = await response.json();
  if (requestId !== papersRequestId) return;
  papers = { items: result.papers, ...result };
  renderPapers();
  clearTimeout(ocrRefreshTimer);
  if (papers.items.some((paper) => paper.ocr_status === "processing")) {
    ocrRefreshTimer = setTimeout(() => {
      loadPapers(currentPage).catch((error) => { message.textContent = error.message; });
    }, 2000);
  }
}

starFilter.addEventListener("click", () => {
  starFilter.setAttribute("aria-pressed", String(starFilter.getAttribute("aria-pressed") !== "true"));
  currentPage = 1;
  loadPapers(1).catch((error) => { message.textContent = error.message; });
});

titleSearch.addEventListener("input", () => {
  currentPage = 1;
  loadPapers(1).catch((error) => { message.textContent = error.message; });
});

previousPage.addEventListener("click", () => {
  if (currentPage === 1) return;
  loadPapers(currentPage - 1).catch((error) => { message.textContent = error.message; });
});

nextPage.addEventListener("click", () => {
  if (currentPage >= pageCount) return;
  loadPapers(currentPage + 1).catch((error) => { message.textContent = error.message; });
});

function renderPendingFiles(files) {
  pendingList.replaceChildren();
  fileName.textContent = files.length ? `已选择 ${files.length} 个文件` : "选择 PDF 文件";
  files.forEach((file) => {
    const item = document.createElement("li");
    const name = document.createElement("span");
    name.className = "pending-name";
    name.textContent = file.name;
    name.title = file.name;
    const size = document.createElement("span");
    size.className = "pending-size";
    size.textContent = `${Math.max(1, Math.round(file.size / 1024))} KB`;
    item.append(name, size);
    pendingList.append(item);
  });
}

fileInput.addEventListener("change", () => {
  renderPendingFiles([...fileInput.files]);
});

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (!fileInput.files.length || form.dataset.busy) return;

  form.dataset.busy = "true";
  const uploadBody = new FormData(form);
  const uploadButton = form.querySelector('button[type="submit"]');
  uploadButton.disabled = true;
  fileInput.disabled = true;
  message.textContent = "正在上传并逐页 OCR，请等待识别完成...";
  try {
    const response = await fetch("/upload", { method: "POST", body: uploadBody });
    if (!response.ok) {
      const detail = await response.json();
      throw new Error(detail.detail || "上传失败");
    }
    const result = await response.json();
    form.reset();
    renderPendingFiles([]);
    message.textContent = `已上传 ${result.filenames.length} 个文件` +
      (result.duplicates.length ? `，跳过 ${result.duplicates.length} 个重复文件：${result.duplicates.join("、")}` : "");
    await loadPapers();
  } catch (error) {
    message.textContent = error.message;
  } finally {
    delete form.dataset.busy;
  }
});

document.querySelector("#close-dialog").addEventListener("click", () => paperDialog.close());

deleteButton.addEventListener("click", async () => {
  if (!window.confirm(`确定删除《${paperTitle.value}》吗？此操作无法撤销。`)) return;
  deleteButton.disabled = true;
  saveButton.disabled = true;
  dialogMessage.textContent = "正在删除...";
  try {
    const response = await fetch(`/api/papers/${encodeURIComponent(selectedFilename)}`, { method: "DELETE" });
    if (!response.ok) {
      const result = await response.json();
      throw new Error(result.detail || "删除失败");
    }
    paperDialog.close();
    await loadPapers();
  } catch (error) {
    dialogMessage.textContent = error.message;
  } finally {
    deleteButton.disabled = false;
    saveButton.disabled = false;
  }
});

paperForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  saveButton.disabled = true;
  deleteButton.disabled = true;
  dialogMessage.textContent = "正在保存...";
  try {
    const response = await fetch(`/api/papers/${encodeURIComponent(selectedFilename)}`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        title: paperTitle.value,
        venue: paperVenue.value,
        abstract: paperAbstract.value,
        reference: paperReference.value,
      }),
    });
    if (!response.ok) throw new Error("保存失败");
    dialogMessage.textContent = "已保存";
    await loadPapers();
  } catch (error) {
    dialogMessage.textContent = error.message;
  } finally {
    saveButton.disabled = false;
    deleteButton.disabled = false;
  }
});

loadPapers().catch((error) => { message.textContent = error.message; });
