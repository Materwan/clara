<script lang="ts">
  import { ICONS } from '../lib/icons';

  // `label` makes the icon announced; without one it is decorative.
  let { name, size = 20, label = '' }: { name: string; size?: number; label?: string } = $props();
  const strokes = $derived(ICONS[name] ?? []);
</script>

<svg class="icon" viewBox="0 0 24 24" width={size} height={size} role={label ? 'img' : undefined} aria-label={label || undefined}
  aria-hidden={label ? undefined : 'true'}>
  {#each strokes as stroke, index (index)}
    {#if stroke.startsWith('fill:')}<path class="solid" d={stroke.slice(5)} />{:else}<path d={stroke} />{/if}
  {/each}
</svg>
