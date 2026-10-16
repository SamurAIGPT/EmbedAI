// A small Markdown parser for assistant answers. It produces plain data (no
// HTML strings), and the page turns that into React elements, so nothing an
// answer contains can become markup. It is safe on half-written text: an open
// code fence or an unfinished `**` while an answer streams just renders as it
// stands, and the next chunk re-parses the whole text.

export type Inline =
  | { type: "text"; text: string }
  | { type: "code"; text: string }
  | { type: "strong"; children: Inline[] }
  | { type: "em"; children: Inline[] }
  | { type: "del"; children: Inline[] }
  | { type: "link"; href: string; children: Inline[] };

export type Block =
  | { type: "paragraph"; children: Inline[] }
  | { type: "heading"; level: number; children: Inline[] }
  | { type: "code"; lang: string; text: string; closed: boolean }
  | { type: "quote"; blocks: Block[] }
  | { type: "list"; ordered: boolean; start: number; items: Inline[][] }
  | { type: "table"; header: Inline[][]; rows: Inline[][][]; align: (string | null)[] }
  | { type: "rule" };

const SAFE_PROTOCOLS = new Set(["http:", "https:", "mailto:"]);

// Only these links become clickable; anything else (javascript:, data:, ...) is
// shown as the text it was written with.
export function safeHref(raw: string): string | null {
  const href = raw.trim();
  if (!href) return null;
  try {
    const url = new URL(href);
    return SAFE_PROTOCOLS.has(url.protocol) ? url.href : null;
  } catch {
    return null;
  }
}

// Position of the first unescaped `needle` at or after `from`, or -1.
function indexOfUnescaped(text: string, needle: string, from: number): number {
  let i = from;
  while (i < text.length) {
    if (text[i] === "\\") {
      i += 2;
      continue;
    }
    if (text.startsWith(needle, i)) return i;
    i += 1;
  }
  return -1;
}

const ESCAPABLE = "\\`*_{}[]()#+-.!|~>";

export function parseInline(text: string): Inline[] {
  const out: Inline[] = [];
  let buffer = "";
  const flush = () => {
    if (buffer) out.push({ type: "text", text: buffer });
    buffer = "";
  };

  let i = 0;
  while (i < text.length) {
    const ch = text[i];

    if (ch === "\\" && i + 1 < text.length && ESCAPABLE.includes(text[i + 1])) {
      buffer += text[i + 1];
      i += 2;
      continue;
    }

    if (ch === "`") {
      let ticks = 1;
      while (text[i + ticks] === "`") ticks += 1;
      const fence = "`".repeat(ticks);
      const end = text.indexOf(fence, i + ticks);
      if (end !== -1) {
        flush();
        out.push({ type: "code", text: text.slice(i + ticks, end).replace(/^ (.*) $/, "$1") });
        i = end + ticks;
        continue;
      }
      buffer += fence;
      i += ticks;
      continue;
    }

    const doubles: ReadonlyArray<readonly [string, "strong" | "del"]> = [
      ["**", "strong"],
      ["__", "strong"],
      ["~~", "del"],
    ];
    let matched = false;
    for (const [marker, type] of doubles) {
      if (!text.startsWith(marker, i)) continue;
      const end = indexOfUnescaped(text, marker, i + 2);
      if (end > i + 2) {
        flush();
        out.push({ type, children: parseInline(text.slice(i + 2, end)) });
        i = end + 2;
        matched = true;
        break;
      }
    }
    if (matched) continue;

    if (ch === "*" || ch === "_") {
      const next = text[i + 1];
      const prev = i > 0 ? text[i - 1] : " ";
      // `_` inside a word (snake_case) is not emphasis.
      const wordInside = ch === "_" && /\w/.test(prev);
      if (next !== undefined && next !== " " && next !== ch && !wordInside) {
        const end = indexOfUnescaped(text, ch, i + 1);
        if (end > i + 1 && text[end - 1] !== " " && !(ch === "_" && /\w/.test(text[end + 1] ?? " "))) {
          flush();
          out.push({ type: "em", children: parseInline(text.slice(i + 1, end)) });
          i = end + 1;
          continue;
        }
      }
    }

    if (ch === "[") {
      const close = indexOfUnescaped(text, "]", i + 1);
      if (close !== -1 && text[close + 1] === "(") {
        const paren = text.indexOf(")", close + 2);
        if (paren !== -1) {
          const label = text.slice(i + 1, close);
          const target = text.slice(close + 2, paren).trim().split(/\s+/)[0] ?? "";
          const href = safeHref(target);
          flush();
          if (href) out.push({ type: "link", href, children: parseInline(label) });
          else out.push(...parseInline(label));
          i = paren + 1;
          continue;
        }
      }
    }

    // A bare web address becomes a link.
    if ((ch === "h" || ch === "H") && /^https?:\/\//i.test(text.slice(i, i + 8))) {
      const match = /^https?:\/\/[^\s<>"')\]]+/i.exec(text.slice(i));
      if (match) {
        const trimmed = match[0].replace(/[.,;:!?]+$/, "");
        const href = safeHref(trimmed);
        if (href) {
          flush();
          out.push({ type: "link", href, children: [{ type: "text", text: trimmed }] });
          i += trimmed.length;
          continue;
        }
      }
    }

    buffer += ch;
    i += 1;
  }
  flush();
  return out;
}

const FENCE = /^ {0,3}(`{3,}|~{3,})\s*([^\s`]*)[^`]*$/;
const HEADING = /^ {0,3}(#{1,6})\s+(.*?)\s*#*\s*$/;
const RULE = /^ {0,3}([-*_])(?:\s*\1){2,}\s*$/;
const BULLET = /^( {0,3})[-*+]\s+(.*)$/;
const ORDERED = /^( {0,3})(\d{1,9})[.)]\s+(.*)$/;
const QUOTE = /^ {0,3}>\s?(.*)$/;

function splitRow(line: string): string[] {
  let row = line.trim();
  if (row.startsWith("|")) row = row.slice(1);
  if (row.endsWith("|") && !row.endsWith("\\|")) row = row.slice(0, -1);
  const cells: string[] = [];
  let cell = "";
  for (let i = 0; i < row.length; i += 1) {
    if (row[i] === "\\" && row[i + 1] === "|") {
      cell += "|";
      i += 1;
    } else if (row[i] === "|") {
      cells.push(cell.trim());
      cell = "";
    } else cell += row[i];
  }
  cells.push(cell.trim());
  return cells;
}

const DELIMITER_CELL = /^:?-{1,}:?$/;

function isTableStart(lines: string[], i: number): boolean {
  if (i + 1 >= lines.length || !lines[i].includes("|")) return false;
  const delimiter = splitRow(lines[i + 1]);
  return (
    lines[i + 1].includes("-") &&
    delimiter.every((cell) => DELIMITER_CELL.test(cell)) &&
    delimiter.length === splitRow(lines[i]).length
  );
}

function startsBlock(line: string): boolean {
  return (
    FENCE.test(line) ||
    HEADING.test(line) ||
    RULE.test(line) ||
    QUOTE.test(line) ||
    BULLET.test(line) ||
    ORDERED.test(line)
  );
}

export function parseMarkdown(source: string): Block[] {
  const lines = source.replace(/\r\n?/g, "\n").split("\n");
  const blocks: Block[] = [];
  let i = 0;

  while (i < lines.length) {
    const line = lines[i];

    if (line.trim() === "") {
      i += 1;
      continue;
    }

    const fence = FENCE.exec(line);
    if (fence) {
      const marker = fence[1];
      const body: string[] = [];
      i += 1;
      let closed = false;
      while (i < lines.length) {
        if (new RegExp(`^ {0,3}${marker[0]}{${marker.length},}\\s*$`).test(lines[i])) {
          closed = true;
          i += 1;
          break;
        }
        body.push(lines[i]);
        i += 1;
      }
      blocks.push({ type: "code", lang: fence[2], text: body.join("\n"), closed });
      continue;
    }

    const heading = HEADING.exec(line);
    if (heading) {
      blocks.push({ type: "heading", level: heading[1].length, children: parseInline(heading[2]) });
      i += 1;
      continue;
    }

    if (RULE.test(line)) {
      blocks.push({ type: "rule" });
      i += 1;
      continue;
    }

    if (QUOTE.test(line)) {
      const inner: string[] = [];
      while (i < lines.length && QUOTE.test(lines[i])) {
        inner.push(QUOTE.exec(lines[i])![1]);
        i += 1;
      }
      blocks.push({ type: "quote", blocks: parseMarkdown(inner.join("\n")) });
      continue;
    }

    const bullet = BULLET.exec(line);
    const ordered = ORDERED.exec(line);
    if (bullet || ordered) {
      const isOrdered = ordered !== null && bullet === null;
      const start = isOrdered ? Number(ordered![2]) : 1;
      const items: Inline[][] = [];
      while (i < lines.length) {
        const b = BULLET.exec(lines[i]);
        const o = ORDERED.exec(lines[i]);
        if (isOrdered ? o && !b : b && !RULE.test(lines[i])) {
          items.push(parseInline(isOrdered ? o![3] : b![2]));
          i += 1;
          // A continuation line is indented and belongs to the item above.
          while (i < lines.length && /^ {2,}\S/.test(lines[i]) && !BULLET.test(lines[i]) && !ORDERED.test(lines[i])) {
            items[items.length - 1].push({ type: "text", text: " " }, ...parseInline(lines[i].trim()));
            i += 1;
          }
        } else break;
      }
      blocks.push({ type: "list", ordered: isOrdered, start, items });
      continue;
    }

    if (isTableStart(lines, i)) {
      const header = splitRow(line).map(parseInline);
      const align = splitRow(lines[i + 1]).map((cell) => {
        const left = cell.startsWith(":");
        const right = cell.endsWith(":");
        return left && right ? "center" : right ? "right" : left ? "left" : null;
      });
      i += 2;
      const rows: Inline[][][] = [];
      while (i < lines.length && lines[i].trim() !== "" && lines[i].includes("|")) {
        const cells = splitRow(lines[i]);
        rows.push(header.map((_, col) => parseInline(cells[col] ?? "")));
        i += 1;
      }
      blocks.push({ type: "table", header, rows, align });
      continue;
    }

    // A paragraph runs to a blank line or the start of another block.
    const para: string[] = [line.trim()];
    i += 1;
    while (i < lines.length && lines[i].trim() !== "" && !startsBlock(lines[i]) && !isTableStart(lines, i)) {
      para.push(lines[i].trim());
      i += 1;
    }
    const inlines: Inline[] = [];
    para.forEach((text, index) => {
      if (index > 0) inlines.push({ type: "text", text: "\n" });
      inlines.push(...parseInline(text));
    });
    blocks.push({ type: "paragraph", children: inlines });
  }

  return blocks;
}
