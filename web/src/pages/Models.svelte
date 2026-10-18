<script lang="ts">
  import Checkbox from '../components/Checkbox.svelte';
  import Icon from '../components/Icon.svelte';
  import Segmented from '../components/Segmented.svelte';
  import { api } from '../lib/api';
  import { toast } from '../lib/state.svelte';

  let providers = $state<any[]>([]);
  let catalogue = $state<any>({ models: [], problems: {}, default: '', upkeep: '', fallbacks: [] });
  let filter = $state('');
  let view = $state<'works' | 'all' | 'broken'>('works');
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
    try {
      await api.patch('/v1/admin/models', { refs: [model.ref], shown: !model.shown });
      model.shown = !model.shown;
    } catch (error) {
      toast((error as Error).message, 'error');
      load();
    }
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

  // A model that does not work (OpenCode lists models of APIs Clara does not speak) is left out of the default view.
  const broken = (model: any): boolean => model.fit?.works === false;
  const brokenCount = $derived(catalogue.models.filter(broken).length);
  const inView = (model: any): boolean => view === 'all' || (view === 'broken') === broken(model);
  const shown = $derived(catalogue.models.filter((model: any) => inView(model)
    && (!filter || model.ref.toLowerCase().includes(filter.toLowerCase()))));

  function can(model: any): string {
    const caps = model.capabilities;
    return [caps.tools ? 'tools' : '', caps.vision ? 'pictures' : '', caps.thinking ? 'thinking' : ''].filter(Boolean).join(', ') || '?';
  }
</script>

<div class="page stack">
  <section class="panel">
    <h2>Providers</h2>
    <table>
      <thead><tr><th>Provider</th><th>Where</th><th>Key</th><th></th></tr></thead>
      <tbody>
        {#each providers as provider (provider.id)}
          <tr><td><strong>{provider.label}</strong> <code class="small">{provider.id}</code></td><td class="small"><Icon name={provider.local ? 'home' : 'cloud'} size={16} /> {provider.local ? 'local' : 'hosted'} · {provider.host}</td>
            <td class="small">{provider.needs_key ? (provider.key_set ? 'set' : 'missing (clara secrets set …)') : '—'}</td>
            <td class="row"><button class="small" onclick={() => enable(provider)}>{provider.enabled ? 'Disable' : 'Enable'}</button>
              <button class="small" onclick={() => check(provider)}>Check</button>
              {#if catalogue.problems[provider.id]}<span class="danger small">{catalogue.problems[provider.id]}</span>{/if}</td></tr>
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
          <button class="ghost icon-btn" aria-label={`Move ${ref} up`} disabled={index === 0} onclick={() => move(index, -1)}><Icon name="up" size={16} /></button>
          <button class="ghost icon-btn" aria-label={`Move ${ref} down`} disabled={index === catalogue.fallbacks.length - 1} onclick={() => move(index, 1)}><Icon name="down" size={16} /></button>
          <button class="ghost icon-btn" aria-label={`Remove ${ref}`} onclick={() => setFallbacks(catalogue.fallbacks.filter((item: string) => item !== ref))}><Icon name="close" size={16} /></button></div>
      {:else}<div class="muted small">None.</div>{/each}
    </div>
  </section>
  <section class="panel">
    <div class="row"><h2 class="grow">Catalogue</h2>
      <Segmented label="Show models that" bind:value={view} options={[
        { value: 'works', label: 'Work with Clara', title: 'Hides the models Clara cannot use (they speak an API it does not)' },
        { value: 'all', label: 'All' },
        { value: 'broken', label: "Don't work", count: brokenCount },
      ]} />
      <input type="search" aria-label="Filter models" placeholder="Filter" bind:value={filter} /><button aria-busy={busy} onclick={() => !busy && load(true)}><Icon name="refresh" size={16} />Refresh</button></div>
    <table>
      <thead><tr><th>Model</th><th>Window</th><th>Can</th><th>In pickers</th><th></th></tr></thead>
      <tbody>
        {#each shown as model (model.ref)}
          <tr><td>{#if model.ref === catalogue.default}<Icon name="star" size={16} label="Default model" /> {/if}{model.ref}{model.listed ? '' : ' (not listed now)'}</td>
            <td class="small">{model.window.toLocaleString()}</td>
            <td class="small">{#if broken(model)}<span class="tag danger">doesn't work</span> {model.fit.why}{:else}{can(model)}{#if model.fit?.works === null} <span class="tag warn" title={model.fit.why}>unverified</span>{/if}{/if}</td>
            <td><Checkbox aria-label={`Show ${model.ref} in pickers`} checked={model.shown} disabled={broken(model)} onchange={() => show(model)} /></td>
            <td class="row"><button class="small" disabled={broken(model)} onclick={() => setDefault(model.ref)}>Default</button>
              <button class="small" disabled={broken(model)} onclick={() => setUpkeep(model.ref)}>Side jobs</button>
              <button class="small" disabled={broken(model) || catalogue.fallbacks.includes(model.ref)} onclick={() => setFallbacks([...catalogue.fallbacks, model.ref])}>+ Fallback</button></td></tr>
        {:else}<tr><td colspan="5" class="empty">{catalogue.models.length ? 'No model matches: try another view or filter.' : 'No model: enable a provider and refresh.'}</td></tr>{/each}
      </tbody>
    </table>
  </section>
</div>
