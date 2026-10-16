// The colour theme, as public/theme.js applies it before the first frame: "auto" follows the system.
// The browser's own bars (the theme-color tags of index.html, one per theme) follow it: a forced theme keeps only its own tag.

export type Theme = 'auto' | 'light' | 'dark';

const KEY = 'clara-theme';

export function getTheme(): Theme {
  try {
    const value = localStorage.getItem(KEY);
    return value === 'light' || value === 'dark' ? value : 'auto';
  } catch {
    return 'auto';
  }
}

function paintBars(choice: Theme): void {
  for (const meta of document.querySelectorAll<HTMLMetaElement>('meta[name="theme-color"]')) {
    const scheme = meta.dataset.scheme;
    meta.media = choice === 'auto' ? `(prefers-color-scheme: ${scheme})` : choice === scheme ? 'all' : 'not all';
  }
}

export function setTheme(choice: Theme): void {
  const root = document.documentElement;
  if (choice === 'auto') delete root.dataset.theme;
  else root.dataset.theme = choice;
  paintBars(choice);
  try {
    if (choice === 'auto') localStorage.removeItem(KEY);
    else localStorage.setItem(KEY, choice);
  } catch {
    /* not remembered: this page keeps it until reloaded */
  }
}
