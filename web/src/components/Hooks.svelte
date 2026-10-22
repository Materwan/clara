<script lang="ts">
  import { tick } from 'svelte';
  import Checkbox from './Checkbox.svelte';
  import Choice from './Choice.svelte';
  import Icon from './Icon.svelte';
  import ModelField from './ModelField.svelte';
  import Wizard from './Wizard.svelte';
  import { api, when } from '../lib/api';
  import { ATTEMPTS, RETRY_HELP, excerpt, inWords } from '../lib/automation';
  import { confirmDialog } from '../lib/dialog.svelte';
  import { conversations, live, toast } from '../lib/state.svelte';

  // The hooks of Clara: what she does when something happens outside (a new mail, a pull request, a push). The server decides everything; this
  // page writes the triggers, shows what they did and lets you test one on a stored event without running anything.
  type Condition = { field: string; operator: string; value: string };

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
  const TITLES: Record<string, string> = { 'mail.received': 'New mail', 'github.pull_request': 'Pull request', 'github.push': 'Push' };
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
  const STEPS = [
    { id: 'trigger', label: 'Trigger', title: 'What sets it off',
      lead: 'Something that happens outside: a new mail in a mailbox you connected, or a pull request or a push in a GitHub repository you added.' },
    { id: 'action', label: 'Action', title: 'What it does', lead: 'A name to find it by, and whether Clara is run or you are only told.' },
    { id: 'tools', label: 'Tools', title: 'Tools, model and privacy',
      lead: 'What arrives was written by someone else, so a run starts untrusted and has only the tools you give it. Anything it would change outside (send a mail, write to GitHub or Drive) asks you first.' },
    { id: 'runs', label: 'Runs', title: 'How runs behave', lead: 'What to do with events that pile up, how fast runs may start, and what you hear afterwards.' },
    { id: 'review', label: 'Review', title: 'Review', lead: '' },
  ];
  type Problem = { step: string; field: string; message: string };
  const empty = () => ({
    id: 0, name: '', on: 'mail.received', action: 'run', prompt: '', conditions: [] as Condition[], toolsMode: 'default', tools: [] as string[],
    project: 'none', model: '', local_only: false, catch_up: 'all', max_per_hour: 20, max_age_hours: 0, include_self: false,
    rerun_interrupted: false, attempts: 3, announce: true,
  });

  let hooks = $state<any[]>([]);
  let kinds = $state<any[]>([]);
  let projects = $state<any[]>([]);
  let models = $state<any>({ models: [], default: '' });
  let toolNames = $state<string[]>([]);
  let sources = $state<Record<string, { ready: boolean; poll: number }>>({ mail: { ready: true, poll: 120 }, github: { ready: true, poll: 120 } });
  let form = $state(empty());
  let open = $state(false);
  let step = $state('trigger');
  let checked = $state(false); // the last step was seen, or a save was tried: everything missing is shown where it is missing
  let tried = $state<string[]>([]); // the steps one tried to leave while something was missing: theirs is shown
  let saving = $state(false);
  let toolFilter = $state('');
  let runs = $state<any>(null); // { hook, runs }
  let probe = $state<any>(null); // { hook, events, pick, result }
  let seen = $state<any[] | null>(null); // the events stored, when that panel is open
  let newButton = $state<HTMLButtonElement>();

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

  // The triggers as cards: the source's icon, what happens, and a warning when there is nothing yet to look at.
  const kindOptions = $derived(
    kinds.map((kind) => {
      const source = sourceOf(kind.kind);
      return {
        value: kind.kind, label: TITLES[kind.kind] ?? kind.kind, hint: up(LONG[kind.kind] ?? kind.label), icon: SOURCES[source]?.icon,
        tag: sources[source] && !sources[source].ready ? SOURCES[source].tag : undefined,
      };
    }),
  );
  const toolOptions = $derived([
    { value: 'default', label: 'The safe default', hint: `It can only ${SOURCES[sourceOf(form.on)].reads} and notify you.` },
    { value: 'choose', label: 'Choose the tools', hint: 'Give the fewest the job needs.' },
  ]);

  // What is missing, and on which step. Next does not go past a step that holds something; the server says the same in its own words.
  // A number input gives null when it is empty or not a number.
  const whole = (value: unknown, least: number, most: number): boolean => typeof value === 'number' && Number.isInteger(value) && value >= least && value <= most;
  const problems = $derived.by<Problem[]>(() => {
    const found: Problem[] = [];
    form.conditions.forEach((condition, index) => {
      if (fields[condition.field] !== 'bool' && !condition.value.split(',').some((item) => item.trim())) {
        found.push({ step: 'trigger', field: `hf-cond-${index}`, message: `The condition on “${label(condition.field)}” needs a value.` });
      }
    });
    if (!found.length) {
      try {
        filterOf(form.conditions);
      } catch (error) {
        found.push({ step: 'trigger', field: 'hf-add', message: (error as Error).message });
      }
    }
    if (!form.name.trim()) found.push({ step: 'action', field: 'hf-name', message: 'Give the hook a name.' });
    if (form.action === 'run' && !form.prompt.trim()) found.push({ step: 'action', field: 'hf-prompt', message: 'Say what Clara should do.' });
    if (!whole(form.max_age_hours, 0, 8760)) found.push({ step: 'runs', field: 'hf-max-age', message: 'Hours a run may wait must be a whole number from 0 to 8760.' });
    if (!whole(form.max_per_hour, 1, 600)) found.push({ step: 'runs', field: 'hf-max-hour', message: 'Runs an hour must be a whole number from 1 to 600.' });
    return found;
  });
  const shown = (stepId: string): boolean => checked || tried.includes(stepId);
  const errorOf = (field: string): string | undefined => {
    const found = problems.find((item) => item.field === field);
    return found && shown(found.step) ? found.message : undefined;
  };
  // A notification has no model and no tools: its form has no step for them.
  const steps = $derived(
    STEPS.filter((item) => item.id !== 'tools' || form.action === 'run').map((item) => {
      const missing = problems.filter((p) => p.step === item.id).map((p) => p.message);
      return { ...item, missing, problems: shown(item.id) ? missing.length : 0 };
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

  $effect(() => {
    if (step === 'review') checked = true;
  });

  async function load(): Promise<void> {
    const data = await api.get('/v1/triggers');
    hooks = data.triggers;
    kinds = data.kinds;
    projects = (await api.get('/v1/projects')).projects;
    models = await api.get('/v1/models').catch(() => ({ models: [], default: '' }));
    const connections = await api.get('/v1/connections').catch(() => null);
    if (connections) {
      sources = {
        mail: { ready: connections.gmail.connected, poll: connections.mail.poll_seconds },
        github: { ready: connections.github.connected && connections.github.repos.length > 0, poll: connections.github.poll_seconds },
      };
    }
    if (runs) runs = { hook: runs.hook, runs: (await api.get(`/v1/triggers/${runs.hook.id}/runs`)).runs };
    if (seen) seen = (await api.get('/v1/trigger-events', { limit: 30 })).events;
  }

  $effect(() => {
    void live.refresh;
    load();
  });

  // A run starts and ends without a word on the event stream: while one is going or waiting, look again from time to time.
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
    (document.querySelector('#hook-form .step-head h3') as HTMLElement | null)?.focus();
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

  function fieldChanged(condition: Condition): void {
    condition.operator = 'contains';
    condition.value = fields[condition.field] === 'bool' ? 'true' : '';
  }

  const toggleTool = (name: string): void => {
    form.tools = form.tools.includes(name) ? form.tools.filter((item) => item !== name) : [...form.tools, name];
  };

  async function edit(hook: any): Promise<void> {
    form = {
      id: hook.id, name: hook.name, on: hook.on, action: hook.action, prompt: hook.prompt, conditions: conditionsOf(hook.filter),
      toolsMode: hook.tools.length ? 'choose' : 'default', tools: [...hook.tools], project: hook.project ? String(hook.project) : 'none',
      model: hook.model, local_only: hook.local_only, catch_up: hook.catch_up, max_per_hour: hook.max_per_hour,
      max_age_hours: hook.max_age_hours, include_self: hook.include_self, rerun_interrupted: hook.rerun_interrupted,
      attempts: hook.attempts, announce: hook.announce,
    };
    await openForm();
  }

  // Next, Enter or a later step was asked for while this one holds something missing: show it and go to the field
  async function blockedAt(stepId: string): Promise<void> {
    if (!tried.includes(stepId)) tried = [...tried, stepId];
    const found = problems.find((item) => item.step === stepId);
    if (found) await jump(found);
  }

  async function save(): Promise<void> {
    if (problems.length) {
      checked = true;
      await jump(problems[0]);
      return;
    }
    const filter = filterOf(form.conditions);
    const body: Record<string, any> = {
      name: form.name, filter, action: form.action, prompt: form.prompt,
      tools: form.action === 'run' && form.toolsMode === 'choose' ? form.tools : [], model: form.model, local_only: form.local_only,
      catch_up: form.catch_up, max_per_hour: Number(form.max_per_hour), max_age_hours: Number(form.max_age_hours),
      include_self: form.include_self, rerun_interrupted: form.rerun_interrupted, attempts: Number(form.attempts), announce: form.announce,
      project: form.project === 'none' ? 'none' : Number(form.project),
    };
    saving = true;
    try {
      if (form.id) await api.patch(`/v1/triggers/${form.id}`, body);
      else await api.post('/v1/triggers', { ...body, on: form.on });
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

  async function toggle(hook: any): Promise<void> {
    try {
      await api.patch(`/v1/triggers/${hook.id}`, { enabled: !hook.enabled });
    } catch (error) {
      toast((error as Error).message, 'error');
    }
    load();
  }

  async function remove(hook: any): Promise<void> {
    const message = `Delete “${hook.name}”? Its runs are forgotten; the conversations they made stay, no longer grouped under the hook.`;
    if (!(await confirmDialog({ title: 'Delete hook', message, confirm: 'Delete', danger: true }))) return;
    await api.del(`/v1/triggers/${hook.id}`);
    if (runs?.hook.id === hook.id) runs = null;
    if (probe?.hook.id === hook.id) probe = null;
    load();
  }

  async function inHistory(hook: any): Promise<void> {
    try {
      await api.patch(`/v1/triggers/${hook.id}`, { in_history: !hook.in_history });
      conversations.changed++;
      load();
    } catch (error) {
      toast((error as Error).message, 'error');
    }
  }

  async function showRuns(hook: any): Promise<void> {
    runs = { hook, runs: (await api.get(`/v1/triggers/${hook.id}/runs`)).runs };
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

<div class="stack">
  <div class="row start">
    <p class="intro grow">A hook runs Clara, or only notifies you, when something happens outside: a new mail in a mailbox you
      connected, or a pull request or a push in a GitHub repository you added. What arrives was written by someone else, so a run starts
      <b>unattended and untrusted</b>: anything it would change outside (send a mail, write to GitHub or Drive) asks you first, and it
      has only the tools you give it.</p>
    <button class="primary" aria-expanded={open} aria-controls="hook-form" bind:this={newButton}
      onclick={() => (open ? closeForm() : openForm())}><Icon name="plus" size={18} />New hook</button>
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
  <Wizard id="hook-form" label={form.id ? `Change ${form.name}` : 'New hook'} {steps} bind:step editing={!!form.id} busy={saving}
    submitLabel={form.id ? 'Save' : 'Make the hook'} onsubmit={save} oncancel={closeForm} onblocked={blockedAt}>
    {#snippet children(stepId)}
      {#if stepId === 'trigger'}
        <Choice cards legend="Where it happens" options={kindOptions} bind:value={form.on} disabled={!!form.id}
          help={form.id ? 'The trigger of a hook cannot be changed: make another hook for another trigger.' : undefined}
          onchange={() => (form.conditions = [])} />
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
                    <input id={`hf-cond-${index}`} class="grow value" aria-label="Text to compare with" placeholder="invoice, facture  (a comma means “or”)"
                      bind:value={condition.value} aria-invalid={errorOf(`hf-cond-${index}`) ? 'true' : undefined} />
                  {/if}
                  <button type="button" class="icon-btn" aria-label={`Remove condition ${index + 1}`} onclick={() => form.conditions.splice(index, 1)}><Icon name="close" size={16} /></button>
                </div>
                {#if errorOf(`hf-cond-${index}`)}<span class="field-error">{errorOf(`hf-cond-${index}`)}</span>{/if}
              {/each}
            </div>
          {/if}
          <div class="row">
            <button type="button" class="small" id="hf-add" onclick={addCondition}><Icon name="plus" size={14} />Add a condition</button>
            {#if errorOf('hf-add')}<span class="field-error">{errorOf('hf-add')}</span>{/if}
          </div>
          {#if form.conditions.length && conditionsText}<p class="muted" aria-live="polite">It starts when {conditionsText}.</p>{/if}
          <p class="muted small">Capitals do not matter. A sender's name and address can be forged, so a filter on them is a convenience, not a proof.</p>
        </section>
      {:else if stepId === 'action'}
        <label class="field">Name
          <input id="hf-name" bind:value={form.name} maxlength="100" aria-invalid={errorOf('hf-name') ? 'true' : undefined}
            aria-describedby={errorOf('hf-name') ? 'hf-name-error' : undefined} />
          {#if errorOf('hf-name')}<span class="field-error" id="hf-name-error">{errorOf('hf-name')}</span>{/if}
        </label>
        <Choice cards legend="Then" options={ACTIONS} bind:value={form.action} />
        <label class="field">{form.action === 'run' ? 'What Clara should do' : 'The notification'}
          {#if form.action === 'notify'}<span class="hint">Use {HINTS[form.on] ?? ''}</span>{/if}
          <textarea id="hf-prompt" rows={form.action === 'run' ? 6 : 3} bind:value={form.prompt} aria-invalid={errorOf('hf-prompt') ? 'true' : undefined}
            aria-describedby={errorOf('hf-prompt') ? 'hf-prompt-error' : undefined}></textarea>
          {#if errorOf('hf-prompt')}<span class="field-error" id="hf-prompt-error">{errorOf('hf-prompt')}</span>{/if}
        </label>
        <label class="field narrow">Project of the runs
          <span class="hint">Its files and instructions are context for every run. The runs are listed under the hook either way.</span>
          <select bind:value={form.project}>
            <option value="none">no project</option>
            {#each projects as project}<option value={String(project.id)}>{project.name}</option>{/each}
          </select></label>
      {:else if stepId === 'tools'}
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
        <section class="block stack">
          <Choice cards legend="If it happened while the server was off" help="Events found more than 15 minutes after they happened." options={CATCH_UPS} bind:value={form.catch_up} />
          <label class="field narrow">Expire a run that waited more than (hours)
            <span class="hint">0: never.</span>
            <input id="hf-max-age" type="number" min="0" max="8760" bind:value={form.max_age_hours} aria-invalid={errorOf('hf-max-age') ? 'true' : undefined}
              aria-describedby={errorOf('hf-max-age') ? 'hf-max-age-error' : undefined} />
            {#if errorOf('hf-max-age')}<span class="field-error" id="hf-max-age-error">{errorOf('hf-max-age')}</span>{/if}
          </label>
        </section>
        <section class="block stack">
          <h4>Pace</h4>
          <label class="field narrow">Runs an hour, at most
            <span class="hint">One run of a hook at a time.</span>
            <input id="hf-max-hour" type="number" min="1" max="600" bind:value={form.max_per_hour} aria-invalid={errorOf('hf-max-hour') ? 'true' : undefined}
              aria-describedby={errorOf('hf-max-hour') ? 'hf-max-hour-error' : undefined} />
            {#if errorOf('hf-max-hour')}<span class="field-error" id="hf-max-hour-error">{errorOf('hf-max-hour')}</span>{/if}
          </label>
          {#if form.action === 'run'}
            <Choice legend="If the model is busy or unreachable" help={RETRY_HELP} options={ATTEMPTS}
              bind:value={() => String(form.attempts), (value) => (form.attempts = Number(value))} />
          {/if}
        </section>
        <section class="block stack">
          <h4>Afterwards</h4>
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
      {:else}
        {#if problems.length}
          <div class="stack">
            <p class="row"><span class="tag danger"><Icon name="warn" size={14} />{problems.length} to fix before the hook can be {form.id ? 'saved' : 'made'}</span></p>
            <ul class="problems">{#each problems as problem}<li><button type="button" class="link" onclick={() => jump(problem)}>{problem.message}</button></li>{/each}</ul>
          </div>
        {/if}
        <dl class="facts">
          <dt>Name</dt><dd>{form.name.trim() || '—'}</dd>
          <dt>Starts</dt>
          <dd>{TITLES[form.on] ?? form.on}<br /><span class="muted">{form.conditions.length ? (conditionsText ? cap(`when ${conditionsText}`) : 'The conditions are not complete.') : cap(EVERY[form.on] ?? 'every event')}</span></dd>
          <dt>Then</dt><dd>{labelOf(ACTIONS, form.action)}</dd>
          <dt>{form.action === 'run' ? 'What Clara does' : 'The notification'}</dt><dd>{excerpt(form.prompt) || '—'}</dd>
          {#if form.action === 'run'}<dt>Tools</dt><dd>{toolsText}</dd>{/if}
          <dt>Project</dt><dd>{projects.find((project) => String(project.id) === form.project)?.name ?? 'No project'}</dd>
          {#if form.action === 'run'}
            <dt>Model</dt><dd>{form.model || `Default model (${models.default || 'none'})`}{#if form.local_only}<br /><span class="muted">Local models only.</span>{/if}</dd>
          {/if}
          <dt>If the server was off</dt><dd>{labelOf(CATCH_UPS, form.catch_up)}</dd>
          <dt>Pace</dt>
          <dd>At most {form.max_per_hour} an hour{#if Number(form.max_age_hours) > 0}; a run that waited more than {form.max_age_hours} hours expires{/if}.</dd>
          {#if form.action === 'run'}<dt>If the model fails</dt><dd>{labelOf(ATTEMPTS, String(form.attempts))}</dd>{/if}
          <dt>When a run ends</dt><dd>{form.announce ? 'You are told.' : 'Quiet if it went well; a failed one tells you.'}</dd>
        </dl>
      {/if}
    {/snippet}
  </Wizard>
  {/if}
  <table>
    <thead><tr><th>Hook</th><th>Last run</th><th><span class="sr-only">Actions</span></th></tr></thead>
    <tbody>
      {#each hooks as hook (hook.id)}
        <tr>
          <td><strong>{hook.name}</strong> <span class="tag"><Icon name={SOURCES[sourceOf(hook.on)]?.icon ?? 'bolt'} size={14} />{KINDS[hook.on] ?? hook.on}</span>
            <span class="tag">{hook.action === 'run' ? 'runs Clara' : 'notifies'}</span>
            {#if !hook.enabled}<span class="tag">paused</span>{/if}{#if hook.running}<span class="tag ok">running</span>{/if}
            {#if hook.queued}<span class="tag warn">{hook.queued} waiting</span>{/if}
            {#if hook.local_only}<span class="tag"><Icon name="home" size={14} />local only</span>{/if}
            {#if hook.catch_up !== 'all'}<span class="tag">backlog: {hook.catch_up}</span>{/if}
            <span class="tag">{hook.max_per_hour} an hour</span>
            <br /><span class="muted small">When {describeFilter(hook.filter, hook.on)}</span>
            {#if hook.action === 'run'}<br /><span class="muted small">Tools: {hook.effective_tools.join(', ')}</span>{/if}</td>
          <td class="small">{#if hook.last}<span class:danger={hook.last.status === 'failed'}>{hook.last.status}</span> {when(hook.last.finished_at)}<br /><span class="muted">{hook.last.summary || hook.last.note}</span>{:else}—{/if}</td>
          <td class="row">
            <button class="small" onclick={() => openProbe(hook)}>Test</button>
            <button class="small" onclick={() => edit(hook)}>Edit</button>
            <button class="small" onclick={() => toggle(hook)}>{hook.enabled ? 'Pause' : 'Resume'}</button>
            <button class="small" onclick={() => showRuns(hook)}>Runs</button>
            <button class="small toggle" aria-pressed={hook.in_history} title="List the conversations of this hook's runs in the history of the rail"
              onclick={() => inHistory(hook)}><Icon name="eye" size={14} />Show in history</button>
            <button class="small danger" onclick={() => remove(hook)}>Delete</button>
          </td>
        </tr>
      {:else}
        <tr><td colspan="3" class="empty">No hook yet.</td></tr>
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
  {#if runs}
    <div class="panel">
      <div class="row"><h2 class="grow">Runs of {runs.hook.name}</h2><button class="small" onclick={() => (runs = null)}>Close</button></div>
      <table>
        <thead><tr><th>When</th><th>Status</th><th>Event</th><th>What happened</th><th><span class="sr-only">Conversation</span></th></tr></thead>
        <tbody>
          {#each runs.runs as item (item.id)}
            <tr>
              <td class="small">{when(item.started_at ?? item.queued_at)}</td>
              <td><span class="tag {tone(item.status)}">{item.status}</span></td>
              <td class="small">{item.event_count > 1 ? `${item.event_count} events: ` : ''}{item.events.slice(0, 2).map((stored: any) => stored.title).join(' · ')}</td>
              <td class="small">{item.summary || item.note}</td>
              <td>{#if item.conversation}<a class="small" href={'#/chat/' + item.conversation}>Conversation</a>{/if}</td>
            </tr>
          {:else}
            <tr><td colspan="5" class="empty">No run yet.</td></tr>
          {/each}
        </tbody>
      </table>
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
