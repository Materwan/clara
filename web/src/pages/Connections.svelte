<script lang="ts">
  import Icon from '../components/Icon.svelte';
  import { api } from '../lib/api';
  import { confirmDialog } from '../lib/dialog.svelte';
  import { route, toast } from '../lib/state.svelte';

  let info = $state<any>(null);
  let githubToken = $state('');
  let repo = $state('');
  let branch = $state('');

  async function load(): Promise<void> {
    info = await api.get('/v1/connections');
  }

  $effect(() => {
    load();
    finishGoogle();
  });

  async function finishGoogle(): Promise<void> {
    const state = route.query.get('google_state');
    const code = route.query.get('google_code');
    const error = route.query.get('google_error');
    if (!state) return;
    history.replaceState(null, '', '#/connections');
    if (error || !code) {
      toast(`Google said: ${error || 'no code'}`, 'error');
      return;
    }
    try {
      const found = await api.post('/v1/connections/google/finish', { state, code });
      // Google has said which account it is: Clara attaches it only if you agree
      const attach = await confirmDialog({
        title: 'Attach a Google account', message: `Attach the Google account ${found.account || '(address not given)'} to Clara?`, confirm: 'Attach it',
      });
      if (attach) await confirmGoogle(state, found.account);
    } catch (problem) {
      toast((problem as Error).message, 'error');
    }
  }

  async function connectGitHub(event: SubmitEvent): Promise<void> {
    event.preventDefault();
    try {
      const done = await api.put('/v1/connections/github', { token: githubToken.trim() });
      githubToken = '';
      toast(`GitHub connected as ${done.account}.`, 'ok');
      load();
    } catch (error) {
      toast((error as Error).message, 'error');
    }
  }

  async function addRepo(event: SubmitEvent): Promise<void> {
    event.preventDefault();
    try {
      await api.post('/v1/connections/github/repos', { repo, branch });
      repo = branch = '';
      load();
    } catch (error) {
      toast((error as Error).message, 'error');
    }
  }

  async function removeResource(id: number): Promise<void> {
    await api.del(`/v1/connections/resources/${id}`);
    load();
  }

  async function startGoogle(): Promise<void> {
    try {
      location.href = (await api.post('/v1/connections/google/start')).url;
    } catch (error) {
      toast((error as Error).message, 'error');
    }
  }

  async function confirmGoogle(state: string, account: string): Promise<void> {
    await api.post('/v1/connections/google/confirm', { state });
    toast(`Google Drive connected (${account}).`, 'ok');
    load();
  }

  async function disconnect(kind: 'github' | 'google'): Promise<void> {
    const message = kind === 'google' ? 'Disconnect Google Drive? The token is revoked at Google.' : 'Disconnect GitHub? The token is forgotten.';
    if (!(await confirmDialog({ title: kind === 'google' ? 'Disconnect Google Drive' : 'Disconnect GitHub', message, confirm: 'Disconnect', danger: true }))) return;
    await api.del(`/v1/connections/${kind}`);
    load();
  }

  function picker(): void {
    window.open('/picker.html', 'clara-picker', 'width=900,height=650');
    addEventListener('focus', load, { once: true });
  }
</script>

<div class="page narrow stack">
  <p class="intro">Clara reaches only what is listed here. Every change she makes goes through your rules; by default
    each GitHub and Drive change asks you first.</p>
  {#if info}
    <section class="panel stack">
      <h2><Icon name="branch" size={18} /> GitHub {#if info.github.connected}<span class="tag ok">{info.github.account}</span>{/if}
        {#if info.github.status === 'needs_reconnect'}<span class="tag danger">token refused</span>{/if}</h2>
      <form class="row" onsubmit={connectGitHub}>
        <input class="grow" type="password" aria-label="GitHub token" placeholder="A fine-grained personal access token (only the repositories and permissions Clara needs)" bind:value={githubToken} />
        <button class="primary" disabled={!githubToken.trim()}>{info.github.connected ? 'Replace token' : 'Connect'}</button>
        {#if info.github.connected}<button type="button" class="danger" onclick={() => disconnect('github')}>Disconnect</button>{/if}
      </form>
      {#if info.github.connected}
        <form class="row" onsubmit={addRepo}>
          <input class="grow" aria-label="Repository" placeholder="owner/name" bind:value={repo} required />
          <input aria-label="Branch" placeholder="branch (optional)" bind:value={branch} />
          <button>Add repository</button>
        </form>
        {#each info.github.repos as item (item.id)}
          <div class="row"><span class="grow">{item.locator}{item.branch ? ` (branch ${item.branch})` : ''}</span><button class="small danger" onclick={() => removeResource(item.id)}>Remove</button></div>
        {:else}<div class="muted small">No repository yet: Clara can reach none.</div>{/each}
      {/if}
    </section>
    <section class="panel stack">
      <h2><Icon name="cloud" size={18} /> Google Drive {#if info.google.connected}<span class="tag ok">{info.google.account}</span>{/if}
        {#if info.google.status === 'needs_reconnect'}<span class="tag danger">reconnect</span>{/if}</h2>
      {#if !info.google.configured}
        <p class="muted small">Set CLARA_GOOGLE_CLIENT_ID and the secret google_client_secret (a Google Cloud OAuth client, "web
          application", with the redirect &lt;your Clara address&gt;/v1/connections/google/callback). Clara asks only for
          <code>drive.file</code>: the files she creates and those you open to her.</p>
      {:else}
        <div class="row">
          <button class="primary" onclick={startGoogle}>{info.google.connected ? 'Connect again' : 'Connect Google Drive'}</button>
          {#if info.google.connected}
            <button onclick={picker} disabled={!info.google.picker} title={info.google.picker ? '' : 'Needs CLARA_GOOGLE_PROJECT_NUMBER and the secret google_picker_api_key'}>Open files to Clara…</button>
            <button class="danger" onclick={() => disconnect('google')}>Disconnect</button>
          {/if}
        </div>
      {/if}
      {#each info.google.files as item (item.id)}
        <div class="row"><span class="grow"><Icon name={item.kind === 'drive_folder' ? 'folder' : 'file'} size={16} /> {item.label}</span>
          {#if item.mime !== 'clara'}<button class="small danger" onclick={() => removeResource(item.id)}>Remove</button>{/if}</div>
      {/each}
    </section>
  {/if}
</div>
