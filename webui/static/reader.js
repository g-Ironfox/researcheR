import * as pdfjsLib from "/static/pdf.mjs";

pdfjsLib.GlobalWorkerOptions.workerSrc = "/static/pdf.worker.mjs";

const container = document.querySelector("#pdf-pages");
const viewer = document.querySelector(".reader-main");
const message = document.querySelector("#reader-message");
const zoomInput = document.querySelector("#zoom-level");
const pageInput = document.querySelector("#page-input");
const pageTotal = document.querySelector("#page-total");
const zoomOutButton = document.querySelector("#zoom-out");
const zoomInButton = document.querySelector("#zoom-in");
const selectionOverlay = document.createElement("div");
selectionOverlay.className = "selection-overlay";
document.body.append(selectionOverlay);
let documentPdf;
let zoom = 1;
let baseWidth = 0;
const entries = [];
const visible = new Set();

function updateSelectionOverlay() {
  selectionOverlay.replaceChildren();
  const selection = window.getSelection();
  if (!selection || selection.isCollapsed || !selection.anchorNode?.parentElement?.closest(".textLayer")) return;
  for (let index = 0; index < selection.rangeCount; index += 1) {
    const range = selection.getRangeAt(index);
    const walker = document.createTreeWalker(range.commonAncestorContainer, NodeFilter.SHOW_TEXT);
    let node = range.commonAncestorContainer.nodeType === Node.TEXT_NODE
      ? range.commonAncestorContainer : walker.nextNode();
    while (node) {
      if (!node.parentElement?.closest(".textLayer") || !range.intersectsNode(node)) {
        node = walker.nextNode();
        continue;
      }
      const textRange = document.createRange();
      textRange.selectNodeContents(node);
      if (range.compareBoundaryPoints(Range.START_TO_START, textRange) > 0) {
        textRange.setStart(range.startContainer, range.startOffset);
      }
      if (range.compareBoundaryPoints(Range.END_TO_END, textRange) < 0) {
        textRange.setEnd(range.endContainer, range.endOffset);
      }
      for (const rect of textRange.getClientRects()) {
        if (!rect.width || !rect.height || rect.bottom < 0 || rect.top > innerHeight) continue;
        const highlight = document.createElement("span");
        highlight.style.left = `${rect.left}px`;
        highlight.style.top = `${rect.top + rect.height * 0.3}px`;
        highlight.style.width = `${rect.width}px`;
        highlight.style.height = `${rect.height * 0.7}px`;
        selectionOverlay.append(highlight);
      }
      node = walker.nextNode();
    }
  }
}

document.addEventListener("selectionchange", updateSelectionOverlay);
window.addEventListener("scroll", updateSelectionOverlay, { passive: true });
window.addEventListener("resize", updateSelectionOverlay);

const observer = new IntersectionObserver((observations) => {
  for (const observation of observations) {
    const entry = observation.target.entry;
    if (observation.isIntersecting) {
      visible.add(entry);
      renderEntry(entry);
    } else {
      visible.delete(entry);
    }
  }
}, { rootMargin: "600px 0px" });

async function renderEntry(entry) {
  if (entry.rendering || entry.renderedZoom === zoom) return;
  entry.rendering = true;
  const renderZoom = zoom;
  try {
    const viewport = entry.page.getViewport({ scale: baseWidth * renderZoom / entry.naturalWidth });
    const pixelRatio = Math.min(window.devicePixelRatio || 1, 2);
    entry.canvas.width = Math.ceil(viewport.width * pixelRatio);
    entry.canvas.height = Math.ceil(viewport.height * pixelRatio);
    await entry.page.render({ canvasContext: entry.canvas.getContext("2d"), viewport, transform: [pixelRatio, 0, 0, pixelRatio, 0, 0] }).promise;
    entry.renderedZoom = renderZoom;
    renderText(entry, viewport);
    message.textContent = "";
  } catch (error) {
    message.textContent = "无法渲染 PDF 页面";
  } finally {
    entry.rendering = false;
    if (entry.renderedZoom === renderZoom && renderZoom !== zoom && visible.has(entry)) renderEntry(entry);
  }
}

async function renderText(entry, viewport) {
  entry.textLayerTask?.cancel();
  entry.textLayer?.remove();
  const textLayer = document.createElement("div");
  textLayer.className = "textLayer";
  entry.wrapper.append(textLayer);
  entry.textLayer = textLayer;
  entry.wrapper.style.setProperty("--scale-factor", viewport.scale);
  const task = new pdfjsLib.TextLayer({
    textContentSource: entry.page.streamTextContent(),
    container: textLayer,
    viewport,
  });
  entry.textLayerTask = task;
  try {
    await task.render();
  } catch (error) {
    // 文本层被取消或构建失败时，页面图片仍可正常阅读
  }
}

function updateSizes() {
  const width = baseWidth * zoom;
  for (const entry of entries) {
    entry.wrapper.style.width = `${width}px`;
    entry.wrapper.style.height = `${width * entry.naturalHeight / entry.naturalWidth}px`;
  }
}

function updateZoomButtons() {
  zoomOutButton.disabled = zoom <= 0.5;
  zoomInButton.disabled = zoom >= 2;
}

function applyZoom() {
  zoomInput.value = `${Math.round(zoom * 100)}%`;
  updateZoomButtons();
  updateSizes();
  for (const entry of visible) renderEntry(entry);
  updateCurrentPage();
}

zoomOutButton.addEventListener("click", () => {
  if (zoom <= 0.5) return;
  zoom = Math.round((zoom - 0.25) * 100) / 100;
  applyZoom();
});
zoomInButton.addEventListener("click", () => {
  if (zoom >= 2) return;
  zoom = Math.round((zoom + 0.25) * 100) / 100;
  applyZoom();
});
zoomInput.addEventListener("change", () => {
  const value = Number.parseFloat(zoomInput.value);
  if (!Number.isFinite(value)) {
    applyZoom();
    return;
  }
  zoom = Math.min(2, Math.max(0.5, Math.round(value) / 100));
  applyZoom();
});

function updateCurrentPage(force = false) {
  if (!entries.length || (!force && document.activeElement === pageInput)) return;
  const middle = window.innerHeight / 2;
  let current = entries[0].number;
  for (const entry of entries) {
    if (entry.wrapper.getBoundingClientRect().top <= middle) current = entry.number;
  }
  const text = String(current);
  if (pageInput.value !== text) pageInput.value = text;
}

let pageUpdateScheduled = false;
window.addEventListener("scroll", () => {
  if (pageUpdateScheduled) return;
  pageUpdateScheduled = true;
  requestAnimationFrame(() => {
    pageUpdateScheduled = false;
    updateCurrentPage();
  });
}, { passive: true });

pageInput.addEventListener("change", () => {
  const value = Number.parseInt(pageInput.value, 10);
  if (entries.length && Number.isFinite(value)) {
    entries[Math.min(entries.length, Math.max(1, value)) - 1].wrapper.scrollIntoView({ block: "start" });
  }
  updateCurrentPage(true);
});

async function setupPages() {
  baseWidth = Math.min(900, viewer.clientWidth - 40);
  for (let number = 1; number <= documentPdf.numPages; number += 1) {
    const page = await documentPdf.getPage(number);
    const natural = page.getViewport({ scale: 1 });
    const wrapper = document.createElement("div");
    wrapper.className = "pdf-page";
    const canvas = document.createElement("canvas");
    canvas.setAttribute("aria-label", `第 ${number} 页`);
    wrapper.append(canvas);
    const entry = {
      number,
      page,
      wrapper,
      canvas,
      textLayer: null,
      textLayerTask: null,
      naturalWidth: natural.width,
      naturalHeight: natural.height,
      renderedZoom: null,
      rendering: false,
    };
    wrapper.entry = entry;
    container.append(wrapper);
    entries.push(entry);
    observer.observe(wrapper);
  }
  updateSizes();
  pageTotal.textContent = `/ ${documentPdf.numPages}`;
  updateCurrentPage(true);
}

async function loadDocument() {
  try {
    const filename = decodeURIComponent(location.pathname.split("/").pop());
    documentPdf = await pdfjsLib.getDocument(`/papers/${encodeURIComponent(filename)}`).promise;
    await setupPages();
    message.textContent = "";
    updateZoomButtons();
  } catch (error) {
    message.textContent = "无法加载 PDF，请检查网络连接后重试";
  }
}

loadDocument();