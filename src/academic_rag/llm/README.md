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

## Try it

- Ask the same question with two models from the Ask page's menu (Gemini Flash and Flash-Lite, for
  example), and compare the tool calls under **Behind the answer**. Which searches more? Which
  answers best?
