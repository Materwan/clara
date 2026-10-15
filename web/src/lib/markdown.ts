// Markdown with LaTeX. Formulas are taken out before the markdown is parsed (so `_` and `*` inside them survive),
// the HTML is sanitised (no scripts, no style attributes, no event handlers), and each formula is drawn by KaTeX
// with DOM calls, which the site's strict Content-Security-Policy allows.

import DOMPurify from 'dompurify';
import katex from 'katex';
import { marked } from 'marked';

marked.setOptions({ gfm: true, breaks: false });

const MATH = /\$\$([\s\S]+?)\$\$|\\\[([\s\S]+?)\\\]|\\\(([\s\S]+?)\\\)|(?<![\\$\w])\$(?!\s)([^$\n]+?)(?<!\s)\$(?!\w)/g;
const FENCE = /(```[\s\S]*?```|`[^`\n]+`)/g;

export function render(text: string): { html: string; formulas: { tex: string; display: boolean }[] } {
  const formulas: { tex: string; display: boolean }[] = [];
  const pieces = text.split(FENCE).map((piece, index) => {
    if (index % 2 === 1) return piece; // code: never touched
    return piece.replace(MATH, (_all, block, bracket, paren, inline) => {
      const display = block !== undefined || bracket !== undefined;
      formulas.push({ tex: (block ?? bracket ?? paren ?? inline ?? '').trim(), display });
      return display ? `\n\n<div data-math="${formulas.length - 1}"></div>\n\n` : `<span data-math="${formulas.length - 1}"></span>`;
    });
  });
  const html = DOMPurify.sanitize(marked.parse(pieces.join('')) as string, {
    FORBID_ATTR: ['style'],
    FORBID_TAGS: ['style', 'form', 'input', 'button', 'iframe'],
    ADD_ATTR: ['target'],
  });
  return { html, formulas };
}

/** A Svelte action: fills the node with rendered markdown, then draws its formulas. */
export function markdown(node: HTMLElement, text: string) {
  const draw = (value: string) => {
    const { html, formulas } = render(value ?? '');
    node.innerHTML = html;
    node.querySelectorAll<HTMLElement>('[data-math]').forEach((target) => {
      const formula = formulas[Number(target.dataset.math)];
      if (!formula) return;
      try {
        katex.render(formula.tex, target, { displayMode: formula.display, throwOnError: false, output: 'html' });
      } catch {
        target.textContent = formula.tex;
      }
    });
    node.querySelectorAll('a').forEach((link) => {
      link.setAttribute('rel', 'noopener noreferrer');
      link.setAttribute('target', '_blank');
    });
  };
  draw(text);
  return { update: draw };
}

/** For plain text with formulas (QCM questions): escaped, then the formulas drawn. */
export function inlineMath(node: HTMLElement, text: string) {
  const draw = (value: string) => {
    node.textContent = '';
    let last = 0;
    for (const match of (value ?? '').matchAll(MATH)) {
      node.append(document.createTextNode(value.slice(last, match.index)));
      const span = document.createElement('span');
      const display = match[1] !== undefined || match[2] !== undefined;
      try {
        katex.render((match[1] ?? match[2] ?? match[3] ?? match[4] ?? '').trim(), span, { displayMode: display, throwOnError: false });
      } catch {
        span.textContent = match[0];
      }
      node.append(span);
      last = (match.index ?? 0) + match[0].length;
    }
    node.append(document.createTextNode(value.slice(last)));
  };
  draw(text);
  return { update: draw };
}
