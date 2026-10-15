import 'katex/dist/katex.min.css';
import './app.css';
import { mount } from 'svelte';
import App from './App.svelte';

try {
  const theme = localStorage.getItem('clara-theme');
  if (theme === 'light' || theme === 'dark') document.documentElement.dataset.theme = theme;
} catch {
  /* private mode: the system theme */
}

mount(App, { target: document.getElementById('app')! });
