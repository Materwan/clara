<script lang="ts">
  import { api, download, fileToBase64, saveBlob, size, when } from '../lib/api';
  import { markdown } from '../lib/markdown';
  import { live, toast } from '../lib/state.svelte';

  let tab = $state<'files' | 'history' | 'runs'>('files');
  let info = $state<any>(null);
  let entries = $state<any[]>([]);
  let open = $state<any>(null);
  let commits = $state<any[]>([]);
  let runs = $state<any[]>([]);
  let shown = $state<string>('');
  let draft = $state<string | null>(null);

  async function load(): Promise<void> {
    info = await api.get('/v1/workspace');
    entries = (await api.get('/v1/workspace/tree', { depth: 6 })).entries;
    commits = (await api.get('/v1/workspace/log', { limit: 100 })).commits;
    runs = (await api.get('/v1/workspace/runs')).runs;
  }

  $effect(() => {
    void live.refresh;
    load();
  });

  async function view(path: string): Promise<void> {
    open = await api.get('/v1/workspace/file', { path });
    draft = null;
  }

  async function write(): Promise<void> {
    if (!open || draft === null) return;
    try {
      const done = await api.put('/v1/workspace/file', { path: open.path, content: draft });
      toast(`Saved (commit ${done.commit}).`, 'ok');
      await view(open.path);
      load();
    } catch (error) {
      toast((error as Error).message, 'error');
    }
  }

  async function save(path: string): Promise<void> {
    saveBlob(await download(`/v1/workspace/file?path=${encodeURIComponent(path)}&raw=true`), path.split('/').pop() ?? 'file');
  }

  async function remove(path: string): Promise<void> {
    if (!confirm(`Delete ${path}? It stays in the history and can be undone.`)) return;
    await api.del(`/v1/workspace/file?path=${encodeURIComponent(path)}`);
    open = null;
    load();
  }

  async function upload(event: Event): Promise<void> {
    for (const file of (event.target as HTMLInputElement).files ?? []) {
      try {
        await api.post('/v1/workspace/upload', { path: `uploads/${file.name}`, data: await fileToBase64(file), overwrite: true });
      } catch (error) {
        toast((error as Error).message, 'error');
      }
    }
    load();
  }

  async function undo(commit: string): Promise<void> {
    if (!confirm(`Undo ${commit}? A new commit reverts it.`)) return;
    try {
      toast((await api.post('/v1/workspace/undo', { commit })).message, 'ok');
      load();
    } catch (error) {
      toast((error as Error).message, 'error');
    }
  }

  async function showCommit(commit: string): Promise<void> {
    shown = (await api.get(`/v1/workspace/commits/${commit}`)).show;
  }
</script>

<div class="page stack">
  <h1>Workspace</h1>
  {#if info}
    <p class="small muted">{size(info.size)} of {size(info.max_size)} · scripts:
      {#if info.run_ready}<span class="ok">ready</span>{:else}<span class="warn">off ({info.run_problem})</span>{/if}</p>
  {/if}
  <div class="tabs">
    <button class:active={tab === 'files'} onclick={() => (tab = 'files')}>Files</button>
    <button class:active={tab === 'history'} onclick={() => (tab = 'history')}>History</button>
    <button class:active={tab === 'runs'} onclick={() => (tab = 'runs')}>Script runs</button>
  </div>
  {#if tab === 'files'}
    <div class="row"><label class="small">Upload into uploads/ <input type="file" multiple onchange={upload} /></label></div>
    <div class="split">
      <div class="panel tree">
        {#each entries as entry (entry.path)}
          <div class="entry" style:padding-left={`${entry.depth * 1}rem`}>
            {#if entry.kind === 'dir'}📁 {entry.path.split('/').pop()}
            {:else if entry.kind === 'link'}<span class="muted">🔗 {entry.path.split('/').pop()} (not followed)</span>
            {:else}<button class="link" onclick={() => view(entry.path)}>📄 {entry.path.split('/').pop()}</button> <span class="muted small">{size(entry.size)}</span>{/if}
          </div>
        {:else}<div class="empty">Empty.</div>{/each}
      </div>
      <div class="panel">
        {#if open}
          <div class="row"><strong class="grow">{open.path}</strong>
            {#if !open.binary && draft === null}<button class="small" onclick={() => (draft = open.content)}>Edit</button>{/if}
            <button class="small" onclick={() => save(open.path)}>Download</button>
            <button class="small danger" onclick={() => remove(open.path)}>Delete</button></div>
          {#if draft !== null}
            <textarea class="editor" rows="24" bind:value={draft}></textarea>
            <div class="row"><button class="primary" onclick={write}>Save (a commit)</button><button onclick={() => (draft = null)}>Cancel</button></div>
          {:else if open.binary}<p class="muted">A binary file ({size(open.size)}).</p>
          {:else if open.path.endsWith('.md')}<div class="markdown" use:markdown={open.content}></div>
          {:else}<pre>{open.content}</pre>{/if}
        {:else}<div class="empty">Pick a file.</div>{/if}
      </div>
    </div>
  {:else if tab === 'history'}
    <table>
      <thead><tr><th>Commit</th><th>When</th><th>What</th><th></th></tr></thead>
      <tbody>
        {#each commits as commit (commit.commit)}
          <tr><td><button class="link" onclick={() => showCommit(commit.commit)}><code>{commit.commit}</code></button></td>
            <td class="small">{when(commit.at)}</td><td>{commit.message}</td>
            <td><button class="small" onclick={() => undo(commit.commit)}>Undo</button></td></tr>
        {/each}
      </tbody>
    </table>
    {#if shown}<div class="panel"><button class="small" onclick={() => (shown = '')}>Close</button><pre>{shown}</pre></div>{/if}
  {:else}
    <table>
      <thead><tr><th>#</th><th>Started</th><th>Exit</th><th>Network</th><th>Took</th><th>Log</th></tr></thead>
      <tbody>
        {#each runs as run (run.id)}
          <tr><td>{run.id}</td><td class="small">{when(run.started_at)}</td>
            <td class:bad={run.exit_code !== 0}>{run.timed_out ? 'timed out' : run.exit_code ?? run.status}</td>
            <td>{run.network ? 'on' : 'off'}</td><td class="small">{(run.duration_ms / 1000).toFixed(1)} s</td>
            <td>{#if run.log_path}<button class="link" onclick={() => { tab = 'files'; view(run.log_path); }}>{run.log_path}</button>{/if}</td></tr>
        {:else}<tr><td colspan="6" class="empty">No run yet.</td></tr>{/each}
      </tbody>
    </table>
  {/if}
</div>

<style>
  .split {
    display: grid;
    grid-template-columns: 320px 1fr;
    gap: 0.8rem;
  }
  .tree {
    max-height: 75vh;
    overflow: auto;
  }
  .entry {
    white-space: nowrap;
  }
  .editor {
    width: 100%;
    font-family: ui-monospace, 'SF Mono', Menlo, Consolas, monospace;
  }
  .link {
    border: none;
    background: none;
    padding: 0;
    color: var(--accent);
  }
  @media (max-width: 760px) {
    .split {
      grid-template-columns: 1fr;
    }
  }
</style>
