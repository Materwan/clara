// The Google Picker window: the only way Drive files Clara did not create become reachable (scope drive.file).
import { api } from './lib/api';

declare const gapi: any;
declare const google: any;

const status = document.getElementById('picker') ?? document.body;

function say(text: string): void {
  status.textContent = text;
}

function load(src: string): Promise<void> {
  return new Promise((resolve, reject) => {
    const script = document.createElement('script');
    script.src = src;
    script.onload = () => resolve();
    script.onerror = () => reject(new Error(`Could not load ${src}`));
    document.head.append(script);
  });
}

async function open(): Promise<void> {
  const config = await api.get('/v1/connections/google/picker');
  await load('https://apis.google.com/js/api.js');
  await new Promise<void>((resolve) => gapi.load('picker', resolve));
  const docs = new google.picker.DocsView().setIncludeFolders(true).setSelectFolderEnabled(true);
  new google.picker.PickerBuilder()
    .addView(docs)
    .enableFeature(google.picker.Feature.MULTISELECT_ENABLED)
    .setOAuthToken(config.token)
    .setDeveloperKey(config.api_key)
    .setAppId(config.app_id)
    .setCallback(async (data: any) => {
      if (data.action === google.picker.Action.CANCEL) window.close();
      if (data.action !== google.picker.Action.PICKED) return;
      const files = data.docs.map((doc: any) => ({ id: doc.id, name: doc.name, mimeType: doc.mimeType ?? '' }));
      const done = await api.post('/v1/connections/google/picked', { files });
      say(`${done.added.length} item(s) opened to Clara. You can close this window.`);
      setTimeout(() => window.close(), 1500);
    })
    .build()
    .setVisible(true);
}

open().catch((error) => say((error as Error).message));
