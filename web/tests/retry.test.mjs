// How a read waits and asks again (src/lib/retry.ts), run by Node on the file as it is: `npm test` in web/.
import assert from 'node:assert/strict';
import test from 'node:test';

import { TimedOut, retrying, within } from '../src/lib/retry.ts';

const flaky = (failures, error = new Error('dropped')) => {
  const calls = { n: 0 };
  const run = async () => {
    calls.n++;
    if (calls.n <= failures) throw error;
    return 'ok';
  };
  return { calls, run };
};
const always = () => true;

test('a read that fails twice is asked a third time, after pauses that grow', async () => {
  const { calls, run } = flaky(2);
  const pauses = [];
  const answer = await retrying(run, { retries: 2, retryable: always, wait: async (ms) => void pauses.push(ms) });
  assert.equal(answer, 'ok');
  assert.equal(calls.n, 3);
  assert.deepEqual(pauses, [400, 1200]);
});

test('it gives up after the retries and throws the last failure', async () => {
  const { calls, run } = flaky(10, new Error('still down'));
  await assert.rejects(retrying(run, { retries: 2, retryable: always, wait: async () => {} }), /still down/);
  assert.equal(calls.n, 3);
});

test('a failure that is not worth asking again for is thrown at once', async () => {
  const { calls, run } = flaky(1, new Error('not found'));
  await assert.rejects(retrying(run, { retries: 2, retryable: (error) => error.message !== 'not found', wait: async () => {} }), /not found/);
  assert.equal(calls.n, 1);
});

test('nothing is asked again once the caller has given up', async () => {
  const { calls, run } = flaky(10);
  await assert.rejects(retrying(run, { retries: 5, retryable: always, cancelled: () => true, wait: async () => {} }), /dropped/);
  assert.equal(calls.n, 1);
});

test('no retries: one attempt', async () => {
  const { calls, run } = flaky(1);
  await assert.rejects(retrying(run, { retries: 0, retryable: always }), /dropped/);
  assert.equal(calls.n, 1);
});

const hangs = (signal) =>
  new Promise((_, reject) => signal.addEventListener('abort', () => reject(new DOMException('aborted', 'AbortError'))));

test('a request that never answers ends with TimedOut', async () => {
  await assert.rejects(within(20, undefined, hangs), TimedOut);
});

test('a request that answers in time is not touched, and its timer is gone', async () => {
  assert.equal(await within(1000, undefined, async () => 'fast'), 'fast');
});

test('with no time limit (0) a request waits as long as it likes', async () => {
  assert.equal(await within(0, undefined, () => new Promise((resolve) => setTimeout(() => resolve('late'), 40))), 'late');
});

test('the caller aborting is its own error, not a timeout', async () => {
  const caller = new AbortController();
  const asked = within(5000, caller.signal, hangs);
  caller.abort();
  await assert.rejects(asked, (error) => error.name === 'AbortError' && !(error instanceof TimedOut));
});

test('a failure of the request itself is passed on as it is', async () => {
  await assert.rejects(within(1000, undefined, async () => { throw new TypeError('Failed to fetch'); }), TypeError);
});

test('once the answer has started, the time limit stops: a long body is read to its end', async () => {
  const body = await within(20, undefined, async (signal, answered) => {
    answered();
    await new Promise((resolve) => setTimeout(resolve, 60));
    return 'whole body';
  });
  assert.equal(body, 'whole body');
});

test('after the answer has started, the caller can still stop the read', async () => {
  const caller = new AbortController();
  const asked = within(5000, caller.signal, (signal, answered) => {
    answered();
    return hangs(signal);
  });
  caller.abort();
  await assert.rejects(asked, (error) => error.name === 'AbortError' && !(error instanceof TimedOut));
});
