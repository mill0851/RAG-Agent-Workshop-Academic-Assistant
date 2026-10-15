"""llm/: choosing a provider, the model catalog, and how each provider answers with tools:
Gemini through our own loop, Claude Code and Codex through MCP. Fake CLIs and a fake API."""

import io
import json
from pathlib import Path

import httpx
import pytest
import respx

from academic_rag.config import Settings
from academic_rag.llm import ProviderError, get_provider, model_choices
from academic_rag.llm import catalog, cli, gemini
from academic_rag.llm.claude import ClaudeProvider
from academic_rag.llm.codex import CodexProvider
from academic_rag.llm.gemini import GeminiProvider


class FakeTools:
    """Stands in for a Toolbox: one tool, and a record of what was called."""

    mcp_url = "http://127.0.0.1:8765/mcp/s3cret"

    def __init__(self):
        self.called = []

    def specs(self):
        return [{"name": "find_text", "description": "Find exact text.",
                 "input_schema": {"type": "object", "properties": {"text": {"type": "string"}}}}]

    def call(self, name, args):
        self.called.append((name, args))
        return "HW3 2026.pdf (p. 1): Problems 3.5 and 3.9"


# ----------------------------------------------------------- choosing one
def test_choosing_a_provider_explains_what_is_missing():
    with pytest.raises(ProviderError, match="LLM_PROVIDER"):
        get_provider(Settings())
    with pytest.raises(ProviderError, match="GEMINI_API_KEY"):
        get_provider(Settings(llm_provider="gemini"))
    assert get_provider(Settings(llm_provider="gemini", gemini_api_key="k")).model == "gemini-3.5-flash-lite"
    assert get_provider(Settings(llm_provider="codex", llm_model="o4")).model == "o4"


def test_a_provider_picked_on_the_page_overrides_env():
    settings = Settings(llm_provider="codex", llm_model="o4", gemini_api_key="k")
    picked = get_provider(settings, "gemini", "gemini-3.5-flash-lite")
    assert (picked.name, picked.model) == ("gemini", "gemini-3.5-flash-lite")
    assert get_provider(settings, "codex", "").model == ""  # empty: that provider's own default
    with pytest.raises(ProviderError, match="Not a valid model name"):
        get_provider(settings, "codex", "gpt & del *")  # model names end up on a command line


def test_model_choices_show_what_works_here(monkeypatch):
    monkeypatch.setattr(catalog.shutil, "which", lambda name: "/bin/codex" if name == "codex" else None)
    choices = model_choices(Settings(llm_provider="claude", llm_model="claude-haiku-5-5"))
    by_provider = lambda name: [c for c in choices if c["provider"] == name]  # noqa: E731

    assert [(c["provider"], c["model"]) for c in choices] == [  # two per AI, in the menu's order
        ("gemini", "gemini-3.5-flash-lite"), ("gemini", "gemini-3.8-flash"),
        ("codex", "gpt-6-astra"), ("codex", "gpt-5.6-sol"),
        ("claude", "claude-opus-5-5"), ("claude", "claude-sonnet-5-5"),
        ("claude", "claude-haiku-5-5")]  # plus the .env model, which is not one of them
    assert by_provider("claude")[-1]["label"] == "claude · claude-haiku-5-5 (from .env)"
    assert all(c["available"] for c in by_provider("codex"))
    assert not any(c["available"] for c in by_provider("gemini"))
    assert by_provider("gemini")[0]["problem"] == "set GEMINI_API_KEY in .env"
    assert by_provider("claude")[0]["problem"] == "Claude Code is not installed"
    assert all(c["efforts"] == ["medium", "high"] for c in choices)  # the reasoning level menu


def test_a_listed_env_model_is_not_offered_twice():
    choices = model_choices(Settings(llm_provider="codex", llm_model="gpt-6-astra"))
    assert [c["model"] for c in choices].count("gpt-6-astra") == 1 and len(choices) == 6


# ------------------------------------------------------------------ Gemini
def sse(*parts_per_chunk):
    """A streamed Gemini response: one server-sent event per chunk of parts."""
    return "".join(f"data: {json.dumps({'candidates': [{'content': {'parts': parts}}]})}\r\n\r\n"
                   for parts in parts_per_chunk)


def test_gemini_runs_the_tool_loop_itself():
    call = {"functionCall": {"name": "find_text", "args": {"text": "HW3"}}, "thoughtSignature": "sig-1"}
    replies = [httpx.Response(200, text=sse([{"text": "Let me look. "}], [call])),
               httpx.Response(200, text=sse([{"text": "Problems 3.5 "}], [{"text": "and 3.9."}]))]
    tools = FakeTools()
    with respx.mock() as router:
        route = router.post(url__regex=r".*/models/gemini-x:streamGenerateContent").mock(side_effect=replies)
        events = list(GeminiProvider("SECRET-KEY", "gemini-x").answer("rules", "question", tools, "high"))
        first, second = (json.loads(c.request.content) for c in route.calls)
        request = route.calls[0].request

    assert events == [{"type": "text", "text": "Let me look. "},
                      {"type": "tool", "name": "find_text", "args": {"text": "HW3"}},
                      {"type": "text", "text": "Problems 3.5 "}, {"type": "text", "text": "and 3.9."}]
    assert tools.called == [("find_text", {"text": "HW3"})]  # our code ran the tool
    assert request.headers["x-goog-api-key"] == "SECRET-KEY" and "SECRET-KEY" not in str(request.url)
    assert first["systemInstruction"]["parts"][0]["text"] == "rules" and request.url.params["alt"] == "sse"
    assert first["tools"][0]["functionDeclarations"][0]["name"] == "find_text"
    assert first["generationConfig"]["thinkingConfig"]["thinkingLevel"] == "high"
    # the second request sends Gemini's turn back unchanged (with its signature), then the tool's result
    model_turn, results = second["contents"][1], second["contents"][2]
    assert model_turn == {"role": "model", "parts": [{"text": "Let me look. "}, call]}
    assert results["parts"][0]["functionResponse"]["response"]["result"].startswith("HW3 2026.pdf")


def test_gemini_must_answer_after_the_last_tool_round(monkeypatch):
    monkeypatch.setattr(gemini, "MAX_TOOL_ROUNDS", 1)
    call = {"functionCall": {"name": "find_text", "args": {"text": "HW3"}}}
    replies = [httpx.Response(200, text=sse([call])),
               httpx.Response(200, text=sse([call], [{"text": "Here is what I found."}]))]
    tools = FakeTools()
    with respx.mock() as router:
        route = router.post(url__regex=r".*streamGenerateContent").mock(side_effect=replies)
        events = list(GeminiProvider("k", "gemini-x").answer("s", "p", tools))
        last = json.loads(route.calls[1].request.content)
    assert "tools" not in last and last["contents"][-1]["parts"][-1]["text"] == gemini.LAST_ROUND
    assert len(tools.called) == 1  # a tool asked for in the last round is not run
    assert events[-1] == {"type": "text", "text": "Here is what I found."}


def test_gemini_errors_are_readable_and_a_busy_model_is_tried_again(monkeypatch):
    with respx.mock() as router:
        router.post(url__regex=r".*streamGenerateContent").mock(
            return_value=httpx.Response(400, json={"error": {"message": "API key not valid."}}))
        with pytest.raises(ProviderError, match="400: API key not valid"):
            list(GeminiProvider("bad").answer("s", "p", FakeTools()))

    waited = []
    monkeypatch.setattr(gemini.time, "sleep", waited.append)
    busy = httpx.Response(503, json={"error": {"message": "high demand"}})
    quota = httpx.Response(429, json={"error": {"message": "quota", "details": [
        {"@type": "type.googleapis.com/google.rpc.RetryInfo", "retryDelay": "31s"}]}})
    with respx.mock() as router:
        router.post(url__regex=r".*streamGenerateContent").mock(
            side_effect=[busy, quota, httpx.Response(200, text=sse([{"text": "Fine."}]))])
        assert list(GeminiProvider("k").answer("s", "p", FakeTools())) == [{"type": "text", "text": "Fine."}]
    assert waited == [2, 31.0]  # a short pause, then as long as Google asked

    with respx.mock() as router:  # still busy after every retry: say what to do
        router.post(url__regex=r".*streamGenerateContent").mock(return_value=busy)
        with pytest.raises(ProviderError, match="503: high demand. .*pick another model"):
            list(GeminiProvider("k").answer("s", "p", FakeTools()))


def test_gemini_going_silent_is_a_readable_error():
    with respx.mock() as router:
        router.post(url__regex=r".*streamGenerateContent").mock(side_effect=httpx.ReadTimeout("timed out"))
        with pytest.raises(ProviderError, match="gemini-x sent nothing for 120 seconds. .*pick another model"):
            list(GeminiProvider("k", "gemini-x").answer("s", "p", FakeTools()))
        router.post(url__regex=r".*streamGenerateContent").mock(side_effect=httpx.ConnectError("no route"))
        with pytest.raises(ProviderError, match="internet connection"):
            list(GeminiProvider("k", "gemini-x").answer("s", "p", FakeTools()))


# --------------------------------------------------------------- the CLIs
class Stdin(io.StringIO):
    """Keeps what was written even after the provider closes it."""

    def close(self):
        self.written = self.getvalue()
        super().close()


class FakeProcess:
    """Stands in for a CLI started with subprocess.Popen."""

    def __init__(self, lines, returncode=0):
        self.stdin = Stdin()
        self.stdout = iter(line + "\n" for line in lines)
        self.returncode = returncode

    def wait(self, timeout=None):
        return self.returncode

    def poll(self):
        return self.returncode

    def kill(self):
        pass


@pytest.fixture
def fake_cli(monkeypatch):
    """Pretend a CLI is installed; returns the list of (args, Popen keywords, process) it was started with.
    The keywords also get "files": the files in its working folder when it started."""
    started = []

    def install(lines, returncode=0):
        monkeypatch.setattr(cli.shutil, "which", lambda name: f"/bin/{name}")
        process = FakeProcess(lines, returncode)

        def popen(args, **kw):
            kw["files"] = {p.name: p.read_text(encoding="utf-8") for p in Path(kw["cwd"]).iterdir() if p.is_file()}
            started.append((args, kw, process))
            return process
        monkeypatch.setattr(cli.subprocess, "Popen", popen)
        return started
    return install


def test_claude_reaches_only_our_tools_over_mcp(fake_cli):
    started = fake_cli([
        '{"type": "system", "subtype": "init"}',
        '{"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "mcp__acad__find_text", '
        '"input": {"text": "HW3"}}]}}',
        '{"type": "stream_event", "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "Yes "}}}',
        '{"type": "stream_event", "event": {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "[1]."}}}',
        '{"type": "result", "result": "Yes [1]."}',
    ])
    events = list(ClaudeProvider("sonnet").answer("rules", "question", FakeTools(), "high"))
    assert events == [{"type": "tool", "name": "find_text", "args": {"text": "HW3"}},
                      {"type": "text", "text": "Yes "}, {"type": "text", "text": "[1]."}]
    args, kw, process = started[0]
    value = lambda flag: args[args.index(flag) + 1]  # noqa: E731
    assert args[:2] == ["/bin/claude", "-p"] and value("--tools") == ""  # no built-in tools
    assert value("--mcp-config") == "mcp.json" and "--strict-mcp-config" in args
    assert value("--allowedTools") == "mcp__acad" and value("--effort") == "high"
    assert value("--system-prompt-file") == "system.txt" and args[-2:] == ["--model", "sonnet"]
    assert json.loads(kw["files"]["mcp.json"]) == {"mcpServers": {"acad": {"type": "http", "url": FakeTools.mcp_url}}}
    assert kw["files"]["system.txt"] == "rules" and process.stdin.written == "question"
    assert kw["env"]["ENABLE_TOOL_SEARCH"] == "false"


def test_codex_reaches_only_our_tools_over_mcp(fake_cli):
    started = fake_cli([
        '{"type": "thread.started", "thread_id": "t"}',
        "some log line",
        '{"type": "item.completed", "item": {"type": "agent_message", "text": "I will look for HW 3."}}',
        '{"type": "item.started", "item": {"type": "mcp_tool_call", "server": "acad", "tool": "find_text", '
        '"arguments": {"text": "HW3"}, "status": "in_progress"}}',
        '{"type": "item.completed", "item": {"type": "agent_message", "text": "HW 3 is problems 3.5 and 3.9 [1]."}}',
        '{"type": "turn.completed"}',
    ])
    events = list(CodexProvider("gpt-x").answer("rules", "question", FakeTools(), "high"))
    assert events == [{"type": "note", "text": "I will look for HW 3."},
                      {"type": "tool", "name": "find_text", "args": {"text": "HW3"}},
                      {"type": "text", "text": "HW 3 is problems 3.5 and 3.9 [1]."}]  # only the last message answers
    args, _, process = started[0]
    assert args[1:3] == ["exec", "--json"] and "read-only" in args and args[-3:] == ["--model", "gpt-x", "-"]
    assert "model_reasoning_effort=high" in args and f'mcp_servers.acad.url="{FakeTools.mcp_url}"' in args
    assert args[args.index("shell_tool") - 1] == "--disable"  # no shell: our tools only
    assert process.stdin.written == "rules\n\nquestion"  # no system-prompt flag: rules go first


def test_cli_failures_become_provider_errors(fake_cli, monkeypatch):
    fake_cli(["Error: not logged in"], returncode=1)
    with pytest.raises(ProviderError, match="not logged in"):
        list(CodexProvider().answer("s", "p", FakeTools()))
    fake_cli(['{"type": "turn.failed", "error": {"message": "usage limit reached"}}'])
    with pytest.raises(ProviderError, match="usage limit"):
        list(CodexProvider().answer("s", "p", FakeTools()))
    fake_cli(['{"type": "result", "is_error": true, "result": "Not logged in"}'])
    with pytest.raises(ProviderError, match="Not logged in"):
        list(ClaudeProvider().answer("s", "p", FakeTools()))
    monkeypatch.setattr(cli.shutil, "which", lambda name: None)
    with pytest.raises(ProviderError, match="was not found"):
        list(ClaudeProvider().answer("s", "p", FakeTools()))
