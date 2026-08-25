# CodeCub

## 项目介绍

CodeCub 是一个本地优先的 AI Coding Agent，用于在真实代码仓库中完成代码理解、问题定位、上下文管理、工具调用和代码修改。

它不是简单封装一次大模型调用，而是围绕 Coding Agent 的完整执行过程构建了一套 Agent Harness：

**代码检索 → 上下文编译 → 模型推理 → 工具执行 → 状态记录 → 安全控制**

CodeCub 默认采用 Single Agent，复杂任务可以显式开启 Multi-Agent，将任务拆分为 Research、Implement 和 Review 三个阶段。

---

## 核心流程

```text
用户任务
   ↓
代码检索
   ↓
Context Compiler
   ↓
模型推理
   ↓
Tool Calling
   ↓
工具执行 / 代码修改
   ↓
结果回传
   ↓
继续 Agent Loop
   ↓
任务完成
```

复杂任务可以进一步进入：

```text
                 用户任务
                    ↓
               Orchestrator
              /      |       \
             ↓       ↓        ↓
        Research  Implement  Review
```

---

## 核心流程亮点

### 1. 代码检索：先找到真正相关的代码

CodeCub 使用 **Lexical + AST + Semantic + Reranker** 多路代码检索。

- **Lexical**：快速关键词定位
- **AST**：函数、类、引用等结构化代码定位
- **Semantic**：处理关键词无法直接命中的语义查询
- **Reranker**：对候选代码重新排序
- **Fast Path**：Definition / Reference 等明确查询优先走结构化检索

在 20 个隔离 Holdout 代码定位任务中：

**Top-3 命中率由 30% 提升至 85%。**

---

### 2. Context Compiler：控制长任务上下文

Agent 在长任务中会不断产生源码读取、搜索结果、测试日志和历史推理，如果全部原样加入 Prompt，很容易造成上下文膨胀。

CodeCub 设计了分层 Context Compiler：

```text
近期原文
   +
任务状态
   +
压缩历史
   +
检索证据
   ↓
Token Budget
   ↓
最终 Prompt
```

主要设计包括：

- **State-Preserving Compression**：压缩旧历史，同时保留关键约束、事实和决策
- **Dynamic Token Budget**：根据模型 Context Window 控制 Prompt 大小
- **File Freshness**：文件修改后旧代码证据自动失效
- **Range Coverage**：避免重复读取相同源码区间

核心目标不是单纯把 Prompt 压得越小越好，而是：

> **在有限 Context Budget 内保留当前任务真正需要的信息。**

---

### 3. Agent Loop：模型和工具持续交互

CodeCub 的模型不是只回答一次，而是在 Runtime 中持续执行：

```text
模型判断下一步
   ↓
调用工具
   ↓
读取代码 / 搜索 / 修改 / 测试
   ↓
工具结果返回模型
   ↓
继续判断
```

直到任务完成或者达到终止条件。

---

### 4. Tool Runtime：工具调用统一受控

模型生成 Tool Call 后不会直接执行，而是统一经过 Runtime：

```text
Tool Call
   ↓
参数校验
   ↓
权限 / Approval
   ↓
执行保护
   ↓
Tool Execution
   ↓
Result Validation
```

对于副作用工具，还设计了基于稳定 `operation_key` 的 Replay Protection，用于避免异常恢复、并发或 Checkpoint / Resume 时重复执行同一个操作。

---

### 5. Multi-Agent：复杂任务按角色拆分

CodeCub 默认使用 **Single Agent**。

复杂任务可以显式开启 Multi-Agent：

- **Research**：代码探索、问题定位、调用链分析
- **Implement**：负责代码修改
- **Review**：独立检查修改结果

只读、没有写冲突的任务可以并行执行，而真正修改代码的 Implement 保持受控。

在 15 组 Serial / Parallel 对比中：

**平均执行耗时下降 26.4%，额外 Token 开销仅约 1.5%。**

---

### 6. Reliability：失败时也要可控

CodeCub 不假设 LLM 每次都会正确，因此在 Agent Runtime 中加入：

- Retry / Provider Fallback
- Circuit Breaker
- Tool Validation
- Approval
- Checkpoint / Resume
- Tool Result Contract
- Side-Effect Replay Protection

核心原则是：

> **Agent 可以失败，但不能因为失败而无限重试、重复执行副作用或把异常结果静默当成成功。**

---

## 项目结构

```text
CodeCub/
├── codecub/       # Agent Runtime / Retrieval / Context / Tools
├── desktop/       # Electron + React Desktop
├── tests/         # Python tests
├── benchmarks/    # Evaluation definitions
├── scripts/       # Utilities
├── assets/
├── README.md
├── pyproject.toml
├── uv.lock
└── .env.example
```
