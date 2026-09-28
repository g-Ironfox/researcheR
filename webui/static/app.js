const form = document.querySelector("#upload-form");
const fileInput = document.querySelector("#file-input");
const message = document.querySelector("#message");
const paperCount = document.querySelector("#paper-count");
const paperList = document.querySelector("#paper-list");
const pendingList = document.querySelector("#pending-list");
const fileName = document.querySelector(".file-copy strong");

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
    const link = document.createElement("a");
    link.href = `/papers/${encodeURIComponent(paper.filename)}`;
    link.textContent = paper.name;
    const size = document.createElement("span");
    size.textContent = `${paper.size_kb} KB`;
    item.append(link, size);
    paperList.append(item);
  });
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

loadPapers().catch((error) => { message.textContent = error.message; });