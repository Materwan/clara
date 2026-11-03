<script lang="ts">
  // A file Clara handed over at the end of her answer (the `present` tool): its name, where it is, and a way to take it.
  // The card is only what the server says about the file; nothing of its content is in the page until you download it.
  //   workspace: See in the workspace (the Library) and Download
  //   vault:     See in the vault (the Library's Vault view) and Download (the note's text, from the vault's own route)
  //   drive:     Open in Drive, a link on Google's own address: nothing of the file passes through this server
  import { api, download, saveBlob, size } from '../lib/api';
  import { type Presented, baseName, folderOf, libraryLink } from '../lib/panel.svelte';
  import { toast } from '../lib/state.svelte';
  import Icon from './Icon.svelte';

  let { card }: { card: Presented } = $props();
  const kind = $derived(card.kind ?? 'workspace');
  const title = $derived(kind === 'drive' ? card.name || 'Drive file' : baseName(card.path));
  const where = $derived(
    kind === 'drive' ? 'Google Drive' : [folderOf(card.path), card.size !== null ? size(card.size) : ''].filter(Boolean).join(' · '),
  );

  async function save(): Promise<void> {
    try {
      if (kind === 'vault') {
        const note = await api.get('/v1/vault/note', { path: card.path });
        saveBlob(new Blob([note.content ?? ''], { type: 'text/markdown' }), baseName(card.path));
      } else {
        saveBlob(await download(`/v1/workspace/file?path=${encodeURIComponent(card.path)}&raw=true`), baseName(card.path));
      }
    } catch (error) {
      toast((error as Error).message, 'error');
    }
  }

  function see(): void {
    window.location.hash = libraryLink(kind === 'vault' ? 'note' : 'file', card.path);
  }

  function open(): void {
    if (card.url) window.open(card.url, '_blank', 'noopener,noreferrer');
  }
</script>

<div class="file-card present" class:gone={card.missing}>
  <div class="file-card-head">
    <span class="glyph"><Icon name={kind === 'drive' ? 'cloud' : kind === 'vault' ? 'book' : 'file'} size={18} /></span>
    <span class="info">
      <strong title={kind === 'drive' ? title : card.path}>{title}</strong>
      <span>
        {#if card.missing}No longer {kind === 'vault' ? 'in the vault' : kind === 'drive' ? 'available' : 'in the workspace'}
        {:else}{where}{/if}
        {#if card.origin && !card.missing}<span class="tag warn" title={`It came from outside: ${card.origin}`}>from outside</span>{/if}
      </span>
    </span>
    {#if !card.missing}
      <span class="actions">
        {#if kind === 'drive'}
          <button class="small" onclick={open}><Icon name="cloud" size={16} /> Open in Drive</button>
        {:else}
          <button class="small" onclick={see}><Icon name="folder" size={16} /> {kind === 'vault' ? 'See in the vault' : 'See in the workspace'}</button>
          <button class="small" onclick={save}><Icon name="download" size={16} /> Download</button>
        {/if}
      </span>
    {/if}
  </div>
</div>
