<script lang="ts">
  import { tick } from 'svelte';
  import Checkbox from './Checkbox.svelte';
  import Hint from './Hint.svelte';
  import Icon from './Icon.svelte';
  import { api, fileToBase64, when, zone } from '../lib/api';
  import { confirmDialog } from '../lib/dialog.svelte';
  import { conversations, live, toast } from '../lib/state.svelte';

  // What Clara does at a time: the Schedules view of the Automation page.
  const DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
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
  let newButton = $state<HTMLButtonElement>();
  let nameInput = $state<HTMLInputElement>();

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

  $effect(() => {
    void live.refresh;
    load();
  });

  async function openForm(): Promise<void> {
    open = true;
    await tick();
    nameInput?.focus();
  }

  async function closeForm(): Promise<void> {
    open = false;
    form = empty();
    await tick();
    newButton?.focus();
  }

  async function save(event: SubmitEvent): Promise<void> {
    event.preventDefault();
    const body = {
      name: form.name, prompt: form.prompt, at: form.at, timezone: zone, repeat: form.repeat, weekdays: form.weekdays,
      project: form.project ? Number(form.project) : null, local_only: form.local_only, documents: form.documents,
      packs: form.packs, per_run: form.per_run, missed: form.missed, overlap: form.overlap, model: form.model, attempts: form.attempts,
    };
    try {
      if (form.id) await api.patch(`/v1/schedules/${form.id}`, body);
      else await api.post('/v1/schedules', body);
      closeForm();
      load();
    } catch (error) {
      toast((error as Error).message, 'error');
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
  <form class="panel stack" id="schedule-form" aria-label={form.id ? `Change ${form.name}` : 'New schedule'} onsubmit={save}>
    <div class="grid-2">
      <label class="field">Name <input bind:this={nameInput} bind:value={form.name} maxlength="100" required /></label>
      <label class="field">First run <input type="datetime-local" bind:value={form.at} required /></label>
    </div>
    <label class="field">What Clara should do <textarea rows="4" bind:value={form.prompt}></textarea></label>
    <div class="grid-2">
      <label class="field">Repeat
        <select bind:value={form.repeat}>
          <option value="">once</option><option value="daily">daily</option><option value="weekly">weekly</option><option value="monthly">monthly</option>
        </select></label>
      <label class="field">Project
        <select bind:value={form.project}>
          <option value="">no project</option>
          {#each projects as project}<option value={String(project.id)}>{project.name}</option>{/each}
        </select></label>
    </div>
    {#if form.repeat === 'weekly'}
      <div class="row" role="group" aria-label="On these days">
        {#each DAYS as day, index}
          <label class="small row"><Checkbox checked={form.weekdays.includes(index)}
            onchange={() => (form.weekdays = form.weekdays.includes(index) ? form.weekdays.filter((d) => d !== index) : [...form.weekdays, index])} />{day}</label>
        {/each}
      </div>
    {/if}
    <div class="row">
      <label class="small row" title="Local models only; nothing leaves your machines"><Checkbox bind:checked={form.local_only} />Local only</label>
      <label class="button small"><Icon name="upload" size={16} />Add documents<input class="sr-only" type="file" multiple onchange={addDocuments} /></label>
    </div>
    {#if form.documents.length}
      <div class="chips">{#each form.documents as doc, index}<span class="chip"><Icon name="file" size={14} /><span class="name">{doc.name}</span><button type="button" aria-label={`Remove ${doc.name}`} onclick={() => form.documents.splice(index, 1)}><Icon name="close" size={14} /></button></span>{/each}</div>
    {/if}
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
    <div class="grid-2">
      <label class="field">Conversation
        <select bind:value={form.per_run}>
          <option value={false}>one for all runs</option><option value={true}>one per run</option>
        </select></label>
      <label class="field">Model
        <select bind:value={form.model}>
          <option value="">Default model ({models.default || 'none'})</option>
          {#each models.models as model (model.ref)}<option value={model.ref}>{model.ref}</option>{/each}
        </select></label>
      <label class="field">If the server was off
        <select bind:value={form.missed}>
          <option value="skip">skip what was missed</option><option value="latest">run the latest missed</option><option value="all">run every missed one</option>
        </select></label>
      <label class="field">If the last run is not over
        <select bind:value={form.overlap}>
          <option value="skip">skip the new run</option><option value="replace">close the old one, start the new</option><option value="queue">queue the new run</option>
        </select></label>
      <label class="field">If the model is busy or unreachable
        <select bind:value={form.attempts}>
          <option value={1}>do not try again</option><option value={2}>try again once, after 1 minute</option><option value={3}>try again, after 1 and 5 minutes</option>
        </select></label>
    </div>
    <div class="row">
      <button class="primary">{form.id ? 'Save' : 'Plan it'}</button>
      <button type="button" onclick={closeForm}>Cancel</button>
    </div>
  </form>
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
