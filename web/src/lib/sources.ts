// The event sources page, without the page: the checks of a new source (the server's rules, said where the mistake is), the body it
// sends, the address and the curl example shown with a new secret, the body of a test event, and a few words for a card.
// The server checks everything again (automations/sources.py); this only puts the message next to the field.

export const MAX_FIELDS = 12;
export const MAX_NAME = 30; // the slug of the name: the part of the kind after `custom.`
export const FIELD_NAME = /^[a-z][a-z0-9_]{0,31}$/;
export const RESERVED = ['actor', 'data', 'key', 'occurred_at', 'title']; // the names of the event itself: a field may not take one

export type FieldType = 'text' | 'bool';
export type FieldRow = { name: string; type: FieldType };

/** `Front door!` is `front-door`, as the server makes it: the part of the kind after `custom.`. */
export function slugOf(name: string): string {
  const plain = name.normalize('NFKD').replace(/[^\x00-\x7f]/g, '').toLowerCase();
  return (plain.match(/[a-z0-9]+/g) ?? []).join('-').slice(0, MAX_NAME).replace(/^-+|-+$/g, '');
}

/** What is wrong with a name, or '' (`taken`: the kinds that exist). */
export function checkName(name: string, taken: string[]): string {
  if (!name.trim()) return 'Give it a name.';
  const slug = slugOf(name);
  if (!slug) return 'A name needs letters or digits in it.';
  if (taken.includes(`custom.${slug}`)) return `A source named ${slug} exists already.`;
  return '';
}

/** One message per row ('' when it is fine): the pattern, the names of the event itself, and a name used twice. */
export function checkFields(rows: FieldRow[]): string[] {
  return rows.map((row, index) => {
    const name = row.name;
    if (!name) return 'Name the field, or remove the row.';
    if (!FIELD_NAME.test(name)) return 'A field name starts with a lower-case letter, then lower-case letters, digits or _ (32 characters at most).';
    if (RESERVED.includes(name)) return `${name} is a name of the event itself (${RESERVED.join(', ')}): pick another.`;
    if (rows.findIndex((other) => other.name === name) !== index) return `${name} is already a field of this source.`;
    return '';
  });
}

/** The body of `POST /v1/event-sources`. A source is never made trusted here: that is a box on its card, with a question. */
export function sourceBody(name: string, rows: FieldRow[]): { name: string; fields: Record<string, FieldType> } {
  return { name: name.trim(), fields: Object.fromEntries(rows.map((row) => [row.name, row.type])) };
}

/** The address a sender calls: the origin of this page and the path the server gives. */
export const addressUrl = (origin: string, address: string): string => `${origin.replace(/\/+$/, '')}/${address.replace(/^\/+/, '')}`;

/** A call a sender could make, with the placeholder `<secret>` where the secret goes: the real one is never put in a second place. */
export function curlExample(url: string, fields: Record<string, string>): string {
  const data = Object.fromEntries(Object.entries(fields).map(([name, type]) => [name, type === 'bool' ? true : 'example']));
  const body = JSON.stringify({ title: 'Something happened', ...(Object.keys(data).length ? { data } : {}) });
  return [`curl -X POST "${url}"`, '  -H "Authorization: Bearer <secret>"', '  -H "Content-Type: application/json"', `  -d '${body}'`].join(' \\\n');
}

/** The body of a test event: yes/no fields as true or false, a text field left empty is left out, the title only when written. */
export function fireBody(fields: Record<string, string>, values: Record<string, string>, title: string, key: string): Record<string, unknown> {
  const data: Record<string, string | boolean> = {};
  for (const [name, type] of Object.entries(fields)) {
    const value = values[name] ?? '';
    if (type === 'bool') data[name] = value === 'yes';
    else if (value.trim()) data[name] = value;
  }
  return { key, ...(title.trim() ? { title: title.trim() } : {}), data };
}

/** "state (text)", "open (yes/no)". */
export const describeFields = (fields: Record<string, string>): string[] =>
  Object.entries(fields).map(([name, type]) => `${name} (${type === 'bool' ? 'yes/no' : type})`);

/** What a stored event carries, as "state: open" lines (a yes/no field is yes or no; a long text is cut). */
export function showData(data: Record<string, unknown>): string[] {
  return Object.entries(data).map(([name, value]) => {
    const text = typeof value === 'boolean' ? (value ? 'yes' : 'no') : String(value).replace(/\s+/g, ' ').trim();
    return `${name}: ${text.length > 200 ? `${text.slice(0, 200)}…` : text}`;
  });
}

/** How long ago, in a few words ("5 min ago"); the exact time goes in a title. */
export function ago(iso: string, now = Date.now()): string {
  const seconds = (now - Date.parse(iso)) / 1000;
  if (!(seconds >= 45)) return 'just now'; // also a date in the future (a clock a little ahead) and one that is not a date
  if (seconds < 90 * 60) return `${Math.round(seconds / 60)} min ago`;
  if (seconds < 36 * 3600) return `${Math.round(seconds / 3600)} h ago`;
  return `${Math.round(seconds / 86400)} days ago`;
}
