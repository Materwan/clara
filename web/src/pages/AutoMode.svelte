<script lang="ts">
  import Icon from '../components/Icon.svelte';
  import { api } from '../lib/api';
  import { confirmDialog } from '../lib/dialog.svelte';
  import { type LevelsState, type ToolRow, TOP_WARNING, cells, take } from '../lib/levels.svelte';
  import { toast } from '../lib/state.svelte';

  let data = $state<LevelsState>({ levels: [], top: 5, defaults: { chat: 3, unattended: 3 }, classes: [], tools: [] });
  let filter = $state('');
  let added = $state({ name: '', before: '' });
  let groupAsk = $state<Record<string, string>>({});
  let groupRun = $state<Record<string, string>>({});

  async function load(): Promise<void> {
    keep(await api.get('/v1/levels'));
  }

  function keep(state: LevelsState): void {
    data = state;
    take(state);
  }

  /** Run a change of the server, which answers with the whole state; say why when it is refused. */
  async function change(call: () => Promise<LevelsState>): Promise<boolean> {
    try {
      keep(await call());
      return true;
    } catch (error) {
      toast((error as Error).message, 'error');
      await load();
      return false;
    }
  }

  $effect(() => {
    load().catch((error) => toast((error as Error).message, 'error'));
  });

  const positions = $derived(Array.from({ length: data.top + 1 }, (_, n) => n));
  const nameOf = (position: number): string => data.levels.find((level) => level.position === position)?.name ?? '';

  // ---- the ladder
  async function rename(position: number, name: string): Promise<void> {
    if (!name.trim() || name === nameOf(position)) return;
    await change(() => api.patch(`/v1/levels/${position}`, { name }));
  }

  async function add(event: SubmitEvent): Promise<void> {
    event.preventDefault();
    const before = added.before === '' ? undefined : Number(added.before);
    if (await change(() => api.post('/v1/levels', { name: added.name, before }))) added = { name: '', before: '' };
  }

  async function remove(position: number): Promise<void> {
    const message = `Remove level ${position} (${nameOf(position)})? What is set to it moves to the level below, and the tools that ran from it run from the next level up: nothing becomes more permissive.`;
    if (!(await confirmDialog({ title: 'Remove a level', message, confirm: 'Remove', danger: true }))) return;
    await change(() => api.del(`/v1/levels/${position}`));
  }

  // ---- the defaults
  async function setDefault(which: 'chat' | 'unattended', value: string): Promise<void> {
    const level = Number(value);
    if (level === data.top && !(await confirmDialog({ title: 'The last level', message: TOP_WARNING, confirm: 'Use this level', danger: true }))) {
      await load();
      return;
    }
    await change(() => api.put('/v1/levels/defaults', { [which]: level }));
  }

  // ---- the tools
  async function setTool(tool: ToolRow, ask: number, approve: number): Promise<void> {
    if (ask > approve) {
      // the one you did not touch follows: asking cannot start after running does
      if (ask !== tool.ask_from) approve = ask;
      else ask = approve;
    }
    await change(() => api.put(`/v1/levels/tools/${tool.name}`, { ask_from: ask, approve_from: approve }));
  }

  const reset = (tool: ToolRow): Promise<boolean> => change(() => api.del(`/v1/levels/tools/${tool.name}`));

  async function setGroup(klass: string, tools: ToolRow[]): Promise<void> {
    const ask = Number(groupAsk[klass]);
    const approve = Number(groupRun[klass]);
    if (!ask || !approve) return;
    if (ask > approve) {
      toast('A tool cannot ask from a level above the one it runs from.', 'error');
      return;
    }
    for (const tool of tools) {
      if (!(await change(() => api.put(`/v1/levels/tools/${tool.name}`, { ask_from: ask, approve_from: approve })))) return;
    }
    toast(`${tools.length} tools set.`);
  }

  const shown = $derived(
    data.tools.filter((tool) => !filter.trim() || `${tool.name} ${tool.description} ${tool.classes.join(' ')}`.toLowerCase().includes(filter.trim().toLowerCase())),
  );
  const groups = $derived(
    data.classes
      .map((row) => ({ ...row, tools: shown.filter((tool) => (tool.classes[0] ?? '') === row.class) }))
      .filter((group) => group.tools.length),
  );
</script>

<div class="page stack">
  <p class="intro">A conversation runs at an <b>auto level</b>: level 0 refuses every tool, the last level runs every tool, and in between each tool
    has the level it <b>asks</b> from and the level it <b>runs</b> from (below the first it is refused). Set the level of a
    conversation in the chat, of all the conversations of a project on its page, and of the runs of a schedule or a hook on theirs.
    The rules of the Rules &amp; approvals page still apply, except that a rule cannot open a tool the level refuses.</p>

  <section class="panel stack">
    <h2>Levels</h2>
    <table>
      <thead><tr><th>Level</th><th>Name</th><th><span class="sr-only">Actions</span></th></tr></thead>
      <tbody>
        {#each data.levels as level (level.position)}
          <tr>
            <td class="num">{level.position}</td>
            <td>
              {#if level.position === 0 || level.position === data.top}
                <b>{level.name}</b> <span class="muted small">· fixed{level.position === 0 ? ': every tool is refused' : ': every tool runs'}</span>
              {:else}
                <input aria-label={`Name of level ${level.position}`} value={level.name} maxlength="30" onchange={(event) => rename(level.position, (event.target as HTMLInputElement).value)} />
              {/if}
            </td>
            <td>{#if level.position > 0 && level.position < data.top}<button class="small danger" onclick={() => remove(level.position)}>Remove</button>{/if}</td>
          </tr>
        {/each}
      </tbody>
    </table>
    <p class="warn small"><Icon name="warn" size={14} /> {TOP_WARNING}</p>
    <form class="row" onsubmit={add}>
      <input class="grow" aria-label="Name of the new level" placeholder="Name of a new level" maxlength="30" bind:value={added.name} required />
      <select aria-label="Where to add it" bind:value={added.before}>
        <option value="">just below the last level</option>
        {#each positions.slice(1, data.top) as position (position)}<option value={String(position)}>just below level {position} ({nameOf(position)})</option>{/each}
      </select>
      <button class="primary" disabled={data.top >= 9}>Add a level</button>
    </form>
    <p class="muted small">A new level acts like the one below it until you change the tools. At most 10 levels in all.</p>
  </section>

  <section class="panel stack">
    <h2>Defaults</h2>
    <p class="muted small">The level of a conversation that has none of its own, none from its schedule or hook and none from its project.</p>
    <div class="row">
      <label class="row">Chats
        <select aria-label="Default level of a chat" value={String(data.defaults.chat)} onchange={(event) => setDefault('chat', (event.target as HTMLSelectElement).value)}>
          {#each data.levels as level (level.position)}<option value={String(level.position)}>{level.position} · {level.name}</option>{/each}
        </select>
      </label>
      <label class="row">Schedules, reminders and hooks
        <select aria-label="Default level of a run nobody watches" value={String(data.defaults.unattended)} onchange={(event) => setDefault('unattended', (event.target as HTMLSelectElement).value)}>
          {#each data.levels as level (level.position)}<option value={String(level.position)}>{level.position} · {level.name}</option>{/each}
        </select>
      </label>
    </div>
    <p class="muted small">A run nobody watches never skips the tainted-turn rule or the secret check, whatever its level.</p>
  </section>

  <section class="stack">
    <h2>Tools</h2>
    <div class="row">
      <input class="grow" type="search" aria-label="Find a tool" placeholder="Find a tool or an action class" bind:value={filter} />
      <span class="legend small"><span class="cell refuse">refused</span> <span class="cell ask">asks</span> <span class="cell run">runs</span> at each level</span>
    </div>
    {#each groups as group (group.class)}
      <div class="panel stack">
        <div class="row group-head">
          <h3 class="grow">{group.description} <span class="muted small">({group.class})</span></h3>
          <label class="row small">asks from
            <select aria-label={`Asks from, for the tools of ${group.class}`} bind:value={groupAsk[group.class]}>
              <option value="">…</option>{#each positions.slice(1) as position (position)}<option value={String(position)}>{position}</option>{/each}
            </select></label>
          <label class="row small">runs from
            <select aria-label={`Runs from, for the tools of ${group.class}`} bind:value={groupRun[group.class]}>
              <option value="">…</option>{#each positions.slice(1) as position (position)}<option value={String(position)}>{position}</option>{/each}
            </select></label>
          <button class="small" disabled={!groupAsk[group.class] || !groupRun[group.class]} onclick={() => setGroup(group.class, group.tools)}>Set these {group.tools.length}</button>
        </div>
        <table>
          <thead><tr><th>Tool</th><th>Asks from</th><th>Runs from</th><th>At each level</th><th><span class="sr-only">Actions</span></th></tr></thead>
          <tbody>
            {#each group.tools as tool (tool.name)}
              <tr class:changed={tool.changed}>
                <td><code>{tool.name}</code><div class="muted small">{tool.description}</div></td>
                <td>
                  <select aria-label={`${tool.name} asks from`} value={String(tool.ask_from)} onchange={(event) => setTool(tool, Number((event.target as HTMLSelectElement).value), tool.approve_from)}>
                    {#each positions.slice(1) as position (position)}<option value={String(position)}>{position}</option>{/each}
                  </select>
                </td>
                <td>
                  <select aria-label={`${tool.name} runs from`} value={String(tool.approve_from)} onchange={(event) => setTool(tool, tool.ask_from, Number((event.target as HTMLSelectElement).value))}>
                    {#each positions.slice(1) as position (position)}<option value={String(position)}>{position}</option>{/each}
                  </select>
                </td>
                <td>
                  <span class="bar" role="img" aria-label={`Refused below ${tool.ask_from}, asks from ${tool.ask_from}, runs from ${tool.approve_from}`}>
                    {#each cells(tool.ask_from, tool.approve_from, data.top) as effect, level (level)}<span class="cell {effect}" title={`Level ${level} (${nameOf(level)}): ${effect === 'run' ? 'runs' : effect === 'ask' ? 'asks' : 'refused'}`}>{level}</span>{/each}
                  </span>
                </td>
                <td>{#if tool.changed}<button class="small" title={`Back to the default: asks from ${tool.default_ask_from}, runs from ${tool.default_approve_from}`} onclick={() => reset(tool)}>Reset</button>{/if}</td>
              </tr>
            {/each}
          </tbody>
        </table>
      </div>
    {:else}<div class="empty">No tool matches.</div>{/each}
  </section>
</div>

<style>
  .group-head h3 {
    margin: 0;
  }
  .bar {
    display: inline-flex;
    gap: 2px;
  }
  .cell {
    display: inline-flex;
    align-items: center;
    justify-content: center;
    min-width: 1.5rem;
    padding: 0 var(--sp-6);
    border-radius: var(--r-sm);
    font-size: var(--fs-2xs);
    font-variant-numeric: tabular-nums;
    border: 1px solid transparent;
  }
  .cell.run {
    background: var(--ok-soft);
    color: var(--ok);
  }
  .cell.ask {
    background: var(--warn-soft);
    color: var(--warn);
  }
  .cell.refuse {
    background: var(--danger-soft);
    color: var(--danger);
  }
  .legend {
    display: inline-flex;
    gap: var(--sp-6);
    align-items: center;
    flex-wrap: wrap;
  }
  tr.changed td:first-child {
    box-shadow: inset 2px 0 0 var(--warn);
  }
</style>
