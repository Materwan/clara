<script lang="ts">
  import type { HTMLInputAttributes } from 'svelte/elements';

  // A checkbox drawn in the site's hand: a square that fills violet, with a tick drawn like the line icons. The input
  // itself is the box (appearance: none), so its label, keyboard and screen reader work as they do for any checkbox.
  let { checked = $bindable(false), ...rest }: Omit<HTMLInputAttributes, 'type'> = $props();
</script>

<span class="check"><input type="checkbox" bind:checked {...rest} /><svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12.5l4.5 4.5L19 7" pathLength="1" /></svg></span>
