// The rules of the Modes page (src/lib/modes.ts).
import assert from 'node:assert/strict';
import test from 'node:test';

import { confirmQuery, decidedByDefault, deleteWarning, effectiveOf, exceptionsOf, ignoredFor, isShown, otherLines, ownLine, taintedAfter, taintedChoices, sameUses, toolsOf, usedBy } from '../src/lib/modes.ts';

test('a tainted turn is never more permissive than the normal one', () => {
  assert.deepEqual(taintedChoices('approve'), ['approve', 'ask', 'refuse']);
  assert.deepEqual(taintedChoices('ask'), ['ask', 'refuse']);
  assert.deepEqual(taintedChoices('refuse'), ['refuse']);
});

test('lowering the normal verdict takes the tainted one down with it, raising it leaves it', () => {
  assert.equal(taintedAfter('ask', 'approve'), 'ask');
  assert.equal(taintedAfter('refuse', 'ask'), 'refuse');
  assert.equal(taintedAfter('approve', 'ask'), 'ask');
  assert.equal(taintedAfter('ask', 'refuse'), 'refuse');
});

const lines = [
  { tool: 'mail_send', class: 'mail_send', target: '', verdict: 'ask', tainted: 'ask' },
  { tool: 'mail_send', class: 'mail_send', target: '*@epita.fr', verdict: 'approve', tainted: 'ask' },
  { tool: 'github_*', class: '', target: '', verdict: 'ask', tainted: 'refuse' },
];
const tools = [
  { name: 'mail_send', description: 'Send a mail', classes: ['mail_send'], pack: 'mail' },
  { name: 'ws_run', description: 'Run a script', classes: ['script', 'script_net'], pack: null },
];

test('lines are sorted into the row of their tool and class, their exceptions, and the others', () => {
  assert.equal(ownLine(lines, 'mail_send', 'mail_send')?.verdict, 'ask');
  assert.equal(ownLine(lines, 'ws_run', 'script'), undefined);
  assert.deepEqual(exceptionsOf(lines, 'mail_send', 'mail_send').map((line) => line.target), ['*@epita.fr']);
  assert.deepEqual(otherLines(lines, tools).map((line) => line.tool), ['github_*']);
});

test('a tool is listed under each of its classes, and the filter looks at name, description and classes', () => {
  assert.deepEqual(toolsOf(tools, 'script_net', '').map((tool) => tool.name), ['ws_run']);
  assert.deepEqual(toolsOf(tools, 'mail_send', 'SEND').map((tool) => tool.name), ['mail_send']);
  assert.deepEqual(toolsOf(tools, 'mail_send', 'script'), []);
});

test('the effective verdict is looked up by tool and class', () => {
  const rows = [{ tool: 'ws_run', class: 'script', verdict: 'ask', tainted: 'refuse', line: null, from: 'default' }];
  assert.equal(effectiveOf(rows, 'ws_run', 'script')?.from, 'default');
  assert.equal(effectiveOf(rows, 'ws_run', 'script_net'), undefined);
});

const packed = [
  { tool: 'mail_send', class: 'mail_send', verdict: 'ask', tainted: 'ask', line: null, from: 'default', pack: 'mail', shown: false },
  { tool: 'mail_read', class: 'mail_read', verdict: 'approve', tainted: 'ask', line: null, from: 'default', pack: 'mail', shown: false },
  { tool: 'ws_read', class: 'ws_read', verdict: 'approve', tainted: 'approve', line: null, from: 'default', pack: null, shown: true },
];

test('a tool whose pack is off leaves the table and is listed as decided by the default mode, with its pack', () => {
  assert.equal(isShown(packed, 'ws_read', 'ws_read'), true);
  assert.equal(isShown(packed, 'mail_send', 'mail_send'), false);
  assert.equal(isShown(packed, 'nothing', 'ws_read'), false);
  const label = (id) => (id === 'mail' ? 'Mail' : '');
  assert.deepEqual(decidedByDefault(packed, '', label).map((row) => row.tool), ['mail_send', 'mail_read']);
  assert.deepEqual(decidedByDefault(packed, 'MAIL', label).length, 2);
  assert.deepEqual(decidedByDefault(packed, 'send', label).map((row) => row.tool), ['mail_send']);
});

test('a line over several tools says which tools with a pack off it is ignored for', () => {
  const glob = { tool: 'mail_*', class: '', target: '', verdict: 'approve', tainted: 'approve' };
  assert.deepEqual(ignoredFor(glob, packed), ['mail_send', 'mail_read']);
  assert.deepEqual(ignoredFor({ ...glob, tool: '', class: 'mail_send' }, packed), ['mail_send']);
  assert.deepEqual(ignoredFor({ ...glob, tool: 'ws_*' }, packed), []);
});

test('the numbers of a deletion are shown, then sent back as they were', () => {
  const uses = { conversations: 3, automation_conversations: 1, automations: 2, projects: 1 };
  assert.equal(usedBy(uses), 'Used by 4 conversations, 1 project, 2 automations');
  assert.match(deleteWarning(uses), /^3 conversations, 1 automation conversation, 2 automations and 1 project use this mode/);
  assert.equal(confirmQuery(uses), 'conversations=3&automation_conversations=1&automations=2&projects=1');
});

test('a dictation client that uses the mode is shown and its count sent back; with none, the query is the old one', () => {
  const uses = { conversations: 0, automation_conversations: 0, automations: 0, projects: 0, dictation_clients: 2 };
  assert.equal(usedBy(uses), 'Used by 0 conversations, 0 projects, 0 automations, 2 dictation clients');
  assert.match(deleteWarning(uses), /0 automations, 2 dictation clients and 0 projects use this mode/);
  assert.equal(confirmQuery(uses), 'conversations=0&automation_conversations=0&automations=0&projects=0&dictation_clients=2');
  assert.equal(confirmQuery({ ...uses, dictation_clients: 0 }), 'conversations=0&automation_conversations=0&automations=0&projects=0');
  assert.match(deleteWarning({ ...uses, dictation_clients: 1 }), /1 dictation client and/);
});

test('uses are the same only when the dictation clients agree too (none counts as zero)', () => {
  const base = { conversations: 1, automation_conversations: 0, automations: 0, projects: 0 };
  assert.equal(sameUses(base, { ...base, dictation_clients: 0 }), true);
  assert.equal(sameUses(base, { ...base, dictation_clients: 1 }), false);
});
