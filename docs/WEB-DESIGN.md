# The web site's design system

What the site is drawn with, and the rules that keep it one thing. The design itself ("Cyanotype": the page is the
print, the rail is the negative, one violet signal) is in [DECISIONS.md](DECISIONS.md); this is how it is built.
Everything lives in `web/src/app.css` (tokens and shared components) and `web/src/components/`.

## Rules

1. **No colour, size or space is written twice.** A value comes from a token. A page's own `<style>` block may lay
   things out; it may not contain a hex colour, a `px`/`rem` font size or a padding in rem. `grep -nE '#[0-9a-f]{3,8}\b'`
   over `web/src` finds nothing outside the token block.
2. **State has one vocabulary:** `ok`, `warn`, `danger`. The same three words style a text colour (`.ok`), a `.tag`, a
   `.count` (`.count.danger`), a `.dot` and a button (`button.danger`).
3. **The state of a control is an attribute**, and the attribute drives the style: `aria-pressed` (toggles, pins,
   the segmented control), `aria-current="page"` (navigation), `aria-current="true"` (the open note or file),
   `aria-expanded` (disclosures), `aria-busy` (a button waiting for an answer), `aria-invalid` (a field). A class that
   only repeats it is not added. The exceptions are structural: `.open` on the drawer, `.on` on the scrim, `.convo.active`
   on a list row (a list item cannot carry `aria-current`).
4. **Every control has a name a screen reader can read:** a wrapping `<label>`, `label.field`, or `aria-label`. A
   placeholder is a hint, not a name. Icon-only buttons have `aria-label` (and `title` when the picture is not obvious).
5. **No inline `style=` attributes** (the CSP forbids them). A value that comes from data goes through Svelte's `style:`
   directive, as in `style:width={…}`.
6. **A question the user must answer is a dialog** (`confirmDialog`, `promptDialog`), never `confirm()` or `prompt()`.

## Tokens

All colours are written once, as `light-dark(light, dark)`, in the `:root` block. `color-scheme: light dark` follows the
system; `<html data-theme="light|dark">` (set by `public/theme.js` before the first frame, and by the theme switch)
forces one. Needs a browser from 2024 or later (`light-dark()`).

| Group | Tokens | Use |
| --- | --- | --- |
| Surfaces | `--bg` `--panel` `--panel-2` `--raised` `--hover` `--select` | The page, a side column, a chip or a quiet fill, a field or a card, the tint of a hover, of a selected row |
| Lines | `--line` `--line-strong` | A rule (decorative); the border of a control (3:1 against every surface it sits on) |
| Text | `--text` `--text-2` `--muted` | Body; secondary; the least (6:1 against the page) |
| Main action | `--primary` `--primary-hover` `--primary-ink` | Ink on the page's own colour |
| Ticked | `--check` `--check-ink` | A ticked checkbox or a chosen radio: one violet for both themes (3:1 on every surface), a white tick on it (5:1) |
| The signal | `--accent` `--accent-hover` `--accent-ink` `--accent-soft` `--accent-text` `--accent-line` | The violet of Clara's ring. As a fill (with `-ink` on it); as text or a link (`-text`, 4.5:1); as a mark: an underline, a bar, a cursor (`-line`, 3:1); as a tint (`-soft`) |
| State | `--ok` `--warn` `--danger`, each with `-soft` (a tint), `--danger-ink` (on the solid colour) | A tag, a note, a toast |
| Code | `--code-bg` `--code-line` | Code and tool output |
| The negative | `--rail-bg` `--rail-text` `--rail-text-2` `--rail-muted` `--rail-signal` `--rail-ok` `--rail-warn` `--rail-danger` `--rail-grid` | Prussian blue, both themes (see below) |
| Overlay | `--scrim` `--shadow-lg` | Behind a drawer or dialog; the lift of a drawer or toast |

| Scale | Tokens |
| --- | --- |
| Type (rem, so the reader's text-size setting applies) | `--fs-2xs` 11 · `--fs-xs` 11.5 · `--fs-sm` 12 · `--fs-md` 13 (the page) · `--fs-lg` 14 (answers, what you write) · `--fs-xl` 16 (the title bar, section headings) · `--fs-2xl` 18 · `--fs-3xl` 20 · `--fs-4xl` 26. Families: `--font` (Hanken Grotesk), `--font-display` (Epilogue, for titles), `--font-mono`. Only the sign-in and welcome wordmarks use `clamp()` |
| Space (a 2px grid, named by pixels) | `--sp-2` `4` `6` `8` `10` `12` `14` `16` `20` `24` `32` `48` `64`; a negative offset is `calc(var(--sp-12) * -1)` |
| Shape | `--r-xs` 4 · `--r-sm` 6 · `--r-md` 8 · `--r-lg` 12 · `--r-pill` |
| Motion | `--dur-fast` .15s (a colour) · `--dur` .24s (a panel, a toast) · `--dur-slow` .4s; `--ease-out`. `prefers-reduced-motion` switches all of it off |
| Stacking | `--z-head` 5 · `--z-scrim` 30 · `--z-rail` 40 · `--z-toast` 100; a dialog is in the browser's top layer, above all |
| Breakpoints | 860 px (the rail, with the conversation list in it, becomes a drawer), 600 px (phone). CSS cannot read a token in a media query, so the first is repeated in `lib/state.svelte.ts` (`watchViewport`) |

## The negative

The rail is Prussian blue whatever the theme. `.rail` and `.negative` re-read the tokens (`--text`, `--muted`, `--line`,
`--accent-text`, `--danger` …) against that blue, so any component dropped inside — a button, a count, an input — needs no
variant of its own. Use `class="negative"` for another dark surface (the Admin console and the Logs tab are two); never hard-code its colours.

The rail has three parts: `.rail-top` (the logo and **New chat**) and `.rail-foot` (the approvals waiting for you, and the
account tab) stay put; `.rail-scroll` between them holds the pages and the conversation list (`ConversationRail.svelte`),
which scroll as one. A conversation row's background is opaque (`--row`, mixed from `--rail-bg`) so its buttons can fade over its title.

The state of the connection is a lamp on the rim of Clara's portrait (`.portrait > .dot`, ringed in the rail's blue so it
sits in the picture: `ok` connected, `warn` reconnecting, `danger` Clara not reachable). It has no visible text; a
visually hidden `role="status"` beside it says the same in words. Settings, the colour theme and Sign out are in the
account tab below.

## Components

| Component | Where | Notes |
| --- | --- | --- |
| Button | `button` | Variants: default (outline), `.primary`, `.danger`, `.ghost`, `.icon-btn`, `.link`. Sizes: default, `.small`, `.large`. States: hover, active, focus-visible, `:disabled`, `aria-busy="true"` (spinner; keeps its label). The base is wrapped in `:where()` (specificity 0): a component restyles a button with one plain class |
| Field | `label.field` + `input` / `select` / `textarea` | A label above a control. States: hover, focus-visible, `:disabled`, `:user-invalid` and `[aria-invalid='true']` (red border). `.small` for compact controls. On iPhone and iPad fields are 16 px (Safari zooms into a smaller one and stays zoomed) |
| Checkbox | `Checkbox.svelte` | Never a bare `<input type="checkbox">`. The input is the box (`appearance: none`, 16 px, 2 px `--line-strong` border); ticked, it fills `--check` and a tick drawn like the line icons unrolls over it. Takes every input attribute; `bind:checked` works. It is a line high, so beside text it sits on the first line. Radios share the look, round, with a violet dot |
| Frame | `.frame`, `.panel`, `table`, `.approval`, `.qcm`, `.cal` | The ruled box: a 2px rule on top, a hairline around. Knobs: `--frame-rule`, `--frame-edge`. `.frame.signal` marks a step that waits for you |
| Segmented control | `Segmented.svelte` | A few choices, one on: a group of toggle buttons (`aria-pressed`) named by `label`; `count` shows a badge; `quiet` is the small variant (the theme switch). Not a tab list: it does not own a panel |
| Account tab | `AccountMenu.svelte` | The foot of the rail: the signed-in token's name, the alert count and a chevron. It opens upward onto a `.frame` panel (violet rule) with Settings, the theme switch and Sign out. A disclosure (`aria-expanded`), not a menu role. Escape (heard in the capture phase, so it shuts the panel before the drawer), a click or focus outside, and the drawer closing all close it; picking a theme keeps it open |
| Settings bar | `.settings-bar` | Page-level navigation: links with `aria-current="page"` |
| Tag, count, dot | `.tag`, `.count`, `.dot` | A state, a number, a connection; each takes `ok` / `warn` / `danger` |
| Chip | `.chip` | A file or a kind; its remove button is 24 px |
| Dialog | `Dialog.svelte`, `lib/dialog.svelte.ts` | A native `<dialog>`: the page behind is inert, Tab stays inside, Escape cancels. `confirmDialog({ message, title?, confirm?, danger? })` returns a boolean (focus starts on **Cancel**; `danger` makes the yes button red and the top rule red); `promptDialog({ message, label?, value?, type? })` returns the text or `null` (`type: 'password'` hides it; `'datetime-local'` shows a picker). A click on the backdrop does not dismiss it, so a half-typed secret is not lost |
| Toast | `toast(text, kind)` in `lib/state.svelte.ts` | `info`, `ok`, `error`. Gone after 4 s (8 s for an error), but it waits while you point at it or tab into it, and has a dismiss button. Errors are `role="alert"`, the rest `role="status"` |
| Empty state | `.empty` | A line of text; say what to do next |
| Icon, mark | `Icon.svelte`, `Mark.svelte` | Line icons from `lib/icons.ts` (one stroke width). Sizes 14, 16, 18, 20: decorative unless given a `label` |
| Cards | `ApprovalCard`, `FileCard`, `QcmForm` | What Clara puts in a conversation |

## Accessibility

- **Contrast:** text pairs are 4.5:1 or more and controls' borders and marks 3:1 or more, in both themes and on the
  negative; the lowest text pair is 5.16:1 (`--ok` on `--ok-soft`, light). Check a change by computing the pairs from the
  tokens, not by eye.
- **Focus:** one ring everywhere (2px `--text`, offset 2px; `--rail-signal` on the negative). Never `outline: none` on a
  control without drawing another.
- **Keyboard:** a **Skip to content** button is the first stop; `<main id="main">` takes focus from it. Escape closes a
  drawer or a dialog; a drawer takes focus when it opens and gives it back to what opened it, and the page behind it is
  `inert`.
- **Targets:** at least 24 px (WCAG 2.2); 36 px for icon buttons and small buttons on a touch screen
  (`pointer: coarse`). Per-conversation buttons are always visible on touch (`hover: none`) since there is no pointing
  at a row.
- **Headings:** one `<h1>` per page (the title in the header); sections are `<h2>`. The chat's greeting is a display
  line (`.greeting`, an `<h2>`), not a second title.
- **Forced colours** (Windows high contrast): the selected state is drawn with an outline, select arrows fall back
  to the native ones, and a ticked box draws its tick in the text colour.
- **Text size:** the page is `--fs-md` in rem, not a pixel size, so the browser's setting scales everything.

## Changing it

1. Need a value that is not in a table above? Add a token; do not write the literal.
2. A new colour needs a light and a dark side, and a contrast check against the surfaces it sits on.
3. A new component that restyles a button: write one plain class (the base has no specificity), and do not copy
   `:hover:not(:disabled)`.
4. A new page: `<div class="page stack">`, one `<h1>` from the header, `Segmented` for views, `Dialog` for questions,
   `label.field` or `aria-label` on every control.
5. Run `npm run check` and `npm run build` in `web/` (releases build in a pinned Node image: see `scripts/`).
