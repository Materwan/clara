// How a conversation is cut into exchanges for drawing a few at a time (src/lib/turns.ts), run by Node on the file as it is.
import assert from 'node:assert/strict';
import test from 'node:test';

import { splitTurns } from '../src/lib/turns.ts';

const rows = [
  { id: 1, role: 'user' },
  { id: 2, role: 'assistant' },
  { id: 3, role: 'tool' },
  { id: 4, role: 'assistant' },
  { id: 5, role: 'user' },
  { id: 6, role: 'assistant' },
  { id: 7, role: 'user' },
];

test('each question starts an exchange, and its replies and tool results stay with it', () => {
  const turns = splitTurns(rows).map((turn) => turn.map((row) => row.id));
  assert.deepEqual(turns, [[1, 2, 3, 4], [5, 6], [7]]);
});

test('taking the exchanges back in order gives the history as it was: nothing lost, nothing twice', () => {
  assert.deepEqual(splitTurns(rows).flat(), rows);
});

test('replies before any question form a first exchange of their own', () => {
  const turns = splitTurns([{ id: 1, role: 'assistant' }, { id: 2, role: 'user' }]).map((turn) => turn.map((row) => row.id));
  assert.deepEqual(turns, [[1], [2]]);
});

test('an empty history has no exchanges', () => {
  assert.deepEqual(splitTurns([]), []);
});

const ran = { role: 'user', shown: 'Clara ran /review-pr 12' };

test('the instructions of a skill Clara ran, between her tool result and her answer, are no exchange: the answer stays with the real question', () => {
  const history = [
    { id: 1, role: 'user', shown: '/review-pr 12' }, // a command you typed is a question
    { id: 2, role: 'assistant' }, // her call of skill_run
    { id: 3, role: 'tool' },
    { id: 4, ...ran },
    { id: 5, role: 'assistant' },
    { id: 6, role: 'user' },
    { id: 7, role: 'assistant' },
  ];
  assert.deepEqual(splitTurns(history).map((turn) => turn.map((row) => row.id)), [[1, 2, 3, 5], [6, 7]]);
  assert.deepEqual(splitTurns([...history, { id: 8, role: 'tool' }, { id: 9, ...ran }, { id: 10, role: 'assistant' }]).at(-1)?.map((row) => row.id), [6, 7, 8, 10]);
});

test('a skill row that opens the history, or follows another one, starts nothing', () => {
  assert.deepEqual(splitTurns([{ id: 1, ...ran }, { id: 2, role: 'assistant' }]).map((turn) => turn.map((row) => row.id)), [[2]]);
  const twice = [{ id: 1, role: 'user' }, { id: 2, ...ran }, { id: 3, ...ran }, { id: 4, role: 'assistant' }];
  assert.deepEqual(splitTurns(twice).map((turn) => turn.map((row) => row.id)), [[1, 4]]);
});
