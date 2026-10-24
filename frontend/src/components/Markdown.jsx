// The Markdown of release notes, drawn as React elements.
//
// Covers what the notes use: headings, paragraphs, nested lists, fenced code,
// tables, bold, inline code and links. Nothing is set as HTML, so a release
// note cannot put markup or script into the panel; links go only to https.
import React from 'react';

const LIST_RE = /^(\s*)([-*]|\d+\.)\s+(.*)$/;
const INLINE_RE = /(`[^`]+`)|(\*\*[^*]+\*\*)|(\[[^\]]+\]\((https:\/\/[^)\s]+)\))/g;

function inline(text, keyBase) {
  const out = [];
  let last = 0;
  let index = 0;
  for (const match of text.matchAll(INLINE_RE)) {
    if (match.index > last) out.push(text.slice(last, match.index));
    const key = `${keyBase}-${index++}`;
    if (match[1]) out.push(<code key={key}>{match[1].slice(1, -1)}</code>);
    else if (match[2]) out.push(<strong key={key}>{inline(match[2].slice(2, -2), key)}</strong>);
    else if (match[3]) {
      const label = match[3].slice(1, match[3].indexOf(']('));
      out.push(<a key={key} href={match[4]} target="_blank" rel="noopener noreferrer">{label}</a>);
    }
    last = match.index + match[0].length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

const cells = line => line.trim().replace(/^\|/, '').replace(/\|$/, '').split('|').map(cell => cell.trim());

function list(lines, start, keyBase) {
  // Items at the indent of the first line; deeper lines belong to the item
  // above them.
  const first = lines[start].match(LIST_RE);
  const indent = first[1].length;
  const ordered = /\d/.test(first[2]);
  const items = [];
  let i = start;
  while (i < lines.length) {
    const match = lines[i].match(LIST_RE);
    if (!match) {
      // A wrapped line of the item above.
      if (lines[i].trim() && /^\s+/.test(lines[i]) && items.length) {
        items[items.length - 1].text += ` ${lines[i].trim()}`;
        i++;
        continue;
      }
      break;
    }
    if (match[1].length < indent) break;
    if (match[1].length > indent) {
      const [child, next] = list(lines, i, `${keyBase}-${i}`);
      if (items.length) items[items.length - 1].children.push(child);
      i = next;
      continue;
    }
    items.push({ text: match[3], children: [] });
    i++;
  }
  const Tag = ordered ? 'ol' : 'ul';
  return [<Tag key={keyBase}>{items.map((item, n) =>
    <li key={n}>{inline(item.text, `${keyBase}-${n}`)}{item.children}</li>)}</Tag>, i];
}

export function Markdown({ text, className = 'markdown' }) {
  const lines = String(text || '').replace(/\r\n/g, '\n').split('\n');
  const blocks = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    const key = `b${i}`;
    if (!line.trim()) { i++; continue; }
    if (line.trim().startsWith('```')) {
      const body = [];
      i++;
      while (i < lines.length && !lines[i].trim().startsWith('```')) body.push(lines[i++]);
      i++;
      blocks.push(<pre key={key}><code>{body.join('\n')}</code></pre>);
      continue;
    }
    const heading = line.match(/^(#{1,6})\s+(.*)$/);
    if (heading) {
      // Inside a page that has its own h2, a note's "##" is a sub-heading.
      const Tag = `h${Math.min(6, heading[1].length + 2)}`;
      blocks.push(<Tag key={key}>{inline(heading[2], key)}</Tag>);
      i++;
      continue;
    }
    if (line.trim().startsWith('|') && i + 1 < lines.length && /^\s*\|?[\s:|-]+\|[\s:|-]*$/.test(lines[i + 1])) {
      const head = cells(line);
      const rows = [];
      i += 2;
      while (i < lines.length && lines[i].trim().startsWith('|')) rows.push(cells(lines[i++]));
      blocks.push(<div className="markdown-table" key={key}><table>
        <thead><tr>{head.map((cell, n) => <th key={n}>{inline(cell, `${key}-h${n}`)}</th>)}</tr></thead>
        <tbody>{rows.map((row, r) => <tr key={r}>{row.map((cell, n) => <td key={n}>{inline(cell, `${key}-${r}-${n}`)}</td>)}</tr>)}</tbody>
      </table></div>);
      continue;
    }
    if (LIST_RE.test(line)) {
      const [node, next] = list(lines, i, key);
      blocks.push(node);
      i = next;
      continue;
    }
    if (line.startsWith('>')) {
      const quote = [];
      while (i < lines.length && lines[i].startsWith('>')) quote.push(lines[i++].replace(/^>\s?/, ''));
      blocks.push(<blockquote key={key}>{inline(quote.join(' '), key)}</blockquote>);
      continue;
    }
    // The first line always goes in, so a stray "|" or "```" cannot stall the loop.
    const para = [line.trim()];
    i++;
    while (i < lines.length && lines[i].trim() && !LIST_RE.test(lines[i]) && !/^(#{1,6})\s/.test(lines[i])
           && !lines[i].trim().startsWith('```') && !lines[i].trim().startsWith('|')) para.push(lines[i++].trim());
    blocks.push(<p key={key}>{inline(para.join(' '), key)}</p>);
  }
  return <div className={className}>{blocks}</div>;
}

export default Markdown;
