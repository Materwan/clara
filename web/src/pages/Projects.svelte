<script lang="ts">
  import Icon from '../components/Icon.svelte';
  import { api, fileToBase64, size, when } from '../lib/api';
  import { confirmDialog } from '../lib/dialog.svelte';
  import { go, route, toast } from '../lib/state.svelte';

  let projects = $state<any[]>([]);
  let project = $state<any>(null);
  let conversations = $state<any[]>([]);
  let name = $state('');
  let repo = $state('');
  let viewing = $state<any>(null);
  let busy = $state(false);
  const id = $derived(route.params.id ? Number(route.params.id) : null);

  async function load(): Promise<void> {
    projects = (await api.get('/v1/projects')).projects;
    if (id) {
      project = await api.get(`/v1/projects/${id}`);
      conversations = (await api.get('/v1/conversations', { project: String(id) })).conversations;
    } else {
      project = null;
    }
  }

  $effect(() => {
    void id;
    load();
  });

  async function create(event: SubmitEvent): Promise<void> {
    event.preventDefault();
    const made = await api.post('/v1/projects', { name });
    name = '';
    go('/projects/' + made.id);
  }

  async function save(): Promise<void> {
    await api.patch(`/v1/projects/${id}`, { name: project.name, description: project.description, instructions: project.instructions });
    toast('Saved.', 'ok');
  }

  async function upload(files: File[]): Promise<void> {
    busy = true;
    try {
      const payload = await Promise.all(files.map(async (file) => ({ path: (file as any).webkitRelativePath || file.name, data: await fileToBase64(file) })));
      const result = await api.post(`/v1/projects/${id}/files`, { files: payload });
      toast(`${result.added.length} added, ${result.replaced.length} replaced, ${result.skipped_count} left out.`, 'ok');
      load();
    } catch (error) {
      toast((error as Error).message, 'error');
    } finally {
      busy = false;
    }
  }

  async function addRepo(event: SubmitEvent): Promise<void> {
    event.preventDefault();
    busy = true;
    try {
      const result = await api.post(`/v1/projects/${id}/github`, { repo });
      toast(`${result.added.length} files from ${repo}.`, 'ok');
      repo = '';
      load();
    } catch (error) {
      toast((error as Error).message, 'error');
    } finally {
      busy = false;
    }
  }

  async function sync(source: any): Promise<void> {
    busy = true;
    try {
      await api.post(`/v1/projects/${id}/sources/${source.id}/sync`);
      load();
    } catch (error) {
      toast((error as Error).message, 'error');
    } finally {
      busy = false;
    }
  }

  async function view(path: string): Promise<void> {
    viewing = await api.get(`/v1/projects/${id}/file`, { path });
  }

  async function removeFile(path: string): Promise<void> {
    await api.del(`/v1/projects/${id}/files?path=${encodeURIComponent(path)}`);
    load();
  }

  async function removeProject(): Promise<void> {
    if (!(await confirmDialog({ title: 'Delete a project', message: 'Delete this project and its files? Its conversations stay.', confirm: 'Delete', danger: true }))) return;
    await api.del(`/v1/projects/${id}`);
    go('/projects');
  }

  async function pin(item: any): Promise<void> {
    await api.patch(`/v1/projects/${item.id}`, { pinned: !item.pinned });
    load();
  }
</script>

<div class="page stack">
  {#if !project}
    <p class="intro">Files given once (uploads, folders, zips, GitHub snapshots) that a project's conversations use: in
      the prompt when they fit, else read with search tools.</p>
    <form class="row" onsubmit={create}><input class="grow" aria-label="New project's name" placeholder="A new project's name" bind:value={name} required /><button class="primary"><Icon name="plus" size={18} />Create</button></form>
    <div class="cards">
      {#each projects as item (item.id)}
        <div class="panel">
          <div class="row project-head"><span class="glyph"><Icon name="folder" size={18} /></span><a class="grow" href={'#/projects/' + item.id}><strong>{item.name}</strong></a>
            <button class="ghost icon-btn" aria-pressed={!!item.pinned} title={item.pinned ? 'Unpin' : 'Pin'} aria-label={item.pinned ? 'Unpin' : 'Pin'} onclick={() => pin(item)}><Icon name="pin" size={18} /></button></div>
          <div class="muted small">{item.description}</div>
          <div class="small">{item.files} files · {size(item.size)} · {item.conversations} conversations · {item.context.inline ? 'in the prompt' : 'searched'}</div>
        </div>
      {:else}
        <div class="empty">No project yet.</div>
      {/each}
    </div>
  {:else}
    <a class="back" href="#/projects"><Icon name="left" size={18} />All projects</a>
    <div class="row"><h2 class="grow">{project.name}</h2>
      <button onclick={() => go('/chat?project=' + id)}><Icon name="plus" size={18} />New conversation</button><button class="danger" onclick={removeProject}>Delete</button></div>
    <div class="grid-2">
      <div class="panel stack">
        <label class="field">Name <input bind:value={project.name} /></label>
        <label class="field">Description <textarea rows="2" bind:value={project.description}></textarea></label>
        <label class="field">Instructions for Clara <textarea rows="6" bind:value={project.instructions}></textarea></label>
        <button class="primary" onclick={save}>Save</button>
      </div>
      <div class="panel stack">
        <div class="small">{project.files} files · {size(project.size)} · {project.context.percent}% of the window ·
          <strong>{project.context.inline ? 'given whole in the prompt' : 'too big: Clara searches and reads them'}</strong></div>
        <div class="row">
          <label class="button"><Icon name="upload" size={18} />Add files <input type="file" multiple hidden onchange={(e) => upload([...((e.target as HTMLInputElement).files ?? [])])} /></label>
          <label class="button"><Icon name="folder" size={18} />Add a folder <input type="file" webkitdirectory hidden onchange={(e) => upload([...((e.target as HTMLInputElement).files ?? [])])} /></label>
          {#if busy}<span class="muted small">Working…</span>{/if}
        </div>
        <form class="row" onsubmit={addRepo}><input class="grow" aria-label="GitHub repository" placeholder="GitHub owner/name or URL (@branch)" bind:value={repo} required /><button>Add repository</button></form>
        {#each project.sources as source (source.id)}
          <div class="row small"><span class="grow">{source.repo}{source.ref ? '@' + source.ref : ''} ({source.files} files, synced {when(source.synced_at)})
            {#if source.problem}<span class="danger">{source.problem}</span>{/if}</span>
            <button class="small" onclick={() => sync(source)} disabled={busy}>Sync</button>
            <button class="small danger" onclick={async () => { await api.del(`/v1/projects/${id}/sources/${source.id}`); load(); }}>Remove</button></div>
        {/each}
      </div>
    </div>
    <div class="panel">
      <h2>Files</h2>
      <table>
        <tbody>
          {#each project.file_list as file (file.path)}
            <tr><td><button class="link" onclick={() => view(file.path)}>{file.path}</button></td><td class="small muted">{file.kind}</td>
              <td class="small">{size(file.size)}</td><td><button class="small danger" onclick={() => removeFile(file.path)}>Remove</button></td></tr>
          {:else}
            <tr><td class="empty">No file yet.</td></tr>
          {/each}
        </tbody>
      </table>
    </div>
    {#if viewing}
      <div class="panel"><div class="row"><strong class="grow">{viewing.path}</strong><button class="small" onclick={() => (viewing = null)}>Close</button></div><pre>{viewing.content}</pre></div>
    {/if}
    <div class="panel">
      <h2>Conversations</h2>
      {#each conversations as conversation}<div><a href={'#/chat/' + conversation.id}>{conversation.title || conversation.preview}</a> <span class="muted small">{when(conversation.updated_at)}</span></div>
      {:else}<div class="muted small">None yet: start one with “New conversation” above.</div>{/each}
    </div>
  {/if}
</div>

<style>
  .cards {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
    gap: var(--sp-16);
  }
  .cards .panel > * + * {
    margin-top: var(--sp-8);
  }
  .project-head strong {
    font-family: var(--font-display);
    font-weight: 600;
    letter-spacing: -0.02em;
    font-size: var(--fs-lg);
  }
  .project-head a {
    color: var(--text);
    text-decoration: none;
  }
  .project-head a:hover {
    text-decoration: underline;
  }
  .project-head button[aria-pressed='true'] {
    color: var(--accent-text);
  }
  .back {
    display: inline-flex;
    align-items: center;
    gap: var(--sp-6);
    color: var(--muted);
    text-decoration: none;
    font-size: var(--fs-sm);
  }
  .back:hover {
    color: var(--text);
  }
</style>
