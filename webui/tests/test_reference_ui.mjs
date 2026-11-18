import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import vm from 'node:vm';

// Test the actual frontend with a minimal DOM. This is not browser/layout QA.
export async function runReferenceUiTests() {
  const source = await readFile(new URL('../static/app.js', import.meta.url), 'utf8');
  const elements = new Map();
  function element(tag = 'div') {
    return {
      tagName: tag.toUpperCase(), value: '', textContent: '', disabled: false, open: false,
      dataset: {}, children: [], listeners: {}, attrs: {}, className: '', hidden: false,
      classList: { toggle() {} },
      addEventListener(name, callback) { this.listeners[name] = callback; },
      getAttribute(name) { return this.attrs[name] || ''; },
      setAttribute(name, value) { this.attrs[name] = value; },
      removeAttribute() {}, replaceChildren() { this.children = []; },
      append(...nodes) { this.children.push(...nodes); }, reset() {}, focus() { this.focused = true; },
      showModal() { this.open = true; }, close() { this.open = false; },
      get lastElementChild() { return this.children.at(-1); },
      querySelector(selector) { return this.querySelectorAll(selector)[0]; },
      querySelectorAll(selector) {
        const matches = [];
        for (const child of this.children) {
          if (selector.startsWith('.') && child.className === selector.slice(1)) matches.push(child);
          const status = selector.match(/^\[data-status="(.+)"\]$/)?.[1];
          if (status && child.dataset?.status === status) matches.push(child);
          const field = selector.match(/^\[data-reference-field="(.+)"\]$/)?.[1];
          if (field && child.dataset?.referenceField === field) matches.push(child);
          matches.push(...child.querySelectorAll(selector));
        }
        return matches;
      },
    };
  }
  const query = selector => {
    if (!elements.has(selector)) elements.set(selector, element());
    return elements.get(selector);
  };
  const batchButtons = ["ocr", "references", "markdown"].map(action => {
    const button = element("button");
    button.dataset.action = action;
    button.dataset.bulkAction = action;
    return button;
  });
  let listing = [];
  let batchResponses = {};
  let queueListing = {tasks: [], counts: {queued: 0, running: 0, completed: 0, failed: 0}};
  let batchMode = false;
  const calls = [];
  let pending, pendingMetadata, failure, confirmation = true;
  const taskResolvers = new Map();
  const taskValues = new Map();
  let taskSequence = 0;
  const citation = (title = 'First reference', number = '1') => ({
    number, title, authors: ['Alice Smith'], year: '2024', venue: 'Journal', raw: 'Raw ' + title,
    source_pages: [8],
  });
  const reply = data => ({ok: true, json: async () => data});
  const extracted = (references, warnings = []) => reply({references, count: references.length, total_pages: 10, warnings});
  const context = vm.createContext({
    document: {
      querySelector: query,
      querySelectorAll: selector => selector === "[data-bulk-action]" ? batchButtons : [],
      createElement: element,
    },
    window: { confirm: () => confirmation }, URLSearchParams, setTimeout, clearTimeout,
    fetch: async (url, options) => {
      calls.push([url, options]);
      if (url === '/api/tasks?limit=100') return reply(queueListing);
      if (url === '/api/tasks' && options?.method === 'POST') {
        const request = JSON.parse(options.body);
        const id = 'task-' + request.action + '-' + (++taskSequence);
        const finishTask = response => response.json().then(payload => { const data = {id, status: response.ok ? 'completed' : 'failed', result: payload, error: payload.detail || ''}; taskValues.set(id, data); taskResolvers.get(id)?.(data); });
        if (!batchMode && request.action === 'references') pending = finishTask;
        if (!batchMode && request.action === 'metadata') pendingMetadata = finishTask;
        return reply({id, status: 'queued'});
      }
      if (url.startsWith('/api/tasks/task-')) {
        if (!batchMode) { const id = url.slice('/api/tasks/'.length); return new Promise(resolve => { taskResolvers.set(id, data => resolve(reply(data))); if (taskValues.has(id)) taskResolvers.get(id)(taskValues.get(id)); }); }
        return reply({id: url.slice('/api/tasks/'.length), status: 'completed', result: batchResponses.task || {}});
      }
      if (url.startsWith('/api/papers?')) return reply({papers: listing, total: listing.length, page: 1, page_count: 1});
      if (batchMode && url.endsWith('/ocr')) return reply(batchResponses.ocr);
      if (batchMode && url.endsWith('/references')) return reply(batchResponses.references);
      if (batchMode && url.endsWith('/markdown')) return reply(batchResponses.markdown);
      if (batchMode && (!options || options.method === 'GET')) return reply(batchResponses.metadata);
      if (batchMode && options?.method === 'PUT') return reply(JSON.parse(options.body));
      if (url.endsWith('/metadata/extract')) return new Promise(resolve => { pendingMetadata = resolve; });
      if (url.endsWith('/references')) return new Promise(resolve => { pending = resolve; });
      if (options?.method === 'PUT') return reply(JSON.parse(options.body));
      if (failure) return {ok: false, json: async () => ({detail: 'load failed'})};
      return reply({title: url, venue: '', abstract: '', reference: 'Legacy text', references: [citation(url)]});
    },
  });
  vm.runInContext(source, context);
  const open = filename => context.openPaper(filename);
  const button = query('#extract-reference');
  const save = query('#save-paper');
  const status = query('#references-status');
  const list = query('#references-list');
  const search = query('#references-search');
  const count = query('#references-count');
  const submit = query('#paper-form').listeners.submit;
  const entries = () => vm.runInContext('referenceEntries', context);
  let checks = 0;

  listing = [{filename: "batch.pdf", name: "Batch paper", venue: "", size_kb: 1,
    starred: false, ocr_status: "not_started", ocr_downloadable: false,
    markdown_available: false, references_count: 0, references_processing: false}];
  batchMode = true;
  await context.loadPapers(1);
  const selectPage = query('#select-page-papers');
  selectPage.checked = true;
  selectPage.listeners.change();
  assert.equal(query('#bulk-selected-count').textContent, '已选 1 篇');
  assert.equal(batchButtons[0].disabled, false);
  const rowCheckbox = query('#paper-list').children[0].children[0].children[0];
  assert.equal(rowCheckbox.checked, true);
  batchResponses.ocr = {ocr_status: 'completed', ocr_downloadable: true, ocr_error: ''};
  let beforeBatch = calls.length;
  await batchButtons[0].listeners.click();
  assert.ok(calls.slice(beforeBatch).some(([url, options]) => url === '/api/tasks' && JSON.parse(options.body).action === 'ocr'));
  assert.ok(query('#bulk-results').children[0].children[1].textContent.includes('已排队'));
  assert.equal(query('#bulk-selected-count').textContent, '已选 0 篇');
  checks++;

  listing = [{filename: "partial-ocr.pdf", name: "Partial OCR", venue: "", size_kb: 1,
    starred: false, ocr_status: "partial", ocr_downloadable: true,
    markdown_available: false, references_count: 0, references_processing: false}];
  await context.loadPapers(1);
  selectPage.checked = true;
  selectPage.listeners.change();
  beforeBatch = calls.length;
  await batchButtons[0].listeners.click();
  assert.ok(!calls.slice(beforeBatch).some(([url]) => url.endsWith('/partial-ocr.pdf/ocr')));
  assert.ok(query('#bulk-results').children[0].children[1].textContent.includes('已有 OCR'));
  checks++;

  listing = [{filename: "existing.pdf", name: "Existing references", venue: "", size_kb: 1,
    starred: false, ocr_status: "completed", ocr_downloadable: true,
    markdown_available: true, references_count: 4, references_processing: false}];
  batchResponses.metadata = {references: [citation("Already saved")], title: "Existing", venue: "", abstract: "", reference: "", starred: false};
  await context.loadPapers(1);
  selectPage.checked = true;
  selectPage.listeners.change();
  beforeBatch = calls.length;
  await batchButtons[1].listeners.click();
  assert.ok(!calls.slice(beforeBatch).some(([url]) => url === '/api/tasks' && JSON.parse(url[1]?.body || '{}').action === 'references'));
  assert.ok(query('#bulk-results').children[0].children[1].textContent.includes('已有 4 条参考文献'));
  checks++;

  beforeBatch = calls.length;
  selectPage.checked = true;
  selectPage.listeners.change();
  await batchButtons[2].listeners.click();
  assert.ok(!calls.slice(beforeBatch).some(([url]) => url === '/api/tasks'));
  assert.ok(query('#bulk-results').children[0].children[1].textContent.includes('已有 Markdown'));
  checks++;

  listing = [{filename: "new-refs.pdf", name: "New references", venue: "", size_kb: 1,
    starred: false, ocr_status: "completed", ocr_downloadable: true,
    markdown_available: false, references_count: 0, references_processing: false}];
  batchResponses.references = {references: [{title: "Ref", raw: "Ref", authors: [], source_pages: []}],
    warnings: [], count: 1, total_pages: 1};
  await context.loadPapers(1);
  selectPage.checked = true;
  selectPage.listeners.change();
  beforeBatch = calls.length;
  await batchButtons[1].listeners.click();
  const refsTask = calls.slice(beforeBatch).find(([url, options]) => url === '/api/tasks' && options?.method === 'POST');
  assert.equal(JSON.parse(refsTask[1].body).save_result, true);
  assert.ok(query('#bulk-results').children[0].children[1].textContent.includes('已排队'));
  checks++;

  listing = [{filename: "new-md.pdf", name: "New markdown", venue: "", size_kb: 1,
    starred: false, ocr_status: "partial", ocr_downloadable: true,
    markdown_available: false, references_count: 0, references_processing: false}];
  await context.loadPapers(1);
  selectPage.checked = true;
  selectPage.listeners.change();
  beforeBatch = calls.length;
  await batchButtons[2].listeners.click();
  assert.ok(calls.slice(beforeBatch).some(([url, options]) => url === '/api/tasks' && JSON.parse(options.body).action === 'markdown'));
  assert.ok(query('#bulk-results').children[0].children[1].textContent.includes('已排队'));
  checks++;

  listing = [{filename: "no-ocr.pdf", name: "No OCR", venue: "", size_kb: 1,
    starred: false, ocr_status: "failed", ocr_downloadable: true,
    markdown_available: false, references_count: 0, references_processing: false}];
  await context.loadPapers(1);
  selectPage.checked = true;
  selectPage.listeners.change();
  beforeBatch = calls.length;
  await batchButtons[2].listeners.click();
  assert.ok(!calls.slice(beforeBatch).some(([url]) => url === '/api/tasks'));
  assert.ok(query('#bulk-results').children[0].children[1].textContent.includes('尚无可用 OCR'));
  checks++;
  batchMode = false;
  listing = [];
  calls.length = 0;

  await open('first.pdf');
  assert.equal(button.disabled, false);
  assert.equal(count.textContent, '1 条');
  assert.equal(query('#legacy-reference').hidden, false);
  assert.equal(query('#paper-reference').value, 'Legacy text');
  const extraction = button.listeners.click();
  assert.equal(button.disabled, true);
  assert.equal(save.disabled, true);
  const beforeSubmit = calls.length;
  await submit({preventDefault() {}});
  assert.equal(calls.length, beforeSubmit, 'No save during extraction');
  pending(extracted([citation('Extracted one'), citation('Extracted two', '2')], ['OCR incomplete']));
  await extraction;
  assert.equal(entries().length, 2);
  assert.equal(entries()[1].title, 'Extracted two');
  assert.equal(count.textContent, '2 条');
  assert.ok(status.textContent.includes('OCR incomplete'));
  assert.equal(save.disabled, false);
  assert.ok(calls.every(([, options]) => options?.method !== 'PUT'), 'Extraction only previews');
  checks++;

  const failed = button.listeners.click();
  pending({ok: false, json: async () => ({detail: 'Extraction failed'})});
  await failed;
  assert.equal(entries().length, 2);
  assert.equal(status.textContent, 'Extraction failed');
  assert.equal(button.disabled, false);
  checks++;

  const empty = button.listeners.click();
  pending(extracted([]));
  await empty;
  assert.equal(entries().length, 2, 'No matches must not erase existing list');
  checks++;

  const edited = button.listeners.click();
  const titleInput = list.querySelector('[data-reference-field="title"]');
  titleInput.value = 'Manual edit';
  titleInput.listeners.input();
  pending(extracted([citation('Must not replace edit')]));
  await edited;
  assert.equal(entries()[0].title, 'Manual edit');
  checks++;

  search.value = 'Extracted two';
  search.listeners.input();
  assert.equal(list.children.length, 1);
  assert.ok(list.children[0].children[0].children[0].textContent.includes('Extracted two'));
  assert.equal(count.textContent, '2 条');
  search.value = 'no match';
  search.listeners.input();
  assert.equal(list.children[0].className, 'references-empty');
  search.value = '';
  search.listeners.input();
  checks++;

  const authors = list.querySelector('[data-reference-field="authors"]');
  authors.value = '作者一\nAuthor Two\n';
  authors.listeners.input();
  assert.equal(entries()[0].authors.length, 2);
  assert.equal(entries()[0].authors[0], '作者一');
  await submit({preventDefault() {}});
  const saved = JSON.parse(calls.findLast(([, options]) => options?.method === 'PUT')[1].body);
  assert.equal(saved.references.length, 2);
  assert.equal(saved.references[0].title, 'Manual edit');
  assert.equal(saved.reference, 'Legacy text');
  checks++;

  query('#add-reference').listeners.click();
  assert.equal(entries().length, 3);
  assert.equal(list.lastElementChild.open, true);
  assert.equal(list.lastElementChild.querySelector('[data-reference-field="title"]').focused, true);
  const beforeBlankSave = calls.length;
  await submit({preventDefault() {}});
  assert.equal(calls.length, beforeBlankSave, 'Cannot save blank source entry');
  const newRaw = list.lastElementChild.querySelector('[data-reference-field="raw"]');
  newRaw.value = 'New raw entry';
  newRaw.listeners.input();
  const remove = list.lastElementChild.children[1].children.at(-1);
  remove.listeners.click();
  assert.equal(entries().length, 2);
  checks++;

  confirmation = false;
  const beforeCancel = calls.length;
  await button.listeners.click();
  assert.equal(calls.length, beforeCancel);
  confirmation = true;
  checks++;

  const stale = button.listeners.click();
  const finishStale = pending;
  query('#paper-dialog').close();
  await open('second.pdf');
  finishStale(extracted([citation('Wrong paper')]));
  await stale;
  assert.ok(entries()[0].title.endsWith('second.pdf'));
  assert.equal(button.disabled, false);
  checks++;

  const oldSamePaper = button.listeners.click();
  const finishOld = pending;
  query('#paper-dialog').close();
  await open('second.pdf');
  const newSamePaper = button.listeners.click();
  const finishNew = pending;
  finishOld(extracted([citation('Old request')]));
  await oldSamePaper;
  assert.equal(button.disabled, true);
  finishNew(extracted([citation('New request')]));
  await newSamePaper;
  assert.equal(entries()[0].title, 'New request');
  assert.equal(button.disabled, false);
  checks++;


  const metadataButton = query('#extract-metadata');
  const metadataStatus = query('#metadata-status');
  const title = query('#paper-title');
  const abstract = query('#paper-abstract');
  const metadataReply = (title, abstract) => reply({title, abstract, warnings: []});
  assert.equal(metadataButton.disabled, false);
  const oldReferenceCount = entries().length;
  const beforeMetadata = calls.length;
  const metadataExtraction = metadataButton.listeners.click();
  assert.equal(save.disabled, true);
  assert.equal(button.disabled, true);
  await submit({preventDefault() {}});
  assert.equal(calls.length, beforeMetadata + 1);
  pendingMetadata(metadataReply('Extracted title', 'Full original abstract.'));
  await metadataExtraction;
  assert.equal(title.value, 'Extracted title');
  assert.equal(abstract.value, 'Full original abstract.');
  assert.equal(entries().length, oldReferenceCount);
  assert.equal(button.disabled, false);
  assert.equal(metadataButton.disabled, false);
  assert.ok(calls.slice(beforeMetadata).every(([, opts]) => opts?.method !== 'PUT'));
  checks++;

  const emptyMetadata = metadataButton.listeners.click();
  pendingMetadata(metadataReply('', ''));
  await emptyMetadata;
  assert.equal(title.value, 'Extracted title');
  assert.equal(abstract.value, 'Full original abstract.');
  assert.ok(metadataStatus.textContent.includes('保留原内容'));
  checks++;

  const failureMetadata = metadataButton.listeners.click();
  pendingMetadata({ok: false, json: async () => ({detail: 'Metadata failed'})});
  await failureMetadata;
  assert.equal(title.value, 'Extracted title');
  assert.equal(abstract.value, 'Full original abstract.');
  assert.equal(metadataStatus.textContent, 'Metadata failed');
  assert.equal(metadataButton.disabled, false);
  checks++;

  const editMetadata = metadataButton.listeners.click();
  title.value = 'Manual title';
  title.listeners.input();
  pendingMetadata(metadataReply('Should not replace', 'New original abstract.'));
  await editMetadata;
  assert.equal(title.value, 'Manual title');
  assert.equal(abstract.value, 'New original abstract.');
  checks++;

  const sameValueEdit = metadataButton.listeners.click();
  abstract.value = 'Changed temporarily';
  abstract.listeners.input();
  abstract.value = 'New original abstract.';
  abstract.listeners.input();
  pendingMetadata(metadataReply('Second extracted title', 'Must not replace edited abstract'));
  await sameValueEdit;
  assert.equal(title.value, 'Second extracted title');
  assert.equal(abstract.value, 'New original abstract.');
  checks++;

  const staleMetadata = metadataButton.listeners.click();
  const finishStaleMetadata = pendingMetadata;
  query('#paper-dialog').close();
  await open('third.pdf');
  finishStaleMetadata(metadataReply('Wrong paper title', 'Wrong abstract'));
  await staleMetadata;
  assert.ok(title.value.endsWith('third.pdf'));
  assert.equal(metadataButton.disabled, false);
  checks++;

  const oldMetadata = metadataButton.listeners.click();
  const finishOldMetadata = pendingMetadata;
  query('#paper-dialog').close();
  await open('third.pdf');
  const newMetadata = metadataButton.listeners.click();
  const finishNewMetadata = pendingMetadata;
  finishOldMetadata(metadataReply('Old metadata', 'Old abstract'));
  await oldMetadata;
  assert.equal(metadataButton.disabled, true);
  finishNewMetadata(metadataReply('New metadata', 'New full abstract'));
  await newMetadata;
  assert.equal(title.value, 'New metadata');
  assert.equal(abstract.value, 'New full abstract');
  await submit({preventDefault() {}});
  const metadataSaved = JSON.parse(calls.findLast(([, opts]) => opts?.method === 'PUT')[1].body);
  assert.equal(metadataSaved.title, 'New metadata');
  assert.equal(metadataSaved.abstract, 'New full abstract');
  checks++;

  const resultTask = {id: 'task-result-metadata', filename: 'third.pdf', title: 'Third paper', action: 'metadata', status: 'completed', result: {title: 'Recovered title', abstract: 'Recovered abstract'}};
  taskValues.set(resultTask.id, resultTask);
  await context.showTaskResult(resultTask.id);
  assert.equal(query('#task-result-dialog').open, true);
  assert.ok(query('#task-result-content').textContent.includes('Recovered abstract'));
  assert.equal(query('#apply-task-result').hidden, false);
  await open('third.pdf');
  confirmation = false;
  await query('#apply-task-result').listeners.click();
  assert.notEqual(title.value, 'Recovered title');
  confirmation = true;
  await query('#apply-task-result').listeners.click();
  assert.equal(title.value, 'Recovered title');
  assert.equal(abstract.value, 'Recovered abstract');
  assert.equal(query('#task-result-dialog').open, false);
  checks++;

  const referenceTask = {...resultTask, id: 'task-result-references', action: 'references', result: {references: [citation('Recovered citation')]}};
  taskValues.set(referenceTask.id, referenceTask);
  await context.showTaskResult(referenceTask.id);
  await query('#apply-task-result').listeners.click();
  assert.equal(entries()[0].title, 'Recovered citation');
  checks++;

  queueListing = {workers: 3, counts: {running: 1, queued: 1, failed: 1}, tasks: [
    {...resultTask, id: 'running', status: 'running', worker: 'worker-2'},
    {...resultTask, id: 'queued', status: 'queued'},
    {...resultTask, id: 'failed', status: 'failed', error: 'Provider unavailable'},
  ]};
  await context.refreshTasks();
  const taskRows = query('#task-list').children;
  assert.equal(taskRows.length, 3);
  assert.ok(taskRows[0].children.some(child => child.textContent === 'worker-2'));
  assert.ok(taskRows[1].children.some(child => child.textContent === '取消'));
  assert.ok(taskRows[2].children.some(child => child.textContent === '重试'));
  assert.ok(taskRows[2].children.some(child => child.textContent === 'Provider unavailable'));
  checks++;

  failure = true;
  await open('unavailable.pdf');
  assert.equal(save.disabled, true);
  assert.equal(button.disabled, true);
  assert.equal(query('#add-reference').disabled, true);
  assert.equal(query('#extract-metadata').disabled, true);
  checks++;
  return checks;
}

console.log('Reference UI checks passed: ' + await runReferenceUiTests());
