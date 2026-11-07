"""Tests the tool's own logic (no API key needed): a local demo site + scripted 'AI' decisions.

Checks that real passes pass, real failures fail, bad steps are recovered from, new tabs are followed,
secrets never reach logs, and that a model that *claims* PASS without proof is NOT believed.

    python tests/self_test.py
"""
from __future__ import annotations

import functools
import http.server
import json
import sys
import tempfile
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from nlqa.agent import Agent  # noqa: E402
from nlqa.llm import ScriptedLLM  # noqa: E402

SITE = ROOT / "tests" / "demo_site"
SCRIPTS = ROOT / "tests" / "scripts"
DATA = {"CUSTOMER_EMAIL": "demo@example.com", "CUSTOMER_PASSWORD": "secret123"}

CASES = [
    ("happy-login.json", "Log in as a customer and verify the dashboard is shown", "PASS"),
    ("wrong-password.json", "Log in with a wrong password and verify an error message is shown", "PASS"),
    ("app-bug-fail.json", "Log in as a customer and verify the dashboard is shown", "FAIL"),
    ("lying-model.json", "Log in as a customer and verify the dashboard is shown", "FAIL"),
    ("new-tab.json", "Open the help page and verify the help center is shown", "PASS"),
    ("bad-element.json", "Dismiss the notice and verify the login form is usable", "PASS"),
    ("checkbox-proof.json", "Tick 'Remember me' and verify it is ticked", "PASS"),
    ("weak-evidence.json", "Verify 'Remember me' is ticked (it is not)", "FAIL"),
    ("guessed-url.json", "Verify the login form is usable", "PASS"),
    ("stuck-loop.json", "Do something impossible", "ERROR"),
    ("popup-and-custom-widget.json", "Search hotels in Dubai and verify a list of hotels is shown", "PASS", "search.html"),
]


def serve() -> tuple[http.server.ThreadingHTTPServer, str]:
    class Quiet(http.server.SimpleHTTPRequestHandler):
        def log_message(self, *args):
            pass

    handler = functools.partial(Quiet, directory=str(SITE))
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, f"http://127.0.0.1:{server.server_address[1]}/login.html"


def main() -> int:
    server, url = serve()
    failures = 0
    with tempfile.TemporaryDirectory() as tmp:
        for script, case, expected, *page in CASES:
            agent = Agent(ScriptedLLM(str(SCRIPTS / script)), DATA, Path(tmp) / script, verbose=False)
            res = agent.run(case, url.replace("login.html", page[0]) if page else url)
            dump = json.dumps([s.__dict__ for s in res.steps]) + res.reason
            leaked = DATA["CUSTOMER_PASSWORD"] in dump
            ok = res.status == expected and not leaked
            if script == "guessed-url.json":  # the guessed URL must have been refused, not opened
                ok = ok and res.steps and res.steps[0].result.startswith("refused")
            if script == "stuck-loop.json":  # must stop early, not burn all 25 steps
                ok = ok and len(res.steps) <= 5 and res.reason.startswith("Stuck")
            if script == "popup-and-custom-widget.json":  # the click behind the pop-up must be reported as blocked
                ok = ok and res.steps and "blocked" in res.steps[0].result
            failures += not ok
            print(f"{'OK   ' if ok else 'WRONG'} {script:<22} expected {expected:<5} got {res.status:<5} "
                  f"{'(SECRET LEAKED) ' if leaked else ''}- {res.reason}")
    server.shutdown()
    print("\nAll self-tests passed." if not failures else f"\n{failures} self-test(s) failed.")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
