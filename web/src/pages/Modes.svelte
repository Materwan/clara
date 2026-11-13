<script lang="ts">
  import Icon from '../components/Icon.svelte';
  import ApprovalCard from '../components/ApprovalCard.svelte';
  import Checkbox from '../components/Checkbox.svelte';
  import Segmented from '../components/Segmented.svelte';
  import { api, when } from '../lib/api';
  import { confirmDialog, promptDialog } from '../lib/dialog.svelte';
  import {
    type Effective, type Line, type Uses, type Verdict, KIND, VERDICTS, confirmQuery, deleteWarning, effectiveOf, exceptionsOf, otherLines, ownLine,
    sameUses, taintedAfter, taintedChoices, toolsOf, usedBy,
  } from '../lib/modes';
  import { live, route, session, toast } from '../lib/state.svelte';

  type Tab = 'modes' | 'waiting' | 'test' | 'audit';
  const tabs = $derived([
    { value: 'modes' as Tab, label: 'Modes' },
    { value: 'waiting' as Tab, label: 'Waiting', count: live.approvals.length },
    { value: 'test' as Tab, label: 'Test' },
    { value: 'audit' as Tab, label: 'Audit log' },
  ]);

  type Mode = { name: string; packs: string[]; verdicts: Line[]; default: boolean; uses?: Uses; file?: string; from_file?: boolean; effective?: Effective[] };
  let tab = $state<Tab>((['modes', 'waiting', 'test', 'audit'] as Tab[]).find((value) => value === route.query.get('tab')) ?? 'modes');
  let index = $state<{ default: Mode | null; modes: Mode[]; tools: any[]; classes: { class: string; description: string }[]; packs: { id: string; label: string; where: string }[] }>(
    { default: null, modes: [], tools: [], classes: [], packs: [] },
  );
  let selected = $state('default');
  let detail = $state<Mode | null>(null);
  let filter = $state('');
  let adding = $state<{ tool: string; klass: string; target: string } | null>(null);
  let history = $state<any[]>([]);
  let audit = $state<any[]>([]);
  let test = $state({ tool: 'ws_delete', arguments: '{"path": "notes/old.md"}', situation: 'interactive', mode: '', tainted: false, local_only: false });
  let result = $state<any>(null);

  /** Only a token that may answer everything changes a mode (the others get a 403: they are shown the modes, read-only). */
  const canEdit = $derived(session.approvals === 'all');
  const locked = $derived(!canEdit || !!detail?.default);

  async function loadIndex(): Promise<void> {
    index = await api.get('/v1/modes');
    if (selected !== 'default' && !index.modes.some((mode) => mode.name === selected)) selected = 'default';
  }

  async function open(name: string): Promise<void> {
    selected = name;
    adding = null;
    detail = await api.get(`/v1/modes/${encodeURIComponent(name)}`);
  }

  async function load(): Promise<void> {
    await loadIndex();
    await open(selected);
  }

  async function loadOthers(): Promise<void> {
    history = (await api.get('/v1/approvals', { limit: 100 })).approvals;
    audit = (await api.get('/v1/audit', { limit: 200 })).entries;
  }

  $effect(() => {
    void live.refresh;
    load().catch((error) => toast((error as Error).message, 'error'));
    loadOthers().catch(() => undefined);
  });

  /** Run a change of the selected mode; the server answers with the mode as it is now. Say why when it is refused. */
  async function change(call: () => Promise<Mode>): Promise<void> {
    try {
      detail = await call();
      await loadIndex();
    } catch (error) {
      toast((error as Error).message, 'error');
      await load().catch(() => undefined);
    }
  }

  const base = $derived(`/v1/modes/${encodeURIComponent(selected)}`);

  // ---- the lines
  const setLine = (tool: string, klass: string, target: string, verdict: Verdict, tainted: Verdict): Promise<void> =>
    change(() => api.put(`${base}/verdicts`, { tool, class: klass, target, verdict, tainted }));
  const removeLine = (line: Line): Promise<void> =>
    change(() => api.del(`${base}/verdicts?${new URLSearchParams({ tool: line.tool, class: line.class, target: line.target })}`));

  async function addException(event: SubmitEvent, line: { verdict: Verdict; tainted: Verdict }): Promise<void> {
    event.preventDefault();
    if (!adding || !adding.target.trim()) return;
    const { tool, klass, target } = adding;
    adding = null;
    await setLine(tool, klass, target.trim(), line.verdict, line.tainted);
  }

  // ---- the mode
  async function make(): Promise<void> {
    const name = await promptDialog({ title: 'New mode', message: 'Name of the new mode', label: 'Name (30 characters at most)', confirm: 'Make it' });
    if (!name?.trim()) return;
    try {
      const made = await api.post('/v1/modes', { name: name.trim() });
      await loadIndex();
      await open(made.name);
      toast('Made: it follows the default mode for every tool until you change one.', 'ok');
    } catch (error) {
      toast((error as Error).message, 'error');
    }
  }

  const togglePack = (id: string): Promise<void> =>
    change(() => api.put(base, { packs: detail!.packs.includes(id) ? detail!.packs.filter((item) => item !== id) : [...detail!.packs, id] }));

  async function remove(): Promise<void> {
    if (!detail || detail.default) return;
    const name = detail.name;
    for (let tries = 0; tries < 3; tries++) {
      const shown: Uses = (await api.get(`/v1/modes/${encodeURIComponent(name)}`)).uses;
      if (!(await confirmDialog({ title: `Delete the mode ${name}`, message: deleteWarning(shown), confirm: 'Delete mode', danger: true }))) return;
      try {
        await api.del(`/v1/modes/${encodeURIComponent(name)}?${confirmQuery(shown)}`);
        selected = 'default';
        toast(`Deleted ${name}.`, 'ok');
        await load();
        return;
      } catch (error) {
        // 409: something started or stopped using it while the warning was open; the numbers are read again and shown again
        if ((error as { code?: string }).code !== 'needs_confirmation') {
          toast((error as Error).message, 'error');
          return;
        }
        const now: Uses = (await api.get(`/v1/modes/${encodeURIComponent(name)}`)).uses;
        if (sameUses(now, shown)) return;
      }
    }
  }

  // ---- what is shown
  const rowOf = (tool: string, klass: string): Effective | undefined => effectiveOf(detail?.effective ?? [], tool, klass);
  const groups = $derived(
    index.classes.map((row) => ({ ...row, tools: toolsOf(index.tools, row.class, filter) })).filter((group) => group.tools.length),
  );
  const others = $derived(otherLines(detail?.verdicts ?? [], index.tools));

  // ---- the test box
  async function runTest(event: SubmitEvent): Promise<void> {
    event.preventDefault();
    try {
      result = await api.post('/v1/modes/test', {
        tool: test.tool, arguments: JSON.parse(test.arguments || '{}'), situation: test.situation, tainted: test.tainted, local_only: test.local_only,
        mode: test.mode || undefined,
      });
    } catch (error) {
      toast((error as Error).message, 'error');
    }
  }

  /** allow, ask, refuse: the same three words and colours as every other state (ok, warn, danger). */
  const kind = (effect: string): string => (effect === 'allow' ? 'ok' : effect === 'ask' ? 'warn' : 'danger');
  const lineText = (line: Line): string => `${line.tool || '*'} · ${line.class || 'any class'}${line.target ? ` · ${line.target}` : ''}: ${line.verdict}, if tainted ${line.tainted}`;
</script>

{#snippet pick(value: Verdict, choices: Verdict[], label: string, onpick: (verdict: Verdict) => void)}
  <select class="verdict {KIND[value]}" aria-label={label} {value} disabled={locked} onchange={(event) => onpick((event.target as HTMLSelectElement).value as Verdict)}>
    {#each choices as choice (choice)}<option value={choice}>{choice}</option>{/each}
  </select>
{/snippet}

<div class="page stack">
  <p class="intro">A <b>mode</b> says, for each tool, whether it runs without asking (<span class="tag ok">approve</span>), asks you
    (<span class="tag warn">ask</span>) or is refused (<span class="tag danger">refuse</span>), and which special tools it switches on.
    Set the mode of a conversation in the chat, of a project's conversations on its page, and of the runs of a schedule or a hook on theirs.
    Clara can neither read nor change a mode, nor answer a request.</p>
  <div><Segmented label="Section" options={tabs} bind:value={tab} /></div>

  {#if tab === 'modes'}
    {#if !canEdit}<p class="muted small">This token may not change modes: you can look at them.</p>{/if}
    <div class="modes">
      <aside class="side stack">
        <ul class="list" aria-label="Modes">
          {#each [...(index.default ? [index.default] : []), ...index.modes] as mode (mode.name)}
            <li><button class="pick" class:on={mode.name === selected} aria-current={mode.name === selected ? 'true' : undefined} onclick={() => open(mode.name)}>
              {#if mode.default}<Icon name="lock" size={14} />{/if}{mode.name}{#if mode.default}<span class="muted small"> (file)</span>{/if}</button></li>
          {/each}
          <li><button class="pick new" disabled={!canEdit} onclick={make}>+ New mode</button></li>
        </ul>
        <div class="picker row">
          <select class="grow" aria-label="Mode" value={selected} onchange={(event) => open((event.target as HTMLSelectElement).value)}>
            <option value="default">default (file)</option>
            {#each index.modes as mode (mode.name)}<option value={mode.name}>{mode.name}</option>{/each}
          </select>
          <button disabled={!canEdit} onclick={make}>+ New mode</button>
        </div>
        {#if detail && !detail.default && detail.uses}<p class="muted small">{usedBy(detail.uses)}</p>{/if}
      </aside>

      {#if detail}
        <section class="detail stack">
          <div class="row">
            <h2 class="grow">{#if detail.default}<Icon name="lock" size={18} /> {/if}{detail.name}</h2>
            {#if !detail.default}<button class="danger" disabled={!canEdit} onclick={remove}>Delete mode</button>{/if}
          </div>
          {#if detail.default}
            <p class="muted small"><Icon name="lock" size={14} /> The default mode is a file the server reads (<code>{detail.file}</code>): it is shown here and changed there.
              Every other mode takes its verdict from it for a tool it does not name.</p>
            {#if detail.from_file === false}
              <p class="warn small"><Icon name="warn" size={14} /> The file <code>{detail.file}</code> is missing: the built-in copy applies.</p>
            {/if}
          {/if}
          <div class="row">
            <b>Switches on:</b>
            {#each index.packs as pack (pack.id)}
              <button class="chip-toggle" aria-pressed={detail.packs.includes(pack.id)} disabled={locked} title={pack.where} onclick={() => togglePack(pack.id)}>{pack.label}</button>
            {/each}
          </div>

          <input class="grow" type="search" aria-label="Find a tool or an action class" placeholder="Find a tool or an action class" bind:value={filter} />
          {#each groups as group (group.class)}
            <div class="panel stack">
              <h3>{group.description} <span class="muted small">({group.class})</span></h3>
              <table>
                <thead><tr><th>Tool</th><th>Normal turn</th><th>If tainted</th></tr></thead>
                <tbody>
                  {#each group.tools as tool (tool.name)}
                    {@const row = rowOf(tool.name, group.class)}
                    {@const own = ownLine(detail.verdicts, tool.name, group.class)}
                    {#if row}
                      <tr class:inherited={row.from === 'default' && !detail.default}>
                        <td>
                          <code>{tool.name}</code>
                          <div class="muted small">{tool.description}</div>
                          {#if row.from === 'default' && !detail.default}<div class="muted small">from default</div>{/if}
                          {#if own && !detail.default}<button class="small" disabled={locked} title="Remove this mode's line: the tool takes the default mode's verdict again" onclick={() => removeLine(own)}>Reset</button>{/if}
                          {#if !locked}<button class="small" onclick={() => (adding = { tool: tool.name, klass: group.class, target: '' })}>+ exception</button>{/if}
                        </td>
                        <td>{@render pick(row.verdict, VERDICTS, `${tool.name}, ${group.class}: normal turn`, (verdict) => setLine(tool.name, group.class, '', verdict, taintedAfter(verdict, row.tainted)))}</td>
                        <td>{@render pick(row.tainted, taintedChoices(row.verdict), `${tool.name}, ${group.class}: if tainted`, (tainted) => setLine(tool.name, group.class, '', row.verdict, tainted))}</td>
                      </tr>
                      {#each exceptionsOf(detail.verdicts, tool.name, group.class) as line (line.target)}
                        <tr class="exception">
                          <td><span aria-hidden="true">↳</span> <code>{line.target}</code>
                            {#if !locked}<button class="small danger" aria-label={`Remove the exception ${line.target} of ${tool.name}`} onclick={() => removeLine(line)}>Remove</button>{/if}</td>
                          <td>{@render pick(line.verdict, VERDICTS, `${tool.name} ${line.target}: normal turn`, (verdict) => setLine(line.tool, line.class, line.target, verdict, taintedAfter(verdict, line.tainted)))}</td>
                          <td>{@render pick(line.tainted, taintedChoices(line.verdict), `${tool.name} ${line.target}: if tainted`, (tainted) => setLine(line.tool, line.class, line.target, line.verdict, tainted))}</td>
                        </tr>
                      {/each}
                      {#if adding && adding.tool === tool.name && adding.klass === group.class}
                        <tr class="exception">
                          <td colspan="3">
                            <form class="row" onsubmit={(event) => addException(event, { verdict: 'ask', tainted: 'ask' })}>
                              <input class="grow" aria-label={`Target of the new exception for ${tool.name}`} placeholder="*@epita.fr, ws:private/**, domain:*.wikipedia.org" bind:value={adding.target} required />
                              <button class="primary small">Add (asks)</button>
                              <button type="button" class="small" onclick={() => (adding = null)}>Cancel</button>
                            </form>
                          </td>
                        </tr>
                      {/if}
                    {/if}
                  {/each}
                </tbody>
              </table>
            </div>
          {:else}<div class="empty">No tool matches.</div>{/each}

          {#if others.length}
            <div class="panel stack">
              <h3>Other lines</h3>
              <p class="muted small">Lines that cover several tools or a whole class: they decide the rows above that have no line of their own.</p>
              <ul class="plain">
                {#each others as line (`${line.tool}|${line.class}|${line.target}`)}
                  <li class="row"><code class="grow">{lineText(line)}</code>
                    {#if !locked}<button class="small danger" onclick={() => removeLine(line)}>Remove</button>{/if}</li>
                {/each}
              </ul>
            </div>
          {/if}
          <p class="muted small">If tainted is never more permissive than the normal turn. A tool this mode does not name takes its verdict from the default mode.
            The gate's floor, local only, chat only and the secret check apply in every mode.</p>
        </section>
      {/if}
    </div>
  {:else if tab === 'waiting'}
    {#each live.approvals as approval (approval.id)}
      <div><ApprovalCard {approval} /><span class="muted small">{when(approval.created_at)} · expires {when(approval.expires_at)}
        {#if approval.conversation}· <a href={'#/chat/' + approval.conversation}>conversation</a>{/if}</span></div>
    {:else}<div class="empty"><Icon name="check" size={18} /> Nothing waits for you.</div>{/each}
    <h2>Recent</h2>
    <table>
      <thead><tr><th>#</th><th>Action</th><th>Status</th><th>Answered by</th></tr></thead>
      <tbody>
        {#each history.filter((item) => item.status !== 'pending') as item (item.id)}
          <tr><td class="num">{item.id}</td><td>{item.summary}<div class="muted small">{item.tool} · {item.level} · {item.reason}</div></td>
            <td>{item.status}{item.answer ? ` (${item.answer})` : ''}</td><td class="small">{item.decided_by || '—'} {when(item.decided_at)}</td></tr>
        {:else}<tr><td colspan="4" class="empty">No request answered yet.</td></tr>{/each}
      </tbody>
    </table>
  {:else if tab === 'test'}
    <form class="panel stack" onsubmit={runTest}>
      <div class="row">
        <select aria-label="Tool" bind:value={test.tool}>{#each index.tools as tool (tool.name)}<option>{tool.name}</option>{/each}</select>
        <select aria-label="Situation" bind:value={test.situation}><option>interactive</option><option>unattended</option></select>
        <select aria-label="Mode" bind:value={test.mode}><option value="">the default mode</option>{#each index.modes as mode (mode.name)}<option value={mode.name}>{mode.name}</option>{/each}</select>
        <label class="row small"><Checkbox bind:checked={test.tainted} /> tainted turn</label>
        <label class="row small"><Checkbox bind:checked={test.local_only} /> local-only conversation</label>
      </div>
      <textarea rows="3" aria-label="Arguments (JSON)" bind:value={test.arguments}></textarea>
      <button class="primary">Which line decides?</button>
    </form>
    {#if result}
      <div class="panel stack">
        <div><span class="tag big {kind(result.effect)}">{result.effect}</span> {result.reason}{result.floor ? ' (the floor: no mode can change it)' : result.by_mode ? ' (the mode refuses it: nothing can change that)' : ''}</div>
        <div class="small">{result.action.summary} · {result.class.name} ({result.class.description}) · {result.action.level} · {result.action.targets.join(' ')}</div>
        {#if result.raised.length}<div class="warn small">Raised: {result.raised.join('; ')}</div>{/if}
        <div class="small muted">
          {#if result.line}Decided by the line <code>{lineText(result.line)}</code> of the {result.from_default ? 'default mode (this mode does not name it)' : `mode ${result.mode}`}.
            Normal turn: <span class="tag {KIND[result.verdicts.normal as Verdict]}">{result.verdicts.normal}</span>
            if tainted: <span class="tag {KIND[result.verdicts.tainted as Verdict]}">{result.verdicts.tainted}</span>
          {:else}No line of the mode {result.mode} decides it.{/if}
        </div>
      </div>
    {/if}
  {:else}
    <table>
      <thead><tr><th>When</th><th>Tool</th><th>Target</th><th>Situation</th><th>Effect</th><th>Outcome</th></tr></thead>
      <tbody>
        {#each audit as entry (entry.id)}
          <tr><td class="small">{when(entry.at)}</td><td>{entry.tool}</td><td class="small"><code>{entry.target}</code></td><td class="small">{entry.situation}</td>
            <td><span class="tag {kind(entry.effect)}">{entry.effect}</span></td><td>{entry.outcome}{entry.approval_id ? ` #${entry.approval_id}` : ''}</td></tr>
        {/each}
      </tbody>
    </table>
  {/if}
</div>

<style>
  .big {
    font-size: var(--fs-lg);
    padding-block: var(--sp-4);
  }
  .modes {
    display: grid;
    grid-template-columns: minmax(12rem, 16rem) minmax(0, 1fr);
    gap: var(--sp-24);
    align-items: start;
  }
  .list,
  .plain {
    list-style: none;
    margin: 0;
    padding: 0;
  }
  .pick {
    display: flex;
    align-items: center;
    gap: var(--sp-6);
    width: 100%;
    text-align: left;
    background: transparent;
    border: 1px solid transparent;
  }
  .pick.on {
    background: var(--panel-2);
    border-color: var(--line-strong);
  }
  .pick.new {
    border: 1px dashed var(--line-strong);
    color: var(--muted);
  }
  .picker {
    display: none;
  }
  .detail h2 {
    margin: 0;
  }
  .detail table {
    position: relative; /* the hidden headers of a table are positioned against it, not the page */
  }
  .chip-toggle {
    border-radius: var(--r-pill);
    font-size: var(--fs-xs);
  }
  .chip-toggle[aria-pressed='true'] {
    background: var(--ok-soft);
    color: var(--ok);
    border-color: var(--ok);
  }
  select.verdict {
    font-size: var(--fs-xs);
    border-color: transparent;
  }
  select.verdict.ok {
    background-color: var(--ok-soft);
    color: var(--ok);
  }
  select.verdict.warn {
    background-color: var(--warn-soft);
    color: var(--warn);
  }
  select.verdict.danger {
    background-color: var(--danger-soft);
    color: var(--danger);
  }
  select.verdict option {
    background: var(--raised);
    color: var(--text);
  }
  tr.inherited {
    opacity: 0.6;
  }
  tr.exception td:first-child {
    padding-left: var(--sp-24);
  }
  td code {
    overflow-wrap: anywhere;
  }
  @media (max-width: 720px) {
    .modes {
      grid-template-columns: minmax(0, 1fr);
      gap: var(--sp-12);
    }
    .list {
      display: none;
    }
    .picker {
      display: flex;
    }
    .detail table td,
    .detail table th {
      padding-inline: var(--sp-6);
    }
  }
</style>
