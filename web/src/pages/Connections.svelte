<script lang="ts">
  import Checkbox from '../components/Checkbox.svelte';
  import Icon from '../components/Icon.svelte';
  import { api, when } from '../lib/api';
  import { confirmDialog } from '../lib/dialog.svelte';
  import { route, toast } from '../lib/state.svelte';

  let info = $state<any>(null);
  let githubToken = $state('');
  let repo = $state('');
  let branch = $state('');
  let senders = $state('');
  type Mailbox = 'gmail';
  const WATCHED: Record<string, string> = { 'github.pull_request': 'pull requests', 'github.push': 'pushes' };

  async function load(): Promise<void> {
    info = await api.get('/v1/connections');
    senders = (info.mail.senders ?? []).join(', ');
  }

  $effect(() => {
    load();
    finishConsent();
  });

  // The provider sent the browser back here with a code. The server proves the account is reachable and says which it
  // is; Clara attaches it only if you agree.
  async function finishConsent(): Promise<void> {
    const state = route.query.get('google_state');
    if (!state) return;
    const kind = route.query.get('flow') === 'gmail' ? 'gmail' : 'drive';
    const code = route.query.get('google_code');
    const error = route.query.get('google_error');
    history.replaceState(null, '', '#/connections');
    if (error || !code) {
      toast(`Google said: ${error || 'no code'}`, 'error');
      return;
    }
    try {
      const found = await api.post(kind === 'drive' ? '/v1/connections/google/finish' : `/v1/connections/mail/${kind}/finish`, { state, code });
      const what = kind === 'drive' ? 'Google account' : 'Gmail mailbox';
      const attach = await confirmDialog({
        title: `Attach a ${what}`, message: `Attach the ${what} ${found.account || '(address not given)'} to Clara?`
          + (kind === 'drive' ? '' : ' She will be able to read it and save drafts; she sends only what you approve.'),
        confirm: 'Attach it',
      });
      if (!attach) return;
      if (kind === 'drive') await confirmGoogle(state, found.account);
      else await confirmMailbox(kind, state, found.account);
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

  async function startMailbox(kind: Mailbox): Promise<void> {
    try {
      location.href = (await api.post(`/v1/connections/mail/${kind}/start`)).url;
    } catch (error) {
      toast((error as Error).message, 'error');
    }
  }

  async function confirmMailbox(kind: Mailbox, state: string, account: string): Promise<void> {
    await api.post(`/v1/connections/mail/${kind}/confirm`, { state });
    toast(`Gmail connected (${account}).`, 'ok');
    load();
  }

  async function disconnectMailbox(kind: Mailbox): Promise<void> {
    if (!(await confirmDialog({ title: 'Disconnect Gmail', message: 'Disconnect Gmail? The token is revoked at Google.', confirm: 'Disconnect', danger: true }))) return;
    await api.del(`/v1/connections/mail/${kind}`);
    load();
  }

  async function setNotify(on: boolean): Promise<void> {
    try {
      await api.put('/v1/admin/settings/mail_notify', { value: on });
    } catch (error) {
      toast((error as Error).message, 'error');
    }
    load();
  }

  async function saveSenders(event: SubmitEvent): Promise<void> {
    event.preventDefault();
    try {
      await api.put('/v1/admin/settings/mail_notify_senders', { value: senders.split(',').map((word) => word.trim()).filter(Boolean) });
      toast('Saved.', 'ok');
    } catch (error) {
      toast((error as Error).message, 'error');
    }
    load();
  }

  const DISCONNECT = {
    github: { title: 'Disconnect GitHub', message: 'Disconnect GitHub? The token is forgotten.' },
    google: { title: 'Disconnect Google Drive', message: 'Disconnect Google Drive? The token is revoked at Google.' },
  };

  async function disconnect(kind: 'github' | 'google'): Promise<void> {
    if (!(await confirmDialog({ ...DISCONNECT[kind], confirm: 'Disconnect', danger: true }))) return;
    await api.del(`/v1/connections/${kind}`);
    load();
  }

  function picker(): void {
    window.open('/picker.html', 'clara-picker', 'width=900,height=650');
    addEventListener('focus', load, { once: true });
  }
</script>

<div class="page stack">
  <p class="intro">Clara reaches only what is listed here. Every change she makes goes through your rules; by default
    each GitHub and Drive change asks you first, and an email is sent only after you have read it in an approval request.</p>
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
          <div class="row"><span class="grow">{item.locator}{item.branch ? ` (branch ${item.branch})` : ''}
            {#each Object.entries<string>(item.watching ?? {}) as [kind, since] (kind)}<span class="muted small">· {WATCHED[kind] ?? kind} since {when(since)} </span>{/each}</span><button class="small danger" onclick={() => removeResource(item.id)}>Remove</button></div>
        {:else}<div class="muted small">No repository yet: Clara can reach none.</div>{/each}
      {/if}
    </section>
    <section class="panel stack">
      <h2><Icon name="cloud" size={18} /> Google Drive {#if info.google.connected}<span class="tag ok">{info.google.account}</span>{/if}
        {#if info.google.status === 'needs_reconnect'}<span class="tag danger">reconnect</span>{/if}</h2>
      {#if !info.google.configured}
        <p class="muted small">Google Drive is not set up on this server. Clara asks only for <code>drive.file</code>: the files she creates and those you open to her.</p>
        <details class="setup"><summary>How to set it up</summary>
          <p class="muted small">Create a Google Cloud OAuth client of type “web application”, with the redirect
            <code>&lt;your Clara address&gt;/v1/connections/google/callback</code>, then set <code>CLARA_GOOGLE_CLIENT_ID</code>
            and the secret <code>google_client_secret</code> (Admin &gt; Secrets), and restart Clara.</p>
        </details>
      {:else}
        <div class="row">
          <button class="primary" onclick={startGoogle}>{info.google.connected ? 'Connect again' : 'Connect Google Drive'}</button>
          {#if info.google.connected}
            <button onclick={picker} disabled={!info.google.picker} title={info.google.picker ? '' : 'Needs CLARA_GOOGLE_PROJECT_NUMBER and the secret google_picker_api_key on the server'}>Open files to Clara…</button>
            <button class="danger" onclick={() => disconnect('google')}>Disconnect</button>
          {/if}
        </div>
      {/if}
      {#each info.google.files as item (item.id)}
        <div class="row"><span class="grow"><Icon name={item.kind === 'drive_folder' ? 'folder' : 'file'} size={16} /> {item.label}</span>
          {#if item.mime !== 'clara'}<button class="small danger" onclick={() => removeResource(item.id)}>Remove</button>{/if}</div>
      {/each}
    </section>
    <section class="panel stack">
      <h2><Icon name="mail" size={18} /> Gmail {#if info.gmail.connected}<span class="tag ok">{info.gmail.account}</span>{/if}
        {#if info.gmail.status === 'needs_reconnect'}<span class="tag danger">reconnect</span>{/if}</h2>
      {#if !info.gmail.configured}
        <p class="muted small">Gmail is not set up on this server. Clara cannot archive, delete a message for good or change any setting of your mailbox.</p>
        <details class="setup"><summary>How to set it up</summary>
          <p class="muted small">Gmail uses the Google OAuth client of Drive: set <code>CLARA_GOOGLE_CLIENT_ID</code> and the secret
            <code>google_client_secret</code>, turn on the <b>Gmail API</b> in the same Google Cloud project, and add the scopes
            <code>gmail.readonly</code>, <code>gmail.compose</code> and <code>gmail.modify</code> to its consent screen (Data Access; if the picker does not list them,
            paste their full addresses in <i>Manually add scopes</i>).</p>
        </details>
      {:else}
        <div class="row">
          <button class="primary" onclick={() => startMailbox('gmail')}>{info.gmail.connected ? 'Connect again' : 'Connect Gmail'}</button>
          {#if info.gmail.connected}<button class="danger" onclick={() => disconnectMailbox('gmail')}>Disconnect</button>{/if}
        </div>
        {#if info.gmail.connected}
          <p class="muted small">Clara can search and read this mailbox, read attachments and keep them in her workspace, and save drafts. She sends
            nothing without your approval, which shows every address and the whole text.</p>
        {/if}
      {/if}
    </section>
    {#if info.gmail.connected}
      <section class="panel stack">
        <h2><Icon name="bell" size={18} /> New email</h2>
        <label class="row"><Checkbox checked={info.mail.notify} disabled={info.mail.poll_seconds === 0} onchange={(event) => setNotify(event.currentTarget.checked)} />
          Tell me about new email in the inbox</label>
        {#if info.mail.poll_seconds === 0}
          <p class="muted small">Switched off on the server (<code>CLARA_MAIL_POLL_SECONDS=0</code>).</p>
        {:else}
          <p class="muted small">Checked every {info.mail.poll_seconds} seconds. The notification names the sender and the subject, never the text; it goes to
            every client, Discord's notification channel included.</p>
        {/if}
        <form class="row" onsubmit={saveSenders}>
          <input class="grow" aria-label="Only for these senders" placeholder="Only from senders containing (comma separated; empty: everyone)" bind:value={senders} />
          <button>Save</button>
        </form>
      </section>
    {/if}
  {/if}
</div>
