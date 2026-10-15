<script lang="ts">
  import { api } from '../lib/api';

  let { onSignedIn }: { onSignedIn: () => void } = $props();
  let token = $state('');
  let problem = $state('');
  let busy = $state(false);

  async function signIn(event: SubmitEvent): Promise<void> {
    event.preventDefault();
    busy = true;
    problem = '';
    try {
      await api.post('/v1/auth/web-login', { token: token.trim() });
      token = '';
      onSignedIn();
    } catch (error) {
      problem = (error as Error).message;
    } finally {
      busy = false;
    }
  }
</script>

<div class="center">
  <form class="panel stack" onsubmit={signIn}>
    <h1>Clara</h1>
    <p class="muted small">
      Paste a client token once (make one with <code>clara token create web web</code>). The browser keeps a cookie that
      stands for it; revoking the token signs this browser out.
    </p>
    <input type="password" bind:value={token} placeholder="clara_…" autocomplete="off" required />
    {#if problem}<p class="bad small">{problem}</p>{/if}
    <button class="primary" disabled={busy || !token.trim()}>Sign in</button>
  </form>
</div>

<style>
  .center {
    min-height: 100vh;
    display: grid;
    place-items: center;
    padding: 1rem;
  }
  form {
    width: min(420px, 100%);
    display: flex;
    flex-direction: column;
  }
</style>
