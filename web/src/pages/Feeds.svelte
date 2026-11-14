<script lang="ts">
  import { tick } from 'svelte';
  import Icon from '../components/Icon.svelte';
  import { ApiError, api, when } from '../lib/api';
  import { confirmDialog } from '../lib/dialog.svelte';
  import {
    NO_SCOPE, addressLabel, canChange, checkAddress, feedError, feedLine, feedName, followBody, itemDate, shortAddress, type Feed, type Found,
  } from '../lib/feeds';
  import { live, session, toast } from '../lib/state.svelte';

  // The feeds you follow: a blog, a channel, a podcast. You paste an address, Clara finds the feed and shows it with its latest items
  // (nothing is stored), and only Follow stores it. Every word a feed wrote (its title, its items) is shown as plain text: never as a
  // link, never as markup.
  type Preview = { pasted: string; found: Found };
  let { onchange }: { onchange?: () => void } = $props(); // the list changed: the Automation page looks again at whether any feed is followed

  let feeds = $state<Feed[] | null>(null);
  let limit = $state(50);
  let pollSeconds = $state(1800);
  let loadError = $state('');
  let address = $state('');
  let finding = $state(false);
  let findError = $state('');
  let preview = $state<Preview | null>(null);
  let following = $state(false);
  let followError = $state('');

  /** Only a token that may answer everything looks at an address or changes the list (the others get a 403): they read the list. */
  const mayChange = $derived(canChange(session.approvals));

  async function load(): Promise<void> {
    try {
      const data = await api.get('/v1/feeds');
      feeds = data.feeds;
      limit = data.limit;
      pollSeconds = data.poll_seconds;
      loadError = '';
    } catch (error) {
      loadError = (error as Error).message;
    }
  }

  $effect(() => {
    void live.refresh;
    load();
  });

  const focusAddress = async (): Promise<void> => {
    await tick();
    document.getElementById('fd-address')?.focus();
  };

  async function find(): Promise<void> {
    if (finding) return;
    findError = checkAddress(address);
    if (findError) return void document.getElementById('fd-address')?.focus();
    finding = true;
    preview = null;
    followError = '';
    const pasted = address.trim();
    try {
      preview = { pasted, found: await api.post('/v1/feeds/find', { url: pasted }) };
      await tick();
      document.getElementById('fd-preview')?.focus();
    } catch (error) {
      findError = feedError(error as ApiError);
    } finally {
      finding = false;
    }
  }

  async function cancel(): Promise<void> {
    preview = null;
    followError = '';
    await focusAddress();
  }

  async function follow(): Promise<void> {
    if (!preview || following) return;
    following = true;
    followError = '';
    try {
      const added: Feed = (await api.post('/v1/feeds', followBody(preview.found))).feed; // the feed's own address, not what was pasted
      toast(`Following ${feedName(added)}`, 'ok');
      preview = null;
      address = '';
      load();
      onchange?.();
      await focusAddress();
    } catch (error) {
      followError = feedError(error as ApiError);
    } finally {
      following = false;
    }
  }

  async function remove(feed: Feed): Promise<void> {
    const message = `Stop following “${feedName(feed)}”? Automations that wait for feed items stay; this feed stops being read.`;
    if (!(await confirmDialog({ title: 'Remove feed', message, confirm: 'Remove', danger: true }))) return;
    try {
      await api.del(`/v1/feeds/${feed.id}`);
    } catch (error) {
      toast(feedError(error as ApiError, 'remove'), 'error');
    }
    await load();
    onchange?.();
    await tick();
    document.getElementById('fd-list-title')?.focus();
  }
</script>

<p class="intro">Blogs, channels, podcasts, arXiv: a new item can start an automation. Paste the address of a feed, or of a page (a YouTube channel,
  a blog) and Clara finds the feed once.</p>

<form class="panel stack" aria-label="Follow a feed" novalidate onsubmit={(event) => { event.preventDefault(); find(); }}>
  <div class="field">
    <label class="field-title" for="fd-address">Paste the address of a feed, or of a page (a YouTube channel, a blog)</label>
    <div class="row nowrap">
      <input id="fd-address" class="grow" bind:value={address} disabled={!mayChange} inputmode="url" autocomplete="off" autocapitalize="none"
        spellcheck="false" placeholder="https://example.org/blog" aria-invalid={findError ? 'true' : undefined} aria-describedby={findError ? 'fd-address-error' : undefined} />
      <button type="submit" class="primary" disabled={!mayChange} aria-busy={finding}>Find</button>
    </div>
    {#if findError}<span class="field-error" id="fd-address-error" role="alert">{findError}</span>{/if}
  </div>
  {#if !mayChange}<p class="muted small">{NO_SCOPE}</p>{/if}
  <p class="muted small">The Shorts of a YouTube channel are in its feed and cannot be told apart reliably.</p>
</form>

{#if preview}
  <section class="panel frame signal stack" id="fd-preview" tabindex="-1" aria-labelledby="fd-preview-title">
    <h2 id="fd-preview-title">{feedName({ title: preview.found.title, url: preview.found.feed_url })}</h2>
    <p class="small"><span class="muted">{addressLabel(preview.pasted, preview.found.feed_url)}</span> <span class="url">{preview.found.feed_url}</span></p>
    {#if preview.found.items.length}
      <div class="stack tight">
        <span class="field-title">Its latest items</span>
        <ul class="rows" aria-label="Latest items">
          {#each preview.found.items as item}
            <li><span class="item">{item.title || '(untitled)'}</span><span class="when">{itemDate(item.published)}</span></li>
          {/each}
        </ul>
      </div>
    {:else}
      <p class="muted small">It has no item yet. You can follow it: a new item will start an automation.</p>
    {/if}
    <p class="muted small">Nothing is stored until you press Follow.</p>
    {#if followError}<p class="field-error" role="alert">{followError}</p>{/if}
    <div class="row">
      <button type="button" class="primary" disabled={following} aria-busy={following} onclick={follow}>Follow</button>
      <button type="button" disabled={following} onclick={cancel}>Cancel</button>
    </div>
  </section>
{/if}

{#if pollSeconds === 0}
  <p class="call-warning" role="note"><Icon name="warn" size={14} /><span>Feed polling is off (<code>CLARA_FEEDS_POLL_SECONDS=0</code>): no automation will start from a feed.</span></p>
{/if}

{#if loadError}<p class="field-error" role="alert">{loadError}</p>{/if}
{#if feeds === null}
  {#if !loadError}<p class="muted" role="status">Loading…</p>{/if}
{:else}
  <section class="panel stack" aria-labelledby="fd-list-title">
    <h2 id="fd-list-title" tabindex="-1">Followed ({feeds.length} of {limit})</h2>
    {#if feeds.some((feed) => !feed.watching_since)}
      <p class="muted small">“Waits for an automation”: a feed is read only while an enabled automation waits for feed items.</p>
    {/if}
    {#if !feeds.length}
      <p class="muted">Nothing followed yet. Paste the address of a blog, a YouTube channel or a podcast above, and an automation can start from what it publishes.</p>
    {:else}
      <ul class="feeds">
        {#each feeds as feed (feed.id)}
          <li>
            <div class="main">
              <strong class="name">{feedName(feed)}</strong>
              <span class="small muted url" title={feed.url}>{shortAddress(feed.url)}</span>
              <span class="small" title={feed.checked_at ? `Checked ${when(feed.checked_at)}` : undefined}>{feedLine(feed).join(' · ')}</span>
              {#if feed.error}<span class="call-warning" role="note"><Icon name="warn" size={14} /><span>{feed.error}</span></span>{/if}
            </div>
            <button type="button" class="small danger" disabled={!mayChange} aria-label={`Remove ${feedName(feed)}`} onclick={() => remove(feed)}>Remove</button>
          </li>
        {/each}
      </ul>
    {/if}
  </section>
{/if}

<style>
  .tight > * + * { margin-top: var(--sp-6); }
  .nowrap { flex-wrap: nowrap; align-self: stretch; }
  .nowrap input { min-width: 0; }
  .item { min-width: 0; overflow-wrap: anywhere; }
  .feeds { list-style: none; margin: 0; padding: 0; }
  .feeds li { display: flex; flex-wrap: wrap; align-items: flex-start; gap: var(--sp-10) var(--sp-16); padding: var(--sp-12) 0; border-bottom: 1px solid var(--line); }
  .feeds li:first-child { padding-top: 0; }
  .feeds li:last-child { border-bottom: none; padding-bottom: 0; }
  .main { flex: 1 1 14rem; min-width: 0; display: flex; flex-direction: column; gap: var(--sp-4); }
  .name { overflow-wrap: anywhere; }
</style>
