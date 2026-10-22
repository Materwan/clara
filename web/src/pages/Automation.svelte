<script lang="ts">
  import Hooks from '../components/Hooks.svelte';
  import Schedules from '../components/Schedules.svelte';
  import Segmented from '../components/Segmented.svelte';
  import { route } from '../lib/state.svelte';

  // Everything Clara does without being asked, one view for each way she is set off: at a time (schedules) or when something
  // happens (hooks). Another kind of automation is one more entry in VIEWS and one more block below.
  type View = 'schedules' | 'hooks';
  const VIEWS = [{ value: 'schedules' as View, label: 'Schedules', icon: 'clock' }, { value: 'hooks' as View, label: 'Hooks', icon: 'bolt' }];
  const viewOf = (): View => VIEWS.find((item) => item.value === route.query.get('view'))?.value ?? 'schedules';
  let view = $state<View>(viewOf());
  $effect(() => {
    view = viewOf(); // a link to #/automation?view=hooks, or back to #/automation
  });
  // the view is in the address, so a reload or a link comes back to it
  const viewChanged = (next: View): void => window.history.replaceState(null, '', next === 'schedules' ? '#/automation' : `#/automation?view=${next}`);
</script>

<div class="page stack">
  <div><Segmented label="Kind of automation" options={VIEWS} bind:value={view} onchange={viewChanged} /></div>
  {#if view === 'hooks'}
    <Hooks />
  {:else}
    <Schedules />
  {/if}
</div>
