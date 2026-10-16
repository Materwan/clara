<script lang="ts">
  import { api } from '../lib/api';
  import { loadPending } from '../lib/live';
  import { session, toast } from '../lib/state.svelte';
  import Icon from './Icon.svelte';

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

<div class="approval" class:settled={status !== 'pending'}>
  <div class="approval-head">
    <Icon name="shield" size={18} /><span>Clara asks</span>
    <span class="tags"><span class="tag" class:danger={approval.level === 'destructive'}>{approval.level}</span><span class="tag">#{approval.id}</span>
      {#if status !== 'pending'}<span class="tag" class:ok={status === 'approved' || status === 'done'} class:danger={status === 'denied' || status === 'failed'}>{status}</span>{/if}</span>
  </div>
  <p class="approval-summary">{approval.summary}</p>
  {#if approval.reason}<p class="approval-reason">Why it asks: {approval.reason}</p>{/if}
  {#if status === 'pending'}
    {#if answerable}
      <div class="row">
        <button class="primary small" disabled={busy} onclick={() => answer('allow_once')}>Allow once</button>
        <button class="small" disabled={busy} onclick={() => answer('allow_always')}>Allow always</button>
        <button class="small" disabled={busy} onclick={() => answer('refuse_once')}>Refuse once</button>
        <button class="small danger" disabled={busy} onclick={() => answer('refuse_always')}>Refuse always</button>
      </div>
    {:else}
      <p class="muted small">This token may not answer {approval.level} requests.</p>
    {/if}
  {/if}
</div>
