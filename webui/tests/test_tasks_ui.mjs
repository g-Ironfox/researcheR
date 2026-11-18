import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import vm from 'node:vm';

const html = await readFile(new URL('../static/tasks.html', import.meta.url), 'utf8');
const home = await readFile(new URL('../static/index.html', import.meta.url), 'utf8');
const source = await readFile(new URL('../static/tasks.js', import.meta.url), 'utf8');
assert.ok(home.includes('href="/tasks"'));
assert.doesNotMatch(home, /id="task-list"/);
assert.ok(html.includes('href="/"'));
assert.ok(html.includes("/static/tasks.js"));
assert.ok(!html.includes("/static/app.js"));

const elements = new Map();
function element(tag = 'div') {
  return {
    tagName: tag.toUpperCase(), textContent: '', hidden: false, disabled: false,
    children: [], dataset: {}, listeners: {}, href: '', open: false,
    addEventListener(name, callback) { this.listeners[name] = callback; },
    append(...nodes) { this.children.push(...nodes); },
    replaceChildren() { this.children = []; },
    showModal() { this.open = true; },
    close() { this.open = false; },
  };
}
for (const [, id] of html.matchAll(/id="([^"]+)"/g)) elements.set('#' + id, element());
const query = selector => elements.get(selector) || null;
const calls = [], streams = [], timers = [], windowEvents = {};
const results = new Map();
let failAction = false;
class MockEventSource {
  constructor(url) { this.url = url; this.listeners = {}; streams.push(this); }
  addEventListener(name, callback) { this.listeners[name] = callback; }
  emit(data) { this.listeners.tasks({data: JSON.stringify(data)}); }
  close() { this.closed = true; }
}
const context = vm.createContext({
  document: {querySelector: query, createElement: element},
  window: {addEventListener(name, callback) { windowEvents[name] = callback; }},
  EventSource: MockEventSource,
  setInterval(callback) { timers.push(callback); return {unref() {}}; },
  fetch: async (url, options) => {
    calls.push([url, options]);
    if (options?.method === 'POST') return {
      ok: !failAction, json: async () => failAction ? {detail: 'Cannot retry'} : {status: 'queued'},
    };
    const id = decodeURIComponent(url.slice('/api/tasks/'.length));
    assert.ok(results.has(id), 'Unexpected request: ' + url);
    return {ok: true, json: async () => results.get(id)};
  },
});
vm.runInContext(source, context);
assert.equal(streams.length, 1);
assert.equal(streams[0].url, '/api/tasks/events?limit=100');
assert.equal(calls.length, 0, 'Task history must arrive through SSE');
const base = {filename: 'paper.pdf', title: '<script>paper</script>', action: 'metadata', created_at: new Date(Date.now() - 10000).toISOString()};
const tasks = [
  {...base, id: 'running', status: 'running', worker: 'worker-2'},
  {...base, id: 'queued /#', status: 'queued'},
  {...base, id: 'failed', status: 'failed', error: 'Provider unavailable'},
  {...base, id: 'completed', status: 'completed', result: {title: 'Recovered title'}},
];
const snapshot = {workers: 3, counts: {running: 1, queued: 1, failed: 1, completed: 1}, tasks};
streams[0].emit(snapshot);
let rows = query('#task-list').children;
assert.equal(rows.length, 4);
assert.equal(query('#task-connection').dataset.state, 'connected');
assert.ok(query('#task-counts').textContent.includes('3 个 worker'));
assert.ok(rows[0].children.some(child => child.textContent === 'worker-2'));
assert.ok(rows[0].children.some(child => child.textContent === base.title));
assert.ok(!rows[0].children.some(child => child.tagName === 'BUTTON'));
assert.ok(rows[2].children.some(child => child.textContent === 'Provider unavailable'));
const cancel = rows[1].children.find(child => child.textContent === '取消');
await cancel.listeners.click();
assert.equal(calls.at(-1)[0], '/api/tasks/queued%20%2F%23/cancel');
assert.equal(calls.at(-1)[1].method, 'POST');
assert.equal(cancel.disabled, true);
const retry = rows[2].children.find(child => child.textContent === '重试');
failAction = true;
await retry.listeners.click();
assert.equal(query('#task-message').textContent, 'Cannot retry');
assert.equal(retry.disabled, false, 'A failed action must allow retrying');
failAction = false;
await retry.listeners.click();
assert.equal(calls.at(-1)[0], '/api/tasks/failed/retry');
assert.equal(query('#task-message').textContent, '已提交重试。');

results.set('completed', tasks[3]);
await rows[3].children.find(child => child.textContent === '查看结果').listeners.click();
assert.equal(query('#task-result-dialog').open, true);
assert.ok(query('#task-result-content').textContent.includes('Recovered title'));
assert.equal(query('#apply-task-result').hidden, false);
assert.equal(query('#apply-task-result').href, '/?task=completed');
query('#close-task-result').listeners.click();
assert.equal(query('#task-result-dialog').open, false);
results.set('ocr', {...base, id: 'ocr', status: 'completed', action: 'ocr', result: {ocr_status: 'completed'}});
await context.showTaskResult('ocr');
assert.equal(query('#apply-task-result').hidden, true);

const requestsBeforeIdle = calls.length;
for (let i = 0; i < 4; i++) timers.forEach(callback => callback());
assert.equal(calls.length, requestsBeforeIdle, 'Elapsed timers must not poll task state');
streams[0].onerror();
assert.equal(query('#task-connection').dataset.state, 'reconnecting');
assert.equal(query('#task-list').children.length, 4, 'Keep the last snapshot on disconnection');
streams[0].emit({...snapshot, tasks: []});
assert.equal(query('#task-connection').dataset.state, 'connected');
assert.equal(query('#task-list').children[0].textContent, '暂无后台任务');
streams[0].listeners.tasks({data: 'invalid JSON'});
assert.ok(query('#task-message').textContent.startsWith('任务状态更新失败：'));
windowEvents.pagehide();
assert.equal(streams[0].closed, true);
windowEvents.pageshow();
assert.equal(streams.length, 2);
windowEvents.pageshow();
assert.equal(streams.length, 2, 'Restoring a page must create only one stream');
console.log('Standalone task page UI checks passed');
