<script lang="ts">
  import { api } from '../lib/api';
  import { loadPending } from '../lib/live';
  import { session, toast } from '../lib/state.svelte';

  let { approval }: { approval: any } = $props();
  let answered = $state<string | null>(null);
  const status = $derived(answered ?? approval.status ?? 'pending');
  let busy = $state(false);

  async function answer(choice: string): Promise<void> {
    busy = true;
    try {
      const settled = await api.post(`/v1/approvals/${approval.id}/decide`, { answer: choice });
      answered = settled.status;
      loadPending();
    } catch (error) {
      toast((error as Error).message, 'error');
    } finally {
      busy = false;
    }
  }

  const answerable = $derived(session.approvals === 'all' || (session.approvals === 'safe' && approval.level !== 'destructive'));
</script>

<div class="approval" class:done={status !== 'pending'}>
  <div class="row">
    <strong>🔐 Clara asks</strong>
    <span class="tag">{approval.level}</span>
    <span class="tag">#{approval.id}</span>
    {#if status !== 'pending'}<span class="tag">{status}</span>{/if}
  </div>
  <div>{approval.summary}</div>
  {#if approval.reason}<div class="muted small">Why it asks: {approval.reason}</div>{/if}
  {#if status === 'pending'}
    {#if answerable}
      <div class="row">
        <button class="primary small" disabled={busy} onclick={() => answer('allow_once')}>Allow once</button>
        <button class="small" disabled={busy} onclick={() => answer('allow_always')}>Allow always</button>
        <button class="small" disabled={busy} onclick={() => answer('refuse_once')}>Refuse once</button>
        <button class="small danger" disabled={busy} onclick={() => answer('refuse_always')}>Refuse always</button>
      </div>
    {:else}
      <div class="muted small">This token may not answer {approval.level} requests.</div>
    {/if}
  {/if}
</div>

<style>
  .approval {
    border: 1px solid var(--accent);
    background: var(--accent-soft);
    border-radius: 8px;
    padding: 0.6rem 0.8rem;
    display: flex;
    flex-direction: column;
    gap: 0.4rem;
    margin: 0.4rem 0;
  }
  .approval.done {
    border-color: var(--line);
    background: var(--panel-2);
  }
</style>
