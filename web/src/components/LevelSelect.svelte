<script lang="ts">
  import { confirmDialog } from '../lib/dialog.svelte';
  import { TOP_WARNING, ladder, loadLadder, label as describe } from '../lib/levels.svelte';

  // The auto level of a conversation, a project, a schedule or a hook: how much its tools may do without asking. `value` is its
  // own level (null: it follows `follows`, the level it runs at then is `effective`). Choosing the last level asks first: it is
  // the one that suspends the raises. `onchange` hears the choice once it is confirmed.
  let { value, effective, follows, label = 'Auto level', onchange, small = false }: {
    value: number | null; effective: number; follows: string; label?: string; onchange: (value: number | null) => void; small?: boolean;
  } = $props();
  $effect(() => {
    loadLadder().catch(() => undefined);
  });

  async function pick(event: Event): Promise<void> {
    const select = event.target as HTMLSelectElement;
    const next = select.value === '' ? null : Number(select.value);
    if (next === ladder.top && value !== ladder.top) {
      const yes = await confirmDialog({ title: `Level ${describe(next)}`, message: TOP_WARNING, confirm: 'Use this level', danger: true });
      if (!yes) {
        select.value = value === null ? '' : String(value);
        return;
      }
    }
    onchange(next);
  }
</script>

<select class="level-select" class:small aria-label={label} title="How much Clara may do without asking in this conversation" value={value === null ? '' : String(value)} onchange={pick}>
  <option value="">Follows {follows} ({ladder.loaded ? describe(effective) : effective})</option>
  {#each ladder.levels as level (level.position)}<option value={String(level.position)}>{level.position} · {level.name}</option>{/each}
</select>
