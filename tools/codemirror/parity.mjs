// Reads {"docs": [[line, ...], ...]} on stdin and prints what markdown.js makes of each
// doc, for tests/test_notes_web_parity.py to compare with notes_markdown.py.
import * as md from "./src/markdown.js";

let input = "";
for await (const chunk of process.stdin) input += chunk;
const out = JSON.parse(input).docs.map((lines) => {
  const infos = md.classify(lines);
  return {
    infos,
    spans: lines.map((line, n) => md.inlineSpans(line, infos[n].content)),
    renumber: md.renumber(lines, infos),
    levels: md.orderedLevels(infos),
    continuation: lines.map((line, n) => md.continuation(line, infos[n])),
    shorthand: lines.map((_, n) => md.shorthand(lines, infos, n)),
    indent: lines.map((_, n) => (infos[n].kind === "ordered" ? md.reindent(lines, n, md.indent) : null)),
    outdent: lines.map((_, n) => (infos[n].kind === "ordered" ? md.reindent(lines, n, md.outdent) : null)),
    stepOut: lines.map((_, n) => (infos[n].depth > 0 && md.isEmptyItem(lines[n], infos[n]) ? md.stepOut(lines, infos, n) : null)),
  };
});
process.stdout.write(JSON.stringify(out));
