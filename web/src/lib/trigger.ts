// The trigger of an automation as a form: branches (an Event, a Time, or an All-of group), turned into the `when` tree the server takes
// and back, and checked before it is sent. The server checks everything again; this only puts the message where the mistake is.
// The editor is flat: the top is an OR of branches, a group is an AND of at most one event and conditions of time, so a trigger
// with a deeper shape (an OR inside an AND, a date inside an AND) is shown in words only (`fromWhen` says so).

export const MAX_NODES = 40;
export const MAX_FILTER_FIELDS = 10;
export const MAX_CONDITIONS = 5;
export const MAX_PATTERN = 500;
export const TWO_EVENTS = 'An All of group holds at most one event: put two events in separate branches of the Or.';

export type Cond = { pattern: string; not: boolean; caseSensitive: boolean };
export type FilterRow = { field: string; bool: boolean; conds: Cond[]; flag: 'yes' | 'no' };
export type EventBranch = { type: 'event'; kind: string; filters: FilterRow[] };
export type TimeBranch =
  | { type: 'once'; at: string }
  | { type: 'window'; from: string; to: string; days: number[] }
  | { type: 'cron'; expr: string };
export type GroupBranch = { type: 'group'; of: (EventBranch | TimeBranch)[] };
export type Branch = EventBranch | TimeBranch | GroupBranch;
export type KindInfo = { kind: string; fields: Record<string, string> };
export type Problem = { id: string; message: string };

export const WEEK = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
export const ALL_DAYS = [0, 1, 2, 3, 4, 5, 6];

export const newCond = (): Cond => ({ pattern: '', not: false, caseSensitive: false });
export const newEvent = (kind = ''): EventBranch => ({ type: 'event', kind, filters: [] });
export const newTime = (type: TimeBranch['type'] = 'once'): TimeBranch =>
  type === 'once' ? { type, at: '' } : type === 'window' ? { type, from: '18:00', to: '20:00', days: [...ALL_DAYS] } : { type, expr: '' };
export const newGroup = (): GroupBranch => ({ type: 'group', of: [newEvent()] });

export const isEvent = (node: { type: string }): boolean => node.type === 'event';
const isTimeNode = (node: { type: string }): boolean => node.type === 'once' || node.type === 'window' || node.type === 'cron';

/** A field name as a label: `from_address` becomes “From address”. */
export function fieldLabel(name: string): string {
  const text = name.replace(/_/g, ' ');
  return text.charAt(0).toUpperCase() + text.slice(1);
}

// ---------------------------------------------------------------- form -> `when`
function filterOf(rows: FilterRow[]): Record<string, unknown> {
  const filter: Record<string, unknown> = {};
  for (const row of rows) {
    if (row.bool) {
      filter[row.field] = { eq: row.flag === 'yes' };
      continue;
    }
    const conds = row.conds
      .filter((cond) => cond.pattern.trim())
      .map((cond) => ({
        pattern: cond.pattern,
        ...(cond.not ? { not: true } : {}),
        ...(cond.caseSensitive ? { case_sensitive: true } : {}),
      }));
    if (conds.length) filter[row.field] = conds.length === 1 ? conds[0] : conds;
  }
  return filter;
}

function nodeOf(node: EventBranch | TimeBranch, inGroup: boolean): Record<string, unknown> {
  if (node.type === 'event') return { type: 'event', kind: node.kind, filter: filterOf(node.filters) };
  if (node.type === 'once') return { type: 'once', at: node.at };
  if (node.type === 'window') return { type: 'window', from: node.from, to: node.to, days: [...node.days].sort() };
  return { type: 'cron', expr: node.expr.trim(), as: inGroup ? 'condition' : 'instant' };
}

/** The `when` tree the server takes: an OR of the branches; a group is an AND. */
export function toWhen(branches: Branch[]): Record<string, unknown> {
  return {
    type: 'or',
    of: branches.map((branch) =>
      branch.type === 'group' ? { type: 'and', of: branch.of.map((node) => nodeOf(node, true)) } : nodeOf(branch, false)),
  };
}

export function countNodes(branches: Branch[]): number {
  return 1 + branches.reduce((total, branch) => total + 1 + (branch.type === 'group' ? branch.of.length : 0), 0);
}

// ---------------------------------------------------------------- `when` -> form
function condsOf(spec: any): Cond[] {
  return (Array.isArray(spec) ? spec : [spec]).map((one) => ({ pattern: String(one.pattern ?? ''), not: !!one.not, caseSensitive: !!one.case_sensitive }));
}

function eventOf(node: any): EventBranch {
  const filters: FilterRow[] = Object.entries((node.filter ?? {}) as Record<string, any>).map(([field, spec]) =>
    spec && !Array.isArray(spec) && typeof spec.eq === 'boolean'
      ? { field, bool: true, conds: [], flag: spec.eq ? 'yes' : 'no' }
      : { field, bool: false, conds: condsOf(spec), flag: 'yes' });
  return { type: 'event', kind: node.kind, filters };
}

function timeOf(node: any): TimeBranch {
  if (node.type === 'once') return { type: 'once', at: node.at };
  if (node.type === 'window') return { type: 'window', from: node.from, to: node.to, days: [...(node.days ?? ALL_DAYS)] };
  return { type: 'cron', expr: node.expr };
}

/** The branches of a stored `when`, or `unsupported` with the reason when its shape is deeper than the editor draws. */
export function fromWhen(when: any): { branches: Branch[] } | { unsupported: string } {
  const top: any[] = [];
  const flatten = (node: any): void => {
    if (node.type === 'or') node.of.forEach(flatten);
    else top.push(node);
  };
  if (!when) return { branches: [] };
  flatten(when);
  const branches: Branch[] = [];
  for (const node of top) {
    if (node.type === 'event') branches.push(eventOf(node));
    else if (isTimeNode(node)) branches.push(timeOf(node));
    else if (node.type === 'and') {
      const inner: any[] = [];
      const gather = (child: any): void => {
        if (child.type === 'and') child.of.forEach(gather);
        else inner.push(child);
      };
      gather(node);
      const nodes: (EventBranch | TimeBranch)[] = [];
      for (const child of inner) {
        if (child.type === 'event') nodes.push(eventOf(child));
        else if (child.type === 'window' || (child.type === 'cron' && child.as === 'condition')) nodes.push(timeOf(child));
        else return { unsupported: 'An All of group here holds a date, a cron instant or an Or, which the editor does not draw.' };
      }
      branches.push({ type: 'group', of: nodes });
    } else return { unsupported: `A node of type ${node.type} is not one the editor draws.` };
  }
  return { branches };
}

// ---------------------------------------------------------------- checks
const CLOCK = /^([01]?\d|2[0-3]):[0-5]\d$/;

function checkEvent(node: EventBranch, id: string, kinds: KindInfo[], found: Problem[]): void {
  const info = kinds.find((item) => item.kind === node.kind);
  if (!info) {
    found.push({ id: `${id}-kind`, message: 'Choose what happens.' });
    return;
  }
  if (node.filters.length > MAX_FILTER_FIELDS) found.push({ id: `${id}-kind`, message: `At most ${MAX_FILTER_FIELDS} fields in a filter.` });
  node.filters.forEach((row, index) => {
    if (row.bool) return;
    if (row.conds.length > MAX_CONDITIONS) found.push({ id: `${id}-f${index}-0`, message: `At most ${MAX_CONDITIONS} conditions on a field.` });
    row.conds.forEach((cond, at) => {
      if (cond.pattern.length > MAX_PATTERN) found.push({ id: `${id}-f${index}-${at}`, message: `A pattern is ${MAX_PATTERN} characters at most.` });
      else if (!cond.pattern.trim() && (cond.not || cond.caseSensitive || row.conds.length > 1)) {
        found.push({ id: `${id}-f${index}-${at}`, message: 'Write the pattern, or remove the condition.' });
      }
    });
  });
}

function checkTime(node: TimeBranch, id: string, found: Problem[]): void {
  if (node.type === 'once' && !node.at) found.push({ id: `${id}-at`, message: 'Choose the date and the time.' });
  if (node.type === 'cron' && !node.expr.trim()) found.push({ id: `${id}-expr`, message: 'Write a cron expression, five fields: minute hour day month weekday.' });
  if (node.type === 'window') {
    if (!CLOCK.test(node.from)) found.push({ id: `${id}-from`, message: 'Choose when the window starts.' });
    else if (!CLOCK.test(node.to)) found.push({ id: `${id}-to`, message: 'Choose when the window ends.' });
    else if (node.from === node.to) found.push({ id: `${id}-to`, message: 'A window ends at another time than it starts.' });
    if (!node.days.length) found.push({ id: `${id}-days`, message: 'Tick at least one day.' });
  }
}

/** What is wrong with the branches, each with the id of the field it concerns (`tr-b<i>-…`). */
export function check(branches: Branch[], kinds: KindInfo[]): Problem[] {
  const found: Problem[] = [];
  if (!branches.length) return [{ id: 'tr-add', message: 'Add at least one branch: an event or a time.' }];
  branches.forEach((branch, index) => {
    const id = `tr-b${index}`;
    if (branch.type === 'event') checkEvent(branch, id, kinds, found);
    else if (branch.type === 'window') {
      found.push({ id: `${id}-from`, message: 'A window alone never starts a run: put it in an All of group, beside an event.' });
    } else if (branch.type === 'group') {
      const events = branch.of.filter(isEvent);
      if (events.length > 1) found.push({ id: `${id}-group`, message: TWO_EVENTS });
      else if (!events.length) found.push({ id: `${id}-group`, message: 'An All of group needs one event, with the conditions of time beside it.' });
      branch.of.forEach((node, at) => (node.type === 'event' ? checkEvent(node, `${id}-n${at}`, kinds, found) : checkTime(node, `${id}-n${at}`, found)));
    } else checkTime(branch, id, found);
  });
  if (countNodes(branches) > MAX_NODES) found.push({ id: 'tr-add', message: `At most ${MAX_NODES} nodes in a trigger.` });
  return found;
}

// ---------------------------------------------------------------- what depends on the tree
export const hasEvent = (branches: Branch[]): boolean =>
  branches.some((branch) => (branch.type === 'group' ? branch.of.some(isEvent) : isEvent(branch)));

const CATCH_UPS = [
  { value: 'all', label: 'A run for each', hint: 'Each one that was missed or found late, at the pace of the limit of runs an hour.' },
  { value: 'latest', label: 'Only the newest', hint: 'The others are recorded as skipped.' },
  { value: 'digest', label: 'One run for all', hint: 'It is given the whole backlog of events, up to 25. Needs an event in the trigger.' },
  { value: 'skip', label: 'Nothing', hint: 'All are recorded, none run.' },
];

/** The answers to "If a time was missed" that the server takes for this tree (`digest` needs an event); `follow` adds "by the trigger"
 * (null), which only a new automation can take: a change ignores null. */
export function catchUpOptions(branches: Branch[], follow: boolean): { value: string; label: string; hint: string }[] {
  const list = CATCH_UPS.filter((option) => option.value !== 'digest' || hasEvent(branches));
  return follow ? [{ value: '', label: 'By its trigger', hint: 'A run for each for events, nothing for times only.' }, ...list] : list;
}

/** The value to keep when the tree changes: a `digest` that no longer fits goes back to what the trigger says (or `all`). */
export const catchUpFor = (value: string, branches: Branch[], follow: boolean): string =>
  value === 'digest' && !hasEvent(branches) ? (follow ? '' : 'all') : value;
