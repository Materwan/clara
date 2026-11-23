// Voice dictation on the page (src/lib/dictation.ts): the microphone button's state machine, the words of its errors, where the text goes.
import assert from 'node:assert/strict';
import test from 'node:test';

import { changes, clock, dictationPath, editOf, failure, maxSeconds, outcome, pickMimeType, step, unavailable, withText } from '../src/lib/dictation.ts';

test('the microphone opens on a press and on nothing else', () => {
  assert.deepEqual(step('idle', 'press'), { phase: 'starting', action: 'start' });
  for (const event of ['granted', 'denied', 'limit', 'hidden', 'moved', 'cancel', 'answered', 'failed']) assert.deepEqual(step('idle', event), { phase: 'idle', action: 'none' }, event);
});

test('a recording ends with the microphone closed, whatever ends it', () => {
  assert.deepEqual(step('starting', 'granted'), { phase: 'recording', action: 'none' });
  assert.deepEqual(step('starting', 'denied'), { phase: 'idle', action: 'none' });
  for (const phase of ['starting', 'recording']) for (const event of ['hidden', 'moved', 'cancel']) assert.deepEqual(step(phase, event), { phase: 'idle', action: 'discard' }, `${phase} ${event}`);
  assert.deepEqual(step('recording', 'failed'), { phase: 'idle', action: 'discard' });
  assert.deepEqual(step('recording', 'press'), { phase: 'sending', action: 'send' });
  assert.deepEqual(step('recording', 'limit'), { phase: 'sending', action: 'send' });
});

test('a second press while the browser asks or the audio goes up does nothing', () => {
  assert.deepEqual(step('starting', 'press'), { phase: 'starting', action: 'none' });
  assert.deepEqual(step('sending', 'press'), { phase: 'sending', action: 'none' });
});

test('while the audio is turned into text: a hidden page waits, Cancel or another conversation drops the request, an answer ends it', () => {
  assert.deepEqual(step('sending', 'hidden'), { phase: 'sending', action: 'none' });
  assert.deepEqual(step('sending', 'cancel'), { phase: 'idle', action: 'abort' });
  assert.deepEqual(step('sending', 'moved'), { phase: 'idle', action: 'abort' });
  assert.deepEqual(step('sending', 'answered'), { phase: 'idle', action: 'none' });
  assert.deepEqual(step('sending', 'failed'), { phase: 'idle', action: 'none' });
});

test('the recorder type is the first the browser records, webm first and mp4 for Safari; none: the browser chooses', () => {
  assert.equal(pickMimeType(() => true), 'audio/webm;codecs=opus');
  assert.equal(pickMimeType((type) => type === 'audio/mp4'), 'audio/mp4');
  assert.equal(pickMimeType(() => false), '');
});

test('the button explains why it cannot record', () => {
  assert.equal(unavailable({ secure: true, devices: true, recorder: true }), null);
  assert.match(unavailable({ secure: false, devices: false, recorder: true }), /secure page.*https/);
  assert.match(unavailable({ secure: true, devices: false, recorder: true }), /cannot record/);
  assert.match(unavailable({ secure: true, devices: true, recorder: false }), /cannot record/);
});

test('the timer reads mm:ss', () => {
  assert.equal(clock(0), '00:00');
  assert.equal(clock(7.9), '00:07');
  assert.equal(clock(83), '01:23');
  assert.equal(clock(-3), '00:00');
});

test('the longest dictation comes from the server\'s setting, else 300 seconds', () => {
  assert.equal(maxSeconds([{ key: 'x', value: 1 }, { key: 'dictation_max_seconds', value: 120 }]), 120);
  for (const settings of [undefined, [], [{ key: 'dictation_max_seconds', value: '120' }], [{ key: 'dictation_max_seconds', value: 99999 }]]) assert.equal(maxSeconds(settings), 300);
});

test('errors are said in plain words, the server\'s own for what it can tell how to fix', () => {
  assert.match(failure({ name: 'NotAllowedError' }), /refused.*Allow it/);
  assert.match(failure({ name: 'NotFoundError' }), /No microphone/);
  assert.match(failure({ name: 'NotSupportedError', message: 'Not supported' }), /cannot use the microphone/);
  assert.match(failure({ name: 'NotReadableError' }), /another program/);
  assert.match(failure({ status: 429, code: 'busy', message: 'busy' }), /try again in a moment/);
  assert.equal(failure({ status: 503, code: 'model_not_installed', message: 'Fetch it with /dictation fetch-model.' }), 'Fetch it with /dictation fetch-model.');
  assert.match(failure({ status: 503, code: 'dictation_not_installed' }), /not installed/);
  assert.match(failure({ status: 413, code: 'too_long' }), /too long/);
  assert.match(failure({ status: 413, code: 'too_large' }), /too big/);
  assert.match(failure({ status: 409 }), /already answering/);
  assert.match(failure({ status: 504 }), /too long and was stopped/);
  assert.match(failure({ status: 422 }), /could not read/);
  assert.match(failure({ status: 0 }), /cannot be reached/);
  assert.equal(failure({ status: 500, message: 'boom' }), 'boom');
});

test('the text goes at the end of what is in the box', () => {
  assert.equal(withText('', 'hello'), 'hello');
  assert.equal(withText('  \n', 'hello'), 'hello');
  assert.equal(withText('Please', 'send it'), 'Please send it');
  assert.equal(withText('Please   ', 'send it'), 'Please send it');
  assert.equal(withText('Line one\n', 'line two'), 'Line one\nline two');
});

test('the answer decides: silence, a run to follow, or the box', () => {
  const answer = { text: 'hi', seconds: 1, conversation: 'c1', sent: false };
  assert.equal(outcome({ ...answer, text: '' }), 'silence');
  assert.equal(outcome({ ...answer, text: '  ' }), 'silence');
  assert.equal(outcome(answer), 'compose');
  assert.equal(outcome({ ...answer, sent: true, running: true }), 'follow');
});

test('the audio goes to the open conversation, else to the web client\'s own', () => {
  assert.equal(dictationPath('c0123456789abcdef'), '/v1/dictation?client=web&conversation=c0123456789abcdef');
  assert.equal(dictationPath(null), '/v1/dictation?client=web');
});

test('only what differs from the saved settings is sent', () => {
  const saved = editOf({ client: 'web', conversation: null, auto_send: false, mode: null, packs: ['web', 'mail'] });
  assert.deepEqual(changes(saved, { ...saved }), {});
  assert.deepEqual(changes(saved, { ...saved, auto_send: true }), { auto_send: true });
  assert.deepEqual(changes(saved, { ...saved, mode: 'strict' }), { mode: 'strict' });
  assert.deepEqual(changes({ ...saved, mode: 'strict' }, { ...saved, mode: null }), { mode: null });
  assert.deepEqual(changes(saved, { ...saved, packs: ['mail', 'web'] }), {});
  assert.deepEqual(changes(saved, { ...saved, packs: ['web'] }), { packs: ['web'] });
});
