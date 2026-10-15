<script lang="ts">
  import { api, when, zone } from '../lib/api';
  import { live, toast } from '../lib/state.svelte';

  let tab = $state<'tasks' | 'calendar' | 'reminders'>('tasks');
  let status = $state<'open' | 'done' | 'all'>('open');
  let tasks = $state<any[]>([]);
  let reminders = $state<any[]>([]);
  let form = $state({ title: '', description: '', due: '', reminders: '', parent_id: '' });
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

  async function add(event: SubmitEvent): Promise<void> {
    event.preventDefault();
    try {
      await api.post('/v1/tasks', {
        title: form.title, description: form.description, due: form.due || null, timezone: zone,
        reminders: form.reminders.split(',').map((item) => item.trim()).filter(Boolean),
        parent_id: form.parent_id ? Number(form.parent_id) : null,
      });
      form = { title: '', description: '', due: '', reminders: '', parent_id: '' };
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
    if (!confirm(`Delete “${task.title}” and its sub tasks?`)) return;
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
  <div class="task" class:done={task.status === 'done'} style:padding-left={`${depth * 1.4}rem`}>
    <input type="checkbox" checked={task.status === 'done'} onchange={() => patch(task, { status: task.status === 'done' ? 'open' : 'done' })} />
    <div class="grow">
      {#if editing?.id === task.id}
        <div class="grid-2">
          <input bind:value={editing.title} />
          <input type="datetime-local" bind:value={editing.due} />
          <textarea rows="2" bind:value={editing.description}></textarea>
          <input placeholder="reminders: 2026-10-20T09:00, …" bind:value={editing.reminders} />
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
    <div class="row actions">
      <button class="small" onclick={() => (editing = { id: task.id, title: task.title, description: task.description, due: task.due_at ? task.due_at.slice(0, 16) : '', reminders: task.reminders.map((r: string) => r.slice(0, 16)).join(', ') })}>Edit</button>
      <button class="small" onclick={() => (form.parent_id = String(task.id))}>+ Sub</button>
      <select class="small" onchange={(event) => { const value = (event.target as HTMLSelectElement).value; if (value) patch(task, { parent_id: Number(value) }); }}>
        <option value="">Move…</option>
        <option value="0">to the top</option>
        {#each tasks.filter((other) => other.id !== task.id && other.status === 'open') as other}<option value={other.id}>under {other.title}</option>{/each}
      </select>
      <button class="small danger" onclick={() => remove(task)}>Delete</button>
    </div>
  </div>
  {#each childrenOf(task.id) as child (child.id)}{@render taskRow(child, depth + 1)}{/each}
{/snippet}

<div class="page stack">
  <h1>Tasks & reminders</h1>
  <div class="tabs">
    <button class:active={tab === 'tasks'} onclick={() => (tab = 'tasks')}>Tasks</button>
    <button class:active={tab === 'calendar'} onclick={() => (tab = 'calendar')}>Calendar</button>
    <button class:active={tab === 'reminders'} onclick={() => (tab = 'reminders')}>Reminders</button>
  </div>

  {#if tab === 'tasks'}
    <form class="panel stack" onsubmit={add}>
      <div class="grid-2">
        <input placeholder="New task" bind:value={form.title} maxlength="200" required />
        <label class="row small">due <input class="grow" type="datetime-local" bind:value={form.due} /></label>
        <textarea rows="2" placeholder="Description (what, where, with whom, steps)" bind:value={form.description}></textarea>
        <div class="stack">
          <input placeholder="Reminders (local times, comma separated); empty: Clara picks them" bind:value={form.reminders} />
          <select bind:value={form.parent_id}>
            <option value="">A main task</option>
            {#each tasks.filter((task) => task.status === 'open') as task}<option value={String(task.id)}>Sub task of: {task.title}</option>{/each}
          </select>
        </div>
      </div>
      <button class="primary">Add task</button>
    </form>
    <div class="row">
      {#each ['open', 'done', 'all'] as name}<button class:primary={status === name} class="small" onclick={() => (status = name as typeof status)}>{name}</button>{/each}
    </div>
    <div class="panel">
      {#each roots as task (task.id)}{@render taskRow(task, 0)}{:else}<div class="empty">No task.</div>{/each}
    </div>
  {:else if tab === 'calendar'}
    <div class="row">
      <button class="small" onclick={() => (month = new Date(month.getFullYear(), month.getMonth() - 1, 1))}>‹</button>
      <strong>{month.toLocaleDateString(undefined, { month: 'long', year: 'numeric' })}</strong>
      <button class="small" onclick={() => (month = new Date(month.getFullYear(), month.getMonth() + 1, 1))}>›</button>
    </div>
    <div class="calendar">
      {#each ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'] as name}<div class="head muted small">{name}</div>{/each}
      {#each days as cell}
        <div class="cell" class:other={!cell.inMonth}>
          <div class="small muted">{cell.day.getDate()}</div>
          {#each cell.due as task}<div class="item" class:done={task.status === 'done'}>📋 {task.title}</div>{/each}
          {#each cell.reminded as reminder}<div class="item">⏰ {reminder.text}</div>{/each}
        </div>
      {/each}
    </div>
  {:else}
    <form class="panel row" onsubmit={addReminder}>
      <input class="grow" placeholder="Remind me to…" bind:value={reminderForm.text} maxlength="500" required />
      <input type="datetime-local" bind:value={reminderForm.at} required />
      <select bind:value={reminderForm.repeat}>
        <option value="">once</option><option value="daily">daily</option><option value="weekly">weekly</option><option value="monthly">monthly</option>
      </select>
      <button class="primary">Add</button>
    </form>
    <table>
      <thead><tr><th>When</th><th>Reminder</th><th>Repeat</th><th></th></tr></thead>
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
  .task {
    display: flex;
    gap: 0.6rem;
    align-items: flex-start;
    padding: 0.4rem 0;
    border-bottom: 1px solid var(--line);
  }
  .task.done strong {
    text-decoration: line-through;
    color: var(--muted);
  }
  .actions {
    opacity: 0.4;
  }
  .task:hover .actions {
    opacity: 1;
  }
  .pre {
    white-space: pre-wrap;
  }
  .calendar {
    display: grid;
    grid-template-columns: repeat(7, 1fr);
    gap: 2px;
  }
  .head {
    text-align: center;
    padding: 0.2rem;
  }
  .cell {
    min-height: 90px;
    background: var(--panel);
    border: 1px solid var(--line);
    border-radius: 6px;
    padding: 0.25rem;
    overflow: hidden;
  }
  .cell.other {
    opacity: 0.45;
  }
  .item {
    font-size: 0.78em;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .item.done {
    text-decoration: line-through;
  }
</style>
