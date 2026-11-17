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
          if (selector === '.reference-card' && child.className === 'reference-card') matches.push(child);
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
  const calls = [];
  let pending, pendingMetadata, failure, confirmation = true;
  const citation = (title = 'First reference', number = '1') => ({
    number, title, authors: ['Alice Smith'], year: '2024', venue: 'Journal', raw: 'Raw ' + title,
    source_pages: [8],
  });
  const reply = data => ({ok: true, json: async () => data});
  const extracted = (references, warnings = []) => reply({references, count: references.length, total_pages: 10, warnings});
  const context = vm.createContext({
    document: { querySelector: query, createElement: element },
    window: { confirm: () => confirmation }, URLSearchParams, setTimeout, clearTimeout,
    fetch: async (url, options) => {
      calls.push([url, options]);
      if (url.startsWith('/api/papers?')) return reply({papers: [], total: 0, page: 1, page_count: 1});
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
