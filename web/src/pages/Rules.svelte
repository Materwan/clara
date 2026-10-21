<script lang="ts">
  import Icon from '../components/Icon.svelte';
  import ApprovalCard from '../components/ApprovalCard.svelte';
  import Checkbox from '../components/Checkbox.svelte';
  import Segmented from '../components/Segmented.svelte';
  import { api, when } from '../lib/api';
  import { confirmDialog } from '../lib/dialog.svelte';
  import { live, toast } from '../lib/state.svelte';

  type Tab = 'pending' | 'rules' | 'test' | 'audit';
  const tabs = $derived([
    { value: 'pending' as Tab, label: 'Waiting', count: live.approvals.length },
    { value: 'rules' as Tab, label: 'Rules' },
    { value: 'test' as Tab, label: 'Test' },
    { value: 'audit' as Tab, label: 'Audit log' },
  ]);

  let tab = $state<Tab>('pending');
  let data = $state<any>({ rules: [], defaults: [], tools: [], tainted_rule: true });
  let history = $state<any[]>([]);
  let audit = $state<any[]>([]);
  const empty = () => ({ id: 0, effect: 'ask', tool: '', target: '', situation: '', level: '', note: '' });
  let form = $state(empty());
  let test = $state({ tool: 'ws_delete', arguments: '{"path": "notes/old.md"}', situation: 'interactive', tainted: false, local_only: false });
  let result = $state<any>(null);

  async function load(): Promise<void> {
    data = await api.get('/v1/rules');
    history = (await api.get('/v1/approvals', { limit: 100 })).approvals;
    audit = (await api.get('/v1/audit', { limit: 200 })).entries;
  }

  $effect(() => {
    void live.refresh;
    load();
  });

  async function save(event: SubmitEvent): Promise<void> {
    event.preventDefault();
    const { id, ...fields } = form;
    try {
      if (id) await api.patch(`/v1/rules/${id}`, fields);
      else await api.post('/v1/rules', fields);
      form = empty();
      load();
    } catch (error) {
      toast((error as Error).message, 'error');
    }
  }

  function edit(rule: any): void {
    const { id, effect, tool, target, situation, level, note } = rule;
    form = { id, effect, tool, target, situation, level, note };
  }

  async function toggle(rule: any): Promise<void> {
    await api.patch(`/v1/rules/${rule.id}`, { enabled: !rule.enabled });
    load();
  }

  async function remove(rule: any): Promise<void> {
    if (!(await confirmDialog({ title: 'Delete rule', message: `Delete rule #${rule.id}?`, confirm: 'Delete', danger: true }))) return;
    await api.del(`/v1/rules/${rule.id}`);
    load();
  }

  /** allow, ask, refuse: the same three words and colours as every other state (ok, warn, danger). */
  const kind = (effect: string): string => (effect === 'allow' ? 'ok' : effect === 'ask' ? 'warn' : 'danger');

  async function tainted(on: boolean): Promise<void> {
    await api.put('/v1/rules/tainted', { on });
    load();
  }

  async function runTest(event: SubmitEvent): Promise<void> {
    event.preventDefault();
    try {
      result = await api.post('/v1/rules/test', { tool: test.tool, arguments: JSON.parse(test.arguments || '{}'), situation: test.situation, tainted: test.tainted, local_only: test.local_only });
    } catch (error) {
      toast((error as Error).message, 'error');
    }
  }
</script>

<div class="page stack">
  <p class="intro">Every action Clara takes resolves to allow, ask or refuse. The most specific matching rule wins (more
    fields set, then more literal characters in the target); on a tie the stricter one. Clara can neither read nor change
    these, nor answer a request.</p>
  <div><Segmented label="Section" options={tabs} bind:value={tab} /></div>

  {#if tab === 'pending'}
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
  {:else if tab === 'rules'}
    <label class="row"><Checkbox checked={data.tainted_rule} onchange={(event) => tainted((event.target as HTMLInputElement).checked)} />
      Tainted-turn rule: after reading untrusted content (a web page, an issue, a fetched file), outside-world writes and
      networked scripts ask at least.</label>
    <form class="panel stack" onsubmit={save}>
      <div class="row">
        <select aria-label="Effect" bind:value={form.effect}><option>allow</option><option>ask</option><option>refuse</option></select>
        <input list="tools" aria-label="Tool" placeholder="tool or glob (github_*)" bind:value={form.tool} />
        <datalist id="tools">{#each data.tools as tool}<option value={tool}></option>{/each}</datalist>
        <input class="grow" aria-label="Target" placeholder="target: scratch/**, github:me/repo, domain:*.wikipedia.org, drive:<id>, vault:…, net" bind:value={form.target} />
        <select aria-label="Situation" bind:value={form.situation}><option value="">any situation</option><option>interactive</option><option>unattended</option><option>tainted</option></select>
        <select aria-label="Level" bind:value={form.level}><option value="">any level</option><option>read</option><option>write</option><option>destructive</option></select>
      </div>
      <div class="row"><input class="grow" aria-label="Note" placeholder="note" bind:value={form.note} /><button class="primary">{form.id ? 'Save rule' : 'Add rule'}</button>
        {#if form.id}<button type="button" onclick={() => (form = empty())}>Cancel</button>{/if}</div>
    </form>
    <table>
      <thead><tr><th>#</th><th>Effect</th><th>Tool</th><th>Target</th><th>Situation</th><th>Level</th><th>Origin</th><th>Hits</th><th><span class="sr-only">Actions</span></th></tr></thead>
      <tbody>
        {#each data.rules as rule (rule.id)}
          <tr class:off={!rule.enabled}>
            <td class="num">{rule.id}</td><td><span class="tag {kind(rule.effect)}">{rule.effect}</span></td><td>{rule.tool || '*'}</td><td><code>{rule.target || '*'}</code></td>
            <td>{rule.situation || 'any'}</td><td>{rule.level || 'any'}</td><td class="small">{rule.origin}{rule.note ? ` · ${rule.note}` : ''}</td>
            <td class="small">{rule.hits}</td>
            <td class="row"><button class="small" onclick={() => edit(rule)}>Edit</button><button class="small" onclick={() => toggle(rule)}>{rule.enabled ? 'Off' : 'On'}</button>
              <button class="small danger" onclick={() => remove(rule)}>Delete</button></td>
          </tr>
        {:else}<tr><td colspan="9" class="empty">No rules: the defaults below decide.</td></tr>{/each}
      </tbody>
    </table>
    <h2>Defaults</h2>
    <table>
      <thead><tr><th>Action class</th><th>In a chat</th><th>Unattended</th></tr></thead>
      <tbody>{#each data.defaults as row}<tr><td>{row.description} <span class="muted small">({row.class})</span></td><td><span class="tag {kind(row.interactive)}">{row.interactive}</span></td><td><span class="tag {kind(row.unattended)}">{row.unattended}</span></td></tr>{/each}</tbody>
    </table>
  {:else if tab === 'test'}
    <form class="panel stack" onsubmit={runTest}>
      <div class="row">
        <select aria-label="Tool" bind:value={test.tool}>{#each data.tools as tool}<option>{tool}</option>{/each}</select>
        <select aria-label="Situation" bind:value={test.situation}><option>interactive</option><option>unattended</option></select>
        <label class="row small"><Checkbox bind:checked={test.tainted} /> tainted turn</label>
        <label class="row small"><Checkbox bind:checked={test.local_only} /> local-only conversation</label>
      </div>
      <textarea rows="3" aria-label="Arguments (JSON)" bind:value={test.arguments}></textarea>
      <button class="primary">Which rule decides?</button>
    </form>
    {#if result}
      <div class="panel stack">
        <div><span class="tag big {kind(result.effect)}">{result.effect}</span> {result.reason}{result.floor ? ' (the floor: no rule can change it)' : ''}</div>
        <div class="small">{result.action.summary} · {result.action.class} · {result.action.level} · {result.action.targets.join(' ')}</div>
        {#if result.raised.length}<div class="warn small">Raised: {result.raised.join('; ')}</div>{/if}
        <div class="small muted">Default for this class: chat {result.default.interactive}, unattended {result.default.unattended}. Matching rules: {result.matching.map((rule: any) => '#' + rule.id).join(', ') || 'none'}</div>
      </div>
    {/if}
  {:else}
    <table>
      <thead><tr><th>When</th><th>Tool</th><th>Target</th><th>Situation</th><th>Effect</th><th>Outcome</th></tr></thead>
      <tbody>
        {#each audit as entry (entry.id)}
          <tr><td class="small">{when(entry.at)}</td><td>{entry.tool}</td><td class="small"><code>{entry.target}</code></td><td class="small">{entry.situation}</td>
            <td><span class="tag {kind(entry.effect)}">{entry.effect}</span>{entry.rule_id ? ` #${entry.rule_id}` : ''}</td><td>{entry.outcome}{entry.approval_id ? ` #${entry.approval_id}` : ''}</td></tr>
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
</style>
