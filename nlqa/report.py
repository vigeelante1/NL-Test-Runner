"""Writes report.json and a self-contained report.html for each test, plus a summary for suites."""
from __future__ import annotations

import html
import json
from dataclasses import asdict
from pathlib import Path

from .agent import TestResult

COLORS = {"PASS": "#1a7f37", "FAIL": "#cf222e", "ERROR": "#9a6700"}


def write_test_report(res: TestResult, out_dir: Path) -> Path:
    (out_dir / "report.json").write_text(json.dumps(asdict(res), indent=2), encoding="utf-8")
    e = html.escape
    rows = []
    for s in res.steps:
        shot = f'<a href="{e(s.screenshot)}" target="_blank"><img src="{e(s.screenshot)}" loading="lazy"></a>' if s.screenshot else ""
        rows.append(f"""<tr class="{'ok' if s.ok else 'bad'}"><td>{s.step}</td>
<td><div class="thought">{e(s.thought)}</div><code>{e(s.summary)}</code><div class="res">{e(s.result)}</div>
<div class="url">{e(s.url)}</div></td><td>{shot}</td></tr>""")
    evidence = "".join(
        f"<li><code>{e(str(x.get('type')))}</code> "
        + (f"element [{e(str(x.get('id')))}] " if x.get("id") is not None else "")
        + (e(str(x.get("value"))) if x.get("value") not in (None, "") else "") + "</li>"
        for x in res.evidence)
    final = f'<img class="final" src="{e(res.final_screenshot)}">' if res.final_screenshot else ""
    doc = f"""<!doctype html><html><head><meta charset="utf-8"><title>{res.status}: {e(res.test_case[:60])}</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
body{{font:15px/1.5 system-ui,sans-serif;margin:0;background:#f6f8fa;color:#1f2328}}
main{{max-width:1100px;margin:auto;padding:24px 16px}}
.badge{{display:inline-block;padding:4px 14px;border-radius:999px;color:#fff;font-weight:700;background:{COLORS.get(res.status, '#555')}}}
h1{{font-size:20px;margin:12px 0}} .meta{{color:#57606a;font-size:13px}}
.card{{background:#fff;border:1px solid #d0d7de;border-radius:8px;padding:16px;margin:16px 0}}
table{{width:100%;border-collapse:collapse;background:#fff}} td{{border-top:1px solid #d0d7de;padding:10px;vertical-align:top}}
td:first-child{{width:32px;color:#57606a}} td:last-child{{width:280px}} img{{width:100%;border:1px solid #d0d7de;border-radius:4px}}
.thought{{color:#57606a;font-style:italic}} code{{background:#eff1f3;padding:1px 6px;border-radius:4px}}
tr.bad .res{{color:#cf222e}} .res{{font-size:13px}} .url{{font-size:12px;color:#8c959f;word-break:break-all}}
img.final{{max-width:100%;width:auto}}
</style></head><body><main>
<span class="badge">{res.status}</span>
<h1>{e(res.test_case)}</h1>
<div class="meta">Start: {e(res.start_url or '-')} · Model: {e(res.model)} · {len(res.steps)} steps · {res.llm_calls} AI calls · {res.duration_s}s</div>
<div class="card"><b>Verdict:</b> {e(res.reason or '-')}{f'<ul>{evidence}</ul>' if evidence else ''}
<div class="meta">Final URL: {e(res.final_url)}</div></div>
<table>{''.join(rows)}</table>
<div class="card"><b>Final page</b><br>{final}</div>
</main></body></html>"""
    path = out_dir / "report.html"
    path.write_text(doc, encoding="utf-8")
    return path


def write_suite_summary(results: list[tuple[TestResult, Path]], root: Path) -> Path:
    e = html.escape
    rows = "".join(
        f'<tr><td><span style="color:{COLORS.get(r.status)};font-weight:700">{r.status}</span></td>'
        f'<td><a href="{e(p.relative_to(root).as_posix())}">{e(r.test_case)}</a></td><td>{e(r.reason)}</td>'
        f'<td>{len(r.steps)}</td><td>{r.duration_s}s</td></tr>'
        for r, p in results)
    doc = f"""<!doctype html><html><head><meta charset="utf-8"><title>Test run summary</title>
<style>body{{font:15px system-ui,sans-serif;margin:24px}}table{{border-collapse:collapse;width:100%}}
td,th{{border:1px solid #d0d7de;padding:8px;text-align:left;vertical-align:top}}</style></head><body>
<h1>Test run summary</h1><table><tr><th>Result</th><th>Test case</th><th>Why</th><th>Steps</th><th>Time</th></tr>{rows}</table>
</body></html>"""
    path = root / "summary.html"
    path.write_text(doc, encoding="utf-8")
    return path
