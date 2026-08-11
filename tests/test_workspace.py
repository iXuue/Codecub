import subprocess

import codecub.workspace as workspace_module
from codecub.workspace import WorkspaceContext


def test_workspace_git_root_decodes_utf8_paths(monkeypatch, tmp_path):
    repo = tmp_path / "代码备份" / "demo"
    repo.mkdir(parents=True)
    (repo / ".git").mkdir()
    (repo / "README.md").write_text("demo\n", encoding="utf-8")

    def fake_run_git(cwd, args, fallback=""):
        assert cwd == repo.resolve()
        command = tuple(args)
        if command == ("rev-parse", "--show-toplevel"):
            return repo.as_posix()
        elif command == ("branch", "--show-current"):
            return "main\n"
        elif command == ("symbolic-ref", "--short", "refs/remotes/origin/HEAD"):
            return "origin/main\n"
        elif command == ("status", "--short"):
            return "clean\n"
        elif command == ("log", "--oneline", "-5"):
            return "abc123 init\n"
        return fallback

    monkeypatch.setattr(workspace_module, "_run_git", fake_run_git)

    workspace = WorkspaceContext.build(repo)

    assert workspace.repo_root == str(repo.resolve())
    assert "README.md" in workspace.project_docs


def test_workspace_without_git_marker_skips_git(monkeypatch, tmp_path):
    project = tmp_path / "plain-project"
    project.mkdir()
    (project / "README.md").write_text("plain project\n", encoding="utf-8")

    def fail_git(*_args, **_kwargs):
        raise AssertionError("git should not run outside a git workspace")

    monkeypatch.setattr(workspace_module, "_run_git", fail_git)

    workspace = WorkspaceContext.build(project)

    assert workspace.repo_root == str(project.resolve())
    assert workspace.branch == "-"
    assert workspace.default_branch == "main"
    assert workspace.status == "clean"
    assert workspace.recent_commits == []
    assert "README.md" in workspace.project_docs


def test_run_git_timeout_returns_fallback_and_kills_process(monkeypatch, tmp_path):
    class FakeProcess:
        returncode = None

        def __init__(self):
            self.killed = False

        def communicate(self, timeout=None):
            if not self.killed:
                raise subprocess.TimeoutExpired(cmd=["git"], timeout=timeout)
            return b"", b""

        def kill(self):
            self.killed = True

    fake_process = FakeProcess()

    def fake_popen(*_args, **_kwargs):
        return fake_process

    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    result = workspace_module._run_git(tmp_path, ["rev-parse", "--show-toplevel"], "fallback-root")

    assert result == "fallback-root"
    assert fake_process.killed is True
