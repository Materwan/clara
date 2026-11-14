// The Feeds tab without the tab (pages/Feeds.svelte): the check of what is pasted, an address as it is shown, the words of a feed's line,
// what an API error becomes and the body that follows a feed. The server checks everything again (api/feeds.py).
import { ago } from './sources';

export const MAX_ADDRESS = 2000; // the longest address the server takes
export const SHOWN = 56; // an address longer than this is cut in the middle in the list (the whole one is in its title)
export const NO_SCOPE = 'This token may not add or remove feeds: the server fetches the address you give. You can still read the list.';

export type Feed = {
  id: number; url: string; title: string; added_at: string; checked_at: string | null; error: string; today: number; watching_since: string | null;
};
export type Item = { title: string; link: string; published: string };
export type Found = { feed_url: string; title: string; items: Item[] };

/** Only a token that may answer everything looks at an address or changes the list (the others get a 403); any token may read the list. */
export const canChange = (scope: string): boolean => scope === 'all';

/** What is wrong with the pasted text, or '' (the server says the rest, in fixed sentences). */
export function checkAddress(text: string): string {
  const address = text.trim();
  if (!address) return 'Paste the address of a feed or of a page.';
  if (address.length > MAX_ADDRESS) return `An address of more than ${MAX_ADDRESS} characters is not one.`;
  return '';
}

/** `https://example.org/blog/feed/` as `example.org/blog/feed`; one longer than `max` keeps its start and its end (a channel id is at the end). */
export function shortAddress(url: string, max = SHOWN): string {
  const chars = [...url.trim().replace(/^https?:\/\//i, '').replace(/\/+$/, '')];
  if (chars.length <= max) return chars.join('');
  const tail = Math.floor((max - 1) / 2);
  return `${chars.slice(0, max - 1 - tail).join('')}…${chars.slice(chars.length - tail).join('')}`;
}

/** Two spellings of one address: the scheme, `www.`, the case of the host and a closing slash do not tell them apart. */
export function sameAddress(first: string, second: string): boolean {
  const plain = (url: string): string => {
    const bare = url.trim().replace(/^https?:\/\//i, '').replace(/^www\./i, '');
    const cut = bare.search(/[/?#]/);
    return (cut < 0 ? bare.toLowerCase() : bare.slice(0, cut).toLowerCase() + bare.slice(cut)).replace(/\/+$/, '');
  };
  return plain(first) === plain(second);
}

/** The label of the feed's own address on the preview: « Found at » when it is not the address that was pasted. */
export const addressLabel = (pasted: string, feedUrl: string): string => (sameAddress(pasted, feedUrl) ? 'Address' : 'Found at');

/** The name to show: the title it has, else its address. */
export const feedName = (feed: { title: string; url: string }): string => feed.title.trim() || shortAddress(feed.url);

/** The date of an item: « 5 min ago » within a week, else the day (« 2026-10-02 »), else « no date » (a date in the future is a day too). */
export function itemDate(iso: string, now = Date.now()): string {
  const time = Date.parse(iso);
  if (!iso || Number.isNaN(time)) return 'no date';
  const age = now - time;
  return age < 7 * 86_400_000 && age > -3_600_000 ? ago(iso, now) : new Date(time).toISOString().slice(0, 10);
}

/** The pieces of a feed's line: when it was looked at, what it made today, and that none waits for it (it is read only while one does). */
export function feedLine(feed: Pick<Feed, 'checked_at' | 'today' | 'watching_since'>, now = Date.now()): string[] {
  return [
    feed.checked_at ? `checked ${ago(feed.checked_at, now)}` : 'not checked yet',
    ...(feed.watching_since || feed.today ? [`${feed.today} new today`] : []),
    ...(feed.watching_since ? [] : ['waits for an automation']),
  ];
}

/** What to show for a failed call: the server's own sentence (it is fixed words), except for a token without the scope. */
export const feedError = (error: { status?: number; message: string }, doing: 'add' | 'remove' = 'add'): string =>
  error.status === 403 ? `This token may not ${doing} feeds.` : error.message;

/** The body of `POST /v1/feeds`: the address the feed was found at, never what was pasted (the page it came from may have moved). */
export const followBody = (found: Found): { url: string } => ({ url: found.feed_url });
