<script lang="ts">
  import { api, when } from '../lib/api';
  import { live, toast } from '../lib/state.svelte';

  let tab = $state<'status' | 'tokens' | 'secrets' | 'settings' | 'backups' | 'updates' | 'console'>('status');
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
    if (!confirm(`Revoke ${name}? It is refused at once.`)) return;
    await api.del(`/v1/admin/tokens/${name}`);
    load();
  }

  async function setSecret(name: string): Promise<void> {
    const value = prompt(`New value of ${name} (it is never shown again)`);
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
    if (!confirm(`Remove ${name}?`)) return;
    await api.del(`/v1/admin/secrets/${name}`);
    load();
  }

  async function setSetting(item: any): Promise<void> {
    const text = prompt(`${item.key} (JSON)`, JSON.stringify(item.value));
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

  async function act(path: string, body: any = {}, question = ''): Promise<void> {
    if (question && !confirm(question)) return;
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
  <h1>Admin</h1>
  <div class="tabs">
    {#each ['status', 'tokens', 'secrets', 'settings', 'backups', 'updates', 'console'] as name}
      <button class:active={tab === name} onclick={() => (tab = name as typeof tab)}>{name}</button>
    {/each}
  </div>

  {#if tab === 'status' && status}
    <div class="grid-2">
      <div class="panel small stack">
        <div><strong>Clara {status.version}</strong> {status.release ? `(${status.release})` : '(source tree)'} · {status.state} · up {Math.round(status.uptime / 3600)} h</div>
        <div>Default model: {status.models.default || '(none)'}</div>
        <div>Turns running: {status.agent.active} · since start: {status.agent.turns} · today: {status.usage_today.calls} calls</div>
        <div>Database {status.storage.database_mb} MB · workspace {status.storage.workspace_mb} MB · disk {status.storage.disk_percent}%</div>
        <div>Scripts: {status.workspace.run_ready ? 'ready' : `off (${status.workspace.run_problem})`}</div>
        <div>Tailscale: {status.tailscale}</div>
        <div>Backups: last {status.backups.last ?? 'never'}{status.backups.remote ? ` · remote ${status.backups.last_remote ?? 'never'}` : ''}</div>
        <div>Clients: {Object.entries(status.clients).map(([name, seen]) => `${name} ${seen}`).join(', ') || '—'} · {status.events.listeners} listening</div>
        <div class="row">Debug content log: {status.debug_until ? `on until ${when(status.debug_until)}` : 'off'}
          <button class="small" onclick={() => debug(!status.debug_until)}>{status.debug_until ? 'Turn off' : 'On for 1 hour'}</button></div>
      </div>
      <div class="panel small stack">
        <h2>Alerts</h2>
        {#each Object.entries(status.alerts) as [key, text]}<div class="bad">⚠ {text} <button class="small" onclick={() => act('/v1/admin/command', { line: `/alerts clear ${key}` })}>Clear</button></div>{:else}<div class="ok">No active alert.</div>{/each}
        <h2>Server</h2>
        <div class="row">
          <button onclick={() => act('/v1/admin/restart', { now: false }, 'Restart Clara? Running answers finish first (bounded).')}>Restart</button>
          <button class="danger" onclick={() => act('/v1/admin/stop', { now: false }, 'Stop Clara? systemd will not start it again by itself.')}>Stop</button>
        </div>
      </div>
    </div>
  {:else if tab === 'tokens'}
    <form class="panel row" onsubmit={createToken}>
      <input placeholder="name" bind:value={newToken.name} required />
      <select bind:value={newToken.client}>{#each ['web', 'cli', 'console', 'discord', 'script'] as name}<option>{name}</option>{/each}</select>
      <select bind:value={newToken.approvals}><option value="">approvals: default</option><option>all</option><option>safe</option><option>none</option></select>
      <button class="primary">Create token</button>
    </form>
    {#if madeToken}<div class="panel"><p>Shown once: copy it now.</p><code class="token">{madeToken}</code> <button class="small" onclick={() => (madeToken = '')}>Done</button></div>{/if}
    <table>
      <thead><tr><th>Name</th><th>Client</th><th>May answer</th><th>Last used</th><th></th></tr></thead>
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
        <span class="small muted">Remote: {backups.remote || 'not set (CLARA_RESTIC_REPOSITORY)'} {backups.problem ? `· ${backups.problem}` : ''}</span></div>
      <table>
        <thead><tr><th>File</th><th>Kind</th><th>Size</th></tr></thead>
        <tbody>{#each backups.files as file}<tr><td><code>{file.name}</code></td><td>{file.kind}</td><td>{file.size_mb} MB</td></tr>{/each}</tbody>
      </table>
    {/if}
  {:else if tab === 'updates'}
    {#if releases}
      {#if releases.error}<p class="bad">{releases.error}</p>{/if}
      {#if releases.state}<p class="small">Running {releases.state.current} · {releases.state.state}{releases.state.detail ? `: ${releases.state.detail}` : ''}
        {releases.state.signed ? '· signatures required' : '· unsigned releases accepted'}{releases.state.launched ? '' : ' · started without the launcher: updates are off'}</p>{/if}
      {#each releases.releases as release (release.tag)}
        <div class="panel stack">
          <div class="row"><strong class="grow">{release.name}</strong><span class="muted small">{when(release.published_at)}</span>
            {#if release.current}<span class="tag ok">installed</span>{:else}<button class="primary small" onclick={() => act('/v1/admin/update', { tag: release.tag }, `Install ${release.tag}? Clara checks it, then restarts into it, and rolls back by herself if it fails.`)}>Install</button>{/if}</div>
          <details><summary class="small">Notes</summary><pre class="notes">{release.notes}</pre></details>
        </div>
      {/each}
    {/if}
  {:else if tab === 'console'}
    <div class="panel console">
      {#each output as item}<div class="muted">clara&gt; {item.line}</div><pre>{item.text}</pre>{/each}
      <form class="row" onsubmit={run}><span>clara&gt;</span><input class="grow" bind:value={line} placeholder="/help" /></form>
    </div>
  {/if}
</div>

<style>
  tr.off {
    opacity: 0.5;
  }
  .token {
    word-break: break-all;
  }
  .notes {
    white-space: pre-wrap;
  }
  .console pre {
    margin: 0.2rem 0 0.8rem;
    white-space: pre-wrap;
  }
</style>
