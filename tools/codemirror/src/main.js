// The Notes editor as a CodeMirror 6 view, run in a WebKitGTK WebView by
// src/launcher/notes/web_editor.py. The document is always the plain markdown; what
// each line is comes from markdown.js (a port of notes_markdown.py), and the live
// formatting is CodeMirror decorations:
//
// - headings, inline styles, rules and image links show their markup dimmed on the
//   cursor's line and hide it elsewhere
// - list, checkbox and quote markers are always replaced by a bullet, checkbox, number
//   or bar, and are atomic: the cursor never lands inside them. A list number can still
//   be edited: Left at the start of the item's text (or a click on it) opens it
// - Enter continues lists, Tab / Shift+Tab indent items, Backspace at the start of an
//   item removes its marker; "[] " makes a checkbox, and ordered lists renumber
//   themselves in the same transaction as the keystroke (one undo step)
//
// Python talks to it through window.notes (below); it answers with messages to the
// "notes" script message handler.

import { EditorSelection, EditorState, Prec, RangeSet, StateEffect, StateField } from "@codemirror/state";
import { Decoration, EditorView, ViewPlugin, WidgetType, keymap } from "@codemirror/view";
import { defaultKeymap, history, historyKeymap, insertNewline } from "@codemirror/commands";
import * as md from "./markdown.js";

const STEP = 28; // px per list level; the bullet or number sits in the step before the text
const QUOTE_STEP = 18;
const CODE_INSET = 14;
const BULLETS = ["•", "◦", "▪"];
const isList = (kind) => md.LIST_KINDS.includes(kind);

function post(message) {
  const handler = window.webkit?.messageHandlers?.notes;
  if (handler) handler.postMessage(JSON.stringify(message));
}

// --- what every line is --------------------------------------------------------------

const setBaseDir = StateEffect.define();
let baseDir = null; // file:// URI of the open note's folder (ends in "/"), kept across loads

function analyze(lines, dir) {
  const infos = md.classify(lines);
  const roles = new Map(); // code block line -> "first" / "last" / "only"
  for (const [a, z] of md.codeBlocks(infos)) {
    roles.set(a, a === z ? "only" : "first");
    if (z !== a) roles.set(z, "last");
  }
  return { lines, infos, roles, dir, images: imageDecorations(lines, infos, dir) };
}

const analysis = StateField.define({
  create: (state) => analyze(state.doc.toJSON(), baseDir),
  update(value, tr) {
    let dir = value.dir;
    for (const e of tr.effects) if (e.is(setBaseDir)) dir = e.value;
    if (!tr.docChanged && dir === value.dir) return value;
    if (!tr.docChanged) return { ...value, dir, images: imageDecorations(value.lines, value.infos, dir) };
    return analyze(tr.newDoc.toJSON(), dir);
  },
  // Pictures are block widgets, which have to come from a state field.
  provide: (field) => EditorView.decorations.from(field, (value) => value.images),
});

// --- the list number being edited ----------------------------------------------------

const openNumber = StateEffect.define(); // the start of the line whose number is open, or null
const NUMBER_PREFIX = /^([ \t]*)([0-9a-z]{0,15}[.)]?)[ \t]*/;

// The start of the line whose "1. " shows as text. Closes once the cursor leaves the
// number (checked after each edit, so changing "3." to "3)" can pass through "3 ").
const numberLine = StateField.define({
  create: () => null,
  update(value, tr) {
    for (const e of tr.effects) if (e.is(openNumber)) return e.value;
    if (value === null) return null;
    const from = tr.changes.mapPos(value, -1);
    const line = tr.newDoc.lineAt(from);
    const sel = tr.newSelection.main;
    if (line.from !== from || !sel.empty || sel.head < line.from || sel.head > line.to) return null;
    const m = NUMBER_PREFIX.exec(line.text);
    const col = sel.head - line.from;
    return col >= m[1].length && col < m[0].length ? from : null;
  },
});

// --- widgets ---------------------------------------------------------------------------

class Marker extends WidgetType {
  constructor(kind, label = "", checked = false) {
    super();
    this.kind = kind;
    this.label = label;
    this.checked = checked;
  }

  eq(other) {
    return other.kind === this.kind && other.label === this.label && other.checked === this.checked;
  }

  toDOM(view) {
    const el = document.createElement("span");
    el.className = `cm-marker cm-marker-${this.kind}`;
    if (this.kind === "task") {
      const box = el.appendChild(document.createElement("span"));
      box.className = this.checked ? "cm-checkbox cm-checked" : "cm-checkbox";
      el.addEventListener("mousedown", (e) => {
        e.preventDefault();
        toggleTask(view, view.state.doc.lineAt(view.posAtDOM(el)).number - 1);
      });
    } else if (this.kind === "number") {
      el.textContent = this.label;
      el.addEventListener("mousedown", (e) => {
        e.preventDefault();
        const line = view.state.doc.lineAt(view.posAtDOM(el));
        const it = view.state.field(analysis).infos[line.number - 1];
        openNumberAt(view, line, it.hidden);
        view.focus();
      });
    } else if (this.kind !== "open") {
      el.textContent = this.label;
    }
    return el;
  }

  ignoreEvent() {
    return true;
  }
}

class Rule extends WidgetType {
  eq() {
    return true;
  }

  toDOM() {
    const el = document.createElement("span");
    el.className = "cm-rule";
    return el;
  }
}

class Picture extends WidgetType {
  constructor(src) {
    super();
    this.src = src;
  }

  eq(other) {
    return other.src === this.src;
  }

  toDOM() {
    const el = document.createElement("div");
    el.className = "cm-picture";
    const img = el.appendChild(document.createElement("img"));
    img.src = this.src;
    img.draggable = false;
    img.onerror = () => el.classList.add("cm-picture-missing");
    return el;
  }

  get estimatedHeight() {
    return 240;
  }
}

function pictureSource(url, dir) {
  if (url.includes("://")) return null; // web pictures aren't fetched
  try {
    if (url.startsWith("/")) return new URL("file://" + url).href;
    return dir ? new URL(url, dir).href : null;
  } catch {
    return null;
  }
}

function imageDecorations(lines, infos, dir) {
  const ranges = [];
  let pos = 0;
  lines.forEach((line, n) => {
    const end = pos + line.length;
    if (infos[n].kind === "image") {
      const src = pictureSource(infos[n].url, dir);
      if (src) ranges.push(Decoration.widget({ widget: new Picture(src), block: true, side: 1 }).range(end));
    }
    pos = end + 1;
  });
  return Decoration.set(ranges);
}

// --- live formatting -----------------------------------------------------------------

const dim = Decoration.mark({ class: "cm-dim" });
const hidden = Decoration.replace({});
const styles = {};
for (const kind of ["bold", "italic", "strike", "code", "highlight", "underline", "link", "image", "url"]) {
  styles[kind] = Decoration.mark({ class: kind === "image" || kind === "url" ? "cm-link" : `cm-${kind}` });
}

function decorate(view) {
  const { state } = view;
  const { infos, roles } = state.field(analysis);
  const cursorLine = state.doc.lineAt(state.selection.main.head).number - 1;
  const open = state.field(numberLine);
  const decos = [];
  const atomic = [];
  const seen = new Set();

  for (const { from, to } of view.visibleRanges) {
    for (let pos = from; pos <= to; ) {
      const line = state.doc.lineAt(pos);
      pos = line.to + 1;
      const n = line.number - 1;
      if (seen.has(n)) continue;
      seen.add(n);
      decorateLine(line, infos[n], n === cursorLine, open === line.from, roles.get(n));
    }
  }

  function decorateLine(line, it, onCursor, numberOpen, role) {
    const at = line.from;
    const text = line.text;
    const lineClass = (cls, style) =>
      decos.push(Decoration.line({ class: cls, attributes: style ? { style } : undefined }).range(at));
    const hide = (a, z) => z > a && decos.push(hidden.range(at + a, at + z));
    const markup = (a, z) => z > a && decos.push((onCursor ? dim : hidden).range(at + a, at + z));
    // A marker the cursor never enters: replaced by a widget (or by nothing).
    const marker = (z, widget) => {
      const deco = widget ? Decoration.replace({ widget }) : hidden;
      if (z > 0) {
        decos.push(deco.range(at, at + z));
        atomic.push(deco.range(at, at + z));
      } else if (widget) {
        decos.push(Decoration.widget({ widget, side: -1 }).range(at));
      }
    };
    const inline = (start) => {
      for (const s of md.inlineSpans(text, start)) {
        if (s.innerEnd > s.innerStart) decos.push(styles[s.kind].range(at + s.innerStart, at + s.innerEnd));
        markup(s.start, s.innerStart);
        markup(s.innerEnd, s.end);
      }
    };
    const listPad = (depth) => `padding-left: ${(depth + 1) * STEP}px`;

    switch (it.kind) {
      case "heading":
        lineClass(`cm-h cm-h${Math.min(it.level, 4)}`);
        markup(0, it.marker);
        inline(it.content);
        break;
      case "bullet":
        lineClass("cm-li", listPad(it.depth));
        marker(it.hidden, new Marker("bullet", BULLETS[it.depth % 3]));
        inline(it.content);
        break;
      case "task":
        lineClass(it.checked ? "cm-li cm-done" : "cm-li", listPad(it.depth));
        marker(it.hidden, new Marker("task", "", it.checked));
        inline(it.content);
        break;
      case "ordered":
        lineClass("cm-li", listPad(it.depth));
        if (numberOpen) marker(it.hidden, new Marker("open"));
        else marker(it.content, new Marker("number", md.markerText(text, it)));
        inline(it.content);
        break;
      case "quote":
        lineClass("cm-quote", quoteStyle(it.depth));
        marker(it.hidden, null);
        inline(it.content);
        break;
      case "rule":
        if (onCursor) markup(0, text.length);
        else decos.push(Decoration.replace({ widget: new Rule() }).range(at, at + text.length));
        break;
      case "fence":
      case "code":
        lineClass(`cm-codeblock${role ? ` cm-codeblock-${role}` : ""}${it.kind === "fence" ? " cm-fence" : ""}`);
        break;
      case "image":
        if (onCursor) markup(0, text.length);
        else hide(0, text.length);
        break;
      case "text":
        inline(0);
        break;
    }
  }

  return { decorations: Decoration.set(decos, true), atomic: Decoration.set(atomic, true) };
}

function quoteStyle(depth) {
  const stops = [];
  for (let d = 0; d < depth; d++) {
    const x = d * QUOTE_STEP;
    stops.push(`transparent ${x}px, var(--quote) ${x}px, var(--quote) ${x + 3}px, transparent ${x + 3}px`);
  }
  return `padding-left: ${depth * QUOTE_STEP}px; background: linear-gradient(to right, ${stops.join(", ")}) no-repeat`;
}

const livePreview = ViewPlugin.fromClass(
  class {
    constructor(view) {
      Object.assign(this, decorate(view));
    }

    update(u) {
      if (
        u.docChanged ||
        u.viewportChanged ||
        u.selectionSet ||
        u.startState.field(numberLine) !== u.state.field(numberLine) ||
        u.startState.field(analysis) !== u.state.field(analysis)
      ) {
        Object.assign(this, decorate(u.view));
      }
    }
  },
  { decorations: (plugin) => plugin.decorations },
);

const atomicMarkers = EditorView.atomicRanges.of((view) => view.plugin(livePreview)?.atomic ?? RangeSet.empty);

// --- automatic edits ------------------------------------------------------------------

// A cursor inside a hidden marker (Home, Up/Down, a click left of a bullet) moves to the
// item's text; inside a number being edited it may stay, but not in the indent before it.
const keepOutOfMarkers = EditorState.transactionFilter.of((tr) => {
  if (!tr.selection || tr.docChanged || tr.selection.ranges.length > 1 || !tr.selection.main.empty) return tr;
  const state = tr.state;
  const head = tr.selection.main.head;
  const line = state.doc.lineAt(head);
  const it = state.field(analysis).infos[line.number - 1];
  const col = head - line.from;
  let target = null;
  if (it.kind === "ordered") {
    if (state.field(numberLine) === line.from) {
      if (col < it.hidden) target = it.hidden;
    } else if (col < it.content) {
      target = it.content;
    }
  } else if (it.hidden && col < it.hidden) {
    target = it.content;
  }
  if (target === null) return tr;
  return [tr, { selection: { anchor: line.from + Math.min(target, line.length) }, sequential: true }];
});

// After each edit (not undo/redo): "[] " typed at a line start becomes "- [ ] ", "- "
// typed on an empty numbered item makes it a bullet (and back), and the ordered lists
// around the changed lines are renumbered. It all joins the edit's transaction, so one
// Ctrl+Z undoes the keystroke and what followed from it.
const autoEdits = EditorState.transactionFilter.of((tr) => {
  if (!tr.docChanged || tr.isUserEvent("undo") || tr.isUserEvent("redo")) return tr;
  const before = tr.startState.field(analysis);
  const doc = tr.newDoc;
  let lines = doc.toJSON();
  let infos = md.classify(lines);
  let first = Infinity;
  let last = -1;
  tr.changes.iterChangedRanges((_fa, _ta, fromB, toB) => {
    first = Math.min(first, doc.lineAt(fromB).number - 1);
    last = Math.max(last, doc.lineAt(toB).number - 1);
  });
  const specs = [tr];

  // The shorthand just typed at the cursor
  let shortLine = -1;
  let shortDelta = 0;
  const sel = tr.newSelection.main;
  if (sel.empty) {
    const line = doc.lineAt(sel.head);
    const n = line.number - 1;
    const found = md.shorthand(lines, infos, n);
    if (found && sel.head - line.from === found[0]) {
      const [length, replacement] = found;
      specs.push({
        changes: { from: line.from, to: line.from + length, insert: replacement },
        selection: { anchor: line.from + replacement.length },
        sequential: true,
      });
      lines = lines.slice();
      lines[n] = replacement + lines[n].slice(length);
      infos = md.classify(lines);
      shortLine = n;
      shortDelta = replacement.length - length;
    }
  }

  if (infos.some((it) => it.kind === "ordered")) {
    const starts = md.keptStarts(before.lines, before.infos, lines, infos);
    let a = Math.min(first, infos.length - 1);
    let z = Math.min(last, infos.length - 1);
    const listy = (it) => isList(it.kind) || it.kind === "blank";
    while (a > 0 && listy(infos[a - 1])) a--;
    while (z + 1 < infos.length && listy(infos[z + 1])) z++;
    const edits = md.renumber(lines, infos, starts).filter(([n]) => n >= a && n <= z);
    if (edits.length) {
      // positions in the document after the shorthand, if there was one
      const lineStart = (n) => doc.line(n + 1).from + (shortLine >= 0 && n > shortLine ? shortDelta : 0);
      const changes = edits.map(([n, s, e, number]) => ({ from: lineStart(n) + s, to: lineStart(n) + e, insert: number }));
      specs.push({ changes, sequential: true });
    }
  }
  return specs.length > 1 ? specs : tr;
});

// --- commands ---------------------------------------------------------------------------

function current(state) {
  const sel = state.selection.main;
  if (!sel.empty) return null;
  const line = state.doc.lineAt(sel.head);
  const { lines, infos } = state.field(analysis);
  const n = line.number - 1;
  return { line, n, col: sel.head - line.from, text: line.text, it: infos[n], lines, infos };
}

function setLine(view, n, text, cursorCol = null) {
  const line = view.state.doc.line(n + 1);
  view.dispatch({
    changes: { from: line.from, to: line.to, insert: text },
    selection: cursorCol === null ? undefined : { anchor: line.from + Math.max(0, Math.min(cursorCol, text.length)) },
    scrollIntoView: true,
    userEvent: "input.list",
  });
}

function openNumberAt(view, line, col) {
  view.dispatch({ selection: { anchor: line.from + col }, effects: openNumber.of(line.from) });
}

function enter(view) {
  const c = current(view.state);
  if (!c) return false;
  const { line, n, col, text, it, lines, infos } = c;
  if (it.kind === "ordered" && view.state.field(numberLine) === line.from) {
    view.dispatch({ selection: { anchor: line.from + it.content }, effects: openNumber.of(null) });
    return true; // Enter in the number: back to the text
  }
  if ((isList(it.kind) || it.kind === "quote") && col >= it.content) {
    if (md.isEmptyItem(text, it)) {
      // Enter on an empty item: step out one level (as an item of the list it steps back
      // into), or end the list.
      if (isList(it.kind) && it.indent) {
        const updated = md.stepOut(lines, infos, n);
        setLine(view, n, updated, updated.length);
      } else {
        setLine(view, n, "", 0);
      }
      return true;
    }
    view.dispatch(view.state.replaceSelection("\n" + md.continuation(text, it)), {
      scrollIntoView: true,
      userEvent: "input",
    });
    return true;
  }
  if (it.kind === "fence" && col === text.length && fenceIsOpen(infos, n)) {
    // "```" + Enter: close the block and put the cursor inside it.
    view.dispatch({
      changes: { from: line.to, insert: "\n\n" + text.trim().slice(0, 3) },
      selection: { anchor: line.to + 1 },
      scrollIntoView: true,
      userEvent: "input",
    });
    return true;
  }
  return insertNewline(view); // a plain newline: no copied indentation
}

function fenceIsOpen(infos, n) {
  const fencesBefore = infos.slice(0, n).filter((it) => it.kind === "fence").length;
  return fencesBefore % 2 === 0 && infos.slice(n + 1).every((it) => it.kind === "code");
}

function selectedLines(state) {
  const sel = state.selection.main;
  const first = state.doc.lineAt(sel.from).number - 1;
  let last = state.doc.lineAt(sel.to).number - 1;
  if (last > first && state.doc.line(last + 1).from === sel.to) last--; // ends at a line start
  const out = [];
  for (let n = first; n <= last; n++) out.push(n);
  return out;
}

function indentLines(view, change) {
  const { state } = view;
  const { lines, infos } = state.field(analysis);
  const targets = selectedLines(state).filter((n) => isList(infos[n].kind));
  if (!targets.length) return false;
  const text = lines.slice(); // as edited so far: each line's new level depends on it
  const changes = [];
  const sel = state.selection.main;
  let cursor = null;
  for (const n of targets) {
    const old = text[n];
    const updated = (text[n] = md.reindent(text, n, change));
    if (updated === old) continue;
    const line = state.doc.line(n + 1);
    changes.push({ from: line.from, to: line.to, insert: updated });
    if (sel.empty && state.doc.lineAt(sel.head).number - 1 === n) {
      cursor = { line: n, col: sel.head - line.from + updated.length - old.length, length: updated.length };
    }
  }
  if (!changes.length) return true;
  const changeSet = state.changes(changes);
  let selection;
  if (cursor) {
    const from = changeSet.mapPos(state.doc.line(cursor.line + 1).from, -1);
    selection = { anchor: from + Math.max(0, Math.min(cursor.col, cursor.length)) };
  }
  view.dispatch({ changes: changeSet, selection, scrollIntoView: true, userEvent: "input.indent" });
  return true;
}

function tab(view) {
  if (indentLines(view, md.indent)) return true;
  view.dispatch(view.state.replaceSelection("\t"), { userEvent: "input", scrollIntoView: true });
  return true;
}

function shiftTab(view) {
  indentLines(view, md.outdent);
  return true; // never move focus out of the editor
}

function backspace(view) {
  const c = current(view.state);
  if (!c) return false;
  const { n, col, text, it, lines } = c;
  if (!(isList(it.kind) || it.kind === "quote" || it.kind === "heading") || col !== it.content) return false;
  if (isList(it.kind) && it.indent) {
    const updated = md.reindent(lines, n, md.outdent);
    setLine(view, n, updated, col - (text.length - updated.length));
  } else {
    setLine(view, n, md.withoutMarker(text, it), 0);
  }
  return true;
}

// Left at the start of an item's text goes to the previous line, not into the hidden
// marker; on a numbered item it steps into the number, to edit it.
function left(view) {
  const c = current(view.state);
  if (!c) return false;
  const { line, col, text, it } = c;
  const open = view.state.field(numberLine) === line.from;
  if (it.kind === "ordered" && !open && col === it.content) {
    openNumberAt(view, line, text.slice(0, it.content).trimEnd().length); // after the "."
    return true;
  }
  if (it.kind === "ordered") {
    if (!open || col !== it.hidden) return false;
  } else if (!it.hidden || col !== it.content) {
    return false;
  }
  view.dispatch({ selection: { anchor: Math.max(0, line.from - 1) }, scrollIntoView: true });
  return true;
}

function toggleTask(view, n) {
  const { infos } = view.state.field(analysis);
  if (n < 0 || n >= infos.length || infos[n].kind !== "task") return false;
  const line = view.state.doc.line(n + 1);
  const box = line.text.indexOf("[", infos[n].indent.length) + 1;
  view.dispatch({
    changes: { from: line.from + box, to: line.from + box + 1, insert: infos[n].checked ? " " : "x" },
    userEvent: "input.task",
  });
  return true;
}

function wrap(style) {
  return (view) => {
    const sel = view.state.selection.main;
    const line = view.state.doc.lineAt(sel.from);
    if (sel.to > line.to) return true; // styles don't span lines in markdown
    const [text, s, e] = md.toggleWrap(line.text, sel.from - line.from, sel.to - line.from, style);
    view.dispatch({
      changes: { from: line.from, to: line.to, insert: text },
      selection: EditorSelection.single(line.from + s, line.from + e),
      userEvent: "input.style",
    });
    return true;
  };
}

// Ctrl+K: [selection](|), or [|](url) when a URL is selected.
function insertLink(view) {
  const sel = view.state.selection.main;
  const line = view.state.doc.lineAt(sel.from);
  if (sel.to > line.to) return true;
  const chosen = view.state.sliceDoc(sel.from, sel.to);
  const isUrl = /^(https?:\/\/|www\.)/.test(chosen);
  const insert = isUrl ? `[](${chosen})` : `[${chosen}]()`;
  const cursor = sel.from + (isUrl ? 1 : chosen.length + 3);
  view.dispatch({ changes: { from: sel.from, to: sel.to, insert }, selection: { anchor: cursor }, userEvent: "input" });
  return true;
}

function linkAt(state, pos) {
  const line = state.doc.lineAt(pos);
  const it = state.field(analysis).infos[line.number - 1];
  if (it.kind === "image") return it.url;
  if (it.kind === "code" || it.kind === "fence") return null;
  const col = pos - line.from;
  const found = md
    .inlineSpans(line.text, it.content)
    .find((s) => ["link", "url", "image"].includes(s.kind) && s.start <= col && col <= s.end);
  return found ? found.url : null;
}

const keys = [
  { key: "Enter", run: enter },
  { key: "Mod-Enter", run: (view) => toggleTask(view, view.state.doc.lineAt(view.state.selection.main.head).number - 1) },
  { key: "Tab", run: tab },
  { key: "Shift-Tab", run: shiftTab },
  { key: "Backspace", run: backspace },
  { key: "ArrowLeft", run: left },
  { key: "Mod-b", run: wrap("bold") },
  { key: "Mod-i", run: wrap("italic") },
  { key: "Mod-u", run: wrap("underline") },
  { key: "Mod-e", run: wrap("code") },
  { key: "Mod-Shift-x", run: wrap("strike") },
  { key: "Mod-Shift-h", run: wrap("highlight") },
  { key: "Mod-k", run: insertLink },
];

// --- the view and the bridge to Python ----------------------------------------------

let lastLine = -1;

const report = EditorView.updateListener.of((u) => {
  const head = u.state.selection.main.head;
  const line = u.state.doc.lineAt(head).number - 1;
  if (u.docChanged) {
    post({ type: "changed", text: u.state.doc.toString(), line, offset: head });
  } else if (u.selectionSet && line !== lastLine) {
    post({ type: "cursor", line, offset: head });
  }
  lastLine = line;
});

// Layout that has to beat CodeMirror's base theme; the rest is in web/editor.css.
const layout = EditorView.theme({
  "&": { height: "100%", fontSize: "var(--size)", background: "transparent", color: "var(--fg)" },
  "&.cm-focused": { outline: "none" },
  ".cm-scroller": { fontFamily: "var(--font)", lineHeight: "1.5", overflow: "auto" },
  ".cm-content": {
    boxSizing: "border-box",
    width: "100%",
    maxWidth: "884px", // 820px of text plus the margins
    margin: "0 auto",
    padding: "24px 32px 120px",
    caretColor: "var(--fg)",
  },
  ".cm-line": { padding: "0 0 6px" }, // CodeMirror measures lines: spacing is padding
  ".cm-line.cm-codeblock": { padding: `0 ${CODE_INSET}px` },
  ".cm-line.cm-codeblock-first, .cm-line.cm-codeblock-only": { paddingTop: "6px" },
  ".cm-line.cm-codeblock-last, .cm-line.cm-codeblock-only": { paddingBottom: "6px" },
});

const extensions = [
  layout,
  analysis,
  numberLine,
  livePreview,
  atomicMarkers,
  history(),
  keepOutOfMarkers,
  autoEdits,
  Prec.highest(keymap.of(keys)),
  keymap.of([...historyKeymap, ...defaultKeymap]),
  EditorView.lineWrapping,
  EditorState.tabSize.of(4),
  EditorView.contentAttributes.of({ spellcheck: "false", autocorrect: "off", autocapitalize: "off" }),
  EditorView.domEventHandlers({
    mousedown(e, view) {
      if (!(e.ctrlKey || e.metaKey)) return false;
      const pos = view.posAtCoords({ x: e.clientX, y: e.clientY });
      const url = pos === null ? null : linkAt(view.state, pos);
      if (!url) return false;
      e.preventDefault();
      post({ type: "link", url });
      return true;
    },
  }),
  report,
];

const view = new EditorView({ parent: document.getElementById("editor"), state: EditorState.create({ extensions }) });

function place(offset, top = false) {
  const pos = offset < 0 ? view.state.doc.length : Math.min(offset, view.state.doc.length);
  view.dispatch({
    selection: { anchor: pos },
    effects: EditorView.scrollIntoView(pos, { y: top ? "start" : "nearest" }),
  });
}

window.notes = {
  // A note's text as it is: no renumbering, no rewrites, and not undoable.
  load(text, offset = 0) {
    view.setState(EditorState.create({ doc: text, extensions }));
    lastLine = -1;
    place(offset);
  },
  setCursor(offset) {
    place(offset);
  },
  goToLine(n) {
    if (n < 0 || n >= view.state.doc.lines) return;
    const line = view.state.doc.line(n + 1);
    const it = view.state.field(analysis).infos[n];
    const pos = line.from + Math.min(it.content, line.length);
    view.dispatch({ selection: { anchor: pos }, effects: EditorView.scrollIntoView(pos, { y: "start", yMargin: 24 }) });
    view.focus();
  },
  focus() {
    view.focus();
  },
  setBaseDir(uri) {
    baseDir = uri;
    view.dispatch({ effects: setBaseDir.of(uri) });
  },
  // Put ![](link) on a line of its own at the cursor; the cursor goes below it.
  insertPictureLink(link) {
    const head = view.state.selection.main.head;
    const before = head === view.state.doc.lineAt(head).from ? "" : "\n";
    view.dispatch(view.state.replaceSelection(`${before}![](${link})\n`), { userEvent: "input.paste", scrollIntoView: true });
  },
  setStyle({ dark, accent, font, size }) {
    const root = document.documentElement;
    root.dataset.theme = dark ? "dark" : "light";
    if (accent) root.style.setProperty("--accent", accent);
    if (font) root.style.setProperty("--font", `"${font}", system-ui, sans-serif`);
    if (size) root.style.setProperty("--size", `${size}pt`);
    view.requestMeasure();
  },
  // For tests: where a character is on the page (its middle), in CSS pixels.
  coords(n, col) {
    const line = view.state.doc.line(n + 1);
    const rect = view.coordsAtPos(Math.min(line.from + col, line.to), 1);
    return rect && { x: (rect.left + rect.right) / 2, y: (rect.top + rect.bottom) / 2 };
  },
  view, // for tests and the web inspector
};

post({ type: "ready" });
