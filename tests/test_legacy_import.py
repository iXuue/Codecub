from codecub.legacy_import import detect_legacy_pico, import_legacy_pico_sessions


def test_detect_legacy_pico_sessions(tmp_path):
    legacy = tmp_path / ".pico" / "sessions"
    legacy.mkdir(parents=True)
    (legacy / "old.json").write_text('{"id":"old","history":[]}', encoding="utf-8")

    result = detect_legacy_pico(tmp_path)

    assert result["exists"] is True
    assert result["session_count"] == 1
    assert result["session_paths"] == [str(legacy / "old.json")]


def test_import_legacy_pico_sessions_copies_without_modifying_source(tmp_path):
    legacy = tmp_path / ".pico" / "sessions"
    legacy.mkdir(parents=True)
    source = legacy / "old.json"
    source.write_text('{"id":"old","history":[]}', encoding="utf-8")

    summary = import_legacy_pico_sessions(tmp_path)

    assert summary["imported_count"] == 1
    assert summary["skipped_count"] == 0
    assert summary["errors"] == []
    assert source.read_text(encoding="utf-8") == '{"id":"old","history":[]}'
    assert (tmp_path / ".codecub" / "sessions" / "old.json").exists()


def test_import_legacy_pico_sessions_skips_existing_targets(tmp_path):
    legacy = tmp_path / ".pico" / "sessions"
    target = tmp_path / ".codecub" / "sessions"
    legacy.mkdir(parents=True)
    target.mkdir(parents=True)
    (legacy / "old.json").write_text('{"id":"old","history":[]}', encoding="utf-8")
    (target / "old.json").write_text('{"id":"existing","history":[]}', encoding="utf-8")

    summary = import_legacy_pico_sessions(tmp_path)

    assert summary["imported_count"] == 0
    assert summary["skipped_count"] == 1
    assert (target / "old.json").read_text(encoding="utf-8") == '{"id":"existing","history":[]}'
