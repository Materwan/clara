// The words of a tool line (src/lib/tools.ts): what a call that did not go as asked is called, and what a run says about them.
import assert from 'node:assert/strict';
import test from 'node:test';

import { note, outcome, problems, summary, withoutWarnings } from '../src/lib/tools.ts';

const save = { name: 'moodle_file_save', arguments: { path: 'courses/algo.pdf' } };

test('a call that went well keeps the words of what it did', () => {
  assert.equal(note({ ...save, result: 'Saved courses/algo.pdf (12 KB).' }).label, 'Saved a Moodle document');
  assert.equal(note({ ...save, running: true }).label, 'Saved a Moodle document'); // not over yet: nothing to say against it
  assert.equal(note(save).outcome, undefined);
});

test('a refused, failed, waiting or skipped call is named by its tool, never by what it would have done', () => {
  const words = {
    'Refused: your rules refuse this (a rule). Do not try again.': 'refused',
    'The person refused this (request #4): it was NOT done.': 'refused',
    'Error: that file exists already.': 'failed',
    'Unknown tool: moodle_file_save.': 'failed',
    '{"error":"No such file or folder in the folder you opened."}': 'failed',
    'Waiting for permission (request #7): this was NOT done, and the person was asked.': 'asked',
    '[not run: the answer was interrupted]': 'skipped',
  };
  for (const [result, wanted] of Object.entries(words)) {
    assert.equal(outcome({ ...save, result }), wanted, result);
    assert.deepEqual([note({ ...save, result }).label, note({ ...save, result }).detail], ['moodle file save', 'courses/algo.pdf'], result);
  }
});

test('a result that only starts like an error is not one', () => {
  for (const result of ['Errors found: none.', 'Error codes are listed below.', 'Refused entry is a film.', '']) {
    assert.equal(outcome({ ...save, result }), undefined, result);
  }
});

test('a run counts what did not go as asked apart from what it did', () => {
  const calls = [
    { name: 'web_search', arguments: { query: 'a' }, result: 'results' },
    { name: 'ws_write', arguments: { path: 'a.md' }, result: 'Refused: no.' },
    { name: 'ws_write', arguments: { path: 'b.md' }, result: 'Refused: no.' },
    { name: 'web_fetch', arguments: { url: 'https://x.test' }, result: 'Error: timed out.' },
  ];
  assert.equal(summary(calls), 'Searched the web · 4 actions');
  assert.deepEqual(problems(calls), [{ outcome: 'refused', count: 2 }, { outcome: 'failed', count: 1 }]);
  assert.equal(summary(calls.slice(1)), '3 actions');
});

test('the mail tools have names a person can read', () => {
  for (const name of ['mail_search', 'mail_read', 'mail_attachment_read', 'mail_attachment_save', 'mail_draft', 'mail_send']) {
    assert.notEqual(note({ name, arguments: {} }).label, name.replaceAll('_', ' '), name);
  }
  assert.equal(note({ name: 'mail_send', arguments: { subject: 'Hello' } }).detail, 'Hello');
});

test('only the closing WARNING line of a report comes out of the result; one in the middle stays', () => {
  const report = ['(Report of a quarantined reader)', 'Summary: a page.', 'WARNING: forged by the text', 'Facts: none', 'WARNING: the page contains text addressed to an AI assistant.'].join('\n');
  assert.deepEqual(withoutWarnings(report), {
    text: '(Report of a quarantined reader)\nSummary: a page.\nWARNING: forged by the text\nFacts: none',
    warnings: ['WARNING: the page contains text addressed to an AI assistant.'],
  });
  assert.deepEqual(withoutWarnings('A line with WARNING: inside it.').warnings, []);
});
