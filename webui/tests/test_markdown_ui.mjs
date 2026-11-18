import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import vm from 'node:vm';

const source = await readFile(new URL('../static/markdown-reader.js', import.meta.url), 'utf8');
const elements = new Map();
const events = {};
const calls = [];
const streams = [];
let responseState = 'generating';
let releasePost;
const pendingPost = new Promise(resolve => { releasePost = resolve; });
function query(selector) {
  if (!elements.has(selector)) elements.set(selector, {
    hidden: false, disabled: false, textContent: '', innerHTML: '', listeners: {},
    classList: {add() {}, remove() {}, toggle() {}},
    setAttribute() {}, replaceChildren() { this.innerHTML = ''; },
    addEventListener(name, callback) { this.listeners[name] = callback; },
  });
  return elements.get(selector);
}
class EventSource {
  constructor(url) { this.url = url; this.listeners = {}; streams.push(this); }
  addEventListener(name, callback) { this.listeners[name] = callback; }
  emit(data) { this.listeners.tasks({data: JSON.stringify(data)}); }
  close() { this.closed = true; }
}
const reply = status => ({ok: true, status: 200, text: async () => JSON.stringify({status, title: 'Paper', html: status === 'ready' ? '<p>Ready</p>' : '', content: ''})});
const context = vm.createContext({
  document: {querySelector: query}, EventSource, URL, URLSearchParams,
  location: {pathname: '/markdown/paper.pdf', search: '', href: 'http://test/markdown/paper.pdf'},
  history: {replaceState() {}},
  window: {addEventListener: (name, callback) => { events[name] = callback; }},
  fetch: async (url, options) => { calls.push([url, options]); return options?.method === 'POST' ? pendingPost : reply(responseState); },
});
vm.runInContext(source, context);
const tick = async () => { for (let i = 0; i < 16; i++) await Promise.resolve(); };
await tick();
assert.equal(streams.length, 1);
assert.equal(calls.length, 1);
streams[0].emit({tasks: [], changed: {filename: 'other.pdf', action: 'markdown'}});
await tick();
assert.equal(calls.length, 1, 'Unrelated task events must not reload the document');
responseState = 'ready';
streams[0].emit({changed: {filename: 'paper.pdf', action: 'markdown', status: 'completed'}});
await tick();
assert.equal(query('#markdown-content').innerHTML, '<p>Ready</p>');
assert.equal(calls.length, 2);
streams[0].onerror();
assert.ok(query('#markdown-status').textContent.includes('重新连接'));
streams[0].emit({tasks: []});
await tick();
assert.equal(query('#markdown-status').textContent, '');
assert.equal(calls.length, 3, 'Reconnect snapshot must reconcile the document');

const posting = context.generateDocument(true);
await tick();
streams[0].emit({changed: {filename: 'paper.pdf', action: 'markdown', status: 'completed'}});
await tick();
releasePost(reply('generating'));
await posting;
assert.equal(query('#markdown-content').innerHTML, '<p>Ready</p>', 'Late enqueue response cannot overwrite completion');
assert.equal(query('#markdown-regenerate').hidden, false);

responseState = 'generating';
query('#markdown-language-zh').listeners.click();
await tick();
const afterLanguage = calls.length;
streams[0].emit({changed: {filename: 'paper.pdf', action: 'markdown'}});
await tick();
assert.equal(calls.length, afterLanguage);
responseState = 'ready';
streams[0].emit({changed: {filename: 'paper.pdf', action: 'translation', status: 'completed'}});
await tick();
assert.equal(calls.length, afterLanguage + 1);
assert.ok(query('#markdown-download').href.endsWith('language=zh'));
events.pagehide();
assert.equal(streams[0].closed, true);
events.pageshow();
assert.equal(streams.length, 2);
assert.ok(!source.includes('setTimeout') && !source.includes('setInterval'), 'Reader must not poll');
console.log('Markdown SSE checks passed: 8');
