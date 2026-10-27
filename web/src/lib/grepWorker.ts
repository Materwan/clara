// Runs the regular expression of a `folder_grep` away from the page. The pattern is the model's, so it may backtrack
// without end: the page ends this worker when it takes too long (folders.ts), and the tab never waits for it.
type Job = { pattern: string; lines: string[]; room: number };

self.onmessage = (event: MessageEvent<Job>) => {
  const { pattern, lines, room } = event.data;
  const re = new RegExp(pattern, 'i');
  const found: number[] = [];
  for (let index = 0; index < lines.length && found.length < room; index++) {
    if (re.test(lines[index])) found.push(index);
  }
  self.postMessage(found);
};
