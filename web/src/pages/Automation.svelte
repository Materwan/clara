<script lang="ts">
  import { tick } from 'svelte';
  import Checkbox from '../components/Checkbox.svelte';
  import Choice from '../components/Choice.svelte';
  import Facts from '../components/Facts.svelte';
  import Hint from '../components/Hint.svelte';
  import Icon from '../components/Icon.svelte';
  import LevelSelect from '../components/LevelSelect.svelte';
  import ModelField from '../components/ModelField.svelte';
  import Wizard from '../components/Wizard.svelte';
  import { api, fileToBase64, when, zone } from '../lib/api';
  import { ATTEMPTS, RETRY_HELP, excerpt, inWords } from '../lib/automation';
  import { confirmDialog } from '../lib/dialog.svelte';
  import { ladder } from '../lib/levels.svelte';
  import { keptNote } from '../lib/memory';
  import { conversations, live, toast } from '../lib/state.svelte';

  // What Clara does without being asked, in one list and one form. An item is a schedule (`on` is TIME: she runs at a time) or a hook
  // (`on` is the kind of event: she runs, or tells you, when something happens outside). The server keeps them apart (`/v1/schedules`,
  // `/v1/triggers`); each form is sent to its own.
  const TIME = 'time';
  type Condition = { field: string; operator: string; value: string };
  type Problem = { step: string; field: string; message: string };

  // The schedules' choices
  const DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
  const DAYS_LONG = ['Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday'];
  const REPEATS = [
    { value: '', label: 'Once', hint: 'Runs one time, at the first run.' },
    { value: 'daily', label: 'Daily', hint: 'Every day at the time of the first run.' },
    { value: 'weekly', label: 'Weekly', hint: 'On the days you tick below; if none, on the weekday of the first run.' },
    { value: 'monthly', label: 'Monthly', hint: 'On the day of the month of the first run; the last day of a shorter month.' },
  ];
  const CONVERSATIONS = [
    { value: 'shared', label: 'One for all runs', hint: 'Every run goes on in the same conversation.' },
    { value: 'each', label: 'One per run', hint: 'Each run is a new chat in your list.' },
  ];
  const MISSED = [
    { value: 'skip', label: 'Skip what was missed', hint: 'It is recorded as missed.' },
    { value: 'latest', label: 'Run the latest missed', hint: 'One run, at the next start.' },
    { value: 'all', label: 'Run every missed one', hint: 'In turn, 31 at most.' },
  ];
  const OVERLAPS = [
    { value: 'skip', label: 'Skip the new run', hint: 'It is recorded as skipped.' },
    { value: 'replace', label: 'Close the old one, start the new', hint: 'A waiting run is closed from what it has; a running one is waited for.' },
    { value: 'queue', label: 'Queue the new run', hint: 'It starts when the old one ends.' },
  ];
  // Where the result of a run goes (`deliver`): the same four answers for a schedule and for a hook
  const DELIVERIES = [
    { value: 'notice', label: 'A short notification', hint: 'Every client gets a few lines, Discord\'s #notification included.' },
    { value: 'web', label: 'Web only', hint: 'The same few lines, without Discord.' },
    { value: 'discord', label: 'Discord conversation', hint: 'The whole answer in #general. You answer there and the conversation goes on.' },
    { value: 'both', label: 'Web and Discord', hint: 'The conversation on Discord, and the few lines on the other clients.' },
  ];
  const TALKS = ['discord', 'both'];
  // Cleaning up the conversations of its runs: each limit follows the Admin setting or is the automation's own
  const KEEP_AGE = [
    { value: 'follow', label: 'The Admin setting', hint: 'automation_keep_days and automation_keep_hours (none at first).' },
    { value: 'own', label: 'Its own age', hint: 'Deleted this long after the last message.' },
  ];
  const KEEP_COUNT = [
    { value: 'follow', label: 'The Admin setting', hint: 'automation_keep_last (none at first).' },
    { value: 'own', label: 'Its own count', hint: 'Only the latest ones are kept.' },
  ];
  const TRIGGER_TIME = { value: TIME, label: 'At a time', hint: 'Once, or every day, week or month, from the first run you give.', icon: 'clock' };

  // The hooks' choices
  const OPERATORS = [
    { value: 'contains', label: 'contains' }, { value: 'eq', label: 'is exactly' }, { value: 'starts_with', label: 'starts with' },
    { value: 'ends_with', label: 'ends with' }, { value: 'not_contains', label: 'does not contain' },
  ];
  const FIELDS: Record<string, string> = {
    from: 'Sender', from_address: 'Sender address', subject: 'Subject', snippet: 'Start of the text', account: 'Service (gmail)',
    mailbox: 'Mailbox address', has_attachment: 'Has an attachment', unread: 'Is unread',
    repo: 'Repository', action: 'What happened (opened, merged, closed)', title: 'Title', author: 'Author', branch: 'Branch',
    base: 'Base branch', draft: 'Is a draft', from_fork: 'Comes from a fork', message: 'Commit message',
    created: 'Makes a new branch', forced: 'Rewrites history',
  };
  const KINDS: Record<string, string> = { 'mail.received': 'new mail', 'github.pull_request': 'pull request', 'github.push': 'push' };
  const LONG: Record<string, string> = {
    'mail.received': 'new mail in a connected mailbox',
    'github.pull_request': 'a pull request opened, merged or closed in a GitHub repository',
    'github.push': 'a push to a branch of a GitHub repository',
  };
  const EVERY: Record<string, string> = {
    'mail.received': 'every new mail', 'github.pull_request': 'every pull request opened, merged or closed', 'github.push': 'every push',
  };
  const HINTS: Record<string, string> = {
    'mail.received': '{from}, {subject}, {snippet}, {mailbox}. Empty: the sender and the subject.',
    'github.pull_request': '{repo}, {number}, {action}, {title}, {author}, {branch}, {url}. Empty: the repository, the number, what happened, the author and the title.',
    'github.push': '{repo}, {branch}, {author}, {message}, {sha}, {url}. Empty: the repository, the branch, the author and the message.',
  };
  // The places hooks look at: what each needs, and the server setting that turns its polling off.
  const SOURCES: Record<string, { label: string; tag: string; missing: string; setting: string; reads: string; icon: string }> = {
    mail: { label: 'Mail', tag: 'No mailbox', missing: 'A hook on new mail has nothing to look at. Connect Gmail on the', setting: 'CLARA_MAIL_POLL_SECONDS', reads: 'read mail', icon: 'mail' },
    github: { label: 'GitHub', tag: 'No repository', missing: 'A hook on GitHub has nothing to look at. Connect GitHub and add a repository on the', setting: 'CLARA_GITHUB_POLL_SECONDS', reads: 'read GitHub', icon: 'branch' },
  };
  const sourceOf = (kind: string): string => kind.split('.')[0];
  const TITLES: Record<string, string> = { [TIME]: 'At a time', 'mail.received': 'New mail', 'github.pull_request': 'Pull request', 'github.push': 'Push' };
  const CATCH_UPS = [
    { value: 'all', label: 'A run for each', hint: 'At the pace of the limit of runs an hour.' },
    { value: 'digest', label: 'One run for all', hint: 'It is given the whole backlog, up to 25 events.' },
    { value: 'latest', label: 'Only the newest', hint: 'The others are recorded as skipped.' },
    { value: 'skip', label: 'Nothing', hint: 'All are recorded, none run.' },
  ];
  const ACTIONS = [
    { value: 'run', label: 'Run Clara', hint: 'She reads the event and does what you ask, with the tools you give her.', icon: 'chat' },
    { value: 'notify', label: 'Only notify me', hint: 'No model: you get the text below, filled in from the event.', icon: 'bell' },
  ];

  // The steps of both kinds, in one order; what each says depends on the kind chosen
  const STEPS = [
    { id: 'trigger', label: 'Trigger', title: 'What sets it off' },
    { id: 'action', label: 'Action', title: 'What Clara does' },
    { id: 'tools', label: 'Tools', title: 'Tools, model and privacy' },
    { id: 'runs', label: 'Runs', title: 'How runs behave' },
    { id: 'review', label: 'Review', title: 'Review', lead: '' },
  ];
  const LEADS: Record<string, { time: string; event: string }> = {
    trigger: {
      time: `The first run, and whether it comes back. Times are in your zone (${zone}).`,
      event: 'Something that happens outside: a new mail in a mailbox you connected, or a pull request or a push in a GitHub repository you added.',
    },
    action: {
      time: 'A name to find it by, and the prompt she is given at every run.',
      event: 'A name to find it by, and whether Clara is run or you are only told.',
    },
    tools: {
      time: 'The special tools you tick are the only extra ones a run has. An action your rules say to ask about is held and pushed to you, and the run goes on without it.',
      event: 'What arrives was written by someone else, so a run starts untrusted and has only the tools you give it. Anything it would change outside (send a mail, write to GitHub or Drive) asks you first.',
    },
    runs: {
      time: 'Where a run is told, and what happens when the server was off, when a run is late, or when the model fails.',
      event: 'What to do with events that pile up, how fast runs may start, and what you hear afterwards.',
    },
  };

  const empty = () => ({
    id: 0, name: '', on: TIME, action: 'run', prompt: '', conditions: [] as Condition[], toolsMode: 'default', tools: [] as string[],
    at: '', repeat: '', weekdays: [] as number[], project: 'none', local_only: false, documents: [] as any[], packs: ['web'] as string[],
    per_run: false, missed: 'skip', overlap: 'skip', model: '', catch_up: 'all', max_per_hour: 20, max_age_hours: 0, include_self: false,
    rerun_interrupted: false, attempts: 3, announce: true, deliver: 'notice',
    keepAge: 'follow', keepDays: 0, keepHours: 0, keepCount: 'follow', keepLast: 0,
  });

  let schedules = $state<any[]>([]);
  let hooks = $state<any[]>([]);
  let kinds = $state<any[]>([]);
  let projects = $state<any[]>([]);
  let packs = $state<any[]>([]);
  let models = $state<any>({ models: [], default: '' });
  let toolNames = $state<string[]>([]);
  let repos = $state<string[]>([]); // the GitHub repositories added on Connections
  let sources = $state<Record<string, { ready: boolean; poll: number }>>({ mail: { ready: true, poll: 120 }, github: { ready: true, poll: 120 } });
  let form = $state(empty());
  let open = $state(false);
  let step = $state('trigger');
  let checked = $state(false); // the last step was seen, or a save was tried: everything missing is shown where it is missing
  let tried = $state<string[]>([]); // the steps one tried to leave while something was missing: theirs is shown
  let saving = $state(false);
  let toolFilter = $state('');
  let inspect = $state<{ item: any; rows: any[] } | null>(null); // the runs of one item, when that panel is open
  let remembering = $state<any>(null); // the item whose memory is open
  let memory = $state<Record<string, { facts: number; waiting: number; total: number }>>({}); // the facts each item remembers, by scope
  let probe = $state<any>(null); // { hook, events, pick, result }
  let seen = $state<any[] | null>(null); // the events stored, when that panel is open
  let newButton = $state<HTMLButtonElement>();

  const isTime = $derived(form.on === TIME);
  // Clara runs for a schedule and for a hook that runs her; a hook that only notifies has no model and no tools
  const runsClaude = $derived(isTime || form.action === 'run');
  const rows = $derived([...schedules, ...hooks]);
  const keyOf = (item: any): string => `${item.on}-${item.id}`;
  const pathOf = (item: any): string => (item.on === TIME ? `/v1/schedules/${item.id}` : `/v1/triggers/${item.id}`);
  const scopeOf = (item: any): string => `${item.on === TIME ? 'schedule' : 'hook'}:${item.id}`;
  // Does a hook's list of tools (names or globs such as mail_*) offer this tool?
  const offers = (item: any, name: string): boolean =>
    item.effective_tools.some((pattern: string) => new RegExp(`^${pattern.replace(/[.+^${}()|[\]\\]/g, '\\$&').replace(/\*/g, '.*')}$`).test(name));
  const remembered = (item: any): { facts: number; waiting: number; total: number } =>
    memory[scopeOf(item)] ?? { facts: 0, waiting: 0, total: 0 };
  const fields = $derived<Record<string, string>>(kinds.find((kind) => kind.kind === form.on)?.fields ?? {});
  const readTools = $derived<string[]>([...(kinds.find((kind) => kind.kind === form.on)?.read_tools ?? []), 'notify']);
  const shownTools = $derived(toolNames.filter((name) => name.includes(toolFilter.trim().toLowerCase())));
  const label = (field: string): string => FIELDS[field] ?? field;
  // A source is worth a warning only to someone who has a hook on it, or is making one.
  const watched = (source: string): boolean =>
    (open && sourceOf(form.on) === source) || hooks.some((hook) => hook.enabled && sourceOf(hook.on) === source);
  const looked = $derived(
    Object.entries(SOURCES)
      .map(([key, item]) => `${item.label} ${sources[key].poll ? `is looked at every ${sources[key].poll} seconds while a hook is on` : 'is not looked at: polling is off on this server'}`)
      .join('; '),
  );
  const operatorLabel = (operator: string): string => OPERATORS.find((item) => item.value === operator)?.label ?? operator;
  const labelOf = (options: { value: string; label: string }[], value: string): string => options.find((item) => item.value === value)?.label ?? value;
  const cap = (text: string): string => `${text.charAt(0).toUpperCase()}${text.slice(1)}`;
  const up = (text: string): string => `${cap(text)}.`;
  // A repository already in a condition stays in the list, even if it was removed from Connections since
  const repoOptions = (value: string): string[] => (value && !repos.includes(value) ? [value, ...repos] : repos);
  const packLabel = (id: string): string => packs.find((pack) => pack.id === id)?.label ?? id;

  // The triggers as cards: a time, then the events, each with its source's icon and a warning when there is nothing yet to look at.
  const triggerOptions = $derived([
    TRIGGER_TIME,
    ...kinds.map((kind) => {
      const source = sourceOf(kind.kind);
      return {
        value: kind.kind, label: TITLES[kind.kind] ?? kind.kind, hint: up(LONG[kind.kind] ?? kind.label), icon: SOURCES[source]?.icon,
        tag: sources[source] && !sources[source].ready ? SOURCES[source].tag : undefined,
      };
    }),
  ]);
  // Discord is outside the machine, and a notification has no conversation to answer in
  const cannotTalk = $derived(form.local_only || (!isTime && form.action === 'notify'));
  const deliveryOptions = $derived(DELIVERIES.map((option) => ({ ...option, disabled: cannotTalk && TALKS.includes(option.value) })));
  const toolOptions = $derived([
    { value: 'default', label: 'The safe default', hint: `It can only ${SOURCES[sourceOf(form.on)]?.reads} and notify you.` },
    { value: 'choose', label: 'Choose the tools', hint: 'Give the fewest the job needs.' },
  ]);

  // What is missing, and on which step. Next does not go past a step that holds something; the server says the same in its own words.
  // A number input gives null when it is empty or not a number.
  const whole = (value: unknown, least: number, most: number): boolean => typeof value === 'number' && Number.isInteger(value) && value >= least && value <= most;
  // The conversations of its runs: a hook that runs Clara makes one per run, a schedule only when it is set to
  const makesConversations = $derived(isTime ? form.per_run : form.action === 'run');
  // null: follow the Admin setting. The age is both days and hours, or neither.
  const keepBody = (): Record<string, number | null> => ({
    keep_days: form.keepAge === 'own' ? Number(form.keepDays) : null,
    keep_hours: form.keepAge === 'own' ? Number(form.keepHours) : null,
    keep_last: form.keepCount === 'own' ? Number(form.keepLast) : null,
  });
  const keepText = (): string => {
    const age = form.keepAge === 'follow' ? 'age by the Admin setting'
      : Number(form.keepDays) + Number(form.keepHours) === 0 ? 'no age limit'
      : `deleted ${[Number(form.keepDays) ? `${form.keepDays} day${Number(form.keepDays) === 1 ? '' : 's'}` : '', Number(form.keepHours) ? `${form.keepHours} h` : ''].filter(Boolean).join(' ')} after the last message`;
    const count = form.keepCount === 'follow' ? 'count by the Admin setting' : Number(form.keepLast) === 0 ? 'no count limit' : `the latest ${form.keepLast} kept`;
    return `${cap(age)}; ${count}.`;
  };
  // What the next pass would delete for this automation with the limits on the form (an automation not saved yet has nothing to delete)
  let keepPreview = $state<{ would_delete: number; more: number } | null>(null);
  $effect(() => {
    keepPreview = null;
    if (!open || !form.id || !makesConversations || step !== 'runs') return;
    const params: Record<string, number | string> = { kind: isTime ? 'schedule' : 'hook', id: form.id };
    const own = keepBody();
    for (const [key, value] of Object.entries(own)) if (value !== null) params[key] = value;
    let stale = false; // the limits changed again: an answer that comes late is not the one to show
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

  const problems = $derived.by<Problem[]>(() => {
    const found: Problem[] = [];
    if (isTime && !form.at) found.push({ step: 'trigger', field: 'af-at', message: 'Choose when it first runs.' });
    if (!isTime) {
      form.conditions.forEach((condition, index) => {
        if (fields[condition.field] !== 'bool' && !condition.value.split(',').some((item) => item.trim())) {
          found.push({ step: 'trigger', field: `af-cond-${index}`, message: `The condition on “${label(condition.field)}” needs a value.` });
        }
      });
      if (!found.length) {
        try {
          filterOf(form.conditions);
        } catch (error) {
          found.push({ step: 'trigger', field: 'af-add', message: (error as Error).message });
        }
      }
    }
    if (!form.name.trim()) found.push({ step: 'action', field: 'af-name', message: 'Give it a name.' });
    if (isTime && !form.prompt.trim() && !form.documents.length) {
      found.push({ step: 'action', field: 'af-prompt', message: 'Say what Clara should do, or add a document.' });
    } else if (!isTime && form.action === 'run' && !form.prompt.trim()) {
      found.push({ step: 'action', field: 'af-prompt', message: 'Say what Clara should do.' });
    }
    if (cannotTalk && TALKS.includes(form.deliver)) {
      found.push({
        step: 'runs', field: 'af-deliver',
        message: form.local_only ? 'Discord is outside the machine: a local-only automation cannot deliver there.' : 'A notification has no conversation to answer in.',
      });
    }
    if (!isTime && !whole(form.max_age_hours, 0, 8760)) {
      found.push({ step: 'runs', field: 'af-max-age', message: 'Hours a run may wait must be a whole number from 0 to 8760.' });
    }
    if (!isTime && !whole(form.max_per_hour, 1, 600)) {
      found.push({ step: 'runs', field: 'af-max-hour', message: 'Runs an hour must be a whole number from 1 to 600.' });
    }
    if (makesConversations && form.keepAge === 'own') {
      if (!whole(form.keepDays, 0, 3650)) found.push({ step: 'runs', field: 'af-keep-days', message: 'Days must be a whole number from 0 to 3650.' });
      if (!whole(form.keepHours, 0, 8760)) found.push({ step: 'runs', field: 'af-keep-hours', message: 'Hours must be a whole number from 0 to 8760.' });
    }
    if (makesConversations && form.keepCount === 'own' && !whole(form.keepLast, 0, 10000)) {
      found.push({ step: 'runs', field: 'af-keep-last', message: 'The number of conversations kept must be a whole number from 0 to 10000.' });
    }
    return found;
  });
  const shown = (stepId: string): boolean => checked || tried.includes(stepId);
  const errorOf = (field: string): string | undefined => {
    const found = problems.find((item) => item.field === field);
    return found && shown(found.step) ? found.message : undefined;
  };
  // A notification has no model and no tools: its form has no step for them.
  const steps = $derived(
    STEPS.filter((item) => item.id !== 'tools' || runsClaude).map((item) => {
      const missing = problems.filter((p) => p.step === item.id).map((p) => p.message);
      const lead = LEADS[item.id]?.[isTime ? 'time' : 'event'] ?? '';
      return { ...item, lead, missing, problems: shown(item.id) ? missing.length : 0 };
    }),
  );
  const conditionsText = $derived.by<string>(() => {
    try {
      return describeFilter(filterOf(form.conditions), form.on);
    } catch {
      return ''; // a condition is not finished: the problems say which
    }
  });
  const toolsText = $derived(form.toolsMode === 'choose' && form.tools.length ? form.tools.join(', ') : readTools.join(', '));
  // The first run and the repeat of a schedule, as a sentence.
  const timing = $derived.by<string>(() => {
    if (!form.at) return '';
    const first = new Date(form.at);
    const time = first.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    const day = first.toLocaleDateString([], { weekday: 'long', day: 'numeric', month: 'long', year: 'numeric' });
    if (form.repeat === 'daily') return `Every day at ${time}, from ${day}.`;
    if (form.repeat === 'weekly') {
      const days = form.weekdays.length ? [...form.weekdays].sort() : [(first.getDay() + 6) % 7];
      return `Every ${inWords(days.map((index) => DAYS_LONG[index]))} at ${time}, from ${day}.`;
    }
    if (form.repeat === 'monthly') return `Every month on day ${first.getDate()} at ${time} (the last day of a shorter month), from ${day}.`;
    return `Once, on ${day} at ${time}.`;
  });

  $effect(() => {
    if (step === 'review') checked = true;
  });

  async function load(): Promise<void> {
    schedules = (await api.get('/v1/schedules')).schedules.map((schedule: any) => ({ ...schedule, on: TIME }));
    const data = await api.get('/v1/triggers');
    hooks = data.triggers;
    kinds = data.kinds;
    projects = (await api.get('/v1/projects')).projects;
    packs = (await api.get('/v1/packs', { where: 'schedule' })).packs;
    models = await api.get('/v1/models').catch(() => ({ models: [], default: '' }));
    const connections = await api.get('/v1/connections').catch(() => null);
    if (connections) {
      repos = connections.github.repos.map((repo: any) => repo.locator);
      sources = {
        mail: { ready: connections.gmail.connected, poll: connections.mail.poll_seconds },
        github: { ready: connections.github.connected && connections.github.repos.length > 0, poll: connections.github.poll_seconds },
      };
    }
    await loadMemory();
    if (inspect) await showRuns(inspect.item);
    if (seen) seen = (await api.get('/v1/trigger-events', { limit: 30 })).events;
  }

  async function loadMemory(): Promise<void> {
    memory = (await api.get('/v1/memory/scopes').catch(() => ({ scopes: {} }))).scopes;
  }

  $effect(() => {
    void live.refresh;
    load();
  });

  // A run of a hook starts and ends without a word on the event stream: while one is going or waiting, look again from time to time.
  $effect(() => {
    if (!hooks.some((hook) => hook.running || hook.queued)) return;
    const timer = setInterval(load, 5000);
    return () => clearInterval(timer);
  });

  // ------------------------------------------------------------------ the form
  async function openForm(): Promise<void> {
    open = true;
    step = 'trigger';
    checked = false;
    tried = [];
    if (!toolNames.length) toolNames = (await api.get('/v1/rules')).tools;
    await tick();
    (document.querySelector('#automation-form .step-head h3') as HTMLElement | null)?.focus();
  }

  async function closeForm(): Promise<void> {
    open = false;
    toolFilter = '';
    form = empty();
    await tick();
    newButton?.focus();
  }

  async function jump(problem: Problem): Promise<void> {
    step = problem.step;
    await tick();
    document.getElementById(problem.field)?.focus();
  }

  // Next, Enter or a later step was asked for while this one holds something missing: show it and go to the field
  async function blockedAt(stepId: string): Promise<void> {
    if (!tried.includes(stepId)) tried = [...tried, stepId];
    const found = problems.find((item) => item.step === stepId);
    if (found) await jump(found);
  }

  function conditionsOf(filter: Record<string, any>): Condition[] {
    const rows: Condition[] = [];
    for (const [field, spec] of Object.entries(filter)) {
      if (typeof spec.eq === 'boolean') rows.push({ field, operator: 'eq', value: String(spec.eq) });
      else for (const [operator, values] of Object.entries(spec)) rows.push({ field, operator, value: (values as string[]).join(', ') });
    }
    return rows;
  }

  function filterOf(conditions: Condition[]): Record<string, any> {
    const filter: Record<string, any> = {};
    for (const condition of conditions) {
      if (fields[condition.field] === 'bool') {
        if (condition.field in filter) throw new Error(`“${label(condition.field)}” is there twice.`);
        filter[condition.field] = { eq: condition.value === 'true' };
        continue;
      }
      const values = condition.value.split(',').map((item) => item.trim()).filter(Boolean);
      if (!values.length) throw new Error(`The condition on “${label(condition.field)}” needs a value.`);
      const spec = (filter[condition.field] ??= {});
      if (condition.operator in spec) {
        throw new Error(`“${label(condition.field)} ${operatorLabel(condition.operator)}” is there twice: put the alternatives in one row, separated by commas.`);
      }
      spec[condition.operator] = values;
    }
    return filter;
  }

  function addCondition(): void {
    const first = ['subject', 'title', 'message'].find((field) => field in fields) ?? (Object.keys(fields).find((field) => fields[field] === 'text') ?? Object.keys(fields)[0] ?? 'subject');
    form.conditions.push({ field: first, operator: 'contains', value: '' });
  }

  // A repository is picked from the ones added, and compared as a whole: “is exactly”
  function fieldChanged(condition: Condition): void {
    condition.operator = condition.field === 'repo' ? 'eq' : 'contains';
    condition.value = fields[condition.field] === 'bool' ? 'true' : '';
  }

  const toggleTool = (name: string): void => {
    form.tools = form.tools.includes(name) ? form.tools.filter((item) => item !== name) : [...form.tools, name];
  };

  const toggleDay = (index: number): void => {
    form.weekdays = form.weekdays.includes(index) ? form.weekdays.filter((day) => day !== index) : [...form.weekdays, index];
  };

  const togglePack = (id: string): void => {
    form.packs = form.packs.includes(id) ? form.packs.filter((item) => item !== id) : [...form.packs, id];
  };

  async function addDocuments(event: Event): Promise<void> {
    for (const file of (event.target as HTMLInputElement).files ?? []) {
      const extracted = await api.post('/v1/documents/extract', { name: file.name, data: await fileToBase64(file) });
      form.documents.push({ name: file.name, text: extracted.text });
    }
  }

  async function edit(item: any): Promise<void> {
    const keep = {
      keepAge: item.keep_days === null ? 'follow' : 'own', keepDays: item.keep_days ?? 0, keepHours: item.keep_hours ?? 0,
      keepCount: item.keep_last === null ? 'follow' : 'own', keepLast: item.keep_last ?? 0,
    };
    form = item.on === TIME
      ? {
        ...empty(), id: item.id, name: item.name, prompt: item.prompt, at: item.local_at, repeat: item.repeat, weekdays: [...item.weekdays],
        project: item.project ? String(item.project) : 'none', local_only: item.local_only,
        documents: item.documents.map((doc: any) => ({ name: doc.name })), packs: [...item.packs], per_run: item.per_run, missed: item.missed,
        overlap: item.overlap, model: item.model, attempts: item.attempts, deliver: item.deliver, ...keep,
      }
      : {
        ...empty(), id: item.id, on: item.on, name: item.name, action: item.action, prompt: item.prompt, conditions: conditionsOf(item.filter),
        toolsMode: item.tools.length ? 'choose' : 'default', tools: [...item.tools], project: item.project ? String(item.project) : 'none',
        model: item.model, local_only: item.local_only, catch_up: item.catch_up, max_per_hour: item.max_per_hour,
        max_age_hours: item.max_age_hours, include_self: item.include_self, rerun_interrupted: item.rerun_interrupted,
        attempts: item.attempts, announce: item.announce, deliver: item.deliver, ...keep,
      };
    await openForm();
  }

  async function save(): Promise<void> {
    if (problems.length) {
      checked = true;
      await jump(problems[0]);
      return;
    }
    saving = true;
    try {
      if (isTime) {
        const body = {
          name: form.name, prompt: form.prompt, at: form.at, timezone: zone, repeat: form.repeat, weekdays: form.weekdays,
          project: form.project === 'none' ? null : Number(form.project), local_only: form.local_only, documents: form.documents,
          packs: form.packs, per_run: form.per_run, missed: form.missed, overlap: form.overlap, model: form.model, attempts: form.attempts,
          deliver: form.deliver, ...keepBody(),
        };
        if (form.id) await api.patch(`/v1/schedules/${form.id}`, body);
        else await api.post('/v1/schedules', body);
      } else {
        const body: Record<string, any> = {
          name: form.name, filter: filterOf(form.conditions), action: form.action, prompt: form.prompt,
          tools: form.action === 'run' && form.toolsMode === 'choose' ? form.tools : [], model: form.model, local_only: form.local_only,
          catch_up: form.catch_up, max_per_hour: Number(form.max_per_hour), max_age_hours: Number(form.max_age_hours),
          include_self: form.include_self, rerun_interrupted: form.rerun_interrupted, attempts: Number(form.attempts), announce: form.announce,
          deliver: form.deliver, project: form.project === 'none' ? 'none' : Number(form.project), ...keepBody(),
        };
        if (form.id) await api.patch(`/v1/triggers/${form.id}`, body);
        else await api.post('/v1/triggers', { ...body, on: form.on });
      }
      conversations.changed++;
      await closeForm();
      load();
    } catch (error) {
      toast((error as Error).message, 'error');
    } finally {
      saving = false;
    }
  }

  // ------------------------------------------------------------------ the list
  function describeFilter(filter: Record<string, any>, kind: string): string {
    const parts = Object.entries(filter).flatMap(([field, spec]) =>
      typeof spec.eq === 'boolean'
        ? [`${label(field).toLowerCase()}: ${spec.eq ? 'yes' : 'no'}`]
        : Object.entries(spec).map(([operator, values]) => `${label(field).toLowerCase()} ${operatorLabel(operator)} ${(values as string[]).map((value) => `“${value}”`).join(' or ')}`));
    return parts.join(' · ') || (EVERY[kind] ?? 'every event');
  }

  async function run(item: any): Promise<void> {
    try {
      await api.post(`/v1/schedules/${item.id}/run`);
      toast('Started: the summary arrives when it ends.', 'ok');
      load();
    } catch (error) {
      toast((error as Error).message, 'error');
    }
  }

  async function close(item: any): Promise<void> {
    try {
      await api.post(`/v1/schedules/${item.id}/close`);
      toast('Closing it from what Clara has: the summary arrives when it ends.', 'ok');
      load();
    } catch (error) {
      toast((error as Error).message, 'error');
    }
  }

  /** The auto level of a schedule's or a hook's runs (null: its project's, else the default of the runs nobody watches). */
  async function setLevel(item: any, value: number | null): Promise<void> {
    try {
      await api.put(`${pathOf(item)}/level`, { level: value });
    } catch (error) {
      toast((error as Error).message, 'error');
    }
    load();
  }

  async function toggle(item: any): Promise<void> {
    try {
      await api.patch(pathOf(item), { enabled: !item.enabled });
    } catch (error) {
      toast((error as Error).message, 'error');
    }
    load();
  }

  async function remove(item: any): Promise<void> {
    const timed = item.on === TIME;
    const message =
      (timed
        ? `Delete “${item.name}”?`
        : `Delete “${item.name}”? Its runs are forgotten; the conversations they made stay, no longer grouped under the hook.`) +
      keptNote(remembered(item));
    if (!(await confirmDialog({ title: timed ? 'Delete schedule' : 'Delete hook', message, confirm: 'Delete', danger: true }))) return;
    await api.del(pathOf(item));
    if (inspect && keyOf(inspect.item) === keyOf(item)) inspect = null;
    if (remembering && keyOf(remembering) === keyOf(item)) remembering = null;
    if (probe && keyOf(probe.hook) === keyOf(item)) probe = null;
    load();
  }

  async function inHistory(item: any): Promise<void> {
    try {
      await api.patch(pathOf(item), { in_history: !item.in_history });
      conversations.changed++;
      load();
    } catch (error) {
      toast((error as Error).message, 'error');
    }
  }

  async function showRuns(item: any): Promise<void> {
    inspect = {
      item,
      rows: item.on === TIME ? (await api.get(`/v1/schedules/${item.id}`)).history : (await api.get(`/v1/triggers/${item.id}/runs`)).runs,
    };
  }

  // ------------------------------------------------------------------ test and events
  async function openProbe(hook: any): Promise<void> {
    const stored = (await api.get('/v1/trigger-events', { limit: 30, kind: hook.on })).events;
    probe = { hook, events: stored, pick: '', result: null, error: '' };
  }

  async function runProbe(): Promise<void> {
    if (!probe) return;
    try {
      const body = probe.pick ? { event: Number(probe.pick) } : {};
      probe.result = await api.post(`/v1/triggers/${probe.hook.id}/test`, body);
      probe.error = '';
    } catch (error) {
      probe.result = null;
      probe.error = (error as Error).message;
    }
  }

  async function toggleSeen(): Promise<void> {
    seen = seen ? null : (await api.get('/v1/trigger-events', { limit: 30 })).events;
  }

  const tone = (status: string): string => (status === 'ok' ? 'ok' : status === 'failed' ? 'danger' : status === 'interrupted' ? 'warn' : '');
</script>

<div class="page stack">
  <div class="row start">
    <p class="intro grow">Clara does things by herself, unattended: at a time you set, or when something happens outside, a new mail
      in a mailbox you connected, or a pull request or a push in a GitHub repository you added. A run that would change something
      outside (send a mail, write to GitHub or Drive) asks you first, and an action your rules say to ask about is held and pushed to
      you. A run has only the tools you give it.</p>
    <button class="primary" aria-expanded={open} aria-controls="automation-form" bind:this={newButton}
      onclick={() => (open ? closeForm() : openForm())}><Icon name="plus" size={18} />New automation</button>
  </div>
  {#each Object.entries(SOURCES) as [source, item] (source)}
    {#if watched(source) && !sources[source].ready}
      <p class="row" role="status"><span class="tag warn"><Icon name="warn" size={14} />{item.tag}</span>
        <span class="small">{item.missing} <a href="#/connections">Connections</a> page.</span></p>
    {:else if watched(source) && sources[source].poll === 0}
      <p class="row" role="status"><span class="tag warn"><Icon name="warn" size={14} />{item.label} polling is off</span>
        <span class="small"><code>{item.setting}</code> is 0 on the server, so no hook will start.</span></p>
    {/if}
  {/each}
  {#if open}
  <Wizard id="automation-form" label={form.id ? `Change ${form.name}` : 'New automation'} {steps} bind:step editing={!!form.id} busy={saving}
    submitLabel={form.id ? 'Save' : 'Create'} onsubmit={save} oncancel={closeForm} onblocked={blockedAt}>
    {#snippet children(stepId)}
      {#if stepId === 'trigger'}
        <Choice cards legend="What sets it off" options={triggerOptions} bind:value={form.on} disabled={!!form.id}
          help={form.id ? 'The trigger cannot be changed: make another automation for another trigger.' : undefined}
          onchange={() => (form.conditions = [])} />
        {#if isTime}
          <label class="field narrow">First run
            <span class="hint">Your local time; summer time is followed.</span>
            <input id="af-at" type="datetime-local" bind:value={form.at} aria-invalid={errorOf('af-at') ? 'true' : undefined}
              aria-describedby={errorOf('af-at') ? 'af-at-error' : undefined} />
            {#if errorOf('af-at')}<span class="field-error" id="af-at-error">{errorOf('af-at')}</span>{/if}
          </label>
          <Choice legend="Repeat" options={REPEATS} bind:value={form.repeat} />
          {#if form.repeat === 'weekly'}
            <fieldset class="choice">
              <legend>On these days</legend>
              <div class="segmented" role="group" aria-label="On these days">
                {#each DAYS as day, index}
                  <button type="button" aria-pressed={form.weekdays.includes(index)} onclick={() => toggleDay(index)}>{day}</button>
                {/each}
              </div>
            </fieldset>
          {/if}
          {#if timing}<p class="muted" aria-live="polite">{timing}</p>{/if}
        {:else}
          <section class="block stack">
            <h4>Only when</h4>
            <p>{form.conditions.length ? 'Every condition must hold.' : `No condition: ${EVERY[form.on] ?? 'every event'} starts it.`}</p>
            {#if form.conditions.length}
              <div class="conds">
                {#each form.conditions as condition, index}
                  <div class="cond" role="group" aria-label={`Condition ${index + 1}`}>
                    <span class="cond-join" aria-hidden="true">{index === 0 ? 'If' : 'and'}</span>
                    <select aria-label="Field" bind:value={condition.field} onchange={() => fieldChanged(condition)}>
                      {#each Object.keys(fields) as field}<option value={field}>{label(field)}</option>{/each}
                    </select>
                    {#if fields[condition.field] === 'bool'}
                      <div class="segmented" role="group" aria-label="Value">
                        <button type="button" aria-pressed={condition.value === 'true'} onclick={() => (condition.value = 'true')}>Yes</button>
                        <button type="button" aria-pressed={condition.value !== 'true'} onclick={() => (condition.value = 'false')}>No</button>
                      </div>
                    {:else}
                      <select aria-label="Comparison" bind:value={condition.operator}>
                        {#each OPERATORS as operator}<option value={operator.value}>{operator.label}</option>{/each}
                      </select>
                      {#if condition.field === 'repo' && repos.length}
                        <select id={`af-cond-${index}`} class="grow value" aria-label="Repository" bind:value={condition.value}
                          aria-invalid={errorOf(`af-cond-${index}`) ? 'true' : undefined}>
                          <option value="" disabled>Choose a repository</option>
                          {#each repoOptions(condition.value) as repo}<option value={repo}>{repo}</option>{/each}
                        </select>
                      {:else}
                        <input id={`af-cond-${index}`} class="grow value" aria-label="Text to compare with" placeholder="invoice, facture  (a comma means “or”)"
                          bind:value={condition.value} aria-invalid={errorOf(`af-cond-${index}`) ? 'true' : undefined} />
                      {/if}
                    {/if}
                    <button type="button" class="icon-btn" aria-label={`Remove condition ${index + 1}`} onclick={() => form.conditions.splice(index, 1)}><Icon name="close" size={16} /></button>
                  </div>
                  {#if errorOf(`af-cond-${index}`)}<span class="field-error">{errorOf(`af-cond-${index}`)}</span>{/if}
                {/each}
              </div>
            {/if}
            <div class="row">
              <button type="button" class="small" id="af-add" onclick={addCondition}><Icon name="plus" size={14} />Add a condition</button>
              {#if errorOf('af-add')}<span class="field-error">{errorOf('af-add')}</span>{/if}
            </div>
            {#if form.conditions.length && conditionsText}<p class="muted" aria-live="polite">It starts when {conditionsText}.</p>{/if}
            <p class="muted small">Capitals do not matter. A sender's name and address can be forged, so a filter on them is a convenience, not a proof.</p>
          </section>
        {/if}
      {:else if stepId === 'action'}
        <label class="field">Name
          <input id="af-name" bind:value={form.name} maxlength="100" aria-invalid={errorOf('af-name') ? 'true' : undefined}
            aria-describedby={errorOf('af-name') ? 'af-name-error' : undefined} />
          {#if errorOf('af-name')}<span class="field-error" id="af-name-error">{errorOf('af-name')}</span>{/if}
        </label>
        {#if !isTime}<Choice cards legend="Then" options={ACTIONS} bind:value={form.action} />{/if}
        <label class="field">{runsClaude ? 'What Clara should do' : 'The notification'}
          {#if !runsClaude}<span class="hint">Use {HINTS[form.on] ?? ''}</span>{/if}
          <textarea id="af-prompt" rows={runsClaude ? 6 : 3} bind:value={form.prompt} aria-invalid={errorOf('af-prompt') ? 'true' : undefined}
            aria-describedby={errorOf('af-prompt') ? 'af-prompt-error' : undefined}></textarea>
          {#if errorOf('af-prompt')}<span class="field-error" id="af-prompt-error">{errorOf('af-prompt')}</span>{/if}
        </label>
        {#if isTime}
          <div class="grid-2">
            <label class="field">Project
              <span class="hint">Its files and instructions are context for every run.</span>
              <select bind:value={form.project}>
                <option value="none">no project</option>
                {#each projects as project}<option value={String(project.id)}>{project.name}</option>{/each}
              </select></label>
            <div class="field">
              <span class="field-title">Documents</span>
              <span class="hint">Their text goes with the prompt at every run.</span>
              <div><label class="button small"><Icon name="upload" size={16} />Add documents<input class="sr-only" type="file" multiple onchange={addDocuments} /></label></div>
            </div>
          </div>
          {#if form.documents.length}
            <div class="chips">{#each form.documents as doc, index}<span class="chip"><Icon name="file" size={14} /><span class="name">{doc.name}</span><button type="button" aria-label={`Remove ${doc.name}`} onclick={() => form.documents.splice(index, 1)}><Icon name="close" size={14} /></button></span>{/each}</div>
          {/if}
        {:else}
          <label class="field narrow">Project of the runs
            <span class="hint">Its files and instructions are context for every run. The runs are listed under the hook either way.</span>
            <select bind:value={form.project}>
              <option value="none">no project</option>
              {#each projects as project}<option value={String(project.id)}>{project.name}</option>{/each}
            </select></label>
        {/if}
      {:else if stepId === 'tools'}
        {#if isTime}
          <section class="block stack">
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
          </section>
        {:else}
          <Choice cards legend="Tools of the run" options={toolOptions} bind:value={form.toolsMode} />
          {#if form.toolsMode === 'default'}
            <p class="muted small">Exactly: {readTools.join(', ')}.</p>
          {:else}
            <input type="search" aria-label="Filter the tools" placeholder="filter the list" bind:value={toolFilter} />
            <div class="tool-list" role="group" aria-label="Tools to choose from">
              {#each shownTools as name (name)}
                <label class="small row"><Checkbox checked={form.tools.includes(name)} onchange={() => toggleTool(name)} />{name}</label>
              {/each}
            </div>
            <p class="muted small">{form.tools.length} chosen{form.tools.length ? '' : ': with none, the safe default applies'}. Give the fewest that the job needs: a hostile message or pull request can steer a run only within them.
              Whatever changes something outside still asks you.</p>
          {/if}
        {/if}
        <section class="block stack">
          <h4>Model</h4>
          <ModelField bind:value={form.model} {models} />
        </section>
        <section class="block stack">
          <h4>Privacy</h4>
          <label class="option"><Checkbox bind:checked={form.local_only} />
            <span class="option-text"><strong>Local only</strong><span>Local models only; nothing leaves your machines.</span></span></label>
        </section>
      {:else if stepId === 'runs'}
        {#snippet cleaning()}
          {#if makesConversations}
            <section class="block stack">
              <h4>Cleaning up</h4>
              <p class="hint">The conversations of its runs are deleted for good when they pass a limit, whichever comes first. A pinned one, one you archived and a run still waiting are kept. Both limits are off until one is set.</p>
              <Choice legend="Age" options={KEEP_AGE} bind:value={form.keepAge} />
              {#if form.keepAge === 'own'}
                <div class="row">
                  <label class="field narrow">Days
                    <input id="af-keep-days" type="number" min="0" max="3650" bind:value={form.keepDays} aria-invalid={errorOf('af-keep-days') ? 'true' : undefined} />
                    {#if errorOf('af-keep-days')}<span class="field-error">{errorOf('af-keep-days')}</span>{/if}
                  </label>
                  <label class="field narrow">Hours
                    <input id="af-keep-hours" type="number" min="0" max="8760" bind:value={form.keepHours} aria-invalid={errorOf('af-keep-hours') ? 'true' : undefined} />
                    {#if errorOf('af-keep-hours')}<span class="field-error">{errorOf('af-keep-hours')}</span>{/if}
                  </label>
                </div>
                <p class="small muted">Counted from the last message. 0 days and 0 hours: never deleted for its age.</p>
              {/if}
              <Choice legend="Count" options={KEEP_COUNT} bind:value={form.keepCount} />
              {#if form.keepCount === 'own'}
                <label class="field narrow">Conversations kept
                  <span class="hint">The latest ones. 0: no count limit.</span>
                  <input id="af-keep-last" type="number" min="0" max="10000" bind:value={form.keepLast} aria-invalid={errorOf('af-keep-last') ? 'true' : undefined} />
                  {#if errorOf('af-keep-last')}<span class="field-error">{errorOf('af-keep-last')}</span>{/if}
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
            </section>
          {/if}
        {/snippet}
        {#snippet delivery()}
          <div id="af-deliver">
            <Choice cards legend="Where the result goes" options={deliveryOptions} bind:value={form.deliver}
              help={cannotTalk ? 'Discord is not offered here: it is outside the machine, and a notification has no conversation to answer in.' : undefined} />
            {#if errorOf('af-deliver')}<span class="field-error">{errorOf('af-deliver')}</span>{/if}
          </div>
        {/snippet}
        {#if isTime}
          {@render delivery()}
          <Choice legend="Conversation" options={CONVERSATIONS}
            bind:value={() => (form.per_run ? 'each' : 'shared'), (value) => (form.per_run = value === 'each')} />
          <Choice cards legend="If the server was off" help="A run more than an hour late counts as missed." options={MISSED} bind:value={form.missed} />
          <Choice cards legend="If the last run is not over" help="A run comes due while the previous one still runs or waits." options={OVERLAPS} bind:value={form.overlap} />
          <Choice legend="If the model is busy or unreachable" help={RETRY_HELP} options={ATTEMPTS}
            bind:value={() => String(form.attempts), (value) => (form.attempts = Number(value))} />
          {@render cleaning()}
        {:else}
          <section class="block stack">
            <Choice cards legend="If it happened while the server was off" help="Events found more than 15 minutes after they happened." options={CATCH_UPS} bind:value={form.catch_up} />
            <label class="field narrow">Expire a run that waited more than (hours)
              <span class="hint">0: never.</span>
              <input id="af-max-age" type="number" min="0" max="8760" bind:value={form.max_age_hours} aria-invalid={errorOf('af-max-age') ? 'true' : undefined}
                aria-describedby={errorOf('af-max-age') ? 'af-max-age-error' : undefined} />
              {#if errorOf('af-max-age')}<span class="field-error" id="af-max-age-error">{errorOf('af-max-age')}</span>{/if}
            </label>
          </section>
          <section class="block stack">
            <h4>Pace</h4>
            <label class="field narrow">Runs an hour, at most
              <span class="hint">One run of a hook at a time.</span>
              <input id="af-max-hour" type="number" min="1" max="600" bind:value={form.max_per_hour} aria-invalid={errorOf('af-max-hour') ? 'true' : undefined}
                aria-describedby={errorOf('af-max-hour') ? 'af-max-hour-error' : undefined} />
              {#if errorOf('af-max-hour')}<span class="field-error" id="af-max-hour-error">{errorOf('af-max-hour')}</span>{/if}
            </label>
            {#if form.action === 'run'}
              <Choice legend="If the model is busy or unreachable" help={RETRY_HELP} options={ATTEMPTS}
                bind:value={() => String(form.attempts), (value) => (form.attempts = Number(value))} />
            {/if}
          </section>
          <section class="block stack">
            <h4>Afterwards</h4>
            {@render delivery()}
            <label class="option"><Checkbox bind:checked={form.announce} />
              <span class="option-text"><strong>Tell me when a run ends</strong><span>A good run stays quiet when this is off; a failed one always tells.</span></span></label>
            {#if form.action === 'run'}
              <label class="option"><Checkbox bind:checked={form.rerun_interrupted} />
                <span class="option-text"><strong>Repeat a run the server stopped half way</strong><span>Normally it is not repeated: it may have done part of its work.</span></span></label>
            {/if}
            {#if form.on === 'mail.received'}
              <label class="option"><Checkbox bind:checked={form.include_self} />
                <span class="option-text"><strong>Also for mail from my own account</strong><span>Mail sent from Clara's own account is left out, so a hook cannot answer its own answer.</span></span></label>
            {/if}
          </section>
          {@render cleaning()}
        {/if}
      {:else}
        {#if problems.length}
          <div class="stack">
            <p class="row"><span class="tag danger"><Icon name="warn" size={14} />{problems.length} to fix before it can be {form.id ? 'saved' : 'created'}</span></p>
            <ul class="problems">{#each problems as problem}<li><button type="button" class="link" onclick={() => jump(problem)}>{problem.message}</button></li>{/each}</ul>
          </div>
        {/if}
        <dl class="facts">
          <dt>Name</dt><dd>{form.name.trim() || '—'}</dd>
          {#if isTime}
            <dt>When</dt><dd>{timing || '—'}</dd>
          {:else}
            <dt>Starts</dt>
            <dd>{TITLES[form.on] ?? form.on}<br /><span class="muted">{form.conditions.length ? (conditionsText ? cap(`when ${conditionsText}`) : 'The conditions are not complete.') : cap(EVERY[form.on] ?? 'every event')}</span></dd>
            <dt>Then</dt><dd>{labelOf(ACTIONS, form.action)}</dd>
          {/if}
          <dt>{runsClaude ? 'What Clara does' : 'The notification'}</dt>
          <dd>{excerpt(form.prompt) || '—'}{#if isTime && form.documents.length}<br /><span class="muted">With {inWords(form.documents.map((doc: any) => doc.name))}.</span>{/if}</dd>
          {#if isTime}
            <dt>Tools</dt><dd>{form.packs.length ? inWords(form.packs.map(packLabel)) : 'None'}</dd>
          {:else if form.action === 'run'}
            <dt>Tools</dt><dd>{toolsText}</dd>
          {/if}
          <dt>Project</dt><dd>{projects.find((project) => String(project.id) === form.project)?.name ?? 'No project'}</dd>
          {#if runsClaude}
            <dt>Model</dt><dd>{form.model || `Default model (${models.default || 'none'})`}{#if form.local_only}<br /><span class="muted">Local models only.</span>{/if}</dd>
          {/if}
          {#if isTime}
            <dt>Conversation</dt><dd>{labelOf(CONVERSATIONS, form.per_run ? 'each' : 'shared')}</dd>
            <dt>If the server was off</dt><dd>{labelOf(MISSED, form.missed)}</dd>
            <dt>If the last run is not over</dt><dd>{labelOf(OVERLAPS, form.overlap)}</dd>
            <dt>If the model fails</dt><dd>{labelOf(ATTEMPTS, String(form.attempts))}</dd>
            <dt>Result</dt><dd>{labelOf(DELIVERIES, form.deliver)}</dd>
            {#if form.per_run}<dt>Cleaning up</dt><dd>{keepText()}</dd>{/if}
          {:else}
            <dt>If the server was off</dt><dd>{labelOf(CATCH_UPS, form.catch_up)}</dd>
            <dt>Pace</dt>
            <dd>At most {form.max_per_hour} an hour{#if Number(form.max_age_hours) > 0}; a run that waited more than {form.max_age_hours} hours expires{/if}.</dd>
            {#if form.action === 'run'}<dt>If the model fails</dt><dd>{labelOf(ATTEMPTS, String(form.attempts))}</dd>{/if}
            <dt>Result</dt><dd>{labelOf(DELIVERIES, form.deliver)}</dd>
            <dt>When a run ends</dt><dd>{form.announce ? 'You are told.' : 'Quiet if it went well; a failed one tells you.'}</dd>
            {#if form.action === 'run'}<dt>Cleaning up</dt><dd>{keepText()}</dd>{/if}
          {/if}
        </dl>
      {/if}
    {/snippet}
  </Wizard>
  {/if}
  <table>
    <thead><tr><th>Automation</th><th>Last run</th><th><span class="sr-only">Actions</span></th></tr></thead>
    <tbody>
      {#each rows as item (keyOf(item))}
        {@const timed = item.on === TIME}
        <tr>
          <td>
            <strong>{item.name}</strong>
            {#if timed}
              <span class="tag"><Icon name="clock" size={14} />{item.repeat || 'once'}</span>
            {:else}
              <span class="tag"><Icon name={SOURCES[sourceOf(item.on)]?.icon ?? 'bolt'} size={14} />{KINDS[item.on] ?? item.on}</span>
              <span class="tag">{item.action === 'run' ? 'runs Clara' : 'notifies'}</span>
            {/if}
            {#if !item.enabled}<span class="tag">paused</span>{/if}{#if item.running}<span class="tag ok">running</span>{/if}
            {#if item.queued}<span class="tag" class:warn={!timed}>{item.queued} {timed ? 'queued' : 'waiting'}</span>{/if}
            {#if item.local_only}<span class="tag"><Icon name="home" size={14} />local only</span>{/if}
            {#if remembered(item).waiting}<span class="tag warn">{remembered(item).waiting} {remembered(item).waiting === 1 ? 'fact waits' : 'facts wait'} for you</span>{/if}
            {#if TALKS.includes(item.deliver)}<span class="tag"><Icon name="chat" size={14} />on Discord</span>{/if}
            {#if timed}
              {#if item.waiting_run}<span class="tag warn">waiting for you</span>{/if}
              {#if item.per_run}<span class="tag">one conversation per run</span>{/if}
              {#if item.model}<span class="tag">{item.model}</span>{/if}
            {:else}
              {#if item.catch_up !== 'all'}<span class="tag">backlog: {item.catch_up}</span>{/if}
              <span class="tag">{item.max_per_hour} an hour</span>
            {/if}
            {#if timed}
              <br /><span class="muted small" title={item.next_local ? `${item.next_local} (${item.timezone})` : undefined}>Next {when(item.next_at)}</span>
              {#if item.packs.some((id: string) => id !== 'web')}<br /><span class="muted small">{item.packs.filter((id: string) => id !== 'web').map(packLabel).join(' · ')}</span>{/if}
            {:else}
              <br /><span class="muted small">When {describeFilter(item.filter, item.on)}</span>
              {#if item.action === 'run'}<br /><span class="muted small">Tools: {item.effective_tools.join(', ')}</span>{/if}
            {/if}
            {#if timed || item.action === 'run'}
              <br /><LevelSelect small label={`Auto level of ${item.name}`} value={item.auto_level} effective={item.auto_level ?? ladder.defaults.unattended}
                follows={item.project ? 'its project or the default' : 'the default'} onchange={(value) => setLevel(item, value)} />
            {/if}
          </td>
          <td class="small">{#if item.last}<span class:danger={timed ? item.last.status !== 'ok' : item.last.status === 'failed'}>{item.last.status}</span> {when(item.last.finished_at)}<br /><span class="muted">{item.last.summary || item.last.note}</span>{:else}—{/if}</td>
          <td class="row">
            {#if timed}
              <button class="small" onclick={() => run(item)}>Run now</button>
            {:else}
              <button class="small" onclick={() => openProbe(item)}>Test</button>
            {/if}
            <button class="small" onclick={() => edit(item)}>Edit</button>
            <button class="small" onclick={() => toggle(item)}>{item.enabled ? 'Pause' : 'Resume'}</button>
            <button class="small" onclick={() => showRuns(item)}>Runs</button>
            <button class="small" aria-expanded={!!remembering && keyOf(remembering) === keyOf(item)}
              onclick={() => (remembering && keyOf(remembering) === keyOf(item) ? (remembering = null) : (remembering = item))}>Memory{remembered(item).facts ? ` (${remembered(item).facts})` : ''}</button>
            <button class="small toggle" aria-pressed={item.in_history} title="List this automation's conversations in the history of the rail"
              onclick={() => inHistory(item)}><Icon name="eye" size={14} />Show in history</button>
            {#if timed && item.waiting_run}
              <a class="small" href={'#/chat/' + item.waiting_run.conversation}>Answer</a>
              <button class="small" onclick={() => close(item)}>Close it</button>
            {:else if timed && (item.conversation ?? item.last?.conversation_id)}
              <a class="small" href={'#/chat/' + (item.conversation ?? item.last.conversation_id)}>Conversation</a>
            {/if}
            <button class="small danger" onclick={() => remove(item)}>Delete</button>
          </td>
        </tr>
      {:else}
        <tr><td colspan="3" class="empty">Nothing planned yet.</td></tr>
      {/each}
    </tbody>
  </table>
  {#if probe}
    <div class="panel stack">
      <div class="row"><h2 class="grow">Test “{probe.hook.name}”</h2><button class="small" onclick={() => (probe = null)}>Close</button></div>
      <p class="muted small">Nothing is run or sent: this shows whether an event would start the hook, and exactly what Clara would be given.</p>
      <div class="row end">
        <label class="field grow">On this event
          <select bind:value={probe.pick}>
            <option value="">the latest one stored</option>
            {#each probe.events as stored (stored.id)}<option value={String(stored.id)}>{when(stored.occurred_at)} — {stored.title}</option>{/each}
          </select></label>
        <button class="primary" onclick={runProbe}>Test</button>
      </div>
      {#if probe.error}<p class="row" role="status"><span class="tag warn"><Icon name="warn" size={14} />Cannot test</span> <span class="small">{probe.error}</span></p>{/if}
      {#if probe.result}
        <div class="row">
          {#if probe.result.matches}<span class="tag ok">It would start</span>{:else}<span class="tag warn">It would not start</span> <span class="muted small">{probe.result.why_not}</span>{/if}
          <span class="tag">{probe.result.action === 'run' ? 'runs Clara' : 'notifies'}</span>
          {#if probe.result.tainted}<span class="tag">untrusted from the start</span>{/if}
        </div>
        {#if probe.result.tools.length}<p class="small">Tools: {probe.result.tools.join(', ')}</p>{/if}
        <pre class="message" aria-label="What Clara would receive">{probe.result.message}</pre>
      {/if}
    </div>
  {/if}
  {#if remembering}
    <div class="panel stack">
      <div class="row"><h2 class="grow">Memory of {remembering.name}</h2><button class="small" onclick={() => (remembering = null)}>Close</button></div>
      <Facts scope={scopeOf(remembering)} onchange={loadMemory} />
      {#if remembering.on !== TIME && remembering.action === 'run' && !offers(remembering, 'remember')}
        <p class="muted small">This hook's runs read this memory but cannot add to it: put the tool <code>remember</code> in its list of tools.</p>
      {/if}
    </div>
  {/if}
  {#if inspect}
    <div class="panel">
      <div class="row"><h2 class="grow">Runs of {inspect.item.name}</h2><button class="small" onclick={() => (inspect = null)}>Close</button></div>
      {#if inspect.item.on === TIME}
        <table>
          <thead><tr><th>Due</th><th>Status</th><th>Summary</th><th>Waiting</th><th><span class="sr-only">Conversation</span></th></tr></thead>
          <tbody>
            {#each inspect.rows as entry}
              <tr><td class="small">{when(entry.due_at)}</td><td>{entry.status}</td><td class="small">{entry.summary}</td><td>{entry.waiting}</td>
                <td>{#if entry.conversation_id}<a class="small" href={'#/chat/' + entry.conversation_id}>Conversation</a>{/if}</td></tr>
            {:else}
              <tr><td colspan="5" class="empty">No run yet.</td></tr>
            {/each}
          </tbody>
        </table>
      {:else}
        <table>
          <thead><tr><th>When</th><th>Status</th><th>Event</th><th>What happened</th><th><span class="sr-only">Conversation</span></th></tr></thead>
          <tbody>
            {#each inspect.rows as entry (entry.id)}
              <tr>
                <td class="small">{when(entry.started_at ?? entry.queued_at)}</td>
                <td><span class="tag {tone(entry.status)}">{entry.status}</span></td>
                <td class="small">{entry.event_count > 1 ? `${entry.event_count} events: ` : ''}{entry.events.slice(0, 2).map((stored: any) => stored.title).join(' · ')}</td>
                <td class="small">{entry.summary || entry.note}</td>
                <td>{#if entry.conversation}<a class="small" href={'#/chat/' + entry.conversation}>Conversation</a>{/if}</td>
              </tr>
            {:else}
              <tr><td colspan="5" class="empty">No run yet.</td></tr>
            {/each}
          </tbody>
        </table>
      {/if}
    </div>
  {/if}
  <div class="row">
    <button class="small" aria-expanded={!!seen} aria-controls="hook-events" onclick={toggleSeen}>{seen ? 'Hide' : 'Show'} the events seen</button>
    <span class="muted small">What the hooks were offered ({looked}).</span>
  </div>
  {#if seen}
    <div class="panel" id="hook-events">
      <table>
        <thead><tr><th>When</th><th>Event</th></tr></thead>
        <tbody>
          {#each seen as stored (stored.id)}
            <tr><td class="small">{when(stored.occurred_at)}</td>
              <td class="small">{stored.title}{#if stored.self_caused} <span class="tag">from my own account</span>{/if}</td></tr>
          {:else}
            <tr><td colspan="2" class="empty">Nothing yet. A hook sees only what happens after it is made.</td></tr>
          {/each}
        </tbody>
      </table>
    </div>
  {/if}
</div>

<style>
  .conds { display: flex; flex-direction: column; gap: var(--sp-8); }
  .cond { display: flex; flex-wrap: wrap; align-items: center; gap: var(--sp-8); padding: var(--sp-8) var(--sp-10); border: 1px solid var(--line); border-radius: var(--r-md); background: var(--panel); }
  .cond-join { min-width: var(--sp-24); color: var(--muted); font-size: var(--fs-xs); font-weight: 650; }
  .cond select { flex: none; max-width: 100%; }
  .cond .value { flex: 1 1 calc(var(--sp-64) * 3); }
  .tool-list { max-height: calc(var(--sp-64) * 3); overflow: auto; display: grid; grid-template-columns: repeat(auto-fill, minmax(calc(var(--sp-64) * 3), 1fr)); gap: var(--sp-6) var(--sp-12); }
  .message { white-space: pre-wrap; overflow-wrap: anywhere; }
</style>
