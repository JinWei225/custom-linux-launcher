"""The web editor's markdown.js (a port of notes_markdown) reads notes the same way."""

import dataclasses
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from launcher import notes_markdown as md

TOOLS = Path(__file__).parent.parent / "tools" / "codemirror"

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="needs node")

DOCS = [
    """# Lecture 3
## Sorting *fast*

Some **bold**, *italic*, _also_, ~~gone~~, `code`, ==marked==, <u>under</u>, ***both***.
A [link](https://example.com) and <https://x.org> and www.example.com/a_(b), then snake_case.
![](attachments/pic.png)
Text with ![inline](a.png) and \\*not italic\\*.

- one
- two
\t- nested **deep**
\t\t- deeper
\t- [ ] task
\t- [x] done
-

1. first
2. second
\ta) sub
\tb) sub two
\t\ti. roman
\t\tii. roman two
\t\tiv. skipped
3. third
5. fifth
\t- bullet under number
\t1. digits one in

> quote
> > nested quote
>
> after bare
>not a quote

---
* * *
___

```python
# not a heading
- not a list
```

~~~
open fence to the end
- still code
""",
    """\ta) first point, not a list
- a) first option
1. x
\tvs. that
\tc) jumps
\taa) long
2. y
\tz) z
\taa) after z
[] shorthand
- [] shorthand too
1. -
- 1.
\t- a)
- -
- [ ]
7) seven
8) eight
10. ten
   3. spaced
    - four spaces
""",
    "",
    "just one line",
    "```\nunclosed",
]


def js(docs: list[list[str]]) -> list[dict]:
    out = subprocess.run(
        ["node", str(TOOLS / "parity.mjs")],
        input=json.dumps({"docs": docs}),
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(out.stdout)


def py(lines: list[str]) -> dict:
    infos = md.classify(lines)
    ordered = [info.kind == "ordered" for info in infos]
    return {
        "infos": [dataclasses.asdict(info) for info in infos],
        "spans": [
            [
                {
                    "kind": s.kind,
                    "start": s.start,
                    "end": s.end,
                    "innerStart": s.inner_start,
                    "innerEnd": s.inner_end,
                    "url": s.url,
                }
                for s in md.inline_spans(line, info.content)
            ]
            for line, info in zip(lines, infos, strict=True)
        ],
        "renumber": [list(e) for e in md.renumber(lines, infos)],
        "levels": md.ordered_levels(infos),
        "continuation": [md.continuation(line, i) for line, i in zip(lines, infos, strict=True)],
        "shorthand": [
            list(found) if (found := md.shorthand(lines, infos, n)) else None
            for n in range(len(lines))
        ],
        "indent": [md.reindent(lines, n, md.indent) if o else None for n, o in enumerate(ordered)],
        "outdent": [
            md.reindent(lines, n, md.outdent) if o else None for n, o in enumerate(ordered)
        ],
        "stepOut": [
            md.step_out(lines, infos, n)
            if infos[n].depth > 0 and md.is_empty_item(lines[n], infos[n])
            else None
            for n in range(len(lines))
        ],
    }


def test_js_reads_notes_like_python():
    docs = [doc.split("\n") for doc in DOCS]
    for lines, got in zip(docs, js(docs), strict=True):
        want = py(lines)
        for key, value in want.items():
            for n, (a, b) in enumerate(zip(value, got[key], strict=True)):
                assert a == b, f"{key} differs at {n}: {lines[n] if n < len(lines) else ''!r}"
