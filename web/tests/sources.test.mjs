// The event sources page without the page (src/lib/sources.ts): the checks of a new source, its body, the address and the example call
// shown with a secret, the body of a test event, and the words of a card.
import assert from 'node:assert/strict';
import test from 'node:test';

import { MAX_FIELDS, addressUrl, ago, checkFields, checkName, curlExample, describeFields, fireBody, showData, slugOf, sourceBody } from '../src/lib/sources.ts';

const row = (name, type = 'text') => ({ name, type });

test('a name is turned into the slug of its kind as the server does, and refused when empty or taken', () => {
  assert.equal(slugOf('Front door!'), 'front-door');
  assert.equal(slugOf('  Café   de la Gare '), 'cafe-de-la-gare');
  assert.equal(slugOf('日本'), '');
  assert.equal(slugOf('a'.repeat(40)), 'a'.repeat(30));
  assert.equal(slugOf(`${'a'.repeat(29)} b`), 'a'.repeat(29)); // cut where the dash would end it
  assert.equal(checkName('', []), 'Give it a name.');
  assert.match(checkName('!!', []), /letters or digits/);
  assert.match(checkName('Front door', ['custom.front-door']), /exists already/);
  assert.equal(checkName('Front door', ['custom.back-door', 'github.push']), '');
});

test('the fields are checked like the server does: the pattern, the names of the event itself, a name used twice', () => {
  assert.deepEqual(checkFields([row('state'), row('open', 'bool'), row('a_1')]), ['', '', '']);
  const found = checkFields([row(''), row('State'), row('1x'), row('a'.repeat(33)), row('title'), row('key'), row('data'), row('actor'), row('occurred_at'), row('ok'), row('ok')]);
  assert.match(found[0], /Name the field/);
  assert.match(found[1], /lower-case/);
  assert.match(found[2], /lower-case/);
  assert.match(found[3], /32 characters/);
  for (const at of [4, 5, 6, 7, 8]) assert.match(found[at], /name of the event itself/);
  assert.equal(found[9], ''); // the first of two is fine, the second says so
  assert.match(found[10], /already a field/);
  assert.equal(MAX_FIELDS, 12);
});

test('the body of a new source holds its name and its fields, never a trusted flag', () => {
  assert.deepEqual(sourceBody('  Front door ', [row('state'), row('open', 'bool')]), { name: 'Front door', fields: { state: 'text', open: 'bool' } });
  assert.deepEqual(sourceBody('Hub', []), { name: 'Hub', fields: {} });
  assert.ok(!('trusted' in sourceBody('Hub', [])));
});

test('the address is the origin of the page and the path of the server, joined by one slash', () => {
  assert.equal(addressUrl('https://clara.tail1234.ts.net', '/v1/events/kabc'), 'https://clara.tail1234.ts.net/v1/events/kabc');
  assert.equal(addressUrl('http://localhost:8800/', '/v1/events/kabc'), 'http://localhost:8800/v1/events/kabc');
  assert.equal(addressUrl('http://localhost:8800', 'v1/events/kabc'), 'http://localhost:8800/v1/events/kabc');
});

test('the example call has the address, a placeholder where the secret goes and the declared fields', () => {
  const call = curlExample('https://clara.example/v1/events/kabc', { state: 'text', open: 'bool' });
  assert.match(call, /^curl -X POST "https:\/\/clara\.example\/v1\/events\/kabc"/);
  assert.ok(call.includes('Authorization: Bearer <secret>'));
  assert.ok(call.includes('Content-Type: application/json'));
  const body = JSON.parse(call.match(/-d '(.*)'$/s)[1]);
  assert.deepEqual(body, { title: 'Something happened', data: { state: 'example', open: true } });
  // it takes no secret, so it can hold none: a source with no field has no data
  assert.equal(JSON.parse(curlExample('https://x/v1/events/k', {}).match(/-d '(.*)'$/s)[1]).data, undefined);
  assert.ok(!/evt_/.test(call));
});

test('a test event sends yes/no fields as true or false, leaves an empty text out and the title only when written', () => {
  const fields = { state: 'text', note: 'text', open: 'bool' };
  assert.deepEqual(fireBody(fields, { state: 'ajar', note: '  ', open: 'yes' }, ' Door ', 'web-1'), { key: 'web-1', title: 'Door', data: { state: 'ajar', open: true } });
  assert.deepEqual(fireBody(fields, { open: 'no' }, '', 'web-2'), { key: 'web-2', data: { open: false } });
  assert.deepEqual(fireBody({}, {}, '', 'web-3'), { key: 'web-3', data: {} });
});

test('the words of a card: its fields, the data of an event, and how long ago', () => {
  assert.deepEqual(describeFields({ state: 'text', open: 'bool' }), ['state (text)', 'open (yes/no)']);
  assert.deepEqual(showData({ state: 'open', open: true, night: false }), ['state: open', 'open: yes', 'night: no']);
  assert.equal(showData({ note: `a\n  b ${'x'.repeat(300)}` })[0].length, 'note: '.length + 200 + 1);
  const now = Date.parse('2026-11-13T12:00:00Z');
  assert.equal(ago('2026-11-13T11:59:30Z', now), 'just now');
  assert.equal(ago('2026-11-13T12:00:30Z', now), 'just now'); // a clock a little ahead
  assert.equal(ago('2026-11-13T11:55:00Z', now), '5 min ago');
  assert.equal(ago('2026-11-13T09:00:00Z', now), '3 h ago');
  assert.equal(ago('2026-11-10T12:00:00Z', now), '3 days ago');
});
