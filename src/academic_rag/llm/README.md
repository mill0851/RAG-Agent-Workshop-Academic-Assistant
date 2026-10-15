# llm/: talking to language models

> **You don't need this folder for the tasks.** Read it to see the agent loop itself: the few lines
> that let a model call tools until it can answer.

Everything that sends a question to an AI lives here, behind one small interface, so the rest of the
project never cares which AI is answering. [agent/](../agent/README.md) calls it once per question.

| File | What it does |
|---|---|
| [base.py](base.py) | The interface every provider follows, and `get_provider()`, which picks one |
| [gemini.py](gemini.py) | Google's Gemini API over HTTPS, with the tool loop written out in our code |
| [claude.py](claude.py) · [codex.py](codex.py) | Claude Code (`claude -p`) and Codex (`codex exec`), reaching our tools over MCP |
| [cli.py](cli.py) | Runs either program once and reads the events it prints |
| [catalog.py](catalog.py) | The models the Ask page's menu offers, and whether each works on this computer |

## Architecture diagram

[architecture.mmd](architecture.mmd) draws how `get_provider()` picks an AI, Gemini's tool loop in
our own code, and how Claude Code and Codex run their own loop with our tools over MCP. It is a
[Mermaid](https://mermaid.js.org) diagram: plain text that a Mermaid viewer draws as a flowchart. To
see it:

- **In VS Code:** install a Mermaid extension, such as *Mermaid Chart*, open `architecture.mmd`, and
  open its preview.
- **In a browser:** paste the file into the editor at https://mermaid.live.
- **As an image file:** run this in this folder. The first run downloads a browser to draw with.

  ```bash
  npx -p @mermaid-js/mermaid-cli mmdc -i architecture.mmd -o architecture.svg
  ```

## The idea

**One method.** Every provider has `answer(system, prompt, tools, effort)`, which yields the same
events: `tool` when it calls a tool, `note` when it says what it is doing, `text` for each piece of
the answer. `tools` is the question's Toolbox ([tools/](../tools/README.md)), the same for every AI.
So swapping Gemini for Codex is a setting, not a code change.

**Two kinds of AI, one set of tools.**

| | Gemini | Claude Code, Codex |
|---|---|---|
| What it is | a web API | an agent program on your computer, run once per question |
| Login | an API key in `.env` | the program's own login (your Claude or ChatGPT account) |
| Who runs the tool loop | **our code**, below | **the program**, calling our tools over MCP |

### The agent loop, for Gemini

`answer()` in [gemini.py](gemini.py), abridged:

```python
contents = [{"role": "user", "parts": [{"text": prompt}]}]
for round_ in range(MAX_TOOL_ROUNDS + 1):
    body = {"systemInstruction": ..., "contents": contents,
            "tools": [{"functionDeclarations": [...]}],            # name, description, parameters
            "generationConfig": {"thinkingConfig": {"thinkingLevel": effort}}}
    parts = list(self._stream(body))                                 # text is yielded as it arrives
    calls = [p["functionCall"] for p in parts if "functionCall" in p]
    if not calls:
        return                                                       # it answered: done
    contents.append({"role": "model", "parts": parts})              # its turn, sent back unchanged
    contents.append({"role": "user", "parts": [
        {"functionResponse": {"name": c["name"], "response": {"result": tools.call(c["name"], c["args"])}}}
        for c in calls]})                                            # the tools' results
```

That loop is what makes something an agent: the model decides, the code acts, the model sees the
result and decides again. After `MAX_TOOL_ROUNDS` rounds it must answer with what it has.

### Claude Code and Codex: their loop, our tools

They run the same loop inside their own program. We start them in an empty temporary folder with
their own tools switched off, and give them the address of our tools. **MCP** (Model Context
Protocol) is the standard way to plug tools into an agent program; under the name it is small JSON
messages that the server answers ([tools/mcp.py](../tools/mcp.py)):

```
-> {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "find_text", "arguments": {"text": "HW3"}}}
<- {"jsonrpc": "2.0", "id": 3, "result": {"content": [{"type": "text", "text": "- HW3 | file: ..."}]}}
```

Each question gets its own MCP address with an unguessable token, closed when the answer ends. So
every AI sees the same material through the same tools, and none can read anything outside your
course folders.

## Inside one answer, step by step

`agent.ask()` calls `provider.answer(system, prompt, tools, effort)` once per question. Which
provider that is comes from `get_provider()` in [base.py](base.py): the AI picked on the Ask page,
or `LLM_PROVIDER` and `LLM_MODEL` in `.env`.

### Gemini: our loop, in gemini.py

```
answer()
for up to 11 requests:
1. POST .../models/<model>:streamGenerateContent    the system prompt, the conversation so far,
                                                    the tool list, and the effort as thinkingLevel
2. read the streamed answer                         text goes to the page as it arrives
3. no tool requested?                               done: that was the answer
4. run each requested tool                          Toolbox.call(), then its result goes back
5. after 10 rounds of tools                         one last request with no tools: answer now
```

**Sent back unchanged.** Gemini's own turn, including its hidden `thoughtSignature`s, is sent back
exactly as it came. Gemini needs that to continue its reasoning after a tool result.

**When Google is busy.** A 429 (over the per-minute quota) or 503 (overloaded) is retried up to 4
times, waiting as long as Google asks, at most a minute. A request that sends nothing for 120
seconds gives up with a message to pick another model. The API key goes in a header, never in the
address, so it cannot end up in a log.

### Claude Code and Codex: their loop, our tools

Both are agent programs on your computer. `run_cli()` in [cli.py](cli.py) starts one per question,
in an empty temporary folder, sends the prompt on stdin, and reads the JSON events it prints:

| | Claude Code ([claude.py](claude.py)) | Codex ([codex.py](codex.py)) |
|---|---|---|
| Command | `claude -p --output-format stream-json` | `codex exec --json` |
| Its own tools | Off: `--tools ""` | Off: no shell, a read-only sandbox |
| Our tools | `--mcp-config mcp.json`, the question's MCP address | `-c mcp_servers.acad.url=...` |
| The system prompt | A file: `--system-prompt-file system.txt` | First in the prompt: Codex has no flag for it |
| Effort | `--effort medium` or `high` | `-c model_reasoning_effort=...` |
| Text | Streamed word by word | Each message whole: the last one is the answer, earlier ones become notes |

If the page is closed mid-answer, `run_cli()` stops the program. If it exits with an error, its last
lines become the message on the Ask page.

### What each provider sends the page

| Event | Gemini | Claude Code | Codex |
|---|---|---|---|
| `tool` | Each tool it asks for | Each `tool_use` in its messages | Each MCP tool call it starts |
| `note` | | | A message it wrote before a tool call |
| `text` | As it arrives | As it is written | The final message, whole |

## Try it

- Ask the same question with two models from the Ask page's menu (Gemini Flash and Flash-Lite, for
  example), and compare the tool calls under **Behind the answer**. Which searches more? Which
  answers best?
