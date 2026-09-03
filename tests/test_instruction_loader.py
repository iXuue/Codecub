"""Independent Phase 7D InstructionLoader contract tests."""

import ast
from pathlib import Path

import pytest

from codecub.instruction_loader import (
    InstructionLoader,
)
from codecub.instructions import Instruction, InstructionLayer, InstructionResolver


def test_repository_without_agents_is_valid(tmp_path):
    result = InstructionLoader(tmp_path).load()

    assert result.instructions == ()
    assert result.discovered_files == ()
    assert result.load_errors == ()


def test_root_agents_loads_as_repository_instruction_with_provenance(tmp_path):
    (tmp_path / "AGENTS.md").write_text("Run pytest before finalizing.", encoding="utf-8")

    result = InstructionLoader(tmp_path).load()

    assert result.loaded_files == ("AGENTS.md",)
    item = result.instructions[0]
    assert item.content == "Run pytest before finalizing."
    assert item.source == "repository"
    assert item.layer == InstructionLayer.REPOSITORY
    assert item.scope_id == ""
    assert item.metadata["source_kind"] == "repository_file"
    assert item.metadata["source_path"] == "AGENTS.md"
    assert item.metadata["scope_path"] == ""
    assert item.metadata["specificity_depth"] == 0
    assert item.metadata["repository_root"] == str(tmp_path.resolve())
    assert item.metadata["file_freshness"]


def test_nested_discovery_is_ancestor_scoped_and_root_to_nested(tmp_path):
    (tmp_path / "backend").mkdir()
    (tmp_path / "frontend").mkdir()
    (tmp_path / "AGENTS.md").write_text("ROOT", encoding="utf-8")
    (tmp_path / "backend" / "AGENTS.md").write_text("BACKEND", encoding="utf-8")
    (tmp_path / "frontend" / "AGENTS.md").write_text("FRONTEND", encoding="utf-8")

    backend = InstructionLoader(tmp_path).load([Path("backend") / "auth.py"])
    frontend = InstructionLoader(tmp_path).load(["frontend/ui.tsx"])

    assert backend.loaded_files == ("AGENTS.md", "backend/AGENTS.md")
    assert [item.content for item in backend.instructions] == ["ROOT", "BACKEND"]
    assert "frontend/AGENTS.md" not in backend.loaded_files
    assert frontend.loaded_files == ("AGENTS.md", "frontend/AGENTS.md")
    assert "backend/AGENTS.md" not in frontend.loaded_files


def test_nested_specificity_is_deterministic_and_preserved_in_resolver(tmp_path):
    (tmp_path / "src" / "api").mkdir(parents=True)
    (tmp_path / "AGENTS.md").write_text("ROOT", encoding="utf-8")
    (tmp_path / "src" / "AGENTS.md").write_text("SRC", encoding="utf-8")
    (tmp_path / "src" / "api" / "AGENTS.md").write_text("API", encoding="utf-8")

    loaded = InstructionLoader(tmp_path).load(["src/api/auth.py"])
    reversed_result = InstructionResolver().resolve(reversed(loaded.instructions))

    assert [item.content for item in reversed_result.instructions] == ["ROOT", "SRC", "API"]
    assert [item.metadata["specificity_depth"] for item in reversed_result.instructions] == [0, 1, 2]


def test_repository_layer_specificity_wins_same_layer_conflict(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "AGENTS.md").write_text("May modify auth.py", encoding="utf-8")
    (tmp_path / "src" / "AGENTS.md").write_text("Do not modify auth.py", encoding="utf-8")

    loaded = InstructionLoader(tmp_path).load(["src/auth.py"])
    resolved = InstructionResolver().resolve(loaded.instructions)

    assert [item.content for item in resolved.instructions] == ["Do not modify auth.py"]
    assert [item.content for item in resolved.shadowed] == ["May modify auth.py"]
    assert resolved.conflict_count == 1


def test_explicit_root_repository_instruction_deduplicates_loaded_root(tmp_path):
    (tmp_path / "AGENTS.md").write_text("Only use pytest.", encoding="utf-8")
    loaded = InstructionLoader(tmp_path).load()
    explicit = Instruction(
        "Only use pytest.",
        source="repository",
        layer="repository",
        scope="repository",
    )

    resolved = InstructionResolver().resolve([*loaded.instructions, explicit])

    assert [item.content for item in resolved.instructions] == ["Only use pytest."]
    assert resolved.deduplicated_count == 1


def test_empty_invalid_and_oversized_files_have_bounded_evidence(tmp_path):
    (tmp_path / "empty").mkdir()
    (tmp_path / "invalid").mkdir()
    (tmp_path / "large").mkdir()
    (tmp_path / "empty" / "AGENTS.md").write_text("", encoding="utf-8")
    (tmp_path / "invalid" / "AGENTS.md").write_bytes(b"\xff\xfe")
    (tmp_path / "large" / "AGENTS.md").write_text("123456789", encoding="utf-8")

    result = InstructionLoader(tmp_path, max_file_bytes=8).load(
        ["empty/file.py", "invalid/file.py", "large/file.py"]
    )

    assert result.instructions == ()
    assert {item["code"] for item in result.ignored_files} == {"empty", "oversized"}
    assert {item["code"] for item in result.load_errors} == {"invalid_encoding"}
    assert all("123456789" not in str(item) for item in result.ignored_files + result.load_errors)


def test_unreadable_file_is_reported_without_an_obscure_exception(tmp_path, monkeypatch):
    path = tmp_path / "AGENTS.md"
    path.write_text("should not load", encoding="utf-8")
    original_read_bytes = Path.read_bytes

    def fail_for_instruction_file(candidate):
        if candidate == path:
            raise OSError("permission denied")
        return original_read_bytes(candidate)

    monkeypatch.setattr(Path, "read_bytes", fail_for_instruction_file)
    result = InstructionLoader(tmp_path).load()

    assert result.instructions == ()
    assert result.load_errors[0]["code"] == "unreadable"


def test_modified_nested_agents_is_visible_on_next_scoped_load(tmp_path):
    (tmp_path / "src").mkdir()
    path = tmp_path / "src" / "AGENTS.md"
    path.write_text("NESTED A", encoding="utf-8")
    loader = InstructionLoader(tmp_path)
    first = loader.load(["src/app.py"])
    path.write_text("NESTED B", encoding="utf-8")
    second = loader.load(["src/app.py"])

    assert [item.content for item in first.instructions] == ["NESTED A"]
    assert [item.content for item in second.instructions] == ["NESTED B"]


def test_target_escape_is_rejected_without_reading_outside_workspace(tmp_path):
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    (outside / "AGENTS.md").write_text("OUTSIDE", encoding="utf-8")

    result = InstructionLoader(tmp_path).load(["../" + outside.name + "/file.py"])

    assert result.instructions == ()
    assert any(item["code"] == "workspace_escape" for item in result.load_errors)


def test_symlink_escape_follows_existing_workspace_policy(tmp_path):
    outside = tmp_path.parent / f"{tmp_path.name}-symlink-outside"
    outside.mkdir()
    (outside / "AGENTS.md").write_text("OUTSIDE", encoding="utf-8")
    link = tmp_path / "AGENTS.md"
    try:
        link.symlink_to(outside / "AGENTS.md")
    except OSError as exc:
        pytest.skip(f"symlink creation is not available in this environment: {exc}")

    result = InstructionLoader(tmp_path).load()

    assert result.instructions == ()
    assert any(item["code"] == "workspace_escape" for item in result.load_errors)


def test_modified_agents_is_visible_on_next_load_without_stale_cache(tmp_path):
    path = tmp_path / "AGENTS.md"
    path.write_text("Rule A", encoding="utf-8")
    loader = InstructionLoader(tmp_path)
    first = loader.load()
    path.write_text("Rule B", encoding="utf-8")
    second = loader.load()

    assert first.instructions[0].content == "Rule A"
    assert second.instructions[0].content == "Rule B"
    assert first.instructions[0].id != second.instructions[0].id
    assert second.instructions[0].metadata["file_freshness"] != first.instructions[0].metadata["file_freshness"]


def test_loader_uses_explicit_workspace_root_and_has_no_runtime_dependency():
    source = Path(__file__).parents[1] / "codecub" / "instruction_loader.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    imported = {
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.Import)
        for alias in node.names
    }
    imported.update(
        alias.name
        for node in ast.walk(tree)
        if isinstance(node, ast.ImportFrom)
        for alias in node.names
    )

    assert not any(name.endswith("runtime") or name.endswith("pico") for name in imported)
    assert "Path.cwd" not in source.read_text(encoding="utf-8")
