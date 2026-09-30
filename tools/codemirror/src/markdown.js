// Markdown block structure and list edits, line by line: a port of
// src/launcher/notes_markdown.py so the web editor formats notes exactly as the rest of
// the app reads them (outline, PDF, search). tests/test_notes_web_parity.py checks the
// two agree. Columns are string indexes (UTF-16 units, as CodeMirror counts).

export const ATTACHMENTS = "attachments";
export const INDENT = "\t";
export const MAX_DEPTH = 8;
export const LIST_KINDS = ["bullet", "task", "ordered"];

const FENCE = /^ {0,3}(`{3,}|~{3,})(.*)$/;
const RULE = /^ {0,3}([-*_])(?:[ \t]*\1){2,}[ \t]*$/;
const HEADING = /^ {0,3}(#{1,6})[ \t]+/;
const TASK = /^([ \t]*)([-*+])[ \t]+\[([ xX])\](?:[ \t]+|$)/;
const BULLET = /^([ \t]*)([-*+])[ \t]+/;
const ORDERED = /^([ \t]*)(\d{1,9})([.)])[ \t]+/;
const ALPHA_ORDERED = /^([ \t]+)([a-z]{1,15})([.)])[ \t]+/;
const QUOTE = /^ {0,3}((?:>[ \t]?)+)/;
const TASK_SHORTHAND = /^([ \t]*)(?:[-*+][ \t]+)?\[ ?\][ \t]/;
const RETYPE = /^([ \t]*)(\d{1,9}[.)]|[a-z]{1,15}[.)]|[-*+])[ \t]+(1[.)]|[ai][.)]|[-*+])[ \t]$/;
const DESTINATION = String.raw`(<[^<>\n]*>|[^\s()<>]+)`;
const IMAGE_LINE = new RegExp(String.raw`^\s*!\[[^\]\n]*\]\(` + DESTINATION + String.raw`\)\s*$`);

const isList = (kind) => LIST_KINDS.includes(kind);

function info(kind, fields = {}) {
  return {
    kind, depth: 0, level: 0, hidden: 0, marker: 0, content: 0, number: 0, style: "",
    delim: "", checked: false, indent: "", url: "", ...fields,
  };
}

const BLANK = info("blank");
const TEXT = info("text");
const CODE = info("code");

export function classify(lines) {
  const infos = [];
  let fence = null;
  // The enclosing list items: [indent width, numbered lists up to it, style, number].
  const stack = [];
  for (const line of lines) {
    if (fence !== null) {
      const m = FENCE.exec(line);
      if (m && m[1][0] === fence[0] && m[1].length >= fence.length && !m[2].trim()) {
        infos.push(info("fence", { marker: line.length }));
        fence = null;
      } else {
        infos.push(CODE);
      }
      continue;
    }
    let it = classifyLine(line);
    if (it.kind === "text" && line.trim() === ">" && infos.length && infos.at(-1).kind === "quote") {
      it = info("quote", { depth: 1, hidden: line.length, content: line.length });
    }
    if (it.kind === "fence") fence = FENCE.exec(line)[1];
    if (it.kind === "text") it = lettered(line, stack) || it;
    if (isList(it.kind)) {
      const width = indentWidth(it.indent);
      while (stack.length && width < stack.at(-1)[0]) stack.pop();
      if (stack.length && stack.at(-1)[0] === width) stack.pop();
      const level = stack.length ? stack.at(-1)[1] : 0;
      const depth = Math.min(stack.length, MAX_DEPTH);
      if (it.depth !== depth) it = { ...it, depth };
      stack.push([width, level + (it.kind === "ordered" ? 1 : 0), it.style, it.number]);
    } else if (it.kind !== "blank") {
      stack.length = 0;
    }
    infos.push(it);
  }
  return infos;
}

function lettered(line, stack) {
  const m = ALPHA_ORDERED.exec(line);
  if (!m) return null;
  const width = indentWidth(m[1]);
  const parents = stack.filter((e) => e[0] < width);
  const level = parents.length ? parents.at(-1)[1] : 0;
  const same = stack.find((e) => e[0] === width);
  const before = same ? [same[2], same[3]] : ["", 0];
  const token = m[2];
  let style, number, short;
  if (level % 3 === 1) {
    style = "a";
    number = lettersValue(token);
    short = token.length === 1;
  } else if (level % 3 === 2) {
    style = "i";
    number = romanValue(token);
    short = number !== null && number <= 39;
  } else {
    return null;
  }
  if (number === null) return null;
  const follows = before[0] === style && (before[1] === number - 1 || before[1] === number);
  if (!(short || follows)) return null;
  const end = m[0].length;
  return info("ordered", {
    hidden: m[1].length, marker: end, content: end, number, style, delim: m[3], indent: m[1],
  });
}

function indentWidth(indent) {
  let w = 0;
  for (const ch of indent) w += ch === "\t" ? 4 : 1;
  return w;
}

// Lines seldom change between keystrokes: the per-line work is cached, like the lru_cache
// in Python. Infos are never mutated, so sharing them is safe.
const lineCache = new Map();

function classifyLine(line) {
  let it = lineCache.get(line);
  if (it === undefined) {
    it = classifyLineUncached(line);
    if (lineCache.size > 32768) lineCache.clear();
    lineCache.set(line, it);
  }
  return it;
}

function classifyLineUncached(line) {
  let m;
  if (!line.trim()) return BLANK;
  if ((m = IMAGE_LINE.exec(line))) return info("image", { marker: line.length, url: destination(m[1]) });
  if ((m = FENCE.exec(line))) {
    if (!m[2].includes("`") || m[1][0] === "~") return info("fence", { marker: line.length });
  }
  if (RULE.test(line)) return info("rule", { marker: line.length });
  if ((m = HEADING.exec(line))) {
    return info("heading", { level: m[1].length, marker: m[0].length, content: m[0].length });
  }
  if ((m = TASK.exec(line))) {
    return info("task", {
      hidden: m[0].length, content: m[0].length, delim: m[2], checked: "xX".includes(m[3]), indent: m[1],
    });
  }
  if ((m = BULLET.exec(line))) {
    return info("bullet", { hidden: m[0].length, content: m[0].length, delim: m[2], indent: m[1] });
  }
  if ((m = ORDERED.exec(line))) {
    return info("ordered", {
      hidden: m[1].length, marker: m[0].length, content: m[0].length, number: parseInt(m[2], 10),
      style: "1", delim: m[3], indent: m[1],
    });
  }
  if ((m = QUOTE.exec(line))) {
    const prefix = m[1];
    if (prefix.endsWith(" ") || prefix.endsWith("\t")) {
      const depth = prefix.split(">").length - 1;
      return info("quote", { depth, hidden: m[0].length, content: m[0].length });
    }
  }
  return TEXT;
}

export function isEmptyItem(line, it) {
  return (isList(it.kind) || it.kind === "quote") && !line.slice(it.content).trim();
}

export function continuation(line, it) {
  if (it.kind === "bullet") return `${it.indent}${it.delim} `;
  if (it.kind === "task") return `${it.indent}${it.delim} [ ] `;
  if (it.kind === "ordered") return `${it.indent}${formatNumber(it.number + 1, it.style)}${it.delim} `;
  if (it.kind === "quote") return "> ".repeat(it.depth);
  return null;
}

export const indent = (line) => INDENT + line;

export function outdent(line) {
  if (line.startsWith("\t")) return line.slice(1);
  const spaces = line.length - line.replace(/^ +/, "").length;
  return line.slice(Math.min(spaces, 4));
}

export function reindent(lines, n, change) {
  const line = lines[n];
  const moved = change(line);
  const it = classify(lines.slice(0, n + 1))[n];
  if (it.kind !== "ordered" || moved === line) return moved;
  const newIndent = moved.slice(0, moved.length - moved.replace(/^[ \t]+/, "").length);
  const text = line.slice(it.content);
  const space = line.slice(line.slice(0, it.content).trimEnd().length, it.content);
  const level = orderedLevels(classify([...lines.slice(0, n), `${newIndent}1. ${text}`]))[n];
  const style = LEVEL_STYLES[level % 3];
  const delim = style === it.style ? it.delim : DEFAULT_DELIMS[style];
  return `${newIndent}${formatNumber(1, style)}${delim}${space}${text}`;
}

export function withoutMarker(line, it) {
  if (isList(it.kind) || it.kind === "quote" || it.kind === "heading") return line.slice(it.content);
  return line;
}

export function toggleTask(line, it) {
  if (it.kind !== "task") return line;
  const box = line.indexOf("[", it.indent.length);
  return line.slice(0, box + 1) + (it.checked ? " " : "x") + line.slice(box + 2);
}

function taskShorthand(line) {
  const m = TASK_SHORTHAND.exec(line);
  if (!m || TASK.test(line)) return null;
  return [m[0].length, `${m[1]}- [ ] `];
}

function retypeShorthand(line) {
  const m = RETYPE.exec(line);
  if (!m || (m[3] === m[2] && "-*+".includes(m[2]))) return null;
  return [m[0].length, `${m[1]}${m[3]} `];
}

export function shorthand(lines, infos, n) {
  if (infos[n].kind === "code" || infos[n].kind === "fence") return null;
  const line = lines[n];
  let found = null;
  if (isList(infos[n].kind)) found = retypeShorthand(line);
  found = found || taskShorthand(line);
  if (!found) return null;
  const [length, replacement] = found;
  const candidate = classify([...lines.slice(0, n), replacement + line.slice(length)])[n];
  return isList(candidate.kind) ? found : null;
}

export function orderedLevels(infos) {
  const levels = [];
  const stack = [];
  for (const it of infos) {
    if (isList(it.kind)) {
      while (stack.length && stack.at(-1)[0] >= it.depth) stack.pop();
      const level = stack.length ? stack.at(-1)[1] : 0;
      levels.push(level);
      stack.push([it.depth, level + (it.kind === "ordered" ? 1 : 0)]);
    } else {
      if (it.kind !== "blank") stack.length = 0;
      levels.push(0);
    }
  }
  return levels;
}

const MAX_ROMAN = 3999;
const LEVEL_STYLES = ["1", "a", "i"];
const DEFAULT_DELIMS = { 1: ".", a: ")", i: "." };
const ROMAN_DIGITS = { i: 1, v: 5, x: 10, l: 50, c: 100, d: 500, m: 1000 };
const ROMAN_TABLE = [
  [1000, "m"], [900, "cm"], [500, "d"], [400, "cd"], [100, "c"], [90, "xc"], [50, "l"],
  [40, "xl"], [10, "x"], [9, "ix"], [5, "v"], [4, "iv"], [1, "i"],
];

function letters(n) {
  let out = "";
  while (n > 0) {
    const rest = (n - 1) % 26;
    n = Math.floor((n - 1) / 26);
    out = String.fromCharCode(97 + rest) + out;
  }
  return out;
}

function roman(n) {
  let out = "";
  for (const [value, digits] of ROMAN_TABLE) {
    out += digits.repeat(Math.floor(n / value));
    n %= value;
  }
  return out;
}

function lettersValue(token) {
  let n = 0;
  for (const ch of token) n = n * 26 + ch.charCodeAt(0) - 96;
  return n;
}

function romanValue(token) {
  if ([...token].some((ch) => !(ch in ROMAN_DIGITS))) return null;
  let total = 0;
  for (let i = 0; i < token.length; i++) {
    const value = ROMAN_DIGITS[token[i]];
    const following = i + 1 < token.length ? ROMAN_DIGITS[token[i + 1]] : 0;
    total += value < following ? -value : value;
  }
  return total >= 1 && total <= MAX_ROMAN && roman(total) === token ? total : null;
}

export function formatNumber(number, style) {
  if (style === "a" && number >= 1) return letters(number);
  if (style === "i" && number >= 1 && number <= MAX_ROMAN) return roman(number);
  return String(number);
}

export function markerText(line, it) {
  return line.slice(it.hidden, it.content).trim();
}

export function stepOut(lines, infos, n) {
  const target = infos[n].depth - 1;
  for (let above = n - 1; above >= 0; above--) {
    const other = infos[above];
    if (other.kind === "blank" || (isList(other.kind) && other.depth > target)) continue;
    if (isList(other.kind) && other.depth === target) return continuation(lines[above], other);
    break;
  }
  return outdent(lines[n]);
}

function orderedLists(infos) {
  const lists = [];
  const open = new Map();
  infos.forEach((it, i) => {
    if (it.kind === "ordered") {
      for (const d of [...open.keys()]) if (d > it.depth) open.delete(d);
      if (!open.has(it.depth)) {
        const items = [];
        open.set(it.depth, items);
        lists.push(items);
      }
      open.get(it.depth).push(i);
    } else if (it.kind === "bullet" || it.kind === "task") {
      for (const d of [...open.keys()]) if (d >= it.depth) open.delete(d);
    } else if (it.kind !== "blank") {
      open.clear();
    }
  });
  return lists;
}

// Edits [line, start column, end column, new number] numbering each ordered list 1, 2,
// 3... from its first item's number (or from starts.get(first item's line)).
export function renumber(lines, infos, starts = new Map()) {
  const edits = [];
  for (const items of orderedLists(infos)) {
    let expected = starts.has(items[0]) ? starts.get(items[0]) : infos[items[0]].number;
    for (const i of items) {
      const it = infos[i];
      if (it.number !== expected) {
        const start = it.indent.length;
        const end = lines[i].slice(0, it.content).trimEnd().length - it.delim.length;
        edits.push([i, start, end, formatNumber(expected, it.style)]);
      }
      expected++;
    }
  }
  return edits.sort((a, b) => a[0] - b[0] || a[1] - b[1]);
}

export function keptStarts(oldLines, oldInfos, lines, infos) {
  const shortest = Math.min(oldLines.length, lines.length);
  let p = 0;
  while (p < shortest && oldLines[p] === lines[p]) p++;
  let q = 0;
  while (q < shortest - p && oldLines[oldLines.length - 1 - q] === lines[lines.length - 1 - q]) q++;
  const oldEnd = oldLines.length - q;
  const starts = new Map();
  let any = false;
  for (let i = p; i < oldEnd; i++) if (oldInfos[i].kind === "ordered") any = true;
  if (!any) return starts;
  const shift = lines.length - oldLines.length;
  const oldLists = orderedLists(oldInfos).filter((items) => items[0] >= p && items[0] < oldEnd);
  for (const items of orderedLists(infos)) {
    if (items[0] < lines.length - q && items[0] >= p) continue;
    const was = new Set(items.map((i) => (i < p ? i : i - shift)));
    for (const old of oldLists) {
      if (
        old.some((i) => was.has(i)) &&
        oldInfos[old[0]].style === infos[items[0]].style &&
        oldInfos[old[0]].depth === infos[items[0]].depth
      ) {
        starts.set(items[0], oldInfos[old[0]].number);
        break;
      }
    }
  }
  return starts;
}

// --- inline styles ----------------------------------------------------------------------

const W = String.raw`\p{L}\p{N}_`; // Python's \w
const CODE_SPAN = /(`+)(?!`)(.+?)(?<!`)\1(?!`)/gu;
const LINK = new RegExp(String.raw`(!?)\[([^\]\n]*)\]\(` + DESTINATION + String.raw`(?:\s+"[^"\n]*")?\)`, "gu");
const ANGLE_URL = /<((?:https?|mailto):[^<>\s]+)>/gu;
const BARE_URL = new RegExp(String.raw`(?<![${W}/])(?:https?://|www\.)[^\s<>]+`, "gu");
const UNDERLINE = /<u>(.+?)<\/u>/gu;
const BOLD_ITALIC = /(?<![*\\])\*\*\*(?=[^\s*])(.+?)(?<=[^\s*])\*\*\*(?!\*)/gu;
const PAIRED = [
  ["bold", /(?<!\\)\*\*(?=\S)(.+?)(?<=\S)\*\*(?!\*)/gu],
  ["bold", new RegExp(String.raw`(?<![${W}\\])__(?=\S)(.+?)(?<=\S)__(?![${W}])`, "gu")],
  ["strike", /(?<!\\)~~(?=\S)(.+?)(?<=\S)~~/gu],
  ["highlight", /(?<!\\)==(?=\S)(.+?)(?<=\S)==/gu],
];
const ITALIC = [
  /(?<![*\\])\*(?=[^\s*])(.+?)(?<=[^\s*\\])\*(?!\*)/gu,
  new RegExp(String.raw`(?<![${W}\\])_(?=[^\s_])(.+?)(?<=[^\s_])_(?![${W}])`, "gu"),
];
const BLOCKED = "\x00";
const USED = "\x01";

function destination(raw) {
  return raw.startsWith("<") && raw.endsWith(">") ? raw.slice(1, -1) : raw;
}

function span(kind, start, end, innerStart, innerEnd, url = "") {
  return { kind, start, end, innerStart, innerEnd, url };
}

// The inline styles in a line, from column `start` (after a list marker...).
export function inlineSpans(line, start = 0) {
  const spans = [];
  const work = (BLOCKED.repeat(start) + line.slice(start)).split("");
  const text = () => work.join("");
  const block = (a, z, fill = BLOCKED) => {
    for (let i = a; i < z; i++) work[i] = fill;
  };
  for (const m of text().matchAll(CODE_SPAN)) {
    const n = m[1].length;
    const end = m.index + m[0].length;
    spans.push(span("code", m.index, end, m.index + n, end - n));
    block(m.index, end);
  }
  for (const m of text().matchAll(LINK)) {
    const end = m.index + m[0].length;
    const innerStart = m.index + m[1].length + 1;
    const innerEnd = innerStart + m[2].length;
    spans.push(span(m[1] ? "image" : "link", m.index, end, innerStart, innerEnd, destination(m[3])));
    block(m.index, innerStart);
    block(innerEnd, end);
  }
  for (const m of text().matchAll(ANGLE_URL)) {
    const end = m.index + m[0].length;
    spans.push(span("url", m.index, end, m.index + 1, end - 1, m[1]));
    block(m.index, end);
  }
  for (const m of text().matchAll(BARE_URL)) {
    let url = m[0].replace(/[.,;:!?'")]+$/, "");
    const count = (s, ch) => s.split(ch).length - 1;
    if (count(url, "(") < count(m[0], ")") && url.endsWith(")")) url = url.slice(0, -1);
    const end = m.index + url.length;
    spans.push(span("url", m.index, end, m.index, end, url.startsWith("http") ? url : "https://" + url));
    block(m.index, end);
  }
  const paired = (kind, m, n) => {
    const end = m.index + m[0].length;
    spans.push(span(kind, m.index, end, m.index + n, end - n));
    block(m.index, m.index + n, USED);
    block(end - n, end, USED);
  };
  for (const m of text().matchAll(UNDERLINE)) {
    const end = m.index + m[0].length;
    spans.push(span("underline", m.index, end, m.index + 3, end - 4));
    block(m.index, m.index + 3, USED);
    block(end - 4, end, USED);
  }
  for (const m of text().matchAll(BOLD_ITALIC)) {
    const end = m.index + m[0].length;
    spans.push(span("bold", m.index, end, m.index + 3, end - 3));
    spans.push(span("italic", m.index + 3, end - 3, m.index + 3, end - 3));
    block(m.index, m.index + 3, USED);
    block(end - 3, end, USED);
  }
  for (const [kind, pattern] of PAIRED) for (const m of text().matchAll(pattern)) paired(kind, m, 2);
  for (const pattern of ITALIC) for (const m of text().matchAll(pattern)) paired("italic", m, 1);
  return spans.sort((a, b) => a.start - b.start || b.end - a.end);
}

export const MARKERS = {
  bold: ["**", "**"],
  italic: ["*", "*"],
  strike: ["~~", "~~"],
  code: ["`", "`"],
  highlight: ["==", "=="],
  underline: ["<u>", "</u>"],
};

export function toggleWrap(text, selStart, selEnd, kind) {
  const [opening, closing] = MARKERS[kind];
  const before = text.slice(0, selStart);
  const inner = text.slice(selStart, selEnd);
  const after = text.slice(selEnd);
  if (
    before.endsWith(opening) &&
    after.startsWith(closing) &&
    (kind !== "italic" || !(before.endsWith("**") && after.startsWith("**")))
  ) {
    const updated = before.slice(0, -opening.length) + inner + after.slice(closing.length);
    return [updated, selStart - opening.length, selEnd - opening.length];
  }
  if (inner.startsWith(opening) && inner.endsWith(closing) && inner.length >= opening.length * 2) {
    const stripped = inner.slice(opening.length, inner.length - closing.length);
    return [before + stripped + after, selStart, selStart + stripped.length];
  }
  return [before + opening + inner + closing + after, selStart + opening.length, selEnd + opening.length];
}

export function codeBlocks(infos) {
  const blocks = [];
  let n = 0;
  while (n < infos.length) {
    if (infos[n].kind !== "fence") {
      n++;
      continue;
    }
    let end = n + 1;
    while (end < infos.length && infos[end].kind === "code") end++;
    blocks.push([n, Math.min(end, infos.length - 1)]);
    n = end + 1;
  }
  return blocks;
}
