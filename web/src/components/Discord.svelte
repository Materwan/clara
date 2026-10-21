<script lang="ts">
  import Icon from './Icon.svelte';
  import { api } from '../lib/api';
  import { toast } from '../lib/state.svelte';

  // The Discord bot as the server sees it: whether it is there, what it sees in each of its four channels, which channel carries
  // each kind of message (the server works the channel out; this page only writes the choice) and a test message per channel.
  // Channel ids never come here: they stay in the bot's own settings.
  type Test = { state: 'waiting' | 'ok' | 'failed'; text: string };

  let data = $state<any>(null);
  let tests = $state<Record<string, Test>>({});
  let saving = $state(false);
  let gone = false;

  const connected = $derived(data?.bot.state === 'connected');
  const changed = $derived<number>(data ? data.categories.filter((item: any) => item.channel !== item.default).length : 0);

  async function load(): Promise<void> {
    try {
      data = await api.get('/v1/admin/discord');
    } catch (error) {
      toast((error as Error).message, 'error');
    }
  }

  $effect(() => {
    gone = false;
    load();
    const timer = setInterval(load, 5000); // "seen 12 s ago" and the rights move by themselves
    return () => {
      gone = true;
      clearInterval(timer);
    };
  });

  function ago(seconds: number | null): string {
    if (seconds === null) return 'never';
    if (seconds < 90) return `${seconds} s ago`;
    if (seconds < 5400) return `${Math.round(seconds / 60)} min ago`;
    return `${Math.round(seconds / 3600)} h ago`;
  }

  const rights = (missing: string[]): string => missing.map((right) => right.replaceAll('_', ' ')).join(', ');

  async function save(value: Record<string, string>): Promise<void> {
    saving = true;
    try {
      await api.put('/v1/admin/settings/discord_routes', { value });
      toast('Saved.', 'ok');
    } catch (error) {
      toast((error as Error).message, 'error');
    }
    saving = false;
    load();
  }

  /** The categories sent somewhere other than their default: all the server keeps. */
  function overrides(change: { id: string; channel: string }): Record<string, string> {
    const found: Record<string, string> = {};
    for (const item of data.categories) {
      const channel = item.id === change.id ? change.channel : item.channel;
      if (channel !== item.default) found[item.id] = channel;
    }
    return found;
  }

  async function test(channel: string): Promise<void> {
    tests[channel] = { state: 'waiting', text: 'Sending…' };
    let sent: { event: number };
    try {
      sent = await api.post('/v1/admin/discord/test', { channel });
    } catch (error) {
      tests[channel] = { state: 'failed', text: (error as Error).message };
      return;
    }
    // The bot says how it went in its next heartbeat, which it sends at once after a test.
    for (let attempt = 0; attempt < 12 && !gone; attempt++) {
      await new Promise((resolve) => setTimeout(resolve, 1000));
      await load();
      if (data?.test?.event === sent.event) {
        tests[channel] = true ? { state: 'ok', text: 'Delivered.' } : { state: 'failed', text: `Discord refused it: ${data.test.error || 'no reason given'}` };
        return;
      }
    }
    tests[channel] = { state: 'failed', text: 'The bot did not answer within 12 seconds.' };
  }
</script>

{#if data}
  <div class="stack">
    <section class="panel small stack">
      <h2>The bot
        <span class="tag" class:ok={connected} class:warn={!connected}>{data.bot.state}</span>
        {#if data.bot.name}<span class="muted">{data.bot.name}</span>{/if}</h2>
      <p class="small">Last heartbeat: {ago(data.bot.seen_seconds)}.
        {data.bot.started_by_server ? 'Started by the server (CLARA_DISCORD_AUTOSTART).' : 'Not started by the server: it runs as clara-discord.service or by hand.'}</p>
      {#if data.bot.problem}<p class="danger"><Icon name="warn" size={16} /> {data.bot.problem}</p>{/if}
      {#if data.bot.alert}<p class="danger"><Icon name="warn" size={16} /> {data.bot.alert}</p>{/if}
      {#if data.bot.state === 'not seen' && !data.bot.expected}
        <p class="muted small">No bot has checked in since the server started, and the server is not told to expect one. To set it up, see Discord in docs/OPERATIONS.md.</p>
      {/if}
    </section>

    <div class="grid-2">
      {#each data.channels as channel (channel.id)}
        <section class="panel small stack" aria-label="Channel {channel.id}">
          <h2>#{channel.id}
            {#if channel.known && channel.missing.length === 0}<span class="tag ok">all rights</span>
            {:else if channel.known}<span class="tag warn">missing rights</span>{/if}</h2>
          <p class="small">In Discord: {#if channel.known}<strong>#{channel.name}</strong>{:else}<span class="muted">not reported yet</span>{/if}</p>
          {#if channel.missing.length}<p class="small danger">The bot lacks: {rights(channel.missing)}.</p>{/if}
          <p class="small">Receives now: {channel.carries.length ? channel.carries.join(', ') : 'nothing'}.</p>
          <div class="row">
            <button disabled={!connected || tests[channel.id]?.state === 'waiting'} aria-label="Send a test message to #{channel.id}" onclick={() => test(channel.id)}
              title={connected ? '' : 'The bot is not connected'}>Send a test message</button>
            {#if tests[channel.id]}
              <span class="small" class:ok={tests[channel.id].state === 'ok'} class:danger={tests[channel.id].state === 'failed'} role="status">{tests[channel.id].text}</span>
            {/if}
          </div>
        </section>
      {/each}
    </div>

    <section class="panel small stack">
      <h2>Where each kind of message goes</h2>
      <p class="muted small">Pick the channel of each kind, or switch it off on Discord (the web site and the other clients still get it). A change applies to the next message.</p>
      {#each data.categories as item (item.id)}
        <div class="row">
          <div class="grow"><strong>{item.label}</strong>
            {#if item.channel !== item.default}<span class="tag">changed</span>{/if}
            <div class="muted small">{item.about}</div></div>
          <select aria-label="Channel for {item.label}" value={item.channel} disabled={saving}
            onchange={(event) => save(overrides({ id: item.id, channel: event.currentTarget.value }))}>
            {#each data.choices as choice}<option value={choice}>#{choice}{choice === item.default ? ' (default)' : ''}</option>{/each}
            {#if item.can_be_off}<option value="off">Off on Discord</option>{/if}
          </select>
        </div>
      {/each}
      <p class="muted small">Always the same: approval requests go to #approval (the buttons are there), the questions of scheduled runs and your conversation to #general.
        Alerts can move but cannot be switched off.</p>
      <div class="row"><button disabled={!changed || saving} onclick={() => save({})}>Back to the defaults</button></div>
    </section>
  </div>
{/if}
