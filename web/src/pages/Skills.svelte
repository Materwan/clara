<script lang="ts">
  import { tick } from 'svelte';
  import Checkbox from '../components/Checkbox.svelte';
  import Choice from '../components/Choice.svelte';
  import Icon from '../components/Icon.svelte';
  import { api, fileToBase64, size, when } from '../lib/api';
  import { confirmDialog } from '../lib/dialog.svelte';
  import {
    MAX_PROJECTS, MAX_ZIP, type Project, type Scope, type Skill, type Source, addBody, cap, held, previewSource, scopeLabel, scopeProblem, zipProblem,
  } from '../lib/skills';
  import { live, session, toast } from '../lib/state.svelte';

  // The skills: instructions you add and run from a chat with `/name arguments`. A list, and a panel to add one: paste a SKILL.md or send a zip, read
  // it back in a preview (every file, every warning), choose where it applies, then add it. Everything a skill holds is drawn as plain text, never as HTML.
  type PreviewFile = { path: string; size: number; kind: 'text' | 'binary'; note: string; text: string | null };
  type Preview = {
    name: string | null; description: string; argument_hint: string; packs: { id: string; label: string; known: boolean }[]; ignored: string[];
    files: PreviewFile[]; size: number; warnings: string[]; errors: string[]; can_add: boolean; skill_md_text: string; would_replace: boolean;
    skills_held: number; skills_max: number;
  };

  let skills = $state<Skill[] | null>(null);
  let loadError = $state('');
  let projects = $state<Project[]>([]);
  let packLabels = $state<Record<string, string>>({});
  let adding = $state(false);
  let how = $state<'paste' | 'zip'>('paste');
  let text = $state('');
  let given = $state(''); // the name, for a skill whose frontmatter has none
  let zip = $state<File | null>(null);
  let zipError = $state('');
  let checking = $state(false);
  let saving = $state(false);
  let problem = $state(''); // why the preview or the add could not be done
  let preview = $state<Preview | null>(null);
  let previewed: Source | null = null; // the very input that was previewed: what is added
  let where = $state<'global' | 'projects'>('global');
  let picked = $state<number[]>([]);
  let open = $state<string[]>([]); // the files whose text is shown
  let newButton = $state<HTMLButtonElement>();

  /** Only a token that may answer everything adds or removes a skill (the others get a 403: they look). */
  const canEdit = $derived(session.approvals === 'all');
  const scope = $derived<Scope>(where === 'global' ? { kind: 'global' } : { kind: 'projects', ids: picked });
  const scopeIssue = $derived(scopeProblem(scope));
  const canAdd = $derived(canEdit && !!preview?.can_add && !scopeIssue && !saving);
  const canPreview = $derived(canEdit && !checking && (how === 'paste' ? !!text.trim() : !!zip && !zipError));
  const label = (id: string): string => packLabels[id] ?? id;

  async function load(): Promise<void> {
    try {
      skills = (await api.get('/v1/skills')).skills;
      loadError = '';
    } catch (error) {
      loadError = (error as Error).message;
    }
  }

  $effect(() => {
    void live.refresh;
    load();
  });

  // the names of the projects (the scope of a skill) and of the packs (what it needs): nice to have, the page works with ids without them
  $effect(() => {
    api.get('/v1/projects').then((found) => (projects = found.projects)).catch(() => undefined);
    api.get('/v1/packs').then((found) => (packLabels = Object.fromEntries(found.packs.map((pack: { id: string; label: string }) => [pack.id, pack.label])))).catch(() => undefined);
  });

  /** What was typed or chosen changed: the preview no longer describes it, so it goes. */
  function forget(): void {
    preview = null;
    previewed = null;
    problem = '';
  }

  async function openAdd(): Promise<void> {
    adding = true;
    await tick();
    document.getElementById(how === 'paste' ? 'sk-text' : 'sk-zip')?.focus();
  }

  async function closeAdd(): Promise<void> {
    adding = false;
    text = given = zipError = '';
    zip = null;
    where = 'global';
    picked = [];
    open = [];
    forget();
    await tick();
    newButton?.focus();
  }

  function pickZip(event: Event): void {
    const input = event.target as HTMLInputElement;
    forget();
    zip = input.files?.[0] ?? null;
    zipError = zip ? zipProblem(zip.size) : '';
  }

  async function check(): Promise<void> {
    if (!canPreview) return;
    checking = true;
    forget();
    try {
      const source = previewSource(how, text, how === 'zip' && zip ? await fileToBase64(zip) : '', given);
      preview = await api.post('/v1/skills/preview', source);
      previewed = source;
      open = [];
      await tick();
      document.getElementById('sk-preview')?.focus();
    } catch (error) {
      problem = (error as Error).message;
    } finally {
      checking = false;
    }
  }

  async function add(): Promise<void> {
    if (!canAdd || !preview || !previewed) return;
    const name = preview.name;
    let replace = false;
    if (preview.would_replace) {
      const message = `A skill named ${name} exists. Adding this one replaces it, with its files and the projects it was reserved for. Conversations that ran it keep their messages.`;
      if (!(await confirmDialog({ title: `Replace ${name}`, message, confirm: 'Replace it', danger: true }))) return;
      replace = true;
    }
    saving = true;
    problem = '';
    try {
      await api.post('/v1/skills', addBody(previewed, scope, replace));
      toast(replace ? `Replaced ${name}.` : `Added ${name}: type /${name} in a chat to run it.`, 'ok');
      await closeAdd();
      await load();
    } catch (error) {
      problem = (error as Error).message;
    } finally {
      saving = false;
    }
  }

  async function remove(skill: Skill): Promise<void> {
    const message = `Delete the skill ${skill.name}? Its ${skill.file_count === 1 ? 'file is' : `${skill.file_count} files are`} removed with it. Conversations that ran it keep their messages; /${skill.name} becomes plain text.`;
    if (!(await confirmDialog({ title: `Delete ${skill.name}`, message, confirm: 'Delete skill', danger: true }))) return;
    try {
      await api.del(`/v1/skills/${encodeURIComponent(skill.name)}`);
      toast(`Deleted ${skill.name}.`, 'ok');
    } catch (error) {
      toast((error as Error).message, 'error');
    }
    await load();
  }

  const toggleFile = (path: string): void => {
    open = open.includes(path) ? open.filter((item) => item !== path) : [...open, path];
  };
  const togglePicked = (id: number): void => {
    picked = picked.includes(id) ? picked.filter((item) => item !== id) : [...picked, id];
  };
</script>

<div class="page stack">
  <div class="row start">
    <p class="intro grow">A <b>skill</b> is a folder with a <code>SKILL.md</code>: instructions you run from a chat by typing <code>/name</code> and its arguments.
      It is text only: it adds no tool and grants no right, and the mode of the conversation still decides every call Clara makes.</p>
    <button class="primary" disabled={!canEdit} aria-expanded={adding} aria-controls="sk-add" bind:this={newButton} onclick={() => (adding ? closeAdd() : openAdd())}>
      <Icon name="plus" size={18} />Add a skill</button>
  </div>
  {#if !canEdit}
    <p class="muted small">This token may not add or remove skills: their text steers Clara like your own message. You can look at them.</p>
  {/if}

  {#if adding}
    <form id="sk-add" class="panel stack" aria-labelledby="sk-add-title" novalidate onsubmit={(event) => { event.preventDefault(); check(); }}>
      <h2 id="sk-add-title">Add a skill</h2>
      <Choice legend="How" options={[{ value: 'paste', label: 'Paste a SKILL.md' }, { value: 'zip', label: 'Send a zip of the folder' }]} bind:value={how} onchange={forget} />
      {#if how === 'paste'}
        <label class="field">SKILL.md
          <span class="hint">The text of the file: a line <code>---</code>, a <code>name</code> and a <code>description</code>, a line <code>---</code>, then the instructions.</span>
          <textarea id="sk-text" class="mono" rows="12" spellcheck="false" autocomplete="off" bind:value={text} oninput={forget}></textarea>
        </label>
      {:else}
        <label class="field">The folder, as a .zip
          <span class="hint"><code>SKILL.md</code> at its top, or in the one folder that holds everything. {MAX_ZIP.toLocaleString('en-US')} bytes at most.</span>
          <input id="sk-zip" type="file" accept=".zip,application/zip,application/x-zip-compressed" aria-invalid={zipError ? 'true' : undefined}
            aria-describedby={zipError ? 'sk-zip-error' : undefined} onchange={pickZip} />
        </label>
        {#if zipError}<p class="field-error" id="sk-zip-error" role="alert">{zipError}</p>{/if}
      {/if}
      <label class="field">Name
        <span class="hint">Only when the SKILL.md has no <code>name</code> (a zip: the name of its folder is the other choice). Lower-case letters, digits and dashes.</span>
        <input maxlength="80" autocomplete="off" spellcheck="false" bind:value={given} oninput={forget} />
      </label>
      <div class="row">
        <button type="submit" class="primary" disabled={!canPreview} aria-busy={checking || undefined}>Preview</button>
        <button type="button" onclick={closeAdd}>Cancel</button>
        <span class="muted small">Nothing is added yet: you see what it holds first.</span>
      </div>
      {#if problem && !preview}<p class="field-error" role="alert">{problem}</p>{/if}
    </form>
  {/if}

  {#if preview}
    <section id="sk-preview" class="panel stack" tabindex="-1" aria-labelledby="sk-preview-title">
      <h2 id="sk-preview-title">Preview: {preview.name ? `/${preview.name}` : 'no name yet'}</h2>
      {#if preview.errors.length}
        <div class="errors" role="alert">
          <p><Icon name="warn" size={14} /> <b>This cannot be added yet:</b></p>
          <ul>{#each preview.errors as error}<li>{error}</li>{/each}</ul>
        </div>
      {/if}
      {#each preview.warnings as warning}<p class="call-warning" role="note"><Icon name="warn" size={14} /><span>{warning}</span></p>{/each}
      {#if preview.would_replace}
        <p class="call-warning" role="note"><Icon name="warn" size={14} /><span>This replaces the skill of the same name: its files and the projects it was reserved for are replaced too.</span></p>
      {/if}
      <dl class="facts">
        <dt>Name</dt><dd>{preview.name ? `/${preview.name}` : '—'}</dd>
        <dt>Description</dt><dd>{preview.description || '—'}</dd>
        <dt>Arguments</dt><dd>{preview.argument_hint || 'none named'}</dd>
        <dt>Needs</dt>
        <dd>
          {#if preview.packs.length}
            <span class="chips">
              {#each preview.packs as pack (pack.id)}
                {#if pack.known}<span class="chip">{pack.label}</span>{:else}<span class="tag warn"><Icon name="warn" size={14} />{pack.id}: no such pack</span>{/if}
              {/each}
            </span>
          {:else}no special tool{/if}
        </dd>
      </dl>
      <p class="muted small">The packs it needs are shown when you type its command; Clara switches none on by herself.</p>

      <div class="stack">
        <h3>SKILL.md <span class="muted small">{size(new TextEncoder().encode(preview.skill_md_text).length)}</span></h3>
        <!-- svelte-ignore a11y_no_noninteractive_tabindex (a block that scrolls must take the keyboard, or its lines cannot be reached without a mouse) -->
        <pre class="shown" tabindex="0" aria-label="The text of SKILL.md">{preview.skill_md_text}</pre>
      </div>

      <div class="stack">
        <h3>Other files <span class="muted small">{preview.files.length}</span></h3>
        {#if preview.files.length}
          <p class="muted small">They are stored with the skill and never run, served or put in a prompt: only <code>SKILL.md</code> is read.</p>
          <ul class="files" aria-label="Files of the skill">
            {#each preview.files as file, index (file.path)}
              {@const shown = file.text === null ? null : cap(file.text)}
              <li>
                <div class="row">
                  {#if shown}
                    <button type="button" class="link" aria-expanded={open.includes(file.path)} aria-controls={`sk-file-${index}`} onclick={() => toggleFile(file.path)}><code>{file.path}</code></button>
                  {:else}<code class="path">{file.path}</code>{/if}
                  <span class="muted small">{size(file.size)} · {file.kind === 'text' ? 'text' : 'binary: not shown'}</span>
                </div>
                {#if file.note}<p class="muted small">{file.note}</p>{/if}
                {#if shown && open.includes(file.path)}
                  <!-- svelte-ignore a11y_no_noninteractive_tabindex (as above) -->
                  <pre class="shown" id={`sk-file-${index}`} tabindex="0" aria-label={`The text of ${file.path}`}>{shown.text}</pre>
                  {#if shown.hidden}<p class="muted small">… and {shown.hidden.toLocaleString('en-US')} more characters, not shown here.</p>{/if}
                {/if}
              </li>
            {/each}
          </ul>
        {:else}<p class="muted small">None: the skill is its SKILL.md alone.</p>{/if}
      </div>

      <Choice legend="Where" options={[
          { value: 'global', label: 'Everywhere', hint: 'Every conversation can run it, those of projects included.' },
          { value: 'projects', label: 'Only in these projects', hint: 'It does not exist in any other conversation: /name there is plain text.', disabled: !projects.length },
        ]} bind:value={where} />
      {#if where === 'projects'}
        <fieldset class="projects" aria-describedby={scopeIssue ? 'sk-scope-problem' : undefined}>
          <legend>Projects <span class="muted small">({MAX_PROJECTS} at most)</span></legend>
          {#each projects as project (project.id)}
            <label class="option"><Checkbox checked={picked.includes(project.id)} onchange={() => togglePicked(project.id)} /><span class="option-text"><strong>{project.name}</strong></span></label>
          {/each}
        </fieldset>
      {:else if !projects.length}<p class="muted small">There is no project yet: a skill can be reserved for projects once you have made one.</p>{/if}
      {#if scopeIssue}<p class="field-error" id="sk-scope-problem" role="alert">{scopeIssue}</p>{/if}

      {#if problem}<p class="field-error" role="alert">{problem}</p>{/if}
      <div class="row">
        <button type="button" class="primary" disabled={!canAdd} aria-busy={saving || undefined} onclick={add}>{preview.would_replace ? 'Replace the skill' : 'Add the skill'}</button>
        {#if !preview.can_add}<span class="muted small">Fix what is listed above, then preview it again.</span>{/if}
      </div>
    </section>
  {/if}

  {#if loadError}<p class="field-error" role="alert">{loadError}</p>{/if}
  {#if skills === null}
    {#if !loadError}<p class="muted" role="status">Loading…</p>{/if}
  {:else}
    <div class="row"><h2 class="grow">Your skills</h2><span class="muted small" role="status">{held(skills.length)}</span></div>
    {#each skills as skill (skill.name)}
      <article class="panel skill stack" aria-labelledby={`sk-${skill.name}`}>
        <div class="row">
          <h3 class="grow" id={`sk-${skill.name}`}><code>/{skill.name}</code>{#if skill.argument_hint} <span class="muted small hint">{skill.argument_hint}</span>{/if}</h3>
          <button type="button" class="small danger" disabled={!canEdit} aria-label={`Delete ${skill.name}`} onclick={() => remove(skill)}>Delete</button>
        </div>
        <p>{skill.description}</p>
        {#if skill.packs.length}
          <div class="chips" aria-label="Special tools it needs">{#each skill.packs as id (id)}<span class="chip">{label(id)}</span>{/each}</div>
        {/if}
        <p class="muted small">{scopeLabel(skill, projects)} · {skill.file_count} {skill.file_count === 1 ? 'file' : 'files'}, {size(skill.size)} · added {when(skill.created_at)}</p>
      </article>
    {:else}
      {#if !adding}
        <div class="empty">No skill yet: add one to run a task you repeat with <code>/name</code> in a chat.</div>
      {/if}
    {/each}
  {/if}
</div>

<style>
  .skill h3 { margin: 0; overflow-wrap: anywhere; }
  .skill h3 .hint { font-weight: 400; }
  textarea.mono { font-family: var(--font-mono); font-size: var(--fs-sm); line-height: 1.45; }
  .errors { padding: var(--sp-8) var(--sp-12); border-radius: var(--r-xs); background: var(--danger-soft); color: var(--danger); font-size: var(--fs-md); overflow-wrap: anywhere; }
  .errors p { margin: 0 0 var(--sp-4); }
  .errors ul { margin: 0; padding-left: var(--sp-20); }
  .shown { max-height: 18rem; overflow: auto; white-space: pre-wrap; overflow-wrap: anywhere; font-size: var(--fs-xs); line-height: 1.5; }
  .files { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: var(--sp-8); }
  .files li { min-width: 0; padding-bottom: var(--sp-8); border-bottom: 1px solid var(--line); }
  .files li:last-child { border-bottom: none; padding-bottom: 0; }
  .files .row { gap: var(--sp-4) var(--sp-10); }
  .files p { margin: var(--sp-2) 0 0; }
  .path, .files button code { overflow-wrap: anywhere; }
  .files .shown { margin-top: var(--sp-6); }
  .projects { border: 1px solid var(--line); border-radius: var(--r-md); padding: var(--sp-8) var(--sp-12); margin: 0; min-width: 0; display: flex; flex-direction: column; gap: var(--sp-8); max-height: 14rem; overflow: auto; }
  .projects legend { padding: 0 var(--sp-4); color: var(--muted); font-size: var(--fs-sm); font-weight: 600; }
  .projects .option-text strong { overflow-wrap: anywhere; }
  dl.facts dd { min-width: 0; }
</style>
