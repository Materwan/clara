<script lang="ts">
  import { tick } from 'svelte';
  import Checkbox from '../components/Checkbox.svelte';
  import Choice from '../components/Choice.svelte';
  import Icon from '../components/Icon.svelte';
  import { api, when } from '../lib/api';
  import { confirmDialog } from '../lib/dialog.svelte';
  import {
    GITHUB_KINDS, MAX_FIELDS, WEBHOOK_NOTES, addressUrl, ago, checkFields, checkName, checkRepo, curlExample, describeFields, doorLine, fireBody,
    githubBody, proofText, showData, slugOf, sourceBody, webhookSteps,
    type Door, type FieldRow,
  } from '../lib/sources';
  import { live, session, toast } from '../lib/state.svelte';
  import { fieldLabel } from '../lib/trigger';

  // The event sources: programs of yours (a script, a home hub) and the webhooks of GitHub repositories that tell Clara something happened.
  // A line for the events door (what lets GitHub reach Clara), a card per source, a form to make one, and the address and secret of a new
  // one in a panel that stays until you say you have copied them. The secret is shown once: it is kept here only while that panel is
  // open, and goes with the panel or with the page.
  type Source = {
    id: string; name: string; kind: string; repo: string; fields: Record<string, string>; proof: string; trusted: boolean; can_trust: boolean;
    address: string; public_url: string | null; events: number; waiting: number; failures: number; last_failure_at: string | null;
    last_event_at: string | null;
  };
  type Issued = {
    id: string; title: string; name: string; url: string; secret: string; fields: Record<string, string>;
    github: boolean; repo: string; publicUrl: string | null; rotated: boolean; // a GitHub source: its steps, and the address GitHub calls (null: the door is not public)
  };
  type Kind = 'program' | 'github';
  type Test = { source: Source; values: Record<string, string>; title: string; result: string; error: string; sending: boolean };
  type Looking = { source: Source; events: any[] | null; error: string };

  const TRUST = 'Trusted: its events do not taint a run. Tick only for what you write yourself. Needs a token that may answer everything.';
  const KINDS = [
    { value: 'program' as Kind, label: 'A program of mine', icon: 'terminal', hint: 'A script, a home hub: anything that can send an HTTP call with a secret. You say what its events carry.' },
    { value: 'github' as Kind, label: 'A GitHub repository (webhook)', icon: 'branch', hint: 'GitHub tells Clara of its pushes and pull requests as they happen, instead of the poll. Needs the events door to be public.' },
  ];
  const SENT = 'Sent as a real event: automations that wait for this source may start a run.';

  let sources = $state<Source[] | null>(null);
  let loadError = $state('');
  let open = $state(false);
  let kind = $state<Kind>('program');
  let name = $state('');
  let repo = $state('');
  let offered = $state<string[]>([]); // the repositories of the Connections page that have no webhook yet: what the field suggests
  let door = $state<Door | null>(null); // `events_door` of the status; null when it could not be read (the line is then left out)
  let rows = $state<FieldRow[]>([]);
  let checked = $state(false); // a save was tried: every mistake is shown where it is
  let saving = $state(false);
  let serverError = $state('');
  let issued = $state<Issued[]>([]);
  let testing = $state<Test | null>(null);
  let looking = $state<Looking | null>(null);
  let box = $state<HTMLDialogElement>();
  let newButton = $state<HTMLButtonElement>();

  /** Only a token that may answer everything makes or changes a source (the others get a 403: they look, and may send a test event). */
  const canEdit = $derived(session.approvals === 'all');
  const taken = $derived((sources ?? []).map((source) => source.kind));
  const watched = $derived((sources ?? []).filter((source) => source.kind === 'github').map((source) => source.repo));
  const suggestions = $derived(offered.filter((item) => !watched.some((other) => other.toLowerCase() === item.toLowerCase())));
  const line = $derived(doorLine(door));
  const slug = $derived(slugOf(name));
  const problems = $derived.by(() => {
    const found: { id: string; message: string }[] = [];
    if (kind === 'github') {
      const bad = checkRepo(repo, watched);
      if (bad) found.push({ id: 'es-repo', message: bad });
      return found;
    }
    const bad = checkName(name, taken);
    if (bad) found.push({ id: 'es-name', message: bad });
    checkFields(rows).forEach((message, index) => {
      if (message) found.push({ id: `es-field-${index}`, message });
    });
    return found;
  });
  const errorOf = (id: string): string | undefined => (checked ? problems.find((item) => item.id === id)?.message : undefined);
  const invalid = (id: string): 'true' | undefined => (errorOf(id) ? 'true' : undefined);
  const urlOf = (source: { address: string }): string => addressUrl(location.origin, source.address);
  const plural = (count: number, one: string, many: string): string => `${count} ${count === 1 ? one : many}`;
  const newKey = (): string => `web-${[...crypto.getRandomValues(new Uint8Array(6))].map((byte) => byte.toString(16).padStart(2, '0')).join('')}`;

  async function load(): Promise<void> {
    try {
      sources = (await api.get('/v1/event-sources')).sources;
      loadError = '';
    } catch (error) {
      loadError = (error as Error).message;
    }
  }

  /** The door's line: read once when the tab opens (a status is heavier than a list, and the door changes only with a restart). */
  async function loadDoor(): Promise<void> {
    try {
      door = (await api.get('/v1/admin/status')).events_door ?? null;
    } catch {
      door = null; // a token that may not read it, or a server without it: no line, no error
    }
  }

  /** The repositories of the Connections page that have no webhook, for the field to suggest; none when Connections is not available. */
  async function loadOffered(): Promise<void> {
    try {
      const github = (await api.get('/v1/connections')).github;
      offered = [...new Set<string>((github?.repos ?? []).filter((item: any) => !item.webhook).map((item: any) => String(item.locator)))]; // a repository can be there once per branch
    } catch {
      offered = [];
    }
  }

  $effect(() => {
    void live.refresh;
    load();
  });

  $effect(() => {
    loadDoor();
  });

  // The secrets go with the page: nothing keeps one once it is gone.
  $effect(() => () => {
    issued = [];
  });

  async function copy(text: string, what: string): Promise<void> {
    try {
      await navigator.clipboard.writeText(text);
      toast(`${what} copied.`, 'ok');
    } catch {
      toast('Could not copy: select the text and copy it by hand.', 'error'); // a page not served over HTTPS has no clipboard
    }
  }

  // ------------------------------------------------------------------ a new source
  async function openForm(): Promise<void> {
    open = true;
    loadOffered();
    await tick();
    document.getElementById('es-name')?.focus();
  }

  async function closeForm(refocus = true): Promise<void> {
    open = false;
    kind = 'program';
    name = repo = '';
    rows = [];
    checked = false;
    serverError = '';
    if (!refocus) return;
    await tick();
    newButton?.focus();
  }

  /** The address and the secret of a source just made or given a new secret, in a panel of their own (a new one for the same source replaces it). */
  async function show(title: string, made: { source: Source; address: string; public_url: string | null; secret: string }, rotated = false): Promise<void> {
    const { source } = made;
    const found: Issued = {
      id: source.id, title, name: source.name, url: urlOf(made), secret: made.secret, fields: source.fields,
      github: source.kind === 'github', repo: source.repo, publicUrl: made.public_url, rotated,
    };
    issued = [found, ...issued.filter((item) => item.id !== found.id)];
    await tick();
    document.getElementById(`es-issued-${found.id}`)?.focus();
  }

  async function make(): Promise<void> {
    serverError = '';
    if (problems.length) {
      checked = true;
      await tick();
      document.getElementById(problems[0].id)?.focus();
      return;
    }
    saving = true;
    try {
      const made = await api.post('/v1/event-sources', kind === 'github' ? githubBody(repo) : sourceBody(name, rows));
      await closeForm(false);
      await show('New source made', made);
      load();
    } catch (error) {
      serverError = (error as Error).message;
    } finally {
      saving = false;
    }
  }

  // ------------------------------------------------------------------ a card
  async function rotate(source: Source): Promise<void> {
    const sender = source.kind === 'github' ? 'GitHub' : 'whatever sends with it';
    const message = `Make a new secret for “${source.name}”? The old one stops working at once: ${sender} is refused until it is given the new one.`;
    if (!(await confirmDialog({ title: 'New secret', message, confirm: 'Make a new secret', danger: true }))) return;
    try {
      await show('New secret made', await api.post(`/v1/event-sources/${source.id}/rotate`), true);
    } catch (error) {
      toast((error as Error).message, 'error');
    }
    load();
  }

  async function revoke(source: Source): Promise<void> {
    const message = source.kind === 'github'
      ? `Revoke “${source.name}”? Its address and its secret stop working at once: delete the webhook in GitHub too, or its deliveries fail. Polling of this repository resumes (if it is one of those of the Connections page).`
      : `Revoke “${source.name}”? Its address and its secret stop working at once. Automations that wait for it stay, and never start until a source with the same name exists again.`;
    if (!(await confirmDialog({ title: 'Revoke source', message, confirm: 'Revoke', danger: true }))) return;
    try {
      await api.del(`/v1/event-sources/${source.id}`);
      issued = issued.filter((item) => item.id !== source.id);
    } catch (error) {
      toast((error as Error).message, 'error');
    }
    load();
  }

  // The box is not ticked by the click: the server says whether it is, and the box follows the list once it has answered.
  async function trust(source: Source, event: Event): Promise<void> {
    event.preventDefault();
    const want = !source.trusted;
    if (want) {
      const message = `${TRUST} Trust “${source.name}” only if you write what it sends.`;
      if (!(await confirmDialog({ title: `Trust “${source.name}”?`, message, confirm: 'Trust it', danger: true }))) return;
    }
    try {
      await api.patch(`/v1/event-sources/${source.id}`, { trusted: want });
    } catch (error) {
      toast((error as Error).message, 'error');
    }
    load();
  }

  // ------------------------------------------------------------------ the two dialogs
  async function openTest(source: Source): Promise<void> {
    const values = Object.fromEntries(Object.entries(source.fields).map(([field, type]) => [field, type === 'bool' ? 'no' : '']));
    looking = null;
    testing = { source, values, title: '', result: '', error: '', sending: false };
    await tick();
    if (!box?.open) box?.showModal();
  }

  async function send(): Promise<void> {
    const test = testing;
    if (!test || test.sending) return;
    test.sending = true;
    test.result = test.error = '';
    try {
      const answer = await api.post(`/v1/event-sources/${test.source.id}/fire`, fireBody(test.source.fields, test.values, test.title, newKey()));
      test.result = answer.duplicate ? 'Duplicate: an event with this key was stored before, so nothing was started.' : `${SENT} Event ${answer.id} stored.`;
      load();
    } catch (error) {
      test.error = (error as Error).message;
    } finally {
      test.sending = false;
    }
  }

  async function openEvents(source: Source): Promise<void> {
    testing = null;
    looking = { source, events: null, error: '' };
    const view = looking; // read back: the state's own object, so that what is set on it shows
    await tick();
    if (!box?.open) box?.showModal();
    try {
      view.events = (await api.get(`/v1/event-sources/${source.id}/events`, { limit: 20 })).events;
    } catch (error) {
      view.error = (error as Error).message;
    }
  }

  const closeBox = (): void => box?.close();
</script>

<div class="row start">
  <p class="intro grow">Things that tell Clara something happened: your scripts, your home hub, a GitHub repository. Each one has an address and a secret; an
    automation can wait for its events.</p>
  <button class="primary" disabled={!canEdit} aria-expanded={open} aria-controls="es-form" bind:this={newButton}
    onclick={() => (open ? closeForm() : openForm())}><Icon name="plus" size={18} />New source</button>
</div>
{#if line}
  <div class="stack tight" id="es-door">
    {#if line.tone === 'warn'}
      <p class="call-warning" role="note"><Icon name="warn" size={14} /><span>{line.text}</span></p>
    {:else}
      <p class={`small ${line.tone}`}>{#if line.tone === 'ok'}<Icon name="check" size={14} /> {/if}{line.text}</p>
    {/if}
    {#if line.howTo}
      <details class="setup">
        <summary>How to turn it on</summary>
        <ol class="steps small">
          <li>In your Tailscale access controls (<code>login.tailscale.com/admin/acls</code>), add <code>funnel</code> to the <code>nodeAttrs</code> of this machine, and enable HTTPS for the tailnet.</li>
          <li>In Clara’s settings file (<code>/etc/clara/clara.env</code>) set <code>CLARA_EVENTS=on</code> and make sure <code>CLARA_TAILSCALE=serve</code>.</li>
          <li>Restart Clara (<code>sudo systemctl restart clara</code>). This line then says where the door is public.</li>
        </ol>
        <p class="muted small">This page cannot turn it on or off: it is a setting file. The details are in <code>docs/OPERATIONS.md</code>, “The events door”.</p>
      </details>
    {/if}
  </div>
{/if}
{#if !canEdit}
  <p class="muted small">This token may not make or change event sources: they let a program start Clara by itself. You can still look at them and send a test event.</p>
{/if}

{#snippet copyRow(label: string, text: string, noun: string, name: string, secret = false)}
  <div class="stack tight">
    {#if label}<span class="label">{label}</span>{/if}
    <div class="row"><code class="url" class:secret>{text}</code>
      <button type="button" class="small" aria-label={`Copy the ${noun} of ${name}`} onclick={() => copy(text, `The ${noun}`)}><Icon name="copy" size={14} />Copy</button></div>
  </div>
{/snippet}

{#each issued as item (item.id)}
  <section class="panel frame signal stack" id={`es-issued-${item.id}`} tabindex="-1" aria-labelledby={`es-issued-${item.id}-title`}>
    <h2 id={`es-issued-${item.id}-title`}>{item.title}: “{item.name}”</h2>
    {#if item.github}
      <p>Paste {item.rotated ? 'the secret' : 'both'} into GitHub. The secret is shown once and cannot be read again: copy it before you leave this tab. If it is lost, make a new secret.</p>
      {#if !item.rotated}
        {#if item.publicUrl}
          {@render copyRow('Payload URL', item.publicUrl, 'payload URL', item.name)}
        {:else}
          <div class="stack tight">
            <span class="label">Payload URL</span>
            <p class="call-warning" role="note"><Icon name="warn" size={14} /><span>The events door is not public: GitHub cannot reach this address yet. Turn the door on first (see “Events door” at the top of this tab).</span></p>
            <div class="row"><code class="url">{item.url}</code></div>
            <p class="muted small">Its local address, for reference.</p>
          </div>
        {/if}
      {/if}
      {@render copyRow('Secret', item.secret, 'secret', item.name, true)}
      <div class="stack tight">
        <span class="label">In GitHub</span>
        <ol class="steps small">
          {#each webhookSteps(item.repo, item.rotated) as step (step.label)}<li><b>{step.label}:</b> {step.text}</li>{/each}
        </ol>
        {#if !item.rotated}{#each WEBHOOK_NOTES as note (note)}<p class="muted small">{note}</p>{/each}{/if}
      </div>
    {:else}
      <p>Give both to the sender. The secret is shown once and cannot be read again: copy it before you leave this tab. If it is lost, make a new secret.</p>
      {@render copyRow('Address', item.url, 'address', item.name)}
      {@render copyRow('Secret', item.secret, 'secret', item.name, true)}
      <div class="stack tight">
        <span class="label">A call the sender can make <span class="muted">(put the secret where it says <code>{'<secret>'}</code>)</span></span>
        <pre class="pre">{curlExample(item.url, item.fields)}</pre>
        <div><button type="button" class="small" aria-label={`Copy the example call for ${item.name}`} onclick={() => copy(curlExample(item.url, item.fields), 'The example')}><Icon name="copy" size={14} />Copy the example</button></div>
        <p class="muted small">Two calls with the same body are one event: add a <code>"key"</code> when the same data can happen twice.</p>
      </div>
    {/if}
    <div><button type="button" class="primary" onclick={() => (issued = issued.filter((other) => other.id !== item.id))}>I have copied {item.github && item.rotated ? 'it' : 'them'}</button></div>
  </section>
{/each}

{#if open}
  <form id="es-form" class="panel stack" aria-label="New source" novalidate onsubmit={(event) => { event.preventDefault(); make(); }}>
    <h2>New source</h2>
    <Choice cards legend="What sends the events" options={KINDS} bind:value={kind} onchange={() => { checked = false; serverError = ''; }} />
    {#if kind === 'github'}
      <label class="field">Repository (owner/name)
        <span class="hint">GitHub sends this repository’s pushes and pull requests to Clara. Its poll is off while the webhook exists. You add the webhook in GitHub yourself: Clara needs no right there.</span>
        <input id="es-repo" bind:value={repo} list="es-repos" maxlength="201" placeholder="owner/name" autocomplete="off" spellcheck="false"
          aria-invalid={invalid('es-repo')} aria-describedby={errorOf('es-repo') ? 'es-repo-error' : undefined} />
        <datalist id="es-repos">{#each suggestions as item (item)}<option value={item}></option>{/each}</datalist>
        {#if errorOf('es-repo')}<span class="field-error" id="es-repo-error" role="alert">{errorOf('es-repo')}</span>{/if}
      </label>
      <p class="muted small">A GitHub source is never trusted: what a pull request or a push says is written by others, so its events taint the run.</p>
    {:else}
      <label class="field">Name
        <span class="hint">What sends the events: “Front door”, “Home hub”. Its events have the kind <code>custom.{slug || 'name'}</code>, which cannot be changed later.</span>
        <input id="es-name" bind:value={name} maxlength="60" autocomplete="off" spellcheck="false" aria-invalid={invalid('es-name')}
          aria-describedby={errorOf('es-name') ? 'es-name-error' : undefined} />
        {#if errorOf('es-name')}<span class="field-error" id="es-name-error" role="alert">{errorOf('es-name')}</span>{/if}
      </label>
      <div class="stack" role="group" aria-labelledby="es-fields-title">
        <span class="label" id="es-fields-title">What an event can carry</span>
        <p class="muted small">Optional. An automation can filter on a field: a text one with a regular expression, a yes/no one with yes or no. Every event has a title as well.</p>
        {#each rows as row, index}
          <div class="row">
            <input id={`es-field-${index}`} class="grow field-name" aria-label={`Name of field ${index + 1}`} placeholder="state" bind:value={row.name} autocomplete="off"
              spellcheck="false" aria-invalid={invalid(`es-field-${index}`)} aria-describedby={errorOf(`es-field-${index}`) ? `es-field-${index}-error` : undefined} />
            <select aria-label={`Type of field ${index + 1}`} bind:value={row.type}>
              <option value="text">text</option>
              <option value="bool">yes/no</option>
            </select>
            <button type="button" class="icon-btn" aria-label={`Remove field ${index + 1}`} onclick={() => rows.splice(index, 1)}><Icon name="close" size={16} /></button>
          </div>
          {#if errorOf(`es-field-${index}`)}<span class="field-error" id={`es-field-${index}-error`} role="alert">{errorOf(`es-field-${index}`)}</span>{/if}
        {/each}
        <div>
          {#if rows.length < MAX_FIELDS}
            <button type="button" class="small" onclick={() => rows.push({ name: '', type: 'text' })}><Icon name="plus" size={14} />Field</button>
          {:else}
            <span class="muted small">{MAX_FIELDS} fields at most.</span>
          {/if}
        </div>
      </div>
      <p class="muted small">A new source is not trusted: its events taint the run, as a mail or a pull request does. You can trust it on its card.</p>
    {/if}
    {#if serverError}<p class="field-error" role="alert">{serverError}</p>{/if}
    <div class="row">
      <button type="submit" class="primary" disabled={saving}>Make the source</button>
      <button type="button" onclick={() => closeForm()}>Cancel</button>
    </div>
  </form>
{/if}

{#if loadError}<p class="field-error" role="alert">{loadError}</p>{/if}
{#if sources === null}
  {#if !loadError}<p class="muted" role="status">Loading…</p>{/if}
{:else if !sources.length && !open}
  <div class="panel stack">
    <p>Nothing tells Clara anything yet. Make a source, give its address and secret to a script or a hub, and an automation can start from what it sends.</p>
    <div><button class="primary" disabled={!canEdit} onclick={openForm}><Icon name="plus" size={18} />New source</button></div>
  </div>
{/if}

{#each sources ?? [] as source (source.id)}
  {@const github = source.kind === 'github'}
  <article class="panel source stack" aria-labelledby={`es-${source.id}`}>
    <div class="row">
      <h3 class="grow" id={`es-${source.id}`}>{source.name}</h3>
      {#if github}<span class="tag">GitHub</span><code class="tag">{source.repo}</code>{:else}<code class="tag">{source.kind}</code>{/if}
      {#if source.trusted}<span class="tag warn">trusted</span>{/if}
    </div>
    {#if github}
      {#if source.public_url}
        {@render copyRow('', source.public_url, 'payload URL', source.name)}
      {:else}
        <p class="call-warning" role="note"><Icon name="warn" size={14} /><span>The events door is not public: GitHub cannot reach this source yet. Its local address is {urlOf(source)}.</span></p>
      {/if}
    {:else}
      {@render copyRow('', urlOf(source), 'address', source.name)}
    {/if}
    <p class="muted small">{proofText(source.proof)}</p>
    {#if github}
      <p class="small">Events: {#each GITHUB_KINDS as item, index (item)}{index ? ' ' : ''}<code class="tag">{item}</code>{/each}</p>
      <p class="muted small">Polling of this repository is off while this webhook exists.</p>
    {:else}
      <p class="small">{#if Object.keys(source.fields).length}Fields: {describeFields(source.fields).join(', ')}.{:else}No field: an event is its title alone.{/if}</p>
    {/if}
    <p class="small">
      <span title={source.last_event_at ? when(source.last_event_at) : undefined}>{source.last_event_at ? `Last event ${ago(source.last_event_at)}` : 'No event yet'}</span>
      <span class="muted"> · {plural(source.events, 'event', 'events')} kept · {source.waiting} waiting for a run</span>
    </p>
    {#if source.failures}
      <p class="call-warning" role="note"><Icon name="warn" size={14} /><span>{github ? plural(source.failures, 'delivery with a wrong signature', 'deliveries with a wrong signature') : plural(source.failures, 'wrong secret', 'wrong secrets')} since it was made{source.last_failure_at ? `, the last ${ago(source.last_failure_at)}` : ''}.
        Ten in a minute block it for a minute.</span></p>
    {/if}
    {#if github}
      <p class="muted small">A GitHub source takes GitHub’s own deliveries: use <em>Redeliver</em> in the webhook’s settings to try it.</p>
    {:else}
      <label class="call-warning">
        <Checkbox checked={source.trusted} disabled={!canEdit || !source.can_trust} onclick={(event: Event) => trust(source, event)} />
        <span><b>Trusted</b>: its events do not taint a run. Tick only for what you write yourself. Needs a token that may answer everything.</span>
      </label>
      {#if !source.can_trust}<p class="muted small">It proves itself with something other than a bearer secret: it can never be trusted.</p>{/if}
    {/if}
    <div class="row">
      {#if !github}<button type="button" class="small" onclick={() => openTest(source)}>Send a test event</button>{/if}
      <button type="button" class="small" onclick={() => openEvents(source)}>Latest events</button>
      <button type="button" class="small" disabled={!canEdit} onclick={() => rotate(source)}>New secret</button>
      <button type="button" class="small danger" disabled={!canEdit} onclick={() => revoke(source)}>Revoke</button>
    </div>
  </article>
{/each}

<dialog bind:this={box} class="dialog frame es-dialog" aria-labelledby="es-dialog-title" onclose={() => { testing = null; looking = null; }}>
  {#if testing}
    <form class="body" onsubmit={(event) => { event.preventDefault(); send(); }}>
      <h2 id="es-dialog-title">Send a test event to “{testing.source.name}”</h2>
      <p class="muted small">This is not a dry run: the event is stored like any other, and automations that wait for this source may start a run. To try an automation without running it, use its Test button.</p>
      {#each Object.entries(testing.source.fields) as [field, type] (field)}
        <label class="field">{fieldLabel(field)}
          {#if type === 'bool'}
            <select bind:value={testing.values[field]}><option value="no">No</option><option value="yes">Yes</option></select>
          {:else}
            <input bind:value={testing.values[field]} autocomplete="off" />
          {/if}
        </label>
      {/each}
      <label class="field">Title
        <span class="hint">Optional. Without one, the event is called by the name of the source and its first text.</span>
        <input bind:value={testing.title} maxlength="300" autocomplete="off" />
      </label>
      {#if testing.result}<p class="row" role="status"><span class="tag ok"><Icon name="check" size={14} />Sent</span> <span class="small">{testing.result}</span></p>{/if}
      {#if testing.error}<p class="row" role="alert"><span class="tag warn"><Icon name="warn" size={14} />Not sent</span> <span class="small">{testing.error}</span></p>{/if}
      <div class="actions">
        <button type="button" onclick={closeBox}>Close</button>
        <button type="submit" class="primary" disabled={testing.sending}>Send the event</button>
      </div>
    </form>
  {:else if looking}
    <div class="body">
      <h2 id="es-dialog-title">Latest events of “{looking.source.name}”</h2>
      <p class="muted small">The text of an event was written by whoever sent it, not by you or by Clara: read it as untrusted.</p>
      {#if looking.error}
        <p class="row" role="alert"><span class="tag warn"><Icon name="warn" size={14} />Cannot read</span> <span class="small">{looking.error}</span></p>
      {:else if !looking.events}
        <p class="muted" role="status">Loading…</p>
      {:else if !looking.events.length}
        <p class="muted">{looking.source.kind === 'github' ? 'No event yet. Push to the repository, open a pull request, or use Redeliver in the webhook’s settings.' : 'No event yet. “Send a test event” makes one.'}</p>
      {:else}
        <ul class="events" aria-label="Latest events">
          {#each looking.events as event (event.id)}
            <li>
              <strong>{event.title}</strong>
              <span class="muted small">{when(event.occurred_at)}{event.actor ? ` · ${event.actor}` : ''}</span>
              {#each showData(event.data) as line}<span class="small">{line}</span>{/each}
            </li>
          {/each}
        </ul>
      {/if}
      <div class="actions"><button type="button" onclick={closeBox}>Close</button></div>
    </div>
  {/if}
</dialog>

<style>
  .label { font-size: var(--fs-sm); font-weight: 600; color: var(--text-2); }
  .tight > * + * { margin-top: var(--sp-6); }
  .source h3 { margin: 0; overflow-wrap: anywhere; }
  .source .tag { white-space: normal; overflow-wrap: anywhere; }
  .url { flex: 1 1 14rem; min-width: 0; padding: var(--sp-6) var(--sp-8); overflow-wrap: anywhere; word-break: break-all; background: var(--code-bg); border: 1px solid var(--code-line); border-radius: var(--r-sm); }
  .secret { user-select: all; }
  .steps { margin: 0; padding-left: var(--sp-20); }
  .steps li + li { margin-top: var(--sp-4); }
  .field-name { flex: 1 1 8rem; min-width: 0; }
  dialog.es-dialog { width: min(100% - var(--sp-32), calc(var(--sp-64) * 10)); max-height: 90vh; overflow: auto; }
  .body { display: flex; flex-direction: column; gap: var(--sp-14); padding: var(--sp-20); }
  .events { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: var(--sp-10); }
  .events li { display: flex; flex-direction: column; padding-bottom: var(--sp-10); border-bottom: 1px solid var(--line); overflow-wrap: anywhere; }
  .events li:last-child { border-bottom: none; padding-bottom: 0; }
</style>
