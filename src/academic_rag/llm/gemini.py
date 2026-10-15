"""Google's Gemini API, over plain HTTPS (no SDK). Needs GEMINI_API_KEY.

Gemini is a web API, so the agent loop runs here, in our code:

    1. send the conversation and the tool list
    2. Gemini answers with text, or asks for tools ("functionCall")
    3. run each tool (tools.call) and send the results back ("functionResponse")
    4. repeat until it answers without asking for a tool
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from typing import TYPE_CHECKING

import httpx

from .base import ProviderError

if TYPE_CHECKING:
    from ..tools import Toolbox

API = "https://generativelanguage.googleapis.com/v1beta/models/{model}:streamGenerateContent"  # streamed answers
MAX_TOOL_ROUNDS = 10  # after this many rounds of tool calls, it must answer with what it has
LAST_ROUND = "That is all the looking up there is time for. Answer the student now, from what you found."  # round 11
THINKING = {"low": "low", "medium": "medium", "high": "high"}  # our effort -> Gemini's thinkingLevel
BUSY = {429, 503}  # rate-limited or overloaded: worth a few more tries
RETRIES = 4  # tries after the first, when Google is busy
SILENCE = 120  # seconds without a byte from Google before giving up on a request
TRY_ANOTHER = ("Google's servers are busy with this model right now: "  # added to a "busy" error
               "pick another model in the menu, or try again later.")


class GeminiProvider:
    """Answers with the Gemini API, running the tool loop in our own code."""

    name = "gemini"
    default_model = "gemini-3.5-flash-lite"  # the most free-tier quota; pick Flash on the Ask page for more depth

    def __init__(self, api_key: str, model: str = default_model, client: httpx.Client | None = None) -> None:
        self.model = model
        self._key = api_key
        self._http = client or httpx.Client(timeout=httpx.Timeout(SILENCE, connect=10.0))

    def answer(self, system: str, prompt: str, tools: Toolbox, effort: str = "medium") -> Iterator[dict]:
        """The agent loop: ask, run the tools Gemini asks for, send their results back, and
        ask again, until Gemini answers without a tool (or MAX_TOOL_ROUNDS is reached)."""
        contents = [{"role": "user", "parts": [{"text": prompt}]}]
        declarations = [{"name": s["name"], "description": s["description"], "parameters": s["input_schema"]}
                        for s in tools.specs()]
        for round_ in range(MAX_TOOL_ROUNDS + 1):
            last = round_ == MAX_TOOL_ROUNDS
            body = {
                "systemInstruction": {"parts": [{"text": system}]},
                "contents": contents,
                "generationConfig": {"thinkingConfig": {"thinkingLevel": THINKING.get(effort, "medium")}},
            }
            if last:  # no tools offered at all, and a word to answer now
                contents[-1]["parts"].append({"text": LAST_ROUND})
            else:
                body["tools"] = [{"functionDeclarations": declarations}]

            # Gemini's turn: text goes to the page as it arrives; tool requests are kept
            parts = []  # everything Gemini sent this round, kept to send back unchanged
            for part in self._stream(body):
                parts.append(part)
                if part.get("text") and not part.get("thought"):
                    yield {"type": "text", "text": part["text"]}
            calls = [p["functionCall"] for p in parts if "functionCall" in p]
            if not calls or last:
                return

            # The model's turn goes back exactly as it came, including its "thoughtSignature"s.
            contents.append({"role": "model", "parts": parts})
            results = []
            for call in calls:
                args = call.get("args") or {}
                yield {"type": "tool", "name": call["name"], "args": args}
                response = {"name": call["name"], "response": {"result": tools.call(call["name"], args)}}
                if call.get("id"):
                    response["id"] = call["id"]
                results.append({"functionResponse": response})
            contents.append({"role": "user", "parts": results})

    def _stream(self, body: dict) -> Iterator[dict]:
        """One streamed request. The answer arrives as server-sent events: lines that start
        with `data: ` and hold one JSON chunk each. Yields every part of every chunk."""
        try:
            yield from self._try(body)
        except httpx.TimeoutException:
            raise ProviderError(f"{self.model} sent nothing for {SILENCE} seconds. {TRY_ANOTHER}") from None
        except httpx.TransportError as exc:
            raise ProviderError(f"Could not reach Gemini ({type(exc).__name__}): "
                                "check your internet connection.") from None

    def _try(self, body: dict) -> Iterator[dict]:
        """Send one request, waiting and trying again while Google is busy (429, 503)."""
        for attempt in range(RETRIES + 1):
            # the key goes in a header, never in the URL, so it cannot end up in a log or error message
            with self._http.stream("POST", API.format(model=self.model), params={"alt": "sse"}, json=body,
                                   headers={"x-goog-api-key": self._key}) as resp:
                if resp.status_code in BUSY and attempt < RETRIES:
                    resp.read()  # busy or over the per-minute quota: wait as long as Google says, then retry
                    time.sleep(_retry_delay(resp) or 2 * (attempt + 1))
                    continue
                if resp.status_code != 200:
                    resp.read()
                    message = _message(resp).strip()
                    if resp.status_code in BUSY:
                        message = f"{message.rstrip('.')}. {TRY_ANOTHER}"
                    raise ProviderError(f"Gemini API error {resp.status_code}: {message}")
                for line in resp.iter_lines():
                    if line.startswith("data: "):
                        chunk = json.loads(line[len("data: "):])
                        for candidate in chunk.get("candidates", []):
                            yield from (candidate.get("content") or {}).get("parts", [])
                return


def _message(resp: httpx.Response) -> str:
    """The error message in Google's JSON answer, or the start of the answer itself."""
    try:
        return resp.json()["error"]["message"]
    except (ValueError, KeyError, TypeError):
        return resp.text[:300]


def _retry_delay(resp: httpx.Response) -> float | None:
    """Seconds Google asks us to wait ("retryDelay": "31s"), at most a minute."""
    try:
        details = resp.json()["error"].get("details", [])
        delay = next(d["retryDelay"] for d in details if "retryDelay" in d)
        return min(float(delay.rstrip("s")), 60.0)
    except (ValueError, KeyError, TypeError, AttributeError, StopIteration):
        return None
