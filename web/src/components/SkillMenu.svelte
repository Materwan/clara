<script lang="ts">
  import { type Command, type Project, optionId, projectNames } from '../lib/skills';

  // The menu of "/" above the box you write in: the commands this conversation can run, filtered by what was typed. A listbox the box points
  // at (aria-activedescendant): the keys stay in the box, which is why the rows are not focusable. The box handles Up, Down, Tab, Enter
  // and Escape (Chat.svelte, lib/skills.ts menuKey); a click or a tap chooses too.
  let { rows, at, projects, onPick }: { rows: Command[]; at: number; projects: Project[]; onPick: (command: Command) => void } = $props();

  // the row the keys are on stays in view inside the list (scrollIntoView would move the page too, under a phone's keyboard)
  $effect(() => {
    const row = rows[at] ? document.getElementById(optionId(rows[at].name)) : null;
    const list = row?.parentElement;
    if (!row || !list) return;
    if (row.offsetTop < list.scrollTop) list.scrollTop = row.offsetTop;
    else if (row.offsetTop + row.offsetHeight > list.scrollTop + list.clientHeight) list.scrollTop = row.offsetTop + row.offsetHeight - list.clientHeight;
  });
</script>

<!-- the mouse must not take the focus from the box: a click on a row chooses it, and the keys go on working -->
<!-- svelte-ignore a11y_no_noninteractive_element_interactions (the box drives the listbox by the keys; the pointer is the same choice) -->
<ul class="skill-menu frame" id="skill-menu" role="listbox" aria-label="Commands" onmousedown={(event) => event.preventDefault()}>
  {#each rows as command, index (command.name)}
    <!-- svelte-ignore a11y_click_events_have_key_events, a11y_interactive_supports_focus (the options are chosen from the box, by aria-activedescendant) -->
    <li id={optionId(command.name)} role="option" aria-selected={index === at} class:on={index === at} onclick={() => onPick(command)}>
      <span class="name">/{command.name}</span>
      <span class="what">{command.description}</span>
      {#if command.scope === 'projects'}<span class="tag where" title={projectNames({ projects: command.projects ?? [] }, projects)}>{projectNames({ projects: command.projects ?? [] }, projects)}</span>{/if}
    </li>
  {/each}
</ul>

<style>
  .skill-menu {
    --frame-rule: var(--accent-line); --frame-edge: var(--line-strong);
    position: absolute; z-index: 2; left: 0; right: 0; bottom: calc(100% + var(--sp-8)); margin: 0; padding: var(--sp-6); list-style: none;
    max-height: min(18rem, 40vh); overflow-y: auto; overscroll-behavior: contain; background: var(--raised); box-shadow: var(--shadow-lg);
  }
  li {
    display: flex; align-items: baseline; gap: var(--sp-10); padding: var(--sp-6) var(--sp-8); border-radius: var(--r-sm); cursor: pointer; min-width: 0;
    color: var(--text-2);
  }
  li:hover { background: var(--hover); color: var(--text); }
  li.on { background: var(--accent-soft); color: var(--text); }
  .name { flex: none; font-family: var(--font-mono); font-size: var(--fs-md); font-weight: 650; color: var(--text); }
  .what { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: var(--fs-sm); color: var(--muted); }
  li.on .what { color: var(--text-2); }
  .where { flex: none; max-width: 40%; overflow: hidden; text-overflow: ellipsis; }
</style>
