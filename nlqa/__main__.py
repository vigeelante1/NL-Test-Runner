"""Command line:

  python -m nlqa "Log in as a customer and verify the dashboard is shown" --url https://phptravels.net
  python -m nlqa --file cases/phptravels.txt
"""
from __future__ import annotations

import argparse
import os
import re
import sys
from datetime import datetime
from pathlib import Path

from dotenv import dotenv_values, load_dotenv

from .agent import Agent
from .llm import GeminiLLM, LLMError, ScriptedLLM
from .report import write_suite_summary, write_test_report

ICON = {"PASS": "✅ PASS", "FAIL": "❌ FAIL", "ERROR": "⚠️  ERROR"}


def parse_case_file(path: Path, default_url: str | None) -> list[tuple[str, str | None]]:
    """One test case per line. `@url https://...` sets the start URL for the cases below it. `#` = comment."""
    cases, url = [], default_url
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.lower().startswith("@url"):
            url = line[4:].strip() or None
            continue
        cases.append((line, url))
    return cases


def load_test_data(path: str | None) -> dict[str, str]:
    p = Path(path or "testdata.env")
    if not p.exists():
        return {}
    return {k: v for k, v in dotenv_values(p).items() if k and v is not None}


def describe_evidence(ev: dict, mask=lambda s: s) -> str:
    kind, value = ev.get("type"), ev.get("value")
    if ev.get("id") is not None:
        return f"{kind} element [{ev.get('id')}]" + (f" = '{mask(str(value))}'" if value not in (None, "") else "")
    return f"{kind} '{mask(str(value))}'"


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:50] or "test"


def main(argv: list[str] | None = None) -> int:
    load_dotenv()
    ap = argparse.ArgumentParser(prog="nlqa", description="Run plain-English test cases in a real browser.")
    ap.add_argument("test_case", nargs="?", help='e.g. "Log in as a customer and verify the dashboard is shown"')
    ap.add_argument("--url", default=os.getenv("NLQA_START_URL"), help="start page (optional if the test case names the site)")
    ap.add_argument("--file", help="text file with one test case per line")
    ap.add_argument("--data", default=os.getenv("NLQA_TEST_DATA", "testdata.env"), help="test data file (default testdata.env)")
    ap.add_argument("--headed", action="store_true", help="show the browser window")
    ap.add_argument("--vision", action="store_true", help="also send a screenshot to the model each step")
    ap.add_argument("--max-steps", type=int, default=int(os.getenv("NLQA_MAX_STEPS", "25")))
    ap.add_argument("--model", default=os.getenv("GEMINI_MODEL", "gemini-3.5-flash-lite"))
    ap.add_argument("--script", help="(testing the tool itself) replay actions from a JSON file instead of calling an AI")
    ap.add_argument("--reports", default="reports", help="where reports are written")
    ap.add_argument("--list-models", action="store_true", help="show the Gemini models your API key can use, then exit")
    args = ap.parse_args(argv)

    if args.list_models:
        try:
            models = GeminiLLM(os.getenv("GEMINI_API_KEY", ""), args.model).available_models()
        except LLMError as e:
            print(f"Error: {e}", file=sys.stderr)
            return 2
        print("Gemini models your key can use (best candidates first):" if models
              else "Could not list models (check GEMINI_API_KEY and your connection).")
        for m in models:
            print("  " + m)
        return 0 if models else 2

    if args.file:
        cases = parse_case_file(Path(args.file), args.url)
    elif args.test_case:
        cases = [(args.test_case, args.url)]
    else:
        ap.error("give a test case or --file")

    try:
        llm = ScriptedLLM(args.script) if args.script else GeminiLLM(
            os.getenv("GEMINI_API_KEY", ""), args.model, float(os.getenv("GEMINI_MIN_INTERVAL", "6")))
    except LLMError as e:
        print(f"Error: {e}", file=sys.stderr)
        return 2

    data = load_test_data(args.data)
    run_root = Path(args.reports) / datetime.now().strftime("%Y%m%d-%H%M%S")
    results = []
    for i, (case, url) in enumerate(cases, 1):
        print(f"\n{'=' * 70}\nTEST {i}/{len(cases)}: {case}\nStart URL: {url or '(from test case)'} | "
              f"Model: {llm.name} | Test data: {', '.join(data) or 'none'}\n{'=' * 70}")
        out_dir = run_root / f"{i:02d}-{slug(case)}"
        agent = Agent(llm, data, out_dir, max_steps=args.max_steps, vision=args.vision)
        res = agent.run(case, url, headed=args.headed)
        report = write_test_report(res, out_dir)
        results.append((res, out_dir / "report.html"))
        print(f"\n{ICON.get(res.status, res.status)}  {res.reason}")
        for ev in res.evidence:
            print(f"   evidence: {describe_evidence(ev, agent.mask)}")
        print(f"   {len(res.steps)} steps, {res.duration_s}s. Report: {report.resolve()}")

    if len(results) > 1:
        summary = write_suite_summary(results, run_root)
        print(f"\n{'=' * 70}\nSUMMARY")
        for r, _ in results:
            print(f"  {r.status:<6} {r.test_case}")
        print(f"Summary report: {summary.resolve()}")

    return 0 if all(r.status == "PASS" for r, _ in results) else 1


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")  # Windows consoles
    sys.exit(main())
