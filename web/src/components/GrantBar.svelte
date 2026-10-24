<script lang="ts">
  import { grants, stop } from '../lib/grants.svelte';
  import Icon from './Icon.svelte';

  const left = (seconds: number): string => (seconds >= 120 ? `${Math.round(seconds / 60)} min` : `${seconds} s`);
</script>

{#if grants.items.length}
  <div class="grant-bar" role="status">
    <Icon name="warn" size={18} />
    <p>
      <b>Approve all next is on.</b> In this conversation Clara runs these without asking, until you leave this page or press Stop:
      {#each grants.items as grant, g (grant.id)}{g ? ' · ' : ' '}<code>{grant.tool}</code> {grant.calls} of {grant.max_calls} calls, {left(grant.seconds_left)} left{/each}.
      Every call is still printed above.
    </p>
    <button class="small" onclick={stop}>Stop</button>
  </div>
{/if}
