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
