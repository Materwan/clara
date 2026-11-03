<script lang="ts">
  // A file Clara handed over at the end of her answer (the `present` tool): its name, where it is, and two ways to take it.
  // The card is only what the server says about the file; nothing of its content is in the page until you download it.
  import { download, saveBlob, size } from '../lib/api';
  import { baseName, folderOf, libraryLink } from '../lib/panel.svelte';
  import { toast } from '../lib/state.svelte';
  import Icon from './Icon.svelte';

  // missing: the file is not in the workspace any more (a conversation opened again later)
  let { card }: { card: { path: string; size: number | null; origin?: string | null; missing?: boolean } } = $props();

  async function save(): Promise<void> {
    try {
      saveBlob(await download(`/v1/workspace/file?path=${encodeURIComponent(card.path)}&raw=true`), baseName(card.path));
    } catch (error) {
      toast((error as Error).message, 'error');
    }
  }

  function see(): void {
    window.location.hash = libraryLink('file', card.path);
  }
</script>

<div class="file-card present" class:gone={card.missing}>
  <div class="file-card-head">
    <span class="glyph"><Icon name="file" size={18} /></span>
    <span class="info">
      <strong title={card.path}>{baseName(card.path)}</strong>
      <span>
        {#if card.missing}No longer in the workspace
        {:else}{[folderOf(card.path), card.size !== null ? size(card.size) : ''].filter(Boolean).join(' · ')}{/if}
        {#if card.origin && !card.missing}<span class="tag warn" title={`It came from outside: ${card.origin}`}>from outside</span>{/if}
      </span>
    </span>
    {#if !card.missing}
      <span class="actions">
        <button class="small" onclick={see}><Icon name="folder" size={16} /> See in the workspace</button>
        <button class="small" onclick={save}><Icon name="download" size={16} /> Download</button>
      </span>
    {/if}
  </div>
</div>
