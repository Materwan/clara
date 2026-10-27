// The browser side of the folder tools (src/lib/folders.ts), run by Node on the file as it is: `npm test` in web/.
import assert from 'node:assert/strict';
import test from 'node:test';

import { answerFolderCall, globMatch, isHiddenPath, useWorker } from '../src/lib/folders.ts';

function dir(kids) {
  return {
    kind: 'directory',
    async *entries() {
      for (const entry of Object.entries(kids)) yield entry;
    },
    async getDirectoryHandle(name) {
      if (kids[name]?.kind === 'directory') return kids[name];
      throw new DOMException('no such folder', 'NotFoundError');
    },
    async getFileHandle(name) {
      if (kids[name]?.kind === 'file') return kids[name];
      throw new DOMException('no such file', 'NotFoundError');
    },
  };
}

function file(text) {
  const bytes = new TextEncoder().encode(text);
  return { kind: 'file', async getFile() { return { size: bytes.length, async arrayBuffer() { return bytes.buffer; }, async text() { return text; } }; } };
}

const root = () => dir({
  'notes.txt': file('hello SECRET_TOKEN'),
  '.env': file('API=SECRET_TOKEN'),
  '.ssh': dir({ id_rsa: file('-----BEGIN KEY----- SECRET_TOKEN') }),
  src: dir({ 'a.txt': file('one\nSECRET_TOKEN two\nthree') }),
});

const ask = async (name, args) => JSON.parse(await answerFolderCall(root(), name, args));

test('a pattern with * and ? matches a name without a regular expression', () => {
  assert.ok(globMatch('*.pdf', 'Invoice.PDF'));
  assert.ok(globMatch('inv?ice*', 'invoice-2026.txt'));
  assert.ok(!globMatch('*.pdf', 'notes.txt'));
  assert.ok(globMatch('*', ''));
  const began = Date.now();
  assert.ok(!globMatch('*a*a*a*a*a*a*a*a*a*b', 'a'.repeat(250)));
  assert.ok(Date.now() - began < 500, 'a pattern with many stars must not hang');
});

test('a dot name anywhere in a path makes it hidden', () => {
  assert.ok(isHiddenPath('.ssh/id_rsa'));
  assert.ok(isHiddenPath('a/.git/config'));
  assert.ok(!isHiddenPath('a/b/c.txt'));
});

test('a listing marks what is inside a hidden folder as hidden, and names none of it as clear', async () => {
  const inside = await ask('folder_list', { path: '.ssh' });
  assert.match(inside.text, /\.ssh\/id_rsa {2}\[hidden\]/);
  assert.deepEqual(inside.clear, []);
  const top = await ask('folder_list', {});
  assert.deepEqual(top.clear, ['notes.txt']); // not .env, not the folders
});

test('a search from the top skips hidden files; started inside a hidden folder it reads them, and names none as clear', async () => {
  const top = await ask('folder_grep', { pattern: 'SECRET_TOKEN' });
  assert.deepEqual(top.clear.sort(), ['notes.txt', 'src/a.txt']);
  assert.ok(!top.text.includes('id_rsa') && !top.text.includes('.env'));
  const inside = await ask('folder_grep', { pattern: 'SECRET_TOKEN', path: '.ssh' });
  assert.match(inside.text, /\.ssh\/id_rsa:1:/);
  assert.deepEqual(inside.clear, []);
  const found = await ask('folder_find', { pattern: '*', path: '.ssh' });
  assert.deepEqual(found.clear, []);
});

test('a regular expression runs in a worker and its answer is read', async () => {
  const made = [];
  useWorker(() => {
    const worker = {
      onmessage: null,
      onerror: null,
      terminated: false,
      postMessage(job) {
        const re = new RegExp(job.pattern, 'i');
        const found = [];
        job.lines.forEach((line, index) => { if (re.test(line) && found.length < job.room) found.push(index); });
        queueMicrotask(() => worker.onmessage({ data: found }));
      },
      terminate() { worker.terminated = true; },
    };
    made.push(worker);
    return worker;
  });
  const answer = await ask('folder_grep', { pattern: 'secret_\\w+ two', regex: true, path: 'src' });
  assert.match(answer.text, /src\/a\.txt:2: SECRET_TOKEN two/);
  assert.equal(made.length, 1);
  assert.ok(made[0].terminated, 'the worker is ended with the search');
});

test('a regular expression that never finishes ends its worker and says so', async () => {
  const made = [];
  useWorker(() => {
    const worker = { onmessage: null, onerror: null, terminated: false, postMessage() {}, terminate() { worker.terminated = true; } };
    made.push(worker);
    return worker;
  }, 50);
  const answer = await ask('folder_grep', { pattern: '(a+)+$', regex: true, path: 'src' });
  assert.match(answer.error, /took more than .* on src\/a\.txt/);
  assert.ok(made[0].terminated);
});

test('a mistake in a regular expression is told before anything runs', async () => {
  const answer = await ask('folder_grep', { pattern: '(', regex: true });
  assert.match(answer.error, /not a regular expression/);
});
