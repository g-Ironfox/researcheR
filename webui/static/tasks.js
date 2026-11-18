const taskList = document.querySelector("#task-list");
const taskCounts = document.querySelector("#task-counts");
const connectionStatus = document.querySelector("#task-connection");
const message = document.querySelector("#task-message");
const taskResultDialog = document.querySelector("#task-result-dialog");
const taskResultStatus = document.querySelector("#task-result-status");
const taskResultContent = document.querySelector("#task-result-content");
const applyTaskResult = document.querySelector("#apply-task-result");
let taskAges = [];

async function requestJson(url, options) {
  const response = await fetch(url, options);
  const result = await response.json();
  if (!response.ok) throw new Error(result.detail || result.ocr_error || "请求失败");
  return result;
}

const taskLabels = { ocr: "OCR", references: "提取参考文献", metadata: "提取标题和摘要", markdown: "转 Markdown", translation: "翻译 Markdown" };
const taskStates = { queued: "排队中", running: "运行中", completed: "完成", skipped: "跳过", failed: "失败", cancelled: "已取消", interrupted: "已中断" };


function taskDuration(task) {
  const from = Date.parse(task.started_at || task.created_at);
  const to = task.finished_at ? Date.parse(task.finished_at) : Date.now();
  const seconds = Math.max(0, Math.floor((to - from) / 1000));
  if (!Number.isFinite(seconds)) return "—";
  return seconds < 60 ? seconds + " 秒" : Math.floor(seconds / 60) + " 分钟";
}

async function showTaskResult(id) {
  const task = await requestJson("/api/tasks/" + encodeURIComponent(id));
  taskResultStatus.textContent = (taskLabels[task.action] || task.action) + " · " + (taskStates[task.status] || task.status) + " · " + (task.title || task.filename);
  taskResultContent.textContent = task.error || (task.result == null ? "暂无结果" : JSON.stringify(task.result, null, 2));
  applyTaskResult.hidden = task.status !== "completed" || !["references", "metadata"].includes(task.action);
  applyTaskResult.href = "/?task=" + encodeURIComponent(task.id);
  taskResultDialog.showModal();
}
document.querySelector("#close-task-result").addEventListener("click", () => taskResultDialog.close());

function renderTaskSnapshot(data) {
  connectionStatus.textContent = "实时更新已连接";
  connectionStatus.dataset.state = "connected";
  taskAges = [];
  const counts = data.counts || {};
  taskCounts.textContent = data.workers + " 个 worker · 排队 " + (counts.queued || 0) + " · 运行 " + (counts.running || 0) + " · 完成 " + (counts.completed || 0) + " · 跳过 " + (counts.skipped || 0) + " · 失败/中断 " + ((counts.failed || 0) + (counts.interrupted || 0));
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
        try {
          await requestJson("/api/tasks/" + encodeURIComponent(task.id) + "/" + (task.status === "queued" ? "cancel" : "retry"), { method: "POST" });
          message.textContent = task.status === "queued" ? "已取消排队任务。" : "已提交重试。";
        }
        catch (error) { message.textContent = error.message; control.disabled = false; }
      });
      item.append(control);
    }
    if (["completed", "failed", "interrupted", "skipped", "cancelled"].includes(task.status)) {
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
  connectionStatus.textContent = "正在连接实时更新…";
  connectionStatus.dataset.state = "connecting";
  taskEventSource = new EventSource("/api/tasks/events?limit=100");
  taskEventSource.addEventListener("tasks", event => {
    try { renderTaskSnapshot(JSON.parse(event.data)); }
    catch (error) {
      message.textContent = "任务状态更新失败：" + error.message;
    }
  });
  taskEventSource.onerror = () => {
    connectionStatus.textContent = "连接中断，正在重新连接…";
    connectionStatus.dataset.state = "reconnecting";
  };
}
window.addEventListener("pagehide", () => { taskEventSource?.close(); taskEventSource = null; });
window.addEventListener("pageshow", () => connectTaskEvents());
// Only elapsed labels use a local timer; task status arrives through SSE.
const taskAgeTimer = setInterval(() => {
  for (const {task, age} of taskAges) age.textContent = taskDuration(task);
}, 1000);
taskAgeTimer?.unref?.();
connectTaskEvents();
