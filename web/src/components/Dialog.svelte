<script lang="ts">
  import { tick } from 'svelte';
  import { dialog, settle } from '../lib/dialog.svelte';

  // The one dialog of the site (see lib/dialog.svelte.ts). A native <dialog> opened with showModal(): the page behind is inert,
  // Tab stays inside and Escape cancels, with nothing to build here. A click on the backdrop does not dismiss it: a field
  // half typed (a secret) must not be lost to a stray click.
  let box = $state<HTMLDialogElement>();
  let field = $state<HTMLInputElement>();
  let value = $state('');
  const request = $derived(dialog.current);

  $effect(() => {
    if (!box) return;
    if (request) {
      value = request.value;
      if (!box.open) box.showModal();
      tick().then(() => field?.select());
    } else if (box.open) {
      box.close();
    }
  });

  function cancel(): void {
    if (dialog.current) settle(dialog.current.kind === 'confirm' ? false : null);
  }

  function submit(event: SubmitEvent): void {
    event.preventDefault();
    if (dialog.current) settle(dialog.current.kind === 'confirm' ? true : value);
  }
</script>

<!-- Escape closes a modal dialog by itself, and fires close: that is a cancel -->
<dialog bind:this={box} class="dialog frame" role={request?.kind === 'confirm' ? 'alertdialog' : undefined} aria-labelledby="dialog-title"
  aria-describedby="dialog-message" data-danger={request?.danger ? '' : undefined} onclose={cancel}>
  {#if request}
    <form onsubmit={submit}>
      <h2 id="dialog-title">{request.title || (request.kind === 'confirm' ? 'Please confirm' : 'Enter a value')}</h2>
      <p id="dialog-message">{request.message}</p>
      {#if request.kind === 'prompt'}
        <input bind:this={field} bind:value type={request.type} aria-label={request.label} aria-describedby="dialog-message" autocomplete="off" />
      {/if}
      <div class="actions">
        <button type="button" onclick={cancel}>Cancel</button>
        <button type="submit" class={request.danger ? 'danger' : 'primary'}>{request.confirm}</button>
      </div>
    </form>
  {/if}
</dialog>
