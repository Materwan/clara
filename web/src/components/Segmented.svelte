<script lang="ts" generics="T extends string">
  import Icon from './Icon.svelte';

  // A few choices side by side, one of them on: the views of a page, a filter, the theme. A group of toggle buttons (aria-pressed),
  // named by `label`. `quiet` is the small, low variant. `count` shows a badge when it is above zero. `onchange` hears a pick
  // (a change of `value` from outside does not call it).
  type Option = { value: T; label: string; icon?: string; title?: string; count?: number };
  let { options, value = $bindable(), label, quiet = false, onchange }: {
    options: Option[]; value: T; label: string; quiet?: boolean; onchange?: (value: T) => void;
  } = $props();

  function pick(next: T): void {
    value = next;
    onchange?.(next);
  }
</script>

<div class="segmented" class:quiet role="group" aria-label={label}>
  {#each options as option (option.value)}
    <button type="button" aria-pressed={value === option.value} title={option.title} onclick={() => pick(option.value)}>
      {#if option.icon}<Icon name={option.icon} size={16} />{/if}{option.label}
      {#if option.count}<span class="count">{option.count}</span>{/if}
    </button>
  {/each}
</div>
