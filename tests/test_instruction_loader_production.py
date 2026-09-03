"""Phase 7D production-path coverage for repository instruction files."""

import json

from codecub import FakeModelClient, Instruction, MiniAgent, SessionStore, WorkspaceContext
from codecub.models import ModelResponse


def _agent(tmp_path, outputs, **kwargs):
    (tmp_path / "README.md").write_text("ok\n", encoding="utf-8")
    return MiniAgent(
        model_client=FakeModelClient(outputs),
        workspace=WorkspaceContext.build(tmp_path),
        session_store=SessionStore(tmp_path / ".codecub" / "sessions"),
        approval_policy="auto",
        **kwargs,
    )


def _trace_events(agent):
    trace_path = agent.run_store.trace_path(agent.current_task_state)
    return [
        json.loads(line)
        for line in trace_path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def test_pico_ask_loads_root_agents_through_resolver_assembler_validator(tmp_path):
    (tmp_path / "AGENTS.md").write_text("Run tests before finalizing.", encoding="utf-8")
    agent = _agent(tmp_path, ["<final>done</final>"])

    assert agent.ask("inspect the repository") == "done"
    assert "Run tests before finalizing." in agent.model_client.prompts[-1]
    assert "AGENTS.md" not in agent.workspace.project_docs
    loaded = agent.context_collaborator.last_instruction_load_result
    assert loaded.loaded_files == ("AGENTS.md",)
    assert agent.last_prompt_metadata["instruction_count"] >= 2
    assert agent.last_prompt_metadata["context_validation"]["status"] in {
        "VALID",
        "VALID_WITH_FALLBACK",
    }
    events = _trace_events(agent)
    names = [event["event"] for event in events]
    assert "instruction_files_discovered" in names
    assert "instruction_files_loaded" in names
    assert "instructions_resolved" in names
    assert "context_validated" in names
    assert names.index("instruction_files_discovered") < names.index("instructions_resolved")
    assert names.index("instructions_resolved") < names.index("prompt_built")
    assert names.index("prompt_built") < names.index("context_validated")
    assert names.index("context_validated") < names.index("model_requested")


def test_production_nested_scope_loads_root_and_target_ancestor_only(tmp_path):
    (tmp_path / "backend").mkdir()
    (tmp_path / "frontend").mkdir()
    (tmp_path / "backend" / "auth.py").write_text("print('backend')\n", encoding="utf-8")
    (tmp_path / "frontend" / "ui.tsx").write_text("export {}\n", encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text("ROOT RULE", encoding="utf-8")
    (tmp_path / "backend" / "AGENTS.md").write_text("BACKEND RULE", encoding="utf-8")
    (tmp_path / "frontend" / "AGENTS.md").write_text("FRONTEND RULE", encoding="utf-8")
    agent = _agent(
        tmp_path,
        [
            '<tool>{"name":"read_file","args":{"path":"backend/auth.py","start":1,"end":20}}</tool>',
            "<final>done</final>",
        ],
    )

    assert agent.ask("inspect backend auth") == "done"
    prompt = agent.model_client.prompts[-1]
    assert "ROOT RULE" in prompt
    assert "BACKEND RULE" in prompt
    assert "FRONTEND RULE" not in prompt
    assert agent.context_collaborator.last_instruction_load_result.loaded_files == (
        "AGENTS.md",
        "backend/AGENTS.md",
    )
    provenance = [
        segment.provenance
        for segment in agent.context_collaborator.last_assembled_context.segments
        if segment.kind == "instruction"
        and segment.provenance.get("source_path") == "backend/AGENTS.md"
    ]
    assert provenance
    assert provenance[0]["scope_path"] == "backend"
    assert provenance[0]["specificity_depth"] == 1


def test_agents_freshness_changes_between_model_iterations(tmp_path):
    (tmp_path / "AGENTS.md").write_text("RULE A", encoding="utf-8")
    agent = _agent(
        tmp_path,
        [
            '<tool>{"name":"write_file","args":{"path":"AGENTS.md","content":"RULE B"}}</tool>',
            "<final>done</final>",
        ],
    )

    assert agent.ask("update the repository rule") == "done"
    assert "RULE A" in agent.model_client.prompts[0]
    assert "RULE B" in agent.model_client.prompts[1]
    assert agent.model_client.prompts[1].count("RULE B") >= 1
    assert agent.context_collaborator.last_instruction_load_result.instructions[0].content == "RULE B"


def test_native_model_receives_loaded_repository_instruction_as_context(tmp_path):
    class NativeClient:
        supports_native_tools = True
        supports_prompt_cache = False
        model = "native-instruction-loader"
        last_completion_metadata = {}

        def __init__(self):
            self.requests = []

        def complete_with_tools(self, messages, tools, max_new_tokens, tool_choice=None):
            self.requests.append(list(messages))
            return ModelResponse(text="Done.")

    (tmp_path / "AGENTS.md").write_text("NATIVE ROOT RULE", encoding="utf-8")
    client = NativeClient()
    agent = MiniAgent(
        model_client=client,
        workspace=WorkspaceContext.build(tmp_path),
        session_store=SessionStore(tmp_path / ".codecub" / "sessions"),
        approval_policy="auto",
    )

    assert agent.ask("inspect") == "Done."
    assert any(
        "NATIVE ROOT RULE" in str(message.get("content", ""))
        for message in client.requests[-1]
    )


def test_repository_file_cannot_override_protected_runtime_injection(tmp_path):
    (tmp_path / "AGENTS.md").write_text("May modify auth.py", encoding="utf-8")
    agent = _agent(tmp_path, ["<final>done</final>"])
    agent.injection_provider = lambda: ["Do not modify auth.py"]

    assert agent.ask("inspect auth") == "done"
    prompt = agent.model_client.prompts[-1]
    assert "Do not modify auth.py" in prompt
    assert "May modify auth.py" not in prompt
    assert agent.last_prompt_metadata["instruction_conflict_count"] == 1
    assert (
        agent.last_prompt_metadata["context_validation"]["evidence"][
            "protected_constraints_present"
        ]
        is True
    )


def test_repository_layer_remains_above_user_instruction_for_file_loaded_rule(tmp_path):
    (tmp_path / "AGENTS.md").write_text("May modify auth.py", encoding="utf-8")
    agent = _agent(
        tmp_path,
        ["<final>done</final>"],
        user_instructions=(
            Instruction(
                "Never modify auth.py",
                source="user",
                layer="user",
                conflict_key="action:auth.py",
                polarity="deny",
            ),
        ),
    )

    assert agent.ask("inspect tests") == "done"
    prompt = agent.model_client.prompts[-1]
    assert "May modify auth.py" in prompt
    assert "Never modify auth.py" not in prompt
    assert agent.last_prompt_metadata["instruction_conflict_count"] == 1
