<script lang="ts">
  import { getTheme, setTheme, type Theme } from '../lib/theme';
  import Segmented from './Segmented.svelte';

  const CHOICES: { value: Theme; label: string; icon: string; title: string }[] = [
    { value: 'auto', label: 'Auto', icon: 'auto', title: 'Follow the system' },
    { value: 'light', label: 'Light', icon: 'sun', title: 'Light theme' },
    { value: 'dark', label: 'Dark', icon: 'moon', title: 'Dark theme' },
  ];
  let chosen = $state<Theme>(getTheme());
</script>

<Segmented quiet label="Colour theme" options={CHOICES} bind:value={chosen} onchange={setTheme} />
