from codecub import FakeModelClient, MiniAgent, SessionStore, WorkspaceContext


def test_runtime_persists_request_usage_and_aggregates_report(tmp_path):
    (tmp_path / "README.md").write_text("demo\n", encoding="utf-8")
    model = FakeModelClient(["<final>Done.</final>"])
    model.last_completion_metadata = {
        "usage_record": {
            "schema_version": 1,
            "connection_profile_id": "rightcode-codex",
            "protocol": "responses",
            "context": {"actual_input_tokens": 1000},
            "cache": {"read_tokens": 800, "write_tokens": None, "uncached_input_tokens": 200},
            "output": {"output_tokens": 50, "reasoning_tokens": 20},
            "cost": {"operator_billed_cost": None},
        }
    }
    agent = MiniAgent(
        model_client=model,
        workspace=WorkspaceContext.build(tmp_path),
        session_store=SessionStore(tmp_path / ".codecub" / "sessions"),
        approval_policy="auto",
    )
    events = []
    agent.event_handler = lambda name, payload, runtime, state: events.append((name, payload))

    assert agent.ask("Measure this run.", run_id="run_usage_runtime") == "Done."

    records = agent.run_store.load_usage("run_usage_runtime")
    report = agent.run_store.load_report("run_usage_runtime")
    assert len(records) == 1
    assert records[0]["connection_profile_id"] == "rightcode-codex"
    assert records[0]["request_index"] == 1
    assert report["usage_summary"]["actual_input_tokens"] == 1000
    assert report["usage_summary"]["cache_read_tokens"] == 800
    assert report["usage_summary"]["weighted_cache_read_ratio"] == 0.8
    usage_event = next(payload for name, payload in events if name == "usage_updated")
    assert usage_event["schema_version"] == 2
    assert usage_event["run_snapshot"]["groups"][0]["connection"]["connection_profile_id"] == "rightcode-codex"
    assert usage_event["session_snapshot"]["revision"] == 1
    assert "raw_usage" not in str(usage_event)
