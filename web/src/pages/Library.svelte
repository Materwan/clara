<script lang="ts">
  import Segmented from '../components/Segmented.svelte';
  import Vault from '../components/Vault.svelte';
  import Workspace from '../components/Workspace.svelte';
  import { readRoute, route } from '../lib/state.svelte';

  // The files Clara keeps for you, one view for each place they live: her workspace (a git folder she writes in) and your
  // Obsidian vault (her second brain). Another place is one more entry in VIEWS and one more block below.
  type View = 'workspace' | 'vault';
  const VIEWS = [{ value: 'workspace' as View, label: 'Workspace', icon: 'file' }, { value: 'vault' as View, label: 'Vault', icon: 'book' }];
  const viewOf = (): View => VIEWS.find((item) => item.value === route.query.get('view'))?.value ?? 'workspace';
  let view = $state<View>(viewOf());
  $effect(() => {
    view = viewOf(); // a link to #/library?view=vault, or back to #/library
  });
  // the view is in the address, so a reload or a link comes back to it (and the file or note a link asked for is let go of)
  const viewChanged = (next: View): void => {
    window.history.replaceState(null, '', next === 'workspace' ? '#/library' : `#/library?view=${next}`);
    readRoute();
  };
</script>

<div class="page stack">
  <div><Segmented label="Where" options={VIEWS} bind:value={view} onchange={viewChanged} /></div>
  {#if view === 'vault'}
    <Vault />
  {:else}
    <Workspace />
  {/if}
</div>
