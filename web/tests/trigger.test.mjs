// The trigger form (src/lib/trigger.ts): the `when` tree it builds, what it reads back, what it refuses, the answers to a missed time.
import assert from 'node:assert/strict';
import test from 'node:test';

import { MAX_NODES, TWO_EVENTS, catchUpFor, catchUpOptions, check, fromWhen, hasEvent, newEvent, newGroup, newTime, toWhen } from '../src/lib/trigger.ts';

const kinds = [{ kind: 'github.push', fields: { branch: 'text', repo: 'text', forced: 'bool' } }];
const push = (filters = []) => ({ ...newEvent('github.push'), filters });
const text = (field, ...conds) => ({ field, bool: false, flag: 'yes', conds });

test('a branch of each kind becomes a node of the tree; a cron is an instant at the top and a condition in a group', () => {
  const group = { type: 'group', of: [{ type: 'window', from: '18:00', to: '20:00', days: [4, 0] }, push([text('branch', { pattern: '^main$', not: false, caseSensitive: false })])] };
  const tree = toWhen([group, { type: 'cron', expr: ' 0 9 * * * ' }, { type: 'once', at: '2026-12-01T09:00' }]);
  assert.deepEqual(tree, {
    type: 'or',
    of: [
      { type: 'and', of: [{ type: 'window', from: '18:00', to: '20:00', days: [0, 4] }, { type: 'event', kind: 'github.push', filter: { branch: { pattern: '^main$' } } }] },
      { type: 'cron', expr: '0 9 * * *', as: 'instant' },
      { type: 'once', at: '2026-12-01T09:00' },
    ],
  });
  const inGroup = toWhen([{ type: 'group', of: [push(), { type: 'cron', expr: '* 18 * * *' }] }]);
  assert.equal(inGroup.of[0].of[1].as, 'condition');
});

test('a filter keeps the ticks that are on, several conditions on a field, a yes or no, and drops empty rows', () => {
  const rows = [
    text('branch', { pattern: 'a', not: true, caseSensitive: true }, { pattern: 'b', not: false, caseSensitive: false }),
    text('repo', { pattern: '  ', not: false, caseSensitive: false }),
    { field: 'forced', bool: true, flag: 'no', conds: [] },
  ];
  assert.deepEqual(toWhen([push(rows)]).of[0].filter, {
    branch: [{ pattern: 'a', not: true, case_sensitive: true }, { pattern: 'b' }],
    forced: { eq: false },
  });
});

test('a stored tree is read back into branches, and what is written is read the same', () => {
  const tree = toWhen([{ type: 'group', of: [push([text('branch', { pattern: 'x', not: true, caseSensitive: false })]), newTime('window')] }, { type: 'cron', expr: '0 9 * * *' }]);
  const back = fromWhen(tree);
  assert.deepEqual(toWhen(back.branches), tree);
  assert.equal(fromWhen({ type: 'event', kind: 'github.push', filter: {} }).branches.length, 1);
});

test('a nested Or is flattened, a shape the editor cannot draw is said so', () => {
  const nested = { type: 'or', of: [{ type: 'or', of: [{ type: 'cron', expr: '0 9 * * *', as: 'instant' }] }, { type: 'once', at: '2026-12-01T09:00' }] };
  assert.equal(fromWhen(nested).branches.length, 2);
  const deep = { type: 'or', of: [{ type: 'and', of: [{ type: 'event', kind: 'github.push', filter: {} }, { type: 'or', of: [{ type: 'once', at: '2026-12-01T09:00' }] }] }] };
  assert.match(fromWhen(deep).unsupported, /does not draw/);
});

test('a group with two events is refused with the sentence of the form; one with none too', () => {
  const two = check([{ type: 'group', of: [push(), push()] }], kinds);
  assert.deepEqual(two.map((item) => item.message), [TWO_EVENTS]);
  assert.equal(two[0].id, 'tr-b0-group');
  assert.match(check([{ type: 'group', of: [{ ...newTime('window') }] }], kinds)[0].message, /needs one event/);
  assert.deepEqual(check([{ type: 'group', of: [push(), newTime('window')] }], kinds), []);
});

test('a window alone, an empty field, a window that ends where it starts and a missing branch are refused where they are', () => {
  assert.equal(check([], kinds)[0].id, 'tr-add');
  assert.equal(check([newTime('window')], kinds)[0].id, 'tr-b0-from');
  assert.equal(check([newTime('once')], kinds)[0].id, 'tr-b0-at');
  assert.equal(check([newTime('cron')], kinds)[0].id, 'tr-b0-expr');
  assert.equal(check([newEvent()], kinds)[0].id, 'tr-b0-kind');
  assert.equal(check([{ type: 'group', of: [push(), { type: 'window', from: '10:00', to: '10:00', days: [1] }] }], kinds)[0].id, 'tr-b0-n1-to');
  assert.equal(check([push([text('branch', { pattern: 'x'.repeat(501), not: false, caseSensitive: false })])], kinds)[0].id, 'tr-b0-f0-0');
  assert.deepEqual(check([push(), { type: 'cron', expr: '0 9 * * *' }], kinds), []);
});

test('the limits of nodes and of conditions on a field', () => {
  const many = Array.from({ length: MAX_NODES }, () => push());
  assert.match(check(many, kinds).at(-1).message, /nodes/);
  const six = Array.from({ length: 6 }, () => ({ pattern: 'a', not: false, caseSensitive: false }));
  assert.match(check([push([text('branch', ...six)])], kinds)[0].message, /At most 5 conditions/);
});

test('a missed time offers digest only when the trigger has an event', () => {
  const timeOnly = [newTime('cron')];
  const withEvent = [newGroup()];
  assert.equal(hasEvent(timeOnly), false);
  assert.equal(hasEvent(withEvent), true);
  assert.deepEqual(catchUpOptions(timeOnly, false).map((item) => item.value), ['all', 'latest', 'skip']);
  assert.deepEqual(catchUpOptions(withEvent, true).map((item) => item.value), ['', 'all', 'latest', 'digest', 'skip']);
  assert.equal(catchUpFor('digest', timeOnly, true), '');
  assert.equal(catchUpFor('digest', timeOnly, false), 'all');
  assert.equal(catchUpFor('digest', withEvent, false), 'digest');
});
