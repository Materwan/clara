// Lets `node --test` import the site's .ts files as they are (some Node builds cannot strip types): each one is
// transpiled with the TypeScript of node_modules when it is loaded.
import { register } from 'node:module';

register('./ts-loader.mjs', import.meta.url);
