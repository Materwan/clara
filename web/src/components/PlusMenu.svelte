<script lang="ts">
  import Icon from './Icon.svelte';

  // The "+" of the composer: add files, switch the special tools of this conversation on or off (saved with it, so it
  // follows the conversation to Discord and the terminal), and choose its project. A disclosure (aria-expanded): it holds
  // buttons that toggle (aria-pressed) and a select, not menu items.
  type Pack = { id: string; label: string; description: string; icon: string; available: boolean; reason: string };
  type Project = { id: number; name: string };
  let { packs, active, projects, project, onFiles, onToggle, onProject }: {
    packs: Pack[]; active: string[]; projects: Project[]; project: number | null;
    onFiles: () => void; onToggle: (id: string) => void; onProject: (id: number | null) => void;
  } = $props();
  let open = $state(false);
  let root = $state<HTMLElement>();
  let button = $state<HTMLButtonElement>();

  function keydown(event: KeyboardEvent): void {
    if (!open || event.key !== 'Escape') return;
    event.stopPropagation();
    open = false;
    button?.focus();
  }

  function outside(event: Event): void {
    if (open && !root?.contains(event.target as Node)) open = false;
  }
</script>

<svelte:window onkeydowncapture={keydown} onpointerdown={outside} onfocusin={outside} />

<div class="plus" bind:this={root}>
  <button type="button" class="ghost icon-btn plus-button" title="Add files and tools" aria-label="Add files and tools" aria-expanded={open}
    aria-controls="plus-menu" bind:this={button} onclick={() => (open = !open)}>
    <Icon name="plus" />
  </button>
  {#if open}
    <div class="plus-menu frame" id="plus-menu" role="group" aria-label="Files and tools">
      <button type="button" class="plus-item" onclick={() => { open = false; onFiles(); }}>
        <Icon name="clip" size={18} /><span class="plus-text"><b>Add files</b><small>PDF, Word, text, pictures (4 at most)</small></span>
      </button>
      <p class="plus-head">Tools for this conversation</p>
      {#each packs as pack (pack.id)}
        <button type="button" class="plus-item" aria-pressed={active.includes(pack.id)} disabled={!pack.available && !active.includes(pack.id)}
          title={pack.reason || pack.description} onclick={() => onToggle(pack.id)}>
          <Icon name={pack.icon} size={18} />
          <span class="plus-text"><b>{pack.label}</b><small>{pack.reason || pack.description}</small></span>
          <span class="plus-state" aria-hidden="true">{#if active.includes(pack.id)}<Icon name="check" size={16} />{/if}</span>
        </button>
      {/each}
      <label class="plus-project">
        <Icon name="folder" size={18} /><span class="plus-text"><b>Project</b></span>
        <select aria-label="Project" value={project === null ? '' : String(project)}
          onchange={(event) => { const value = (event.target as HTMLSelectElement).value; onProject(value ? Number(value) : null); }}>
          <option value="">none</option>
          {#each projects as item (item.id)}<option value={String(item.id)}>{item.name}</option>{/each}
        </select>
      </label>
    </div>
  {/if}
</div>
