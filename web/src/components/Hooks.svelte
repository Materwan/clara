<script lang="ts">
  import { tick } from 'svelte';
  import Checkbox from './Checkbox.svelte';
  import Icon from './Icon.svelte';
  import { api, when } from '../lib/api';
  import { confirmDialog } from '../lib/dialog.svelte';
  import { conversations, live, toast } from '../lib/state.svelte';

  // The hooks of Clara: what she does when something happens outside (today: a new mail). The server decides everything; this
  // page writes the triggers, shows what they did and lets you test one on a stored event without running anything.
  type Condition = { field: string; operator: string; value: string };

  const OPERATORS = [
    { value: 'contains', label: 'contains' }, { value: 'eq', label: 'is exactly' }, { value: 'starts_with', label: 'starts with' },
    { value: 'ends_with', label: 'ends with' }, { value: 'not_contains', label: 'does not contain' },
  ];
  const FIELDS: Record<string, string> = {
    from: 'Sender', from_address: 'Sender address', subject: 'Subject', snippet: 'Start of the text', account: 'Service (gmail or outlook)',
    mailbox: 'Mailbox address', has_attachment: 'Has an attachment', unread: 'Is unread',
  };
  const KINDS: Record<string, string> = { 'mail.received': 'new mail' };
  const LONG: Record<string, string> = { 'mail.received': 'new mail in a connected mailbox' };
  const CATCH_UPS = [
    { value: 'all', label: 'run for each of them, at the pace allowed' }, { value: 'digest', label: 'run once for all of them together' },
    { value: 'latest', label: 'run only for the newest' }, { value: 'skip', label: 'do nothing for them' },
  ];
  const empty = () => ({
    id: 0, name: '', on: 'mail.received', action: 'run', prompt: '', conditions: [] as Condition[], toolsMode: 'default', tools: [] as string[],
    project: 'auto', model: '', local_only: false, catch_up: 'all', max_per_hour: 20, max_age_hours: 0, include_self: false,
    rerun_interrupted: false, attempts: 3, announce: true,
  });

  let hooks = $state<any[]>([]);
  let kinds = $state<any[]>([]);
  let projects = $state<any[]>([]);
  let models = $state<any>({ models: [], default: '' });
  let toolNames = $state<string[]>([]);
  let mail = $state<any>({ ready: true, poll: 120 });
  let form = $state(empty());
  let open = $state(false);
  let more = $state(false); // the form's less common options
  let toolFilter = $state('');
  let runs = $state<any>(null); // { hook, runs }
  let probe = $state<any>(null); // { hook, events, pick, result }
  let seen = $state<any[] | null>(null); // the events stored, when that panel is open
  let newButton = $state<HTMLButtonElement>();
  let nameInput = $state<HTMLInputElement>();

  const fields = $derived<Record<string, string>>(kinds.find((kind) => kind.kind === form.on)?.fields ?? {});
  const readTools = $derived<string[]>([...(kinds.find((kind) => kind.kind === form.on)?.read_tools ?? []), 'notify']);
  const shownTools = $derived(toolNames.filter((name) => name.includes(toolFilter.trim().toLowerCase())));
  const label = (field: string): string => FIELDS[field] ?? field;
  const operatorLabel = (operator: string): string => OPERATORS.find((item) => item.value === operator)?.label ?? operator;
  const projectOf = (hook: any): any => projects.find((project) => project.id === hook.project);

  async function load(): Promise<void> {
    const data = await api.get('/v1/triggers');
    hooks = data.triggers;
    kinds = data.kinds;
    projects = (await api.get('/v1/projects')).projects;
    models = await api.get('/v1/models').catch(() => ({ models: [], default: '' }));
    const connections = await api.get('/v1/connections').catch(() => null);
    if (connections) {
      mail = { ready: connections.gmail.connected || connections.outlook.connected, poll: connections.mail.poll_seconds };
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
    if (!toolNames.length) toolNames = (await api.get('/v1/rules')).tools;
    await tick();
    nameInput?.focus();
  }

  async function closeForm(): Promise<void> {
    open = false;
    more = false;
    toolFilter = '';
    form = empty();
    await tick();
    newButton?.focus();
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
    const first = 'subject' in fields ? 'subject' : (Object.keys(fields).find((field) => fields[field] === 'text') ?? Object.keys(fields)[0] ?? 'subject');
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
    more = false;
    await openForm();
  }

  async function save(event: SubmitEvent): Promise<void> {
    event.preventDefault();
    let filter: Record<string, any>;
    try {
      filter = filterOf(form.conditions);
    } catch (error) {
      toast((error as Error).message, 'error');
      return;
    }
    const body: Record<string, any> = {
      name: form.name, filter, action: form.action, prompt: form.prompt,
      tools: form.action === 'run' && form.toolsMode === 'choose' ? form.tools : [], model: form.model, local_only: form.local_only,
      catch_up: form.catch_up, max_per_hour: Number(form.max_per_hour), max_age_hours: Number(form.max_age_hours),
      include_self: form.include_self, rerun_interrupted: form.rerun_interrupted, attempts: Number(form.attempts), announce: form.announce,
      project: form.project === 'auto' || form.project === 'none' ? form.project : Number(form.project),
    };
    try {
      if (form.id) await api.patch(`/v1/triggers/${form.id}`, body);
      else await api.post('/v1/triggers', { ...body, on: form.on });
      conversations.changed++;
      await closeForm();
      load();
    } catch (error) {
      toast((error as Error).message, 'error');
    }
  }

  // ------------------------------------------------------------------ the list
  function describeFilter(filter: Record<string, any>): string {
    const parts = Object.entries(filter).flatMap(([field, spec]) =>
      typeof spec.eq === 'boolean'
        ? [`${label(field).toLowerCase()}: ${spec.eq ? 'yes' : 'no'}`]
        : Object.entries(spec).map(([operator, values]) => `${label(field).toLowerCase()} ${operatorLabel(operator)} ${(values as string[]).map((value) => `“${value}”`).join(' or ')}`));
    return parts.join(' · ') || 'every new mail';
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
    const message = `Delete “${hook.name}”? Its runs are forgotten; the conversations they made stay${hook.project ? ' in its project' : ''}.`;
    if (!(await confirmDialog({ title: 'Delete hook', message, confirm: 'Delete', danger: true }))) return;
    await api.del(`/v1/triggers/${hook.id}`);
    if (runs?.hook.id === hook.id) runs = null;
    if (probe?.hook.id === hook.id) probe = null;
    load();
  }

  async function inHistory(hook: any): Promise<void> {
    const project = projectOf(hook);
    if (!project) return;
    try {
      await api.patch(`/v1/projects/${project.id}`, { in_history: !project.in_history });
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
    <p class="intro grow">A hook runs Clara, or only notifies you, when something happens outside: today, a new mail in a mailbox you
      connected. What arrives was written by someone else, so a run starts <b>unattended and untrusted</b>: anything it would change
      outside (send a mail, write to GitHub or Drive) asks you first, and it has only the tools you give it.</p>
    <button class="primary" aria-expanded={open} aria-controls="hook-form" bind:this={newButton}
      onclick={() => (open ? closeForm() : openForm())}><Icon name="plus" size={18} />New hook</button>
  </div>
  {#if !mail.ready}
    <p class="row" role="status"><span class="tag warn"><Icon name="warn" size={14} />No mailbox</span>
      <span class="small">A hook on new mail has nothing to look at. Connect Gmail or Outlook on the <a href="#/connections">Connections</a> page.</span></p>
  {:else if mail.poll === 0}
    <p class="row" role="status"><span class="tag warn"><Icon name="warn" size={14} />Mail polling is off</span>
      <span class="small"><code>CLARA_MAIL_POLL_SECONDS</code> is 0 on the server, so no hook will start.</span></p>
  {/if}
  {#if open}
  <form class="panel stack" id="hook-form" aria-label={form.id ? `Change ${form.name}` : 'New hook'} onsubmit={save}>
    <div class="grid-2">
      <label class="field">Name <input bind:this={nameInput} bind:value={form.name} maxlength="100" required /></label>
      <label class="field">When
        <select bind:value={form.on} disabled={!!form.id}>
          {#each kinds as kind (kind.kind)}<option value={kind.kind} title={kind.label}>{LONG[kind.kind] ?? kind.kind}</option>{/each}
        </select></label>
    </div>
    <fieldset class="packs">
      <legend class="small">Only when <span class="muted">(every condition must hold; leave empty for every event)</span></legend>
      {#each form.conditions as condition, index}
        <div class="row condition" role="group" aria-label={`Condition ${index + 1}`}>
          <select aria-label="Field" bind:value={condition.field} onchange={() => fieldChanged(condition)}>
            {#each Object.keys(fields) as field}<option value={field}>{label(field)}</option>{/each}
          </select>
          {#if fields[condition.field] === 'bool'}
            <select aria-label="Value" bind:value={condition.value}><option value="true">yes</option><option value="false">no</option></select>
          {:else}
            <select aria-label="Comparison" bind:value={condition.operator}>
              {#each OPERATORS as operator}<option value={operator.value}>{operator.label}</option>{/each}
            </select>
            <input class="grow" aria-label="Text to compare with" placeholder="invoice, facture  (a comma means “or”)" bind:value={condition.value} />
          {/if}
          <button type="button" class="small" aria-label={`Remove condition ${index + 1}`} onclick={() => form.conditions.splice(index, 1)}><Icon name="close" size={14} /></button>
        </div>
      {/each}
      <div><button type="button" class="small" onclick={addCondition}><Icon name="plus" size={14} />Add a condition</button></div>
      <p class="muted small">Capitals do not matter. A sender's name and address can be forged, so a filter on them is a convenience, not a proof.</p>
    </fieldset>
    <div class="grid-2">
      <label class="field">Then
        <select bind:value={form.action}>
          <option value="run">run Clara</option><option value="notify">only notify me (no model)</option>
        </select></label>
      <label class="field">Project of the runs
        <select bind:value={form.project}>
          {#if !form.id}<option value="auto">a new project for this hook</option>{/if}
          <option value="none">no project</option>
          {#each projects as project}<option value={String(project.id)}>{project.name}</option>{/each}
        </select></label>
    </div>
    <label class="field">{form.action === 'run' ? 'What Clara should do' : 'The notification'}
      {#if form.action === 'notify'}<span class="hint">Use {'{from}'}, {'{subject}'}, {'{snippet}'}, {'{mailbox}'}. Empty: the sender and the subject.</span>{/if}
      <textarea rows={form.action === 'run' ? 4 : 2} bind:value={form.prompt} required={form.action === 'run'}></textarea></label>
    {#if form.action === 'run'}
      <fieldset class="packs">
        <legend class="small">Tools of the run</legend>
        <label class="field">
          <select aria-label="Which tools" bind:value={form.toolsMode}>
            <option value="default">the safe default: read mail, notify</option><option value="choose">choose the tools…</option>
          </select></label>
        {#if form.toolsMode === 'default'}<p class="muted small">Exactly: {readTools.join(', ')}.</p>{/if}
        {#if form.toolsMode === 'choose'}
          <input aria-label="Filter the tools" placeholder="filter the list" bind:value={toolFilter} />
          <div class="tool-list" role="group" aria-label="Tools to choose from">
            {#each shownTools as name (name)}
              <label class="small row"><Checkbox checked={form.tools.includes(name)} onchange={() => toggleTool(name)} />{name}</label>
            {/each}
          </div>
          <p class="muted small">{form.tools.length} chosen. Give the fewest that the job needs: a hostile mail can steer a run only within them.
            Whatever changes something outside still asks you.</p>
        {/if}
      </fieldset>
    {/if}
    <div class="row">
      <label class="small row" title="Local models only; nothing leaves your machines"><Checkbox bind:checked={form.local_only} />Local only</label>
      <label class="small row"><Checkbox bind:checked={form.announce} />Tell me when a run ends</label>
      <button type="button" class="small" aria-expanded={more} aria-controls="hook-more" onclick={() => (more = !more)}>More options</button>
    </div>
    {#if more}
    <div class="grid-2" id="hook-more">
      <label class="field">If mail arrived while the server was off
        <select bind:value={form.catch_up}>{#each CATCH_UPS as item}<option value={item.value}>{item.label}</option>{/each}</select></label>
      <label class="field">Runs an hour, at most <input type="number" min="1" max="600" bind:value={form.max_per_hour} /></label>
      <label class="field">Forget what waited more than (hours, 0: never) <input type="number" min="0" max="8760" bind:value={form.max_age_hours} /></label>
      <label class="field">Model
        <select bind:value={form.model}>
          <option value="">Default model ({models.default || 'none'})</option>
          {#each models.models as model (model.ref)}<option value={model.ref}>{model.ref}</option>{/each}
        </select></label>
      <label class="field">If the model is busy or unreachable
        <select bind:value={form.attempts}>
          <option value={1}>do not try again</option><option value={2}>try again once, after 1 minute</option><option value={3}>try again, after 1 and 5 minutes</option>
        </select></label>
      <div class="stack">
        <label class="small row" title="Mail sent from Clara's own account is left out, so a hook cannot answer its own answer"><Checkbox bind:checked={form.include_self} />Also for mail from my own account</label>
        <label class="small row" title="A run the server stop cut half way is normally not repeated: it may have done part of its work"><Checkbox bind:checked={form.rerun_interrupted} />Repeat a run the server stopped half way</label>
      </div>
    </div>
    {/if}
    <div class="row">
      <button class="primary">{form.id ? 'Save' : 'Make the hook'}</button>
      <button type="button" onclick={closeForm}>Cancel</button>
    </div>
  </form>
  {/if}
  <table>
    <thead><tr><th>Hook</th><th>Last run</th><th><span class="sr-only">Actions</span></th></tr></thead>
    <tbody>
      {#each hooks as hook (hook.id)}
        <tr>
          <td><strong>{hook.name}</strong> <span class="tag"><Icon name="mail" size={14} />{KINDS[hook.on] ?? hook.on}</span>
            <span class="tag">{hook.action === 'run' ? 'runs Clara' : 'notifies'}</span>
            {#if !hook.enabled}<span class="tag">paused</span>{/if}{#if hook.running}<span class="tag ok">running</span>{/if}
            {#if hook.queued}<span class="tag warn">{hook.queued} waiting</span>{/if}
            {#if hook.local_only}<span class="tag"><Icon name="home" size={14} />local only</span>{/if}
            {#if hook.catch_up !== 'all'}<span class="tag">backlog: {hook.catch_up}</span>{/if}
            <span class="tag">{hook.max_per_hour} an hour</span>
            <br /><span class="muted small">When {describeFilter(hook.filter)}</span>
            {#if hook.action === 'run'}<br /><span class="muted small">Tools: {hook.effective_tools.join(', ')}</span>{/if}</td>
          <td class="small">{#if hook.last}<span class:danger={hook.last.status === 'failed'}>{hook.last.status}</span> {when(hook.last.finished_at)}<br /><span class="muted">{hook.last.summary || hook.last.note}</span>{:else}—{/if}</td>
          <td class="row">
            <button class="small" onclick={() => openProbe(hook)}>Test</button>
            <button class="small" onclick={() => edit(hook)}>Edit</button>
            <button class="small" onclick={() => toggle(hook)}>{hook.enabled ? 'Pause' : 'Resume'}</button>
            <button class="small" onclick={() => showRuns(hook)}>Runs</button>
            {#if projectOf(hook)}
              <button class="small toggle" aria-pressed={projectOf(hook).in_history} title="List the conversations of this hook's runs in the history of the rail"
                onclick={() => inHistory(hook)}><Icon name="eye" size={14} />Show in history</button>
            {/if}
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
        <thead><tr><th>When</th><th>Status</th><th>Mail</th><th>What happened</th><th><span class="sr-only">Conversation</span></th></tr></thead>
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
    <span class="muted small">What the hooks were offered ({mail.poll ? `mail is looked at every ${mail.poll} seconds while a hook is on` : 'mail is not looked at: polling is off on this server'}).</span>
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
            <tr><td colspan="2" class="empty">Nothing yet. A hook sees only the mail that arrives after it is made.</td></tr>
          {/each}
        </tbody>
      </table>
    </div>
  {/if}
</div>

<style>
  fieldset { min-width: 0; } /* a fieldset is as wide as its widest control unless told it may shrink: a long option would push out of the card */
  .condition select { flex: none; max-width: 100%; }
  .tool-list { max-height: calc(var(--sp-64) * 3); overflow: auto; display: grid; grid-template-columns: repeat(auto-fill, minmax(calc(var(--sp-64) * 3), 1fr)); gap: var(--sp-6) var(--sp-12); }
  .message { white-space: pre-wrap; overflow-wrap: anywhere; }
</style>
