<script lang="ts">
  import { tick } from 'svelte';
  import Choice from '../components/Choice.svelte';
  import Facts from '../components/Facts.svelte';
  import Icon from '../components/Icon.svelte';
  import LevelSelect from '../components/LevelSelect.svelte';
  import PackCards from '../components/PackCards.svelte';
  import Wizard from '../components/Wizard.svelte';
  import { api, fileToBase64, size, when } from '../lib/api';
  import { excerpt, inWords } from '../lib/automation';
  import { confirmDialog } from '../lib/dialog.svelte';
  import { ladder } from '../lib/levels.svelte';
  import { keptNote } from '../lib/memory';
  import { conversations as history, go, route, toast } from '../lib/state.svelte';

  // A project is made in steps, as an automation is: what it is, where its files come from, and the special tools its new conversations
  // start with. The project and its files are made when the last step is confirmed, not before.
  type Problem = { step: string; field: string; message: string };
  const STEPS = [
    { id: 'basics', label: 'Basics', title: 'What the project is', lead: 'A name to find it by, a line on what it is for, and what Clara should always keep in mind in its conversations.' },
    { id: 'content', label: 'Content', title: 'Where its files come from', lead: 'Files given once. They go whole in the prompt when they fit, else Clara searches and reads them. More can be added on the project page at any time.' },
    { id: 'tools', label: 'Tools', title: 'Special tools of its conversations', lead: 'A new conversation in this project starts with the tools you switch on here. The “+” menu of a conversation can still change them, and the rules still decide each call.' },
    { id: 'review', label: 'Review', title: 'Review', lead: '' },
  ];
  const SOURCES = [
    { value: 'files', label: 'Upload files', hint: 'Text, code, PDF, Word, or a .zip.', icon: 'upload' },
    { value: 'folder', label: 'A folder', hint: 'Every text file in it, with its sub-folders.', icon: 'folder' },
    { value: 'github', label: 'A GitHub repository', hint: 'A snapshot of its text files, synced again when you ask.', icon: 'branch' },
    { value: 'none', label: 'Start empty', hint: 'Add files later, on the project page.', icon: 'file' },
  ];
  const empty = () => ({ name: '', description: '', instructions: '', source: 'none', repo: '', packs: ['web'] as string[] });

  let projects = $state<any[]>([]);
  let project = $state<any>(null);
  let conversations = $state<any[]>([]);
  let packs = $state<any[]>([]);
  let memory = $state<Record<string, { facts: number; waiting: number; total: number }>>({}); // the facts each project remembers, by scope
  let repo = $state('');
  let viewing = $state<any>(null);
  let busy = $state(false);
  let form = $state(empty());
  let chosen = $state<File[]>([]); // the files or the folder picked in the form, sent once the project exists
  let open = $state(false);
  let step = $state('basics');
  let checked = $state(false); // the last step was seen, or a save was tried: everything missing is shown where it is missing
  let tried = $state<string[]>([]); // the steps one tried to leave while something was missing: theirs is shown
  let saving = $state(false);
  let newButton = $state<HTMLButtonElement>();
  const id = $derived(route.params.id ? Number(route.params.id) : null);

  const packLabel = (packId: string): string => packs.find((pack) => pack.id === packId)?.label ?? packId;
  const plural = (count: number, word: string): string => `${count} ${word}${count === 1 ? '' : 's'}`;
  const remembered = (projectId: number): { facts: number; waiting: number; total: number } =>
    memory[`project:${projectId}`] ?? { facts: 0, waiting: 0, total: 0 };
  // The tools a project starts with, other than the web search every conversation has: for its card in the list
  const extras = (item: any): string => item.packs.filter((packId: string) => packId !== 'web').map(packLabel).join(' · ');

  // What is missing, and on which step. Next does not go past a step that holds something; the server says the same in its own words.
  const problems = $derived.by<Problem[]>(() => {
    const found: Problem[] = [];
    if (!form.name.trim()) found.push({ step: 'basics', field: 'pf-name', message: 'Give it a name.' });
    if ((form.source === 'files' || form.source === 'folder') && !chosen.length) {
      found.push({ step: 'content', field: 'pf-files', message: form.source === 'files' ? 'Choose the files, or start empty.' : 'Choose the folder, or start empty.' });
    }
    if (form.source === 'github' && !form.repo.trim()) found.push({ step: 'content', field: 'pf-repo', message: 'Give the repository as owner/name or a URL.' });
    return found;
  });
  const shown = (stepId: string): boolean => checked || tried.includes(stepId);
  const errorOf = (field: string): string | undefined => {
    const found = problems.find((item) => item.field === field);
    return found && shown(found.step) ? found.message : undefined;
  };
  const steps = $derived(
    STEPS.map((item) => {
      const missing = problems.filter((p) => p.step === item.id).map((p) => p.message);
      return { ...item, missing, problems: shown(item.id) ? missing.length : 0 };
    }),
  );
  const contentText = $derived(
    form.source === 'github' ? (form.repo.trim() ? `The GitHub repository ${form.repo.trim()}` : 'A GitHub repository')
      : form.source === 'none' ? 'No file yet'
      : chosen.length ? `${plural(chosen.length, 'file')}, ${size(chosen.reduce((sum, file) => sum + file.size, 0))}` : 'Nothing chosen yet',
  );

  $effect(() => {
    if (step === 'review') checked = true;
  });

  async function openForm(): Promise<void> {
    open = true;
    step = 'basics';
    checked = false;
    tried = [];
    await tick();
    (document.querySelector('#project-form .step-head h3') as HTMLElement | null)?.focus();
  }

  function resetForm(): void {
    open = false;
    form = empty();
    chosen = [];
  }

  async function closeForm(): Promise<void> {
    resetForm();
    await tick();
    newButton?.focus();
  }

  async function jump(problem: Problem): Promise<void> {
    step = problem.step;
    await tick();
    document.getElementById(problem.field)?.focus();
  }

  // Next, Enter or a later step was asked for while this one holds something missing: show it and go to the field
  async function blockedAt(stepId: string): Promise<void> {
    if (!tried.includes(stepId)) tried = [...tried, stepId];
    const found = problems.find((item) => item.step === stepId);
    if (found) await jump(found);
  }

  const pickFiles = (event: Event): void => {
    chosen = [...((event.target as HTMLInputElement).files ?? [])];
  };

  async function create(): Promise<void> {
    if (problems.length) {
      checked = true;
      await jump(problems[0]);
      return;
    }
    saving = true;
    try {
      const made = await api.post('/v1/projects', { name: form.name, description: form.description, instructions: form.instructions, packs: form.packs });
      try {
        if (form.source === 'github') await addRepoTo(made.id, form.repo.trim());
        else if (form.source !== 'none') await uploadTo(made.id, chosen);
      } catch (error) {
        toast(`The project is made, but its files were not added: ${(error as Error).message}`, 'error'); // they can be added from its page
      }
      resetForm();
      go('/projects/' + made.id);
    } catch (error) {
      toast((error as Error).message, 'error');
    } finally {
      saving = false;
    }
  }

  async function load(): Promise<void> {
    projects = (await api.get('/v1/projects')).projects;
    packs = (await api.get('/v1/packs', { where: 'chat' }).catch(() => ({ packs: [] }))).packs;
    await loadMemory();
    if (id) {
      project = await api.get(`/v1/projects/${id}`);
      conversations = (await api.get('/v1/conversations', { project: String(id) })).conversations;
    } else {
      project = null;
    }
  }

  async function loadMemory(): Promise<void> {
    memory = (await api.get('/v1/memory/scopes').catch(() => ({ scopes: {} }))).scopes;
  }

  $effect(() => {
    void id;
    load();
  });

  async function save(): Promise<void> {
    await api.patch(`/v1/projects/${id}`, { name: project.name, description: project.description, instructions: project.instructions });
    toast('Saved.', 'ok');
  }

  /** The auto level its conversations follow (null: the default). */
  async function setLevel(value: number | null): Promise<void> {
    try {
      await api.put(`/v1/projects/${id}/level`, { level: value });
      project.auto_level = value;
      toast('Saved: the conversations of this project that follow it now run at this level.', 'ok');
    } catch (error) {
      toast((error as Error).message, 'error');
    }
  }

  async function savePacks(): Promise<void> {
    try {
      await api.patch(`/v1/projects/${id}`, { packs: project.packs });
      toast('Saved: the next conversations of this project start with these tools.', 'ok');
      load();
    } catch (error) {
      toast((error as Error).message, 'error');
    }
  }

  async function uploadTo(projectId: number, files: File[]): Promise<void> {
    const payload = await Promise.all(files.map(async (file) => ({ path: (file as any).webkitRelativePath || file.name, data: await fileToBase64(file) })));
    const result = await api.post(`/v1/projects/${projectId}/files`, { files: payload });
    toast(`${result.added.length} added, ${result.replaced.length} replaced, ${result.skipped_count} left out.`, 'ok');
  }

  async function addRepoTo(projectId: number, name: string): Promise<void> {
    const result = await api.post(`/v1/projects/${projectId}/github`, { repo: name });
    toast(`${result.added.length} files from ${name}.`, 'ok');
  }

  async function upload(files: File[]): Promise<void> {
    busy = true;
    try {
      await uploadTo(id!, files);
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
      await addRepoTo(id!, repo);
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
    const message = `Delete this project and its files? Its conversations stay.${keptNote(remembered(id as number))}`;
    if (!(await confirmDialog({ title: 'Delete a project', message, confirm: 'Delete', danger: true }))) return;
    await api.del(`/v1/projects/${id}`);
    go('/projects');
  }

  async function inHistory(item: any): Promise<void> {
    try {
      await api.patch(`/v1/projects/${item.id}`, { in_history: !item.in_history });
      history.changed++;
      load();
    } catch (error) {
      toast((error as Error).message, 'error');
    }
  }

  async function pin(item: any): Promise<void> {
    await api.patch(`/v1/projects/${item.id}`, { pinned: !item.pinned });
    load();
  }
</script>

<div class="page stack">
  {#if !project}
    <div class="row start">
      <p class="intro grow">Files given once (uploads, folders, zips, GitHub snapshots) that a project's conversations use: in
        the prompt when they fit, else read with search tools. A project also says which special tools its new conversations start with.</p>
      <button class="primary" aria-expanded={open} aria-controls="project-form" bind:this={newButton}
        onclick={() => (open ? closeForm() : openForm())}><Icon name="plus" size={18} />New project</button>
    </div>
    {#if open}
      <Wizard id="project-form" label="New project" {steps} bind:step busy={saving} submitLabel="Create" onsubmit={create} oncancel={closeForm} onblocked={blockedAt}>
        {#snippet children(stepId)}
          {#if stepId === 'basics'}
            <label class="field">Name
              <input id="pf-name" bind:value={form.name} maxlength="100" aria-invalid={errorOf('pf-name') ? 'true' : undefined}
                aria-describedby={errorOf('pf-name') ? 'pf-name-error' : undefined} />
              {#if errorOf('pf-name')}<span class="field-error" id="pf-name-error">{errorOf('pf-name')}</span>{/if}
            </label>
            <label class="field">Description
              <span class="hint">Shown on its card in the list.</span>
              <textarea rows="2" bind:value={form.description}></textarea></label>
            <label class="field">Instructions for Clara
              <span class="hint">Added to the prompt of every conversation of the project.</span>
              <textarea rows="6" bind:value={form.instructions}></textarea></label>
          {:else if stepId === 'content'}
            <Choice cards legend="Start with" options={SOURCES} bind:value={form.source} onchange={() => (chosen = [])} />
            {#if form.source === 'files' || form.source === 'folder'}
              <div class="field">
                <span class="field-title">{form.source === 'files' ? 'Files' : 'Folder'}</span>
                <div class="row">
                  <label class="button"><Icon name={form.source === 'files' ? 'upload' : 'folder'} size={18} />{form.source === 'files' ? 'Choose files' : 'Choose a folder'}
                    {#if form.source === 'files'}<input id="pf-files" class="sr-only" type="file" multiple onchange={pickFiles} />
                    {:else}<input id="pf-files" class="sr-only" type="file" webkitdirectory onchange={pickFiles} />{/if}</label>
                  <span class="muted small" aria-live="polite">{chosen.length ? contentText : 'None chosen yet.'}</span>
                </div>
                {#if errorOf('pf-files')}<span class="field-error">{errorOf('pf-files')}</span>{/if}
              </div>
            {:else if form.source === 'github'}
              <label class="field narrow">Repository
                <span class="hint">owner/name or a GitHub URL, with @branch to take one. Its text files are copied once; sync it again from the project page.</span>
                <input id="pf-repo" placeholder="owner/name" bind:value={form.repo} aria-invalid={errorOf('pf-repo') ? 'true' : undefined}
                  aria-describedby={errorOf('pf-repo') ? 'pf-repo-error' : undefined} />
                {#if errorOf('pf-repo')}<span class="field-error" id="pf-repo-error">{errorOf('pf-repo')}</span>{/if}
              </label>
            {/if}
          {:else if stepId === 'tools'}
            <PackCards legend="Tools a new conversation starts with" {packs} bind:value={form.packs}
              help="Web search is what every conversation has. Switch on Mail, say, and each conversation of the project can use your mailbox from its first message." />
          {:else}
            {#if problems.length}
              <div class="stack">
                <p class="row"><span class="tag danger"><Icon name="warn" size={14} />{problems.length} to fix before it can be created</span></p>
                <ul class="problems">{#each problems as problem}<li><button type="button" class="link" onclick={() => jump(problem)}>{problem.message}</button></li>{/each}</ul>
              </div>
            {/if}
            <dl class="facts">
              <dt>Name</dt><dd>{form.name.trim() || '—'}</dd>
              <dt>Description</dt><dd>{form.description.trim() || '—'}</dd>
              <dt>Instructions</dt><dd>{excerpt(form.instructions) || '—'}</dd>
              <dt>Files</dt><dd>{contentText}</dd>
              <dt>Tools</dt><dd>{form.packs.length ? inWords(form.packs.map(packLabel)) : 'None, not even web search'}</dd>
            </dl>
          {/if}
        {/snippet}
      </Wizard>
    {/if}
    <div class="projects">
      {#each projects as item (item.id)}
        <div class="panel">
          <div class="row project-head"><span class="glyph"><Icon name="folder" size={18} /></span><a class="grow" href={'#/projects/' + item.id}><strong>{item.name}</strong></a>
            <button class="ghost icon-btn" aria-pressed={!!item.pinned} title={item.pinned ? 'Unpin' : 'Pin'} aria-label={item.pinned ? 'Unpin' : 'Pin'} onclick={() => pin(item)}><Icon name="pin" size={18} /></button></div>
          <div class="muted small">{item.description}</div>
          <div class="small">{item.files} files · {size(item.size)} · {item.conversations} conversations · {item.context.inline ? 'in the prompt' : 'searched'}</div>
          {#if extras(item)}<div class="small muted">Starts with {extras(item)}</div>{/if}
          {#if remembered(item.id).facts}
            <div class="row small muted">Remembers {plural(remembered(item.id).facts, 'fact')}
              {#if remembered(item.id).waiting}<span class="tag warn">{remembered(item.id).waiting} waiting for you</span>{/if}</div>
          {/if}
          <button class="small toggle" aria-pressed={item.in_history} title="List this project's conversations in the history of the rail"
            onclick={() => inHistory(item)}><Icon name="eye" size={14} />Show in history</button>
        </div>
      {:else}
        <div class="empty">No project yet.</div>
      {/each}
    </div>
  {:else}
    <a class="back" href="#/projects"><Icon name="left" size={18} />All projects</a>
    <div class="row"><h2 class="grow">{project.name}</h2>
      <button class="toggle" aria-pressed={project.in_history} title="List this project's conversations in the history of the rail"
        onclick={() => inHistory(project)}><Icon name="eye" size={18} />Show in history</button>
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
          <label class="button"><Icon name="upload" size={18} />Add files <input class="sr-only" type="file" multiple onchange={(e) => upload([...((e.target as HTMLInputElement).files ?? [])])} /></label>
          <label class="button"><Icon name="folder" size={18} />Add a folder <input class="sr-only" type="file" webkitdirectory onchange={(e) => upload([...((e.target as HTMLInputElement).files ?? [])])} /></label>
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
    <div class="panel stack">
      <div class="row">
        <span class="grow"><strong>Auto level</strong><br /><span class="muted small">How much Clara may do without asking in the conversations of this project that have no level of their own (the Auto mode page sets what each level allows).</span></span>
        <LevelSelect value={project.auto_level} effective={ladder.defaults.chat} follows="the default" label="Auto level of this project" onchange={setLevel} />
      </div>
    </div>
    <div class="panel stack">
      <PackCards legend="Special tools of its new conversations" {packs} bind:value={project.packs}
        help="A conversation started in this project begins with these tools; its “+” menu can still change them, and the ones already begun keep theirs." />
      <div class="row"><button class="primary" onclick={savePacks}>Save the tools</button></div>
    </div>
    <div class="panel stack">
      <h2>Memory</h2>
      <Facts scope={`project:${id}`} onchange={loadMemory} />
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
      {#if conversations.length}
        <ul class="rows">
          {#each conversations as conversation (conversation.id)}
            <li><a href={'#/chat/' + conversation.id}>{conversation.title || conversation.preview || 'New conversation'}</a><span class="when">{when(conversation.updated_at)}</span></li>
          {/each}
        </ul>
      {:else}<div class="muted small">None yet: start one with “New conversation” above.</div>{/if}
    </div>
  {/if}
</div>

<style>
  .projects {
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
    gap: var(--sp-16);
  }
  .projects .panel > * + * {
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
