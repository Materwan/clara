<script lang="ts">
  // The model of a schedule or of a hook: the server's default unless one is picked. Both forms use it as it is.
  let { value = $bindable(), models }: { value: string; models: { models: { ref: string }[]; default: string } } = $props();
</script>

<label class="field">Model
  <span class="hint">The one set as default on the Models page unless you pick another.</span>
  <select bind:value>
    <option value="">Default model ({models.default || 'none'})</option>
    {#each models.models as model (model.ref)}<option value={model.ref}>{model.ref}</option>{/each}
  </select></label>
