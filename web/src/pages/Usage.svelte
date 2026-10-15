<script lang="ts">
  import { api, when } from '../lib/api';

  let period = $state('30d');
  let group = $state('day');
  let stats = $state<any>({ rows: [], totals: {} });
  let calls = $state<any[]>([]);

  async function load(): Promise<void> {
    stats = await api.get('/v1/admin/usage', { period, group });
    calls = (await api.get('/v1/admin/usage/history', { limit: 100 })).calls;
  }

  $effect(() => {
    void period;
    void group;
    load();
  });

  const most = $derived(Math.max(1, ...stats.rows.map((row: any) => row.prompt + row.completion)));
</script>

<div class="page stack">
  <h1>Usage</h1>
  <p class="muted small">Token counts of every model call. No text is kept here, and there are no limits or credits.</p>
  <div class="row">
    <select bind:value={period}>{#each ['24h', '7d', '30d', '90d', '365d', 'all'] as name}<option>{name}</option>{/each}</select>
    <select bind:value={group}>{#each ['day', 'model', 'kind', 'provider', 'client'] as name}<option>{name}</option>{/each}</select>
    <span class="muted small">{stats.totals.calls ?? 0} calls · {(stats.totals.prompt ?? 0).toLocaleString()} in · {(stats.totals.completion ?? 0).toLocaleString()} out · {(stats.totals.cached ?? 0).toLocaleString()} cached</span>
  </div>
  <table>
    <thead><tr><th>{group}</th><th>Calls</th><th>In</th><th>Out</th><th>Cached</th><th class="wide"></th></tr></thead>
    <tbody>
      {#each stats.rows as row (row.key)}
        <tr><td>{row.key}</td><td>{row.calls}{row.failed ? ` (${row.failed} failed)` : ''}</td><td>{row.prompt.toLocaleString()}</td>
          <td>{row.completion.toLocaleString()}</td><td>{row.cached.toLocaleString()}</td>
          <td><span class="bar" style:width={`${(100 * (row.prompt + row.completion)) / most}%`}></span></td></tr>
      {:else}<tr><td colspan="6" class="empty">No call in this period.</td></tr>{/each}
    </tbody>
  </table>
  <h2>Last calls</h2>
  <table>
    <thead><tr><th>When</th><th>Kind</th><th>Model</th><th>In / out</th><th>Took</th><th>Outcome</th></tr></thead>
    <tbody>
      {#each calls as call (call.id)}
        <tr><td class="small">{when(call.at)}</td><td>{call.kind}</td><td class="small">{call.provider}:{call.model}</td>
          <td class="small">{call.prompt_tokens}{call.estimated ? '~' : ''} / {call.completion_tokens}</td><td class="small">{(call.duration_ms / 1000).toFixed(1)} s</td>
          <td class:bad={call.outcome !== 'ok'}>{call.outcome}</td></tr>
      {/each}
    </tbody>
  </table>
</div>

<style>
  .wide {
    width: 30%;
  }
  .bar {
    display: block;
    height: 8px;
    border-radius: 4px;
    background: var(--accent);
  }
</style>
