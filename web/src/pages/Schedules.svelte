<script lang="ts">
  import Checkbox from '../components/Checkbox.svelte';
  import Icon from '../components/Icon.svelte';
  import { api, fileToBase64, when, zone } from '../lib/api';
  import { confirmDialog } from '../lib/dialog.svelte';
  import { live, toast } from '../lib/state.svelte';

  const DAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];
  const empty = () => ({ id: 0, name: '', prompt: '', at: '', repeat: '', weekdays: [] as number[], project: '', local_only: false, documents: [] as any[] });
  let schedules = $state<any[]>([]);
  let projects = $state<any[]>([]);
  let form = $state(empty());
  let history = $state<any>(null);

  async function load(): Promise<void> {
    schedules = (await api.get('/v1/schedules')).schedules;
    projects = (await api.get('/v1/projects')).projects;
  }

  $effect(() => {
    void live.refresh;
    load();
  });

  async function save(event: SubmitEvent): Promise<void> {
    event.preventDefault();
    const body = {
      name: form.name, prompt: form.prompt, at: form.at, timezone: zone, repeat: form.repeat, weekdays: form.weekdays,
      project: form.project ? Number(form.project) : null, local_only: form.local_only, documents: form.documents,
    };
    try {
      if (form.id) await api.patch(`/v1/schedules/${form.id}`, body);
      else await api.post('/v1/schedules', body);
      form = empty();
      load();
    } catch (error) {
      toast((error as Error).message, 'error');
    }
  }

  function edit(schedule: any): void {
    form = {
      id: schedule.id, name: schedule.name, prompt: schedule.prompt, at: schedule.local_at, repeat: schedule.repeat,
      weekdays: [...schedule.weekdays], project: schedule.project ? String(schedule.project) : '', local_only: schedule.local_only,
      documents: schedule.documents.map((doc: any) => ({ name: doc.name })),
    };
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

  async function toggle(schedule: any): Promise<void> {
    await api.patch(`/v1/schedules/${schedule.id}`, { enabled: !schedule.enabled });
    load();
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

<div class="page stack">
  <p class="intro">Prompts Clara runs by herself, unattended: an action your rules say to ask about is held and pushed
    to you, and the run goes on without it.</p>
  <form class="panel stack" onsubmit={save}>
    <div class="grid-2">
      <input aria-label="Name" placeholder="Name" bind:value={form.name} maxlength="100" required />
      <label class="row small">first run <input class="grow" type="datetime-local" bind:value={form.at} required /></label>
    </div>
    <textarea rows="4" aria-label="What Clara should do" placeholder="What Clara should do" bind:value={form.prompt}></textarea>
    <div class="row">
      <select aria-label="Repeat" bind:value={form.repeat}>
        <option value="">once</option><option value="daily">daily</option><option value="weekly">weekly</option><option value="monthly">monthly</option>
      </select>
      {#if form.repeat === 'weekly'}
        {#each DAYS as day, index}
          <label class="small row"><Checkbox checked={form.weekdays.includes(index)}
            onchange={() => (form.weekdays = form.weekdays.includes(index) ? form.weekdays.filter((d) => d !== index) : [...form.weekdays, index])} />{day}</label>
        {/each}
      {/if}
      <select aria-label="Project" bind:value={form.project}>
        <option value="">no project</option>
        {#each projects as project}<option value={String(project.id)}>{project.name}</option>{/each}
      </select>
      <label class="small row"><Checkbox bind:checked={form.local_only} /> local only</label>
      <label class="small">documents <input type="file" multiple onchange={addDocuments} /></label>
    </div>
    {#if form.documents.length}
      <div class="chips">{#each form.documents as doc, index}<span class="chip"><Icon name="file" size={14} /><span class="name">{doc.name}</span><button type="button" aria-label={`Remove ${doc.name}`} onclick={() => form.documents.splice(index, 1)}><Icon name="close" size={14} /></button></span>{/each}</div>
    {/if}
    <div class="row">
      <button class="primary">{form.id ? 'Save' : 'Plan it'}</button>
      {#if form.id}<button type="button" onclick={() => (form = empty())}>Cancel</button>{/if}
    </div>
  </form>
  <table>
    <thead><tr><th>Name</th><th>Next</th><th>Last run</th><th></th></tr></thead>
    <tbody>
      {#each schedules as schedule (schedule.id)}
        <tr>
          <td><strong>{schedule.name}</strong> <span class="tag">{schedule.repeat || 'once'}</span>
            {#if !schedule.enabled}<span class="tag">paused</span>{/if}{#if schedule.running}<span class="tag ok">running</span>{/if}
            {#if schedule.local_only}<span class="tag"><Icon name="home" size={14} />local only</span>{/if}</td>
          <td class="small">{schedule.next_local ?? '—'}</td>
          <td class="small">{#if schedule.last}<span class:danger={schedule.last.status !== 'ok'}>{schedule.last.status}</span> {when(schedule.last.finished_at)}<br /><span class="muted">{schedule.last.summary}</span>{:else}—{/if}</td>
          <td class="row">
            <button class="small" onclick={() => run(schedule)}>Run now</button>
            <button class="small" onclick={() => edit(schedule)}>Edit</button>
            <button class="small" onclick={() => toggle(schedule)}>{schedule.enabled ? 'Pause' : 'Resume'}</button>
            <button class="small" onclick={() => showHistory(schedule)}>Runs</button>
            {#if schedule.conversation}<a class="small" href={'#/chat/' + schedule.conversation}>Conversation</a>{/if}
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
        <thead><tr><th>Due</th><th>Status</th><th>Summary</th><th>Waiting</th></tr></thead>
        <tbody>
          {#each history.history as item}
            <tr><td class="small">{when(item.due_at)}</td><td>{item.status}</td><td class="small">{item.summary}</td><td>{item.waiting}</td></tr>
          {/each}
        </tbody>
      </table>
    </div>
  {/if}
</div>
