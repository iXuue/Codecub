# CodeCub P0.7 Streaming Run Status Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build true OpenAI-compatible/Qwen streaming responses and a Codex-like active run status display with elapsed time, without exposing hidden model reasoning.

**Architecture:** Add streaming support at the model client boundary, route safe runtime progress through the existing JSONL app protocol, and update the React chat state to append `assistant_delta` into one active assistant message. The backend streams only safe final-answer text; tool XML, hidden thinking blocks, and provider internals stay out of the UI.

**Tech Stack:** Python `urllib` SSE parsing, CodeCub JSONL app protocol, Electron subprocess bridge, React + TypeScript + Vitest, pytest.

---

## Requirement Summary

P0.7 implements the requirement added to `.codecub/spec/2026-06-11-codecub-p0-requirements.md`:

- OpenAI-compatible/Qwen path must use provider-native streaming.
- Backend must emit multiple `assistant_delta` events before final completion for non-trivial streamed final answers.
- Frontend must render partial assistant text as it arrives.
- Final `assistant_message` must not duplicate previously streamed text.
- Frontend must show an active run status area with elapsed time and latest safe activity.
- UI must not display hidden chain-of-thought, provider reasoning blocks, tool XML, or private internals.
- Ollama and Anthropic-compatible native streaming are explicitly P1; P0.7 keeps their compatibility fallback.

## Confirmed Scope

### In Scope

- OpenAI-compatible native streaming in `codecub/models.py`.
- App-mode event routing in `codecub/app_runner.py`.
- Runtime safe status hooks in `codecub/runtime.py`.
- Protocol type/test coverage for `run_status`.
- React state updates for `assistant_delta` and `run_status`.
- Chat UI status strip with elapsed timer.
- Python and desktop tests.

### Out of Scope

- Native Ollama streaming.
- Native Anthropic-compatible streaming.
- WebSocket backend service.
- Persisting token-by-token deltas into session history.
- Displaying model chain-of-thought or raw provider reasoning.
- Redesigning the full chat layout.

## Expected Files

- Modify: `codecub/app_protocol.py`
  - Add `run_status` to known app event types.
- Modify: `codecub/models.py`
  - Add optional streaming model client path for OpenAI-compatible responses.
  - Keep existing `complete()` compatibility API.
- Modify: `codecub/runtime.py`
  - Emit safe app events through the existing `event_handler`.
  - Route final-answer text deltas only after detecting a `<final>` block.
- Modify: `codecub/app_runner.py`
  - Convert runtime app events into JSONL `run_status` and `assistant_delta`.
  - Track whether a run already streamed deltas before deciding whether to emit compatibility whole-answer delta.
- Modify: `tests/test_app_runner.py`
  - Cover native-like streamed final answer, fallback delta, status events, and no tool XML leak.
- Modify: `tests/test_pico.py` or create `tests/test_runtime_streaming.py`
  - Cover final-answer stream filtering at runtime level.
- Modify: `desktop/src/state/backendEvents.ts`
  - Add typed `run_status` event.
- Modify: `desktop/src/state/chatState.ts`
  - Append `assistant_delta` into active assistant message.
  - Replace streamed message with final `assistant_message` without duplication.
  - Store latest active run status.
- Modify: `desktop/src/components/ChatView.tsx`
  - Show active run status strip and elapsed time.
- Modify: `desktop/src/i18n/zh-CN.ts`
  - Add Chinese labels for status fallback text.
- Modify: `desktop/src/i18n/en-US.ts`
  - Add English labels for status fallback text.
- Modify: `desktop/src/styles/app.css`
  - Add compact status strip styling.
- Modify: `desktop/tests/chatState.test.ts`
  - Cover delta append/final dedupe/status update.
- Create: `desktop/tests/ChatViewRunStatus.test.tsx`
  - Cover elapsed timer and status display.
- Optional Modify: `.codecub/spec/2026-06-11-codecub-p0-requirements.md`
  - Only if execution discovers a needed clarification.

## Event Contract

### `run_status`

Backend JSONL event:

```json
{
  "type": "run_status",
  "timestamp": "2026-06-15T00:00:00Z",
  "session_id": "session-id",
  "run_id": "run-id",
  "payload": {
    "phase": "model_streaming",
    "label": "Receiving model response",
    "detail": "qwen-flash",
    "started_at": "2026-06-15T00:00:00Z",
    "elapsed_ms": 1200
  }
}
```

Required phases for P0.7:

- `building_context`
- `model_request`
- `model_streaming`
- `tool_running`
- `waiting_approval`
- `finalizing`
- `completed`
- `failed`
- `canceled`

### `assistant_delta`

Backend JSONL event:

```json
{
  "type": "assistant_delta",
  "timestamp": "2026-06-15T00:00:00Z",
  "session_id": "session-id",
  "run_id": "run-id",
  "payload": {
    "text": "partial safe final answer text"
  }
}
```

Rules:

- Emit only text intended for the final user-visible assistant response.
- Do not emit `<final>`, `</final>`, `<tool ...>`, `</tool>`, provider `thinking`, or reasoning fields.
- If native streaming is unavailable, emit one compatibility `assistant_delta` after complete response, then final `assistant_message`.
- If native streaming already emitted deltas, do not emit another whole-answer delta in `app_runner`.

---

## Task 1: Backend Protocol And Status Event Tests

**Files:**
- Modify: `codecub/app_protocol.py`
- Modify: `tests/test_app_protocol.py`

- [ ] **Step 1: Write failing protocol test for `run_status`**

Add to `tests/test_app_protocol.py`:

```python
def test_make_event_accepts_run_status():
    event = make_event(
        "run_status",
        session_id="session-1",
        run_id="run-1",
        payload={
            "phase": "model_streaming",
            "label": "Receiving model response",
            "elapsed_ms": 42,
        },
    )

    assert event["type"] == "run_status"
    assert event["payload"]["phase"] == "model_streaming"
```

- [ ] **Step 2: Run test and verify failure**

Run:

```powershell
pytest tests/test_app_protocol.py::test_make_event_accepts_run_status -q
```

Expected before implementation: failure if `run_status` is not allowed by the protocol event type list.

- [ ] **Step 3: Add `run_status` to protocol event types**

In `codecub/app_protocol.py`, add:

```python
"run_status",
```

to the app event type allowlist near `user_message_received` and `assistant_delta`.

- [ ] **Step 4: Verify protocol test passes**

Run:

```powershell
pytest tests/test_app_protocol.py::test_make_event_accepts_run_status -q
```

Expected: PASS.

- [ ] **Step 5: Commit protocol event change**

```powershell
git add codecub/app_protocol.py tests/test_app_protocol.py
git commit -m "Add run status app event"
```

---

## Task 2: OpenAI-Compatible Native Streaming Client

**Files:**
- Modify: `codecub/models.py`
- Create or Modify: `tests/test_models_streaming.py`

- [ ] **Step 1: Write failing SSE streaming extraction test**

Create `tests/test_models_streaming.py` if it does not exist:

```python
from codecub.models import iter_openai_text_deltas_from_sse


def test_iter_openai_text_deltas_from_responses_sse():
    body = "\n".join(
        [
            'data: {"type":"response.output_text.delta","delta":"Hel"}',
            'data: {"type":"response.output_text.delta","delta":"lo"}',
            'data: {"type":"response.completed","response":{"usage":{"input_tokens":1,"output_tokens":2}}}',
            "data: [DONE]",
        ]
    )

    assert list(iter_openai_text_deltas_from_sse(body.splitlines())) == ["Hel", "lo"]
```

- [ ] **Step 2: Run test and verify failure**

Run:

```powershell
pytest tests/test_models_streaming.py::test_iter_openai_text_deltas_from_responses_sse -q
```

Expected: FAIL because `iter_openai_text_deltas_from_sse` does not exist.

- [ ] **Step 3: Add SSE delta iterator**

Add this function near existing SSE helpers in `codecub/models.py`:

```python
def iter_openai_text_deltas_from_sse(lines):
    for line in lines:
        line = str(line).strip()
        if not line.startswith("data:"):
            continue
        payload = line[len("data:"):].strip()
        if not payload or payload == "[DONE]":
            continue
        try:
            event = json.loads(payload)
        except json.JSONDecodeError:
            continue
        event_type = event.get("type", "")
        if event_type == "response.output_text.delta":
            delta = event.get("delta")
            if isinstance(delta, str) and delta:
                yield delta
            continue
        choices = event.get("choices")
        if isinstance(choices, list) and choices:
            delta = choices[0].get("delta", {})
            content = delta.get("content") if isinstance(delta, dict) else None
            if isinstance(content, str) and content:
                yield content
```

- [ ] **Step 4: Add `stream_complete()` to fake and OpenAI-compatible clients**

Implement:

```python
class FakeModelClient:
    ...
    def stream_complete(self, prompt, max_new_tokens, on_delta, **kwargs):
        text = self.complete(prompt, max_new_tokens, **kwargs)
        chunks = getattr(self, "stream_chunks", None)
        if chunks is None:
            on_delta(text)
            return text
        for chunk in chunks:
            on_delta(chunk)
        return "".join(chunks)
```

and on `OpenAICompatibleModelClient`:

```python
def stream_complete(self, prompt, max_new_tokens, on_delta, prompt_cache_key=None, prompt_cache_retention=None):
    self.last_completion_metadata = {}
    payload = self._build_responses_payload(
        prompt,
        max_new_tokens,
        stream=True,
        prompt_cache_key=prompt_cache_key,
        prompt_cache_retention=prompt_cache_retention,
    )
    text_parts = []
    response_data = {}
    with self._open_responses_request(payload) as response:
        for raw_line in response:
            line = raw_line.decode("utf-8", errors="replace")
            for delta in iter_openai_text_deltas_from_sse([line]):
                text_parts.append(delta)
                on_delta(delta)
            parsed_response = _extract_response_object_from_sse_line(line)
            if parsed_response:
                response_data = parsed_response
    if response_data:
        self.last_completion_metadata = {
            "prompt_cache_supported": self.supports_prompt_cache,
            "prompt_cache_key": prompt_cache_key,
            "prompt_cache_retention": prompt_cache_retention,
            **_extract_usage_cache_details(response_data),
        }
    return "".join(text_parts)
```

Implementation note: factor existing `complete()` request-building into small helpers instead of duplicating headers/retry logic. Keep the public `complete()` behavior unchanged.

- [ ] **Step 5: Verify model streaming tests**

Run:

```powershell
pytest tests/test_models_streaming.py -q
```

Expected: PASS.

- [ ] **Step 6: Run existing model tests**

Run:

```powershell
pytest tests/test_pico.py tests/test_app_runner.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit model streaming client**

```powershell
git add codecub/models.py tests/test_models_streaming.py
git commit -m "Add OpenAI compatible streaming client"
```

---

## Task 3: Runtime Safe Final-Answer Streaming Filter

**Files:**
- Modify: `codecub/runtime.py`
- Modify: `tests/test_pico.py` or Create: `tests/test_runtime_streaming.py`

- [ ] **Step 1: Write failing test for final-only streamed deltas**

Create `tests/test_runtime_streaming.py`:

```python
from pathlib import Path

from codecub.models import FakeModelClient
from codecub.runtime import Pico, SessionStore
from codecub.workspace import WorkspaceContext


def make_agent(tmp_path, chunks):
    (tmp_path / "README.md").write_text("demo\n", encoding="utf-8")
    workspace = WorkspaceContext.build(str(tmp_path))
    client = FakeModelClient(["".join(chunks)])
    client.stream_chunks = list(chunks)
    agent = Pico(
        model_client=client,
        workspace=workspace,
        session_store=SessionStore(Path(tmp_path) / ".codecub" / "sessions"),
        approval_policy="never",
        max_steps=3,
        max_new_tokens=512,
    )
    return agent


def test_runtime_streams_only_final_answer_text(tmp_path):
    agent = make_agent(tmp_path, ["<final>Hel", "lo", " there</final>"])
    events = []
    agent.event_handler = lambda name, payload, runtime, state: events.append((name, payload))

    answer = agent.ask("say hello")

    assert answer == "Hello there"
    assert [payload["text"] for name, payload in events if name == "assistant_delta"] == ["Hel", "lo", " there"]


def test_runtime_does_not_stream_tool_xml(tmp_path):
    agent = make_agent(tmp_path, ['<tool name="read_file" path="README.md"></tool>', "<final>done</final>"])
    events = []
    agent.event_handler = lambda name, payload, runtime, state: events.append((name, payload))

    answer = agent.ask("read")

    streamed = "".join(payload["text"] for name, payload in events if name == "assistant_delta")
    assert "<tool" not in streamed
    assert "</tool>" not in streamed
    assert answer == "done"
```

- [ ] **Step 2: Run runtime streaming tests and verify failure**

Run:

```powershell
pytest tests/test_runtime_streaming.py -q
```

Expected: FAIL because runtime does not call `stream_complete()` or emit `assistant_delta`.

- [ ] **Step 3: Add final-answer stream filter**

Add near helper functions in `codecub/runtime.py`:

```python
class FinalAnswerDeltaFilter:
    def __init__(self, on_text):
        self.on_text = on_text
        self.buffer = ""
        self.in_final = False
        self.closed = False

    def feed(self, chunk):
        if self.closed or not chunk:
            return
        self.buffer += str(chunk)
        while self.buffer:
            if not self.in_final:
                start = self.buffer.find("<final>")
                tool_start = self.buffer.find("<tool")
                if tool_start != -1 and (start == -1 or tool_start < start):
                    self.buffer = ""
                    return
                if start == -1:
                    self.buffer = self.buffer[-16:]
                    return
                self.buffer = self.buffer[start + len("<final>"):]
                self.in_final = True
            end = self.buffer.find("</final>")
            if end == -1:
                if self.buffer:
                    self.on_text(self.buffer)
                    self.buffer = ""
                return
            if end > 0:
                self.on_text(self.buffer[:end])
            self.buffer = self.buffer[end + len("</final>"):]
            self.closed = True
            return
```

- [ ] **Step 4: Emit runtime app events**

Add a small helper method to `Pico`:

```python
def emit_app_event(self, event_name, task_state, payload=None):
    if self.event_handler is not None:
        self.event_handler(event_name, dict(payload or {}), self, task_state)
```

Add a status helper:

```python
def emit_run_status(self, task_state, phase, label, detail="", started_at="", elapsed_ms=0):
    self.emit_app_event(
        "run_status",
        task_state,
        {
            "phase": phase,
            "label": label,
            "detail": detail,
            "started_at": started_at,
            "elapsed_ms": elapsed_ms,
        },
    )
```

- [ ] **Step 5: Use streaming model call when available**

Replace the direct model call in `Pico.ask()`:

```python
raw = self.model_client.complete(
    prompt,
    self.max_new_tokens,
    prompt_cache_key=prompt_cache_key,
    prompt_cache_retention=prompt_cache_retention,
)
```

with:

```python
stream_filter = FinalAnswerDeltaFilter(
    lambda text: self.emit_app_event("assistant_delta", task_state, {"text": text})
)
if hasattr(self.model_client, "stream_complete"):
    raw = self.model_client.stream_complete(
        prompt,
        self.max_new_tokens,
        on_delta=stream_filter.feed,
        prompt_cache_key=prompt_cache_key,
        prompt_cache_retention=prompt_cache_retention,
    )
else:
    raw = self.model_client.complete(
        prompt,
        self.max_new_tokens,
        prompt_cache_key=prompt_cache_key,
        prompt_cache_retention=prompt_cache_retention,
    )
```

Add safe status emissions around prompt building, model request, model streaming, tool execution, and finalizing:

```python
self.emit_run_status(task_state, "building_context", "Building context")
...
self.emit_run_status(task_state, "model_request", "Requesting model response")
...
self.emit_run_status(task_state, "model_streaming", "Receiving model response")
...
self.emit_run_status(task_state, "tool_running", f"Executing tool: {name}", detail=name)
...
self.emit_run_status(task_state, "finalizing", "Finalizing response")
```

- [ ] **Step 6: Verify runtime streaming tests pass**

Run:

```powershell
pytest tests/test_runtime_streaming.py -q
```

Expected: PASS.

- [ ] **Step 7: Run backend regression tests**

Run:

```powershell
pytest tests/test_pico.py tests/test_app_runner.py tests/test_safety_invariants.py -q
```

Expected: PASS.

- [ ] **Step 8: Commit runtime streaming filter**

```powershell
git add codecub/runtime.py tests/test_runtime_streaming.py
git commit -m "Stream safe final answer deltas"
```

---

## Task 4: App Runner JSONL Routing And Compatibility Fallback

**Files:**
- Modify: `codecub/app_runner.py`
- Modify: `tests/test_app_runner.py`

- [ ] **Step 1: Write failing app-mode streaming/status test**

Add to `tests/test_app_runner.py`:

```python
def test_app_runner_emits_streamed_deltas_before_final_message(tmp_path):
    (tmp_path / "README.md").write_text("demo\n", encoding="utf-8")
    stdin = io.StringIO('{"type":"send_message","message":"say hello"}\n{"type":"close"}\n')
    stdout = io.StringIO()

    def build(args):
        workspace = WorkspaceContext.build(args.cwd)
        client = FakeModelClient(["<final>Hello streamed answer.</final>"])
        client.stream_chunks = ["<final>Hello ", "streamed ", "answer.</final>"]
        return Pico(
            model_client=client,
            workspace=workspace,
            session_store=SessionStore(Path(args.cwd) / ".codecub" / "sessions"),
            approval_policy=args.approval,
            max_steps=args.max_steps,
            max_new_tokens=args.max_new_tokens,
        )

    run_app_mode(make_args(tmp_path), stdin=stdin, stdout=stdout, agent_factory=build)
    events = parse_jsonl(stdout.getvalue())

    delta_events = [event for event in events if event["type"] == "assistant_delta"]
    final_index = next(index for index, event in enumerate(events) if event["type"] == "assistant_message")
    assert [event["payload"]["text"] for event in delta_events] == ["Hello ", "streamed ", "answer."]
    assert all(events.index(event) < final_index for event in delta_events)
    assert any(event["type"] == "run_status" and event["payload"]["phase"] == "model_streaming" for event in events)
```

- [ ] **Step 2: Write failing no-duplicate fallback test**

Add:

```python
def test_app_runner_emits_single_compatibility_delta_when_runtime_did_not_stream(tmp_path):
    (tmp_path / "README.md").write_text("demo\n", encoding="utf-8")
    stdin = io.StringIO('{"type":"send_message","message":"say hello"}\n{"type":"close"}\n')
    stdout = io.StringIO()

    exit_code = run_app_mode(
        make_args(tmp_path),
        stdin=stdin,
        stdout=stdout,
        agent_factory=fake_agent_factory(["<final>Hello from compatibility.</final>"]),
    )

    events = parse_jsonl(stdout.getvalue())
    deltas = [event for event in events if event["type"] == "assistant_delta"]

    assert exit_code == 0
    assert len(deltas) == 1
    assert deltas[0]["payload"]["text"] == "Hello from compatibility."
```

- [ ] **Step 3: Run app runner tests and verify failure**

Run:

```powershell
pytest tests/test_app_runner.py::test_app_runner_emits_streamed_deltas_before_final_message tests/test_app_runner.py::test_app_runner_emits_single_compatibility_delta_when_runtime_did_not_stream -q
```

Expected: first test fails until app runner routes runtime `assistant_delta` events correctly.

- [ ] **Step 4: Route runtime app events in `event_handler`**

Update `event_handler` in `codecub/app_runner.py`:

```python
delta_seen_by_run = set()

def event_handler(event_name, payload, runtime, task_state):
    run_id = getattr(task_state, "run_id", "") or active_run.get("run_id", "")
    if event_name == "assistant_delta":
        text = str(payload.get("text", ""))
        if text:
            delta_seen_by_run.add(run_id)
            emit("assistant_delta", run_id=run_id, payload={"text": text})
        return
    if event_name == "run_status":
        emit("run_status", run_id=run_id, payload=dict(payload or {}))
        return
    if event_name != "tool_executed":
        return
    ...
```

- [ ] **Step 5: Avoid duplicate whole-answer delta**

In `run_worker`, replace:

```python
emit("assistant_delta", run_id=run_id, payload={"text": answer})
emit("assistant_message", run_id=run_id, payload={"text": answer})
```

with:

```python
if run_id not in delta_seen_by_run:
    emit("assistant_delta", run_id=run_id, payload={"text": answer})
emit("assistant_message", run_id=run_id, payload={"text": answer})
```

Also emit terminal statuses in `run_worker`:

```python
emit("run_status", run_id=run_id, payload={"phase": "completed", "label": "Completed"})
```

and on failures/cancel:

```python
emit("run_status", run_id=run_id, payload={"phase": "failed", "label": "Failed", "detail": str(exc)})
emit("run_status", run_id=run_id, payload={"phase": "canceled", "label": "Canceled"})
```

- [ ] **Step 6: Emit waiting approval status**

In `approval_handler`, before `approval_requested`:

```python
emit(
    "run_status",
    run_id=run_id,
    payload={
        "phase": "waiting_approval",
        "label": "Waiting for approval",
        "detail": name,
    },
)
```

- [ ] **Step 7: Verify app runner tests**

Run:

```powershell
pytest tests/test_app_runner.py -q
```

Expected: PASS.

- [ ] **Step 8: Commit app runner routing**

```powershell
git add codecub/app_runner.py tests/test_app_runner.py
git commit -m "Route streaming status app events"
```

---

## Task 5: Desktop Chat State For Delta And Run Status

**Files:**
- Modify: `desktop/src/state/backendEvents.ts`
- Modify: `desktop/src/state/chatState.ts`
- Modify: `desktop/tests/chatState.test.ts`

- [ ] **Step 1: Write failing chat state tests**

Add to `desktop/tests/chatState.test.ts`:

```ts
it("appends assistant deltas into one active message and replaces it on final", () => {
  let state = createInitialChatState();

  state = applyBackendEvent(state, {
    type: "user_message_received",
    timestamp: "2026-06-15T00:00:00Z",
    session_id: "s1",
    run_id: "r1",
    payload: { message: "hello" },
  });
  state = applyBackendEvent(state, {
    type: "assistant_delta",
    timestamp: "2026-06-15T00:00:01Z",
    session_id: "s1",
    run_id: "r1",
    payload: { text: "Hel" },
  });
  state = applyBackendEvent(state, {
    type: "assistant_delta",
    timestamp: "2026-06-15T00:00:02Z",
    session_id: "s1",
    run_id: "r1",
    payload: { text: "lo" },
  });
  state = applyBackendEvent(state, {
    type: "assistant_message",
    timestamp: "2026-06-15T00:00:03Z",
    session_id: "s1",
    run_id: "r1",
    payload: { text: "Hello" },
  });

  expect(state.messages.filter((message) => message.role === "assistant")).toHaveLength(1);
  expect(state.messages[1].content).toBe("Hello");
});

it("stores the latest active run status", () => {
  let state = createInitialChatState();

  state = applyBackendEvent(state, {
    type: "run_status",
    timestamp: "2026-06-15T00:00:01Z",
    session_id: "s1",
    run_id: "r1",
    payload: {
      phase: "model_streaming",
      label: "Receiving model response",
      detail: "qwen-flash",
      elapsed_ms: 1200,
    },
  });

  expect(state.runStatus?.phase).toBe("model_streaming");
  expect(state.runStatus?.label).toBe("Receiving model response");
  expect(state.runStatus?.elapsedMs).toBe(1200);
});
```

- [ ] **Step 2: Run tests and verify failure**

Run:

```powershell
npm run test -- chatState.test.ts
```

Expected: FAIL until `assistant_delta` and `run_status` are handled.

- [ ] **Step 3: Add run status types**

In `desktop/src/state/chatState.ts`, add:

```ts
export type RunStatus = {
  runId: string;
  phase: string;
  label: string;
  detail: string;
  startedAt: string;
  elapsedMs: number;
  updatedAt: string;
};
```

Add to `ChatState`:

```ts
runStatus: RunStatus | null;
```

Initialize it as `null`.

- [ ] **Step 4: Handle `run_status` event**

Add to `applyBackendEvent`:

```ts
if (event.type === "run_status") {
  return {
    ...state,
    runStatus: {
      runId: event.run_id,
      phase: String(event.payload.phase ?? ""),
      label: String(event.payload.label ?? ""),
      detail: String(event.payload.detail ?? ""),
      startedAt: String(event.payload.started_at ?? ""),
      elapsedMs: Number(event.payload.elapsed_ms ?? 0),
      updatedAt: event.timestamp,
    },
  };
}
```

- [ ] **Step 5: Handle `assistant_delta` and final dedupe**

Add helpers:

```ts
function findAssistantMessageIndex(messages: ChatMessage[], runId: string): number {
  return messages.findIndex((message) => message.role === "assistant" && message.runId === runId);
}

function upsertAssistantMessage(state: ChatState, event: BackendEvent, content: string, mode: "append" | "replace"): ChatState {
  const index = findAssistantMessageIndex(state.messages, event.run_id);
  if (index < 0) {
    return {
      ...state,
      messages: [
        ...state.messages,
        {
          id: `${event.run_id}:assistant:${state.messages.length}`,
          role: "assistant",
          content,
          runId: event.run_id,
          createdAt: event.timestamp,
        },
      ],
    };
  }
  return {
    ...state,
    messages: state.messages.map((message, messageIndex) =>
      messageIndex === index
        ? { ...message, content: mode === "append" ? message.content + content : content }
        : message,
    ),
  };
}
```

Use it:

```ts
if (event.type === "assistant_delta") {
  return upsertAssistantMessage(state, event, String(event.payload.text ?? ""), "append");
}

if (event.type === "assistant_message") {
  return upsertAssistantMessage(state, event, String(event.payload.text ?? event.payload.final ?? ""), "replace");
}
```

On terminal run events:

```ts
if (event.type === "run_completed" || event.type === "run_failed" || event.type === "run_canceled") {
  return {
    ...state,
    activeRunId: "",
    isRunning: false,
    runStatus: state.runStatus ? { ...state.runStatus, phase: event.type.replace("run_", "") } : null,
  };
}
```

- [ ] **Step 6: Verify chat state tests**

Run:

```powershell
npm run test -- chatState.test.ts
```

Expected: PASS.

- [ ] **Step 7: Commit chat state changes**

```powershell
git add desktop/src/state/backendEvents.ts desktop/src/state/chatState.ts desktop/tests/chatState.test.ts
git commit -m "Render assistant deltas in chat state"
```

---

## Task 6: Desktop Active Run Status UI

**Files:**
- Modify: `desktop/src/components/ChatView.tsx`
- Modify: `desktop/src/i18n/zh-CN.ts`
- Modify: `desktop/src/i18n/en-US.ts`
- Modify: `desktop/src/styles/app.css`
- Create: `desktop/tests/ChatViewRunStatus.test.tsx`

- [ ] **Step 1: Write failing status UI test**

Create `desktop/tests/ChatViewRunStatus.test.tsx`:

```tsx
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { ChatView } from "../src/components/ChatView";
import type { ChatState } from "../src/state/chatState";

const t = (key: string) => key;

describe("ChatView run status", () => {
  it("shows active run status and elapsed time", () => {
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-06-15T00:00:05Z"));
    const chatState: ChatState = {
      messages: [],
      activeRunId: "r1",
      isRunning: true,
      runStatus: {
        runId: "r1",
        phase: "model_streaming",
        label: "Receiving model response",
        detail: "qwen-flash",
        startedAt: "2026-06-15T00:00:00Z",
        elapsedMs: 0,
        updatedAt: "2026-06-15T00:00:01Z",
      },
    };

    render(<ChatView t={t as never} chatState={chatState} onSend={vi.fn()} onStop={vi.fn()} />);

    expect(screen.getByText("Receiving model response")).toBeInTheDocument();
    expect(screen.getByText(/5s/)).toBeInTheDocument();
    vi.useRealTimers();
  });
});
```

- [ ] **Step 2: Run UI test and verify failure**

Run:

```powershell
npm run test -- ChatViewRunStatus.test.tsx
```

Expected: FAIL until ChatView renders status.

- [ ] **Step 3: Add elapsed time helper in ChatView**

In `desktop/src/components/ChatView.tsx`, import `useEffect`:

```ts
import { useEffect, useState } from "react";
```

Add helpers:

```ts
function formatElapsed(ms: number): string {
  const seconds = Math.max(0, Math.floor(ms / 1000));
  if (seconds < 60) {
    return `${seconds}s`;
  }
  const minutes = Math.floor(seconds / 60);
  return `${minutes}m ${seconds % 60}s`;
}
```

Inside component:

```ts
const [now, setNow] = useState(() => Date.now());

useEffect(() => {
  if (!chatState.isRunning) {
    return;
  }
  const timer = window.setInterval(() => setNow(Date.now()), 1000);
  return () => window.clearInterval(timer);
}, [chatState.isRunning]);

const status = chatState.runStatus;
const elapsedMs = status?.startedAt ? now - new Date(status.startedAt).getTime() : status?.elapsedMs ?? 0;
```

- [ ] **Step 4: Render compact status strip**

Add above `message-list`:

```tsx
{chatState.isRunning && status ? (
  <div className="run-status-strip" aria-label={t("activeRunStatus")}>
    <span className="run-status-dot" />
    <span className="run-status-label">{status.label || t("running")}</span>
    {status.detail ? <span className="run-status-detail">{status.detail}</span> : null}
    <span className="run-status-elapsed">{formatElapsed(elapsedMs)}</span>
  </div>
) : null}
```

- [ ] **Step 5: Add i18n keys**

In `zh-CN.ts` add:

```ts
activeRunStatus: "运行状态",
```

In `en-US.ts` add:

```ts
activeRunStatus: "Run status",
```

- [ ] **Step 6: Add CSS**

In `desktop/src/styles/app.css`:

```css
.run-status-strip {
  display: flex;
  align-items: center;
  gap: 8px;
  border-bottom: 1px solid #e2e6ec;
  padding: 8px 12px;
  color: #475569;
  background: #f8fafc;
  font-size: 12px;
}

.run-status-dot {
  width: 8px;
  height: 8px;
  border-radius: 999px;
  background: #2563eb;
}

.run-status-label {
  font-weight: 600;
}

.run-status-detail,
.run-status-elapsed {
  color: #64748b;
}

.run-status-elapsed {
  margin-left: auto;
  font-variant-numeric: tabular-nums;
}
```

- [ ] **Step 7: Verify UI test**

Run:

```powershell
npm run test -- ChatViewRunStatus.test.tsx chatState.test.ts
```

Expected: PASS.

- [ ] **Step 8: Commit status UI**

```powershell
git add desktop/src/components/ChatView.tsx desktop/src/i18n/zh-CN.ts desktop/src/i18n/en-US.ts desktop/src/styles/app.css desktop/tests/ChatViewRunStatus.test.tsx
git commit -m "Show active run streaming status"
```

---

## Task 7: End-To-End Regression And Packaged Verification

**Files:**
- Modify only if verification finds a defect in files from Tasks 1-6.

- [ ] **Step 1: Run Python tests**

Run:

```powershell
pytest -q
```

Expected: PASS.

- [ ] **Step 2: Run desktop tests**

Run:

```powershell
npm run test
```

from `desktop/`.

Expected: PASS.

- [ ] **Step 3: Run desktop typecheck**

Run:

```powershell
npm run typecheck
```

from `desktop/`.

Expected: PASS.

- [ ] **Step 4: Build and package**

Run:

```powershell
npm run package:win
```

from `desktop/`.

Expected: PASS and regenerated:

- `desktop/release/win-unpacked/CodeCub.exe`
- `desktop/release/CodeCub-0.1.0-x64.exe`

- [ ] **Step 5: Run packaged smoke**

Run:

```powershell
.\scripts\smoke-packaged.ps1
```

from `desktop/`.

Expected:

```text
packaged_backend=...
packaged_alive_after_8s=True
```

- [ ] **Step 6: Manual packaged check**

Open:

```text
D:\代码备份\pico\pico-main\desktop\release\win-unpacked\CodeCub.exe
```

Verify:

- Recent project opens a session.
- Sending a Qwen/OpenAI-compatible prompt shows a status strip immediately.
- Elapsed time increments while running.
- Assistant text appears progressively.
- Final answer is not duplicated.
- Run log does not show hidden chain-of-thought.
- Tool or approval phases update the visible status when triggered.

- [ ] **Step 7: Final git status review**

Run:

```powershell
git status --short
```

Expected: only intentional changes or a clean tree except the known pre-existing `desktop/index.html` line-ending status if still present.

---

## Known Risks And Mitigations

- **Risk: Tool XML leaks into chat while streaming.**
  - Mitigation: Runtime only emits deltas from inside a detected `<final>` block. Tool-like output is not streamed.
- **Risk: Final answer without `<final>` cannot be streamed safely.**
  - Mitigation: Treat as compatibility fallback and emit after parse, preserving correctness over premature display.
- **Risk: OpenAI-compatible providers use different SSE shapes.**
  - Mitigation: Support Responses API `response.output_text.delta` and Chat Completions `choices[0].delta.content` in the SSE iterator.
- **Risk: Existing Chinese i18n files appear mojibake in PowerShell output.**
  - Mitigation: Preserve file encoding and edit minimal keys only. Verify in app UI rather than trusting terminal display.
- **Risk: Full pytest suite may be slow.**
  - Mitigation: Run focused tests after each task and full suite before completion.

## Plan Self-Review

- Spec coverage: The plan covers true OpenAI-compatible streaming, frontend delta rendering, final dedupe, run status event, elapsed status UI, tests, and packaged verification.
- P1 boundary: Ollama and Anthropic-compatible native streaming remain out of scope, matching the requirements.
- Security/privacy: The plan explicitly filters raw tool XML and hidden reasoning, and acceptance checks no hidden chain-of-thought display.
- Type consistency: `run_status`, `assistant_delta`, `RunStatus`, `phase`, `label`, `detail`, `started_at`, and `elapsed_ms` are used consistently across backend and frontend.
- Maintenance: The plan reuses the existing JSONL event path and React state model instead of introducing a new transport.
