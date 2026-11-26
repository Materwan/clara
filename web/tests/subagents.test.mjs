// The words of a sub agent's card (src/lib/subagents.ts) and the check of the Admin setting.
import assert from 'node:assert/strict';
import test from 'node:test';

import { MAX, MIN, badge, cardLine, elapsed, limitProblem } from '../src/lib/subagents.ts';

const now = Date.parse('2026-11-26T12:00:00Z');
const sub = (over = {}) => ({
  id: 'c1', title: 'Library hours', state: 'done', report: 'Opens at nine.', created_at: '2026-11-26T11:50:00Z', updated_at: '2026-11-26T11:58:00Z',
  waiting: 0, asking: '', written: 0, ...over,
});

test('each state has its badge, and a request that waits for you comes first', () => {
  assert.deepEqual(badge(sub()), { word: 'Done', tone: 'ok' });
  assert.deepEqual(badge(sub({ state: 'running' })), { word: 'Running', tone: 'run' });
  assert.deepEqual(badge(sub({ state: 'asked' })), { word: 'Asked', tone: 'warn' });
  assert.deepEqual(badge(sub({ state: 'failed' })), { word: 'Failed', tone: 'danger' });
  assert.deepEqual(badge(sub({ state: 'running', waiting: 1 })), { word: 'Asks you', tone: 'warn' });
  assert.deepEqual(badge(sub({ state: 'failed', waiting: 2 })), { word: 'Asks you', tone: 'warn' });
});

test('how long ago is told in words, and a date that is not one is just now', () => {
  assert.equal(elapsed('2026-11-26T11:59:30Z', now), 'just now');
  assert.equal(elapsed('2026-11-26T12:00:30Z', now), 'just now'); // a clock a little ahead
  assert.equal(elapsed('nonsense', now), 'just now');
  assert.equal(elapsed('2026-11-26T11:59:00Z', now), '1 minute ago');
  assert.equal(elapsed('2026-11-26T11:58:00Z', now), '2 minutes ago');
  assert.equal(elapsed('2026-11-26T10:00:00Z', now), '2 hours ago');
  assert.equal(elapsed('2026-11-24T12:00:00Z', now), '2 days ago');
});

test('the line under the title says what it did and when, and how many files it wrote', () => {
  assert.equal(cardLine(sub({ written: 1 }), now), 'Reported 2 minutes ago · 1 file written');
  assert.equal(cardLine(sub({ written: 3 }), now), 'Reported 2 minutes ago · 3 files written');
  assert.equal(cardLine(sub(), now), 'Reported 2 minutes ago');
  assert.equal(cardLine(sub({ state: 'running', updated_at: '2026-11-26T11:59:59Z' }), now), 'Working, started just now');
  assert.equal(cardLine(sub({ state: 'asked' }), now), 'Asked a question 2 minutes ago');
  assert.equal(cardLine(sub({ state: 'failed' }), now), 'Stopped 2 minutes ago');
});

test('a request that waits for you is named on the line, and the others are counted', () => {
  assert.equal(cardLine(sub({ waiting: 1, asking: 'Send a mail to Ana' }), now), 'Waiting for your permission: Send a mail to Ana');
  assert.equal(cardLine(sub({ waiting: 3, asking: 'Send a mail to Ana' }), now), 'Waiting for your permission: Send a mail to Ana (and 2 more)');
  assert.equal(cardLine(sub({ waiting: 1, written: 2 }), now), 'Waiting for your permission · 2 files written');
});

test('the setting is a whole number from 1 to 20', () => {
  assert.equal([MIN, MAX].join('-'), '1-20');
  for (const good of ['1', '5', ' 20 ', '07']) assert.equal(limitProblem(good), '', good);
  for (const bad of ['', '0', '21', '-1', '2.5', '1e1', 'five', '5 6']) assert.notEqual(limitProblem(bad), '', bad);
  assert.equal(limitProblem('21'), 'It must be from 1 to 20.');
  assert.equal(limitProblem('x'), 'Write a whole number from 1 to 20.');
});
