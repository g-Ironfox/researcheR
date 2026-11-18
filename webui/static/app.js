const form = document.querySelector("#upload-form");
const fileInput = document.querySelector("#file-input");
const message = document.querySelector("#message");
const paperCount = document.querySelector("#paper-count");
const paperList = document.querySelector("#paper-list");
const bulkToolbar = document.querySelector("#bulk-toolbar");
const selectPagePapers = document.querySelector("#select-page-papers");
const bulkSelectedCount = document.querySelector("#bulk-selected-count");
const bulkProgress = document.querySelector("#bulk-progress");
const bulkProgressTitle = document.querySelector("#bulk-progress-title");
const bulkResults = document.querySelector("#bulk-results");
const clearBulkProgress = document.querySelector("#clear-bulk-progress");
const taskList = document.querySelector("#task-list");
const taskCounts = document.querySelector("#task-counts");
const taskResultDialog = document.querySelector("#task-result-dialog");
const taskResultStatus = document.querySelector("#task-result-status");
const taskResultContent = document.querySelector("#task-result-content");
const applyTaskResult = document.querySelector("#apply-task-result");
let viewedTask = null;
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
const extractMetadataButton = document.querySelector("#extract-metadata");
const metadataStatus = document.querySelector("#metadata-status");
const extractReferenceButton = document.querySelector("#extract-reference");
const referencesList = document.querySelector("#references-list");
const referencesCount = document.querySelector("#references-count");
const referencesSearch = document.querySelector("#references-search");
const referencesStatus = document.querySelector("#references-status");
const addReferenceButton = document.querySelector("#add-reference");
const legacyReference = document.querySelector("#legacy-reference");
const paperPagination = document.querySelector("#paper-pagination");
const previousPage = document.querySelector("#previous-page");
const nextPage = document.querySelector("#next-page");
const pageStatus = document.querySelector("#page-status");
let selectedFilename = "";
let paperEditorVersion = 0;
let referenceEntries = [];
let referencesRevision = 0;
let metadataRevisions = { title: 0, abstract: 0 };
paperTitle.addEventListener("input", () => { metadataRevisions.title++; });
paperAbstract.addEventListener("input", () => { metadataRevisions.abstract++; });
let papers = { items: [], total: 0, page: 1, page_count: 1 };
let currentPage = 1;
let pageCount = 1;
let papersRequestId = 0;
const pendingOcr = new Set();
const selectedPapers = new Set();
const paperCache = new Map();
let bulkInProgress = false;

function updateBulkControls() {
  const visible = papers.items || [];
  const selectedOnPage = visible.filter(paper => selectedPapers.has(paper.filename)).length;
  selectPagePapers.checked = visible.length > 0 && selectedOnPage === visible.length;
  selectPagePapers.indeterminate = selectedOnPage > 0 && selectedOnPage < visible.length;
  selectPagePapers.disabled = bulkInProgress || visible.length === 0;
  document.querySelectorAll("[data-bulk-action]").forEach(button => {
    button.disabled = bulkInProgress || selectedPapers.size === 0;
  });
  bulkSelectedCount.textContent = "已选 " + selectedPapers.size + " 篇";
  bulkToolbar.hidden = visible.length === 0;
}

function renderPapers() {
  paperList.replaceChildren();
  bulkToolbar.hidden = papers.items.length === 0;
  const selectedOnPage = papers.items.filter(paper => selectedPapers.has(paper.filename)).length;
  selectPagePapers.checked = papers.items.length > 0 && selectedOnPage === papers.items.length;
  selectPagePapers.indeterminate = selectedOnPage > 0 && selectedOnPage < papers.items.length;
  selectPagePapers.disabled = bulkInProgress;
  document.querySelectorAll("[data-bulk-action]").forEach(button => { button.disabled = bulkInProgress || selectedPapers.size === 0; });
  bulkSelectedCount.textContent = "已选 " + selectedPapers.size + " 篇";
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
    const selectLabel = document.createElement("label");
    selectLabel.className = "paper-select-control";
    const select = document.createElement("input");
    select.type = "checkbox";
    select.className = "paper-select";
    select.checked = selectedPapers.has(paper.filename);
    select.disabled = bulkInProgress;
    select.setAttribute("aria-label", "选择《" + paper.name + "》");
    select.addEventListener("change", () => {
      if (select.checked) selectedPapers.add(paper.filename);
      else selectedPapers.delete(paper.filename);
      updateBulkControls();
    });
    selectLabel.append(select);
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
    item.append(selectLabel, details, actions);
    paperList.append(item);
  });
}

async function retryOcr(paper) {
  if (pendingOcr.has(paper.filename) || paper.ocr_status === "processing") return;
  pendingOcr.add(paper.filename);
  renderPapers();
  message.textContent = "正在提交《" + paper.name + "》的 OCR 任务…";
  try {
    await submitTask(paper.filename, "ocr", { skip_existing: false });
    message.textContent = "《" + paper.name + "》已加入后台任务队列。可在后台任务面板查看进度。";
  } catch (error) {
    message.textContent = error.message;
  } finally {
    pendingOcr.delete(paper.filename);
    loadPapers(currentPage).catch((error) => { message.textContent = error.message; });
  }
}


const referenceFields = [
  ["number", "原文编号"], ["title", "标题"], ["authors", "作者（每行一位）"],
  ["year", "年份"], ["venue", "期刊 / 会议"], ["volume", "卷"], ["issue", "期"],
  ["pages", "页码"], ["publisher", "出版社"], ["doi", "DOI"], ["url", "链接"],
  ["raw", "原始引用文本（必填）"],
];

function newReferenceEntry() {
  return Object.fromEntries([
    ...referenceFields.map(([field]) => [field, field === "authors" ? [] : ""]),
    ["source_pages", []],
  ]);
}

function updateReferenceSummary(entry, index, title, metadata) {
  title.textContent = (entry.number ? "[" + entry.number + "] " : (index + 1) + ". ") +
    (entry.title || entry.raw || "待填写的文献");
  const authors = (entry.authors || []).join(", ");
  const pages = entry.source_pages?.length ? "原文第 " + entry.source_pages.join("、") + " 页" : "";
  metadata.textContent = [authors, entry.year, entry.venue, pages].filter(Boolean).join(" · ");
}

function renderReferences() {
  referencesCount.textContent = referenceEntries.length + " 条";
  referencesList.replaceChildren();
  const query = referencesSearch.value.trim().toLocaleLowerCase();
  let visible = 0;
  referenceEntries.forEach((entry, index) => {
    if (query && !JSON.stringify(entry).toLocaleLowerCase().includes(query)) return;
    visible++;
    const card = document.createElement("details");
    card.className = "reference-card";
    const summary = document.createElement("summary");
    const title = document.createElement("strong");
    const metadata = document.createElement("small");
    updateReferenceSummary(entry, index, title, metadata);
    summary.append(title, metadata);
    const fields = document.createElement("div");
    fields.className = "reference-entry-fields";
    referenceFields.forEach(([field, labelText]) => {
      const label = document.createElement("label");
      label.textContent = labelText;
      if (["title", "authors", "raw", "url"].includes(field)) label.className = "reference-wide";
      const input = document.createElement(["authors", "raw"].includes(field) ? "textarea" : "input");
      if (input.tagName === "TEXTAREA") input.rows = field === "raw" ? 4 : 2;
      else input.type = "text";
      input.dataset.referenceIndex = String(index);
      input.dataset.referenceField = field;
      input.value = field === "authors" ? (entry.authors || []).join("\n") : entry[field] || "";
      input.addEventListener("input", () => {
        entry[field] = field === "authors" ? input.value.split(/\r?\n/).map(s => s.trim()).filter(Boolean) : input.value;
        referencesRevision++;
        updateReferenceSummary(entry, index, title, metadata);
      });
      label.append(input);
      fields.append(label);
    });
    const remove = document.createElement("button");
    remove.type = "button";
    remove.className = "remove-reference";
    remove.textContent = "删除此条";
    remove.addEventListener("click", () => {
      referenceEntries.splice(index, 1);
      referencesRevision++;
      renderReferences();
    });
    fields.append(remove);
    card.append(summary, fields);
    referencesList.append(card);
  });
  if (!visible) {
    const empty = document.createElement("p");
    empty.className = "references-empty";
    empty.textContent = query ? "没有匹配的参考文献。" : "尚未整理参考文献。点击“提取全部参考文献”开始，也可手动添加。";
    referencesList.append(empty);
  }
}

referencesSearch.addEventListener("input", renderReferences);
addReferenceButton.addEventListener("click", () => {
  referenceEntries.push(newReferenceEntry());
  referencesRevision++;
  referencesSearch.value = "";
  renderReferences();
  const card = referencesList.lastElementChild;
  card.open = true;
  card.querySelector('[data-reference-field="title"]').focus();
});

async function openPaper(filename) {
  const version = ++paperEditorVersion;
  selectedFilename = filename;
  saveButton.disabled = true;
  deleteButton.disabled = true;
  extractReferenceButton.disabled = true;
  extractMetadataButton.disabled = true;
  extractReferenceButton.textContent = "提取全部参考文献";
  addReferenceButton.disabled = true;
  referenceEntries = [];
  referencesRevision++;
  referencesSearch.value = "";
  referencesStatus.textContent = "";
  metadataStatus.textContent = "";
  extractMetadataButton.textContent = "提取标题和摘要";
  referencesList.setAttribute("aria-busy", "false");
  legacyReference.hidden = true;
  legacyReference.open = false;
  renderReferences();
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
    if (version !== paperEditorVersion || !paperDialog.open) return;
    paperTitle.value = metadata.title;
    paperVenue.value = metadata.venue;
    paperAbstract.value = metadata.abstract;
    paperReference.value = metadata.reference || "";
    legacyReference.hidden = !paperReference.value;
    referenceEntries = (Array.isArray(metadata.references) ? metadata.references : []).map(entry => ({
      ...newReferenceEntry(), ...entry,
      authors: Array.isArray(entry.authors) ? entry.authors : [],
      source_pages: Array.isArray(entry.source_pages) ? entry.source_pages : [],
    }));
    renderReferences();
    addReferenceButton.disabled = false;
    saveButton.disabled = false;
    deleteButton.disabled = false;
    extractReferenceButton.disabled = false;
    extractMetadataButton.disabled = false;
    dialogMessage.textContent = "";
  } catch (error) {
    if (version === paperEditorVersion && paperDialog.open) dialogMessage.textContent = error.message;
  }
}


selectPagePapers.addEventListener("change", () => {
  papers.items.forEach(paper => {
    if (selectPagePapers.checked) selectedPapers.add(paper.filename);
    else selectedPapers.delete(paper.filename);
  });
  renderPapers();
});

document.querySelectorAll("[data-bulk-action]").forEach(button => {
  button.addEventListener("click", () => runBulkAction(button.dataset.bulkAction));
});

clearBulkProgress.addEventListener("click", () => {
  bulkProgress.hidden = true;
  bulkResults.replaceChildren();
});

function appendBulkResult(paper, status, detail) {
  const item = document.createElement("li");
  item.dataset.status = status;
  const state = document.createElement("span");
  state.className = "bulk-result-state";
  state.textContent = status === "done" ? "完成" : status === "skipped" ? "跳过" : status === "running" ? "处理中" : "失败";
  const text = document.createElement("span");
  text.textContent = "《" + paper.name + "》" + (detail ? "：" + detail : "");
  item.append(state, text);
  bulkResults.append(item);
  bulkProgress.hidden = false;
  item.scrollIntoView?.({ block: "nearest" });
  return item;
}

async function requestJson(url, options) {
  const response = await fetch(url, options);
  const result = await response.json();
  if (!response.ok) throw new Error(result.detail || result.ocr_error || "请求失败");
  return result;
}

const taskLabels = { ocr: "OCR", references: "提取参考文献", metadata: "提取标题和摘要", markdown: "转 Markdown", translation: "翻译 Markdown" };
const taskStates = { queued: "排队中", running: "运行中", completed: "完成", skipped: "跳过", failed: "失败", cancelled: "已取消", interrupted: "已中断" };

async function submitTask(filename, action, options = {}) {
  return requestJson("/api/tasks", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ filename, action, ...options }),
  });
}

const taskWaiters = new Map();

function checkAwaitedTask(taskId, waiter) {
  if (waiter.reading) { waiter.changed = true; return; }
  waiter.reading = true;
  waiter.changed = false;
  requestJson("/api/tasks/" + encodeURIComponent(taskId)).then(task => {
    if (!["queued", "running"].includes(task.status)) {
      taskWaiters.delete(taskId);
      waiter.resolve(task);
    }
  }).catch(error => {
    taskWaiters.delete(taskId);
    waiter.reject(error);
  }).finally(() => {
    waiter.reading = false;
    // A completion event can arrive while the initial GET is still in flight.
    if (waiter.changed && taskWaiters.get(taskId) === waiter) checkAwaitedTask(taskId, waiter);
  });
}

function awaitTask(taskId) {
  const existing = taskWaiters.get(taskId);
  if (existing) return existing.promise;
  let waiter;
  const promise = new Promise((resolve, reject) => {
    waiter = { resolve, reject, reading: false, changed: false };
    taskWaiters.set(taskId, waiter);
    checkAwaitedTask(taskId, waiter);
  });
  waiter.promise = promise;
  return promise;
}

function taskDuration(task) {
  const from = Date.parse(task.started_at || task.created_at);
  const to = task.finished_at ? Date.parse(task.finished_at) : Date.now();
  const seconds = Math.max(0, Math.floor((to - from) / 1000));
  return seconds < 60 ? seconds + " 秒" : Math.floor(seconds / 60) + " 分钟";
}

async function showTaskResult(id) {
  const task = await requestJson("/api/tasks/" + encodeURIComponent(id));
  viewedTask = task;
  taskResultStatus.textContent = (taskLabels[task.action] || task.action) + " · " + (taskStates[task.status] || task.status) + " · " + task.title;
  taskResultContent.textContent = task.error || JSON.stringify(task.result, null, 2) || "暂无结果";
  applyTaskResult.hidden = task.status !== "completed" || !["references", "metadata"].includes(task.action);
  taskResultDialog.showModal();
}

document.querySelector("#close-task-result").addEventListener("click", () => taskResultDialog.close());
applyTaskResult.addEventListener("click", async () => {
  const task = viewedTask;
  if (!task || task.status !== "completed") return;
  if (paperDialog.open && !window.confirm("填入任务结果会替换对应的编辑内容，继续吗？")) return;
  taskResultDialog.close();
  if (!paperDialog.open || selectedFilename !== task.filename) {
    if (paperDialog.open) paperDialog.close();
    await openPaper(task.filename);
  }
  if (saveButton.disabled || selectedFilename !== task.filename) return;
  if (task.action === "references" && Array.isArray(task.result?.references)) {
    referenceEntries = task.result.references; referencesRevision++; referencesSearch.value = ""; renderReferences();
    referencesStatus.textContent = "已填入后台任务结果，请检查后保存。";
  } else if (task.action === "metadata") {
    if (task.result?.title?.trim()) { paperTitle.value = task.result.title; metadataRevisions.title++; }
    if (task.result?.abstract?.trim()) { paperAbstract.value = task.result.abstract; metadataRevisions.abstract++; }
    metadataStatus.textContent = "已填入后台任务结果，请检查后保存。";
  }
});

let taskSignature = null;
let taskAges = [];
function renderTaskSnapshot(data) {
  for (const [id, waiter] of taskWaiters) {
    const task = data.changed?.id === id ? data.changed : (data.tasks || []).find(item => item.id === id);
    if (!Object.hasOwn(data, "changed") || (task && !["queued", "running"].includes(task.status))) {
      checkAwaitedTask(id, waiter);
    }
  }
  taskAges = [];
  const counts = data.counts || {};
  taskCounts.textContent = data.workers + " 个 worker · 排队 " + (counts.queued || 0) + " · 运行 " + (counts.running || 0) + " · 完成 " + (counts.completed || 0) + " · 跳过 " + (counts.skipped || 0) + " · 失败/中断 " + ((counts.failed || 0) + (counts.interrupted || 0));
  const signature = JSON.stringify((data.tasks || []).map(task => [task.id, task.status]));
  if (taskSignature === null || taskSignature !== signature || data.changed) loadPapers(currentPage).catch(error => { message.textContent = error.message; });
  taskSignature = signature;
  taskList.replaceChildren();
  for (const task of data.tasks || []) {
    const item = document.createElement("li");
    item.dataset.status = task.status;
    const heading = document.createElement("strong");
    heading.textContent = (taskStates[task.status] || task.status) + " · " + (taskLabels[task.action] || task.action);
    const name = document.createElement("span"); name.className = "task-paper-name"; name.textContent = task.title || task.filename;
    const age = document.createElement("span"); age.className = "task-age"; age.textContent = taskDuration(task);
    item.append(heading, name, age);
    if (["queued", "running"].includes(task.status)) taskAges.push({task, age});
    if (task.worker && task.status === "running") { const worker = document.createElement("small"); worker.textContent = task.worker; item.append(worker); }
    if (task.error) { const error = document.createElement("p"); error.className = "task-error"; error.textContent = task.error; item.append(error); }
    if (task.status === "queued" || ["failed", "interrupted", "cancelled"].includes(task.status)) {
      const control = document.createElement("button"); control.type = "button"; control.textContent = task.status === "queued" ? "取消" : "重试";
      control.addEventListener("click", async () => {
        control.disabled = true;
        try { await requestJson("/api/tasks/" + encodeURIComponent(task.id) + "/" + (task.status === "queued" ? "cancel" : "retry"), { method: "POST" }); }
        catch (error) { message.textContent = error.message; control.disabled = false; }
      });
      item.append(control);
    }
    if (["completed", "failed", "interrupted"].includes(task.status)) {
      const detail = document.createElement("button"); detail.type = "button"; detail.textContent = "查看结果";
      detail.addEventListener("click", () => showTaskResult(task.id).catch(error => { message.textContent = error.message; }));
      item.append(detail);
    }
    taskList.append(item);
  }
  if (!(data.tasks || []).length) {
    const empty = document.createElement("li"); empty.textContent = "暂无后台任务"; taskList.append(empty);
  }
}

let taskEventSource;
function connectTaskEvents() {
  if (taskEventSource) return;
  taskEventSource = new EventSource("/api/tasks/events?limit=100");
  taskEventSource.addEventListener("tasks", event => {
    try { renderTaskSnapshot(JSON.parse(event.data)); }
    catch (error) { taskCounts.textContent = "任务状态更新失败：" + error.message; }
  });
  taskEventSource.onerror = () => { taskCounts.textContent = "任务状态连接中断，正在重新连接…"; };
}
window.addEventListener("pagehide", () => { taskEventSource?.close(); taskEventSource = null; });
window.addEventListener("pageshow", () => connectTaskEvents());
// Refresh elapsed labels locally, without making any status requests.
const taskAgeTimer = setInterval(() => {
  for (const {task, age} of taskAges) age.textContent = taskDuration(task);
}, 1000);
taskAgeTimer?.unref?.();

async function runBulkAction(action) {
  if (bulkInProgress || !selectedPapers.size) return;
  const queue = [...selectedPapers].map(filename => paperCache.get(filename)).filter(Boolean);
  if (!queue.length) return;
  bulkInProgress = true;
  bulkResults.replaceChildren();
  bulkProgress.hidden = false;
  const labels = { ocr: "批量 OCR", references: "批量提取参考文献", markdown: "批量转 Markdown" };
  bulkProgressTitle.textContent = labels[action] + "：提交中，共 " + queue.length + " 篇";
  renderPapers();
  let submitted = 0;
  for (const paper of queue) {
    const row = appendBulkResult(paper, "running", (submitted + 1) + "/" + queue.length);
    const state = row.querySelector(".bulk-result-state");
    const text = row.children[1];
    try {
      if (action === "ocr" && ["completed", "partial"].includes(paper.ocr_status)) {
        row.dataset.status = "skipped"; state.textContent = "跳过"; text.textContent = "《" + paper.name + "》：已有 OCR";
        continue;
      }
      if (action === "references" && paper.references_count > 0) {
        row.dataset.status = "skipped"; state.textContent = "跳过"; text.textContent = "《" + paper.name + "》：已有 " + paper.references_count + " 条参考文献";
        continue;
      }
      if (action === "references" && paper.references_processing) {
        row.dataset.status = "skipped"; state.textContent = "跳过"; text.textContent = "《" + paper.name + "》：参考文献任务正在进行";
        continue;
      }
      if (action === "markdown" && (paper.markdown_available || !["completed", "partial"].includes(paper.ocr_status))) {
        row.dataset.status = "skipped"; state.textContent = "跳过";
        text.textContent = "《" + paper.name + "》：" + (paper.markdown_available ? "已有 Markdown" : "尚无可用 OCR");
        continue;
      }
      const options = action === "references" ? { save_result: true, skip_existing: true } : { skip_existing: true };
      const task = await submitTask(paper.filename, action, options);
      row.dataset.status = "queued"; state.textContent = "已排队"; text.textContent = "《" + paper.name + "》：任务已排队";
      submitted++;
    } catch (error) {
      row.dataset.status = "failed"; state.textContent = "失败"; text.textContent = "《" + paper.name + "》：" + error.message;
    }
  }
  selectedPapers.clear();
  bulkInProgress = false;
  updateBulkControls();
  await loadPapers(currentPage).catch(error => { message.textContent = error.message; });
  bulkProgressTitle.textContent = labels[action] + "：已提交 " + submitted + " 篇，详细状态见后台任务";
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
  papers.items.forEach(paper => paperCache.set(paper.filename, paper));
  renderPapers();
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
  message.textContent = "正在上传文件；OCR 会自动加入后台队列。";
  try {
    const response = await fetch("/upload", { method: "POST", body: uploadBody });
    if (!response.ok) {
      const detail = await response.json();
      throw new Error(detail.detail || "上传失败");
    }
    const result = await response.json();
    form.reset();
    renderPendingFiles([]);
    message.textContent = `已上传 ${result.filenames.length} 个文件，OCR 已加入后台队列` +
      (result.duplicates.length ? `，跳过 ${result.duplicates.length} 个重复文件：${result.duplicates.join("、")}` : "");
    await loadPapers();
  } catch (error) {
    message.textContent = error.message;
  } finally {
    delete form.dataset.busy;
  }
});

extractMetadataButton.addEventListener("click", async () => {
  if (extractMetadataButton.disabled) return;
  const filename = selectedFilename;
  const version = paperEditorVersion;
  const revisions = { ...metadataRevisions };
  const previous = { title: paperTitle.value, abstract: paperAbstract.value };
  extractMetadataButton.disabled = true; extractReferenceButton.disabled = true; saveButton.disabled = true; deleteButton.disabled = true;
  metadataStatus.textContent = "标题和摘要提取任务已提交后台；可关闭窗口，任务面板会显示状态。";
  try {
    const queued = await submitTask(filename, "metadata");
    const task = await awaitTask(queued.id);
    if (version !== paperEditorVersion || !paperDialog.open) return;
    if (task.status !== "completed") throw new Error(task.error || "标题和摘要提取未完成");
    const result = task.result || {};
    const messages = [];
    for (const [field, label, input] of [["title", "标题", paperTitle], ["abstract", "摘要", paperAbstract]]) {
      if (metadataRevisions[field] !== revisions[field] || input.value !== previous[field]) messages.push(label + "在提取期间已编辑，保留你的输入");
      else if (!result[field]?.trim()) messages.push("未找到可确认的完整" + label + "，保留原内容");
      else { input.value = result[field]; messages.push(label + "已填入"); }
    }
    metadataStatus.textContent = messages.join("；") + "。请检查后点击保存。" + (result.warnings?.length ? " 提醒：" + result.warnings.join("；") : "");
  } catch (error) { if (version === paperEditorVersion && paperDialog.open) metadataStatus.textContent = error.message; }
  finally {
    if (version === paperEditorVersion && paperDialog.open) {
      extractMetadataButton.disabled = false; extractReferenceButton.disabled = false; saveButton.disabled = false; deleteButton.disabled = false;
    }
  }
});

extractReferenceButton.addEventListener("click", async () => {
  if (extractReferenceButton.disabled) return;
  if (referenceEntries.length && !window.confirm("重新提取会替换当前文献列表（保存后生效），继续吗？")) return;
  const filename = selectedFilename, version = paperEditorVersion, revision = referencesRevision;
  extractReferenceButton.disabled = true; extractMetadataButton.disabled = true; saveButton.disabled = true; deleteButton.disabled = true;
  referencesStatus.textContent = "参考文献提取任务已提交后台；可关闭窗口，任务面板会显示状态。";
  referencesList.setAttribute("aria-busy", "true");
  try {
    const queued = await submitTask(filename, "references", { skip_existing: false });
    const task = await awaitTask(queued.id);
    if (version !== paperEditorVersion || !paperDialog.open) return;
    if (task.status !== "completed") throw new Error(task.error || "参考文献提取未完成");
    if (referencesRevision !== revision) { referencesStatus.textContent = "提取期间文献列表已被编辑，保留了你的修改；可再次提取。"; return; }
    const result = task.result || {};
    if (!Array.isArray(result.references)) throw new Error("参考文献返回格式无效，原列表已保留。");
    if (!result.references.length) { referencesStatus.textContent = "未识别到参考文献，原列表已保留，请核对原文和 OCR。"; return; }
    referenceEntries = result.references; referencesRevision++; referencesSearch.value = ""; renderReferences();
    referencesStatus.textContent = "后台提取完成：扫描 " + result.total_pages + " 页，提取 " + referenceEntries.length + " 条。请核对后点击保存。" + (result.warnings?.length ? " 提醒：" + result.warnings.join("；") : "");
  } catch (error) { if (version === paperEditorVersion && paperDialog.open) referencesStatus.textContent = error.message; }
  finally {
    if (version === paperEditorVersion && paperDialog.open) {
      extractReferenceButton.disabled = false; extractMetadataButton.disabled = false; saveButton.disabled = false; deleteButton.disabled = false;
      referencesList.setAttribute("aria-busy", "false");
    }
  }
});

document.querySelector("#close-dialog").addEventListener("click", () => paperDialog.close());

deleteButton.addEventListener("click", async () => {
  if (!window.confirm(`确定删除《${paperTitle.value}》吗？此操作无法撤销。`)) return;
  deleteButton.disabled = true;
  saveButton.disabled = true;
  extractReferenceButton.disabled = true;
  extractMetadataButton.disabled = true;
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
    extractReferenceButton.disabled = false;
    extractMetadataButton.disabled = false;
  }
});

paperForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  if (saveButton.disabled) return;
  if (referenceEntries.some(entry => !entry.raw.trim())) {
    dialogMessage.textContent = "有文献缺少原始引用文本，请展开补充或删除空条目后再保存。";
    referencesSearch.value = "";
    renderReferences();
    referencesList.querySelectorAll(".reference-card").forEach((card, index) => {
      if (!referenceEntries[index].raw.trim()) card.open = true;
    });
    return;
  }
  saveButton.disabled = true;
  extractReferenceButton.disabled = true;
  extractMetadataButton.disabled = true;
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
        references: referenceEntries,
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
    extractReferenceButton.disabled = false;
    extractMetadataButton.disabled = false;
  }
});

loadPapers().catch((error) => { message.textContent = error.message; });
connectTaskEvents();
