"""多步 agent 运行时使用的轻量工作记忆。

session history 负责保存完整事件流；这个模块只保存更小的一层工作集：
当前任务摘要、最近接触的文件、文件短摘要，以及少量跨轮笔记。
这样下一轮 prompt 还能接上上一轮，但不会被整段历史塞满。
"""

import hashlib
from datetime import datetime
import re
from pathlib import Path

from .workspace import clip, now

WORKING_FILE_LIMIT = 8
EPISODIC_NOTE_LIMIT = 12
FILE_SUMMARY_LIMIT = 6

STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "but", "by",
    "can", "contain", "did", "do", "does", "for", "from", "how",
    "i", "if", "in", "is", "it", "me", "of", "on", "or", "should",
    "that", "the", "this", "to", "was", "what", "when", "where",
    "which", "who", "why", "with", "we", "you",
}
RETRIEVAL_REASON_BASE_SCORES = {
    "path_match": 120,
    "source_match": 100,
    "tag_match": 80,
    "keyword_match": 10,
}
PER_KIND_RETRIEVAL_LIMITS = {
    "file_summary": 1,
    "durable": 1,
    "episodic_process": 2,
}
EPISODIC_PROCESS_KINDS = {"episodic", "process"}

DURABLE_TOPIC_DEFAULTS = {
    "project-conventions": {
        "title": "Project Conventions",
        "summary": "Stable repository conventions.",
        "tags": ["convention"],
    },
    "key-decisions": {
        "title": "Key Decisions",
        "summary": "Long-lived decisions and rationale anchors.",
        "tags": ["decision"],
    },
    "dependency-facts": {
        "title": "Dependency Facts",
        "summary": "Stable dependency and environment facts.",
        "tags": ["dependency"],
    },
    "user-preferences": {
        "title": "User Preferences",
        "summary": "Stable user preferences.",
        "tags": ["preference"],
    },
}


def default_memory_state():
    # 用一个小而结构化的状态，而不是一大段自由文本摘要。
    return {
        "working": {
            "task_summary": "",
            "recent_files": [],
        },
        "episodic_notes": [],
        "file_summaries": {},
        "task": "",
        "files": [],
        "notes": [],
        "next_note_index": 0,
    }


class DurableMemoryStore:
    def __init__(self, root):
        self.root = Path(root)
        self.index_path = self.root / "MEMORY.md"
        self.topics_dir = self.root / "topics"

    def topic_slugs(self):
        return [topic["topic"] for topic in self.load_index()]

    def load_index(self):
        if not self.index_path.exists():
            return []
        lines = self.index_path.read_text(encoding="utf-8").splitlines()
        topics = []
        current = None
        for raw in lines:
            line = raw.strip()
            match = re.match(r"- \[([^\]]+)\]\([^)]+\):\s*(.+)", line)
            if match:
                current = {
                    "topic": match.group(1).strip(),
                    "title": match.group(2).strip(),
                    "summary": "",
                    "tags": [],
                }
                topics.append(current)
                continue
            if current is None:
                continue
            summary_match = re.match(r"- summary:\s*(.+)", line)
            if summary_match:
                current["summary"] = summary_match.group(1).strip()
                continue
            tags_match = re.match(r"- tags:\s*(.+)", line)
            if tags_match:
                current["tags"] = [tag.strip() for tag in tags_match.group(1).split(",") if tag.strip()]
        return topics

    def load_topic_notes(self, topic):
        path = self.topics_dir / f"{topic}.md"
        if not path.exists():
            return []
        lines = path.read_text(encoding="utf-8").splitlines()
        notes = []
        capture = False
        updated_at = ""
        tags = []
        for raw in lines:
            line = raw.strip()
            if line.startswith("- tags:"):
                tags = [tag.strip() for tag in line.split(":", 1)[1].split(",") if tag.strip()]
            elif line.startswith("- updated_at:"):
                updated_at = line.split(":", 1)[1].strip()
            elif line == "## Notes":
                capture = True
            elif capture and line.startswith("- "):
                notes.append(
                    {
                        "text": line[2:].strip(),
                        "tags": tags,
                        "source": topic,
                        "created_at": updated_at or now(),
                        "kind": "durable",
                    }
                )
        return notes

    @staticmethod
    def _subject_key(text):
        text = str(text).strip()
        patterns = (
            r"^(.+?)\s+is\s+.+$",
            r"^(.+?)\s+are\s+.+$",
            r"^(.+?)\s+uses?\s+.+$",
            r"^(.+?)\s+should\s+.+$",
            r"^(.+?)是.+$",
            r"^(.+?)使用.+$",
        )
        for pattern in patterns:
            match = re.match(pattern, text, re.I)
            if match:
                subject = " ".join(_tokenize(match.group(1)))
                return subject or None
        return None

    def retrieval_candidates(self, query, limit=3):
        ranked = []
        for topic in self.load_index():
            notes = self.load_topic_notes(topic["topic"])
            for note in notes:
                note["tags"] = _dedupe_preserve_order([*note.get("tags", []), topic["topic"], topic.get("title", "")])
                scored = score_memory_candidate(query, note)
                if scored is None:
                    continue
                ranked.append((_candidate_sort_key(scored), scored))
        ranked.sort(key=lambda item: item[0], reverse=True)
        return [candidate for _, candidate in ranked[:limit]]

    def _write_index(self, topics):
        self.root.mkdir(parents=True, exist_ok=True)
        self.topics_dir.mkdir(parents=True, exist_ok=True)
        lines = ["# Durable Memory Index", ""]
        for topic in topics:
            lines.append(f"- [{topic['topic']}](topics/{topic['topic']}.md): {topic['title']}")
            lines.append(f"  - summary: {topic['summary']}")
            lines.append(f"  - tags: {', '.join(topic['tags'])}")
        self.index_path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")

    def _write_topic(self, topic, notes):
        self.topics_dir.mkdir(parents=True, exist_ok=True)
        meta = DURABLE_TOPIC_DEFAULTS[topic]
        lines = [
            f"# {meta['title']}",
            "",
            f"- topic: {topic}",
            f"- summary: {meta['summary']}",
            f"- tags: {', '.join(meta['tags'])}",
            f"- updated_at: {now()}",
            "",
            "## Notes",
        ]
        for note in notes:
            lines.append(f"- {note}")
        (self.topics_dir / f"{topic}.md").write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")

    def promote(self, promotions):
        if not promotions:
            return [], []
        topics = {topic["topic"]: topic for topic in self.load_index()}
        topic_notes = {slug: [note["text"] for note in self.load_topic_notes(slug)] for slug in topics}
        results = []
        superseded = []
        for topic, note_text in promotions:
            meta = DURABLE_TOPIC_DEFAULTS[topic]
            topics.setdefault(
                topic,
                {
                    "topic": topic,
                    "title": meta["title"],
                    "summary": meta["summary"],
                    "tags": list(meta["tags"]),
                },
            )
            existing = topic_notes.setdefault(topic, [])
            if note_text in existing:
                continue
            new_subject = self._subject_key(note_text)
            replaced = False
            if new_subject:
                for index, old_text in enumerate(list(existing)):
                    if self._subject_key(old_text) == new_subject:
                        superseded.append(f"{topic}: {old_text} -> {note_text}")
                        existing[index] = note_text
                        replaced = True
                        break
            if not replaced:
                existing.append(note_text)
            results.append(f"{topic}: {note_text}")
        self._write_index([topics[slug] for slug in sorted(topics)])
        for topic, notes in topic_notes.items():
            self._write_topic(topic, notes)
        return results, superseded


def _ensure_list(value):
    if isinstance(value, list):
        return value
    if isinstance(value, tuple):
        return list(value)
    if isinstance(value, set):
        return list(value)
    if value in (None, ""):
        return []
    return [value]


def _dedupe_preserve_order(items):
    seen = set()
    result = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


def resolve_workspace_path(raw_path, workspace_root=None):
    path = Path(str(raw_path))
    if workspace_root is None:
        return path

    root = Path(workspace_root).resolve()
    candidate = path if path.is_absolute() else root / path
    resolved = candidate.resolve()
    try:
        resolved.relative_to(root)
    except ValueError:
        return None
    return resolved


def canonicalize_path(raw_path, workspace_root=None):
    resolved = resolve_workspace_path(raw_path, workspace_root)
    if resolved is None:
        return Path(str(raw_path)).as_posix()
    if workspace_root is None:
        return Path(str(raw_path)).as_posix()
    root = Path(workspace_root).resolve()
    return resolved.relative_to(root).as_posix()


def file_freshness(raw_path, workspace_root=None):
    resolved = resolve_workspace_path(raw_path, workspace_root)
    if resolved is None or not resolved.exists() or not resolved.is_file():
        return None
    return hashlib.sha256(resolved.read_bytes()).hexdigest()


def _tokenize(text):
    return {token.lower() for token in re.findall(r"[A-Za-z0-9_]+", str(text))}


def meaningful_tokens(text):
    return _tokenize(text) - STOPWORDS


def candidate_kind_group(kind):
    normalized = str(kind or "episodic").strip() or "episodic"
    if normalized in EPISODIC_PROCESS_KINDS:
        return "episodic_process"
    return normalized


def _candidate_sort_key(candidate):
    return (
        int(candidate.get("score", 0)),
        _parse_timestamp(candidate.get("created_at")),
        int(candidate.get("note_index", -1)),
    )


def score_memory_candidate(query, note):
    query_text = str(query)
    query_tokens = _tokenize(query_text)
    meaningful_query_tokens = query_tokens - STOPWORDS
    kind = str(note.get("kind", "episodic")).strip() or "episodic"
    source = str(note.get("source", "")).strip()
    tags = {str(tag).strip().lower() for tag in _ensure_list(note.get("tags", [])) if str(tag).strip()}
    source_tokens = _tokenize(source)
    text_tokens = meaningful_tokens(note.get("text", ""))
    source_hit = bool(source and source.lower() in query_text.lower()) or bool(query_tokens & source_tokens)
    tag_matches = sorted(query_tokens & tags)
    keyword_matches = sorted(meaningful_query_tokens & text_tokens)

    if kind == "file_summary" and source_hit:
        reason = "path_match"
        matched_terms = sorted(query_tokens & source_tokens) or [source]
    elif source_hit:
        reason = "source_match"
        matched_terms = sorted(query_tokens & source_tokens) or [source]
    elif tag_matches:
        reason = "tag_match"
        matched_terms = tag_matches
    elif meaningful_query_tokens and keyword_matches:
        reason = "keyword_match"
        matched_terms = keyword_matches
    else:
        return None

    scored = dict(note)
    scored["reason"] = reason
    scored["score"] = RETRIEVAL_REASON_BASE_SCORES[reason] + len(matched_terms)
    scored["matched_terms"] = matched_terms
    return scored


def _debug_note_entry(note):
    return {
        "text": str(note.get("text", "")).strip(),
        "kind": str(note.get("kind", "episodic")).strip() or "episodic",
        "source": str(note.get("source", "")).strip(),
    }


def _is_better_candidate(candidate, existing, prefer_file_summary=False):
    candidate_key = _candidate_sort_key(candidate)
    existing_key = _candidate_sort_key(existing)
    if candidate_key != existing_key:
        return candidate_key > existing_key
    if prefer_file_summary:
        return candidate.get("kind") == "file_summary" and existing.get("kind") != "file_summary"
    return False


def _candidate_file_key(candidate, known_file_paths):
    kind = str(candidate.get("kind", "episodic")).strip() or "episodic"
    source = str(candidate.get("source", "")).strip()
    if kind == "file_summary" and source:
        return source
    if kind not in EPISODIC_PROCESS_KINDS:
        return None
    if source in known_file_paths:
        return source
    for tag in _ensure_list(candidate.get("tags", [])):
        tag = str(tag).strip()
        if tag in known_file_paths:
            return tag
    return None


def _dedupe_entry(candidate, reason, kept):
    entry = _debug_note_entry(candidate)
    entry["dedupe_reason"] = reason
    entry["kept_text"] = str(kept.get("text", "")).strip()
    return entry


def _dedupe_retrieval_candidates_with_debug(candidates, known_file_paths):
    deduped_entries = []
    by_text = {}
    for candidate in candidates:
        text = str(candidate.get("text", "")).strip()
        if not text:
            continue
        existing = by_text.get(text)
        if existing is None:
            by_text[text] = candidate
        elif _is_better_candidate(candidate, existing):
            deduped_entries.append(_dedupe_entry(existing, "duplicate_text", candidate))
            by_text[text] = candidate
        else:
            deduped_entries.append(_dedupe_entry(candidate, "duplicate_text", existing))

    by_identity = {}
    for candidate in by_text.values():
        key = (
            str(candidate.get("kind", "episodic")).strip() or "episodic",
            str(candidate.get("source", "")).strip(),
            str(candidate.get("text", "")).strip(),
        )
        existing = by_identity.get(key)
        if existing is None:
            by_identity[key] = candidate
        elif _is_better_candidate(candidate, existing):
            deduped_entries.append(_dedupe_entry(existing, "duplicate_identity", candidate))
            by_identity[key] = candidate
        else:
            deduped_entries.append(_dedupe_entry(candidate, "duplicate_identity", existing))

    by_file = {}
    result = []
    for candidate in by_identity.values():
        file_key = _candidate_file_key(candidate, known_file_paths)
        if not file_key:
            result.append(candidate)
            continue
        existing = by_file.get(file_key)
        if existing is None:
            by_file[file_key] = candidate
            continue
        if _is_better_candidate(candidate, existing, prefer_file_summary=True):
            deduped_entries.append(_dedupe_entry(existing, "same_file_candidate", candidate))
            by_file[file_key] = candidate
        else:
            deduped_entries.append(_dedupe_entry(candidate, "same_file_candidate", existing))

    result.extend(by_file.values())
    return result, deduped_entries


def _dedupe_retrieval_candidates(candidates, known_file_paths):
    kept, _ = _dedupe_retrieval_candidates_with_debug(candidates, known_file_paths)
    return kept


def _apply_per_kind_budgets_with_debug(candidates, limit):
    selected = []
    skipped = []
    group_counts = {}
    for candidate in sorted(candidates, key=_candidate_sort_key, reverse=True):
        group = candidate_kind_group(candidate.get("kind", "episodic"))
        cap = int(PER_KIND_RETRIEVAL_LIMITS.get(group, limit))
        if group_counts.get(group, 0) >= cap:
            entry = _debug_note_entry(candidate)
            entry["budget_group"] = group
            entry["budget_limit"] = cap
            skipped.append(entry)
            continue
        selected.append(candidate)
        group_counts[group] = group_counts.get(group, 0) + 1
        if len(selected) >= limit:
            break
    return selected, skipped


def _apply_per_kind_budgets(candidates, limit):
    selected, _ = _apply_per_kind_budgets_with_debug(candidates, limit)
    return selected


def _parse_timestamp(value):
    if not value:
        return 0.0
    try:
        return datetime.fromisoformat(str(value)).timestamp()
    except Exception:
        return 0.0


def _normalize_note(note, index):
    if isinstance(note, str):
        text = clip(note.strip(), 500)
        return {
            "text": text,
            "tags": [],
            "source": "",
            "created_at": now(),
            "note_index": index,
            "kind": "episodic",
        }

    if not isinstance(note, dict):
        text = clip(str(note).strip(), 500)
        return {
            "text": text,
            "tags": [],
            "source": "",
            "created_at": now(),
            "note_index": index,
            "kind": "episodic",
        }

    text = clip(str(note.get("text", "")).strip(), 500)
    tags = [str(tag).strip() for tag in _ensure_list(note.get("tags", [])) if str(tag).strip()]
    source = str(note.get("source", "")).strip()
    created_at = str(note.get("created_at", "")).strip() or now()
    note_index = int(note.get("note_index", index))
    kind = str(note.get("kind", "episodic")).strip() or "episodic"
    return {
        "text": text,
        "tags": _dedupe_preserve_order(tags),
        "source": source,
        "created_at": created_at,
        "note_index": note_index,
        "kind": kind,
    }


def normalize_memory_state(state, workspace_root=None):
    if state is None:
        state = default_memory_state()
    elif not isinstance(state, dict):
        raise TypeError("memory state must be a mapping")

    # 规范化层的作用，是把“磁盘里可能长得不太一样的旧状态”
    # 统一整理成当前 runtime 可直接使用的紧凑结构。
    working = state.get("working")
    if not isinstance(working, dict):
        working = {}
    working.setdefault("task_summary", "")
    working.setdefault("recent_files", [])
    working["task_summary"] = clip(str(working.get("task_summary", "")).strip(), 300)
    working["recent_files"] = _dedupe_preserve_order(
        [
            canonicalize_path(path, workspace_root)
            for path in _ensure_list(working.get("recent_files", []))
            if str(path).strip()
        ]
    )[-WORKING_FILE_LIMIT:]
    state["working"] = working

    if not str(working["task_summary"]).strip() and state.get("task"):
        working["task_summary"] = clip(str(state.get("task", "")).strip(), 300)
    if not working["recent_files"] and state.get("files"):
        working["recent_files"] = _dedupe_preserve_order(
            [
                canonicalize_path(path, workspace_root)
                for path in _ensure_list(state.get("files", []))
                if str(path).strip()
            ]
        )[-WORKING_FILE_LIMIT:]

    episodic_notes = state.get("episodic_notes")
    if not isinstance(episodic_notes, list):
        episodic_notes = []

    if not episodic_notes and state.get("notes"):
        episodic_notes = [
            _normalize_note(note, index)
            for index, note in enumerate(_ensure_list(state.get("notes", [])))
            if str(note).strip()
        ]
    else:
        normalized_notes = []
        for index, note in enumerate(episodic_notes):
            if isinstance(note, str) and not str(note).strip():
                continue
            normalized_notes.append(_normalize_note(note, index))
        episodic_notes = normalized_notes
    episodic_notes = episodic_notes[-EPISODIC_NOTE_LIMIT:]
    state["episodic_notes"] = episodic_notes

    file_summaries = state.get("file_summaries")
    if not isinstance(file_summaries, dict):
        file_summaries = {}
    normalized_file_summaries = {}
    for path, summary in file_summaries.items():
        path = canonicalize_path(path, workspace_root)
        if isinstance(summary, dict):
            text = clip(str(summary.get("summary", "")).strip(), 500)
            created_at = str(summary.get("created_at", "")).strip() or now()
            freshness = summary.get("freshness")
            freshness = None if freshness in (None, "") else str(freshness).strip() or None
        else:
            text = clip(str(summary).strip(), 500)
            created_at = now()
            freshness = None
        if not path or not text:
            continue
        normalized_file_summaries[path] = {
            "summary": text,
            "created_at": created_at,
            "freshness": freshness,
        }
    state["file_summaries"] = normalized_file_summaries

    next_note_index = state.get("next_note_index")
    if not isinstance(next_note_index, int) or next_note_index < 0:
        next_note_index = 0
    max_index = max([note["note_index"] for note in episodic_notes], default=-1)
    state["next_note_index"] = max(next_note_index, max_index + 1)

    state["task"] = working["task_summary"]
    state["files"] = list(working["recent_files"])
    state["notes"] = [note["text"] for note in episodic_notes]
    durable_root = Path(workspace_root) / ".codecub" / "memory" if workspace_root is not None else None
    durable_store = DurableMemoryStore(durable_root) if durable_root is not None else None
    state["durable_topics"] = durable_store.topic_slugs() if durable_store is not None else []
    return state


def set_task_summary(state, summary, workspace_root=None):
    state = normalize_memory_state(state, workspace_root)
    state["working"]["task_summary"] = clip(str(summary).strip(), 300)
    state["task"] = state["working"]["task_summary"]
    return state


def remember_file(state, path, workspace_root=None):
    state = normalize_memory_state(state, workspace_root)
    path = canonicalize_path(path, workspace_root).strip()
    if not path:
        return state
    files = [item for item in state["working"]["recent_files"] if item != path]
    files.append(path)
    state["working"]["recent_files"] = files[-WORKING_FILE_LIMIT:]
    state["files"] = list(state["working"]["recent_files"])
    return state


def append_note(state, text, tags=(), source="", created_at=None, workspace_root=None, kind="episodic"):
    state = normalize_memory_state(state, workspace_root)
    text = clip(str(text).strip(), 500)
    if not text:
        return state

    normalized_tags = _dedupe_preserve_order(
        [str(tag).strip() for tag in _ensure_list(tags) if str(tag).strip()]
    )
    note = {
        "text": text,
        "tags": normalized_tags,
        "source": str(source).strip(),
        "created_at": str(created_at).strip() if created_at else now(),
        "note_index": int(state.get("next_note_index", 0)),
        "kind": str(kind).strip() or "episodic",
    }
    state["next_note_index"] = note["note_index"] + 1

    notes = [item for item in state["episodic_notes"] if item["text"] != note["text"]]
    notes.append(note)
    state["episodic_notes"] = notes[-EPISODIC_NOTE_LIMIT:]
    state["notes"] = [item["text"] for item in state["episodic_notes"]]
    return state


def set_file_summary(state, path, summary, workspace_root=None):
    state = normalize_memory_state(state, workspace_root)
    path = canonicalize_path(path, workspace_root).strip()
    summary = clip(str(summary).strip(), 500)
    if not path or not summary:
        return state
    state["file_summaries"][path] = {
        "summary": summary,
        "created_at": now(),
        "freshness": file_freshness(path, workspace_root),
    }
    return state


def invalidate_file_summary(state, path, workspace_root=None):
    state = normalize_memory_state(state, workspace_root)
    path = canonicalize_path(path, workspace_root).strip()
    if not path:
        return state
    state["file_summaries"].pop(path, None)
    return state


def invalidate_stale_file_summaries(state, workspace_root=None):
    state = normalize_memory_state(state, workspace_root)
    invalidated = []
    for path, summary in list(state["file_summaries"].items()):
        current_freshness = file_freshness(path, workspace_root)
        if summary.get("freshness") == current_freshness:
            continue
        invalidated.append(path)
        state["file_summaries"].pop(path, None)
    return state, invalidated


def summarize_read_result(result, limit=180):
    # 我们不会把完整文件内容塞进记忆层，
    # 这里只保留足够提醒下一轮“刚刚读到了什么”的短摘要。
    lines = [line.strip() for line in str(result).splitlines() if line.strip()]
    if not lines:
        return "(empty)"
    if lines[0].startswith("# "):
        lines = lines[1:]
    if not lines:
        return "(empty)"
    summary = " | ".join(lines[:3])
    return clip(summary, limit)


def _collect_retrieval_debug_candidates(state, query, workspace_root=None):
    state = normalize_memory_state(state, workspace_root)
    scored = []
    filtered = []

    for note in state["episodic_notes"]:
        candidate = score_memory_candidate(query, note)
        if candidate is None:
            entry = _debug_note_entry(note)
            entry["filter_reason"] = "no_match"
            filtered.append(entry)
            continue
        scored.append(candidate)

    for path in state["working"]["recent_files"][:FILE_SUMMARY_LIMIT]:
        summary = state["file_summaries"].get(path, {})
        summary_text = str(summary.get("summary", "")).strip()
        note = {
            "text": f"{path}: {summary_text}",
            "tags": [path],
            "source": path,
            "created_at": str(summary.get("created_at", "")).strip() or now(),
            "kind": "file_summary",
        }
        if not summary_text:
            entry = _debug_note_entry(note)
            entry["filter_reason"] = "empty_summary"
            filtered.append(entry)
            continue
        current_freshness = file_freshness(path, workspace_root)
        if summary.get("freshness") != current_freshness:
            entry = _debug_note_entry(note)
            entry["filter_reason"] = "stale_file_summary"
            filtered.append(entry)
            continue
        candidate = score_memory_candidate(query, note)
        if candidate is None:
            entry = _debug_note_entry(note)
            entry["filter_reason"] = "no_match"
            filtered.append(entry)
            continue
        scored.append(candidate)

    if workspace_root is not None:
        durable_store = DurableMemoryStore(Path(workspace_root) / ".codecub" / "memory")
        for note in durable_store.retrieval_candidates(query, limit=9999):
            scored.append(note)

    return state, scored, filtered


def retrieval_debug(state, query, limit=3, workspace_root=None):
    state, scored, filtered = _collect_retrieval_debug_candidates(state, query, workspace_root)
    deduped, deduped_entries = _dedupe_retrieval_candidates_with_debug(scored, set(state["file_summaries"]))
    selected, skipped_by_budget = _apply_per_kind_budgets_with_debug(deduped, int(limit))
    return {
        "query": str(query),
        "limit": int(limit),
        "query_tokens": sorted(_tokenize(query)),
        "meaningful_query_tokens": sorted(meaningful_tokens(query)),
        "selected": selected,
        "filtered": filtered,
        "deduped": deduped_entries,
        "skipped_by_budget": skipped_by_budget,
        "durable_filtered_visible": False,
    }


def retrieval_candidates(state, query, limit=3, workspace_root=None):
    debug = retrieval_debug(state, query, limit=limit, workspace_root=workspace_root)
    return debug["selected"]


def _render_debug_list(lines, items, render_item):
    if not items:
        lines.append("- none")
        return
    for index, item in enumerate(items, start=1):
        render_item(lines, index, item)


def retrieval_debug_view(state, query, limit=3, workspace_root=None):
    debug = retrieval_debug(state, query, limit=limit, workspace_root=workspace_root)
    lines = [
        "Relevant memory debug:",
        f"query: {debug['query']}",
        f"limit: {debug['limit']}",
        f"tokens: {', '.join(debug['meaningful_query_tokens']) or '-'}",
        "",
        "Selected:",
    ]

    def render_selected(output, index, item):
        output.append(f"{index}. {item['text']}")
        output.append(f"   kind: {item.get('kind', 'episodic')}")
        output.append(f"   source: {item.get('source', '') or '-'}")
        output.append(f"   reason: {item.get('reason', '-')}")
        output.append(f"   score: {int(item.get('score', 0))}")
        output.append(f"   matched_terms: {', '.join(item.get('matched_terms', [])) or '-'}")

    _render_debug_list(lines, debug["selected"], render_selected)
    lines.extend(["", "Filtered:"])

    def render_filtered(output, _index, item):
        output.append(f"- {item['text']}")
        output.append(f"  kind: {item.get('kind', 'episodic')}")
        output.append(f"  reason: {item.get('filter_reason', '-')}")

    _render_debug_list(lines, debug["filtered"], render_filtered)
    if not debug.get("durable_filtered_visible", True):
        lines.append("- durable filtered candidates are not visible in v1")
    lines.extend(["", "Deduped:"])

    def render_deduped(output, _index, item):
        output.append(f"- {item['text']}")
        output.append(f"  kind: {item.get('kind', 'episodic')}")
        output.append(f"  reason: {item.get('dedupe_reason', '-')}")
        output.append(f"  kept_text: {item.get('kept_text', '-')}")

    _render_debug_list(lines, debug["deduped"], render_deduped)
    lines.extend(["", "Skipped by budget:"])

    def render_skipped(output, _index, item):
        output.append(f"- {item['text']}")
        output.append(f"  kind: {item.get('kind', 'episodic')}")
        output.append(f"  group: {item.get('budget_group', '-')}")
        output.append(f"  limit: {item.get('budget_limit', '-')}")

    _render_debug_list(lines, debug["skipped_by_budget"], render_skipped)
    return "\n".join(lines)


def retrieval_view(state, query, limit=3, workspace_root=None):
    candidates = retrieval_candidates(state, query, limit=limit, workspace_root=workspace_root)
    lines = ["Relevant memory:"]
    if not candidates:
        lines.append("- none")
        return "\n".join(lines)
    for note in candidates:
        lines.append(f"- {note['text']}")
    return "\n".join(lines)


def render_memory_text(state, workspace_root=None):
    state = normalize_memory_state(state, workspace_root)
    # 这里渲染的是给模型看的紧凑“仪表盘”，不是完整回放。
    # 笔记正文默认不展开，只有在相关召回时才按需拿出来。
    lines = [
        "Memory:",
        f"- task: {state['working']['task_summary'] or '-'}",
        f"- recent_files: {', '.join(state['working']['recent_files']) or '-'}",
    ]

    summary_paths = []
    for path in state["working"]["recent_files"][:FILE_SUMMARY_LIMIT]:
        summary = state["file_summaries"].get(path, {})
        current_freshness = file_freshness(path, workspace_root)
        if summary.get("summary", "") and summary.get("freshness") == current_freshness:
            summary_paths.append(path)
    if summary_paths:
        lines.append(f"- file_summaries: available for {', '.join(summary_paths)}")
    else:
        lines.append("- file_summaries: -")

    lines.append(f"- episodic_notes: {len(state['episodic_notes'])}")
    durable_topics = state.get("durable_topics", [])
    lines.append(f"- durable_topics: {', '.join(durable_topics) or '-'}")
    return "\n".join(lines)


def is_effectively_empty(state, workspace_root=None):
    state = normalize_memory_state(state, workspace_root)
    return (
        not str(state["working"]["task_summary"]).strip()
        and not state["working"]["recent_files"]
        and not state["episodic_notes"]
        and not state["file_summaries"]
    )


class LayeredMemory:
    def __init__(self, state=None, workspace_root=None):
        self.workspace_root = workspace_root
        self.state = normalize_memory_state(state, workspace_root)
        self.durable_store = DurableMemoryStore(Path(workspace_root) / ".codecub" / "memory") if workspace_root is not None else None

    def to_dict(self):
        self.state = normalize_memory_state(self.state, self.workspace_root)
        return self.state

    def canonical_path(self, path):
        return canonicalize_path(path, self.workspace_root)

    def set_task_summary(self, summary):
        self.state = set_task_summary(self.state, summary, self.workspace_root)
        return self

    def remember_file(self, path):
        self.state = remember_file(self.state, path, self.workspace_root)
        return self

    def append_note(self, text, tags=(), source="", created_at=None, kind="episodic"):
        self.state = append_note(
            self.state,
            text,
            tags=tags,
            source=source,
            created_at=created_at,
            workspace_root=self.workspace_root,
            kind=kind,
        )
        return self

    def set_file_summary(self, path, summary):
        self.state = set_file_summary(self.state, path, summary, self.workspace_root)
        return self

    def invalidate_file_summary(self, path):
        self.state = invalidate_file_summary(self.state, path, self.workspace_root)
        return self

    def invalidate_stale_file_summaries(self):
        self.state, invalidated = invalidate_stale_file_summaries(self.state, self.workspace_root)
        return invalidated

    def retrieval_candidates(self, query, limit=3):
        return retrieval_candidates(self.state, query, limit=limit, workspace_root=self.workspace_root)

    def retrieval_view(self, query, limit=3):
        return retrieval_view(self.state, query, limit=limit, workspace_root=self.workspace_root)

    def retrieval_debug(self, query, limit=3):
        return retrieval_debug(self.state, query, limit=limit, workspace_root=self.workspace_root)

    def retrieval_debug_view(self, query, limit=3):
        return retrieval_debug_view(self.state, query, limit=limit, workspace_root=self.workspace_root)

    def render_memory_text(self):
        return render_memory_text(self.state, self.workspace_root)

    def promote_durable(self, promotions):
        if self.durable_store is None:
            return [], []
        self.state = normalize_memory_state(self.state, self.workspace_root)
        promoted, superseded = self.durable_store.promote(promotions)
        self.state = normalize_memory_state(self.state, self.workspace_root)
        return promoted, superseded
