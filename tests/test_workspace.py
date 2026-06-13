import subprocess

from codecub.workspace import WorkspaceContext


def test_workspace_git_root_decodes_utf8_paths(monkeypatch, tmp_path):
    repo = tmp_path / "代码备份" / "demo"
    repo.mkdir(parents=True)
    (repo / "README.md").write_text("demo\n", encoding="utf-8")

    def fake_run(args, cwd, capture_output, check, timeout):
        assert capture_output is True
        assert check is True
        assert timeout == 5
        command = tuple(args[1:])
        if command == ("rev-parse", "--show-toplevel"):
            stdout = f"{repo.as_posix()}\n".encode("utf-8")
        elif command == ("branch", "--show-current"):
            stdout = b"main\n"
        elif command == ("symbolic-ref", "--short", "refs/remotes/origin/HEAD"):
            stdout = b"origin/main\n"
        elif command == ("status", "--short"):
            stdout = b"clean\n"
        elif command == ("log", "--oneline", "-5"):
            stdout = b"abc123 init\n"
        else:
            stdout = b""
        return subprocess.CompletedProcess(args=args, returncode=0, stdout=stdout, stderr=b"")

    monkeypatch.setattr(subprocess, "run", fake_run)

    workspace = WorkspaceContext.build(repo)

    assert workspace.repo_root == str(repo.resolve())
    assert "README.md" in workspace.project_docs
