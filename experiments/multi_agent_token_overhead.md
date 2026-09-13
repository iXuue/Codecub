# Multi-Agent Token Overhead Experiment

## Final status

`MULTI_AGENT_TOKEN_EXPERIMENT_INVALID`

本轮严格复用了 `scripts/eval_multiagent.py` 中的 5 组双任务。当前真实路径只创建 Research 子 Agent 并比较串行与并行 dispatch；Implement、Review 没有被调用，Parent/Orchestrator 也没有独立 LLM 请求。因此本报告给出实际可测的 Research-only Token/Latency 数据，但不能宣称完成完整的 Research → Implement → Review 对照。

## Overall Result

| Metric | Single-Agent | Multi-Agent | Change |
| --- | ---: | ---: | ---: |
| Total Token | unavailable | unavailable | unavailable |
| Input Token | unavailable | unavailable | unavailable |
| Cached Input Token | unavailable | unavailable | unavailable |
| Output Token | unavailable | unavailable | unavailable |
| Avg Latency (s) | unavailable | unavailable | unavailable |
| Passed Tasks | 0 | 0 | 0 |
| Token / Passed Task | unavailable | unavailable | unavailable |

Token Overhead（按两臂所有任务 Token 总和计算）：**unavailable**。

Latency Reduction（仅在两臂所有任务成功时计算）：**unavailable**。

本轮尝试的 pair wall-clock 仍记录在下表，但由于任务失败，不能作为有效性能结论。失败类型：`invalid_api_key`。

尝试性 pair wall-clock 平均值：serial `9.54` 秒，parallel `8.37` 秒；该数值不进入有效 Latency Reduction。

Provider 未返回可验证的 cached input token，报告保留为 unavailable。

Provider/model metadata: serial `openai` / `qwen3.7-flash-2026-07-15`; parallel `openai` / `qwen3.7-flash-2026-07-15`. Observed model calls: serial `10`, parallel `10`.

## Per-Task Result

以下列出全部 5 组、10 个任务；失败任务仍保留。

| Pair | Side | Task | Single status | Single total | Multi status | Multi total | Overhead | Passed S/M |
| --- | --- | --- | --- | ---: | --- | ---: | ---: | --- |
| pair-01 | left | Locate token-budget validation. | failed | unavailable | failed | unavailable | unavailable | F/F |
| pair-01 | right | Locate context-window capability validation. | failed | unavailable | failed | unavailable | unavailable | F/F |
| pair-02 | left | Locate symbol-index persistence. | failed | unavailable | failed | unavailable | unavailable | F/F |
| pair-02 | right | Locate async symbol classification. | failed | unavailable | failed | unavailable | unavailable | F/F |
| pair-03 | left | Locate usage aggregation cache accounting. | failed | unavailable | failed | unavailable | unavailable | F/F |
| pair-03 | right | Locate usage persistence boundaries. | failed | unavailable | failed | unavailable | unavailable | F/F |
| pair-04 | left | Locate workspace ignore rules. | failed | unavailable | failed | unavailable | unavailable | F/F |
| pair-04 | right | Locate canonical path normalization. | failed | unavailable | failed | unavailable | unavailable | F/F |
| pair-05 | left | Locate app protocol command validation. | failed | unavailable | failed | unavailable | unavailable | F/F |
| pair-05 | right | Locate app event serialization. | failed | unavailable | failed | unavailable | unavailable | F/F |

## Pair Latency

| Pair | Single wall clock (s) | Multi wall clock (s) | Latency reduction | State unchanged |
| --- | ---: | ---: | ---: | --- |
| pair-01 | 10.00 | 8.62 | 13.75% | True |
| pair-02 | 9.84 | 8.50 | 13.65% | True |
| pair-03 | 9.64 | 8.72 | 9.56% | True |
| pair-04 | 9.34 | 8.66 | 7.35% | True |
| pair-05 | 8.86 | 7.36 | 16.93% | True |

## Multi-Agent Token Breakdown

| Role | Total Token | Share |
| --- | ---: | ---: |
| Orchestrator | 0 | unavailable |
| Research | unavailable | unavailable |
| Implement | 0 | unavailable |
| Review | 0 | unavailable |

当前 Multi-Agent measured arm 的 Parent 是本地调度器，Implement 和 Review 状态均为 `not_invoked`。这些角色的 0 表示本轮没有 LLM 请求，不是把缺失 Usage 当成 0。

## Fairness and data quality audit

| Check | Result |
| --- | --- |
| Exact existing tasks reused | True |
| Same provider/model metadata | True |
| Same model parameters | True |
| Same initial repository state | True |
| Same measured tool path | True |
| All 5 pairs and 10 tasks retained | True |
| Workspace unchanged during pairs | True |
| Runtime initialization left tracked status unchanged | True |
| Provider Usage complete for all four fields | False |
| Full requested R → I → Review workflow present | False |

The repository was already dirty at experiment start: `60` status entries, commit `5cc74add85b5c116922416294df380eee6cd14ab`. The experiment records that state and does not clean or overwrite it.

## Per-task JSON records

The following normalized records are the machine-readable measurements used for the tables above. `null` means the Provider did not return that field; non-LLM roles use zero together with an explicit `usage_status`.

```json
{
  "single_agent": [
    {
      "task_id": "pair-01-left",
      "mode": "single_agent",
      "task": "Locate token-budget validation.",
      "latency_seconds": null,
      "input_tokens": null,
      "cached_input_tokens": null,
      "output_tokens": null,
      "total_tokens": null,
      "task_passed": false,
      "status": "failed",
      "agents": {
        "orchestrator": {
          "input_tokens": 0,
          "cached_input_tokens": 0,
          "output_tokens": 0,
          "total_tokens": 0,
          "usage_status": "not_an_llm_agent",
          "usage_record_count": 0,
          "model_calls": 0
        },
        "research": {
          "input_tokens": 0,
          "cached_input_tokens": 0,
          "output_tokens": 0,
          "total_tokens": 0,
          "usage_status": "not_applicable",
          "usage_record_count": 0,
          "model_calls": 0
        },
        "implement": {
          "input_tokens": 0,
          "cached_input_tokens": 0,
          "output_tokens": 0,
          "total_tokens": 0,
          "usage_status": "not_applicable",
          "usage_record_count": 0,
          "model_calls": 0
        },
        "review": {
          "input_tokens": 0,
          "cached_input_tokens": 0,
          "output_tokens": 0,
          "total_tokens": 0,
          "usage_status": "not_applicable",
          "usage_record_count": 0,
          "model_calls": 0
        },
        "single_agent": {
          "input_tokens": null,
          "cached_input_tokens": null,
          "output_tokens": null,
          "total_tokens": null,
          "usage_status": "unavailable",
          "usage_record_count": 0,
          "status": "failed",
          "model_calls": 1,
          "tool_steps": 0,
          "latency_seconds": null,
          "error_kind": "invalid_api_key"
        }
      }
    },
    {
      "task_id": "pair-01-right",
      "mode": "single_agent",
      "task": "Locate context-window capability validation.",
      "latency_seconds": null,
      "input_tokens": null,
      "cached_input_tokens": null,
      "output_tokens": null,
      "total_tokens": null,
      "task_passed": false,
      "status": "failed",
      "agents": {
        "orchestrator": {
          "input_tokens": 0,
          "cached_input_tokens": 0,
          "output_tokens": 0,
          "total_tokens": 0,
          "usage_status": "not_an_llm_agent",
          "usage_record_count": 0,
          "model_calls": 0
        },
        "research": {
          "input_tokens": 0,
          "cached_input_tokens": 0,
          "output_tokens": 0,
          "total_tokens": 0,
          "usage_status": "not_applicable",
          "usage_record_count": 0,
          "model_calls": 0
        },
        "implement": {
          "input_tokens": 0,
          "cached_input_tokens": 0,
          "output_tokens": 0,
          "total_tokens": 0,
          "usage_status": "not_applicable",
          "usage_record_count": 0,
          "model_calls": 0
        },
        "review": {
          "input_tokens": 0,
          "cached_input_tokens": 0,
          "output_tokens": 0,
          "total_tokens": 0,
          "usage_status": "not_applicable",
          "usage_record_count": 0,
          "model_calls": 0
        },
        "single_agent": {
          "input_tokens": null,
          "cached_input_tokens": null,
          "output_tokens": null,
          "total_tokens": null,
          "usage_status": "unavailable",
          "usage_record_count": 0,
          "status": "failed",
          "model_calls": 1,
          "tool_steps": 0,
          "latency_seconds": null,
          "error_kind": "invalid_api_key"
        }
      }
    },
    {
      "task_id": "pair-02-left",
      "mode": "single_agent",
      "task": "Locate symbol-index persistence.",
      "latency_seconds": null,
      "input_tokens": null,
      "cached_input_tokens": null,
      "output_tokens": null,
      "total_tokens": null,
      "task_passed": false,
      "status": "failed",
      "agents": {
        "orchestrator": {
          "input_tokens": 0,
          "cached_input_tokens": 0,
          "output_tokens": 0,
          "total_tokens": 0,
          "usage_status": "not_an_llm_agent",
          "usage_record_count": 0,
          "model_calls": 0
        },
        "research": {
          "input_tokens": 0,
          "cached_input_tokens": 0,
          "output_tokens": 0,
          "total_tokens": 0,
          "usage_status": "not_applicable",
          "usage_record_count": 0,
          "model_calls": 0
        },
        "implement": {
          "input_tokens": 0,
          "cached_input_tokens": 0,
          "output_tokens": 0,
          "total_tokens": 0,
          "usage_status": "not_applicable",
          "usage_record_count": 0,
          "model_calls": 0
        },
        "review": {
          "input_tokens": 0,
          "cached_input_tokens": 0,
          "output_tokens": 0,
          "total_tokens": 0,
          "usage_status": "not_applicable",
          "usage_record_count": 0,
          "model_calls": 0
        },
        "single_agent": {
          "input_tokens": null,
          "cached_input_tokens": null,
          "output_tokens": null,
          "total_tokens": null,
          "usage_status": "unavailable",
          "usage_record_count": 0,
          "status": "failed",
          "model_calls": 1,
          "tool_steps": 0,
          "latency_seconds": null,
          "error_kind": "invalid_api_key"
        }
      }
    },
    {
      "task_id": "pair-02-right",
      "mode": "single_agent",
      "task": "Locate async symbol classification.",
      "latency_seconds": null,
      "input_tokens": null,
      "cached_input_tokens": null,
      "output_tokens": null,
      "total_tokens": null,
      "task_passed": false,
      "status": "failed",
      "agents": {
        "orchestrator": {
          "input_tokens": 0,
          "cached_input_tokens": 0,
          "output_tokens": 0,
          "total_tokens": 0,
          "usage_status": "not_an_llm_agent",
          "usage_record_count": 0,
          "model_calls": 0
        },
        "research": {
          "input_tokens": 0,
          "cached_input_tokens": 0,
          "output_tokens": 0,
          "total_tokens": 0,
          "usage_status": "not_applicable",
          "usage_record_count": 0,
          "model_calls": 0
        },
        "implement": {
          "input_tokens": 0,
          "cached_input_tokens": 0,
          "output_tokens": 0,
          "total_tokens": 0,
          "usage_status": "not_applicable",
          "usage_record_count": 0,
          "model_calls": 0
        },
        "review": {
          "input_tokens": 0,
          "cached_input_tokens": 0,
          "output_tokens": 0,
          "total_tokens": 0,
          "usage_status": "not_applicable",
          "usage_record_count": 0,
          "model_calls": 0
        },
        "single_agent": {
          "input_tokens": null,
          "cached_input_tokens": null,
          "output_tokens": null,
          "total_tokens": null,
          "usage_status": "unavailable",
          "usage_record_count": 0,
          "status": "failed",
          "model_calls": 1,
          "tool_steps": 0,
          "latency_seconds": null,
          "error_kind": "invalid_api_key"
        }
      }
    },
    {
      "task_id": "pair-03-left",
      "mode": "single_agent",
      "task": "Locate usage aggregation cache accounting.",
      "latency_seconds": null,
      "input_tokens": null,
      "cached_input_tokens": null,
      "output_tokens": null,
      "total_tokens": null,
      "task_passed": false,
      "status": "failed",
      "agents": {
        "orchestrator": {
          "input_tokens": 0,
          "cached_input_tokens": 0,
          "output_tokens": 0,
          "total_tokens": 0,
          "usage_status": "not_an_llm_agent",
          "usage_record_count": 0,
          "model_calls": 0
        },
        "research": {
          "input_tokens": 0,
          "cached_input_tokens": 0,
          "output_tokens": 0,
          "total_tokens": 0,
          "usage_status": "not_applicable",
          "usage_record_count": 0,
          "model_calls": 0
        },
        "implement": {
          "input_tokens": 0,
          "cached_input_tokens": 0,
          "output_tokens": 0,
          "total_tokens": 0,
          "usage_status": "not_applicable",
          "usage_record_count": 0,
          "model_calls": 0
        },
        "review": {
          "input_tokens": 0,
          "cached_input_tokens": 0,
          "output_tokens": 0,
          "total_tokens": 0,
          "usage_status": "not_applicable",
          "usage_record_count": 0,
          "model_calls": 0
        },
        "single_agent": {
          "input_tokens": null,
          "cached_input_tokens": null,
          "output_tokens": null,
          "total_tokens": null,
          "usage_status": "unavailable",
          "usage_record_count": 0,
          "status": "failed",
          "model_calls": 1,
          "tool_steps": 0,
          "latency_seconds": null,
          "error_kind": "invalid_api_key"
        }
      }
    },
    {
      "task_id": "pair-03-right",
      "mode": "single_agent",
      "task": "Locate usage persistence boundaries.",
      "latency_seconds": null,
      "input_tokens": null,
      "cached_input_tokens": null,
      "output_tokens": null,
      "total_tokens": null,
      "task_passed": false,
      "status": "failed",
      "agents": {
        "orchestrator": {
          "input_tokens": 0,
          "cached_input_tokens": 0,
          "output_tokens": 0,
          "total_tokens": 0,
          "usage_status": "not_an_llm_agent",
          "usage_record_count": 0,
          "model_calls": 0
        },
        "research": {
          "input_tokens": 0,
          "cached_input_tokens": 0,
          "output_tokens": 0,
          "total_tokens": 0,
          "usage_status": "not_applicable",
          "usage_record_count": 0,
          "model_calls": 0
        },
        "implement": {
          "input_tokens": 0,
          "cached_input_tokens": 0,
          "output_tokens": 0,
          "total_tokens": 0,
          "usage_status": "not_applicable",
          "usage_record_count": 0,
          "model_calls": 0
        },
        "review": {
          "input_tokens": 0,
          "cached_input_tokens": 0,
          "output_tokens": 0,
          "total_tokens": 0,
          "usage_status": "not_applicable",
          "usage_record_count": 0,
          "model_calls": 0
        },
        "single_agent": {
          "input_tokens": null,
          "cached_input_tokens": null,
          "output_tokens": null,
          "total_tokens": null,
          "usage_status": "unavailable",
          "usage_record_count": 0,
          "status": "failed",
          "model_calls": 1,
          "tool_steps": 0,
          "latency_seconds": null,
          "error_kind": "invalid_api_key"
        }
      }
    },
    {
      "task_id": "pair-04-left",
      "mode": "single_agent",
      "task": "Locate workspace ignore rules.",
      "latency_seconds": null,
      "input_tokens": null,
      "cached_input_tokens": null,
      "output_tokens": null,
      "total_tokens": null,
      "task_passed": false,
      "status": "failed",
      "agents": {
        "orchestrator": {
          "input_tokens": 0,
          "cached_input_tokens": 0,
          "output_tokens": 0,
          "total_tokens": 0,
          "usage_status": "not_an_llm_agent",
          "usage_record_count": 0,
          "model_calls": 0
        },
        "research": {
          "input_tokens": 0,
          "cached_input_tokens": 0,
          "output_tokens": 0,
          "total_tokens": 0,
          "usage_status": "not_applicable",
          "usage_record_count": 0,
          "model_calls": 0
        },
        "implement": {
          "input_tokens": 0,
          "cached_input_tokens": 0,
          "output_tokens": 0,
          "total_tokens": 0,
          "usage_status": "not_applicable",
          "usage_record_count": 0,
          "model_calls": 0
        },
        "review": {
          "input_tokens": 0,
          "cached_input_tokens": 0,
          "output_tokens": 0,
          "total_tokens": 0,
          "usage_status": "not_applicable",
          "usage_record_count": 0,
          "model_calls": 0
        },
        "single_agent": {
          "input_tokens": null,
          "cached_input_tokens": null,
          "output_tokens": null,
          "total_tokens": null,
          "usage_status": "unavailable",
          "usage_record_count": 0,
          "status": "failed",
          "model_calls": 1,
          "tool_steps": 0,
          "latency_seconds": null,
          "error_kind": "invalid_api_key"
        }
      }
    },
    {
      "task_id": "pair-04-right",
      "mode": "single_agent",
      "task": "Locate canonical path normalization.",
      "latency_seconds": null,
      "input_tokens": null,
      "cached_input_tokens": null,
      "output_tokens": null,
      "total_tokens": null,
      "task_passed": false,
      "status": "failed",
      "agents": {
        "orchestrator": {
          "input_tokens": 0,
          "cached_input_tokens": 0,
          "output_tokens": 0,
          "total_tokens": 0,
          "usage_status": "not_an_llm_agent",
          "usage_record_count": 0,
          "model_calls": 0
        },
        "research": {
          "input_tokens": 0,
          "cached_input_tokens": 0,
          "output_tokens": 0,
          "total_tokens": 0,
          "usage_status": "not_applicable",
          "usage_record_count": 0,
          "model_calls": 0
        },
        "implement": {
          "input_tokens": 0,
          "cached_input_tokens": 0,
          "output_tokens": 0,
          "total_tokens": 0,
          "usage_status": "not_applicable",
          "usage_record_count": 0,
          "model_calls": 0
        },
        "review": {
          "input_tokens": 0,
          "cached_input_tokens": 0,
          "output_tokens": 0,
          "total_tokens": 0,
          "usage_status": "not_applicable",
          "usage_record_count": 0,
          "model_calls": 0
        },
        "single_agent": {
          "input_tokens": null,
          "cached_input_tokens": null,
          "output_tokens": null,
          "total_tokens": null,
          "usage_status": "unavailable",
          "usage_record_count": 0,
          "status": "failed",
          "model_calls": 1,
          "tool_steps": 0,
          "latency_seconds": null,
          "error_kind": "invalid_api_key"
        }
      }
    },
    {
      "task_id": "pair-05-left",
      "mode": "single_agent",
      "task": "Locate app protocol command validation.",
      "latency_seconds": null,
      "input_tokens": null,
      "cached_input_tokens": null,
      "output_tokens": null,
      "total_tokens": null,
      "task_passed": false,
      "status": "failed",
      "agents": {
        "orchestrator": {
          "input_tokens": 0,
          "cached_input_tokens": 0,
          "output_tokens": 0,
          "total_tokens": 0,
          "usage_status": "not_an_llm_agent",
          "usage_record_count": 0,
          "model_calls": 0
        },
        "research": {
          "input_tokens": 0,
          "cached_input_tokens": 0,
          "output_tokens": 0,
          "total_tokens": 0,
          "usage_status": "not_applicable",
          "usage_record_count": 0,
          "model_calls": 0
        },
        "implement": {
          "input_tokens": 0,
          "cached_input_tokens": 0,
          "output_tokens": 0,
          "total_tokens": 0,
          "usage_status": "not_applicable",
          "usage_record_count": 0,
          "model_calls": 0
        },
        "review": {
          "input_tokens": 0,
          "cached_input_tokens": 0,
          "output_tokens": 0,
          "total_tokens": 0,
          "usage_status": "not_applicable",
          "usage_record_count": 0,
          "model_calls": 0
        },
        "single_agent": {
          "input_tokens": null,
          "cached_input_tokens": null,
          "output_tokens": null,
          "total_tokens": null,
          "usage_status": "unavailable",
          "usage_record_count": 0,
          "status": "failed",
          "model_calls": 1,
          "tool_steps": 0,
          "latency_seconds": null,
          "error_kind": "invalid_api_key"
        }
      }
    },
    {
      "task_id": "pair-05-right",
      "mode": "single_agent",
      "task": "Locate app event serialization.",
      "latency_seconds": null,
      "input_tokens": null,
      "cached_input_tokens": null,
      "output_tokens": null,
      "total_tokens": null,
      "task_passed": false,
      "status": "failed",
      "agents": {
        "orchestrator": {
          "input_tokens": 0,
          "cached_input_tokens": 0,
          "output_tokens": 0,
          "total_tokens": 0,
          "usage_status": "not_an_llm_agent",
          "usage_record_count": 0,
          "model_calls": 0
        },
        "research": {
          "input_tokens": 0,
          "cached_input_tokens": 0,
          "output_tokens": 0,
          "total_tokens": 0,
          "usage_status": "not_applicable",
          "usage_record_count": 0,
          "model_calls": 0
        },
        "implement": {
          "input_tokens": 0,
          "cached_input_tokens": 0,
          "output_tokens": 0,
          "total_tokens": 0,
          "usage_status": "not_applicable",
          "usage_record_count": 0,
          "model_calls": 0
        },
        "review": {
          "input_tokens": 0,
          "cached_input_tokens": 0,
          "output_tokens": 0,
          "total_tokens": 0,
          "usage_status": "not_applicable",
          "usage_record_count": 0,
          "model_calls": 0
        },
        "single_agent": {
          "input_tokens": null,
          "cached_input_tokens": null,
          "output_tokens": null,
          "total_tokens": null,
          "usage_status": "unavailable",
          "usage_record_count": 0,
          "status": "failed",
          "model_calls": 1,
          "tool_steps": 0,
          "latency_seconds": null,
          "error_kind": "invalid_api_key"
        }
      }
    }
  ],
  "multi_agent": [
    {
      "task_id": "pair-01-left",
      "mode": "multi_agent",
      "task": "Locate token-budget validation.",
      "latency_seconds": null,
      "input_tokens": null,
      "cached_input_tokens": null,
      "output_tokens": null,
      "total_tokens": null,
      "task_passed": false,
      "status": "failed",
      "agents": {
        "orchestrator": {
          "input_tokens": 0,
          "cached_input_tokens": 0,
          "output_tokens": 0,
          "total_tokens": 0,
          "usage_status": "not_an_llm_agent",
          "usage_record_count": 0,
          "model_calls": 0
        },
        "research": {
          "input_tokens": null,
          "cached_input_tokens": null,
          "output_tokens": null,
          "total_tokens": null,
          "usage_status": "unavailable",
          "usage_record_count": 0,
          "status": "failed",
          "model_calls": 1,
          "tool_steps": 0,
          "latency_seconds": null,
          "error_kind": "invalid_api_key"
        },
        "implement": {
          "input_tokens": 0,
          "cached_input_tokens": 0,
          "output_tokens": 0,
          "total_tokens": 0,
          "usage_status": "not_invoked",
          "usage_record_count": 0,
          "model_calls": 0
        },
        "review": {
          "input_tokens": 0,
          "cached_input_tokens": 0,
          "output_tokens": 0,
          "total_tokens": 0,
          "usage_status": "not_invoked",
          "usage_record_count": 0,
          "model_calls": 0
        }
      }
    },
    {
      "task_id": "pair-01-right",
      "mode": "multi_agent",
      "task": "Locate context-window capability validation.",
      "latency_seconds": null,
      "input_tokens": null,
      "cached_input_tokens": null,
      "output_tokens": null,
      "total_tokens": null,
      "task_passed": false,
      "status": "failed",
      "agents": {
        "orchestrator": {
          "input_tokens": 0,
          "cached_input_tokens": 0,
          "output_tokens": 0,
          "total_tokens": 0,
          "usage_status": "not_an_llm_agent",
          "usage_record_count": 0,
          "model_calls": 0
        },
        "research": {
          "input_tokens": null,
          "cached_input_tokens": null,
          "output_tokens": null,
          "total_tokens": null,
          "usage_status": "unavailable",
          "usage_record_count": 0,
          "status": "failed",
          "model_calls": 1,
          "tool_steps": 0,
          "latency_seconds": null,
          "error_kind": "invalid_api_key"
        },
        "implement": {
          "input_tokens": 0,
          "cached_input_tokens": 0,
          "output_tokens": 0,
          "total_tokens": 0,
          "usage_status": "not_invoked",
          "usage_record_count": 0,
          "model_calls": 0
        },
        "review": {
          "input_tokens": 0,
          "cached_input_tokens": 0,
          "output_tokens": 0,
          "total_tokens": 0,
          "usage_status": "not_invoked",
          "usage_record_count": 0,
          "model_calls": 0
        }
      }
    },
    {
      "task_id": "pair-02-left",
      "mode": "multi_agent",
      "task": "Locate symbol-index persistence.",
      "latency_seconds": null,
      "input_tokens": null,
      "cached_input_tokens": null,
      "output_tokens": null,
      "total_tokens": null,
      "task_passed": false,
      "status": "failed",
      "agents": {
        "orchestrator": {
          "input_tokens": 0,
          "cached_input_tokens": 0,
          "output_tokens": 0,
          "total_tokens": 0,
          "usage_status": "not_an_llm_agent",
          "usage_record_count": 0,
          "model_calls": 0
        },
        "research": {
          "input_tokens": null,
          "cached_input_tokens": null,
          "output_tokens": null,
          "total_tokens": null,
          "usage_status": "unavailable",
          "usage_record_count": 0,
          "status": "failed",
          "model_calls": 1,
          "tool_steps": 0,
          "latency_seconds": null,
          "error_kind": "invalid_api_key"
        },
        "implement": {
          "input_tokens": 0,
          "cached_input_tokens": 0,
          "output_tokens": 0,
          "total_tokens": 0,
          "usage_status": "not_invoked",
          "usage_record_count": 0,
          "model_calls": 0
        },
        "review": {
          "input_tokens": 0,
          "cached_input_tokens": 0,
          "output_tokens": 0,
          "total_tokens": 0,
          "usage_status": "not_invoked",
          "usage_record_count": 0,
          "model_calls": 0
        }
      }
    },
    {
      "task_id": "pair-02-right",
      "mode": "multi_agent",
      "task": "Locate async symbol classification.",
      "latency_seconds": null,
      "input_tokens": null,
      "cached_input_tokens": null,
      "output_tokens": null,
      "total_tokens": null,
      "task_passed": false,
      "status": "failed",
      "agents": {
        "orchestrator": {
          "input_tokens": 0,
          "cached_input_tokens": 0,
          "output_tokens": 0,
          "total_tokens": 0,
          "usage_status": "not_an_llm_agent",
          "usage_record_count": 0,
          "model_calls": 0
        },
        "research": {
          "input_tokens": null,
          "cached_input_tokens": null,
          "output_tokens": null,
          "total_tokens": null,
          "usage_status": "unavailable",
          "usage_record_count": 0,
          "status": "failed",
          "model_calls": 1,
          "tool_steps": 0,
          "latency_seconds": null,
          "error_kind": "invalid_api_key"
        },
        "implement": {
          "input_tokens": 0,
          "cached_input_tokens": 0,
          "output_tokens": 0,
          "total_tokens": 0,
          "usage_status": "not_invoked",
          "usage_record_count": 0,
          "model_calls": 0
        },
        "review": {
          "input_tokens": 0,
          "cached_input_tokens": 0,
          "output_tokens": 0,
          "total_tokens": 0,
          "usage_status": "not_invoked",
          "usage_record_count": 0,
          "model_calls": 0
        }
      }
    },
    {
      "task_id": "pair-03-left",
      "mode": "multi_agent",
      "task": "Locate usage aggregation cache accounting.",
      "latency_seconds": null,
      "input_tokens": null,
      "cached_input_tokens": null,
      "output_tokens": null,
      "total_tokens": null,
      "task_passed": false,
      "status": "failed",
      "agents": {
        "orchestrator": {
          "input_tokens": 0,
          "cached_input_tokens": 0,
          "output_tokens": 0,
          "total_tokens": 0,
          "usage_status": "not_an_llm_agent",
          "usage_record_count": 0,
          "model_calls": 0
        },
        "research": {
          "input_tokens": null,
          "cached_input_tokens": null,
          "output_tokens": null,
          "total_tokens": null,
          "usage_status": "unavailable",
          "usage_record_count": 0,
          "status": "failed",
          "model_calls": 1,
          "tool_steps": 0,
          "latency_seconds": null,
          "error_kind": "invalid_api_key"
        },
        "implement": {
          "input_tokens": 0,
          "cached_input_tokens": 0,
          "output_tokens": 0,
          "total_tokens": 0,
          "usage_status": "not_invoked",
          "usage_record_count": 0,
          "model_calls": 0
        },
        "review": {
          "input_tokens": 0,
          "cached_input_tokens": 0,
          "output_tokens": 0,
          "total_tokens": 0,
          "usage_status": "not_invoked",
          "usage_record_count": 0,
          "model_calls": 0
        }
      }
    },
    {
      "task_id": "pair-03-right",
      "mode": "multi_agent",
      "task": "Locate usage persistence boundaries.",
      "latency_seconds": null,
      "input_tokens": null,
      "cached_input_tokens": null,
      "output_tokens": null,
      "total_tokens": null,
      "task_passed": false,
      "status": "failed",
      "agents": {
        "orchestrator": {
          "input_tokens": 0,
          "cached_input_tokens": 0,
          "output_tokens": 0,
          "total_tokens": 0,
          "usage_status": "not_an_llm_agent",
          "usage_record_count": 0,
          "model_calls": 0
        },
        "research": {
          "input_tokens": null,
          "cached_input_tokens": null,
          "output_tokens": null,
          "total_tokens": null,
          "usage_status": "unavailable",
          "usage_record_count": 0,
          "status": "failed",
          "model_calls": 1,
          "tool_steps": 0,
          "latency_seconds": null,
          "error_kind": "invalid_api_key"
        },
        "implement": {
          "input_tokens": 0,
          "cached_input_tokens": 0,
          "output_tokens": 0,
          "total_tokens": 0,
          "usage_status": "not_invoked",
          "usage_record_count": 0,
          "model_calls": 0
        },
        "review": {
          "input_tokens": 0,
          "cached_input_tokens": 0,
          "output_tokens": 0,
          "total_tokens": 0,
          "usage_status": "not_invoked",
          "usage_record_count": 0,
          "model_calls": 0
        }
      }
    },
    {
      "task_id": "pair-04-left",
      "mode": "multi_agent",
      "task": "Locate workspace ignore rules.",
      "latency_seconds": null,
      "input_tokens": null,
      "cached_input_tokens": null,
      "output_tokens": null,
      "total_tokens": null,
      "task_passed": false,
      "status": "failed",
      "agents": {
        "orchestrator": {
          "input_tokens": 0,
          "cached_input_tokens": 0,
          "output_tokens": 0,
          "total_tokens": 0,
          "usage_status": "not_an_llm_agent",
          "usage_record_count": 0,
          "model_calls": 0
        },
        "research": {
          "input_tokens": null,
          "cached_input_tokens": null,
          "output_tokens": null,
          "total_tokens": null,
          "usage_status": "unavailable",
          "usage_record_count": 0,
          "status": "failed",
          "model_calls": 1,
          "tool_steps": 0,
          "latency_seconds": null,
          "error_kind": "invalid_api_key"
        },
        "implement": {
          "input_tokens": 0,
          "cached_input_tokens": 0,
          "output_tokens": 0,
          "total_tokens": 0,
          "usage_status": "not_invoked",
          "usage_record_count": 0,
          "model_calls": 0
        },
        "review": {
          "input_tokens": 0,
          "cached_input_tokens": 0,
          "output_tokens": 0,
          "total_tokens": 0,
          "usage_status": "not_invoked",
          "usage_record_count": 0,
          "model_calls": 0
        }
      }
    },
    {
      "task_id": "pair-04-right",
      "mode": "multi_agent",
      "task": "Locate canonical path normalization.",
      "latency_seconds": null,
      "input_tokens": null,
      "cached_input_tokens": null,
      "output_tokens": null,
      "total_tokens": null,
      "task_passed": false,
      "status": "failed",
      "agents": {
        "orchestrator": {
          "input_tokens": 0,
          "cached_input_tokens": 0,
          "output_tokens": 0,
          "total_tokens": 0,
          "usage_status": "not_an_llm_agent",
          "usage_record_count": 0,
          "model_calls": 0
        },
        "research": {
          "input_tokens": null,
          "cached_input_tokens": null,
          "output_tokens": null,
          "total_tokens": null,
          "usage_status": "unavailable",
          "usage_record_count": 0,
          "status": "failed",
          "model_calls": 1,
          "tool_steps": 0,
          "latency_seconds": null,
          "error_kind": "invalid_api_key"
        },
        "implement": {
          "input_tokens": 0,
          "cached_input_tokens": 0,
          "output_tokens": 0,
          "total_tokens": 0,
          "usage_status": "not_invoked",
          "usage_record_count": 0,
          "model_calls": 0
        },
        "review": {
          "input_tokens": 0,
          "cached_input_tokens": 0,
          "output_tokens": 0,
          "total_tokens": 0,
          "usage_status": "not_invoked",
          "usage_record_count": 0,
          "model_calls": 0
        }
      }
    },
    {
      "task_id": "pair-05-left",
      "mode": "multi_agent",
      "task": "Locate app protocol command validation.",
      "latency_seconds": null,
      "input_tokens": null,
      "cached_input_tokens": null,
      "output_tokens": null,
      "total_tokens": null,
      "task_passed": false,
      "status": "failed",
      "agents": {
        "orchestrator": {
          "input_tokens": 0,
          "cached_input_tokens": 0,
          "output_tokens": 0,
          "total_tokens": 0,
          "usage_status": "not_an_llm_agent",
          "usage_record_count": 0,
          "model_calls": 0
        },
        "research": {
          "input_tokens": null,
          "cached_input_tokens": null,
          "output_tokens": null,
          "total_tokens": null,
          "usage_status": "unavailable",
          "usage_record_count": 0,
          "status": "failed",
          "model_calls": 1,
          "tool_steps": 0,
          "latency_seconds": null,
          "error_kind": "invalid_api_key"
        },
        "implement": {
          "input_tokens": 0,
          "cached_input_tokens": 0,
          "output_tokens": 0,
          "total_tokens": 0,
          "usage_status": "not_invoked",
          "usage_record_count": 0,
          "model_calls": 0
        },
        "review": {
          "input_tokens": 0,
          "cached_input_tokens": 0,
          "output_tokens": 0,
          "total_tokens": 0,
          "usage_status": "not_invoked",
          "usage_record_count": 0,
          "model_calls": 0
        }
      }
    },
    {
      "task_id": "pair-05-right",
      "mode": "multi_agent",
      "task": "Locate app event serialization.",
      "latency_seconds": null,
      "input_tokens": null,
      "cached_input_tokens": null,
      "output_tokens": null,
      "total_tokens": null,
      "task_passed": false,
      "status": "failed",
      "agents": {
        "orchestrator": {
          "input_tokens": 0,
          "cached_input_tokens": 0,
          "output_tokens": 0,
          "total_tokens": 0,
          "usage_status": "not_an_llm_agent",
          "usage_record_count": 0,
          "model_calls": 0
        },
        "research": {
          "input_tokens": null,
          "cached_input_tokens": null,
          "output_tokens": null,
          "total_tokens": null,
          "usage_status": "unavailable",
          "usage_record_count": 0,
          "status": "failed",
          "model_calls": 1,
          "tool_steps": 0,
          "latency_seconds": null,
          "error_kind": "invalid_api_key"
        },
        "implement": {
          "input_tokens": 0,
          "cached_input_tokens": 0,
          "output_tokens": 0,
          "total_tokens": 0,
          "usage_status": "not_invoked",
          "usage_record_count": 0,
          "model_calls": 0
        },
        "review": {
          "input_tokens": 0,
          "cached_input_tokens": 0,
          "output_tokens": 0,
          "total_tokens": 0,
          "usage_status": "not_invoked",
          "usage_record_count": 0,
          "model_calls": 0
        }
      }
    }
  ]
}
```

## Where the extra tokens can come from

The current measured path provides evidence for one structural source: every Research dispatch constructs a fresh leaf `Pico` with its own role instructions and context assembly. The five pairs also issue independent repository lookups, so overlapping files can be read by separate children. These are the two directly observable sources in this harness.

The following requested sources are not measurable from this run because Implement, Review, and a Parent LLM call are absent: long Research handoffs, Review reloading unrelated context, Parent retaining child conversations, and tool-result duplication across those stages. Prompt-cache impact is likewise limited to the Provider Usage field reported above.

## Current Token waste Top 3

1. Fresh Research child context and system/role instructions for every task.
2. Overlapping repository retrieval performed independently by the two Research children.
3. The current benchmark does not exercise handoff boundaries, so any future R → I → Review run should first measure duplicated handoff and review context before optimizing it.

These are measurement-informed structural hypotheses; this run does not assign token quantities to them.

## Next experiment

First repair the benchmark mapping while preserving the same five task pairs: run a genuinely single-agent full task and the current production Multi-Agent Research → Implement → Review path with identical model settings and isolated initial workspaces. After that measurement is valid, a Context Handoff / Context Pruning ablation is worthwhile because fresh child context and repeated retrieval are visible cost candidates. It should remain a separate experiment after the baseline measurement.

## Historical latency reference

历史 artifact 记录 36452ms → 26547ms，speedup=27.17%；该文件只统计调用次数和墙钟时间，没有 Provider Token Usage，不能替代本轮数据。

## Invalidity reasons

- one or more Provider Usage records are incomplete
- one or more tasks failed during the attempted run
- existing five-pair benchmark invokes Research only
- Single-Agent arm is not a full Research -> Implement -> Review task
- Parent/Orchestrator has no independent LLM call in this harness
