<script lang="ts" generics="T extends string">
  import Icon from './Icon.svelte';

  // One answer out of a few, all of them in view: a group of native radio buttons (so the arrow keys, the label and the screen
  // reader work as they do for any radio), drawn as a segmented control, or as cards when each answer has a sentence of its own.
  // `hint` on an option is what choosing it means; in the segmented form the chosen one's hint is written under the group.
  type Option = { value: T; label: string; hint?: string; icon?: string; tag?: string; disabled?: boolean };
  let { options, value = $bindable(), legend, help, cards = false, disabled = false, onchange }: {
    options: Option[]; value: T; legend: string; help?: string; cards?: boolean; disabled?: boolean; onchange?: (value: T) => void;
  } = $props();

  const name = `choice-${Math.random().toString(36).slice(2, 8)}`; // the radios of one group share a name; two groups never do
  const chosen = $derived(options.find((option) => option.value === value));
  const describedBy = `${name}-hint`;

  function pick(next: T): void {
    value = next;
    onchange?.(next);
  }
</script>

<fieldset class="choice" class:cards {disabled} aria-describedby={!cards && chosen?.hint ? describedBy : undefined}>
  <legend>{legend}</legend>
  {#if help}<p class="choice-help">{help}</p>{/if}
  <div class="choice-options">
    {#each options as option (option.value)}
      <label class="choice-option" class:off={option.disabled}>
        <input type="radio" {name} value={option.value} checked={value === option.value} disabled={option.disabled}
          onchange={() => pick(option.value)} />
        {#if cards}
          <span class="choice-text">
            <span class="choice-title">{#if option.icon}<Icon name={option.icon} size={16} />{/if}{option.label}
              {#if option.tag}<span class="tag warn"><Icon name="warn" size={14} />{option.tag}</span>{/if}</span>
            {#if option.hint}<span class="choice-hint">{option.hint}</span>{/if}
          </span>
        {:else}
          {#if option.icon}<Icon name={option.icon} size={16} />{/if}{option.label}
        {/if}
      </label>
    {/each}
  </div>
  {#if !cards && chosen?.hint}<p class="choice-hint" id={describedBy}>{chosen.hint}</p>{/if}
</fieldset>
