from codecub.memory import LayeredMemory


def test_working_memory_tracks_summary_and_recent_files():
    memory = LayeredMemory()

    memory.set_task_summary("Investigate flaky tests")
    memory.remember_file("README.md")
    memory.remember_file("src/app.py")
    memory.remember_file("README.md")

    snapshot = memory.to_dict()

    assert snapshot["working"]["task_summary"] == "Investigate flaky tests"
    assert snapshot["working"]["recent_files"] == ["src/app.py", "README.md"]
    assert snapshot["task"] == "Investigate flaky tests"
    assert snapshot["files"] == ["src/app.py", "README.md"]


def test_episodic_notes_append_and_retrieve_deterministically():
    memory = LayeredMemory()

    memory.append_note("Exact tag note", tags=("recall",), created_at="2026-04-07T10:00:00+00:00")
    memory.append_note("Keyword overlap note about memory", created_at="2026-04-07T10:01:00+00:00")
    memory.append_note("Newest unrelated note", created_at="2026-04-07T10:02:00+00:00")
    memory.append_note("Older unrelated note", created_at="2026-04-07T09:59:00+00:00")

    snapshot = memory.to_dict()
    assert [note["text"] for note in snapshot["episodic_notes"]] == [
        "Exact tag note",
        "Keyword overlap note about memory",
        "Newest unrelated note",
        "Older unrelated note",
    ]
    assert snapshot["notes"] == [
        "Exact tag note",
        "Keyword overlap note about memory",
        "Newest unrelated note",
        "Older unrelated note",
    ]

    lines = [line for line in memory.retrieval_view("recall memory", limit=4).splitlines() if line.startswith("- ")]
    assert lines == [
        "- Exact tag note",
        "- Keyword overlap note about memory",
    ]


def test_file_summaries_use_canonical_paths_and_freshness(tmp_path):
    file_path = tmp_path / "sample.txt"
    file_path.write_text("alpha\n", encoding="utf-8")
    memory = LayeredMemory(workspace_root=tmp_path)

    memory.set_file_summary("./sample.txt", "alpha")
    memory.remember_file("./sample.txt")
    snapshot = memory.to_dict()["file_summaries"]["sample.txt"]

    assert snapshot["summary"] == "alpha"
    assert snapshot["freshness"]

    memory_text = memory.render_memory_text()
    assert "file_summaries: available for sample.txt" in memory_text
    assert "sample.txt: alpha" not in memory_text
    file_path.write_text("beta\n", encoding="utf-8")
    assert "file_summaries: -" in memory.render_memory_text()

    memory.invalidate_file_summary("sample.txt")

    assert "sample.txt" not in memory.to_dict()["file_summaries"]


def test_file_summaries_are_recalled_only_when_relevant(tmp_path):
    file_path = tmp_path / "sample.txt"
    file_path.write_text("alpha\n", encoding="utf-8")
    memory = LayeredMemory(workspace_root=tmp_path)

    memory.set_file_summary("sample.txt", "alpha")
    memory.remember_file("sample.txt")

    related_by_path = memory.retrieval_view("what did sample.txt contain?", limit=4)
    related_by_summary = memory.retrieval_view("where did we see alpha?", limit=4)
    unrelated = memory.retrieval_view("how should I deploy?", limit=4)

    assert "- sample.txt: alpha" in related_by_path
    assert "- sample.txt: alpha" in related_by_summary
    assert "sample.txt: alpha" not in unrelated

    file_path.write_text("beta\n", encoding="utf-8")

    stale = memory.retrieval_view("what did sample.txt contain?", limit=4)
    assert "sample.txt: alpha" not in stale


def test_retrieval_candidates_include_score_reason_and_matches(tmp_path):
    file_path = tmp_path / "sample.txt"
    file_path.write_text("alpha\n", encoding="utf-8")
    memory = LayeredMemory(workspace_root=tmp_path)
    memory.set_file_summary("sample.txt", "alpha")
    memory.remember_file("sample.txt")

    candidate = memory.retrieval_candidates("what did sample.txt contain?", limit=3)[0]

    assert candidate["text"] == "sample.txt: alpha"
    assert candidate["kind"] == "file_summary"
    assert candidate["source"] == "sample.txt"
    assert candidate["reason"] == "path_match"
    assert candidate["score"] >= 120
    assert "sample" in candidate["matched_terms"] or "sample.txt" in candidate["matched_terms"]


def test_stopword_only_query_does_not_recall_weak_matches():
    memory = LayeredMemory()
    memory.append_note("Deploy key is red", created_at="2026-04-07T10:00:00+00:00")

    view = memory.retrieval_view("what should I do?", limit=3)

    assert view == "Relevant memory:\n- none"


def test_path_match_survives_stopword_filtering(tmp_path):
    file_path = tmp_path / "sample.txt"
    file_path.write_text("alpha\n", encoding="utf-8")
    memory = LayeredMemory(workspace_root=tmp_path)
    memory.set_file_summary("sample.txt", "alpha")
    memory.remember_file("sample.txt")

    view = memory.retrieval_view("what did sample.txt contain?", limit=3)

    assert "- sample.txt: alpha" in view


def test_process_notes_keep_kind_and_latest_duplicate_wins():
    memory = LayeredMemory()

    memory.append_note(
        "Shell partial success on README.md; inspect diff before retry",
        tags=("process", "partial_success"),
        created_at="2026-04-07T10:00:00+00:00",
        kind="process",
    )
    memory.append_note(
        "Shell partial success on README.md; inspect diff before retry",
        tags=("process", "partial_success"),
        created_at="2026-04-07T10:01:00+00:00",
        kind="process",
    )

    notes = memory.to_dict()["episodic_notes"]

    assert len(notes) == 1
    assert notes[0]["kind"] == "process"
    assert notes[0]["created_at"] == "2026-04-07T10:01:00+00:00"


def test_durable_memory_index_and_topic_notes_are_loaded_and_retrieved(tmp_path):
    memory_root = tmp_path / ".codecub" / "memory"
    topics_dir = memory_root / "topics"
    topics_dir.mkdir(parents=True)
    (memory_root / "MEMORY.md").write_text(
        "# Durable Memory Index\n\n"
        "- [project-conventions](topics/project-conventions.md): Project Conventions\n"
        "  - summary: Stable repository conventions.\n"
        "  - tags: convention\n",
        encoding="utf-8",
    )
    (topics_dir / "project-conventions.md").write_text(
        "# Project Conventions\n\n"
        "- topic: project-conventions\n"
        "- summary: Stable repository conventions.\n"
        "- tags: convention\n"
        "- updated_at: 2026-04-12T08:14:49+00:00\n\n"
        "## Notes\n"
        "- Use constrained tools instead of guessing.\n"
        "- Preserve local agent state under .codecub/.\n",
        encoding="utf-8",
    )

    memory = LayeredMemory(workspace_root=tmp_path)

    snapshot = memory.to_dict()
    assert snapshot["durable_topics"] == ["project-conventions"]

    lines = [line for line in memory.retrieval_view("constrained tools", limit=4).splitlines() if line.startswith("- ")]
    assert any("Use constrained tools instead of guessing." in line for line in lines)

    candidate = memory.retrieval_candidates("constrained tools", limit=4)[0]
    assert candidate["kind"] == "durable"
    assert candidate["source"] == "project-conventions"
    assert candidate["reason"] in {"tag_match", "keyword_match", "source_match"}
    assert candidate["score"] > 0


def test_retrieval_applies_per_kind_budgets(tmp_path):
    for name in ("one.txt", "two.txt"):
        (tmp_path / name).write_text("alpha\n", encoding="utf-8")

    memory_root = tmp_path / ".codecub" / "memory"
    topics_dir = memory_root / "topics"
    topics_dir.mkdir(parents=True)
    (memory_root / "MEMORY.md").write_text(
        "# Durable Memory Index\n\n"
        "- [project-conventions](topics/project-conventions.md): Project Conventions\n"
        "  - summary: Stable repository conventions.\n"
        "  - tags: convention\n",
        encoding="utf-8",
    )
    (topics_dir / "project-conventions.md").write_text(
        "# Project Conventions\n\n"
        "- topic: project-conventions\n"
        "- summary: Stable repository conventions.\n"
        "- tags: convention\n"
        "- updated_at: 2026-04-12T08:14:49+00:00\n\n"
        "## Notes\n"
        "- Durable alpha one.\n"
        "- Durable alpha two.\n",
        encoding="utf-8",
    )

    memory = LayeredMemory(workspace_root=tmp_path)
    memory.set_file_summary("one.txt", "alpha one")
    memory.set_file_summary("two.txt", "alpha two")
    memory.remember_file("one.txt")
    memory.remember_file("two.txt")
    memory.append_note("Episodic alpha", created_at="2026-04-07T10:00:00+00:00")
    memory.append_note("Process alpha", created_at="2026-04-07T10:01:00+00:00", kind="process")

    candidates = memory.retrieval_candidates("alpha", limit=3)

    kinds = [candidate["kind"] for candidate in candidates]
    assert kinds.count("file_summary") <= 1
    assert kinds.count("durable") <= 1
    assert sum(1 for kind in kinds if kind in {"episodic", "process"}) <= 2
    assert len(candidates) <= 3


def test_retrieval_dedupes_exact_text_and_same_file_candidates(tmp_path):
    readme = tmp_path / "README.md"
    readme.write_text("alpha\n", encoding="utf-8")
    memory = LayeredMemory(
        state={
            "episodic_notes": [
                {
                    "text": "same text alpha",
                    "tags": [],
                    "source": "first",
                    "created_at": "2026-04-07T10:00:00+00:00",
                    "note_index": 0,
                    "kind": "episodic",
                },
                {
                    "text": "same text alpha",
                    "tags": [],
                    "source": "second",
                    "created_at": "2026-04-07T10:01:00+00:00",
                    "note_index": 1,
                    "kind": "episodic",
                },
            ],
        },
        workspace_root=tmp_path,
    )
    memory.set_file_summary("README.md", "alpha")
    memory.remember_file("README.md")
    memory.append_note(
        "README process alpha",
        source="README.md",
        created_at="2026-04-07T10:02:00+00:00",
        kind="process",
    )

    candidates = memory.retrieval_candidates("README.md alpha", limit=4)
    selected_texts = [candidate["text"] for candidate in candidates]
    selected_sources = [candidate["source"] for candidate in candidates]

    assert selected_texts.count("same text alpha") == 1
    assert selected_sources.count("README.md") <= 1
    assert "README.md: alpha" in selected_texts


def test_retrieval_debug_reports_selected_and_filtered_candidates(tmp_path):
    file_path = tmp_path / "sample.txt"
    file_path.write_text("alpha\n", encoding="utf-8")
    memory = LayeredMemory(workspace_root=tmp_path)
    memory.set_file_summary("sample.txt", "alpha")
    memory.remember_file("sample.txt")
    memory.append_note("Deploy key is red", created_at="2026-04-07T10:00:00+00:00")

    debug = memory.retrieval_debug("what did sample.txt contain?", limit=3)

    assert debug["query"] == "what did sample.txt contain?"
    assert debug["limit"] == 3
    assert debug["meaningful_query_tokens"] == ["sample", "txt"]
    assert debug["selected"][0]["text"] == "sample.txt: alpha"
    assert debug["selected"][0]["reason"] == "path_match"
    assert debug["selected"][0]["score"] >= 120
    assert any(item["text"] == "Deploy key is red" and item["filter_reason"] == "no_match" for item in debug["filtered"])


def test_retrieval_debug_reports_deduped_candidates():
    memory = LayeredMemory(
        state={
            "episodic_notes": [
                {
                    "text": "same text alpha",
                    "tags": [],
                    "source": "older",
                    "created_at": "2026-04-07T10:00:00+00:00",
                    "note_index": 0,
                    "kind": "episodic",
                },
                {
                    "text": "same text alpha",
                    "tags": [],
                    "source": "newer",
                    "created_at": "2026-04-07T10:01:00+00:00",
                    "note_index": 1,
                    "kind": "episodic",
                },
            ],
        }
    )

    debug = memory.retrieval_debug("alpha", limit=3)

    assert len(debug["selected"]) == 1
    assert debug["selected"][0]["source"] == "newer"
    assert debug["deduped"] == [
        {
            "text": "same text alpha",
            "kind": "episodic",
            "source": "older",
            "dedupe_reason": "duplicate_text",
            "kept_text": "same text alpha",
        }
    ]


def test_retrieval_debug_reports_budget_skips(tmp_path):
    for name in ("one.txt", "two.txt"):
        (tmp_path / name).write_text("alpha\n", encoding="utf-8")
    memory = LayeredMemory(workspace_root=tmp_path)
    memory.set_file_summary("one.txt", "alpha one")
    memory.set_file_summary("two.txt", "alpha two")
    memory.remember_file("one.txt")
    memory.remember_file("two.txt")

    debug = memory.retrieval_debug("alpha", limit=3)

    assert sum(1 for item in debug["selected"] if item["kind"] == "file_summary") == 1
    assert debug["skipped_by_budget"] == [
        {
            "text": "one.txt: alpha one",
            "kind": "file_summary",
            "source": "one.txt",
            "budget_group": "file_summary",
            "budget_limit": 1,
        }
    ] or debug["skipped_by_budget"] == [
        {
            "text": "two.txt: alpha two",
            "kind": "file_summary",
            "source": "two.txt",
            "budget_group": "file_summary",
            "budget_limit": 1,
        }
    ]


def test_retrieval_debug_view_renders_sections(tmp_path):
    file_path = tmp_path / "sample.txt"
    file_path.write_text("alpha\n", encoding="utf-8")
    memory = LayeredMemory(workspace_root=tmp_path)
    memory.set_file_summary("sample.txt", "alpha")
    memory.remember_file("sample.txt")
    memory.append_note("Deploy key is red", created_at="2026-04-07T10:00:00+00:00")

    view = memory.retrieval_debug_view("what did sample.txt contain?", limit=3)

    assert view.startswith("Relevant memory debug:\n")
    assert "query: what did sample.txt contain?" in view
    assert "Selected:\n1. sample.txt: alpha" in view
    assert "reason: path_match" in view
    assert "Filtered:\n- Deploy key is red" in view
    assert "Skipped by budget:\n- none" in view
