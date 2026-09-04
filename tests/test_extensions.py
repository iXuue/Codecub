import json

import pytest

from codecub import ExtensionConflict, ExtensionContext, ExtensionManifest, ExtensionRegistry
from codecub.cli import build_agent, build_arg_parser
from codecub.tooling.registry import ToolRegistry


def test_extension_discovery_reads_manifests_without_loading_entrypoint(tmp_path):
    root = tmp_path / ".codecub" / "plugins"
    root.mkdir(parents=True)
    (root / "plugin.json").write_text(
        json.dumps(
            {
                "name": "demo-plugin",
                "kind": "plugin",
                "version": "1.0",
                "entrypoint": "missing_module:factory",
                "capabilities": ["tools", "events"],
            }
        ),
        encoding="utf-8",
    )
    (tmp_path / ".codecub" / "skills").mkdir(parents=True)
    (tmp_path / ".codecub" / "skills" / "skill.json").write_text(
        json.dumps({"name": "review-skill", "kind": "skill", "description": "review"}),
        encoding="utf-8",
    )

    registry = ExtensionRegistry().discover([root, tmp_path / ".codecub" / "skills"])
    assert registry.get("demo-plugin").entrypoint == "missing_module:factory"
    assert registry.get("review-skill").kind == "skill"
    assert registry.to_dict()["active"] == []
    assert registry.errors == []


def test_extension_activation_is_explicit_and_lazy():
    manifest = ExtensionManifest.from_dict(
        {
            "name": "lazy",
            "kind": "plugin",
            "entrypoint": "demo:factory",
        }
    )
    registry = ExtensionRegistry()
    registry.register(manifest)
    loaded = []

    def loader(entrypoint):
        loaded.append(entrypoint)
        return lambda current: {"name": current.name}

    assert loaded == []
    assert registry.activate("lazy", loader=loader) == {"name": "lazy"}
    assert loaded == ["demo:factory"]
    assert registry.activate("lazy", loader=loader) == {"name": "lazy"}
    assert loaded == ["demo:factory"]


def test_extension_conflicts_require_higher_priority():
    registry = ExtensionRegistry()
    registry.register(ExtensionManifest.from_dict({"name": "same", "kind": "skill"}))
    with pytest.raises(ExtensionConflict):
        registry.register(ExtensionManifest.from_dict({"name": "same", "kind": "skill"}))
    registry.register(
        ExtensionManifest.from_dict({"name": "same", "kind": "skill", "priority": 1})
    )
    assert registry.get("same").priority == 1


def test_build_agent_attaches_manifest_registry_without_creating_extension_dirs(tmp_path):
    args = build_arg_parser().parse_args(
        ["--cwd", str(tmp_path), "--provider", "ollama"]
    )
    agent = build_agent(args)
    assert isinstance(agent.extension_registry, ExtensionRegistry)
    assert not (tmp_path / ".codecub" / "plugins").exists()
    assert not (tmp_path / ".codecub" / "skills").exists()


def test_extension_lifecycle_requires_grants_and_registers_tools():
    registry = ExtensionRegistry()
    registry.register(ExtensionManifest.from_dict({"name": "base", "kind": "skill"}))
    registry.register(
        ExtensionManifest.from_dict(
            {
                "name": "plugin",
                "kind": "plugin",
                "entrypoint": "demo:factory",
                "dependencies": ["base"],
                "capabilities": ["tools"],
            }
        )
    )
    lifecycle = []

    class Plugin:
        def activate(self, context):
            context.require("tools")
            lifecycle.append("activate")

        def register_tools(self, tools):
            tools.register("extension_ping", {"description": "ping", "run": lambda _args: "pong"})

        def deactivate(self, _context=None):
            lifecycle.append("deactivate")

    tool_registry = ToolRegistry()
    context = ExtensionContext(tool_registry=tool_registry, granted_capabilities=frozenset({"tools"}))
    active = registry.activate(
        "plugin", loader=lambda _entrypoint: lambda _manifest: Plugin(), context=context
    )
    assert isinstance(active, Plugin)
    assert registry.get("base") is not None
    assert registry.to_dict()["active"] == ["base", "plugin"]
    assert tool_registry.has("extension_ping")
    assert registry.deactivate("plugin", context) is True
    assert lifecycle == ["activate", "deactivate"]


def test_extension_activation_without_capability_grant_fails_closed():
    registry = ExtensionRegistry()
    registry.register(
        ExtensionManifest.from_dict(
            {"name": "needs-network", "kind": "plugin", "entrypoint": "demo:factory", "capabilities": ["network"]}
        )
    )
    with pytest.raises(ValueError, match="explicit grant"):
        registry.activate("needs-network", loader=lambda _entrypoint: lambda _manifest: object())
