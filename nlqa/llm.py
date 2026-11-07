"""AI model clients. Any class with `next_action(system, prompt, image) -> dict` can drive the agent."""
from __future__ import annotations

import base64
import json
import os
import re
import time
from pathlib import Path
from typing import Protocol

import requests


class LLMError(Exception):
    pass


class LLM(Protocol):
    name: str

    def next_action(self, system: str, prompt: str, image: bytes | None = None) -> dict: ...


def parse_json(text: str) -> dict:
    """Models sometimes wrap JSON in ```fences``` or add a sentence; extract the object."""
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if fenced:
        text = fenced.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end < 0:
        raise LLMError(f"model did not return JSON: {text[:200]}")
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError as e:
        raise LLMError(f"invalid JSON from model: {e}: {text[:200]}") from None


class GeminiLLM:
    """Google Gemini via the REST API (no SDK needed). Free-tier friendly: retries on rate limits."""

    URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"

    def __init__(self, api_key: str, model: str = "gemini-3.5-flash-lite", min_interval: float = 0.0):
        if not api_key:
            raise LLMError("GEMINI_API_KEY is not set. Put it in a .env file (see .env.example).")
        self.api_key = api_key
        self.model = model
        self.name = f"gemini:{model}"
        self.min_interval = min_interval
        self._last_call = 0.0
        self.calls = 0
        self._available: list[str] | None = None
        self._blocked_until: dict[str, float] = {}  # model -> time it may be tried again
        # Optional preferred backups, e.g. GEMINI_FALLBACK_MODELS=gemini-3.8-flash-lite,gemini-3-flash
        self.fallbacks = [m.strip() for m in os.getenv("GEMINI_FALLBACK_MODELS", "").split(",") if m.strip()]

    def next_action(self, system: str, prompt: str, image: bytes | None = None) -> dict:
        parts = [{"text": prompt}]
        if image:
            parts.append({"inline_data": {"mime_type": "image/png", "data": base64.b64encode(image).decode()}})
        body = {
            "system_instruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": parts}],
            "generationConfig": {"temperature": 0, "responseMimeType": "application/json"},
        }

        tried: list[str] = []
        while True:
            outcome, detail = self._try_model(self.model, body)
            if outcome == "ok":
                return detail
            tried.append(self.model)
            if outcome == "fatal":
                raise LLMError(detail)
            # Remember for the rest of the run: out of quota / not available -> long block; busy -> 2 minutes.
            long_block = "quota" in str(detail) or "not available" in str(detail)
            self._blocked_until[self.model] = time.time() + (6 * 3600 if long_block else 120)
            # The model is overloaded, out of quota, timing out or not available: move to another one.
            nxt = self._next_model(tried)
            if not nxt:
                raise LLMError(f"No Gemini model answered (tried {', '.join(tried)}). Last problem: {detail}. "
                               "Google's servers may be busy; try again in a few minutes.")
            print(f"   ({self.model} unavailable: {detail[:120]}. Switching to {nxt})")
            self.model, self.name = nxt, f"gemini:{nxt}"

    def _try_model(self, model: str, body: dict) -> tuple[str, object]:
        """Returns ("ok", action) | ("switch", reason) | ("fatal", reason)."""
        problem = ""
        for attempt in range(3):
            wait = self.min_interval - (time.time() - self._last_call)
            if wait > 0:
                time.sleep(wait)
            self._last_call = time.time()
            try:
                r = requests.post(self.URL.format(model=model), json=body, timeout=30,
                                  headers={"x-goog-api-key": self.api_key})
            except requests.RequestException as e:
                # A model that doesn't answer within the timeout is usually overloaded: don't wait again.
                return "switch", f"no answer within 30s ({type(e).__name__})"

            if r.status_code == 429:
                delay = self._retry_delay(r) or 10
                if delay > 30:  # free-tier quota for this model is used up for now
                    return "switch", f"quota exhausted (retry in {delay:.0f}s)"
                print(f"   (Gemini {model}: rate limited, waiting {delay:.0f}s)")
                time.sleep(delay)
                problem = "rate limited"
                continue
            if r.status_code in (500, 502, 503, 504):
                problem = f"HTTP {r.status_code} {self._message(r)}"
                if attempt >= 1:  # busy twice in a row: try another model instead of waiting
                    return "switch", problem
                print(f"   (Gemini {model}: busy, retrying in 3s)")
                time.sleep(3)
                continue
            if r.status_code == 404:
                return "switch", f"not available to this key ({self._message(r)})"
            if r.status_code in (400, 401, 403):
                return "fatal", f"Gemini rejected the request ({r.status_code}). Check GEMINI_API_KEY. {r.text[:300]}"
            if r.status_code >= 300:
                return "switch", f"HTTP {r.status_code} {self._message(r)}"

            self.calls += 1
            data = r.json()
            try:
                text = "".join(p.get("text", "") for p in data["candidates"][0]["content"]["parts"])
            except (KeyError, IndexError):
                reason = (data.get("candidates") or [{}])[0].get("finishReason") or data.get("promptFeedback")
                return "fatal", f"empty response from Gemini ({reason})"
            try:
                return "ok", parse_json(text)
            except LLMError as e:
                problem = str(e)  # malformed JSON: ask again
                continue
        return "switch", problem or "kept failing"

    # ---------------------------------------------------------------- model discovery
    def available_models(self) -> list[str]:
        """Models this key can call for text generation. Flash-Lite first: on the free tier it allows
        ~500 requests/day versus ~20 for Flash, and it is fast enough for one-action-per-step work."""
        if self._available is not None:
            return self._available
        names: list[str] = []
        try:
            url, token = "https://generativelanguage.googleapis.com/v1beta/models", None
            for _ in range(5):
                r = requests.get(url, params={"pageSize": 200, **({"pageToken": token} if token else {})},
                                 headers={"x-goog-api-key": self.api_key}, timeout=30)
                if r.status_code != 200:
                    break
                data = r.json()
                for m in data.get("models", []):
                    name = m.get("name", "").removeprefix("models/")
                    if "generateContent" in m.get("supportedGenerationMethods", []) and name.startswith("gemini"):
                        names.append(name)
                token = data.get("nextPageToken")
                if not token:
                    break
        except requests.RequestException:
            pass
        skip = ("image", "tts", "audio", "embedding", "live", "native", "vision", "thinking-exp", "learnlm", "robotics",
                "omni", "banana", "transcribe", "computer-use", "customtools")
        names = [n for n in dict.fromkeys(names) if not any(s in n for s in skip)]

        def rank(n: str):
            tier = 0 if "flash-lite" in n else 1 if "flash" in n else 2
            preview = 1 if ("preview" in n or "exp" in n) else 0
            version = [int(x) for x in re.findall(r"\d+", n)[:2]]
            return (tier, preview, [-v for v in version])

        self._available = sorted(names, key=rank)
        return self._available

    def _next_model(self, tried: list[str]) -> str | None:
        now = time.time()
        for name in self.fallbacks + self.available_models():
            if name and name not in tried and self._blocked_until.get(name, 0) <= now:
                return name
        return None

    @staticmethod
    def _message(r: requests.Response) -> str:
        try:
            return str(r.json()["error"]["message"])[:150]
        except (ValueError, KeyError, TypeError):
            return r.text[:150]

    @staticmethod
    def _retry_delay(r: requests.Response) -> float | None:
        try:
            for d in r.json()["error"]["details"]:
                if "retryDelay" in d:
                    return float(str(d["retryDelay"]).rstrip("s")) + 1
        except (ValueError, KeyError, TypeError):
            pass
        return None


class ScriptedLLM:
    """Replays pre-written actions from a JSON file. Used to test the tool itself without an API key."""

    def __init__(self, path: str):
        self.actions = json.loads(Path(path).read_text(encoding="utf-8"))
        self.name = f"scripted:{Path(path).name}"
        self.calls = 0

    def next_action(self, system: str, prompt: str, image: bytes | None = None) -> dict:
        if self.calls >= len(self.actions):
            return {"thought": "script exhausted", "action": "done", "result": "error", "reason": "script ended"}
        action = self.actions[self.calls]
        self.calls += 1
        return action
