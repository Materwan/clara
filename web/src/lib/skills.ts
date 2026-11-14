// Skills, without the pages: the "/" menu of the chat (the word being typed, the rows, the keys, what goes in when one is chosen), the line
// under the box (the packs a skill needs), how a stored command is drawn and sent again, and the checks and the body of adding a skill.
// The server checks everything again (api/skills.py, skills/parse.py); this only says it where you are looking.

import { type Document, type Sent, repack, unpack } from './documents';

export const MAX_SKILLS = 30;
export const MAX_ZIP = 1_400_000; // bytes of the archive itself (skills/parse.py MAX_ZIP; base64 makes it fit the 2 MB request)
export const MAX_PROJECTS = 50;
export const SHOWN_CHARS = 20_000; // the text of a file shown on the page: the rest is not drawn

export type Skill = {
  name: string; description: string; argument_hint: string; packs: string[]; disable_model_invocation: boolean;
  scope: 'global' | 'projects'; projects: number[]; file_count: number; size: number; created_at: string; created_by: string;
};
export type Chip = { id: string; label: string; on: boolean; available: boolean; reason: string };
export type Project = { id: number; name: string };

// ------------------------------------------------------------------------------------------------ the "/" menu

/** The word being typed after the slash, when the text starts with `/` and the caret is still inside that first word (the slash and its end included). */
export function slashWord(text: string, caret: number): { query: string; end: number } | null {
  if (!text.startsWith('/')) return null;
  const space = text.search(/\s/);
  const end = space < 0 ? text.length : space;
  return caret >= 1 && caret <= end ? { query: text.slice(1, end), end } : null;
}

/** The skills that fit what was typed: the names that start with it first, then those that contain it, then those whose description does; each group in name order. */
export function filterSkills<T extends { name: string; description: string }>(skills: T[], query: string): T[] {
  const word = query.toLowerCase();
  const rank = (skill: T): number =>
    skill.name.startsWith(word) ? 0 : skill.name.includes(word) ? 1 : skill.description.toLowerCase().includes(word) ? 2 : 3;
  return skills.filter((skill) => rank(skill) < 3).sort((a, b) => rank(a) - rank(b) || (a.name < b.name ? -1 : a.name > b.name ? 1 : 0));
}

export type MenuKey = { to: number } | { pick: number } | { close: true } | null;

/** What a key does while the menu is open (null: nothing, the box handles it). Enter sends when the word is already the name of the row it is on. */
export function menuKey(key: string, shift: boolean, names: string[], at: number, query: string): MenuKey {
  const n = names.length;
  if (!n) return null;
  if (key === 'ArrowDown') return { to: (at + 1) % n };
  if (key === 'ArrowUp') return { to: (at + n - 1) % n };
  if (key === 'Escape') return { close: true };
  if (key === 'Tab' && !shift) return { pick: at };
  if (key === 'Enter' && !shift) return names[at] === query ? null : { pick: at };
  return null;
}

/** The text with its first word made `/name` and a space after it (kept if there is one), and where the caret goes. */
export function insertSkill(text: string, name: string): { text: string; caret: number } {
  const space = text.search(/\s/);
  const rest = space < 0 ? '' : text.slice(space);
  const head = `/${name}`;
  return { text: head + (/^\s/.test(rest) ? rest : ` ${rest}`), caret: head.length + 1 };
}

/** The skill the first word names exactly, followed by a space, a line break or the end (the server's own rule): the one the line under the box is about. */
export function hintSkill<T extends { name: string }>(text: string, skills: T[]): T | null {
  const word = /^\/(\S*)(?:\s|$)/.exec(text)?.[1];
  return word ? (skills.find((skill) => skill.name === word) ?? null) : null;
}

/** The packs of the line under the box. The server says which are on in a conversation that exists; one not started has the packs you hold here. */
export function chipsFor(packs: Chip[], active: string[], started: boolean): Chip[] {
  return packs.map((pack) => ({ ...pack, on: active.includes(pack.id) || (started && pack.on) }));
}

/** The names of the projects a skill is reserved for (a project that no longer exists is its number). */
export const projectNames = (skill: Pick<Skill, 'projects'>, projects: Project[]): string =>
  skill.projects.map((id) => projects.find((project) => project.id === id)?.name ?? `#${id}`).join(', ');

/** Where a skill is kept, in words: `Everywhere`, or the projects (their names when they are few). */
export function scopeLabel(skill: Pick<Skill, 'scope' | 'projects'>, projects: Project[]): string {
  if (skill.scope === 'global') return 'Everywhere';
  const n = skill.projects.length;
  if (!n) return 'No project left: it is visible nowhere';
  const names = projectNames(skill, projects);
  const count = `${n} project${n === 1 ? '' : 's'}`;
  return names.length <= 40 ? `${count}: ${names}` : count;
}

/** The id of a row of the menu: the box names the row it is on (aria-activedescendant). */
export const optionId = (name: string): string => `skill-opt-${name}`;

// ------------------------------------------------------------------------------------------------ a command, drawn and sent again

/** A stored question that the server shows as a skill command (`shown` starts with `/`): the other `shown`s (an automation's origin) are not drawn instead of the question. */
export const isCommand = (shown: string | undefined): shown is string => typeof shown === 'string' && shown.startsWith('/');

/** The instructions of a skill Clara ran herself: a stored user row, right after her tool call, whose `shown` starts with this (memory/conversations.py `RAN`). It is
 *  no question: the run line of the call already says it, so the chat draws nothing for it and it never starts an exchange. */
export const isRan = (shown: string | undefined): boolean => typeof shown === 'string' && shown.startsWith('Clara ran /');

/** What you typed, and the documents that came with it. `shown` ends with `(Attached: a.txt)` when files came; their text is in the stored question
 *  (`content`, after the expansion), or in `shown` itself when the command was sent again with them. */
export function typedCommand(shown: string, content: string): Sent {
  const own = unpack(shown);
  if (own.documents.length) return own;
  return { text: shown.replace(/\n\n\(Attached: [^\n]*\)$/, ''), documents: unpack(content).documents };
}

/** What is sent when a question is asked again: a command goes again as typed (never as the instructions it became, which would be sent as plain text). */
export function resendOf(message: { command?: boolean; shown?: string; content: string; documents?: Document[] }): string {
  return message.command ? repack(message.shown ?? '', message.documents ?? []) : message.content;
}

// ------------------------------------------------------------------------------------------------ adding one

export type Source = { skill_md: string; name?: string } | { zip_base64: string; name?: string };
export type Scope = { kind: 'global' } | { kind: 'projects'; ids: number[] };

/** The body of `POST /v1/skills/preview`. `name` goes only when there is one (the frontmatter's wins, and the preview says so). */
export function previewSource(how: 'paste' | 'zip', text: string, zip: string, name: string): Source {
  const given = name.trim();
  return { ...(how === 'paste' ? { skill_md: text } : { zip_base64: zip }), ...(given ? { name: given } : {}) };
}

/** The body of `POST /v1/skills`: the very input that was previewed, where it goes, and whether it replaces. */
export function addBody(source: Source, scope: Scope, replace: boolean): Source & { scope: 'global' | { projects: number[] }; replace: boolean } {
  return { ...source, scope: scope.kind === 'global' ? 'global' : { projects: scope.ids }, replace };
}

/** Why a scope cannot be added yet, or ''. */
export function scopeProblem(scope: Scope): string {
  if (scope.kind === 'global') return '';
  if (!scope.ids.length) return 'Choose at least one project.';
  return scope.ids.length > MAX_PROJECTS ? `A skill is reserved for ${MAX_PROJECTS} projects at most.` : '';
}

/** What is wrong with the size of a zip, in the server's words, or ''. */
export const zipProblem = (bytes: number): string => (bytes > MAX_ZIP ? `The archive is ${bytes.toLocaleString('en-US')} bytes: at most ${MAX_ZIP.toLocaleString('en-US')}.` : '');

/** The text of a file as far as the page draws it, and how many characters are left out. */
export const cap = (text: string, max = SHOWN_CHARS): { text: string; hidden: number } =>
  text.length > max ? { text: text.slice(0, max), hidden: text.length - max } : { text, hidden: 0 };

export const held = (n: number): string => `${n} of ${MAX_SKILLS} skills`;
