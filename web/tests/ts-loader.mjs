import { readFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';

const ts = createRequire(import.meta.url)('typescript');

export async function load(url, context, next) {
  if (!url.endsWith('.ts')) return next(url, context);
  const source = await readFile(fileURLToPath(url), 'utf8');
  const { outputText } = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2022 } });
  return { format: 'module', source: outputText, shortCircuit: true };
}
