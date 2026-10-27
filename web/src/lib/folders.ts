// Folders on the person's computer, read in the browser (DECISIONS.md, « Folders on your computer »). The server asks for
// one of the four folder tools; this module answers it from the folder the person picked, as JSON: `{text, clear}` for
// what the model reads and the files that may be read without a card, or `{error}`. What may be asked, and the budget,
// are decided by the server; here only the reading is done, capped the same way the server caps it.
//
// The browser's folder API has no symbolic links and no Windows hidden flag, so a hidden file is a name that starts with
// a dot, and an ignored one is matched by the `.gitignore` files of its folders, read as Git reads them.

export const FOLDER_TOOLS = ['folder_list', 'folder_find', 'folder_grep', 'folder_read'];

const LIST_LIMIT = 500;
const MATCH_LIMIT = 100;
const READ_CHARS = 40_000;
const LINE_CHARS = 300;
const GREP_FILE_BYTES = 2_000_000;
const READ_FILE_BYTES = 20_000_000;
const SCAN_LIMIT = 50_000; // entries one search walks at most
const SNIFF_BYTES = 8_000; // a file with a zero byte in its first bytes is not text

export type Answer = { text: string; clear: string[] };

export function isFolderTool(name: string): boolean {
  return FOLDER_TOOLS.includes(name);
}

/** A path inside the folder: relative, `/` between names, no `..`. The server checks the same. */
export function cleanPath(value: unknown): string {
  if (value === undefined || value === null || value === '') return '';
  if (typeof value !== 'string') throw new Error('the path must be a text');
  const text = value.trim().replace(/\\/g, '/');
  if (text.startsWith('/') || /^[A-Za-z]:/.test(text)) throw new Error('the path must be relative to the folder you opened');
  const parts = text.split('/').filter((part) => part !== '' && part !== '.');
  if (parts.includes('..')) throw new Error('the path may not leave the folder you opened');
  return parts.join('/');
}

/** A name pattern with `*` (any characters) and `?` (one character), case-insensitive, for one name. */
export function nameRegex(pattern: string): RegExp {
  let source = '';
  for (const char of pattern) {
    if (char === '*') source += '.*';
    else if (char === '?') source += '.';
    else source += char.replace(/[.+^${}()|[\]\\]/g, '\\$&');
  }
  return new RegExp(`^${source}$`, 'i');
}

export type Rule = { base: string; test: RegExp; negate: boolean; dirOnly: boolean };

/** One `.gitignore`, as the rules that apply inside its folder `base` ('' for the folder you opened). */
export function parseIgnore(base: string, text: string): Rule[] {
  const rules: Rule[] = [];
  for (const raw of text.split(/\r?\n/)) {
    let line = raw.replace(/(?<!\\)\s+$/, '');
    if (!line || line.startsWith('#')) continue;
    let negate = false;
    if (line.startsWith('!')) {
      negate = true;
      line = line.slice(1);
    } else if (line.startsWith('\\!') || line.startsWith('\\#')) {
      line = line.slice(1);
    }
    let dirOnly = false;
    if (line.endsWith('/')) {
      dirOnly = true;
      line = line.slice(0, -1);
    }
    if (!line) continue;
    const anchored = line.includes('/');
    if (line.startsWith('/')) line = line.slice(1);
    const body = gitGlob(line);
    rules.push({ base, test: new RegExp(anchored ? `^${body}$` : `^(?:.*/)?${body}$`), negate, dirOnly });
  }
  return rules;
}

function gitGlob(glob: string): string {
  let out = '';
  for (let i = 0; i < glob.length; i++) {
    const char = glob[i];
    if (char === '*' && glob[i + 1] === '*') {
      const atStart = i === 0 || glob[i - 1] === '/';
      if (atStart && glob[i + 2] === '/') {
        out += '(?:.*/)?';
        i += 2;
      } else {
        out += '.*';
        i += 1;
      }
    } else if (char === '*') {
      out += '[^/]*';
    } else if (char === '?') {
      out += '[^/]';
    } else if (char === '[' && glob.indexOf(']', i + 1) > i) {
      const end = glob.indexOf(']', i + 1);
      out += glob.slice(i, end + 1);
      i = end;
    } else {
      out += char.replace(/[.+^${}()|[\]\\]/g, '\\$&');
    }
  }
  return out;
}

function lastMatch(rules: Rule[], path: string, isDir: boolean): boolean {
  let ignored = false;
  for (const rule of rules) {
    if (rule.dirOnly && !isDir) continue;
    const rel = rule.base === '' ? path : path.startsWith(`${rule.base}/`) ? path.slice(rule.base.length + 1) : null;
    if (rel === null || !rule.test.test(rel)) continue;
    ignored = !rule.negate;
  }
  return ignored;
}

/** Ignored by the rules: a folder that is ignored takes everything inside it; the last rule that matches decides. */
export function isIgnored(rules: Rule[], path: string, isDir: boolean): boolean {
  const parts = path.split('/');
  for (let depth = 1; depth < parts.length; depth++) {
    if (lastMatch(rules, parts.slice(0, depth).join('/'), true)) return true;
  }
  return lastMatch(rules, path, isDir);
}

function isBinary(bytes: Uint8Array): boolean {
  const end = Math.min(bytes.length, SNIFF_BYTES);
  for (let i = 0; i < end; i++) if (bytes[i] === 0) return true;
  return false;
}

class Tree {
  private own = new Map<string, Rule[]>();

  constructor(private root: FileSystemDirectoryHandle) {}

  async folder(path: string): Promise<FileSystemDirectoryHandle> {
    let handle = this.root;
    for (const part of path ? path.split('/') : []) handle = await handle.getDirectoryHandle(part);
    return handle;
  }

  async file(path: string): Promise<FileSystemFileHandle> {
    const parts = path.split('/');
    const name = parts.pop() as string;
    let handle = this.root;
    for (const part of parts) handle = await handle.getDirectoryHandle(part);
    return handle.getFileHandle(name);
  }

  /** The rules of the `.gitignore` in one folder (none when it has none). */
  async ownRules(handle: FileSystemDirectoryHandle, path: string): Promise<Rule[]> {
    const known = this.own.get(path);
    if (known) return known;
    let rules: Rule[] = [];
    try {
      const file = await (await handle.getFileHandle('.gitignore')).getFile();
      rules = parseIgnore(path, await file.text());
    } catch {
      rules = []; // no .gitignore here
    }
    this.own.set(path, rules);
    return rules;
  }

  /** The rules that apply inside `path`: the `.gitignore` of each folder from the top down to it. */
  async rulesIn(path: string): Promise<Rule[]> {
    const rules = [...(await this.ownRules(this.root, ''))];
    let handle = this.root;
    let here = '';
    for (const part of path ? path.split('/') : []) {
      handle = await handle.getDirectoryHandle(part);
      here = here ? `${here}/${part}` : part;
      rules.push(...(await this.ownRules(handle, here)));
    }
    return rules;
  }
}

type Entry = { path: string; isDir: boolean; hidden: boolean; ignored: boolean; handle: FileSystemHandle };
type Budget = { scanned: number; cut: boolean };

/** Every entry below `dirPath`, depth first. A hidden or ignored folder is not walked unless `includeHidden`. */
async function* walk(
  tree: Tree,
  dir: FileSystemDirectoryHandle,
  dirPath: string,
  rules: Rule[],
  includeHidden: boolean,
  budget: Budget,
): AsyncGenerator<Entry> {
  for await (const [name, handle] of dir.entries()) {
    if (budget.cut) return;
    budget.scanned += 1;
    if (budget.scanned > SCAN_LIMIT) {
      budget.cut = true;
      return;
    }
    const path = dirPath ? `${dirPath}/${name}` : name;
    const isDir = handle.kind === 'directory';
    const hidden = name.startsWith('.');
    const ignored = !hidden && isIgnored(rules, path, isDir);
    yield { path, isDir, hidden, ignored, handle };
    if (isDir && (includeHidden || (!hidden && !ignored))) {
      const sub = handle as FileSystemDirectoryHandle;
      const subRules = [...rules, ...(await tree.ownRules(sub, path))];
      yield* walk(tree, sub, path, subRules, includeHidden, budget);
    }
  }
}

function notes(budget: Budget, matches: number, limit: number, what: string): string[] {
  const out: string[] = [];
  if (matches >= limit) out.push(`[the first ${limit} ${what}: narrow the pattern or the folder]`);
  if (budget.cut) out.push(`[the search stopped after ${SCAN_LIMIT.toLocaleString('en')} entries: search a smaller folder]`);
  return out;
}

function answer(lines: string[], clear: string[], extra: string[]): Answer {
  return { text: [...lines, ...extra].join('\n') || 'Nothing here.', clear };
}

async function list(tree: Tree, args: Record<string, any>): Promise<Answer> {
  const path = cleanPath(args.path);
  const dir = await tree.folder(path);
  const rules = await tree.rulesIn(path);
  const found: { name: string; isDir: boolean }[] = [];
  for await (const [name, handle] of dir.entries()) found.push({ name, isDir: handle.kind === 'directory' });
  found.sort((a, b) => (a.isDir === b.isDir ? a.name.localeCompare(b.name) : a.isDir ? -1 : 1));
  const lines: string[] = [];
  const clear: string[] = [];
  for (const entry of found.slice(0, LIST_LIMIT)) {
    const childPath = path ? `${path}/${entry.name}` : entry.name;
    const hidden = entry.name.startsWith('.');
    const ignored = !hidden && isIgnored(rules, childPath, entry.isDir);
    lines.push(`${childPath}${entry.isDir ? '/' : ''}${hidden ? '  [hidden]' : ''}${ignored ? '  [ignored]' : ''}`);
    if (!entry.isDir && !hidden && !ignored) clear.push(childPath);
  }
  const extra = found.length > LIST_LIMIT ? [`[${LIST_LIMIT} of ${found.length} entries shown]`] : [];
  return answer(lines, clear, extra);
}

async function find(tree: Tree, args: Record<string, any>): Promise<Answer> {
  const path = cleanPath(args.path);
  const pattern = String(args.pattern ?? '').trim();
  if (!pattern) throw new Error('the pattern is required');
  const test = nameRegex(pattern);
  const includeHidden = args.include_hidden === true;
  const start = await tree.folder(path);
  const rules = await tree.rulesIn(path);
  const budget: Budget = { scanned: 0, cut: false };
  const lines: string[] = [];
  const clear: string[] = [];
  for await (const entry of walk(tree, start, path, rules, includeHidden, budget)) {
    if (!includeHidden && (entry.hidden || entry.ignored)) continue;
    if (!test.test(entry.path.split('/').pop() ?? '')) continue;
    lines.push(`${entry.path}${entry.isDir ? '/' : ''}${entry.ignored ? '  [ignored]' : ''}`);
    if (!entry.isDir && !entry.hidden && !entry.ignored) clear.push(entry.path);
    if (lines.length >= MATCH_LIMIT) break;
  }
  return answer(lines, clear, notes(budget, lines.length, MATCH_LIMIT, 'matches'));
}

async function grep(tree: Tree, args: Record<string, any>): Promise<Answer> {
  const path = cleanPath(args.path);
  const pattern = String(args.pattern ?? '');
  if (!pattern) throw new Error('the pattern is required');
  let matches: (line: string) => boolean;
  if (args.regex === true) {
    let re: RegExp;
    try {
      re = new RegExp(pattern, 'i');
    } catch (error) {
      throw new Error(`not a regular expression: ${(error as Error).message}`);
    }
    matches = (line) => re.test(line);
  } else {
    const needle = pattern.toLowerCase();
    matches = (line) => line.toLowerCase().includes(needle);
  }
  const includeHidden = args.include_hidden === true;
  const start = await tree.folder(path);
  const rules = await tree.rulesIn(path);
  const budget: Budget = { scanned: 0, cut: false };
  const lines: string[] = [];
  const clear: string[] = [];
  for await (const entry of walk(tree, start, path, rules, includeHidden, budget)) {
    if (!includeHidden && (entry.hidden || entry.ignored)) continue;
    if (entry.isDir) continue;
    const file = await (entry.handle as FileSystemFileHandle).getFile();
    if (file.size > GREP_FILE_BYTES) continue;
    const bytes = new Uint8Array(await file.arrayBuffer());
    if (isBinary(bytes)) continue;
    const fileLines = new TextDecoder().decode(bytes).split(/\r?\n/);
    let hit = false;
    for (let index = 0; index < fileLines.length && lines.length < MATCH_LIMIT; index++) {
      if (!matches(fileLines[index])) continue;
      hit = true;
      const suffix = entry.ignored ? '  [ignored]' : '';
      lines.push(`${entry.path}:${index + 1}: ${fileLines[index].slice(0, LINE_CHARS)}${suffix}`);
    }
    if (hit && !entry.hidden && !entry.ignored) clear.push(entry.path);
    if (lines.length >= MATCH_LIMIT) break;
  }
  return answer(lines, clear, notes(budget, lines.length, MATCH_LIMIT, 'matching lines'));
}

async function read(tree: Tree, args: Record<string, any>): Promise<Answer> {
  const path = cleanPath(args.path);
  if (!path) throw new Error('the path of the file to read is required');
  const file = await (await tree.file(path)).getFile();
  if (file.size > READ_FILE_BYTES) throw new Error(`${path} is ${Math.round(file.size / 1_000_000)} MB: too big to read here`);
  const bytes = new Uint8Array(await file.arrayBuffer());
  if (isBinary(bytes)) throw new Error(`${path} is not a text file`);
  const lines = new TextDecoder().decode(bytes).split(/\r?\n/);
  const start = Math.max(1, Math.floor(Number(args.start_line) || 1));
  if (start > lines.length) return { text: `[the file has ${lines.length} lines]`, clear: [] };
  const out: string[] = [];
  let used = 0;
  let number = start;
  for (; number <= lines.length; number++) {
    const line = `${String(number).padStart(5)}  ${lines[number - 1]}`;
    if (used + line.length > READ_CHARS && out.length > 0) break;
    out.push(line);
    used += line.length + 1;
  }
  const more = number <= lines.length ? [`[read on with start_line=${number}]`] : [];
  return { text: [...out, ...more].join('\n'), clear: [] };
}

function describe(error: unknown): string {
  if (error instanceof DOMException) {
    if (error.name === 'NotFoundError') return 'No such file or folder in the folder you opened.';
    if (error.name === 'NotAllowedError') return 'The browser may not read this folder any more: open it again.';
    if (error.name === 'TypeMismatchError') return 'That name is a folder or a file, not the other.';
  }
  return error instanceof Error ? error.message : 'the folder could not be read';
}

/** Answers one folder tool call from the folder the person opened: the JSON the server settles. Never throws. */
export async function answerFolderCall(root: FileSystemDirectoryHandle, name: string, args: Record<string, any>): Promise<string> {
  const tree = new Tree(root);
  try {
    let result: Answer;
    if (name === 'folder_list') result = await list(tree, args ?? {});
    else if (name === 'folder_find') result = await find(tree, args ?? {});
    else if (name === 'folder_grep') result = await grep(tree, args ?? {});
    else if (name === 'folder_read') result = await read(tree, args ?? {});
    else throw new Error('this folder cannot do that');
    return JSON.stringify(result);
  } catch (error) {
    return JSON.stringify({ error: describe(error) });
  }
}

/** The byte budget a grant asks for: the number typed in megabytes (empty: the server's default). */
export function budgetBytes(megabytes: string): number | null {
  const value = Number(String(megabytes).replace(',', '.'));
  if (!String(megabytes).trim() || !Number.isFinite(value) || value < 0) return null;
  return Math.min(Math.round(value * 1_000_000), 100_000_000);
}
