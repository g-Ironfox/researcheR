import * as pdfjsLib from "/static/pdf.mjs";

pdfjsLib.GlobalWorkerOptions.workerSrc = "/static/pdf.worker.mjs";

const canvas = document.querySelector("#pdf-page");
const message = document.querySelector("#reader-message");
const pageCount = document.querySelector("#page-count");
const zoomLevel = document.querySelector("#zoom-level");
const previousButton = document.querySelector("#previous-page");
const nextButton = document.querySelector("#next-page");
const zoomOutButton = document.querySelector("#zoom-out");
const zoomInButton = document.querySelector("#zoom-in");
let documentPdf;
let currentPage = 1;
let zoom = 1;
let rendering = false;

async function renderPage() {
  rendering = true;
  previousButton.disabled = true;
  nextButton.disabled = true;
  zoomOutButton.disabled = true;
  zoomInButton.disabled = true;
  message.textContent = "正在渲染...";
  try {
    const page = await documentPdf.getPage(currentPage);
    const naturalViewport = page.getViewport({ scale: 1 });
    const availableWidth = Math.min(900, document.querySelector(".reader-main").clientWidth - 40);
    const viewport = page.getViewport({ scale: availableWidth / naturalViewport.width * zoom });
    const pixelRatio = Math.min(window.devicePixelRatio || 1, 2);
    canvas.width = Math.ceil(viewport.width * pixelRatio);
    canvas.height = Math.ceil(viewport.height * pixelRatio);
    canvas.style.width = `${viewport.width}px`;
    canvas.style.height = `${viewport.height}px`;
    await page.render({ canvasContext: canvas.getContext("2d"), viewport, transform: [pixelRatio, 0, 0, pixelRatio, 0, 0] }).promise;
    pageCount.textContent = `${currentPage} / ${documentPdf.numPages}`;
    zoomLevel.textContent = `${Math.round(zoom * 100)}%`;
    message.textContent = "";
  } catch (error) {
    message.textContent = "无法渲染 PDF 页面";
  } finally {
    rendering = false;
    previousButton.disabled = currentPage <= 1;
    nextButton.disabled = currentPage >= documentPdf.numPages;
    zoomOutButton.disabled = zoom <= 0.5;
    zoomInButton.disabled = zoom >= 2;
  }
}

previousButton.addEventListener("click", () => {
  if (rendering || currentPage <= 1) return;
  currentPage -= 1;
  renderPage();
});
nextButton.addEventListener("click", () => {
  if (rendering || currentPage >= documentPdf.numPages) return;
  currentPage += 1;
  renderPage();
});
zoomOutButton.addEventListener("click", () => {
  if (rendering || zoom <= 0.5) return;
  zoom = Math.round((zoom - 0.25) * 100) / 100;
  renderPage();
});
zoomInButton.addEventListener("click", () => {
  if (rendering || zoom >= 2) return;
  zoom = Math.round((zoom + 0.25) * 100) / 100;
  renderPage();
});

async function loadDocument() {
  try {
    const filename = decodeURIComponent(location.pathname.split("/").pop());
    documentPdf = await pdfjsLib.getDocument(`/papers/${encodeURIComponent(filename)}`).promise;
    await renderPage();
  } catch (error) {
    message.textContent = "无法加载 PDF，请检查网络连接或返回论文库重试";
  }
}

loadDocument();