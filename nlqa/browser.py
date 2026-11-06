"""Thin Playwright wrapper: describe the page for the AI, and carry out the AI's chosen action."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from playwright.sync_api import Error as PlaywrightError
from playwright.sync_api import Page, sync_playwright

SNAPSHOT_JS = (Path(__file__).parent / "snapshot.js").read_text(encoding="utf-8")

# Phrases that mean a bot-check is in the way: the tool reports this instead of pretending to test.
BOT_CHECK = re.compile(
    r"verify you are human|are you a robot|checking your browser|cf-challenge|captcha|"
    r"press (and|&) hold|unusual traffic", re.I)


@dataclass
class Snapshot:
    url: str
    title: str
    elements: list[str]
    headings: list[str]
    text: str
    frames: dict[int, int] = field(default_factory=dict)  # element id -> frame index
    tabs: int = 1

    def render(self, max_text: int = 2500) -> str:
        parts = [
            f"URL: {self.url}",
            f"Title: {self.title}",
            f"Open tabs: {self.tabs} (you are on the newest one)" if self.tabs > 1 else "",
            "Headings: " + " | ".join(self.headings) if self.headings else "",
            "",
            "Interactive elements (use the number in brackets as `id`):",
            *(self.elements or ["(none)"]),
            "",
            f"Visible page text (first {max_text} chars):",
            self.text[:max_text] or "(empty)",
        ]
        return "\n".join(p for p in parts if p is not None)


class Browser:
    def __init__(self, headed: bool = False, slow_mo: int = 0):
        self._pw = sync_playwright().start()
        self._browser = self._pw.chromium.launch(headless=not headed, slow_mo=slow_mo)
        self._context = self._browser.new_context(viewport={"width": 1366, "height": 860})
        self._context.on("page", self._on_new_page)
        self.page: Page = self._context.new_page()
        self._last: Snapshot | None = None

    # A link that opens a new tab moves us to that tab, like a user would follow it.
    def _on_new_page(self, page: Page) -> None:
        self.page = page
        try:
            page.wait_for_load_state("domcontentloaded", timeout=10000)
        except PlaywrightError:
            pass

    def close(self) -> None:
        try:
            self._context.close()
            self._browser.close()
        finally:
            self._pw.stop()

    # ---------------------------------------------------------------- observe
    def snapshot(self) -> Snapshot:
        self._settle()
        page = self.page
        elements, headings, texts, frames = [], [], [], {}
        next_id = 1
        for index, frame in enumerate(page.frames):
            try:
                data = frame.evaluate(SNAPSHOT_JS, next_id)
            except PlaywrightError:
                continue  # detached / unreachable frame
            for i in range(next_id, data["nextId"]):
                frames[i] = index
            prefix = "" if index == 0 else f"(in iframe {index}) "
            elements += [prefix + line for line in data["elements"]]
            headings += data["headings"]
            if data["text"]:
                texts.append(data["text"])
            next_id = data["nextId"]
        snap = Snapshot(url=page.url, title=self._safe_title(), elements=elements, headings=headings,
                        text="\n".join(texts), frames=frames, tabs=len(self._context.pages))
        self._last = snap
        return snap

    def looks_like_bot_check(self, snap: Snapshot) -> bool:
        return bool(BOT_CHECK.search(snap.title + " " + snap.text[:3000]))

    def screenshot(self, path: Path) -> bytes | None:
        try:
            return self.page.screenshot(path=str(path), full_page=False)
        except PlaywrightError:
            return None

    def visible_text(self) -> str:
        texts = []
        for frame in self.page.frames:
            try:
                texts.append(frame.evaluate("() => document.body ? document.body.innerText : ''"))
            except PlaywrightError:
                pass
        return "\n".join(texts)

    # ---------------------------------------------------------------- act
    def act(self, action: dict) -> str:
        """Performs one action. Returns a short result description; raises ActionError on failure."""
        kind = action.get("action")
        page = self.page
        try:
            if kind == "goto":
                page.goto(action["url"], wait_until="domcontentloaded", timeout=30000)
                return f"opened {action['url']}"
            if kind == "back":
                page.go_back(wait_until="domcontentloaded", timeout=15000)
                return "went back"
            if kind == "scroll":
                page.mouse.wheel(0, -800 if action.get("text") == "up" else 800)
                return "scrolled"
            if kind == "wait":
                page.wait_for_timeout(min(int(action.get("ms") or 2000), 8000))
                return "waited"
            if kind == "press" and action.get("id") is None:
                page.keyboard.press(action.get("key") or "Enter")
                return f"pressed {action.get('key') or 'Enter'}"

            loc = self._locate(action.get("id"))
            if kind == "click":
                try:
                    loc.click(timeout=8000)
                except PlaywrightError as e:
                    if "intercept" not in str(e) and "not visible" not in str(e):
                        raise
                    loc.click(timeout=5000, force=True)  # something overlaps it; click anyway
                return "clicked"
            if kind == "fill":
                loc.fill(action.get("text", ""), timeout=8000)
                return "filled"
            if kind == "select":
                value = action.get("text", "")
                try:
                    loc.select_option(label=value, timeout=8000)
                except PlaywrightError:
                    loc.select_option(value=value, timeout=8000)
                return f"selected {value}"
            if kind in ("check", "uncheck"):
                (loc.check if kind == "check" else loc.uncheck)(timeout=8000)
                return kind + "ed"
            if kind == "hover":
                loc.hover(timeout=8000)
                return "hovered"
            if kind == "press":
                loc.press(action.get("key") or "Enter", timeout=8000)
                return f"pressed {action.get('key') or 'Enter'}"
            raise ActionError(f"unknown action '{kind}'")
        except PlaywrightError as e:
            raise ActionError(first_line(str(e))) from None

    def _locate(self, element_id):
        if element_id is None:
            raise ActionError("this action needs an element `id`")
        element_id = int(element_id)
        frame_index = (self._last.frames if self._last else {}).get(element_id)
        if frame_index is None:
            raise ActionError(f"element [{element_id}] is not in the current page snapshot")
        frame = self.page.frames[frame_index]
        return frame.locator(f'[data-nlqa-id="{element_id}"]').first

    def _settle(self) -> None:
        """Give the page a moment to finish what the last action started (navigation, XHR, animations)."""
        try:
            self.page.wait_for_load_state("domcontentloaded", timeout=10000)
            self.page.wait_for_load_state("networkidle", timeout=3000)
        except PlaywrightError:
            pass
        self.page.wait_for_timeout(300)

    def _safe_title(self) -> str:
        try:
            return self.page.title()
        except PlaywrightError:
            return ""


class ActionError(Exception):
    pass


def first_line(text: str) -> str:
    text = re.sub(r"\x1b\[[0-9;]*m", "", text)
    return text.strip().splitlines()[0][:300] if text.strip() else "error"
