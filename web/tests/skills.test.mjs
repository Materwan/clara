// The rules of the skills pages and of the "/" menu and how a command is drawn (src/lib/skills.ts).
import assert from 'node:assert/strict';
import test from 'node:test';

import { repack } from '../src/lib/documents.ts';
import {
  MAX_ZIP, addBody, cap, chipsFor, commandOf, drawn, filterSkills, held, insertSkill, isCommand, isRan, menuKey, mirrorOf, previewSource, resendOf, scopeLabel, scopeProblem,
  slashWord, typedCommand, zipProblem,
} from '../src/lib/skills.ts';

const skills = [
  { name: 'weekly-report', description: 'Summarise the week' },
  { name: 'review-pr', description: 'Review a pull request, with security in mind' },
  { name: 'review', description: 'Review a file' },
  { name: 'pr-notes', description: 'Notes for a PR' },
  { name: 'translate', description: 'Translate; review the result' },
];

test('the slash word is the first word of a text that starts with a slash, while the caret is in it', () => {
  assert.deepEqual(slashWord('/rev', 4), { query: 'rev', end: 4 });
  assert.deepEqual(slashWord('/rev', 1), { query: 'rev', end: 4 });
  assert.deepEqual(slashWord('/', 1), { query: '', end: 1 });
  assert.deepEqual(slashWord('/rev 12 security', 2), { query: 'rev', end: 4 });
  assert.deepEqual(slashWord('/rev\nmore', 4), { query: 'rev', end: 4 });
  assert.equal(slashWord('/rev 12', 5), null); // the caret is in the arguments
  assert.equal(slashWord('/rev 12', 0), null); // before the slash
  assert.equal(slashWord('hello /rev', 10), null); // does not start with a slash
  assert.equal(slashWord(' /rev', 5), null);
  assert.equal(slashWord('', 0), null);
});

test('the menu lists prefix matches first, then names that contain the word, then descriptions, each in name order', () => {
  assert.deepEqual(filterSkills(skills, 'rev').map((s) => s.name), ['review', 'review-pr', 'translate']);
  assert.deepEqual(filterSkills(skills, 'pr').map((s) => s.name), ['pr-notes', 'review-pr', 'weekly-report']); // the last holds p then r
  assert.deepEqual(filterSkills(skills, 'REV').map((s) => s.name), ['review', 'review-pr', 'translate']);
  assert.deepEqual(filterSkills(skills, '').map((s) => s.name), ['pr-notes', 'review', 'review-pr', 'translate', 'weekly-report']);
  assert.deepEqual(filterSkills(skills, 'nothing'), []);
  assert.equal(filterSkills(skills, 'review')[0].name, 'review'); // the exact name leads
});

test('the letters of a word in order find a name, after the other groups, and a typing mistake finds nothing', () => {
  const commands = [
    { name: 'add-dir', description: 'Add a folder' },
    { name: 'goal', description: 'Keep working until a condition is met' },
    { name: 'review', description: 'Look at a file' },
    { name: 'dir-size', description: 'The addir total' },
  ];
  assert.deepEqual(filterSkills(commands, 'addir').map((c) => c.name), ['dir-size', 'add-dir']); // a description first, then the letters in order
  assert.deepEqual(filterSkills(commands, 'rvw').map((c) => c.name), ['review']);
  assert.deepEqual(filterSkills(commands, 'gl').map((c) => c.name), ['goal']);
  assert.deepEqual(filterSkills(commands, 'dir').map((c) => c.name), ['dir-size', 'add-dir']); // starts with, then contains: unchanged
  assert.deepEqual(filterSkills(commands, 'gaol'), []); // no correction of typing mistakes
  assert.deepEqual(filterSkills(commands, 'ZZ'), []);
});

test('the keys: arrows wrap, Tab and Enter pick, Escape closes, Enter sends when the word is already the name', () => {
  const names = ['review', 'review-pr'];
  assert.deepEqual(menuKey('ArrowDown', false, names, 0, 'rev'), { to: 1 });
  assert.deepEqual(menuKey('ArrowDown', false, names, 1, 'rev'), { to: 0 });
  assert.deepEqual(menuKey('ArrowUp', false, names, 0, 'rev'), { to: 1 });
  assert.deepEqual(menuKey('Enter', false, names, 1, 'rev'), { pick: 1 });
  assert.deepEqual(menuKey('Tab', false, names, 0, 'rev'), { pick: 0 });
  assert.deepEqual(menuKey('Escape', false, names, 0, 'rev'), { close: true });
  assert.equal(menuKey('Enter', false, names, 0, 'review'), null); // already typed: Enter sends as it does today
  assert.deepEqual(menuKey('Tab', false, names, 0, 'review'), { pick: 0 }); // Tab still adds the space
  assert.equal(menuKey('Enter', true, names, 0, 'rev'), null); // Shift+Enter is a new line
  assert.equal(menuKey('Tab', true, names, 0, 'rev'), null);
  assert.equal(menuKey('a', false, names, 0, 'rev'), null);
  assert.equal(menuKey('Enter', false, [], 0, 'zzz'), null); // no row: nothing is taken from the box
});

test('choosing a skill puts /name and a space in the first word and keeps the arguments', () => {
  assert.deepEqual(insertSkill('/rev', 'review-pr'), { text: '/review-pr ', caret: 11 });
  assert.deepEqual(insertSkill('/', 'review'), { text: '/review ', caret: 8 });
  assert.deepEqual(insertSkill('/rev 12 security', 'review-pr'), { text: '/review-pr 12 security', caret: 11 });
  assert.deepEqual(insertSkill('/rev\nmore', 'review-pr'), { text: '/review-pr\nmore', caret: 11 });
});

test('a command is the first word when it is exactly a name, followed by a space, a line break or the end', () => {
  assert.equal(commandOf('/review-pr', skills)?.name, 'review-pr');
  assert.equal(commandOf('/review-pr 12 security', skills)?.name, 'review-pr');
  assert.equal(commandOf('/review-pr\n12', skills)?.name, 'review-pr');
  assert.equal(commandOf('/review-p', skills), null);
  assert.equal(commandOf('/Review-pr', skills), null);
  assert.equal(commandOf('/', skills), null);
  assert.equal(commandOf('say /review', skills), null);
  assert.equal(commandOf('/unknown 1', skills), null);
});

const commands = [
  { name: 'goal', kind: 'builtin', argument_hint: '[--turns N] <condition> | clear', description: 'Keep working until a condition is met' },
  { name: 'review-pr', kind: 'skill', argument_hint: '[pull-request-number]', description: 'Review a pull request' },
  { name: 'summary', kind: 'skill', argument_hint: '', description: 'Summarise' },
];

test('the box draws the name of a command, and its arguments faded while only one space follows the name', () => {
  assert.deepEqual(mirrorOf('/goal ', commands), { name: '/goal', rest: ' ', faded: '[--turns N] <condition> | clear' });
  assert.deepEqual(mirrorOf('/review-pr ', commands), { name: '/review-pr', rest: ' ', faded: '[pull-request-number]' });
  assert.deepEqual(mirrorOf('/review-pr', commands), { name: '/review-pr', rest: '', faded: '' }); // no space yet: nothing is faded
  assert.equal(mirrorOf('/review-pr  ', commands)?.faded, ''); // the first letter, or anything else, typed after the name removes them
  assert.equal(mirrorOf('/review-pr 1', commands)?.faded, '');
  assert.equal(mirrorOf('/review-pr\n', commands)?.faded, '');
  assert.deepEqual(mirrorOf('/review-pr 12 security', commands), { name: '/review-pr', rest: ' 12 security', faded: '' });
  assert.deepEqual(mirrorOf('/summary ', commands), { name: '/summary', rest: ' ', faded: '' }); // a command that expects nothing fades nothing
});

test('a word that is no command is plain: no colour, no faded arguments', () => {
  assert.equal(mirrorOf('/review-p ', commands), null); // a name not yet complete
  assert.equal(mirrorOf('/unknown ', commands), null);
  assert.equal(mirrorOf('/Goal ', commands), null);
  assert.equal(mirrorOf('say /goal ', commands), null);
  assert.equal(mirrorOf(' /goal ', commands), null);
  assert.equal(mirrorOf('/', commands), null);
  assert.equal(mirrorOf('', commands), null);
  assert.equal(mirrorOf('/goal ', []), null); // the list is not read yet
});

test('a sent command is drawn with its name wrapped, and the rest of the text as it was', () => {
  assert.equal(drawn('/review-pr 12 security'), '<span class="command-name">/review-pr</span> 12 security');
  assert.equal(drawn('/summary'), '<span class="command-name">/summary</span>');
  assert.equal(drawn('/review-pr\nmore'), '<span class="command-name">/review-pr</span>\nmore');
  assert.equal(drawn('/review-pr<b>'), '/review-pr<b>'); // not a name: nothing is wrapped, so nothing is made of the text
  assert.equal(drawn('say /review-pr'), 'say /review-pr');
});

test('a pack is on when the server says so or when you hold it here; a conversation not started goes by what you hold', () => {
  const packs = [
    { id: 'mail', label: 'Mail', on: false, available: true, reason: '' },
    { id: 'web', label: 'Web search', on: true, available: true, reason: '' },
    { id: 'github', label: 'GitHub', on: false, available: false, reason: 'No GitHub account is connected.' },
  ];
  assert.deepEqual(chipsFor(packs, [], true).map((p) => p.on), [false, true, false]);
  assert.deepEqual(chipsFor(packs, ['mail'], true).map((p) => p.on), [true, true, false]); // just switched on, before the status is read again
  assert.deepEqual(chipsFor(packs, ['mail'], false).map((p) => p.on), [true, false, false]); // not started: the server's `on` is only the default
  assert.equal(chipsFor(packs, [], true)[2].reason, 'No GitHub account is connected.');
});

test('where a skill is kept, in words', () => {
  const projects = [{ id: 1, name: 'Thesis' }, { id: 2, name: 'Garden' }, { id: 3, name: 'A project with a long name indeed' }];
  assert.equal(scopeLabel({ scope: 'global', projects: [] }, projects), 'Everywhere');
  assert.equal(scopeLabel({ scope: 'projects', projects: [1] }, projects), '1 project: Thesis');
  assert.equal(scopeLabel({ scope: 'projects', projects: [1, 2] }, projects), '2 projects: Thesis, Garden');
  assert.equal(scopeLabel({ scope: 'projects', projects: [1, 2, 3] }, projects), '3 projects'); // too long for names
  assert.equal(scopeLabel({ scope: 'projects', projects: [9] }, projects), '1 project: #9');
  assert.match(scopeLabel({ scope: 'projects', projects: [] }, projects), /visible nowhere/);
});

test('only a shown that starts with a slash is drawn in place of the question', () => {
  assert.equal(isCommand('/review-pr 12'), true);
  assert.equal(isCommand('Automation: weekly'), false);
  assert.equal(isCommand(''), false);
  assert.equal(isCommand(undefined), false);
});

test('the instructions of a skill Clara ran are told by their shown, and a command you typed is not one', () => {
  assert.equal(isRan('Clara ran /review-pr 12 security'), true);
  assert.equal(isRan('Clara ran /weekly-report'), true); // no arguments
  assert.equal(isRan('/review-pr 1'), false); // typed by you: drawn as you typed it
  assert.equal(isCommand('Clara ran /review-pr 1'), false); // and it is no command of yours either
  assert.equal(isRan('Clara ran a script'), false); // the slash is part of the prefix
  assert.equal(isRan('Automation: weekly'), false);
  assert.equal(isRan(''), false);
  assert.equal(isRan(undefined), false);
});

test('a stored command is drawn as typed, its files as chips, and sent again as typed', () => {
  const plain = typedCommand('/review-pr 12 security', 'Review the pull request 12.\n\nArguments: 12 security');
  assert.deepEqual(plain, { text: '/review-pr 12 security', documents: [] });
  const block = '<document name="a.txt" type="text/plain">\nhello\n</document>';
  const withFile = typedCommand('/review-pr 12\n\n(Attached: a.txt)', `Review 12.\n\n(Attached: a.txt)\n\n${block}`);
  assert.equal(withFile.text, '/review-pr 12');
  assert.deepEqual(withFile.documents.map((d) => d.name), ['a.txt']);
  // asked again: the command and its documents, never the instructions
  const message = { command: true, shown: withFile.text, content: 'Review 12.\n\n(Attached: a.txt)\n\n' + block, documents: withFile.documents };
  assert.equal(resendOf(message), repack('/review-pr 12', withFile.documents));
  assert.equal(resendOf({ command: true, shown: plain.text, content: 'The instructions', documents: [] }), '/review-pr 12 security');
  assert.equal(resendOf({ shown: 'hello', content: 'hello', documents: [] }), 'hello'); // not a command: as stored
  // sent again with its documents inside the text: they are chips again, not text in the bubble
  const again = typedCommand(repack('/review-pr 12', withFile.documents), 'ignored');
  assert.equal(again.text, '/review-pr 12');
  assert.deepEqual(again.documents.map((d) => d.name), ['a.txt']);
});

test('what is previewed is what is sent to be added', () => {
  assert.deepEqual(previewSource('paste', '---\nname: a\n---\nDo it', 'ignored', ' '), { skill_md: '---\nname: a\n---\nDo it' });
  assert.deepEqual(previewSource('paste', 'text', '', ' my-skill '), { skill_md: 'text', name: 'my-skill' });
  assert.deepEqual(previewSource('zip', 'ignored', 'UEsDB', ''), { zip_base64: 'UEsDB' });
  const source = previewSource('zip', '', 'UEsDB', 'x');
  assert.deepEqual(addBody(source, { kind: 'global' }, false), { zip_base64: 'UEsDB', name: 'x', scope: 'global', replace: false });
  assert.deepEqual(addBody(source, { kind: 'projects', ids: [3, 5] }, true), { zip_base64: 'UEsDB', name: 'x', scope: { projects: [3, 5] }, replace: true });
});

test('a scope that cannot be added says why', () => {
  assert.equal(scopeProblem({ kind: 'global' }), '');
  assert.equal(scopeProblem({ kind: 'projects', ids: [1] }), '');
  assert.equal(scopeProblem({ kind: 'projects', ids: [] }), 'Choose at least one project.');
  assert.match(scopeProblem({ kind: 'projects', ids: Array.from({ length: 51 }, (_, i) => i) }), /50 projects at most/);
});

test('a zip over the server\'s limit is refused in the server\'s words', () => {
  assert.equal(MAX_ZIP, 1_400_000);
  assert.equal(zipProblem(1_400_000), '');
  assert.equal(zipProblem(1_400_001), 'The archive is 1,400,001 bytes: at most 1,400,000.');
});

test('the text of a file is drawn up to a cap, and the rest is counted', () => {
  assert.deepEqual(cap('abc', 5), { text: 'abc', hidden: 0 });
  assert.deepEqual(cap('abcdefgh', 5), { text: 'abcde', hidden: 3 });
  assert.equal(held(12), '12 of 30 skills');
});
