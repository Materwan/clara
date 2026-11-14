// The Feeds tab without the tab (src/lib/feeds.ts): the check of the pasted address, an address as it is shown, the words of a feed's
// line, what an API error becomes, and the body that follows a feed (it must carry the address the feed was found at).
import assert from 'node:assert/strict';
import test from 'node:test';

import { addressLabel, canChange, checkAddress, feedError, feedLine, feedName, followBody, itemDate, sameAddress, shortAddress } from '../src/lib/feeds.ts';

const now = Date.parse('2026-11-13T12:00:00Z');

test('only a token that may answer everything adds or removes a feed', () => {
  assert.equal(canChange('all'), true);
  for (const scope of ['safe', 'none', '', 'ALL']) assert.equal(canChange(scope), false, scope);
});

test('an empty or enormous address is refused where it is typed; anything else is for the server to read', () => {
  assert.match(checkAddress('   '), /Paste the address/);
  assert.match(checkAddress(`https://x.test/${'a'.repeat(2000)}`), /2000 characters/);
  for (const text of ['https://example.org/feed.xml', ' example.org ', 'youtube.com/@someone', 'not an address']) assert.equal(checkAddress(text), '', text);
});

test('an address is shown without its scheme and closing slash, and a long one keeps its start and its end', () => {
  assert.equal(shortAddress('https://example.org/blog/feed/'), 'example.org/blog/feed');
  assert.equal(shortAddress('  http://example.org  '), 'example.org');
  const channel = 'https://www.youtube.com/feeds/videos.xml?channel_id=UCabcdefghijklmnopqrstuv';
  const shown = shortAddress(channel);
  assert.equal([...shown].length, 56);
  assert.ok(shown.startsWith('www.youtube.com/feeds/'));
  assert.ok(shown.endsWith('UCabcdefghijklmnopqrstuv'), shown); // the channel id is the part that tells feeds apart
  assert.ok(shown.includes('…'));
  assert.equal(shortAddress('a'.repeat(60), 10), `${'a'.repeat(5)}…${'a'.repeat(4)}`);
});

test('two spellings of one address are one; a page and its feed are not', () => {
  assert.equal(sameAddress('https://www.Example.org/feed/', 'example.org/feed'), true);
  assert.equal(sameAddress('http://example.org/feed.xml', 'https://example.org/feed.xml'), true);
  assert.equal(sameAddress('example.org/Feed', 'example.org/feed'), false); // a path is case-sensitive
  assert.equal(sameAddress('example.org', 'https://example.org/feed.xml'), false);
  assert.equal(addressLabel('https://example.org/feed.xml', 'https://example.org/feed.xml'), 'Address');
  assert.equal(addressLabel('https://www.youtube.com/@someone', 'https://www.youtube.com/feeds/videos.xml?channel_id=UCx'), 'Found at');
});

test('a feed is named by its title, else by its address', () => {
  assert.equal(feedName({ title: ' Some blog ', url: 'https://example.org/feed' }), 'Some blog');
  assert.equal(feedName({ title: '  ', url: 'https://example.org/feed/' }), 'example.org/feed');
});

test('the date of an item: relative within a week, the day after that, « no date » when there is none', () => {
  assert.equal(itemDate('2026-11-13T11:55:00Z', now), '5 min ago');
  assert.equal(itemDate('2026-11-10T12:00:00Z', now), '3 days ago');
  assert.equal(itemDate('2026-10-02T08:30:00Z', now), '2026-10-02');
  assert.equal(itemDate('2026-12-25T00:00:00Z', now), '2026-12-25'); // scheduled for later: the day, not « just now »
  assert.equal(itemDate('2026-11-13T12:20:00Z', now), 'just now'); // a clock a little ahead
  assert.equal(itemDate('', now), 'no date');
  assert.equal(itemDate('not a date', now), 'no date');
});

test('the line of a feed: when it was looked at, what it made today, and that no automation waits for it', () => {
  const base = { checked_at: '2026-11-13T11:48:00Z', today: 2, watching_since: '2026-11-12T08:00:00Z' };
  assert.deepEqual(feedLine(base, now), ['checked 12 min ago', '2 new today']);
  assert.deepEqual(feedLine({ ...base, checked_at: null, today: 0 }, now), ['not checked yet', '0 new today']);
  assert.deepEqual(feedLine({ ...base, watching_since: null, today: 0 }, now), ['checked 12 min ago', 'waits for an automation']);
  assert.deepEqual(feedLine({ checked_at: null, today: 0, watching_since: null }, now), ['not checked yet', 'waits for an automation']);
  assert.deepEqual(feedLine({ ...base, watching_since: null }, now), ['checked 12 min ago', '2 new today', 'waits for an automation']);
});

test('an error is the server\'s own sentence, but a 403 says what the token may not do', () => {
  assert.equal(feedError({ status: 422, message: 'No feed was found at this address.' }), 'No feed was found at this address.');
  assert.equal(feedError({ status: 409, message: 'You follow this feed already.' }), 'You follow this feed already.');
  assert.equal(feedError({ status: 0, message: 'Clara cannot be reached: check the connection and try again.' }), 'Clara cannot be reached: check the connection and try again.');
  assert.equal(feedError({ status: 403, message: 'This token may not look at or change the feeds: the server fetches the address you give.' }), 'This token may not add feeds.');
  assert.equal(feedError({ status: 403, message: 'x' }, 'remove'), 'This token may not remove feeds.');
  assert.equal(feedError(new Error('boom')), 'boom'); // not an API error: no status
});

test('following sends the address the feed was found at, not what was pasted', () => {
  const found = { feed_url: 'https://www.youtube.com/feeds/videos.xml?channel_id=UCx', title: 'A channel', items: [] };
  assert.deepEqual(followBody(found), { url: 'https://www.youtube.com/feeds/videos.xml?channel_id=UCx' });
});
