// A message sent with documents is stored with them, as the server writes it (knowledge/attachments.py, `prepare`): what you
// wrote, a line naming the files, then each file in a <document> block. The site shows what you wrote and a chip per file, as it
// did while you were sending; the stored message stays whole (Clara reads the documents in it, and Ask again sends it back).

const BLOCK = /<document name="([^"\n]*)" type="([^"\n]*)">\n([\s\S]*?)\n<\/document>/g;

export type Document = { name: string; chars: number; block: string };
export type Sent = { text: string; documents: Document[] };

/** Your words, and the documents, of a stored message. A message with no document is its own text. */
export function unpack(content: string): Sent {
  const documents: Document[] = [];
  const rest = content.replace(BLOCK, (block, name: string, _kind: string, body: string) => {
    documents.push({ name, chars: body.length, block });
    return '';
  });
  if (!documents.length) return { text: content, documents };
  const names = documents.map((document) => document.name).join(', ');
  let text = rest;
  // the line that names the files comes after your words, just before the first block: the last one is the server's
  for (const line of [`(Attached: ${names})`, `Here is: ${names}.`, `Here are: ${names}.`]) {
    const at = text.lastIndexOf(line);
    if (at >= 0) {
      text = text.slice(0, at) + text.slice(at + line.length);
      break;
    }
  }
  return { text: text.replace(/\n{3,}/g, '\n\n').trim(), documents };
}

/** The stored form again, after your words were edited: the same documents, put back as the server puts them. */
export function repack(text: string, documents: Document[]): string {
  if (!documents.length) return text;
  const names = documents.map((document) => document.name).join(', ');
  const words = text.trim();
  const named = words ? `(Attached: ${names})` : `Here ${documents.length === 1 ? 'is' : 'are'}: ${names}.`;
  return [words, named, ...documents.map((document) => document.block)].filter(Boolean).join('\n\n');
}
