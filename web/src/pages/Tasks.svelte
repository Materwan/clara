<script lang="ts">
  import Checkbox from '../components/Checkbox.svelte';
  import Icon from '../components/Icon.svelte';
  import Segmented from '../components/Segmented.svelte';
  import { tick } from 'svelte';
  import { api, when, zone } from '../lib/api';
  import { confirmDialog } from '../lib/dialog.svelte';
  import { live, toast } from '../lib/state.svelte';

  type Tab = 'tasks' | 'calendar' | 'reminders';
  type Status = 'open' | 'done' | 'all';
  const TABS: { value: Tab; label: string }[] = [{ value: 'tasks', label: 'Tasks' }, { value: 'calendar', label: 'Calendar' }, { value: 'reminders', label: 'Reminders' }];
  const STATUSES: { value: Status; label: string }[] = [{ value: 'open', label: 'Open' }, { value: 'done', label: 'Done' }, { value: 'all', label: 'All' }];

  let tab = $state<Tab>('tasks');
  let status = $state<Status>('open');
  let tasks = $state<any[]>([]);
  let reminders = $state<any[]>([]);
  const blank = () => ({ title: '', description: '', due: '', reminders: '', parent_id: '' });
  let form = $state(blank());
  let adding = $state(false); // the form of a new task is open (it opens from "New task" and from a task's "+ Sub")
  let addButton = $state<HTMLButtonElement>();
  let titleInput = $state<HTMLInputElement>();
  let reminderForm = $state({ text: '', at: '', repeat: '' });
  let month = $state(new Date(new Date().getFullYear(), new Date().getMonth(), 1));
  let editing = $state<any>(null);

  async function load(): Promise<void> {
    tasks = (await api.get('/v1/tasks', { status })).tasks;
    reminders = (await api.get('/v1/reminders')).reminders.filter((item: any) => !item.system);
  }

  $effect(() => {
    void status;
    void live.refresh;
    load();
  });

  const roots = $derived(tasks.filter((task) => !task.parent_id || !tasks.some((other) => other.id === task.parent_id)));
  const childrenOf = (id: number) => tasks.filter((task) => task.parent_id === id).sort((a, b) => a.position - b.position || a.id - b.id);

  async function openForm(parent = ''): Promise<void> {
    form.parent_id = parent;
    adding = true;
    await tick();
    titleInput?.focus();
  }

  async function closeForm(): Promise<void> {
    adding = false;
    form = blank();
    await tick();
    addButton?.focus();
  }

  async function add(event: SubmitEvent): Promise<void> {
    event.preventDefault();
    try {
      await api.post('/v1/tasks', {
        title: form.title, description: form.description, due: form.due || null, timezone: zone,
        reminders: form.reminders.split(',').map((item) => item.trim()).filter(Boolean),
        parent_id: form.parent_id ? Number(form.parent_id) : null,
      });
      closeForm();
      load();
    } catch (error) {
      toast((error as Error).message, 'error');
    }
  }

  async function patch(task: any, fields: any): Promise<void> {
    try {
      await api.patch(`/v1/tasks/${task.id}`, fields);
      editing = null;
      load();
    } catch (error) {
      toast((error as Error).message, 'error');
    }
  }

  async function remove(task: any): Promise<void> {
    if (!(await confirmDialog({ title: 'Delete task', message: `Delete “${task.title}” and its sub tasks?`, confirm: 'Delete', danger: true }))) return;
    await api.del(`/v1/tasks/${task.id}`);
    load();
  }

  async function addReminder(event: SubmitEvent): Promise<void> {
    event.preventDefault();
    try {
      await api.post('/v1/reminders', { ...reminderForm, timezone: zone });
      reminderForm = { text: '', at: '', repeat: '' };
      load();
    } catch (error) {
      toast((error as Error).message, 'error');
    }
  }

  async function cancel(reminder: any): Promise<void> {
    await api.del(`/v1/reminders/${reminder.id}`);
    load();
  }

  const days = $derived.by(() => {
    const first = new Date(month);
    const start = new Date(first);
    start.setDate(1 - ((first.getDay() + 6) % 7));
    return Array.from({ length: 42 }, (_, index) => {
      const day = new Date(start);
      day.setDate(start.getDate() + index);
      const key = day.toDateString();
      return {
        day,
        inMonth: day.getMonth() === first.getMonth(),
        due: tasks.filter((task) => task.due_at && new Date(task.due_at).toDateString() === key),
        reminded: reminders.filter((item) => new Date(item.due_at).toDateString() === key),
      };
    });
  });
</script>

{#snippet taskRow(task: any, depth: number)}
  <div class="task" class:done={task.status === 'done'} style:padding-left={`calc(var(--sp-24) * ${depth})`}>
    <Checkbox aria-label={`Done: ${task.title}`} checked={task.status === 'done'} onchange={() => patch(task, { status: task.status === 'done' ? 'open' : 'done' })} />
    <div class="grow">
      {#if editing?.id === task.id}
        <div class="grid-2">
          <input aria-label="Title" bind:value={editing.title} />
          <input type="datetime-local" aria-label="Due" bind:value={editing.due} />
          <textarea rows="2" aria-label="Description" bind:value={editing.description}></textarea>
          <input aria-label="Reminders" placeholder="reminders: 2026-10-20T09:00, …" bind:value={editing.reminders} />
        </div>
        <div class="row">
          <button class="small primary" onclick={() => patch(task, { title: editing.title, description: editing.description, due: editing.due || '', reminders: editing.reminders.split(',').map((s: string) => s.trim()).filter(Boolean) })}>Save</button>
          <button class="small" onclick={() => (editing = null)}>Cancel</button>
        </div>
      {:else}
        <strong>{task.title}</strong>
        {#if task.due_at}<span class="tag">due {when(task.due_at)}</span>{/if}
        {#if task.subtasks.total}<span class="tag">{task.subtasks.done}/{task.subtasks.total}</span>{/if}
        <span class="muted small">· {task.reminders_sent} sent{task.next_reminder ? ` · next ${when(task.next_reminder)}` : ''}</span>
        {#if task.description}<div class="muted small pre">{task.description}</div>{/if}
      {/if}
    </div>
    <div class="row task-actions">
      <button class="small ghost" onclick={() => (editing = { id: task.id, title: task.title, description: task.description, due: task.due_at ? task.due_at.slice(0, 16) : '', reminders: task.reminders.map((r: string) => r.slice(0, 16)).join(', ') })}>Edit</button>
      <button class="small ghost" onclick={() => openForm(String(task.id))}>+ Sub</button>
      <select class="small" aria-label={`Move “${task.title}”`} onchange={(event) => { const value = (event.target as HTMLSelectElement).value; if (value) patch(task, { parent_id: Number(value) }); }}>
        <option value="">Move…</option>
        <option value="0">to the top</option>
        {#each tasks.filter((other) => other.id !== task.id && other.status === 'open') as other}<option value={other.id}>under {other.title}</option>{/each}
      </select>
      <button class="small ghost danger" onclick={() => remove(task)}>Delete</button>
    </div>
  </div>
  {#each childrenOf(task.id) as child (child.id)}{@render taskRow(child, depth + 1)}{/each}
{/snippet}

<div class="page stack">
  <div class="row">
    <Segmented label="View" options={TABS} bind:value={tab} />
    {#if tab === 'tasks'}
      <button class="primary push" aria-expanded={adding} aria-controls="new-task" bind:this={addButton} onclick={() => (adding ? closeForm() : openForm())}>
        <Icon name="plus" size={18} />New task</button>
    {/if}
  </div>

  {#if tab === 'tasks'}
    {#if adding}
      <form class="panel stack" id="new-task" aria-label="New task" onsubmit={add}>
        <div class="grid-2">
          <label class="field">Title <input bind:this={titleInput} bind:value={form.title} maxlength="200" required /></label>
          <label class="field">Due <input type="datetime-local" bind:value={form.due} /></label>
          <label class="field">Description <textarea rows="3" placeholder="What, where, with whom, steps" bind:value={form.description}></textarea></label>
          <div class="stack">
            <label class="field">Reminders <span class="hint">Local times, comma separated. Left empty, Clara picks them.</span>
              <input placeholder="2026-10-20T09:00, …" bind:value={form.reminders} /></label>
            <label class="field">Sub task of
              <select bind:value={form.parent_id}>
                <option value="">Nothing: a main task</option>
                {#each tasks.filter((task) => task.status === 'open') as task}<option value={String(task.id)}>{task.title}</option>{/each}
              </select></label>
          </div>
        </div>
        <div class="row"><button class="primary">Add task</button><button type="button" onclick={closeForm}>Cancel</button></div>
      </form>
    {/if}
    <div><Segmented label="Which tasks" options={STATUSES} bind:value={status} /></div>
    <div class="panel">
      {#each roots as task (task.id)}{@render taskRow(task, 0)}{:else}<div class="empty">No task.</div>{/each}
    </div>
  {:else if tab === 'calendar'}
    <div class="cal">
      <div class="cal-bar">
        <button class="ghost icon-btn" aria-label="Previous month" onclick={() => (month = new Date(month.getFullYear(), month.getMonth() - 1, 1))}><Icon name="left" /></button>
        <h2 class="cal-title">{month.toLocaleDateString(undefined, { month: 'long', year: 'numeric' })}</h2>
        <button class="ghost icon-btn" aria-label="Next month" onclick={() => (month = new Date(month.getFullYear(), month.getMonth() + 1, 1))}><Icon name="chevron" /></button>
      </div>
      <div class="cal-grid">
        {#each ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'] as name}<div class="cal-dow">{name}</div>{/each}
        {#each days as cell}
          <div class="cal-cell" class:other={!cell.inMonth} class:today={cell.day.toDateString() === new Date().toDateString()}>
            <span class="cal-num">{cell.day.getDate()}</span>
            {#each cell.due as task}<span class="cal-chip" class:done={task.status === 'done'} title={`Task: ${task.title}`}>{task.title}</span>{/each}
            {#each cell.reminded as reminder}<span class="cal-chip reminder" title={`Reminder: ${reminder.text}`}>{reminder.text}</span>{/each}
          </div>
        {/each}
      </div>
    </div>
  {:else}
    <form class="panel row end" onsubmit={addReminder}>
      <label class="field grow">Reminder <input placeholder="Remind me to…" bind:value={reminderForm.text} maxlength="500" required /></label>
      <label class="field">When <input type="datetime-local" bind:value={reminderForm.at} required /></label>
      <label class="field">Repeat
        <select bind:value={reminderForm.repeat}>
          <option value="">once</option><option value="daily">daily</option><option value="weekly">weekly</option><option value="monthly">monthly</option>
        </select></label>
      <button class="primary">Add</button>
    </form>
    <table>
      <thead><tr><th>When</th><th>Reminder</th><th>Repeat</th><th><span class="sr-only">Actions</span></th></tr></thead>
      <tbody>
        {#each reminders as reminder (reminder.id)}
          <tr>
            <td class="small">{reminder.local}<br /><span class="muted">{reminder.timezone}</span></td>
            <td>{reminder.text}</td>
            <td class="small">{reminder.repeat || 'once'}</td>
            <td><button class="small danger" onclick={() => cancel(reminder)}>Cancel</button></td>
          </tr>
        {:else}
          <tr><td colspan="4" class="empty">No reminder.</td></tr>
        {/each}
      </tbody>
    </table>
  {/if}
</div>

<style>
  /* checkbox, then the task, then its actions; on a phone the actions go under the task, not beside it (nothing is squeezed) */
  .task {
    display: grid;
    grid-template-columns: auto minmax(0, 1fr) auto;
    gap: var(--sp-12);
    align-items: start;
    padding: var(--sp-12) var(--sp-4);
    border-bottom: 1px solid var(--line);
  }
  .task:last-child {
    border-bottom: none;
  }
  .task strong {
    font-weight: 650;
    margin-right: var(--sp-4);
  }
  .task.done strong {
    text-decoration: line-through;
    color: var(--muted);
  }
  /* quiet at rest (no borders), at full strength: text of 4.5:1 and more, as everywhere */
  .task-actions {
    gap: var(--sp-4);
    justify-content: flex-end;
  }
  .task-actions select {
    width: 8rem;
  }
  @media (max-width: 600px) {
    .task {
      grid-template-columns: auto minmax(0, 1fr);
    }
    .task-actions {
      grid-column: 2;
      justify-content: flex-start;
      margin-left: calc(var(--sp-10) * -1); /* a small button's own padding: its label lines up with the title */
    }
  }
</style>
