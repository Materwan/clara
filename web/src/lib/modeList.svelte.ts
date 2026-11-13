// The modes you made, for every mode selector: asked for when a selector appears, so a new mode is there at once.

import { api } from './api';

export const modeList = $state<{ names: string[] }>({ names: [] });

export async function loadModes(): Promise<void> {
  modeList.names = (await api.get('/v1/modes')).modes.map((mode: { name: string }) => mode.name);
}
