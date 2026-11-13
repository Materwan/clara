<script lang="ts">
  import { loadModes, modeList } from '../lib/modeList.svelte';

  // The mode of a conversation, a project, a schedule or a hook: what its tools may do without asking, and which special tools
  // it switches on. `value` is its own mode (null: it follows `follows`, and `effective` is the mode it runs in then). The
  // default mode is never stored by name: following it is the choice. `onchange` hears the pick.
  let { value, effective, follows, label = 'Mode', onchange, small = false, disabled = false }: {
    value: string | null; effective: string; follows: string; label?: string; onchange: (value: string | null) => void; small?: boolean; disabled?: boolean;
  } = $props();
  $effect(() => {
    loadModes().catch(() => undefined);
  });
  // a mode that was deleted meanwhile still shows as the one picked
  const names = $derived(value && !modeList.names.includes(value) ? [...modeList.names, value] : modeList.names);
</script>

<select class="mode-select" class:small aria-label={label} title="What Clara may do without asking, and the special tools switched on" value={value ?? ''} {disabled}
  onchange={(event) => onchange((event.target as HTMLSelectElement).value || null)}>
  <option value="">Follows {follows} ({effective})</option>
  {#each names as name (name)}<option value={name}>{name}</option>{/each}
</select>
