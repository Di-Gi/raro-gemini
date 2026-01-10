// [[RARO]]/apps/web-console/src/lib/markdown.ts
import { marked } from 'marked';

// Configure renderer to match our CSS variables
const renderer = new marked.Renderer();

// 1. Links: Add accent color and underline
renderer.link = ({ href, title, text }) => {
  return `<a href="${href}" title="${title || ''}" target="_blank" rel="noopener noreferrer" class="md-link">${text}</a>`;
};

// 2. Blockquotes: Style like our error/info blocks
renderer.blockquote = ({ text }) => {
  return `<blockquote class="md-quote">${text}</blockquote>`;
};

marked.setOptions({
  renderer,
  gfm: true, // GitHub Flavored Markdown (tables, etc)
  breaks: true // Enter key = new line
});

export function parseMarkdown(text: string): string {
  if (!text) return '';
  // FIX: Handle literal escaped newlines ("\n") often sent by JSON loggers
  // converting them to actual newlines so 'breaks: true' can render <br>
  const processed = text.replace(/\\n/g, '\n');
  return marked.parse(processed) as string;
}