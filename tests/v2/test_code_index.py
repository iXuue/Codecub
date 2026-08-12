from codecub.code_index import CodeIndex


def test_index_extracts_symbols_imports_and_syntactic_calls(tmp_path):
    source = tmp_path / "service.py"
    source.write_text(
        "from users import find_user\n\nclass AuthService:\n    async def login(self, name):\n        return find_user(name)\n\ndef login(name):\n    return AuthService().login(name)\n",
        encoding="utf-8",
    )
    index = CodeIndex(tmp_path)
    summary = index.refresh()

    assert summary["reindexed_files"] == ["service.py"]
    assert [(item.qualified_name, item.kind) for item in index.symbol_search("login")] == [
        ("AuthService.login", "async_method"),
        ("login", "function"),
    ]
    assert [item.qualified_name for item in index.symbol_search("login", path="service.py")] == ["AuthService.login", "login"]
    assert [(path, line) for path, line in index.find_references("find_user")] == [("service.py", 5)]
    assert index.files["service.py"]["imports"] == [{"name": "find_user", "module": "users", "line": 1}]


def test_index_incrementally_replaces_changed_and_deleted_files(tmp_path):
    source = tmp_path / "a.py"
    source.write_text("def old():\n    return 1\n", encoding="utf-8")
    index = CodeIndex(tmp_path)
    index.refresh()
    source.write_text("def new():\n    return 2\n", encoding="utf-8")

    refreshed = index.refresh(["a.py"])
    assert refreshed["reindexed_files"] == ["a.py"]
    assert not index.symbol_search("old")
    assert index.symbol_search("new")[0].qualified_name == "new"

    source.unlink()
    index.refresh(["a.py"])
    assert "a.py" not in index.files


def test_index_records_syntax_errors_without_failing_the_project(tmp_path):
    (tmp_path / "bad.py").write_text("def broken(:\n", encoding="utf-8")
    (tmp_path / "good.py").write_text("def valid():\n    pass\n", encoding="utf-8")
    index = CodeIndex(tmp_path)

    summary = index.refresh()

    assert summary["parse_errors"] == ["bad.py"]
    assert index.symbol_search("valid")[0].path == "good.py"
