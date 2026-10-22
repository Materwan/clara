<script lang="ts">
  import Hint from './Hint.svelte';
  import Icon from './Icon.svelte';
  import { api, ApiError } from '../lib/api';
  import { markdown } from '../lib/markdown';
  import { toast } from '../lib/state.svelte';

  let info = $state<any>(null);
  let missing = $state(false); // the server has no vault
  let notes = $state<string[]>([]);
  let query = $state('');
  let results = $state('');
  let note = $state<any>(null);

  async function load(): Promise<void> {
    try {
      info = await api.get('/v1/vault');
      notes = (await api.get('/v1/vault/notes')).notes;
    } catch (error) {
      if (error instanceof ApiError && error.status === 404) missing = true;
      else toast((error as Error).message, 'error');
    }
  }

  $effect(() => {
    load();
  });

  async function search(event: SubmitEvent): Promise<void> {
    event.preventDefault();
    results = (await api.get('/v1/vault/search', { q: query })).results;
  }

  async function open(path: string): Promise<void> {
    note = await api.get('/v1/vault/note', { path });
  }

  async function act(what: 'sync' | 'reindex'): Promise<void> {
    const done = await api.post(`/v1/vault/${what}`);
    toast(done.message ?? `Indexed ${done.indexed} notes.`, 'ok');
    load();
  }
</script>

<div class="stack">
  {#if missing}
    <p class="intro">No vault is connected. To give Clara a second brain, point <Hint text="CLARA_VAULT_PATH" /> at your Obsidian vault (a git clone) on the server.</p>
  {:else if info}
    <div class="panel small">
      <div><strong>{info.notes}</strong> notes in {info.path}</div>
      <div class="muted">git: {info.git}</div>
      {#if info.problem}<div class="danger">{info.problem}</div>{/if}
      <div class="row"><button class="small" onclick={() => act('sync')}><Icon name="refresh" size={16} />Sync now</button><button class="small" onclick={() => act('reindex')}>Reindex</button></div>
    </div>
    <form class="row" onsubmit={search}><input class="grow" type="search" aria-label="Search the notes" placeholder="Search the notes" bind:value={query} required /><button><Icon name="search" size={18} />Search</button></form>
    {#if results}<pre class="results">{results}</pre>{/if}
    <div class="split">
      <div class="panel notes">
        {#each notes as path}<div><button class="link" aria-current={note?.path === path ? 'true' : undefined} onclick={() => open(path)}>{path}</button></div>{/each}
      </div>
      <div class="panel">
        {#if note}<h2>{note.path}</h2><div class="markdown" use:markdown={note.content}></div>{:else}<div class="empty">Pick a note.</div>{/if}
      </div>
    </div>
  {/if}
</div>

<style>
  .results {
    white-space: pre-wrap;
  }
  .notes .link {
    color: var(--text);
    font-size: var(--fs-sm);
  }
  .notes .link[aria-current='true'] {
    color: var(--accent-text);
    font-weight: 650;
  }
</style>
