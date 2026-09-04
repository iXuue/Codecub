# Pico architecture coverage closure

审计日期：2026-09-04。本文是当前工作树的能力账本，不是 Pico 源码的复制清单。

## 固定上游与审计边界

审计对象固定为 [Pico Harness](https://gitee.com/htxoffical/pico-harness) 的 `main`：

- `main`：`d6c7a648fd7ee63e472c0d438f1c8299d9b8f871`
- 发布标签：`v0.1.7`，peeled commit `c2524dbf507b2dc31ba8eb24790444bfb9acb563`
- 标签对象：`762a8e3c1b67cb63266f17c90f16b532e7a0ec26`
- License：Apache-2.0
- 上游树：1336 entries，1191 blobs，145 directories

原始证据入口：[Pico repository](https://gitee.com/htxoffical/pico-harness)、[gateway spine](https://gitee.com/htxoffical/pico-harness/raw/d6c7a648fd7ee63e472c0d438f1c8299d9b8f871/pico/cli/_gateway_spine.py)、[MCP tool](https://gitee.com/htxoffical/pico-harness/raw/d6c7a648fd7ee63e472c0d438f1c8299d9b8f871/pico/agent/tools/mcp.py)、[cron service](https://gitee.com/htxoffical/pico-harness/raw/d6c7a648fd7ee63e472c0d438f1c8299d9b8f871/pico/proactive_engine/schedulers/cron/service.py)、[channel contract](https://gitee.com/htxoffical/pico-harness/raw/d6c7a648fd7ee63e472c0d438f1c8299d9b8f871/pico/channels/contract.py)、[capability token](https://gitee.com/htxoffical/pico-harness/raw/d6c7a648fd7ee63e472c0d438f1c8299d9b8f871/pico/auth/capability_token.py)。

### 上游 package inventory

以下是对 Pico `pico/` package 的完整目录级盘点；数字是该 package 在固定 commit 中的 tree file count，`__init__`/入口文件已单独计入所在 package 或 root。每个 package 均在后续能力矩阵中有对应判定。

| Package | Files | Package | Files | Package | Files |
|---|---:|---|---:|---|---:|
| agent | 30 | auth | 4 | call_efficiency | 9 |
| channels | 23 | cli | 33 | config | 8 |
| context_engine | 14 | eval_engine | 14 | evolver | 75 |
| memory_engine | 21 | plugin | 7 | proactive_engine | 6 |
| providers | 12 | routing | 10 | sandbox | 8 |
| security | 3 | session | 3 | spine | 9 |
| templates | 2 | token_wise | 8 | tracing | 8 |
| tui_rpc | 20 | utils | 6 | root package files | 3 |

仓库其余 top-level tree 也已计入审计：`benchmarks=179`、`docs=12`、`tests=269`、`ui-tui=337`、`scripts=14`，以及构建/许可证/配置入口文件。测试和 UI 的产品表面不被误当作 Runtime 核心；它们分别由 quality gate 和 surface 章节裁定。

## 判定标签

每一行都必须且只使用下列标签之一：

- `ALREADY_EQUIVALENT`：Codecub 已有同等生产语义。
- `KEEP_CODECUB`：Codecub 现有实现是本仓库的权威实现，不复制 Pico。
- `MERGED`：已吸收到现有边界并由同一生产路径承载。
- `PORTED`：已新增可执行的对应能力。
- `ADAPT`：保留能力目标，但按 Codecub 的同步/本地/安全边界改造。
- `REPLACE_WITH_CODECUB`：Pico 的实现由 Codecub 已有实现替代。
- `REJECT_WITH_REASON`：明确不纳入核心，理由写在同一行。

没有未分类行、隐式待办行或依赖外部条件才能成立的核心能力行。外部平台 live E2E 只影响验证范围，不影响本地 mock/contract 路径。

### Coverage counters

本矩阵共有 `TOTAL_PICO_CAPABILITIES=108` 行：

```text
ALREADY_EQUIVALENT=6
KEEP_CODECUB=18
MERGED=25
PORTED=33
PORT_PARTIAL=0
ADAPT=12
REPLACE_WITH_CODECUB=3
REJECT_WITH_REASON=11
DEFER_WITH_DEPENDENCY=0
UNREVIEWED=0
UNKNOWN=0
```

计数按矩阵中的 `Decision` 列生成，而不是按源码文件数量推断；`REJECT_WITH_REASON` 是技术裁定，不是漏项。

## 逐域能力矩阵

### 1. Agent、turn 与 Runtime 组合根

| Pico 能力/证据 | Codecub 落点 | 判定 | 验证与边界 |
|---|---|---|---|
| `pico/agent/loop.py` AgentLoop | `codecub/agent/loop.py` + `Pico` | `KEEP_CODECUB` | 现有 loop、上下文、工具、记忆路径权威。 |
| turn preparation / finalization | `TurnRunner`、`Pico.ask()` | `ALREADY_EQUIVALENT` | 现有运行工件和状态机已覆盖。 |
| subagent bounded manager | `codecub/orchestration.py` | `MERGED` | Research/Implement/Review、角色工具过滤、父取消已由现有实现承载。 |
| runtime composition | `codecub/runtime.py` | `KEEP_CODECUB` | Codecub composition root 不由 Gateway 重建。 |
| runtime-owned gateway adapter | `codecub/gateway_runtime.py` | `PORTED` | session/run/interaction/subscribe 使用现有 Spine + TurnRunner。 |
| CLI Gateway entrypoint | `codecub/cli.py` | `PORTED` | loopback 默认、显式 token 或显式 unauthenticated 开关。 |
| Pico gateway process lock | Codecub Gateway listener lifecycle | `ADAPT` | 单进程本地 listener；不复制 Pico 的产品启动锁语义。 |

### 2. Spine、调度与执行所有权

| Pico 能力/证据 | Codecub 落点 | 判定 | 验证与边界 |
|---|---|---|---|
| request/run/turn contracts | `codecub/spine/contracts.py` | `MERGED` | `Origin`、`BusyPolicy`、`RunStatus`、`TurnRequest` 已存在。 |
| conversation lane serialization | `codecub/spine/lane.py` | `KEEP_CODECUB` | APPEND/INJECT/INTERRUPT 与取消已有测试。 |
| user/system resource pools | `codecub/spine/resource_pool.py` | `ALREADY_EQUIVALENT` | lane dispatch 使用有界线程池。 |
| scheduler cancellation/drain | `codecub/spine/spine.py` | `ADAPT` | 本地同步 host 采用现有 lane cancellation；不宣称分布式 worker 语义。 |
| delivery queue/retry/backpressure | `codecub/spine/delivery.py` | `KEEP_CODECUB` | per-channel bounded queue、serial worker、retry、terminal failure 已实测。 |
| origin policy | `Spine._applied_policy` | `MERGED` | 非 USER 的 INJECT/INTERRUPT 被降级为 APPEND。 |

### 3. Gateway、RPC 与事件流

| Pico 能力/证据 | Codecub 落点 | 判定 | 验证与边界 |
|---|---|---|---|
| local newline JSON-RPC framing | `codecub/gateway.py` | `PORTED` | 1 MiB frame cap、并发 dispatch、serialized writer。 |
| auth handshake | `gateway.auth` + `codecub/auth.py` | `PORTED` | static token、HMAC signed token、fake provider 均有测试。 |
| bounded outbound event queue | `_Connection.outbound` | `PORTED` | queue 满关闭连接，不让生产 worker 无限阻塞。 |
| session create/resume/close | `EmbeddedRuntimeGateway` | `PORTED` | 通过正常 CLI factory 构造/恢复 Pico。 |
| run start/cancel/inject/interrupt | `EmbeddedRuntimeGateway` | `PORTED` | request correlation 保留 run_id 与 identity。 |
| interaction resolve | `ApprovalBroker` adapter | `MERGED` | Gateway 只转发 interaction，不执行审批决策。 |
| health/capabilities | `GatewayServer` | `PORTED` | 能力清单来自 runtime adapter，避免第二套 runtime。 |
| TUI 专属 terminal/image/config/model RPC | Gateway generic methods + Desktop JSONL | `ADAPT` | 保留 transport-neutral 核心；UI 专属方法不复制为后端状态机。 |

### 4. Channels、intake、outlet 与 media

| Pico 能力/证据 | Codecub 落点 | 判定 | 验证与边界 |
|---|---|---|---|
| inbound message contract | `codecub/channels.py::InboundMessage` | `PORTED` | channel/conversation/text/source metadata 有显式字段。 |
| outbound message contract | `codecub/channels.py::OutboundMessage` | `PORTED` | reply_to/idempotency/metadata 保留。 |
| channel registry/manager | `ChannelRegistry` | `PORTED` | adapter register/start/stop 与 snapshot。 |
| bounded outlet delivery | `ChannelRegistry` + `DeliveryHub` | `MERGED` | 出站不绕过已有有界投递、重试和失败证据。 |
| inbound to Spine turn | `ChannelRegistry.ingest` | `PORTED` | 生成 `Source(channel=...)` 的 `TurnRequest`。 |
| media attachments/transcription | 无核心实现 | `REJECT_WITH_REASON` | 当前产品请求是本地 coding runtime；未引入媒体 SDK、转码器或凭据面。 |
| Feishu/QQ/WeCom live adapters | generic adapter contract + loopback | `REJECT_WITH_REASON` | 平台 SDK、应用凭据、外部 webhook/WebSocket E2E 不属于本地核心；可由插件实现。 |

### 5. Proactive engine 与 Cron

| Pico 能力/证据 | Codecub 落点 | 判定 | 验证与边界 |
|---|---|---|---|
| persistent at jobs | `codecub/automation.py::CronStore` | `PORTED` | atomic JSON store、one-shot disable。 |
| every duration jobs | `AutomationScheduler` | `PORTED` | seconds/minutes/hours/days，due claim 后推进 next_run。 |
| five-field cron jobs | `cron_next` | `PORTED` | wildcard/list/range/step，UTC minute resolution。 |
| scheduler execution callback | `AutomationScheduler` | `MERGED` | 只注入 `TurnRequest(origin=CRON)`，由宿主提交给 Spine。 |
| Gateway cron control plane | `cron.create/list/cancel` | `PORTED` | 认证后按 session 持久化和管理。 |
| distributed claim TTL / multi-host leader election | 无分布式 backend | `REJECT_WITH_REASON` | Gateway 是单机本地运行时；不虚构跨主机 exactly-once。 |

### 6. Tools、MCP 与 tool search

| Pico 能力/证据 | Codecub 落点 | 判定 | 验证与边界 |
|---|---|---|---|
| mutable ToolRegistry | `codecub/tooling/registry.py` | `MERGED` | mapping-compatible live registry。 |
| effect/concurrency/idempotency metadata | `ToolCapability` | `MERGED` | read/write/execute/external 元数据进入执行和观测。 |
| governed tool execution | `codecub/tooling/executor.py` | `KEEP_CODECUB` | approval/replay/circuit/workspace/observation 是唯一执行入口。 |
| MCP stdio transport | `codecub/mcp.py` | `PORTED` | no-shell subprocess、newline JSON-RPC、frame cap、timeout。 |
| MCP HTTP/SSE transport | `_HttpTransport` | `PORTED` | URL 校验、SSRF 默认拒绝、response cap、session header。 |
| MCP discovery | `McpClient.discover` | `PORTED` | tools/resources/prompts 基础发现。 |
| MCP namespaced tool bridge | `McpToolBridge` | `PORTED` | `mcp_<server>_<tool>`，external/risky/side_effect。 |
| MCP schema gate | `validate_json_tool_arguments` | `MERGED` | required/type/enum/additionalProperties/array 通过 ToolExecutor 校验。 |
| MCP retry/reconnect | `McpClient.call_tool` | `PORTED` | transport failure 后最多一次 reconnect。 |
| Pico tool search/skill tool | native registry/prompt definitions | `ADAPT` | Codecub 动态 registry 是现有 authority，不复制 Pico 的搜索 UI。 |

### 7. Auth、identity 与 capability

| Pico 能力/证据 | Codecub 落点 | 判定 | 验证与边界 |
|---|---|---|---|
| deny-by-default allowlist | `CapabilityPolicy` | `MERGED` | 未绑定 identity 的 capability 被拒绝。 |
| capability token shape | `Identity`、`SignedAuthToken` | `PORTED` | HMAC-SHA256、expiry、tamper rejection。 |
| auth middleware carrier | `AuthMiddleware` | `PORTED` | bearer normalization 与 provider delegation。 |
| identity propagation | Gateway connection → TurnRequest → Pico | `MERGED` | tool authorization 可观察且不改变模型上下文。 |
| managed settings placeholder | `ManagedPolicy` | `ADAPT` | 作为策略载体保留；本地 token/policy 是可执行实现。 |
| OAuth/OIDC/enterprise tenancy | 无外部 identity provider | `REJECT_WITH_REASON` | 需要部署方 issuer、TLS、密钥轮换和租户规则，不应伪装成本地已完成。 |

### 8. Sandbox、security 与 untrusted text

| Pico 能力/证据 | Codecub 落点 | 判定 | 验证与边界 |
|---|---|---|---|
| path containment | `WorkspaceBoundarySandbox` | `MERGED` | resolve/commonpath/symlink escape 防护。 |
| process approval/env filtering | `tools.run_shell` + approval | `KEEP_CODECUB` | host process 明确受审批和 allowlist 约束。 |
| OS/container isolation | 无默认实现 | `REJECT_WITH_REASON` | 当前 sandbox descriptor 明确 `host_process_isolation=false`；BoxLite/容器需部署依赖和独立威胁模型。 |
| network target validation | `security.validate_url` | `PORTED` | scheme/credential/private DNS target 检查。 |
| trust boundary for external text | `mark_untrusted_text` | `MERGED` | 外部内容不直接成为 instruction。 |
| secret redaction | runtime/trace/report redaction | `KEEP_CODECUB` | API key/token/password shaped values 不进入公开事件。 |
| MCP process shell escape | `_StdioTransport` | `PORTED` | `create_subprocess_exec`，不启用 shell。 |

### 9. Providers、routing 与 model gateway

| Pico 能力/证据 | Codecub 落点 | 判定 | 验证与边界 |
|---|---|---|---|
| provider base contract | `codecub/provider_contract.py` | `MERGED` | capabilities、error classification、streaming contract。 |
| provider registry/lazy selection | `provider_registry.py` | `MERGED` | static catalog、explicit/env/default resolution。 |
| provider config layering | `provider_config.py` | `MERGED` | CLI/env/public diagnostics 无 secret leakage。 |
| model gateway concurrency/retry | `codecub/model_gateway.py` | `KEEP_CODECUB` | semaphore/rate/retry/fallback 已有实现。 |
| Ollama/OpenAI/Anthropic compatible clients | `codecub/models.py` | `REPLACE_WITH_CODECUB` | Codecub client and telemetry contracts are authoritative. |
| provider health/doctor | `provider_health.py` + CLI | `PORTED` | offline default，probe 才触网。 |
| Pico provider onboarding UI | settings/CLI config | `ADAPT` | 保留配置与 doctor，不复制 Pico TUI 产品页面。 |
| provider-specific pricing catalog | existing usage channels | `REJECT_WITH_REASON` | 价格实时变化且不是稳定 runtime contract；usage channel 明确标记 unavailable/unsupported。 |

### 10. Context、memory、token 与 call efficiency

| Pico 能力/证据 | Codecub 落点 | 判定 | 验证与边界 |
|---|---|---|---|
| context engine assembly | `ContextAssembler`/`ContextCompiler` | `MERGED` | budget、history、instructions、retrieval 共用一条路径。 |
| context validation | `ContextValidator` | `KEEP_CODECUB` | evidence-backed validation 已存在。 |
| token-wise budget accounting | `token_budget.py` | `KEEP_CODECUB` | token counter 与 safety margin。 |
| prompt cache semantics | `model_gateway.py`/runtime | `ALREADY_EQUIVALENT` | provider capability 选择性使用。 |
| hybrid code retrieval | `HybridRetriever`/`CodeIndex` | `REPLACE_WITH_CODECUB` | Codecub 检索实现已在生产 prompt path。 |
| evidence store | `memory_v2` | `MERGED` | bounded evidence、freshness、rejection/conflict。 |
| durable memory | `MemoryV2` + session | `MERGED` | promotion/rejection/supersession 有审计字段。 |
| external memory backend protocol | no concrete remote backend | `ADAPT` | 本地 memory remains authority；remote backend is an extension seam. |
| skill forge/consolidation | memory_v2/consolidator | `ADAPT` | 采用 Codecub evidence/persistence model，不复制 Pico storage。 |
| raw prompt compression heuristic | `ContextCompiler` | `REPLACE_WITH_CODECUB` | Codecub state-preserving compression is default. |

### 11. Plugin、Skill 与扩展生命周期

| Pico 能力/证据 | Codecub 落点 | 判定 | 验证与边界 |
|---|---|---|---|
| manifest discovery | `ExtensionRegistry.discover` | `MERGED` | 只读 manifest，发现阶段不 import。 |
| explicit lazy activation | `ExtensionRegistry.activate` | `PORTED` | 未显式 activate 不执行 entrypoint。 |
| dependency ordering/cycle guard | `ExtensionManifest.dependencies` | `PORTED` | 依赖先激活，cycle fail closed。 |
| capability grant | `ExtensionContext.granted_capabilities` | `PORTED` | 缺少显式 grant 时 activation rejected。 |
| activation/tool registration/deactivation | `ExtensionContext` + registry hooks | `PORTED` | activate/register_tools/deactivate/close。 |
| package install/update/signature marketplace | 无分发服务 | `REJECT_WITH_REASON` | 本地仓库不拥有包源、签名根或更新权限；避免伪造供应链安全。 |

### 12. Session、persistence 与 recovery

| Pico 能力/证据 | Codecub 落点 | 判定 | 验证与边界 |
|---|---|---|---|
| session store | `codecub/sessions` | `ALREADY_EQUIVALENT` | session create/resume/latest。 |
| run artifact store | `RunStore` | `KEEP_CODECUB` | task state/trace/report/usage separate persistence。 |
| checkpoint/recovery | runtime checkpoint methods | `KEEP_CODECUB` | schema/workspace mismatch checks。 |
| side-effect ledger | `RunStore.claim_side_effect_operation` | `MERGED` | claim/uncertain/replay block。 |
| remote durable queue | `DurableExecutionBroker` | `ADAPT` | optional Redis stream seam；local default remains deterministic. |
| session transport ownership | Gateway adapter | `ADAPT` | Gateway 不保存第二份 conversation state。 |

### 13. Tracing、telemetry 与 export

| Pico 能力/证据 | Codecub 落点 | 判定 | 验证与边界 |
|---|---|---|---|
| no-op safe trace API | `codecub/tracing` | `KEEP_CODECUB` | 无 exporter 时运行语义不变。 |
| runtime event bus | `LocalEventBus`/spine trace | `MERGED` | 事件由 runtime owner 产生。 |
| usage aggregation | `codecub/telemetry` | `KEEP_CODECUB` | provider-specific usage channels explicit。 |
| redacted OTEL exporter | `codecub/otel_exporter.py` | `PORTED` | optional import，safe attribute allowlist。 |
| trace correlation | trace/turn/run IDs | `ALREADY_EQUIVALENT` | gateway、spine、tool invocation 对齐。 |
| vendor dashboard/remote collector | external deployment | `REJECT_WITH_REASON` | exporter seam 已有；collector availability 不属于仓库闭环。 |

### 14. Evaluation、evolver 与 quality gates

| Pico 能力/证据 | Codecub 落点 | 判定 | 验证与边界 |
|---|---|---|---|
| evaluation task/runner | `codecub/experiments` | `KEEP_CODECUB` | deterministic tasks、baselines、reports 已存在。 |
| final-eval harness | `scripts`/benchmarks | `KEEP_CODECUB` | full regression and baseline evidence owned here. |
| self-evolving prompt/code loop | 无默认自动改写生产 runtime | `REJECT_WITH_REASON` | 安全边界要求人工审查；不把自修改误报为已吸收能力。 |
| quality regression gate | pytest/ruff/diff checks | `MERGED` | 每阶段聚焦测试后执行全量回归。 |

### 15. CLI、installer、doctor 与 config

| Pico 能力/证据 | Codecub 落点 | 判定 | 验证与边界 |
|---|---|---|---|
| CLI run/resume/approval | `codecub/cli.py` | `ALREADY_EQUIVALENT` | 现有 one-shot/interactive/app mode。 |
| provider doctor | `run_doctor` | `PORTED` | offline default，explicit probe。 |
| gateway CLI | `run_gateway` | `PORTED` | exact local transport and auth options。 |
| extension discovery in build_agent | `build_agent` | `MERGED` | 不创建插件目录、不激活代码。 |
| Pico install.ps1/install.sh | repository packaging/uv | `REJECT_WITH_REASON` | 本仓库不在用户未授权路径创建系统安装器或下载依赖。 |
| Pico onboard wizard | settings/config layering | `ADAPT` | 配置入口已存在；交互式 onboarding 不影响 runtime contract。 |

### 16. TUI、Desktop 与 external surface

| Pico 能力/证据 | Codecub 落点 | 判定 | 验证与边界 |
|---|---|---|---|
| TUI RPC session/turn/cancel | Gateway RPC + Desktop app mode | `ADAPT` | surface-neutral backend contract。 |
| TUI confirmation/question | `ApprovalBroker` + `interaction.resolve` | `MERGED` | 同一 interaction owner。 |
| Desktop local JSONL events | `codecub/app_runner.py` | `KEEP_CODECUB` | 既有 Electron contract 不被 Gateway 取代。 |
| Desktop settings/provider profiles | `desktop/src/components/SettingsPage.tsx` | `KEEP_CODECUB` | UI 本地设置路径已有测试。 |
| Pico-specific terminal/image widgets | Desktop-specific implementation | `REJECT_WITH_REASON` | UI 产品细节不扩散进 runtime 核心；必要时由 Desktop surface 自己适配。 |

## 验收口径与当前结论

本轮已完成的 production paths：

1. Gateway TCP JSON-RPC → `EmbeddedRuntimeGateway` → `Spine` → `LegacyTurnRunner` → `Pico`。
2. Gateway auth identity → `TurnRequest.runtime_extensions` → `Pico.bind_identity` → `CapabilityPolicy` → `ToolExecutor`。
3. MCP stdio/HTTP/SSE → live `ToolRegistry` → JSON Schema validation → existing governed executor。
4. Gateway cron store → `Origin.CRON` `TurnRequest` → same Spine lane and cancellation policy。
5. Channel inbound/outbound contract → `Source`/`TurnRequest` and bounded `DeliveryHub`。
6. Extension manifest → explicit grant → lifecycle hooks → live tool registry。

明确的外部验证边界：飞书、QQ、企业微信的真实网络回调/发送需要平台 credentials、SDK 和可控测试租户；BoxLite/容器隔离需要部署环境。它们在矩阵中是有理由的 `REJECT_WITH_REASON`，不是未分类缺口。

严格口号 `PICO_ARCHITECTURE_ABSORPTION_ACCEPTED` 只有在同一工作树完成全量测试、聚焦测试、生产 spy、静态检查、依赖/路径审计且没有任何未验证的核心生产路径时才能发布。本文件本身不自动授予该口号。

## 路线

### 短期：本轮已落地

- Gateway/RPC foundation、auth/identity/capability enforcement。
- MCP tool bridge 与 schema gate。
- persistent at/every/cron scheduler，Gateway cron control plane。
- extension lifecycle、generic channel contract、loopback deterministic adapter。

### 中期：按明确边界推进

- 对需要的平台选择性增加独立 channel plugin；每个插件必须自带 mock、凭据隔离和 outbound retry evidence。
- 若部署确认需要容器沙箱，再以独立 adapter 接入 `SandboxExecutor`，并单独验证 path/process/OS/container/network 五层语义。
- 将 OTEL exporter 接入宿主生命周期和 exporter health，但不把远端 collector 可用性作为本地 runtime 成功条件。

### 长期：仅在产品约束成立时推进

- 多主机 scheduler claim/leader election。
- OAuth/OIDC、租户隔离和密钥轮换。
- 平台渠道的真实 E2E 与媒体能力。

这些长期项在当前矩阵已有明确 `REJECT_WITH_REASON`，不会以隐式 deferred gap 留在账本之外。
