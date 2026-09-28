const form = document.querySelector("#upload-form");
const fileInput = document.querySelector("#file-input");
const message = document.querySelector("#message");
const paperCount = document.querySelector("#paper-count");
const paperList = document.querySelector("#paper-list");
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
let selectedFilename = "";

function renderPapers(papers) {
  paperCount.textContent = papers.length;
  paperList.replaceChildren();

  if (papers.length === 0) {
    const empty = document.createElement("li");
    empty.className = "empty";
    empty.textContent = "还没有论文，上传一篇 PDF 开始吧。";
    paperList.append(empty);
    return;
  }

  papers.forEach((paper) => {
    const item = document.createElement("li");
    const openButton = document.createElement("button");
    openButton.type = "button";
    openButton.className = "paper-button";
    openButton.textContent = paper.name;
    openButton.addEventListener("click", () => openPaper(paper.filename));
    const readLink = document.createElement("a");
    readLink.className = "read-link";
    readLink.href = `/read/${encodeURIComponent(paper.filename)}`;
    readLink.textContent = "阅读";
    const size = document.createElement("span");
    size.textContent = `${paper.size_kb} KB`;
    const actions = document.createElement("div");
    actions.className = "paper-actions";
    actions.append(readLink, size);
    item.append(openButton, actions);
    paperList.append(item);
  });
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

async function loadPapers() {
  const response = await fetch("/api/papers");
  if (!response.ok) throw new Error("无法加载论文列表");
  renderPapers(await response.json());
}

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
  if (!fileInput.files.length) return;

  const button = form.querySelector("button");
  const fileCount = fileInput.files.length;
  button.disabled = true;
  message.textContent = "正在上传...";
  try {
    const response = await fetch("/upload", { method: "POST", body: new FormData(form) });
    if (!response.ok) {
      const detail = await response.json();
      throw new Error(detail.detail || "上传失败");
    }
    form.reset();
    renderPendingFiles([]);
    message.textContent = `已上传 ${fileCount} 个文件`;
    await loadPapers();
  } catch (error) {
    message.textContent = error.message;
  } finally {
    button.disabled = false;
  }
});

document.querySelector("#close-dialog").addEventListener("click", () => paperDialog.close());

paperForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const saveButton = document.querySelector("#save-paper");
  saveButton.disabled = true;
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
  }
});

loadPapers().catch((error) => { message.textContent = error.message; });