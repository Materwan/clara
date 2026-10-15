<script lang="ts">
  import { api, ApiError } from '../lib/api';
  import { markdown } from '../lib/markdown';
  import { toast } from '../lib/state.svelte';

  let info = $state<any>(null);
  let missing = $state('');
  let notes = $state<string[]>([]);
  let query = $state('');
  let results = $state('');
  let note = $state<any>(null);

  async function load(): Promise<void> {
    try {
      info = await api.get('/v1/vault');
      notes = (await api.get('/v1/vault/notes')).notes;
    } catch (error) {
      if (error instanceof ApiError && error.status === 404) missing = error.message;
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

<div class="page stack">
  <h1>Vault</h1>
  {#if missing}
    <p class="muted">{missing} Set CLARA_VAULT_PATH to your Obsidian vault (a git clone) to give Clara a second brain.</p>
  {:else if info}
    <div class="panel small">
      <div><strong>{info.notes}</strong> notes in {info.path}</div>
      <div class="muted">git: {info.git}</div>
      {#if info.problem}<div class="bad">{info.problem}</div>{/if}
      <div class="row"><button class="small" onclick={() => act('sync')}>Sync now</button><button class="small" onclick={() => act('reindex')}>Reindex</button></div>
    </div>
    <form class="row" onsubmit={search}><input class="grow" placeholder="Search the notes" bind:value={query} required /><button>Search</button></form>
    {#if results}<pre class="results">{results}</pre>{/if}
    <div class="split">
      <div class="panel list">
        {#each notes as path}<div><button class="link" onclick={() => open(path)}>{path}</button></div>{/each}
      </div>
      <div class="panel">
        {#if note}<h2>{note.path}</h2><div class="markdown" use:markdown={note.content}></div>{:else}<div class="empty">Pick a note.</div>{/if}
      </div>
    </div>
  {/if}
</div>

<style>
  .split {
    display: grid;
    grid-template-columns: 300px 1fr;
    gap: 0.8rem;
  }
  .list {
    max-height: 70vh;
    overflow: auto;
  }
  .results {
    white-space: pre-wrap;
  }
  .link {
    border: none;
    background: none;
    padding: 0;
    color: var(--accent);
    text-align: left;
  }
  @media (max-width: 760px) {
    .split {
      grid-template-columns: 1fr;
    }
  }
</style>
