<script lang="ts">
  import Hint from '../components/Hint.svelte';
  import Icon from '../components/Icon.svelte';
  import LogStream from '../components/LogStream.svelte';
  import Segmented from '../components/Segmented.svelte';
  import { api, when } from '../lib/api';
  import { confirmDialog, promptDialog } from '../lib/dialog.svelte';
  import { live, toast } from '../lib/state.svelte';

  type Tab = 'status' | 'tokens' | 'secrets' | 'settings' | 'backups' | 'updates' | 'logs' | 'console';
  const TABS: { value: Tab; label: string }[] = (['status', 'tokens', 'secrets', 'settings', 'backups', 'updates', 'logs', 'console'] as Tab[]).map((value) => ({ value, label: value.charAt(0).toUpperCase() + value.slice(1) }));

  let tab = $state<Tab>('status');
  let status = $state<any>(null);
  let tokens = $state<any>({ tokens: [], you: '' });
  let secrets = $state<any[]>([]);
  let settings = $state<any[]>([]);
  let backups = $state<any>(null);
  let releases = $state<any>(null);
  let newToken = $state({ name: '', client: 'script', approvals: '' });
  let madeToken = $state('');
  let line = $state('');
  let output = $state<{ line: string; text: string }[]>([]);

  /** "under a minute", "12 min", "5 h", "3 days". */
  function upFor(seconds: number): string {
    if (seconds < 60) return 'under a minute';
    if (seconds < 3600) return `${Math.round(seconds / 60)} min`;
    if (seconds < 172_800) return `${Math.round(seconds / 3600)} h`;
    return `${Math.round(seconds / 86_400)} days`;
  }

  async function load(): Promise<void> {
    status = await api.get('/v1/admin/status');
    if (tab === 'tokens') tokens = await api.get('/v1/admin/tokens');
    if (tab === 'secrets') secrets = (await api.get('/v1/admin/secrets')).secrets;
    if (tab === 'settings') settings = (await api.get('/v1/admin/settings')).settings;
    if (tab === 'backups') backups = await api.get('/v1/admin/backups').catch(() => null);
    if (tab === 'updates') releases = await api.get('/v1/admin/releases').catch((error) => ({ error: error.message, releases: [] }));
  }

  $effect(() => {
    void tab;
    void live.refresh;
    load();
  });

  async function createToken(event: SubmitEvent): Promise<void> {
    event.preventDefault();
    try {
      madeToken = (await api.post('/v1/admin/tokens', { ...newToken, approvals: newToken.approvals || null })).token;
      newToken = { name: '', client: 'script', approvals: '' };
      load();
    } catch (error) {
      toast((error as Error).message, 'error');
    }
  }

  async function revoke(name: string): Promise<void> {
    if (!(await confirmDialog({ title: 'Revoke token', message: `Revoke ${name}? It is refused at once.`, confirm: 'Revoke', danger: true }))) return;
    await api.del(`/v1/admin/tokens/${name}`);
    load();
  }

  async function setSecret(name: string): Promise<void> {
    // type=password: what you type is not shown (the browser's prompt() would show it)
    const value = await promptDialog({ title: `Set ${name}`, message: `New value of ${name} (it is never shown again)`, label: `Value of ${name}`, type: 'password', confirm: 'Set' });
    if (!value) return;
    try {
      await api.put(`/v1/admin/secrets/${name}`, { value });
      toast(`${name}: set`, 'ok');
      load();
    } catch (error) {
      toast((error as Error).message, 'error');
    }
  }

  async function unsetSecret(name: string): Promise<void> {
    if (!(await confirmDialog({ title: 'Remove secret', message: `Remove ${name}?`, confirm: 'Remove', danger: true }))) return;
    await api.del(`/v1/admin/secrets/${name}`);
    load();
  }

  async function setSetting(item: any): Promise<void> {
    const text = await promptDialog({ title: `Change ${item.key}`, message: `${item.key} (JSON)`, label: `Value of ${item.key}, as JSON`, value: JSON.stringify(item.value) });
    if (text === null) return;
    try {
      await api.put(`/v1/admin/settings/${item.key}`, { value: JSON.parse(text) });
      load();
    } catch (error) {
      toast((error as Error).message, 'error');
    }
  }

  async function debug(on: boolean): Promise<void> {
    await api.post('/v1/admin/debug', { on });
    load();
  }

  async function act(path: string, body: any = {}, ask?: { title: string; message: string; confirm: string; danger?: boolean }): Promise<void> {
    if (ask && !(await confirmDialog(ask))) return;
    try {
      const done = await api.post(path, body);
      toast(done.message ?? 'Done.', 'ok');
      load();
    } catch (error) {
      toast((error as Error).message, 'error');
    }
  }

  async function run(event: SubmitEvent): Promise<void> {
    event.preventDefault();
    const text = line.trim();
    if (!text) return;
    line = '';
    const done = await api.post('/v1/admin/command', { line: text }).catch((error) => ({ output: `! ${error.message}` }));
    output = [...output, { line: text, text: done.output }];
  }
</script>

<div class="page stack">
  <div><Segmented label="Section" options={TABS} bind:value={tab} /></div>

  {#if tab === 'status' && status}
    <div class="grid-2">
      <div class="panel small">
        <dl class="facts">
          <dt>Clara</dt>
          <dd><strong>{status.version}</strong> {status.release ? `(${status.release})` : '(source tree)'}
            <span class="tag" class:ok={status.state === 'running'} class:warn={status.state !== 'running'}>{status.state}</span> up {upFor(status.uptime)}</dd>
          <dt>Default model</dt><dd>{status.models.default || 'none'}</dd>
          <dt>Answers</dt><dd>{status.agent.active} running · {status.agent.turns} since start · {status.usage_today.calls} calls today</dd>
          <dt>Storage</dt><dd>Database {status.storage.database_mb} MB · workspace {status.storage.workspace_mb} MB · disk {status.storage.disk_percent}% full</dd>
          <dt>Scripts</dt>
          <dd>{#if status.workspace.run_ready}<span class="tag ok">ready</span>{:else}<span class="tag warn">off</span> <span class="muted"><Hint text={status.workspace.run_problem} /></span>{/if}</dd>
          <dt>Tailscale</dt><dd>{status.tailscale}</dd>
          <dt>Backups</dt><dd>Last {status.backups.last ?? 'never'}{status.backups.remote ? ` · remote ${status.backups.last_remote ?? 'never'}` : ''}</dd>
          <dt>Clients</dt><dd>{Object.entries(status.clients).map(([name, seen]) => `${name} ${seen}`).join(', ') || 'none seen'} · {status.events.listeners} listening</dd>
          <dt>Debug log</dt>
          <dd class="row">{status.debug_until ? `On until ${when(status.debug_until)}` : 'Off'}
            <button class="small" onclick={() => debug(!status.debug_until)}>{status.debug_until ? 'Turn off' : 'On for 1 hour'}</button></dd>
        </dl>
      </div>
      <div class="panel small stack">
        <h2>Alerts</h2>
        {#each Object.entries(status.alerts) as [key, text]}<div class="danger"><Icon name="warn" size={16} /> {text} <button class="small" onclick={() => act('/v1/admin/command', { line: `/alerts clear ${key}` })}>Clear</button></div>{:else}<div class="ok"><Icon name="check" size={16} /> No active alert.</div>{/each}
        <h2>Server</h2>
        <div class="row">
          <button onclick={() => act('/v1/admin/restart', { now: false }, { title: 'Restart Clara', message: 'Restart Clara? Running answers finish first (bounded).', confirm: 'Restart' })}>Restart</button>
          <button class="danger" onclick={() => act('/v1/admin/stop', { now: false }, { title: 'Stop Clara', message: 'Stop Clara? systemd will not start it again by itself.', confirm: 'Stop', danger: true })}>Stop</button>
        </div>
      </div>
    </div>
  {:else if tab === 'tokens'}
    <form class="panel row" onsubmit={createToken}>
      <input aria-label="Token name" placeholder="name" bind:value={newToken.name} required />
      <select aria-label="Client" bind:value={newToken.client}>{#each ['web', 'cli', 'console', 'discord', 'script'] as name}<option>{name}</option>{/each}</select>
      <select aria-label="Approvals it may answer" bind:value={newToken.approvals}><option value="">approvals: default</option><option>all</option><option>safe</option><option>none</option></select>
      <button class="primary">Create token</button>
    </form>
    {#if madeToken}<div class="panel"><p>Shown once: copy it now.</p><code class="token">{madeToken}</code> <button class="small" onclick={() => (madeToken = '')}>Done</button></div>{/if}
    <table>
      <thead><tr><th>Name</th><th>Client</th><th>May answer</th><th>Last used</th><th><span class="sr-only">Actions</span></th></tr></thead>
      <tbody>
        {#each tokens.tokens as token (token.id)}
          <tr class:off={token.revoked_at}><td>{token.name}{token.name === tokens.you ? ' (this one)' : ''}</td><td>{token.client}</td><td>{token.approvals}</td>
            <td class="small">{token.revoked_at ? `revoked ${when(token.revoked_at)}` : when(token.last_used_at)}</td>
            <td>{#if !token.revoked_at && token.name !== tokens.you}<button class="small danger" onclick={() => revoke(token.name)}>Revoke</button>{/if}</td></tr>
        {/each}
      </tbody>
    </table>
  {:else if tab === 'secrets'}
    <p class="muted small">Encrypted in the database (AES-GCM); the key is a systemd credential outside the data folder. Values are never shown.</p>
    <table>
      <tbody>
        {#each secrets as secret (secret.name)}
          <tr><td><code>{secret.name}</code><div class="muted small">{secret.purpose}</div></td><td>{secret.set ? `set ${when(secret.updated_at)}` : '—'}</td>
            <td class="row">{#if secret.name !== 'google_refresh_token'}<button class="small" onclick={() => setSecret(secret.name)}>Set</button>{/if}
              {#if secret.set}<button class="small danger" onclick={() => unsetSecret(secret.name)}>Remove</button>{/if}</td></tr>
        {/each}
      </tbody>
    </table>
  {:else if tab === 'settings'}
    <table>
      <tbody>
        {#each settings as item (item.key)}
          <tr><td><code>{item.key}</code><div class="muted small">{item.description}</div></td><td><code>{JSON.stringify(item.value)}</code></td>
            <td>{#if !['debug_until', 'sandbox_image'].includes(item.key)}<button class="small" onclick={() => setSetting(item)}>Change</button>{/if}</td></tr>
        {/each}
      </tbody>
    </table>
  {:else if tab === 'backups'}
    {#if backups}
      <div class="row"><button class="primary" onclick={() => act('/v1/admin/backups')}>Back up now</button>
        <span class="small muted">Remote copy: {#if backups.remote}{backups.remote}{:else}not set up. Point <code>CLARA_RESTIC_REPOSITORY</code> at a restic repository to keep one.{/if} {backups.problem ? `· ${backups.problem}` : ''}</span></div>
      <table>
        <thead><tr><th>File</th><th>Kind</th><th>Size</th></tr></thead>
        <tbody>{#each backups.files as file}<tr><td><code>{file.name}</code></td><td>{file.kind}</td><td>{file.size_mb} MB</td></tr>{/each}</tbody>
      </table>
    {/if}
  {:else if tab === 'updates'}
    {#if releases}
      {#if releases.error}<p class="danger">{releases.error}</p>{/if}
      {#if releases.state}<p class="small">Running {releases.state.current} · {releases.state.state}{releases.state.detail ? `: ${releases.state.detail}` : ''}
        {releases.state.signed ? '· signatures required' : '· unsigned releases accepted'}{releases.state.launched ? '' : ' · started without the launcher: updates are off'}</p>{/if}
      {#each releases.releases as release (release.tag)}
        <div class="panel stack">
          <div class="row"><strong class="grow">{release.name}</strong><span class="muted small">{when(release.published_at)}</span>
            {#if release.current}<span class="tag ok">installed</span>{:else}<button class="primary small" onclick={() => act('/v1/admin/update', { tag: release.tag }, { title: 'Install a release', message: `Install ${release.tag}? Clara checks it, then restarts into it, and rolls back by herself if it fails.`, confirm: 'Install' })}>Install</button>{/if}</div>
          <details><summary class="small">Notes</summary><pre class="notes">{release.notes}</pre></details>
        </div>
      {/each}
    {/if}
  {:else if tab === 'logs'}
    <LogStream />
  {:else if tab === 'console'}
    <div class="panel console negative">
      {#each output as item}<div class="echo">clara&gt; {item.line}</div><pre>{item.text}</pre>{/each}
      <form class="row" onsubmit={run}><span aria-hidden="true">clara&gt;</span><input class="grow" aria-label="Command" bind:value={line} placeholder="/help" /></form>
    </div>
  {/if}
</div>

<style>
  .token {
    word-break: break-all;
  }
  .notes {
    white-space: pre-wrap;
  }
  /* the console is drawn on the negative (the .negative class re-reads the tokens against Prussian blue) */
  .console {
    font-family: var(--font-mono);
    font-size: var(--fs-sm);
  }
  .console pre {
    margin: var(--sp-4) 0 var(--sp-12);
    white-space: pre-wrap;
    background: none;
    border: none;
    padding: 0;
    color: var(--text);
  }
  .console .echo {
    color: var(--accent-text);
  }
  .console input {
    background: transparent;
    font-family: var(--font-mono);
  }
</style>
