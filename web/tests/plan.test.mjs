// The plan card's words and state (src/lib/plan.ts): progress, the finished state, how `plan` events and late reads change what the panel shows.
import assert from 'node:assert/strict';
import test from 'node:test';

import { STATUS_WORDS, afterEvent, afterRead, progress, statusWord, summary } from '../src/lib/plan.ts';

const step = (n, status, over = {}) => ({ n, text: `step ${n}`, status, note: '', outside: false, ...over });
const plan = (statuses, over = {}) => ({ conversation: 'c1', title: 'Ship it', steps: statuses.map((status, i) => step(i + 1, status)), updated_at: '', ...over });

test('each status has its own word, and an unknown one is shown as it came', () => {
  assert.deepEqual(Object.values(STATUS_WORDS), ['To do', 'In progress', 'Done', 'Dropped']);
  assert.equal(new Set(Object.values(STATUS_WORDS)).size, 4);
  assert.equal(statusWord('doing'), 'In progress');
  assert.equal(statusWord('later'), 'later');
});

test('progress counts done steps, and a dropped step is neither pending nor in the total', () => {
  assert.deepEqual(progress(plan(['done', 'done', 'doing', 'todo', 'todo'])), { done: 2, dropped: 0, open: 3, finished: false });
  assert.deepEqual(progress(plan(['done', 'dropped', 'todo'])), { done: 1, dropped: 1, open: 1, finished: false });
  assert.equal(summary(plan(['done', 'done', 'done', 'doing', 'todo'])), '3 of 5 done');
  assert.equal(summary(plan(['done', 'dropped', 'todo'])), '1 of 2 done · 1 dropped');
  assert.equal(summary(plan(['todo'])), '0 of 1 done');
});

test('a plan with nothing left to do is finished and says so; it stays until Clara replaces it', () => {
  assert.equal(progress(plan(['done', 'done'])).finished, true);
  assert.equal(summary(plan(['done', 'done'])), 'All steps done');
  assert.equal(summary(plan(['done', 'dropped'])), 'All steps done · 1 dropped');
  assert.equal(summary(plan(['dropped', 'dropped'])), 'All steps dropped');
  assert.equal(progress(plan(['done', 'doing'])).finished, false);
  assert.equal(progress(plan([])).finished, false);
});

test('a plan event replaces the plan, null removes it, and any other event leaves it alone', () => {
  const old = plan(['todo']);
  const next = plan(['done', 'todo'], { title: 'New' });
  assert.equal(afterEvent(old, { type: 'plan', plan: next }), next);
  assert.equal(afterEvent(old, { type: 'plan', plan: null }), null);
  assert.equal(afterEvent(null, { type: 'plan', plan: next }), next);
  assert.equal(afterEvent(old, { type: 'token' }), old);
  assert.equal(afterEvent(null, { type: 'done' }), null);
});

test('a read that began before an event is dropped, one that did not is kept (404 is null)', () => {
  const shown = plan(['doing']);
  const read = plan(['todo']);
  assert.equal(afterRead(shown, read, 3, 3), read);
  assert.equal(afterRead(shown, read, 3, 4), shown);
  assert.equal(afterRead(null, null, 0, 0), null);
  assert.equal(afterRead(null, read, 0, 1), null);
  assert.equal(afterRead(shown, null, 2, 2), null);
});
