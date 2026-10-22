<script lang="ts">
  import { tick } from 'svelte';
  import Checkbox from './Checkbox.svelte';
  import Choice from './Choice.svelte';
  import Hint from './Hint.svelte';
  import Icon from './Icon.svelte';
  import ModelField from './ModelField.svelte';
  import Wizard from './Wizard.svelte';
  import { api, fileToBase64, when, zone } from '../lib/api';
  import { ATTEMPTS, RETRY_HELP, excerpt, inWords } from '../lib/automation';
  import { confirmDialog } from '../lib/dialog.svelte';
  import { conversations, live, toast } from '../lib/state.svelte';

  // What Clara does at a time: the Schedules view of the Automation page.
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
  const STEPS = [
    { id: 'trigger', label: 'Trigger', title: 'When it runs', lead: `The first run, and whether it comes back. Times are in your zone (${zone}).` },
    { id: 'action', label: 'Action', title: 'What Clara does', lead: 'A name to find it by, and the prompt she is given at every run.' },
    { id: 'tools', label: 'Tools', title: 'Tools, model and privacy',
      lead: 'The special tools you tick are the only extra ones a run has. An action your rules say to ask about is held and pushed to you, and the run goes on without it.' },
    { id: 'runs', label: 'Runs', title: 'How runs behave', lead: 'Where a run is told, and what happens when the server was off, when a run is late, or when the model fails.' },
    { id: 'review', label: 'Review', title: 'Review', lead: '' },
  ];
  type Problem = { step: string; field: string; message: string };
  const empty = () => ({
    id: 0, name: '', prompt: '', at: '', repeat: '', weekdays: [] as number[], project: '', local_only: false, documents: [] as any[],
    packs: ['web'] as string[], per_run: false, missed: 'skip', overlap: 'skip', model: '', attempts: 3,
  });
  let schedules = $state<any[]>([]);
  let projects = $state<any[]>([]);
  let packs = $state<any[]>([]);
  let models = $state<any>({ models: [], default: '' });
  let form = $state(empty());
  let history = $state<any>(null);
  let open = $state(false); // the form is open: to plan a new schedule, or to change the one in `form`
  let step = $state('trigger');
  let checked = $state(false); // the last step was seen, or a save was tried: everything missing is shown where it is missing
  let tried = $state<string[]>([]); // the steps one tried to leave while something was missing: theirs is shown
  let saving = $state(false);
  let newButton = $state<HTMLButtonElement>();

  async function load(): Promise<void> {
    schedules = (await api.get('/v1/schedules')).schedules;
    projects = (await api.get('/v1/projects')).projects;
    packs = (await api.get('/v1/packs', { where: 'schedule' })).packs;
    models = await api.get('/v1/models').catch(() => ({ models: [], default: '' }));
  }

  const packLabel = (id: string): string => packs.find((pack) => pack.id === id)?.label ?? id;
  const togglePack = (id: string): void => {
    form.packs = form.packs.includes(id) ? form.packs.filter((item) => item !== id) : [...form.packs, id];
  };
  const toggleDay = (index: number): void => {
    form.weekdays = form.weekdays.includes(index) ? form.weekdays.filter((day) => day !== index) : [...form.weekdays, index];
  };
  const labelOf = (options: { value: string; label: string }[], value: string): string => options.find((item) => item.value === value)?.label ?? value;

  $effect(() => {
    void live.refresh;
    load();
  });

  // What is missing, and on which step. Next does not go past a step that holds something; the server says the same in its own words.
  const problems = $derived.by<Problem[]>(() => {
    const found: Problem[] = [];
    if (!form.at) found.push({ step: 'trigger', field: 'sf-at', message: 'Choose when it first runs.' });
    if (!form.name.trim()) found.push({ step: 'action', field: 'sf-name', message: 'Give the schedule a name.' });
    if (!form.prompt.trim() && !form.documents.length) found.push({ step: 'action', field: 'sf-prompt', message: 'Say what Clara should do, or add a document.' });
    return found;
  });
  const shown = (stepId: string): boolean => checked || tried.includes(stepId);
  const errorOf = (field: string): string | undefined => {
    const found = problems.find((item) => item.field === field);
    return found && shown(found.step) ? found.message : undefined;
  };
  const steps = $derived(
    STEPS.map((item) => {
      const missing = problems.filter((p) => p.step === item.id).map((p) => p.message);
      return { ...item, missing, problems: shown(item.id) ? missing.length : 0 };
    }),
  );

  $effect(() => {
    if (step === 'review') checked = true;
  });

  // The first run and the repeat, as a sentence.
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

  async function openForm(): Promise<void> {
    open = true;
    step = 'trigger';
    checked = false;
    tried = [];
    await tick();
    (document.querySelector('#schedule-form .step-head h3') as HTMLElement | null)?.focus();
  }

  async function closeForm(): Promise<void> {
    open = false;
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

  async function save(): Promise<void> {
    if (problems.length) {
      checked = true;
      await jump(problems[0]);
      return;
    }
    const body = {
      name: form.name, prompt: form.prompt, at: form.at, timezone: zone, repeat: form.repeat, weekdays: form.weekdays,
      project: form.project ? Number(form.project) : null, local_only: form.local_only, documents: form.documents,
      packs: form.packs, per_run: form.per_run, missed: form.missed, overlap: form.overlap, model: form.model, attempts: form.attempts,
    };
    saving = true;
    try {
      if (form.id) await api.patch(`/v1/schedules/${form.id}`, body);
      else await api.post('/v1/schedules', body);
      await closeForm();
      load();
    } catch (error) {
      toast((error as Error).message, 'error');
    } finally {
      saving = false;
    }
  }

  async function edit(schedule: any): Promise<void> {
    form = {
      id: schedule.id, name: schedule.name, prompt: schedule.prompt, at: schedule.local_at, repeat: schedule.repeat,
      weekdays: [...schedule.weekdays], project: schedule.project ? String(schedule.project) : '', local_only: schedule.local_only,
      documents: schedule.documents.map((doc: any) => ({ name: doc.name })),
      packs: [...schedule.packs], per_run: schedule.per_run, missed: schedule.missed, overlap: schedule.overlap, model: schedule.model,
      attempts: schedule.attempts,
    };
    await openForm();
  }

  async function addDocuments(event: Event): Promise<void> {
    for (const file of (event.target as HTMLInputElement).files ?? []) {
      const extracted = await api.post('/v1/documents/extract', { name: file.name, data: await fileToBase64(file) });
      form.documents.push({ name: file.name, text: extracted.text });
    }
  }

  async function run(schedule: any): Promise<void> {
    try {
      await api.post(`/v1/schedules/${schedule.id}/run`);
      toast('Started: the summary arrives when it ends.', 'ok');
      load();
    } catch (error) {
      toast((error as Error).message, 'error');
    }
  }

  async function close(schedule: any): Promise<void> {
    try {
      await api.post(`/v1/schedules/${schedule.id}/close`);
      toast('Closing it from what Clara has: the summary arrives when it ends.', 'ok');
      load();
    } catch (error) {
      toast((error as Error).message, 'error');
    }
  }

  async function toggle(schedule: any): Promise<void> {
    await api.patch(`/v1/schedules/${schedule.id}`, { enabled: !schedule.enabled });
    load();
  }

  async function inHistory(schedule: any): Promise<void> {
    try {
      await api.patch(`/v1/schedules/${schedule.id}`, { in_history: !schedule.in_history });
      conversations.changed++;
      load();
    } catch (error) {
      toast((error as Error).message, 'error');
    }
  }

  async function remove(schedule: any): Promise<void> {
    if (!(await confirmDialog({ title: 'Delete schedule', message: `Delete “${schedule.name}”?`, confirm: 'Delete', danger: true }))) return;
    await api.del(`/v1/schedules/${schedule.id}`);
    load();
  }

  async function showHistory(schedule: any): Promise<void> {
    history = await api.get(`/v1/schedules/${schedule.id}`);
  }
</script>

<div class="stack">
  <div class="row start">
    <p class="intro grow">Prompts Clara runs by herself, unattended: an action your rules say to ask about is held and pushed
      to you, and the run goes on without it. The special tools you tick are the only extra ones a run has; a run that may
      ask you something waits for your answer in its conversation.</p>
    <button class="primary" aria-expanded={open} aria-controls="schedule-form" bind:this={newButton}
      onclick={() => (open ? closeForm() : openForm())}><Icon name="plus" size={18} />New schedule</button>
  </div>
  {#if open}
  <Wizard id="schedule-form" label={form.id ? `Change ${form.name}` : 'New schedule'} {steps} bind:step editing={!!form.id} busy={saving}
    submitLabel={form.id ? 'Save' : 'Plan it'} onsubmit={save} oncancel={closeForm} onblocked={blockedAt}>
    {#snippet children(stepId)}
      {#if stepId === 'trigger'}
        <label class="field narrow">First run
          <span class="hint">Your local time; summer time is followed.</span>
          <input id="sf-at" type="datetime-local" bind:value={form.at} aria-invalid={errorOf('sf-at') ? 'true' : undefined}
            aria-describedby={errorOf('sf-at') ? 'sf-at-error' : undefined} />
          {#if errorOf('sf-at')}<span class="field-error" id="sf-at-error">{errorOf('sf-at')}</span>{/if}
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
      {:else if stepId === 'action'}
        <label class="field">Name
          <input id="sf-name" bind:value={form.name} maxlength="100" aria-invalid={errorOf('sf-name') ? 'true' : undefined}
            aria-describedby={errorOf('sf-name') ? 'sf-name-error' : undefined} />
          {#if errorOf('sf-name')}<span class="field-error" id="sf-name-error">{errorOf('sf-name')}</span>{/if}
        </label>
        <label class="field">What Clara should do
          <textarea id="sf-prompt" rows="6" bind:value={form.prompt} aria-invalid={errorOf('sf-prompt') ? 'true' : undefined}
            aria-describedby={errorOf('sf-prompt') ? 'sf-prompt-error' : undefined}></textarea>
          {#if errorOf('sf-prompt')}<span class="field-error" id="sf-prompt-error">{errorOf('sf-prompt')}</span>{/if}
        </label>
        <div class="grid-2">
          <label class="field">Project
            <span class="hint">Its files and instructions are context for every run.</span>
            <select bind:value={form.project}>
              <option value="">no project</option>
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
      {:else if stepId === 'tools'}
        <section class="block">
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
        <section class="block">
          <h4>Model</h4>
          <ModelField bind:value={form.model} {models} />
        </section>
        <section class="block">
          <h4>Privacy</h4>
          <label class="option"><Checkbox bind:checked={form.local_only} />
            <span class="option-text"><strong>Local only</strong><span>Local models only; nothing leaves your machines.</span></span></label>
        </section>
      {:else if stepId === 'runs'}
        <Choice legend="Conversation" options={CONVERSATIONS}
          bind:value={() => (form.per_run ? 'each' : 'shared'), (value) => (form.per_run = value === 'each')} />
        <Choice cards legend="If the server was off" help="A run more than an hour late counts as missed." options={MISSED} bind:value={form.missed} />
        <Choice cards legend="If the last run is not over" help="A run comes due while the previous one still runs or waits." options={OVERLAPS} bind:value={form.overlap} />
        <Choice legend="If the model is busy or unreachable" help={RETRY_HELP} options={ATTEMPTS}
          bind:value={() => String(form.attempts), (value) => (form.attempts = Number(value))} />
      {:else}
        {#if problems.length}
          <div class="stack">
            <p class="row"><span class="tag danger"><Icon name="warn" size={14} />{problems.length} to fix before it can be {form.id ? 'saved' : 'planned'}</span></p>
            <ul class="problems">{#each problems as problem}<li><button type="button" class="link" onclick={() => jump(problem)}>{problem.message}</button></li>{/each}</ul>
          </div>
        {/if}
        <dl class="facts">
          <dt>Name</dt><dd>{form.name.trim() || '—'}</dd>
          <dt>When</dt><dd>{timing || '—'}</dd>
          <dt>What</dt>
          <dd>{excerpt(form.prompt) || '—'}{#if form.documents.length}<br /><span class="muted">With {inWords(form.documents.map((doc: any) => doc.name))}.</span>{/if}</dd>
          <dt>Project</dt><dd>{projects.find((project) => String(project.id) === form.project)?.name ?? 'No project'}</dd>
          <dt>Tools</dt><dd>{form.packs.length ? inWords(form.packs.map(packLabel)) : 'None'}</dd>
          <dt>Model</dt><dd>{form.model || `Default model (${models.default || 'none'})`}{#if form.local_only}<br /><span class="muted">Local models only.</span>{/if}</dd>
          <dt>Conversation</dt><dd>{labelOf(CONVERSATIONS, form.per_run ? 'each' : 'shared')}</dd>
          <dt>If the server was off</dt><dd>{labelOf(MISSED, form.missed)}</dd>
          <dt>If the last run is not over</dt><dd>{labelOf(OVERLAPS, form.overlap)}</dd>
          <dt>If the model fails</dt><dd>{labelOf(ATTEMPTS, String(form.attempts))}</dd>
        </dl>
      {/if}
    {/snippet}
  </Wizard>
  {/if}
  <table>
    <thead><tr><th>Name</th><th>Next</th><th>Last run</th><th><span class="sr-only">Actions</span></th></tr></thead>
    <tbody>
      {#each schedules as schedule (schedule.id)}
        <tr>
          <td><strong>{schedule.name}</strong> <span class="tag">{schedule.repeat || 'once'}</span>
            {#if !schedule.enabled}<span class="tag">paused</span>{/if}{#if schedule.running}<span class="tag ok">running</span>{/if}
            {#if schedule.local_only}<span class="tag"><Icon name="home" size={14} />local only</span>{/if}
            {#if schedule.waiting_run}<span class="tag warn">waiting for you</span>{/if}
            {#if schedule.queued}<span class="tag">{schedule.queued} queued</span>{/if}
            {#if schedule.per_run}<span class="tag">one conversation per run</span>{/if}
            {#if schedule.model}<span class="tag">{schedule.model}</span>{/if}
            {#if schedule.packs.some((id: string) => id !== 'web')}<br /><span class="muted small">{schedule.packs.filter((id: string) => id !== 'web').map(packLabel).join(' · ')}</span>{/if}</td>
          <td class="small" title={schedule.next_local ? `${schedule.next_local} (${schedule.timezone})` : undefined}>{when(schedule.next_at)}</td>
          <td class="small">{#if schedule.last}<span class:danger={schedule.last.status !== 'ok'}>{schedule.last.status}</span> {when(schedule.last.finished_at)}<br /><span class="muted">{schedule.last.summary}</span>{:else}—{/if}</td>
          <td class="row">
            <button class="small" onclick={() => run(schedule)}>Run now</button>
            <button class="small" onclick={() => edit(schedule)}>Edit</button>
            <button class="small" onclick={() => toggle(schedule)}>{schedule.enabled ? 'Pause' : 'Resume'}</button>
            <button class="small" onclick={() => showHistory(schedule)}>Runs</button>
            <button class="small toggle" aria-pressed={schedule.in_history} title="List this schedule's conversations in the history of the rail"
              onclick={() => inHistory(schedule)}><Icon name="eye" size={14} />Show in history</button>
            {#if schedule.waiting_run}
              <a class="small" href={'#/chat/' + schedule.waiting_run.conversation}>Answer</a>
              <button class="small" onclick={() => close(schedule)}>Close it</button>
            {:else if schedule.conversation ?? schedule.last?.conversation_id}
              <a class="small" href={'#/chat/' + (schedule.conversation ?? schedule.last.conversation_id)}>Conversation</a>
            {/if}
            <button class="small danger" onclick={() => remove(schedule)}>Delete</button>
          </td>
        </tr>
      {:else}
        <tr><td colspan="4" class="empty">Nothing planned.</td></tr>
      {/each}
    </tbody>
  </table>
  {#if history}
    <div class="panel">
      <div class="row"><h2 class="grow">Runs of {history.name}</h2><button class="small" onclick={() => (history = null)}>Close</button></div>
      <table>
        <thead><tr><th>Due</th><th>Status</th><th>Summary</th><th>Waiting</th><th><span class="sr-only">Conversation</span></th></tr></thead>
        <tbody>
          {#each history.history as item}
            <tr><td class="small">{when(item.due_at)}</td><td>{item.status}</td><td class="small">{item.summary}</td><td>{item.waiting}</td>
              <td>{#if item.conversation_id}<a class="small" href={'#/chat/' + item.conversation_id}>Conversation</a>{/if}</td></tr>
          {/each}
        </tbody>
      </table>
    </div>
  {/if}
</div>
