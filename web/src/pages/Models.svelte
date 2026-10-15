<script lang="ts">
  import { api } from '../lib/api';
  import { toast } from '../lib/state.svelte';

  let providers = $state<any[]>([]);
  let catalogue = $state<any>({ models: [], problems: {}, default: '', upkeep: '', fallbacks: [] });
  let filter = $state('');
  let busy = $state(false);

  async function load(refresh = false): Promise<void> {
    busy = true;
    try {
      providers = (await api.get('/v1/admin/providers')).providers;
      catalogue = await api.get('/v1/admin/models', { refresh });
    } finally {
      busy = false;
    }
  }

  $effect(() => {
    load();
  });

  async function enable(provider: any): Promise<void> {
    try {
      await api.put(`/v1/admin/providers/${provider.id}`, { enabled: !provider.enabled });
      load();
    } catch (error) {
      toast((error as Error).message, 'error');
    }
  }

  async function check(provider: any): Promise<void> {
    const done = await api.post(`/v1/admin/providers/${provider.id}/check`);
    toast(done.ok ? `${provider.label}: OK` : `${provider.label}: ${done.problem}`, done.ok ? 'ok' : 'error');
  }

  async function show(model: any): Promise<void> {
    await api.patch('/v1/admin/models', { refs: [model.ref], shown: !model.shown });
    model.shown = !model.shown;
  }

  async function setDefault(ref: string): Promise<void> {
    try {
      await api.put('/v1/admin/models/default', { ref });
      load();
    } catch (error) {
      toast((error as Error).message, 'error');
    }
  }

  async function setUpkeep(ref: string): Promise<void> {
    await api.put('/v1/admin/models/upkeep', { ref });
    load();
  }

  async function setFallbacks(refs: string[]): Promise<void> {
    try {
      const done = await api.put('/v1/admin/models/fallbacks', { refs });
      catalogue.fallbacks = done.fallbacks;
    } catch (error) {
      toast((error as Error).message, 'error');
    }
  }

  function move(index: number, step: number): void {
    const refs = [...catalogue.fallbacks];
    const [item] = refs.splice(index, 1);
    refs.splice(index + step, 0, item);
    setFallbacks(refs);
  }

  const shown = $derived(catalogue.models.filter((model: any) => !filter || model.ref.toLowerCase().includes(filter.toLowerCase())));
</script>

<div class="page stack">
  <h1>Models</h1>
  <section class="panel">
    <h2>Providers</h2>
    <table>
      <thead><tr><th>Provider</th><th>Where</th><th>Key</th><th></th></tr></thead>
      <tbody>
        {#each providers as provider (provider.id)}
          <tr><td><strong>{provider.label}</strong> <code class="small">{provider.id}</code></td><td class="small">{provider.local ? '🏠 local' : '☁ hosted'} · {provider.host}</td>
            <td class="small">{provider.needs_key ? (provider.key_set ? 'set' : 'missing (clara secrets set …)') : '—'}</td>
            <td class="row"><button class="small" onclick={() => enable(provider)}>{provider.enabled ? 'Disable' : 'Enable'}</button>
              <button class="small" onclick={() => check(provider)}>Check</button>
              {#if catalogue.problems[provider.id]}<span class="bad small">{catalogue.problems[provider.id]}</span>{/if}</td></tr>
        {/each}
      </tbody>
    </table>
  </section>
  <section class="panel stack">
    <div class="row"><span>Default model: <strong>{catalogue.default || '(none)'}</strong></span>
      <span>· Side jobs (titles, summaries): <strong>{catalogue.upkeep || 'the default'}</strong> {#if catalogue.upkeep}<button class="small" onclick={() => setUpkeep('')}>Reset</button>{/if}</span></div>
    <div>
      <strong>Fallbacks</strong> <span class="muted small">(asked in turn when a model fails before it said anything; a local model never falls back to a hosted one)</span>
      {#each catalogue.fallbacks as ref, index (ref)}
        <div class="row small">{index + 1}. {ref}
          <button class="small" disabled={index === 0} onclick={() => move(index, -1)}>↑</button>
          <button class="small" disabled={index === catalogue.fallbacks.length - 1} onclick={() => move(index, 1)}>↓</button>
          <button class="small danger" onclick={() => setFallbacks(catalogue.fallbacks.filter((item: string) => item !== ref))}>✕</button></div>
      {:else}<div class="muted small">None.</div>{/each}
    </div>
  </section>
  <section class="panel">
    <div class="row"><h2 class="grow">Catalogue</h2><input placeholder="Filter" bind:value={filter} /><button disabled={busy} onclick={() => load(true)}>Refresh</button></div>
    <table>
      <thead><tr><th>Model</th><th>Window</th><th>Can</th><th>In pickers</th><th></th></tr></thead>
      <tbody>
        {#each shown as model (model.ref)}
          <tr><td>{model.ref === catalogue.default ? '⭐ ' : ''}{model.ref}{model.listed ? '' : ' (not listed now)'}</td>
            <td class="small">{model.window.toLocaleString()}</td>
            <td class="small">{[model.capabilities.tools ? 'tools' : '', model.capabilities.vision ? 'pictures' : '', model.capabilities.thinking ? 'thinking' : ''].filter(Boolean).join(', ') || '?'}</td>
            <td><input type="checkbox" checked={model.shown} onchange={() => show(model)} /></td>
            <td class="row"><button class="small" onclick={() => setDefault(model.ref)}>Default</button>
              <button class="small" onclick={() => setUpkeep(model.ref)}>Side jobs</button>
              <button class="small" disabled={catalogue.fallbacks.includes(model.ref)} onclick={() => setFallbacks([...catalogue.fallbacks, model.ref])}>+ Fallback</button></td></tr>
        {:else}<tr><td colspan="5" class="empty">No model: enable a provider and refresh.</td></tr>{/each}
      </tbody>
    </table>
  </section>
</div>
