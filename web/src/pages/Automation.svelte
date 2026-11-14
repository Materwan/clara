<script lang="ts">
  import { tick } from 'svelte';
  import Checkbox from '../components/Checkbox.svelte';
  import Choice from '../components/Choice.svelte';
  import Facts from '../components/Facts.svelte';
  import Hint from '../components/Hint.svelte';
  import Icon from '../components/Icon.svelte';
  import ModeSelect from '../components/ModeSelect.svelte';
  import ModelField from '../components/ModelField.svelte';
  import Segmented from '../components/Segmented.svelte';
  import { api, fileToBase64, when } from '../lib/api';
  import { ATTEMPTS, RETRY_HELP, excerpt, inWords } from '../lib/automation';
  import { confirmDialog } from '../lib/dialog.svelte';
  import { keptNote } from '../lib/memory';
  import { conversations, live, route, toast } from '../lib/state.svelte';
  import {
    MAX_CONDITIONS, MAX_FILTER_FIELDS, WEEK, catchUpFor, catchUpOptions, check, fieldLabel, fromWhen, hasEvent, newCond, newEvent, newGroup,
    newTime, toWhen, type Branch, type EventBranch, type TimeBranch,
  } from '../lib/trigger';
  import EventSources from './EventSources.svelte';

  // What Clara does without being asked, in one list and one form: an automation starts a run when any branch of its trigger holds.
  // A branch is an event (a mail, a push…), a time (a date, a cron expression, a window) or an All of group (one event with conditions
  // of time). The server keeps the tree (`when`); `lib/trigger.ts` turns the form into it and back.
  const ACTIONS = [
    { value: 'run', label: 'Run Clara', hint: 'She does what you ask, with the tools you give her.', icon: 'chat' },
    { value: 'notify', label: 'Only notify me', hint: 'No model: you get the text below, filled in from the event.', icon: 'bell' },
  ];
  const OVERLAPS = [
    { value: 'skip', label: 'Skip the new run', hint: 'It is recorded as skipped.' },
    { value: 'replace', label: 'Close the old one, start the new', hint: 'A waiting run is closed from what it has; a running one is waited for.' },
    { value: 'queue', label: 'Queue the new run', hint: 'It starts when the old one ends.' },
  ];
  const trio = (yes: string, no: string, follow: boolean) => [
    ...(follow ? [{ value: '', label: 'By its trigger', hint: 'What suits it: events and times have each their usual answer.' }] : []),
    { value: 'true', label: yes, hint: '' }, { value: 'false', label: no, hint: '' },
  ];
  const KEEP_AGE = [
    { value: 'follow', label: 'The Admin setting', hint: 'automation_keep_days and automation_keep_hours (none at first).' },
    { value: 'own', label: 'Its own age', hint: 'Deleted this long after the last message.' },
  ];
  const KEEP_COUNT = [
    { value: 'follow', label: 'The Admin setting', hint: 'automation_keep_last (none at first).' },
    { value: 'own', label: 'Its own count', hint: 'Only the latest ones are kept.' },
  ];
  const SOURCES: Record<string, { label: string; missing: string; setting: string }> = {
    mail: { label: 'Mail', missing: 'An automation on new mail has nothing to look at. Connect Gmail on the', setting: 'CLARA_MAIL_POLL_SECONDS' },
    github: { label: 'GitHub', missing: 'An automation on GitHub has nothing to look at. Connect GitHub and add a repository on the', setting: 'CLARA_GITHUB_POLL_SECONDS' },
  };
  const TIME_TYPES = [{ value: 'once', label: 'Once' }, { value: 'window', label: 'Window' }, { value: 'cron', label: 'Cron (advanced)' }];
  const CONDITION_TYPES = TIME_TYPES.filter((item) => item.value !== 'once');

  // Two tabs: the automations (the list and the form below) and the event sources that can start them (`#/automation?tab=sources`).
  type Tab = 'automations' | 'sources';
  const TABS = [{ value: 'automations' as Tab, label: 'Automations' }, { value: 'sources' as Tab, label: 'Event sources' }];
  let tab = $state<Tab>(route.query.get('tab') === 'sources' ? 'sources' : 'automations');

  const empty = () => ({
    id: 0, name: '', action: 'run', prompt: '', branches: [] as Branch[], keepWhen: null as any, unsupported: '', timezone: '',
    toolsMode: 'default', tools: [] as string[], packs: [] as string[], project: 'none', model: '', local_only: false,
    documents: [] as { name: string; text?: string }[], catch_up: '', overlap: '', per_run: '', rerun_interrupted: '',
    max_per_hour: 20 as number | null, max_age_hours: 0 as number | null, include_self: false, attempts: 3, announce: true, in_history: false,
    keepAge: 'follow', keepDays: 0, keepHours: 0, keepCount: 'follow', keepLast: 0,
  });

  let items = $state<any[]>([]);
  let kinds = $state<any[]>([]);
  let projects = $state<any[]>([]);
  let packs = $state<any[]>([]);
  let models = $state<any>({ models: [], default: '' });
  let toolNames = $state<string[]>([]);
  let sources = $state<Record<string, { ready: boolean; poll: number }>>({ mail: { ready: true, poll: 120 }, github: { ready: true, poll: 120 } });
  let form = $state(empty());
  let open = $state(false);
  let checked = $state(false); // a save was tried: every mistake is shown where it is
  let saving = $state(false);
  let serverError = $state('');
  let toolFilter = $state('');
  let inspect = $state<{ item: any; rows: any[] } | null>(null);
  let remembering = $state<any>(null);
  let memory = $state<Record<string, { facts: number; waiting: number; total: number }>>({});
  let probe = $state<any>(null); // { item, events, source, pick, at, result, error }
  let probeBox = $state<HTMLDialogElement>();
  let seen = $state<any[] | null>(null);
  let newButton = $state<HTMLButtonElement>();

  const scopeOf = (item: any): string => `automation:${item.id}`;
  const remembered = (item: any) => memory[scopeOf(item)] ?? { facts: 0, waiting: 0, total: 0 };
  const offers = (item: any, name: string): boolean =>
    item.effective_tools.some((pattern: string) => new RegExp(`^${pattern.replace(/[.+^${}()|[\]\\]/g, '\\$&').replace(/\*/g, '.*')}$`).test(name));
  const kindInfo = $derived(kinds.map((kind) => ({ kind: kind.kind, fields: kind.fields as Record<string, string> })));
  const labelOfKind = (kind: string): string => kinds.find((item) => item.kind === kind)?.label ?? kind;
  const cap = (text: string): string => `${text.charAt(0).toUpperCase()}${text.slice(1)}`;
  const packLabel = (id: string): string => packs.find((pack) => pack.id === id)?.label ?? id;
  const whole = (value: unknown, least: number, most: number): boolean => typeof value === 'number' && Number.isInteger(value) && value >= least && value <= most;
  const tone = (status: string): string => (status === 'ok' ? 'ok' : status === 'failed' ? 'danger' : status === 'interrupted' ? 'warn' : '');

  const runsClaude = $derived(form.action === 'run');
  const withEvent = $derived(hasEvent(form.branches));
  const formKinds = $derived(form.branches.flatMap((branch) => (branch.type === 'group' ? branch.of : [branch])).filter((node) => node.type === 'event').map((node) => (node as EventBranch).kind));
  // the sources the trigger waits for that no longer exist (an event source that was revoked)
  const formMissing = $derived([...new Set(formKinds.filter((kind) => kind && !kinds.some((item) => item.kind === kind)))]);
  const catchUps = $derived(catchUpOptions(form.branches, !form.id));
  const shownTools = $derived(toolNames.filter((name) => name.includes(toolFilter.trim().toLowerCase())));
  const readTools = $derived([...new Set([...formKinds.flatMap((kind) => kinds.find((item) => item.kind === kind)?.read_tools ?? []), 'notify'])]);
  const triggerProblems = $derived(form.unsupported ? [] : check(form.branches, kindInfo));
  const makesConversations = $derived(runsClaude);

  const keepBody = (): Record<string, number | null> => ({
    keep_days: form.keepAge === 'own' ? Number(form.keepDays) : null,
    keep_hours: form.keepAge === 'own' ? Number(form.keepHours) : null,
    keep_last: form.keepCount === 'own' ? Number(form.keepLast) : null,
  });

  const problems = $derived.by(() => {
    const found = [...triggerProblems];
    if (!form.name.trim()) found.push({ id: 'af-name', message: 'Give it a name.' });
    if (runsClaude && !form.prompt.trim() && !form.documents.length) found.push({ id: 'af-prompt', message: 'Say what Clara should do, or add a document.' });
    if (!runsClaude && !form.prompt.trim()) found.push({ id: 'af-prompt', message: 'Write the notification.' });
    if (!whole(form.max_per_hour, 1, 600)) found.push({ id: 'af-max-hour', message: 'Runs an hour must be a whole number from 1 to 600.' });
    if (!whole(form.max_age_hours, 0, 8760)) found.push({ id: 'af-max-age', message: 'Hours a run may wait must be a whole number from 0 to 8760.' });
    if (makesConversations && form.keepAge === 'own') {
      if (!whole(form.keepDays, 0, 3650)) found.push({ id: 'af-keep-days', message: 'Days must be a whole number from 0 to 3650.' });
      if (!whole(form.keepHours, 0, 8760)) found.push({ id: 'af-keep-hours', message: 'Hours must be a whole number from 0 to 8760.' });
    }
    if (makesConversations && form.keepCount === 'own' && !whole(form.keepLast, 0, 10000)) {
      found.push({ id: 'af-keep-last', message: 'The number of conversations kept must be a whole number from 0 to 10000.' });
    }
    return found;
  });
  // The two-events refusal is shown at once (it answers what you just added); the other mistakes after a first try to save.
  const errorOf = (id: string): string | undefined => {
    const found = problems.find((item) => item.id === id);
    return found && (checked || id.endsWith('-group')) ? found.message : undefined;
  };
  const invalid = (id: string): 'true' | undefined => (errorOf(id) ? 'true' : undefined);

  // The answer to "if a time was missed" follows the tree: a digest that no longer fits goes away with its event.
  $effect(() => {
    const kept = catchUpFor(form.catch_up, form.branches, !form.id);
    if (kept !== form.catch_up) form.catch_up = kept;
  });

  // What the next pass would delete for this automation with the limits on the form
  let keepPreview = $state<{ would_delete: number; more: number } | null>(null);
  $effect(() => {
    keepPreview = null;
    if (!open || !form.id || !makesConversations) return;
    const params: Record<string, number | string> = { kind: 'automation', id: form.id };
    for (const [key, value] of Object.entries(keepBody())) if (value !== null) params[key] = value;
    let stale = false;
    const handle = setTimeout(async () => {
      try {
        const found = await api.get('/v1/retention/preview', params);
        if (!stale) keepPreview = found;
      } catch {
        if (!stale) keepPreview = null;
      }
    }, 300);
    return () => {
      stale = true;
      clearTimeout(handle);
    };
  });

  const watched = (source: string): boolean =>
    (open && formKinds.some((kind) => kind.split('.')[0] === source)) || items.some((item) => item.enabled && item.kinds.some((kind: string) => kind.split('.')[0] === source));

  async function load(): Promise<void> {
    const data = await api.get('/v1/automations');
    items = data.automations;
    kinds = data.kinds;
    projects = (await api.get('/v1/projects')).projects;
    packs = (await api.get('/v1/packs', { where: 'automation' })).packs;
    models = await api.get('/v1/models').catch(() => ({ models: [], default: '' }));
    const connections = await api.get('/v1/connections').catch(() => null);
    if (connections) {
      sources = {
        mail: { ready: connections.gmail.connected, poll: connections.mail.poll_seconds },
        github: { ready: connections.github.connected && connections.github.repos.length > 0, poll: connections.github.poll_seconds },
      };
    }
    await loadMemory();
    if (inspect) await showRuns(items.find((item) => item.id === inspect!.item.id) ?? inspect.item);
    if (seen) seen = (await api.get('/v1/trigger-events', { limit: 30 })).events;
  }

  async function loadMemory(): Promise<void> {
    memory = (await api.get('/v1/memory/scopes').catch(() => ({ scopes: {} }))).scopes;
  }

  $effect(() => {
    void live.refresh;
    load();
  });

  // A run of an event starts and ends without a word on the event stream: while one is going or waiting, look again from time to time.
  $effect(() => {
    if (!items.some((item) => item.running || item.queued)) return;
    const timer = setInterval(load, 5000);
    return () => clearInterval(timer);
  });

  // ------------------------------------------------------------------ the form
  async function openForm(): Promise<void> {
    open = true;
    checked = false;
    serverError = '';
    if (!toolNames.length) toolNames = (await api.get('/v1/modes')).tools.map((tool: { name: string }) => tool.name);
    await tick();
    (document.getElementById('af-name') as HTMLElement | null)?.focus();
  }

  async function closeForm(): Promise<void> {
    open = false;
    toolFilter = '';
    form = empty();
    await tick();
    newButton?.focus();
  }

  const triState = (value: boolean): string => String(value);

  async function edit(item: any): Promise<void> {
    const read = fromWhen(item.when);
    form = {
      ...empty(), id: item.id, name: item.name, action: item.action, prompt: item.prompt,
      branches: 'branches' in read ? read.branches : [], keepWhen: item.when, unsupported: 'unsupported' in read ? read.unsupported ?? '' : '',
      timezone: item.timezone, toolsMode: item.tools.length ? 'choose' : 'default', tools: [...item.tools], packs: [...item.packs],
      project: item.project ? String(item.project) : 'none', model: item.model, local_only: item.local_only,
      documents: item.documents.map((doc: any) => ({ name: doc.name })), catch_up: item.catch_up, overlap: item.overlap,
      per_run: triState(item.per_run), rerun_interrupted: triState(item.rerun_interrupted), max_per_hour: item.max_per_hour,
      max_age_hours: item.max_age_hours, include_self: item.include_self, attempts: item.attempts, announce: item.announce,
      in_history: item.in_history,
      keepAge: item.keep_days === null ? 'follow' : 'own', keepDays: item.keep_days ?? 0, keepHours: item.keep_hours ?? 0,
      keepCount: item.keep_last === null ? 'follow' : 'own', keepLast: item.keep_last ?? 0,
    };
    await openForm();
  }

  async function save(): Promise<void> {
    serverError = '';
    if (problems.length) {
      checked = true;
      await tick();
      const first = problems[0];
      const field = document.getElementById(first.id);
      (field ?? document.getElementById('af-save'))?.focus();
      return;
    }
    saving = true;
    const run = runsClaude;
    const body: Record<string, any> = {
      name: form.name.trim(), when: form.unsupported ? form.keepWhen : toWhen(form.branches), action: form.action, prompt: form.prompt,
      documents: run ? form.documents : [], packs: run ? form.packs : [], tools: run && form.toolsMode === 'choose' ? form.tools : [],
      project: form.project === 'none' ? 'none' : Number(form.project), model: run ? form.model : '', local_only: form.local_only,
      max_per_hour: Number(form.max_per_hour), max_age_hours: Number(form.max_age_hours), include_self: form.include_self,
      attempts: Number(form.attempts), announce: form.announce, in_history: run ? form.in_history : false, ...keepBody(),
    };
    if (form.timezone.trim()) body.timezone = form.timezone.trim();
    if (form.catch_up) body.catch_up = form.catch_up;
    if (form.overlap) body.overlap = form.overlap;
    if (form.per_run) body.per_run = form.per_run === 'true';
    if (form.rerun_interrupted) body.rerun_interrupted = form.rerun_interrupted === 'true';
    try {
      if (form.id) await api.patch(`/v1/automations/${form.id}`, body);
      else await api.post('/v1/automations', body);
      conversations.changed++;
      await closeForm();
      load();
    } catch (error) {
      serverError = (error as Error).message;
    } finally {
      saving = false;
    }
  }

  const toggleTool = (name: string): void => {
    form.tools = form.tools.includes(name) ? form.tools.filter((item) => item !== name) : [...form.tools, name];
  };
  const togglePack = (id: string): void => {
    form.packs = form.packs.includes(id) ? form.packs.filter((item) => item !== id) : [...form.packs, id];
  };
  const toggleDay = (node: { days: number[] }, index: number): void => {
    node.days = node.days.includes(index) ? node.days.filter((day) => day !== index) : [...node.days, index];
  };

  async function addDocuments(event: Event): Promise<void> {
    for (const file of (event.target as HTMLInputElement).files ?? []) {
      const extracted = await api.post('/v1/documents/extract', { name: file.name, data: await fileToBase64(file) });
      form.documents.push({ name: file.name, text: extracted.text });
    }
  }

  // the trigger editor
  const addEvent = (): void => void form.branches.push(newEvent());
  const addTime = (): void => void form.branches.push(newTime('once'));
  const addGroup = (): void => void form.branches.push(newGroup());
  const addFilter = (node: EventBranch, field: string): void => {
    if (!field) return;
    const bool = kindInfo.find((item) => item.kind === node.kind)?.fields[field] === 'bool';
    node.filters.push({ field, bool, conds: bool ? [] : [newCond()], flag: 'yes' });
  };
  const freeFields = (node: EventBranch): string[] =>
    Object.keys(kindInfo.find((item) => item.kind === node.kind)?.fields ?? {}).filter((field) => !node.filters.some((row) => row.field === field));
  const resetTrigger = (): void => {
    form.unsupported = '';
    form.branches = [];
  };

  // ------------------------------------------------------------------ the list
  async function run(item: any): Promise<void> {
    try {
      await api.post(`/v1/automations/${item.id}/run`);
      toast('Started: the summary arrives when it ends.', 'ok');
      load();
    } catch (error) {
      toast((error as Error).message, 'error');
    }
  }

  async function close(item: any): Promise<void> {
    try {
      await api.post(`/v1/automations/${item.id}/close`);
      toast('Closing it from what Clara has: the summary arrives when it ends.', 'ok');
      load();
    } catch (error) {
      toast((error as Error).message, 'error');
    }
  }

  async function setMode(item: any, value: string | null): Promise<void> {
    try {
      await api.put(`/v1/automations/${item.id}/mode`, { mode: value });
    } catch (error) {
      toast((error as Error).message, 'error');
    }
    load();
  }

  async function toggle(item: any): Promise<void> {
    try {
      await api.patch(`/v1/automations/${item.id}`, { enabled: !item.enabled });
    } catch (error) {
      toast((error as Error).message, 'error');
    }
    load();
  }

  async function remove(item: any): Promise<void> {
    const message = `Delete “${item.name}”? Its runs are forgotten; the conversations they made stay, no longer grouped under it.${keptNote(remembered(item))}`;
    if (!(await confirmDialog({ title: 'Delete automation', message, confirm: 'Delete', danger: true }))) return;
    try {
      await api.del(`/v1/automations/${item.id}`);
    } catch (error) {
      toast((error as Error).message, 'error');
    }
    if (inspect?.item.id === item.id) inspect = null;
    if (remembering?.id === item.id) remembering = null;
    if (probe?.item.id === item.id) closeProbe();
    load();
  }

  async function showRuns(item: any): Promise<void> {
    inspect = { item, rows: (await api.get(`/v1/automations/${item.id}/runs`, { limit: 50 })).runs };
  }

  // ------------------------------------------------------------------ test
  async function openProbe(item: any): Promise<void> {
    const lists = await Promise.all(item.kinds.map((kind: string) => api.get('/v1/trigger-events', { limit: 20, kind }).then((found: any) => found.events)));
    const stored = lists.flat().sort((a: any, b: any) => (a.occurred_at < b.occurred_at ? 1 : -1));
    probe = { item, events: stored, source: stored.length || item.kinds.length ? 'event' : 'at', pick: '', at: '', result: null, error: '' };
    await tick();
    probeBox?.showModal();
  }

  function closeProbe(): void {
    probeBox?.close();
    probe = null;
  }

  async function runProbe(): Promise<void> {
    if (!probe) return;
    const body = probe.source === 'at' ? { at: probe.at || null } : { event: probe.pick ? Number(probe.pick) : null };
    try {
      probe.result = await api.post(`/v1/automations/${probe.item.id}/test`, body);
      probe.error = '';
    } catch (error) {
      probe.result = null;
      probe.error = (error as Error).message;
    }
  }

  async function toggleSeen(): Promise<void> {
    seen = seen ? null : (await api.get('/v1/trigger-events', { limit: 30 })).events;
  }
</script>

{#snippet verdictNode(node: any)}
  <li>
    <span class="tag {node.ok ? 'ok' : 'warn'}">{node.ok ? 'holds' : 'does not hold'}</span>
    <strong>{node.type}</strong> <span class="muted small">{node.why}</span>
    {#if node.of?.length}<ul class="verdicts">{#each node.of as child}{@render verdictNode(child)}{/each}</ul>{/if}
  </li>
{/snippet}

{#snippet revokedLine(missing: string[])}
  <span class="tag warn"><Icon name="warn" size={14} />Source revoked</span>
  <span class="small">Waits for a source that was revoked: {missing.join(', ')}. It starts again when a source with the same name exists.</span>
{/snippet}

{#snippet conditionRow(row: any, cond: any, index: number, id: string)}
  <div class="cond" role="group" aria-label={`${fieldLabel(row.field)}, condition ${index + 1}`}>
    <input id={id} class="grow value" aria-label="Regular expression" placeholder="regular expression, e.g. ^invoice" bind:value={cond.pattern}
      aria-invalid={invalid(id)} aria-describedby={errorOf(id) ? `${id}-error` : undefined} />
    <label class="small row"><Checkbox bind:checked={cond.not} />must not match</label>
    <label class="small row"><Checkbox bind:checked={cond.caseSensitive} />case-sensitive</label>
    {#if row.conds.length > 1 || cond.pattern}
      <button type="button" class="icon-btn" aria-label={`Remove condition ${index + 1} on ${fieldLabel(row.field)}`}
        onclick={() => row.conds.splice(index, 1)}><Icon name="close" size={16} /></button>
    {/if}
  </div>
  {#if errorOf(id)}<span class="field-error" id={`${id}-error`} role="alert">{errorOf(id)}</span>{/if}
{/snippet}

{#snippet eventEditor(node: EventBranch, id: string)}
  <label class="field narrow">Something happens
    <select id={`${id}-kind`} bind:value={node.kind} onchange={() => (node.filters = [])} aria-invalid={invalid(`${id}-kind`)}
      aria-describedby={errorOf(`${id}-kind`) ? `${id}-kind-error` : undefined}>
      <option value="" disabled>Choose…</option>
      {#each kinds as kind (kind.kind)}<option value={kind.kind}>{cap(kind.label)}</option>{/each}
      {#if node.kind && !kinds.some((kind) => kind.kind === node.kind)}<option value={node.kind} disabled>{node.kind} (revoked)</option>{/if}
    </select>
    {#if errorOf(`${id}-kind`)}<span class="field-error" id={`${id}-kind-error`} role="alert">{errorOf(`${id}-kind`)}</span>{/if}
  </label>
  {#if node.kind}
    {#each node.filters as row, fi (row.field)}
      <div class="stack filter" role="group" aria-label={`Filter on ${fieldLabel(row.field)}`}>
        <div class="row">
          <strong class="small grow">{fieldLabel(row.field)}</strong>
          <button type="button" class="icon-btn" aria-label={`Remove the filter on ${fieldLabel(row.field)}`} onclick={() => node.filters.splice(fi, 1)}><Icon name="close" size={16} /></button>
        </div>
        {#if row.bool}
          <div class="segmented" role="group" aria-label={fieldLabel(row.field)}>
            <button type="button" aria-pressed={row.flag === 'yes'} onclick={() => (row.flag = 'yes')}>Yes</button>
            <button type="button" aria-pressed={row.flag === 'no'} onclick={() => (row.flag = 'no')}>No</button>
          </div>
        {:else}
          {#each row.conds as cond, ci}{@render conditionRow(row, cond, ci, `${id}-f${fi}-${ci}`)}{/each}
          {#if row.conds.length < MAX_CONDITIONS}
            <div><button type="button" class="small" onclick={() => row.conds.push(newCond())}><Icon name="plus" size={14} />condition</button>
              {#if row.conds.length > 1}<span class="muted small"> Every condition on a field must hold.</span>{/if}</div>
          {/if}
        {/if}
      </div>
    {/each}
    {#if node.filters.length < MAX_FILTER_FIELDS && freeFields(node).length}
      <label class="field narrow">Only when…
        <select aria-label="Add a filter on a field" value="" onchange={(event) => { addFilter(node, event.currentTarget.value); event.currentTarget.value = ''; }}>
          <option value="">{node.filters.length ? 'Add another field' : 'Any event of this kind (add a field to filter)'}</option>
          {#each freeFields(node) as field}<option value={field}>{fieldLabel(field)}</option>{/each}
        </select>
      </label>
    {/if}
    <p class="muted small">A regular expression, found anywhere in the text, capitals ignored unless you tick case-sensitive. A sender's name and address can be forged: a filter on them is a convenience, not a proof.</p>
  {/if}
{/snippet}

{#snippet timeEditor(node: TimeBranch, id: string, list: Branch[] | (EventBranch | TimeBranch)[], index: number, inGroup: boolean)}
  <div class="row start">
    <label class="field narrow">{inGroup ? 'Only while' : 'At a time'}
      <select aria-label="Kind of time" value={node.type} onchange={(event) => (list[index] = newTime(event.currentTarget.value as TimeBranch['type']))}>
        {#each inGroup ? CONDITION_TYPES : TIME_TYPES as type}<option value={type.value}>{type.label}</option>{/each}
      </select>
    </label>
    {#if node.type === 'once'}
      <label class="field">Date and time
        <input id={`${id}-at`} type="datetime-local" bind:value={node.at} aria-invalid={invalid(`${id}-at`)} aria-describedby={errorOf(`${id}-at`) ? `${id}-at-error` : undefined} />
        {#if errorOf(`${id}-at`)}<span class="field-error" id={`${id}-at-error`} role="alert">{errorOf(`${id}-at`)}</span>{/if}
      </label>
    {:else if node.type === 'cron'}
      <label class="field grow">Cron expression
        <span class="hint">Five fields: minute hour day month weekday (names, lists, ranges, steps and L work). {inGroup ? 'Here it is a condition: it holds during the minutes it names.' : 'It starts a run at each minute it names.'}</span>
        <input id={`${id}-expr`} bind:value={node.expr} placeholder="0 9 * * 1-5" spellcheck="false" aria-invalid={invalid(`${id}-expr`)}
          aria-describedby={errorOf(`${id}-expr`) ? `${id}-expr-error` : undefined} />
        {#if errorOf(`${id}-expr`)}<span class="field-error" id={`${id}-expr-error`} role="alert">{errorOf(`${id}-expr`)}</span>{/if}
      </label>
    {:else}
      <label class="field">From
        <input id={`${id}-from`} type="time" bind:value={node.from} aria-invalid={invalid(`${id}-from`)} aria-describedby={errorOf(`${id}-from`) ? `${id}-from-error` : undefined} />
        {#if errorOf(`${id}-from`)}<span class="field-error" id={`${id}-from-error`} role="alert">{errorOf(`${id}-from`)}</span>{/if}
      </label>
      <label class="field">To
        <input id={`${id}-to`} type="time" bind:value={node.to} aria-invalid={invalid(`${id}-to`)} aria-describedby={errorOf(`${id}-to`) ? `${id}-to-error` : undefined} />
        {#if errorOf(`${id}-to`)}<span class="field-error" id={`${id}-to-error`} role="alert">{errorOf(`${id}-to`)}</span>{/if}
      </label>
    {/if}
  </div>
  {#if node.type === 'window'}
    <div id={`${id}-days`} class="segmented" role="group" aria-label="On these days" tabindex="-1">
      {#each WEEK as day, d}<button type="button" aria-pressed={node.days.includes(d)} onclick={() => toggleDay(node, d)}>{day}</button>{/each}
    </div>
    <p class="muted small">It may cross midnight (18:30 to 02:15); the end is not included; the days are the days it starts on.</p>
    {#if errorOf(`${id}-days`)}<span class="field-error" role="alert">{errorOf(`${id}-days`)}</span>{/if}
  {/if}
{/snippet}

<div class="page stack">
  <div><Segmented label="Section" options={TABS} bind:value={tab} /></div>
  {#if tab === 'automations'}
  <div class="row start">
    <p class="intro grow">Clara does things by herself, unattended, when something you describe happens: a new mail, a push, a date, a time
      of day, or a combination. A run that would change something outside (send a mail, write to GitHub or Drive) asks you first, and an action
      its mode says to ask about is held and pushed to you. A run has only the tools you give it.</p>
    <button class="primary" aria-expanded={open} aria-controls="automation-form" bind:this={newButton}
      onclick={() => (open ? closeForm() : openForm())}><Icon name="plus" size={18} />New automation</button>
  </div>
  {#each Object.entries(SOURCES) as [source, info] (source)}
    {#if watched(source) && !sources[source].ready}
      <p class="row" role="status"><span class="tag warn"><Icon name="warn" size={14} />Nothing to look at</span>
        <span class="small">{info.missing} <a href="#/connections">Connections</a> page.</span></p>
    {:else if watched(source) && sources[source].poll === 0}
      <p class="row" role="status"><span class="tag warn"><Icon name="warn" size={14} />{info.label} polling is off</span>
        <span class="small"><code>{info.setting}</code> is 0 on the server, so no automation will start from it.</span></p>
    {/if}
  {/each}

  {#if open}
    <form id="automation-form" class="panel stack" aria-label={form.id ? `Change ${form.name}` : 'New automation'} novalidate
      onsubmit={(event) => { event.preventDefault(); save(); }}>
      <h2>{form.id ? `Change “${form.name}”` : 'New automation'}</h2>

      <section class="stack">
        <label class="field">Name
          <input id="af-name" bind:value={form.name} maxlength="100" aria-invalid={invalid('af-name')} aria-describedby={errorOf('af-name') ? 'af-name-error' : undefined} />
          {#if errorOf('af-name')}<span class="field-error" id="af-name-error" role="alert">{errorOf('af-name')}</span>{/if}
        </label>
        <Choice cards legend="Then" options={ACTIONS} bind:value={form.action} />
        <label class="field">{runsClaude ? 'What Clara should do' : 'The notification'}
          {#if !runsClaude}<span class="hint">Words in braces, such as {'{subject}'}, are filled in from the event.</span>{/if}
          <textarea id="af-prompt" rows={runsClaude ? 6 : 3} bind:value={form.prompt} aria-invalid={invalid('af-prompt')}
            aria-describedby={errorOf('af-prompt') ? 'af-prompt-error' : undefined}></textarea>
          {#if errorOf('af-prompt')}<span class="field-error" id="af-prompt-error" role="alert">{errorOf('af-prompt')}</span>{/if}
        </label>
      </section>

      <section class="block stack" aria-labelledby="af-when">
        <h3 id="af-when">When: it starts a run when any of these happens</h3>
        {#if formMissing.length}<p class="row" role="status">{@render revokedLine(formMissing)}</p>{/if}
        {#if form.unsupported}
          <p class="row" role="status"><span class="tag warn"><Icon name="warn" size={14} />Shown in words</span> <span class="small">{form.unsupported}</span></p>
          <p>{items.find((item) => item.id === form.id)?.when_words}</p>
          <p class="muted small">It stays as it is when you save. Replace it to edit it here.</p>
          <div><button type="button" class="small" onclick={resetTrigger}>Replace it with a new trigger</button></div>
        {:else}
          {#each form.branches as branch, index}
            {@const id = `tr-b${index}`}
            <div class="branch stack" role="group" aria-label={`Branch ${index + 1}`}>
              <div class="row">
                <span class="tag">{index === 0 ? 'Either' : 'or'}</span>
                <strong class="grow small">{branch.type === 'event' ? 'Event' : branch.type === 'group' ? 'All of: one event, with conditions of time' : 'Time'}</strong>
                <button type="button" class="icon-btn" aria-label={`Remove branch ${index + 1}`} onclick={() => form.branches.splice(index, 1)}><Icon name="trash" size={16} /></button>
              </div>
              {#if branch.type === 'event'}
                {@render eventEditor(branch, id)}
              {:else if branch.type === 'group'}
                {#each branch.of as node, at}
                  <div class="member stack">
                    {#if node.type === 'event'}
                      {@render eventEditor(node, `${id}-n${at}`)}
                    {:else}
                      {@render timeEditor(node, `${id}-n${at}`, branch.of, at, true)}
                    {/if}
                    {#if branch.of.length > 1}
                      <div><button type="button" class="small" onclick={() => branch.of.splice(at, 1)}><Icon name="close" size={14} />Remove this part</button></div>
                    {/if}
                  </div>
                {/each}
                <div class="row">
                  <button type="button" class="small" onclick={() => branch.of.push(newEvent())}><Icon name="plus" size={14} />Event</button>
                  <button type="button" class="small" onclick={() => branch.of.push(newTime('window'))}><Icon name="plus" size={14} />Condition of time</button>
                </div>
                {#if errorOf(`${id}-group`)}<span class="field-error" id={`${id}-group`} role="alert" tabindex="-1">{errorOf(`${id}-group`)}</span>{/if}
              {:else}
                {@render timeEditor(branch, id, form.branches, index, false)}
              {/if}
            </div>
          {:else}
            <p class="muted">No branch yet: add the first.</p>
          {/each}
          <div class="row">
            <button type="button" class="small" id="tr-add" onclick={addEvent}><Icon name="plus" size={14} />Event</button>
            <button type="button" class="small" onclick={addTime}><Icon name="plus" size={14} />Time</button>
            <button type="button" class="small" onclick={addGroup}><Icon name="plus" size={14} />All of group</button>
          </div>
          {#if errorOf('tr-add')}<span class="field-error" role="alert">{errorOf('tr-add')}</span>{/if}
        {/if}
        <label class="field narrow">Time zone
          <span class="hint">For its dates, times of day and cron expressions. {form.id ? '' : 'Empty: the server\'s zone.'}</span>
          <input bind:value={form.timezone} placeholder="Europe/Paris" spellcheck="false" />
        </label>
      </section>

      <section class="block stack">
        <h3>Tools and project</h3>
        {#if runsClaude}
          <fieldset class="packs">
            <legend class="small">Special tools</legend>
            {#each packs as pack (pack.id)}
              {@const stuck = !pack.available && !form.packs.includes(pack.id)}
              <label class="pack" class:off={stuck} title={pack.reason || pack.description}>
                <Checkbox checked={form.packs.includes(pack.id)} disabled={stuck} onchange={() => togglePack(pack.id)} />
                <Icon name={pack.icon} size={16} />
                <span class="pack-text"><b>{pack.label}</b> <span class="muted small"><Hint text={pack.reason || pack.description} /></span></span>
              </label>
            {/each}
          </fieldset>
          <details>
            <summary>Tools of the run: {form.toolsMode === 'choose' && form.tools.length ? `${form.tools.length} chosen` : 'the default'}</summary>
            <div class="stack">
              <p class="muted small">Without a list, a run started by a time can only notify you; an event gives its own read tools{withEvent ? ` (now: ${readTools.join(', ')})` : ''}. Give the fewest the job needs: a hostile message or pull request can steer a run only within them. Whatever changes something outside still asks you.</p>
              <input type="search" aria-label="Filter the tools" placeholder="filter the list" bind:value={toolFilter} oninput={() => (form.toolsMode = 'choose')} />
              <div class="tool-list" role="group" aria-label="Tools to choose from">
                {#each shownTools as name (name)}
                  <label class="small row"><Checkbox checked={form.tools.includes(name)} onchange={() => { form.toolsMode = 'choose'; toggleTool(name); }} />{name}</label>
                {/each}
              </div>
              {#if form.tools.length}<div><button type="button" class="small" onclick={() => { form.tools = []; form.toolsMode = 'default'; }}>Back to the default</button></div>{/if}
            </div>
          </details>
          <div class="grid-2">
            <ModelField bind:value={form.model} {models} />
            <div class="field">
              <span class="field-title">Documents</span>
              <span class="hint">Their text goes with the prompt at every run.</span>
              <div><label class="button small"><Icon name="upload" size={16} />Add documents<input class="sr-only" type="file" multiple onchange={addDocuments} /></label></div>
            </div>
          </div>
          {#if form.documents.length}
            <div class="chips">{#each form.documents as doc, index}<span class="chip"><Icon name="file" size={14} /><span class="name">{doc.name}</span><button type="button" aria-label={`Remove ${doc.name}`} onclick={() => form.documents.splice(index, 1)}><Icon name="close" size={14} /></button></span>{/each}</div>
          {/if}
        {/if}
        <label class="field narrow">Project of the runs
          <span class="hint">Its files and instructions are context for every run.</span>
          <select bind:value={form.project}>
            <option value="none">no project</option>
            {#each projects as project}<option value={String(project.id)}>{project.name}</option>{/each}
          </select></label>
        <label class="option"><Checkbox bind:checked={form.local_only} />
          <span class="option-text"><strong>Local only</strong><span>Local models only; nothing leaves your machines.</span></span></label>
      </section>

      <section class="block stack">
        <h3>Result</h3>
        <p>A short notice to every client, Discord included.</p>
        {#if runsClaude}
          <label class="option"><Checkbox bind:checked={form.in_history} />
            <span class="option-text"><strong>Show in history</strong><span>Discord thread with the whole answer: each run's conversation is listed in the history, and answered there.</span></span></label>
        {/if}
        <label class="option"><Checkbox bind:checked={form.announce} />
          <span class="option-text"><strong>Tell me when a run ends</strong><span>A good run stays quiet when this is off; a failed one always tells.</span></span></label>
      </section>

      <details class="block">
        <summary>Advanced: missed times, overlaps, pace, cleaning up</summary>
        <div class="stack">
          <Choice cards legend="If a time was missed" help="Instants missed while the server was off, and events found more than 15 minutes after they happened."
            options={catchUps} bind:value={form.catch_up} />
          <Choice cards legend="If the last run is not over" help="A run comes due while the previous one still runs or waits."
            options={[...(form.id ? [] : [{ value: '', label: 'By its trigger', hint: 'Queued for events, skipped for times.' }]), ...OVERLAPS]} bind:value={form.overlap} />
          {#if runsClaude}
            <Choice legend="Conversation" options={trio('One per run', 'One for all runs', !form.id)} bind:value={form.per_run} />
            <Choice legend="A run the server stopped half way" help="It may have done part of its work."
              options={trio('Run it again', 'Do not repeat it', !form.id)} bind:value={form.rerun_interrupted} />
            <Choice legend="If the model is busy or unreachable" help={RETRY_HELP} options={ATTEMPTS}
              bind:value={() => String(form.attempts), (value) => (form.attempts = Number(value))} />
          {/if}
          <div class="row">
            <label class="field narrow">Runs an hour, at most
              <input id="af-max-hour" type="number" min="1" max="600" bind:value={form.max_per_hour} aria-invalid={invalid('af-max-hour')} />
              {#if errorOf('af-max-hour')}<span class="field-error" role="alert">{errorOf('af-max-hour')}</span>{/if}
            </label>
            <label class="field narrow">Expire a run that waited more than (hours)
              <span class="hint">0: never.</span>
              <input id="af-max-age" type="number" min="0" max="8760" bind:value={form.max_age_hours} aria-invalid={invalid('af-max-age')} />
              {#if errorOf('af-max-age')}<span class="field-error" role="alert">{errorOf('af-max-age')}</span>{/if}
            </label>
          </div>
          {#if formKinds.includes('mail.received')}
            <label class="option"><Checkbox bind:checked={form.include_self} />
              <span class="option-text"><strong>Also for mail from my own account</strong><span>Mail sent from Clara's own account is left out, so an automation cannot answer its own answer.</span></span></label>
          {/if}
          {#if makesConversations}
            <h4>Cleaning up</h4>
            <p class="hint">The conversations of its runs are deleted for good when they pass a limit, whichever comes first. A pinned one, one you archived and a run still waiting are kept. Both limits are off until one is set.</p>
            <Choice legend="Age" options={KEEP_AGE} bind:value={form.keepAge} />
            {#if form.keepAge === 'own'}
              <div class="row">
                <label class="field narrow">Days
                  <input id="af-keep-days" type="number" min="0" max="3650" bind:value={form.keepDays} aria-invalid={invalid('af-keep-days')} />
                  {#if errorOf('af-keep-days')}<span class="field-error" role="alert">{errorOf('af-keep-days')}</span>{/if}
                </label>
                <label class="field narrow">Hours
                  <input id="af-keep-hours" type="number" min="0" max="8760" bind:value={form.keepHours} aria-invalid={invalid('af-keep-hours')} />
                  {#if errorOf('af-keep-hours')}<span class="field-error" role="alert">{errorOf('af-keep-hours')}</span>{/if}
                </label>
              </div>
            {/if}
            <Choice legend="Count" options={KEEP_COUNT} bind:value={form.keepCount} />
            {#if form.keepCount === 'own'}
              <label class="field narrow">Conversations kept
                <span class="hint">The latest ones. 0: no count limit.</span>
                <input id="af-keep-last" type="number" min="0" max="10000" bind:value={form.keepLast} aria-invalid={invalid('af-keep-last')} />
                {#if errorOf('af-keep-last')}<span class="field-error" role="alert">{errorOf('af-keep-last')}</span>{/if}
              </label>
            {/if}
            {#if keepPreview}
              <p class="small" class:danger={keepPreview.more > 0} role="status">
                {#if !keepPreview.would_delete}
                  No conversation of its runs is past these limits now.
                {:else}
                  The next pass (within 10 minutes) deletes {keepPreview.would_delete} conversation{keepPreview.would_delete === 1 ? '' : 's'} of its runs for good{keepPreview.more === keepPreview.would_delete ? '' : keepPreview.more ? `, ${keepPreview.more} of them because of this change` : ', none of them because of this change'}.
                {/if}
              </p>
            {/if}
          {/if}
        </div>
      </details>

      {#if checked && problems.length}
        <p class="field-error" role="alert">{problems.length === 1 ? '1 thing' : `${problems.length} things`} to fix before it can be {form.id ? 'saved' : 'created'}: {problems.map((item) => item.message).join(' ')}</p>
      {/if}
      {#if serverError}<p class="field-error" role="alert">{serverError}</p>{/if}
      <div class="row">
        <button type="submit" id="af-save" class="primary" disabled={saving}>{form.id ? 'Save' : 'Create'}</button>
        <button type="button" onclick={closeForm}>Cancel</button>
      </div>
    </form>
  {/if}

  <table>
    <thead><tr><th>Automation</th><th>Last run</th><th><span class="sr-only">Actions</span></th></tr></thead>
    <tbody>
      {#each items as item (item.id)}
        <tr>
          <td>
            <strong>{item.name}</strong>
            <span class="tag">{item.action === 'run' ? 'runs Clara' : 'notifies'}</span>
            {#if !item.enabled}<span class="tag">paused</span>{/if}{#if item.running}<span class="tag ok">running</span>{/if}
            {#if item.queued}<span class="tag warn">{item.queued} waiting</span>{/if}
            {#if item.waiting_run}<span class="tag warn">waiting for you</span>{/if}
            {#if item.local_only}<span class="tag"><Icon name="home" size={14} />local only</span>{/if}
            {#if item.in_history}<span class="tag"><Icon name="chat" size={14} />in history</span>{/if}
            {#if remembered(item).waiting}<span class="tag warn">{remembered(item).waiting} {remembered(item).waiting === 1 ? 'fact waits' : 'facts wait'} for you</span>{/if}
            <br /><span class="small">{cap(item.when_words)}</span>
            {#if item.missing_kinds.length}<br />{@render revokedLine(item.missing_kinds)}{/if}
            <br /><span class="muted small" title={item.next_local ? `${item.next_local} (${item.timezone})` : undefined}>{#if item.next_local}Next {item.next_local}{:else if item.kinds.length}On {inWords(item.kinds.map(labelOfKind))}{/if}{#if item.model} · {item.model}{/if}{#if item.packs.length} · {item.packs.map(packLabel).join(' · ')}{/if}</span>
            {#if item.action === 'run'}
              <br /><ModeSelect small label={`Mode of ${item.name}`} value={item.mode} effective={item.mode ?? 'default'}
                follows={item.project ? 'its project or the default' : 'the default'} onchange={(value) => setMode(item, value)} />
            {/if}
          </td>
          <td class="small">{#if item.last}<span class="tag {tone(item.last.status)}">{item.last.status}</span> {when(item.last.finished_at ?? item.last.started_at ?? item.last.queued_at)}<br /><span class="muted">{excerpt(item.last.summary || item.last.note, 120)}</span>{:else}—{/if}</td>
          <td class="row">
            <button class="small" onclick={() => openProbe(item)}>Test</button>
            <button class="small" onclick={() => run(item)} disabled={item.running}>Run now</button>
            <button class="small" onclick={() => edit(item)}>Edit</button>
            <button class="small" onclick={() => toggle(item)}>{item.enabled ? 'Pause' : 'Resume'}</button>
            <button class="small" onclick={() => showRuns(item)}>Runs</button>
            <button class="small" aria-expanded={remembering?.id === item.id}
              onclick={() => (remembering?.id === item.id ? (remembering = null) : (remembering = item))}>Memory{remembered(item).facts ? ` (${remembered(item).facts})` : ''}</button>
            {#if item.waiting_run}
              <a class="small" href={'#/chat/' + item.waiting_run.conversation}>Answer</a>
              <button class="small" onclick={() => close(item)}>Close it</button>
            {:else if item.conversation ?? item.last?.conversation}
              <a class="small" href={'#/chat/' + (item.conversation ?? item.last.conversation)}>Conversation</a>
            {/if}
            <button class="small danger" onclick={() => remove(item)}>Delete</button>
          </td>
        </tr>
      {:else}
        <tr><td colspan="3" class="empty">Nothing planned yet.</td></tr>
      {/each}
    </tbody>
  </table>

  {#if remembering}
    <div class="panel stack">
      <div class="row"><h2 class="grow">Memory of {remembering.name}</h2><button class="small" onclick={() => (remembering = null)}>Close</button></div>
      <Facts scope={scopeOf(remembering)} onchange={loadMemory} />
      {#if remembering.action === 'run' && !offers(remembering, 'remember')}
        <p class="muted small">This automation's runs read this memory but cannot add to it: put the tool <code>remember</code> in its list of tools.</p>
      {/if}
    </div>
  {/if}

  {#if inspect}
    <div class="panel">
      <div class="row"><h2 class="grow">Runs of {inspect.item.name}</h2><button class="small" onclick={() => (inspect = null)}>Close</button></div>
      <table>
        <thead><tr><th>When</th><th>Status</th><th>Event</th><th>What happened</th><th><span class="sr-only">Conversation</span></th></tr></thead>
        <tbody>
          {#each inspect.rows as entry (entry.id)}
            <tr>
              <td class="small">{when(entry.started_at ?? entry.queued_at ?? entry.due_at)}</td>
              <td><span class="tag {tone(entry.status)}">{entry.status}</span></td>
              <td class="small">{entry.event_count > 1 ? `${entry.event_count} events: ` : ''}{entry.events.slice(0, 2).map((stored: any) => stored.title).join(' · ')}{#if entry.waiting} · {entry.waiting} waiting{/if}</td>
              <td class="small">{entry.summary || entry.note}</td>
              <td>{#if entry.conversation}<a class="small" href={'#/chat/' + entry.conversation}>Conversation</a>{/if}</td>
            </tr>
          {:else}
            <tr><td colspan="5" class="empty">No run yet.</td></tr>
          {/each}
        </tbody>
      </table>
    </div>
  {/if}

  <div class="row">
    <button class="small" aria-expanded={!!seen} aria-controls="automation-events" onclick={toggleSeen}>{seen ? 'Hide' : 'Show'} the events seen</button>
    <span class="muted small">What the automations were offered.</span>
  </div>
  {#if seen}
    <div class="panel" id="automation-events">
      <table>
        <thead><tr><th>When</th><th>Event</th></tr></thead>
        <tbody>
          {#each seen as stored (stored.id)}
            <tr><td class="small">{when(stored.occurred_at)}</td>
              <td class="small">{stored.title}{#if stored.self_caused} <span class="tag">from my own account</span>{/if}</td></tr>
          {:else}
            <tr><td colspan="2" class="empty">Nothing yet. An automation sees only what happens after it is made.</td></tr>
          {/each}
        </tbody>
      </table>
    </div>
  {/if}
  {:else}
    <EventSources />
  {/if}
</div>

<dialog bind:this={probeBox} class="dialog frame probe" aria-labelledby="probe-title" onclose={() => (probe = null)}>
  {#if probe}
    <div class="stack">
      <div class="row"><h2 class="grow" id="probe-title">Test “{probe.item.name}”</h2><button type="button" class="small" onclick={closeProbe}>Close</button></div>
      <p class="muted small">Nothing is run or sent: this shows which branches of the trigger hold for an event or for a moment, and what Clara would be given.</p>
      <Choice legend="Test with" options={[
        ...(probe.item.kinds.length ? [{ value: 'event', label: 'An event' }] : []), { value: 'at', label: 'A moment' },
      ]} bind:value={probe.source} />
      <div class="row end">
        {#if probe.source === 'event'}
          <label class="field grow">Event
            <select bind:value={probe.pick}>
              <option value="">the latest one stored</option>
              {#each probe.events as stored (stored.id)}<option value={String(stored.id)}>{when(stored.occurred_at)} — {stored.title}</option>{/each}
            </select></label>
        {:else}
          <label class="field grow">Moment (in its time zone, {probe.item.timezone})
            <input type="datetime-local" bind:value={probe.at} /></label>
        {/if}
        <button type="button" class="primary" onclick={runProbe}>Test</button>
      </div>
      {#if probe.error}<p class="row" role="alert"><span class="tag warn"><Icon name="warn" size={14} />Cannot test</span> <span class="small">{probe.error}</span></p>{/if}
      {#if probe.result}
        <div class="row" role="status">
          {#if probe.result.matches}<span class="tag ok">It would start a run</span>{:else}<span class="tag warn">It would not start a run</span> <span class="muted small">{probe.result.why_not}</span>{/if}
          <span class="tag">{probe.result.action === 'run' ? 'runs Clara' : 'notifies'}</span>
          {#if probe.result.tainted}<span class="tag">untrusted from the start</span>{/if}
        </div>
        <ul class="verdicts" aria-label="The trigger, node by node">{@render verdictNode(probe.result.tree)}</ul>
        {#if probe.result.tools?.length}<p class="small">Tools: {probe.result.tools.join(', ')}</p>{/if}
        {#if probe.result.message}<pre class="message" aria-label="What Clara would receive">{probe.result.message}</pre>{/if}
      {/if}
    </div>
  {/if}
</dialog>

<style>
  .branch { padding: var(--sp-12); border: 1px solid var(--line); border-radius: var(--r-md); background: var(--panel); }
  .member { padding-left: var(--sp-12); border-left: 2px solid var(--line-strong); }
  .filter { padding: var(--sp-8) var(--sp-10); border: 1px solid var(--line); border-radius: var(--r-md); }
  .cond { display: flex; flex-wrap: wrap; align-items: center; gap: var(--sp-8); }
  .cond .value { flex: 1 1 calc(var(--sp-64) * 3); }
  .tool-list { max-height: calc(var(--sp-64) * 3); overflow: auto; display: grid; grid-template-columns: repeat(auto-fill, minmax(calc(var(--sp-64) * 3), 1fr)); gap: var(--sp-6) var(--sp-12); }
  .message { white-space: pre-wrap; overflow-wrap: anywhere; }
  .verdicts { list-style: none; padding-left: var(--sp-16); display: flex; flex-direction: column; gap: var(--sp-6); }
  .verdicts :global(ul) { margin-top: var(--sp-6); }
  dialog.probe { width: min(100% - var(--sp-32), calc(var(--sp-64) * 10)); max-height: 90vh; overflow: auto; }
</style>
