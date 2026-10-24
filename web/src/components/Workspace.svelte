<script lang="ts">
  import Hint from './Hint.svelte';
  import Icon from './Icon.svelte';
  import Segmented from './Segmented.svelte';
  import { untrack } from 'svelte';
  import { api, download, fileToBase64, saveBlob, size, when } from '../lib/api';
  import { confirmDialog } from '../lib/dialog.svelte';
  import { markdown } from '../lib/markdown';
  import { live, route, toast } from '../lib/state.svelte';

  type Tab = 'files' | 'history' | 'runs';
  const TABS: { value: Tab; label: string }[] = [{ value: 'files', label: 'Files' }, { value: 'history', label: 'History' }, { value: 'runs', label: 'Script runs' }];

  let tab = $state<Tab>('files');
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

  // A link to #/library?path=notes/plan.md (a card of the chat's side panel) opens that file
  $effect(() => {
    const wanted = route.query.get('path');
    if (wanted) untrack(() => view(wanted).catch((error) => toast((error as Error).message, 'error')));
  });

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
    if (!(await confirmDialog({ title: 'Delete a file', message: `Delete ${path}? It stays in the history and can be undone.`, confirm: 'Delete', danger: true }))) return;
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
    if (!(await confirmDialog({ title: 'Undo a commit', message: `Undo ${commit}? A new commit reverts it.`, confirm: 'Undo' }))) return;
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

<div class="stack">
  {#if info}
    <p class="intro">{size(info.size)} of {size(info.max_size)} used · scripts:
      {#if info.run_ready}<span class="tag ok">ready</span>{:else}<span class="tag warn">off</span> <Hint text={info.run_problem} />{/if}</p>
  {/if}
  <div><Segmented label="Section" quiet options={TABS} bind:value={tab} /></div>
  {#if tab === 'files'}
    <div class="row"><label class="button"><Icon name="upload" size={18} />Upload into uploads/<input class="sr-only" type="file" multiple onchange={upload} /></label></div>
    <div class="split">
      <div class="panel tree">
        {#each entries as entry (entry.path)}
          <div class="entry" style:padding-left={`calc(var(--sp-16) * ${entry.depth})`}>
            {#if entry.kind === 'dir'}<span class="dir"><Icon name="folder" size={16} />{entry.path.split('/').pop()}</span>
            {:else if entry.kind === 'link'}<span class="muted"><Icon name="link" size={16} />{entry.path.split('/').pop()} (not followed)</span>
            {:else}<button class="link" aria-current={open?.path === entry.path ? 'true' : undefined} onclick={() => view(entry.path)}><Icon name="file" size={16} />{entry.path.split('/').pop()}</button> <span class="muted small">{size(entry.size)}</span>{/if}
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
            <textarea class="editor" rows="24" aria-label={`Content of ${open.path}`} bind:value={draft}></textarea>
            <div class="row"><button class="primary" onclick={write}>Save (a commit)</button><button onclick={() => (draft = null)}>Cancel</button></div>
          {:else if open.binary}<p class="muted">A binary file ({size(open.size)}).</p>
          {:else if open.path.endsWith('.md')}<div class="markdown" use:markdown={open.content}></div>
          {:else}<pre>{open.content}</pre>{/if}
        {:else}<div class="empty">Pick a file.</div>{/if}
      </div>
    </div>
  {:else if tab === 'history'}
    <table>
      <thead><tr><th>Commit</th><th>When</th><th>What</th><th><span class="sr-only">Actions</span></th></tr></thead>
      <tbody>
        {#each commits as commit (commit.commit)}
          <tr><td><button class="link" onclick={() => showCommit(commit.commit)}><code>{commit.commit}</code></button></td>
            <td class="small">{when(commit.at)}</td><td>{commit.message}</td>
            <td><button class="small" onclick={() => undo(commit.commit)}><Icon name="undo" size={16} />Undo</button></td></tr>
        {/each}
      </tbody>
    </table>
    {#if shown}<div class="panel"><button class="small" onclick={() => (shown = '')}>Close</button><pre>{shown}</pre></div>{/if}
  {:else}
    <table>
      <thead><tr><th>#</th><th>Started</th><th>Exit</th><th>Network</th><th>Took</th><th>Log</th></tr></thead>
      <tbody>
        {#each runs as run (run.id)}
          <tr><td class="num">{run.id}</td><td class="small">{when(run.started_at)}</td>
            <td class:danger={run.exit_code !== 0}>{run.timed_out ? 'timed out' : run.exit_code ?? run.status}</td>
            <td>{run.network ? 'on' : 'off'}</td><td class="small">{(run.duration_ms / 1000).toFixed(1)} s</td>
            <td>{#if run.log_path}<button class="link" onclick={() => { tab = 'files'; view(run.log_path); }}>{run.log_path}</button>{/if}</td></tr>
        {:else}<tr><td colspan="6" class="empty">No run yet.</td></tr>{/each}
      </tbody>
    </table>
  {/if}
</div>

<style>
  .entry {
    white-space: nowrap;
    padding: var(--sp-2) 0;
  }
  .entry :global(.icon) {
    margin-right: var(--sp-6);
    color: var(--muted);
  }
  .entry .dir {
    color: var(--text-2);
    font-weight: 600;
  }
  .entry .link {
    color: var(--text);
  }
  .entry .link[aria-current='true'] {
    color: var(--accent-text);
    font-weight: 650;
  }
  .editor {
    width: 100%;
    font-family: var(--font-mono);
  }
</style>
