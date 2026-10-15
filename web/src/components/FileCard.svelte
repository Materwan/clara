<script lang="ts">
  import { api, download, saveBlob, size } from '../lib/api';
  import { markdown } from '../lib/markdown';
  import { toast } from '../lib/state.svelte';

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

<div class="card">
  <div class="row">
    <span>📄</span>
    <strong class="grow">{card.path}</strong>
    {#if card.action}<span class="tag">{card.action}</span>{/if}
    {#if card.size}<span class="muted small">{size(card.size)}</span>{/if}
    <button class="small" onclick={show}>{open ? 'Close' : 'Open'}</button>
    <button class="small" onclick={copy}>Copy</button>
    <button class="small" onclick={save}>Download</button>
  </div>
  {#if open}<div class="markdown body" use:markdown={content}></div>{/if}
</div>

<style>
  .card {
    border: 1px solid var(--line);
    border-radius: 8px;
    padding: 0.5rem 0.7rem;
    margin: 0.4rem 0;
    background: var(--panel);
  }
  .body {
    margin-top: 0.6rem;
    border-top: 1px solid var(--line);
    padding-top: 0.6rem;
    max-height: 60vh;
    overflow: auto;
  }
</style>
