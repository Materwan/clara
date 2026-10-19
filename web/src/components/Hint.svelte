<script lang="ts">
  // A sentence from the server that may name a setting (CLARA_VAULT_PATH, CLARA_RUN=off): the plain words come first, and the
  // name is set in code style, so that whoever has to change it can find it and everyone else can read past it.
  let { text }: { text: string } = $props();
  const parts = $derived(text.split(/(CLARA_[A-Z0-9_]+(?:=\w+)?)/).filter(Boolean));
</script>

{#each parts as part}{#if /^CLARA_/.test(part)}<code>{part}</code>{:else}{part}{/if}{/each}
