<script lang="ts">
  import Mark from '../components/Mark.svelte';
  import ThemeSwitch from '../components/ThemeSwitch.svelte';
  import { api } from '../lib/api';

  let { onSignedIn }: { onSignedIn: () => void } = $props();
  let token = $state('');
  let problem = $state('');
  let busy = $state(false);

  async function signIn(event: SubmitEvent): Promise<void> {
    event.preventDefault();
    if (busy) return;
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

<div class="login-wrap">
  <aside class="login-art">
    <Mark size={36} />
    <span class="wordmark">Clara</span>
    <p class="tagline">Your conversations, your notes, and what she remembers about you, on your own machine.</p>
  </aside>
  <main class="login">
    <h1>Sign in to Clara</h1>
    <p class="lede">Paste a web token once. This browser then keeps a cookie that stands for it; revoking the token signs it out.</p>
    <form onsubmit={signIn}>
      <label class="field">Web token
        <input type="password" bind:value={token} placeholder="clara_…" autocomplete="off" required />
      </label>
      {#if problem}<p class="danger small" role="alert">{problem}</p>{/if}
      <button class="primary large" disabled={!token.trim()} aria-busy={busy}>{busy ? 'Signing in…' : 'Sign in'}</button>
    </form>
    <p class="foot">No token? On the server: <code>clara token create web web</code>.</p>
    <ThemeSwitch />
  </main>
</div>
