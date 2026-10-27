<script lang="ts">
  import { api } from '../lib/api';
  import { press } from '../lib/grants.svelte';
  import { loadPending } from '../lib/live';
  import { live, session, toast } from '../lib/state.svelte';
  import Icon from './Icon.svelte';

  // allowGrant: only the chat that shows the request can offer « Approve all next » (it keeps its banner and its beat)
  let { approval, allowGrant = false }: { approval: any; allowGrant?: boolean } = $props();
  let answered = $state<string | null>(null);
  // What the server says about it now: this card is drawn once, but the request may be answered from another client
  // (the desktop island, Discord, another tab), which the event stream tells (live.settled).
  const verdict = $derived(live.settled[approval.id] ?? approval);
  const status = $derived(answered ?? verdict.status ?? 'pending');
  const elsewhere = $derived(answered === null && status !== 'pending' && verdict.decided_by ? verdict : null);
  const WORDS: Record<string, string> = { allow_once: 'Allowed', allow_always: 'Always allowed', allow_conversation: 'Allowed for this conversation', refuse_once: 'Refused', refuse_always: 'Always refused' };
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

  async function approveAll(): Promise<void> {
    busy = true;
    try {
      answered = (await press(approval.id, approval.conversation)).status;
      loadPending();
    } catch (error) {
      toast((error as Error).message, 'error');
    } finally {
      busy = false;
    }
  }

  const grantable = $derived(allowGrant && approval.grantable && !!approval.conversation);
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
  {#if elsewhere}<p class="muted small">{WORDS[elsewhere.answer] ?? 'Answered'} from {elsewhere.decided_by}.</p>{/if}
  {#if status === 'pending'}
    {#if answerable}
      <div class="row">
        <button class="primary small" disabled={busy} onclick={() => answer('allow_once')}>Allow once</button>
        {#if grantable}<button class="small" disabled={busy} onclick={approveAll}
          title="Allow this and the next {approval.tool} requests of this conversation without asking, until you leave this page, press Stop, or a limit set in Admin > Settings is reached">Approve all next</button>{/if}
        {#if approval.class === 'folder_sensitive'}<button class="small" disabled={busy} onclick={() => answer('allow_conversation')}
          title="Allow Clara to read this file again for the rest of this conversation (other files are asked for again)">Allow for this conversation</button>{/if}
        <button class="small" disabled={busy} onclick={() => answer('allow_always')}>Allow always</button>
        <button class="small" disabled={busy} onclick={() => answer('refuse_once')}>Refuse once</button>
        <button class="small danger" disabled={busy} onclick={() => answer('refuse_always')}>Refuse always</button>
      </div>
    {:else}
      <p class="muted small">This token may not answer {approval.level} requests.</p>
    {/if}
  {/if}
</div>
