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
