<script lang="ts">
  import { api, download, saveBlob, size } from '../lib/api';
  import { markdown } from '../lib/markdown';
  import { toast } from '../lib/state.svelte';
  import Icon from './Icon.svelte';

  let { card }: { card: { path: string; action?: string; size?: number } } = $props();
  let open = $state(false);
  let content = $state('');

  async function show(): Promise<void> {
    if (open) {
      open = false;
      return;
    }
    try {
      content = (await api.get('/v1/workspace/file', { path: card.path })).content ?? '';
      open = true;
    } catch (error) {
      toast((error as Error).message, 'error');
    }
  }

  async function save(): Promise<void> {
    const blob = await download(`/v1/workspace/file?path=${encodeURIComponent(card.path)}&raw=true`);
    saveBlob(blob, card.path.split('/').pop() ?? 'note.md');
  }

  async function copy(): Promise<void> {
    const text = content || (await api.get('/v1/workspace/file', { path: card.path })).content;
    await navigator.clipboard.writeText(text);
    toast('Copied.', 'ok');
  }
</script>

<div class="file-card">
  <div class="file-card-head">
    <span class="glyph"><Icon name="file" size={18} /></span>
    <span class="info"><strong title={card.path}>{card.path}</strong>
      <span>{[card.action, card.size ? size(card.size) : ''].filter(Boolean).join(' · ') || 'a note'}</span></span>
    <button class="ghost small" aria-expanded={open} onclick={show}>{open ? 'Close' : 'Open'}</button>
    <button class="ghost icon-btn" title="Copy" aria-label="Copy" onclick={copy}><Icon name="copy" size={18} /></button>
    <button class="ghost icon-btn" title="Download" aria-label="Download" onclick={save}><Icon name="download" size={18} /></button>
  </div>
  {#if open}<div class="markdown file-body" use:markdown={content}></div>{/if}
</div>
