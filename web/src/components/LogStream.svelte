<script lang="ts">
  import { tick } from 'svelte';
  import Checkbox from './Checkbox.svelte';
  import Icon from './Icon.svelte';
  import Segmented from './Segmented.svelte';
  import { stream } from '../lib/api';
  import { session } from '../lib/state.svelte';

  // The live log (Admin, Logs): the server's own log and its traffic records as they happen. The server keeps the last
  // 500 lines in memory and filters by source and level; a new connection (or a change of filter) replays them, so the
  // list is rebuilt from scratch each time. Pause holds new lines aside until you resume; Follow keeps the newest line
  // in view and switches itself off when you scroll up.

  type Source = 'all' | 'app' | 'traffic';
  type Level = 'INFO' | 'WARNING' | 'ERROR';
  type Entry = { id: number; ts: string; source: 'app' | 'traffic'; level: Level; name: string; text: string };

  const MAX = 1000;
  const SOURCES: { value: Source; label: string }[] = [{ value: 'all', label: 'All' }, { value: 'app', label: 'Server' }, { value: 'traffic', label: 'Traffic' }];
  const LEVELS: { value: Level; label: string }[] = [{ value: 'INFO', label: 'Info' }, { value: 'WARNING', label: 'Warnings' }, { value: 'ERROR', label: 'Errors' }];

  let source = $state<Source>('all');
  let level = $state<Level>('INFO');
  let lines = $state.raw<Entry[]>([]);
  let held = $state(0);
  let paused = $state(false);
  let follow = $state(true);
  let connected = $state(false);
  let failure = $state('');
  let box = $state<HTMLElement | null>(null);

  let pending: Entry[] = [];
  let aside: Entry[] = [];
  let timer: ReturnType<typeof setTimeout> | undefined;

  function take(entry: Entry): void {
    if (paused) {
      aside.push(entry);
      if (aside.length > MAX) aside.shift();
      held = aside.length;
      return;
    }
    pending.push(entry);
    if (pending.length > MAX) pending.shift();
    timer ??= setTimeout(flush, 100); // lines arrive in bursts (the replay): one redraw for each burst
  }

  function flush(): void {
    timer = undefined;
    if (!pending.length) return;
    lines = [...lines, ...pending].slice(-MAX);
    pending = [];
  }

  function resume(): void {
    paused = false;
    pending = [...pending, ...aside];
    aside = [];
    held = 0;
    flush();
  }

  function clear(): void {
    pending = [];
    aside = [];
    held = 0;
    lines = [];
  }

  async function run(chosen: { source: Source; level: Level }, signal: AbortSignal): Promise<void> {
    let wait = 1000;
    while (!signal.aborted && session.signedIn) {
      try {
        for await (const event of stream('GET', `/v1/admin/logs/stream?source=${chosen.source}&level=${chosen.level}`, undefined, signal)) {
          if (event.type === 'open') {
            connected = true;
            failure = '';
            wait = 1000;
            clear();
          } else if (event.type === 'log') {
            take(event);
          }
        }
      } catch (error) {
        if (signal.aborted) return;
        failure = (error as Error).message;
      }
      connected = false;
      await new Promise((resolve) => setTimeout(resolve, wait));
      wait = Math.min(wait * 2, 30_000);
    }
  }

  $effect(() => {
    const controller = new AbortController();
    const chosen = { source, level };
    clear();
    void run(chosen, controller.signal);
    return () => {
      controller.abort();
      clearTimeout(timer);
      timer = undefined;
      connected = false;
    };
  });

  $effect(() => {
    void lines;
    if (follow && box) tick().then(() => box && (box.scrollTop = box.scrollHeight));
  });

  function scrolled(): void {
    if (box) follow = box.scrollHeight - box.scrollTop - box.clientHeight < 24;
  }

  function time(iso: string): string {
    const date = new Date(iso);
    return date.toLocaleTimeString([], { hour12: false }) + '.' + String(date.getMilliseconds()).padStart(3, '0');
  }
</script>

<div class="bar row">
  <Segmented label="Source" options={SOURCES} bind:value={source} />
  <Segmented label="Level (and above)" options={LEVELS} bind:value={level} />
  <span class="grow"></span>
  <label class="row follow small"><Checkbox bind:checked={follow} /> Follow</label>
  <button class="small" onclick={() => (paused ? resume() : (paused = true))}>
    <Icon name={paused ? 'play' : 'pause'} size={16} />{paused ? `Resume${held ? ` (${held})` : ''}` : 'Pause'}
  </button>
  <button class="small" onclick={clear}><Icon name="trash" size={16} />Clear</button>
</div>

<p class="muted small status" role="status">
  {#if connected}Live · the last 500 lines are kept in memory, nothing is written to disk. Secrets are masked; sign-in addresses and debug content never appear.
  {:else if failure}Disconnected ({failure}), trying again…
  {:else}Connecting…{/if}
</p>

<div class="panel log negative" role="log" aria-label="Server log" aria-live="off" tabindex="0" bind:this={box} onscroll={scrolled}>
  {#each lines as line (line.id)}
    <div class="line {line.level.toLowerCase()}">
      <time datetime={line.ts}>{time(line.ts)}</time>
      <span class="level">{line.level === 'WARNING' ? 'WARN' : line.level}</span>
      <span class="source">{line.source === 'app' ? line.name : 'traffic'}</span>
      <span class="text">{line.text}</span>
    </div>
  {:else}
    <div class="empty muted">{connected ? 'Nothing yet at this level.' : ''}</div>
  {/each}
</div>

<style>
  .bar {
    margin-bottom: var(--sp-8);
  }
  .follow {
    gap: var(--sp-6);
  }
  .status {
    margin-bottom: var(--sp-8);
  }
  /* drawn on the negative, like the console */
  .log {
    font-family: var(--font-mono);
    font-size: var(--fs-xs);
    height: min(65dvh, 40rem);
    overflow: auto;
    padding: var(--sp-8) var(--sp-12);
  }
  .line {
    display: grid;
    grid-template-columns: auto auto minmax(0, 9rem) minmax(0, 1fr);
    gap: var(--sp-10);
    padding: var(--sp-2) 0;
    align-items: baseline;
  }
  .line + .line {
    border-top: 1px solid var(--line);
  }
  time,
  .source {
    color: var(--muted);
  }
  .source {
    overflow: hidden;
    text-overflow: ellipsis;
    white-space: nowrap;
  }
  .level {
    font-weight: 650;
    min-width: 4ch;
  }
  .text {
    white-space: pre-wrap;
    overflow-wrap: anywhere;
  }
  .warning .level {
    color: var(--warn);
  }
  .error .level,
  .error .text {
    color: var(--danger);
  }
  @media (max-width: 40rem) {
    .line {
      grid-template-columns: auto auto minmax(0, 1fr);
    }
    .source {
      display: none;
    }
  }
</style>
