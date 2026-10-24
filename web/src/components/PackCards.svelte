<script lang="ts">
  import Checkbox from './Checkbox.svelte';
  import Icon from './Icon.svelte';

  // The special tools (packs) as cards, any number of them on: the cards of Choice.svelte, with a checkbox in place of its radio. A tool
  // this server cannot give yet says why; it can still be ticked (it stays hidden from Clara until it can be used).
  type Pack = { id: string; label: string; description: string; icon: string; available: boolean; reason: string };
  let { packs, value = $bindable(), legend, help }: { packs: Pack[]; value: string[]; legend: string; help?: string } = $props();

  const toggle = (id: string): void => {
    value = value.includes(id) ? value.filter((item) => item !== id) : [...value, id];
  };
</script>

<fieldset class="choice cards pack-cards">
  <legend>{legend}</legend>
  {#if help}<p class="choice-help">{help}</p>{/if}
  <div class="choice-options">
    {#each packs as pack (pack.id)}
      <label class="choice-option">
        <Checkbox checked={value.includes(pack.id)} onchange={() => toggle(pack.id)} />
        <span class="choice-text">
          <span class="choice-title"><Icon name={pack.icon} size={16} />{pack.label}
            {#if !pack.available}<span class="tag warn"><Icon name="warn" size={14} />Not available yet</span>{/if}</span>
          <span class="choice-hint">{pack.description}</span>
          {#if !pack.available}<span class="choice-hint">{pack.reason}</span>{/if}
        </span>
      </label>
    {/each}
  </div>
</fieldset>
