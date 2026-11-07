"""The observe -> decide -> act loop, and the evidence check that guards against false passes."""
from __future__ import annotations

import re
import time
from urllib.parse import urljoin, urlsplit
from dataclasses import dataclass, field
from pathlib import Path

from .browser import ActionError, Browser
from .llm import LLM, LLMError
from .prompts import SYSTEM, build_prompt

ALLOWED = {"click", "fill", "select", "check", "uncheck", "hover", "press", "goto", "scroll", "wait", "back", "done"}


@dataclass
class Step:
    step: int
    thought: str
    action: dict
    summary: str
    result: str
    ok: bool
    url: str
    screenshot: str | None = None


@dataclass
class TestResult:
    test_case: str
    status: str = "ERROR"            # PASS | FAIL | ERROR
    reason: str = ""
    evidence: list[dict] = field(default_factory=list)
    steps: list[Step] = field(default_factory=list)
    start_url: str | None = None
    final_url: str = ""
    duration_s: float = 0.0
    model: str = ""
    llm_calls: int = 0
    final_screenshot: str | None = None


class Agent:
    def __init__(self, llm: LLM, variables: dict[str, str], out_dir: Path,
                 max_steps: int = 25, vision: bool = False, verbose: bool = True):
        self.llm = llm
        self.variables = variables
        self.out_dir = out_dir
        self.max_steps = max_steps
        self.vision = vision
        self.verbose = verbose

    # ------------------------------------------------------------------ helpers
    def mask(self, text: str) -> str:
        """Hide test-data values (passwords etc.) from logs, reports and the model."""
        for value in sorted((v for v in self.variables.values() if v and len(v) >= 3), key=len, reverse=True):
            text = text.replace(value, "****")
        return text

    def substitute(self, text: str) -> str:
        def repl(m):
            key = m.group(1).strip()
            if key not in self.variables:
                raise ActionError(f"unknown test data placeholder {{{{{key}}}}}")
            return self.variables[key]
        return re.sub(r"\{\{\s*([A-Za-z0-9_]+)\s*\}\}", repl, text or "")

    def log(self, msg: str) -> None:
        if self.verbose:
            print(self.mask(msg), flush=True)

    @staticmethod
    def describe(a: dict) -> str:
        kind = a.get("action")
        if kind == "goto":
            return f"goto {a.get('url')}"
        if kind == "fill":
            return f"fill [{a.get('id')}] with \"{a.get('text', '')}\""
        if kind == "select":
            return f"select \"{a.get('text', '')}\" in [{a.get('id')}]"
        if kind == "press":
            return f"press {a.get('key', 'Enter')}" + (f" in [{a.get('id')}]" if a.get("id") is not None else "")
        if kind in ("scroll", "wait", "back"):
            return kind + (f" {a.get('text') or a.get('ms') or ''}" if kind != "back" else "")
        return f"{kind} [{a.get('id')}]"

    @staticmethod
    def norm_url(url: str) -> str:
        s = urlsplit(url.strip())
        return f"{s.scheme.lower()}://{s.netloc.lower()}{s.path.rstrip('/') or '/'}".lower()

    def goto_problem(self, url: str, known: set[str], dead: set[str]) -> str | None:
        """Why a goto is not allowed, or None. Guessing URLs leads models into 404 loops."""
        n = self.norm_url(url)
        if n in dead:
            return f"{url} already returned 'Page not found'. Use the page's menus, tabs or search form instead."
        if n not in known:
            return (f"{url} was not given in the test case and is not a link on the pages seen so far, so it is "
                    "a guess and is not allowed. Use the visible links, menus, tabs or search forms. Note: "
                    "clicking a tab can change the page content without changing the URL.")
        return None

    # ------------------------------------------------------------------ evidence
    def check_evidence(self, browser: Browser, evidence: list[dict]) -> tuple[bool, list[str]]:
        """Re-reads the real page and confirms every piece of evidence the model gave for a PASS."""
        if not evidence:
            return False, ["no evidence given"]
        page_text = re.sub(r"\s+", " ", browser.visible_text()).lower()
        url = browser.page.url.lower()
        title = (browser.page.title() or "").lower()
        problems, strong = [], 0
        for ev in evidence:
            kind = (ev.get("type") or "").strip()
            value = self.substitute(str(ev.get("value") or "")).strip()
            v = re.sub(r"\s+", " ", value).lower()

            if kind in ("element_checked", "element_unchecked", "field_value"):
                # Proves the real state of a control, which visible text cannot (e.g. "is the box ticked?").
                problem = self._check_element(browser, kind, ev.get("id"), value)
                if problem:
                    problems.append(problem)
                else:
                    strong += 1
                continue

            if kind in ("text_visible", "url_contains", "title_contains") and (len(v) < 3 or v.replace(" ", "").isdigit()):
                continue  # "1" or "ok" appears on almost any page: not proof of anything, so it doesn't count
            if not v:
                problems.append(f"{kind}: empty value")
                continue
            strong += 1
            if kind == "url_contains":
                if v not in url:
                    problems.append(f"URL does not contain '{value}' (URL is {browser.page.url})")
            elif kind == "title_contains":
                if v not in title:
                    problems.append(f"title does not contain '{value}'")
            elif kind == "text_visible":
                if v not in page_text:
                    problems.append(f"text '{value}' is not visible on the page")
            elif kind == "text_not_visible":
                if v in page_text:
                    problems.append(f"text '{value}' IS visible on the page")
            else:
                strong -= 1
                problems.append(f"unknown evidence type '{kind}'")
        if not problems and strong == 0:
            problems.append("no specific evidence: values like '1' or 'ok' appear on almost any page; "
                            "give text, a URL part or an element state that only holds if the test passed")
        return not problems, problems

    @staticmethod
    def _check_element(browser: Browser, kind: str, element_id, expected: str) -> str | None:
        if element_id is None:
            return f"{kind} needs the element `id` from the current snapshot"
        try:
            loc = browser._locate(element_id)
            if kind == "field_value":
                actual = loc.evaluate(
                    "e => e.tagName === 'SELECT' ? (e.selectedOptions[0] ? e.selectedOptions[0].text : '') : (e.value ?? '')")
                if re.sub(r"\s+", " ", str(actual)).strip().lower() != re.sub(r"\s+", " ", expected).strip().lower():
                    return f"element [{element_id}] contains '{actual}', not '{expected}'"
                return None
            checked = loc.evaluate("e => !!(e.checked || e.getAttribute('aria-checked') === 'true')")
            if kind == "element_checked" and not checked:
                return f"element [{element_id}] is NOT checked"
            if kind == "element_unchecked" and checked:
                return f"element [{element_id}] IS checked"
            return None
        except Exception as e:  # stale id, detached element...
            return f"could not inspect element [{element_id}]: {str(e).splitlines()[0][:120]}"

    # ------------------------------------------------------------------ main loop
    def run(self, test_case: str, start_url: str | None, headed: bool = False) -> TestResult:
        res = TestResult(test_case=test_case, start_url=start_url, model=self.llm.name)
        started = time.time()
        self.out_dir.mkdir(parents=True, exist_ok=True)
        browser = Browser(headed=headed)
        note = None
        rejected_passes = 0
        repeated_failures: dict[str, int] = {}
        calls_before = getattr(self.llm, "calls", 0)
        # URLs the model may open directly: the start URL, URLs written in the test case, real links seen.
        known_urls = {self.norm_url(u) for u in re.findall(r"https?://[^\s,;'\"]+", test_case)}
        if start_url:
            known_urls.add(self.norm_url(start_url))
        dead_urls: set[str] = set()

        try:
            if start_url:
                browser.act({"action": "goto", "url": start_url})
            for step in range(1, self.max_steps + 1):
                snap = browser.snapshot()
                known_urls.update(self.norm_url(u) for u in snap.links)
                if browser.looks_like_bot_check(snap):
                    res.status, res.reason = "ERROR", "The site is showing a bot check / captcha; automation cannot continue."
                    break

                shot_name = f"step-{step:02d}.png"
                image = browser.screenshot(self.out_dir / shot_name)
                prompt = build_prompt(test_case, start_url, list(self.variables), [s.__dict__ for s in res.steps],
                                      self.mask(snap.render()), step, self.max_steps, note)
                note = None

                try:
                    action = self.llm.next_action(SYSTEM, prompt, image if self.vision else None)
                except LLMError as e:
                    res.status, res.reason = "ERROR", f"AI model error: {e}"
                    break

                kind = action.get("action")
                thought = str(action.get("thought", ""))
                self.log(f"\n[{step:02d}] {thought}")

                if kind not in ALLOWED:
                    note = f"'{kind}' is not a valid action. Use one of: {', '.join(sorted(ALLOWED))}."
                    res.steps.append(Step(step, thought, action, f"invalid action {kind}", note, False, snap.url, shot_name))
                    continue

                if kind == "done":
                    result = str(action.get("result", "")).lower()
                    reason = str(action.get("reason", ""))
                    evidence = action.get("evidence") or []
                    if result == "pass":
                        ok, problems = self.check_evidence(browser, evidence)
                        if ok:
                            res.status, res.reason, res.evidence = "PASS", reason, evidence
                            self.log(f"     PASS claimed, evidence verified: {evidence}")
                            break
                        rejected_passes += 1
                        self.log(f"     PASS claimed but evidence did not hold: {problems}")
                        res.steps.append(Step(step, thought, action, "claimed PASS",
                                              "rejected: " + "; ".join(problems), False, snap.url, shot_name))
                        if rejected_passes >= 2:
                            res.status = "FAIL"
                            res.reason = ("The model claimed the test passed, but its evidence was not on the page: "
                                          + "; ".join(problems))
                            break
                        note = ("Your PASS was rejected because the evidence did not match the real page: "
                                + "; ".join(problems) + ". Look again: either take more steps, give evidence copied "
                                "exactly from the current page, or report fail.")
                        continue
                    res.status = "FAIL" if result == "fail" else "ERROR"
                    res.reason, res.evidence = reason, evidence
                    self.log(f"     {res.status}: {reason}")
                    break

                summary = self.describe(action)
                key = summary
                if kind == "goto":
                    target = urljoin(browser.page.url, self.substitute(str(action.get("url", ""))))
                    problem = self.goto_problem(target, known_urls, dead_urls)
                    if problem:
                        note = "Navigation refused: " + problem
                        self.log(f"     {summary} -> REFUSED (guessed URL)")
                        res.steps.append(Step(step, thought, action, summary, "refused: " + problem, False,
                                              snap.url, shot_name))
                        continue
                try:
                    concrete = dict(action)
                    for f in ("text", "url"):
                        if f in concrete and isinstance(concrete[f], str):
                            concrete[f] = self.substitute(concrete[f])
                    outcome = browser.act(concrete)
                    ok = True
                    if kind in ("goto", "click") and browser.looks_like_not_found():
                        dead_urls.add(self.norm_url(browser.page.url))
                        outcome += " (the page says 404 / Page not found)"
                except ActionError as e:
                    outcome, ok = f"FAILED: {e}", False
                    repeated_failures[key] = repeated_failures.get(key, 0) + 1
                    if repeated_failures[key] >= 2:
                        note = f"The action '{summary}' has failed {repeated_failures[key]} times. Choose a different approach."
                self.log(f"     {summary} -> {outcome}")
                res.steps.append(Step(step, thought, action, summary, self.mask(outcome), ok, snap.url, shot_name))

                # Stuck detection: the same action failing again and again, or the same few actions
                # repeating, means the model is not making progress. Stop early and say so honestly.
                if repeated_failures.get(key, 0) >= 4:
                    res.status = "ERROR"
                    res.reason = f"Stuck: '{summary}' failed {repeated_failures[key]} times; could not operate this part of the page."
                    break
                recent = [s.summary for s in res.steps[-10:]]
                if len(recent) == 10 and len(set(recent)) <= 3:
                    res.status = "ERROR"
                    res.reason = ("Stuck in a loop: the last 10 actions only repeated "
                                  + ", ".join(sorted(set(recent))) + " without progress.")
                    break
            else:
                res.status = "ERROR"
                res.reason = f"Gave up after {self.max_steps} steps without reaching a verdict."

            final = "final.png"
            if browser.screenshot(self.out_dir / final):
                res.final_screenshot = final
            res.final_url = browser.page.url
        except Exception as e:  # never crash without a report
            res.status, res.reason = "ERROR", f"Unexpected error: {e}"
        finally:
            browser.close()
            res.duration_s = round(time.time() - started, 1)
            res.llm_calls = getattr(self.llm, "calls", 0) - calls_before
            res.reason = self.mask(res.reason)
        return res
