# 文件摘要按需召回需求

日期：2026-06-02

## 目标

减少 `Memory:` 里的噪声，同时保留最近读过文件摘要的价值。

当前 `file_summaries` 会随 `Memory:` 默认进入每轮 prompt。它在继续编辑同一批文件时有用，但在问题无关时会变成常驻噪声。

第一版改成：**文件摘要不默认展开，只在当前请求相关时进入 `Relevant memory:`。**

## 当前问题

- `Memory:` 每轮都会进入 prompt。
- `render_memory_text()` 会展开最近文件的摘要正文。
- 用户当前请求和这些文件无关时，文件摘要会占用上下文。
- 但完全删除文件摘要也不合适，因为它能帮助继续处理刚读过的文件。

## 需求

1. `Memory:` 不再默认展开文件摘要正文。
2. `Memory:` 可以保留一个很短的提示，例如：
   ```text
   - file_summaries: available for sample.py, cli.py
   ```
   如果没有可用 fresh 摘要，则显示：
   ```text
   - file_summaries: -
   ```
3. 当当前请求命中文件路径、文件名片段或摘要关键词时，对应文件摘要进入 `Relevant memory:`。
4. `Relevant memory:` 中的文件摘要格式保持简单：
   ```text
   - sample.py: contains CLI parser and provider defaults
   ```
5. stale 的文件摘要不能召回。
6. 不改变文件摘要的生成、保存、freshness 校验和路径规范化逻辑。
7. 不改变 durable memory、episodic notes、AGENT/AGENTS 项目文档读取逻辑。

## 暂不做

- 不做 embedding。
- 不做复杂打分模型。
- 不做文件摘要分级。
- 不新增命令或配置项。
- 不改变 `ContextManager` 的 section 结构。

## 验收标准

1. fresh 文件摘要不再以正文形式出现在 `Memory:`。
2. `Memory:` 能提示哪些文件有可用摘要。
3. 当前请求包含文件名或摘要关键词时，相关文件摘要出现在 `Relevant memory:`。
4. 当前请求无关时，文件摘要不出现在 `Relevant memory:`。
5. 文件内容变化导致 freshness 失效后，旧摘要不出现在 `Memory:` 提示和 `Relevant memory:`。
6. 现有 episodic notes 和 durable notes 的召回不被破坏。
