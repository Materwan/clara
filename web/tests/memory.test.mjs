// The note a delete says about the facts it removes (src/lib/memory.ts), run by Node on the file as it is: `npm test` in web/.
import assert from 'node:assert/strict';
import test from 'node:test';

import { keptNote } from '../src/lib/memory.ts';

test('a delete names every fact the memory keeps, live or not', () => {
  assert.equal(keptNote({ facts: 0, total: 0 }), '');
  assert.equal(keptNote({ facts: 1, total: 1 }), ' The 1 fact it remembers is deleted too.');
  assert.equal(keptNote({ facts: 3, total: 3 }), ' The 3 facts it remembers are deleted too.');
  assert.equal(keptNote({ facts: 3, total: 5 }), ' The 5 facts it keeps are deleted too: 3 live, 2 expired or replaced.');
  assert.equal(keptNote({ facts: 0, total: 1 }), ' The 1 fact it keeps is deleted too: 0 live, 1 expired or replaced.');
});
