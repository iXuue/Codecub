# File Summary Relevant Memory Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move file summary details out of always-on `Memory:` and recall them only through `Relevant memory` when the current request is related.

**Architecture:** Keep file summaries in the existing `LayeredMemory` state. `render_memory_text()` shows only a compact availability line. `retrieval_candidates()` adds fresh file summary candidates using the same lightweight keyword matching path already used for notes.

**Tech Stack:** Python standard library, pytest.

---

## Confirmed Scope

- Modify only file-summary prompt behavior.
- Do not change how file summaries are created, stored, invalidated, or canonicalized.
- Do not change durable memory, episodic notes, project docs, or `ContextManager` section order.
- Keep the first implementation small and deterministic.

## Expected File Changes

- Modify `pico/memory.py`.
- Modify `tests/test_memory.py`.
- Modify `tests/test_context_manager.py` only if metadata coverage is useful.
- Do not modify `pico/context_manager.py` unless tests prove the existing candidate flow cannot carry `file_summary` notes.

## Task 1: Stop Expanding File Summaries In Memory

- [ ] Update `tests/test_memory.py::test_file_summaries_use_canonical_paths_and_freshness`.

Expected behavior:

```text
Memory:
- file_summaries: available for sample.txt
```

The full text `sample.txt: alpha` must not appear in `render_memory_text()`.

- [ ] Update `pico/memory.py::render_memory_text()` so it lists fresh summary paths only.

- [ ] Run:

```powershell
python -m pytest tests/test_memory.py::test_file_summaries_use_canonical_paths_and_freshness -q -p no:cacheprovider
```

Expected: pass.

## Task 2: Recall File Summaries When Relevant

- [ ] Add a memory test where a fresh summary exists for `sample.txt`.

Expected query behavior:

```text
retrieval_view("what did sample.txt contain?")
```

includes:

```text
- sample.txt: sample.txt: alpha
```

- [ ] Add an unrelated query assertion.

Expected:

```text
retrieval_view("how should I deploy?")
```

does not include `sample.txt: alpha`.

- [ ] Add stale-summary assertion: after the file changes, the old summary is not recalled.

- [ ] Implement file summary candidates in `pico/memory.py::retrieval_candidates()`.

Candidate shape:

```python
{
    "text": f"{path}: {summary}",
    "tags": [path],
    "source": path,
    "created_at": summary["created_at"],
    "kind": "file_summary",
}
```

- [ ] Run:

```powershell
python -m pytest tests/test_memory.py -q -p no:cacheprovider
```

Expected: pass.

## Task 3: Context Manager Integration Check

- [ ] Add a focused test in `tests/test_context_manager.py` only if needed.

Expected prompt behavior:

```text
Relevant memory:
- sample.txt: sample.txt: alpha
```

appears for a related request and does not appear for an unrelated request.

- [ ] Prefer no `ContextManager` code change. It should already consume `retrieval_candidates()`.

- [ ] Run:

```powershell
python -m pytest tests/test_context_manager.py -q -p no:cacheprovider
```

Expected: pass.

## Task 4: Regression Pass

- [ ] Run focused memory/context tests.

```powershell
python -m pytest tests/test_memory.py tests/test_context_manager.py -q -p no:cacheprovider
```

Expected: pass.

- [ ] Run broader runtime tests if practical.

```powershell
python -m pytest tests/test_pico.py -q -p no:cacheprovider
```

Expected: pass or report existing unrelated failures separately.

## Known Risks

- If file summary matching uses too many summary keywords, irrelevant summaries may still appear.
- If matching uses only file paths, useful content-based recall may be missed.
- Keep the first version simple: path tokens plus summary keyword overlap, no embedding.

## Plan Review

- Requirement match: yes. File summary details move from always-on `Memory:` to relevant recall.
- Scope boundary: no durable memory, no project docs, no command changes.
- Maintenance fit: good. The change stays inside existing `LayeredMemory` retrieval and rendering paths.
