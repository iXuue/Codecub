"""Deterministic end-to-end smoke: retrieval → model → patch → report."""

import json
import tempfile
from pathlib import Path

from codecub.models import FakeModelClient
from codecub.runtime import Pico, SessionStore
from codecub.workspace import WorkspaceContext


def main():
    with tempfile.TemporaryDirectory(prefix="codecub-smoke-") as temporary:
        root = Path(temporary)
        (root / "sample.py").write_text("VALUE = 'old'\n", encoding="utf-8")
        agent = Pico(
            model_client=FakeModelClient(
                [
                    '<tool>{"name":"retrieve_code","args":{"query":"VALUE","limit":3}}</tool>',
                    '<tool>{"name":"patch_file","args":{"path":"sample.py","old_text":"old","new_text":"new"}}</tool>',
                    '<tool>{"name":"run_shell","args":{"command":"python -c \\"from pathlib import Path; assert \'new\' in Path(\'sample.py\').read_text()\\"","timeout":20}}</tool>',
                    "<final>verified</final>",
                ]
            ),
            workspace=WorkspaceContext.build(root),
            session_store=SessionStore(root / ".codecub" / "sessions"),
            approval_policy="auto",
            max_steps=5,
        )
        answer = agent.ask("Update sample value and verify the relevant code.")
        report = agent.run_store.report_path(agent.current_task_state)
        payload = {
            "answer": answer,
            "patched": "new" in (root / "sample.py").read_text(encoding="utf-8"),
            "retrieval_strategy": agent.last_retrieval_result.strategy,
            "test_ran": agent.current_task_state.tool_steps == 3,
            "report_exists": report.exists(),
        }
        print(json.dumps(payload))
        if not all((payload["patched"], payload["test_ran"], payload["report_exists"])):
            raise SystemExit(1)


if __name__ == "__main__":
    main()
