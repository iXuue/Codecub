
# ---------------------------------------------------------------------------
# Context Compiler 主类
# ---------------------------------------------------------------------------


class ContextCompiler:
    """编译“下一次 Model Request 应看到什么”。

    输入：
    - user_message：当前用户任务（Pinned）。
    - working_state：Task-local Working State。
    - history / native_messages：完整 raw 历史（不删除，只影响 model 可见部分）。
    - pinned_extra：项目规则 / safety / workspace 等稳定上下文（可来自 runtime prefix）。
    - code_index / budget / condenser。

    输出：
    - legacy：compile_text -> (prompt_text, metadata)
    - native：compile_native -> (messages, metadata)
    - compiled：CompiledContext（含各层 token 估算与 provenance）
    """

    def __init__(
        self,
        token_counter=None,
        budget=None,
        condenser=None,
        code_index=None,
        repo_map_selector=None,
        redact_fn=None,
        layer_ratios=None,
        recent_floor_groups=DEFAULT_RECENT_VERBATIM_FLOOR_GROUPS,
        workspace_root=None,
    ):
        self.token_counter = token_counter
        self.budget = budget or ContextBudget.resolve()
        self.condenser = condenser or HistoryCondenser(redact_fn=redact_fn)
        self.code_index = code_index
        self.repo_map_selector = repo_map_selector or RepoMapSelector(code_index)
        self.redact_fn = redact_fn or (lambda text: text)
        self.layer_ratios = dict(DEFAULT_LAYER_BUDGET_RATIOS)
        if layer_ratios:
            self.layer_ratios.update({str(k): float(v) for k, v in layer_ratios.items()})
        self.recent_floor_groups = int(recent_floor_groups)
        self.workspace_root = workspace_root
        # 状态（每轮 compile 更新，供 observability）。
        self.last_compile_metadata = {}
        self.compression_count = 0
        self.compression_failure_count = 0
        self.compressed_summaries = []  # recursive condensation 栈

    # ------------------------------------------------------------------
    # 公共入口
    # ------------------------------------------------------------------

    def compile_text(self, user_message, working_state=None, history=None, pinned_extra=None):
        """legacy text 模式：从 session history 编译文本 prompt。"""
        working_state = working_state or WorkingState()
        history = list(history or [])
        pinned_extra = pinned_extra or {}
        pinned = self._build_pinned(user_message, pinned_extra)
        estimated = self._estimate_candidate_tokens(pinned, working_state, history)
        should_compress = self.budget.should_compress(estimated)
        compressed_history = []
        recent_items = []
        if should_compress:
            self.compression_count += 1
            compressed_history, recent_items = self._partition_history(
                history, working_state
            )
        else:
            recent_items = self._history_items(history)
        repo_map_items, repo_map_details = self._build_repo_map(user_message, working_state)
        compiled = self._assemble(
            pinned=pinned,
            working_state=working_state,
            recent_items=recent_items,
            compressed_history_items=compressed_history,
            repo_map_items=repo_map_items,
        )
        self.last_compile_metadata = self._metadata(
            user_message=user_message,
            working_state=working_state,
            history=history,
            pinned=pinned,
            recent_items=recent_items,
            compressed_history_items=compressed_history,
            repo_map_items=repo_map_items,
            repo_map_details=repo_map_details,
            should_compress=should_compress,
            estimated_tokens=estimated,
            compiled=compiled,
        )
        return compiled.text, self.last_compile_metadata

    def compile_native(self, user_message, working_state=None, native_messages=None, pinned_extra=None):
        """native 模式：压缩 native_messages，保持 assistant.tool_calls + tool result 原子性。

        返回 (messages, metadata)；messages 始终以合法 native 顺序：
        system / user / assistant(tool_calls) 后紧跟对应 tool 结果。
        """
        working_state = working_state or WorkingState()
        native_messages = list(native_messages or [])
        pinned_extra = pinned_extra or {}
        pinned = self._build_pinned(user_message, pinned_extra)
        estimated = self._estimate_native_tokens(native_messages)
        should_compress = self.budget.should_compress(estimated)
        if not should_compress:
            self.last_compile_metadata = self._metadata(
                user_message=user_message,
                working_state=working_state,
                history=native_messages,
                pinned=pinned,
                recent_items=self._native_items(native_messages),
                compressed_history_items=[],
                repo_map_items=[],
                repo_map_details={},
                should_compress=False,
                estimated_tokens=estimated,
                compiled=None,
                native_mode=True,
            )
            return native_messages, self.last_compile_metadata
        self.compression_count += 1
        groups = self._group_native_messages(native_messages)
        recent_groups, older_groups = self._partition_native_groups(groups)
        compressed_history_items, older_raw = self._compress_older_groups(older_groups, working_state)
        repo_map_items, repo_map_details = self._build_repo_map(user_message, working_state)
        # 组装消息：system(pinned + working state + compressed) + recent groups
        messages = []
        pinned_text = self._render_pinned(pinned)
        working_text = working_state.to_text()
        summary_text = self._render_compressed_items(compressed_history_items)
        preamble_parts = []
        if pinned_text:
            preamble_parts.append(pinned_text)
        if working_text.strip():
            preamble_parts.append(working_text)
        if summary_text.strip():
            preamble_parts.append(summary_text)
        if repo_map_items:
            preamble_parts.append("Repository map:\n" + "\n".join(repo_map_items))
        if preamble_parts:
            messages.append(
                {
                    "role": "system",
                    "content": "\n\n".join(preamble_parts),
                }
            )
        for group in recent_groups:
            messages.append(group["message"])
            messages.extend(group.get("results", []))
        self.last_native_messages = messages
        self.last_compile_metadata = self._metadata(
            user_message=user_message,
            working_state=working_state,
            history=native_messages,
            pinned=pinned,
            recent_items=self._native_items(recent_groups),
            compressed_history_items=compressed_history_items,
            repo_map_items=repo_map_items,
            repo_map_details=repo_map_details,
            should_compress=True,
            estimated_tokens=estimated,
            compiled=None,
            native_mode=True,
            older_raw_entries=len(older_raw),
        )
        return messages, self.last_compile_metadata

    # ------------------------------------------------------------------
    # Pinned Context
    # ------------------------------------------------------------------

    def _build_pinned(self, user_message, pinned_extra):
        items = OrderedDict()
        items[PINNED_USER_TASK] = ContextItem(
            key=PINNED_USER_TASK,
            kind=ITEM_KIND_PINNED,
            text=f"User task: {str(user_message).strip()}",
            provenance={"step": 0},
        )
        for key in (
            PINNED_PROJECT_RULES,
            PINNED_SAFETY,
            PINNED_WORKSPACE,
            PINNED_RUNTIME_MODE,
            PINNED_TOOL_CONTRACT,
        ):
            value = str(pinned_extra.get(key, "") or "").strip()
            if not value:
                continue
            items[key] = ContextItem(key=key, kind=ITEM_KIND_PINNED, text=value)
        return list(items.values())

    @staticmethod
    def _render_pinned(pinned):
        return "\n".join(item.text for item in pinned)

    # ------------------------------------------------------------------
    # Recent Verbatim / 历史分区
    # ------------------------------------------------------------------

    def _history_items(self, history):
        items = []
        for index, item in enumerate(history):
            items.append(
                ContextItem(
                    key=f"history:{index}",
                    kind=ITEM_KIND_RECENT_VERBATIM,
                    text=self._render_history_item(item),
                )
            )
        return items

    def _render_history_item(self, item):
        role = str(item.get("role", ""))
        name = str(item.get("name", ""))
        if role == "tool":
            prefix = f"[tool:{name}] {json.dumps(item.get('args', {}), sort_keys=True, ensure_ascii=True)}"
            content = self.redact_fn(str(item.get("content", "")))
            return f"{prefix}\n{content}"
        content = self.redact_fn(str(item.get("content", "")))
        return f"[{role}] {content}"

    def _native_items(self, groups):
        items = []
        for group in groups:
            message = group.get("message", {})
            text = str(message.get("content") or message.get("tool_calls") or "")
            items.append(
                ContextItem(
                    key=f"native:{group.get('index', 0)}",
                    kind=ITEM_KIND_RECENT_VERBATIM,
                    text=str(text)[:400],
                )
            )
        return items

    def _group_native_messages(self, messages):
        """把 native messages 分成原子组：assistant(tool_calls) + 其 tool results。

        tool 消息的 tool_call_id 必须在同一组 assistant 的 call_ids 中；否则
        视为 orphan 并保留原样（禁止删除）。
        """
        groups = []
        current = None
        for index, message in enumerate(messages):
            role = str(message.get("role", ""))
            if role == "assistant" and message.get("tool_calls"):
                current = {"index": index, "message": message, "results": [], "call_ids": set()}
                for call in message.get("tool_calls") or []:
                    call_id = str(call.get("id", ""))
                    current["call_ids"].add(call_id)
                groups.append(current)
            elif role == "tool":
                call_id = str(message.get("tool_call_id", ""))
                attached = False
                if current is not None and call_id in current["call_ids"]:
                    current["results"].append(message)
                    attached = True
                if not attached:
                    for group in reversed(groups):
                        if call_id in group.get("call_ids", set()):
                            group["results"].append(message)
                            attached = True
                            break
                if not attached:
                    groups.append({"index": index, "message": message, "results": [], "call_ids": set()})
            else:
                groups.append({"index": index, "message": message, "results": [], "call_ids": set()})
        return groups

    def _partition_native_groups(self, groups):
        """按 budget 与 floor 切分 recent / older。"""
        budget_tokens = int(
            self.budget.usable_input_budget
            * self.layer_ratios.get("recent_verbatim", 0.38)
        )
        recent = []
        used = 0
        # 先保 floor：最近 N 个完整 group。
        for group in reversed(groups):
            recent.append(group)
            used += self._count(self._group_text(group))
            if len(recent) >= self.recent_floor_groups:
                break
        recent.reverse()
        # 再按 budget 吸收更多 recent group（从后往前）。
        for group in reversed(groups):
            if any(item is group for item in recent):
                continue
            cost = self._count(self._group_text(group))
            if used + cost <= budget_tokens:
                recent.insert(0, group)
                used += cost
            else:
                break
        recent_ids = {id(item) for item in recent}
        older = [group for group in groups if id(group) not in recent_ids]
        return recent, older

    def _group_text(self, group):
        message = group.get("message", {})
        parts = [str(message.get("content") or "")]
        for call in message.get("tool_calls") or []:
            parts.append(
                f"{call.get('name', '')}({json.dumps(call.get('arguments', {}), sort_keys=True, ensure_ascii=True)})"
            )
        for result in group.get("results", []):
            parts.append(str(result.get("content", ""))[:400])
        return "\n".join(parts)

    def _compress_older_groups(self, older_groups, working_state):
        """对更旧 native groups 做结构化压缩（Stage C）。"""
        if not older_groups:
            return [], []
        raw_items = []
        for group in older_groups:
            message = group.get("message", {})
            raw_items.append(
                {
                    "role": str(message.get("role", "")),
                    "name": "",
                    "args": {},
                    "content": self._group_text(group),
                }
            )
        summary, meta = self.condenser.condense(
            raw_items, goal=working_state.goal, step=working_state.last_updated_step
        )
        if meta.get("mode") == "deterministic_fallback":
            self.compression_failure_count += 1
        self.compressed_summaries.append({"summary": summary, "meta": meta})
        return [
            ContextItem(
                key=f"condensed:{len(self.compressed_summaries)}",
                kind=ITEM_KIND_COMPRESSED_HISTORY,
                text=summary,
            )
        ], raw_items

    def _partition_history(self, history, working_state):
        """legacy：把 history 分为 older（可压缩）与 recent（原文）。"""
        budget_tokens = int(
            self.budget.usable_input_budget
            * self.layer_ratios.get("recent_verbatim", 0.38)
        )
        recent = []
        older = []
        used = 0
        for item in reversed(history):
            cost = self._count(self._render_history_item(item))
            if used + cost <= budget_tokens or not older:
                recent.append(item)
                used += cost
            else:
                older.append(item)
        recent.reverse()
        older.reverse()
        if older:
            summary, meta = self.condenser.condense(
                older, goal=working_state.goal, step=working_state.last_updated_step
            )
            if meta.get("mode") == "deterministic_fallback":
                self.compression_failure_count += 1
            self.compressed_summaries.append({"summary": summary, "meta": meta})
            return [
                ContextItem(
                    key=f"condensed:{len(self.compressed_summaries)}",
                    kind=ITEM_KIND_COMPRESSED_HISTORY,
                    text=summary,
                )
            ], self._history_items(recent)
        return [], self._history_items(recent)

    @staticmethod
    def _render_compressed_items(items):
        return "\n\n".join(item.text for item in items)

    # ------------------------------------------------------------------
    # Repo Map
    # ------------------------------------------------------------------

    def _build_repo_map(self, user_message, working_state):
        budget_tokens = int(
            self.budget.usable_input_budget
            * self.layer_ratios.get("repo_map", 0.10)
        )
        if self.repo_map_selector is None or budget_tokens <= 0:
            return [], {}
        blocks, details = self.repo_map_selector.select(
            user_message, working_state, budget_tokens, counter=self.token_counter
        )
        return [
            ContextItem(key=f"repo-map:{i}", kind=ITEM_KIND_REPO_MAP, text=block)
            for i, block in enumerate(blocks)
        ], details

    # ------------------------------------------------------------------
    # 组装与估算
    # ------------------------------------------------------------------

    def _assemble(self, pinned, working_state, recent_items, compressed_history_items, repo_map_items):
        parts = []
        pinned_text = self._render_pinned(pinned)
        if pinned_text:
            parts.append(pinned_text)
        working_text = working_state.to_text()
        if working_text.strip():
            parts.append(working_text)
        if compressed_history_items:
            parts.append(self._render_compressed_items(compressed_history_items))
        if repo_map_items:
            parts.append("Repository map:\n" + "\n".join(item.text for item in repo_map_items))
        if recent_items:
            parts.append("Transcript:\n" + "\n\n".join(item.text for item in recent_items))
        text = "\n\n".join(parts).strip()
        return CompiledContext(
            text=text,
            pinned=pinned,
            working_state=working_state,
            recent_items=recent_items,
            compressed_history_items=compressed_history_items,
            repo_map_items=repo_map_items,
        )

    def _estimate_candidate_tokens(self, pinned, working_state, history):
        text = self._render_pinned(pinned) + "\n" + working_state.to_text() + "\n"
        for item in history:
            text += self._render_history_item(item) + "\n"
        return self._count(text)

    def _estimate_native_tokens(self, messages):
        total = 0
        for message in messages:
            total += self._count(str(message.get("content") or ""))
            for call in message.get("tool_calls") or []:
                total += self._count(
                    str(call.get("name", ""))
                    + json.dumps(call.get("arguments", {}), sort_keys=True)
                )
        return total

    def _count(self, text):
        if self.token_counter is not None:
            try:
                return self.token_counter.count(text)
            except Exception:
                return len(str(text))
        return len(str(text))

    # ------------------------------------------------------------------
    # Observability metadata
    # ------------------------------------------------------------------

    def _metadata(self, **kwargs):
        native_mode = kwargs.pop("native_mode", False)
        compiled = kwargs.pop("compiled", None)
        user_message = kwargs.get("user_message", "")
        working_state = kwargs.get("working_state") or WorkingState()
        pinned = kwargs.get("pinned", [])
        recent_items = kwargs.get("recent_items", [])
        compressed_history_items = kwargs.get("compressed_history_items", [])
        repo_map_items = kwargs.get("repo_map_items", [])
        history = kwargs.get("history", [])
        should_compress = kwargs.get("should_compress", False)
        estimated = kwargs.get("estimated_tokens", 0)
        repo_map_details = kwargs.get("repo_map_details", {})
        compiled_text = compiled.text if compiled is not None else ""
        if native_mode:
            compiled_text = str(
                self._estimate_native_tokens(getattr(self, "last_native_messages", []) or [])
            )
        return {
            "compiler": "context_compiler",
            "native_mode": native_mode,
            "context_compile_count": self.compression_count + 1,
            "compression_count": self.compression_count,
            "compression_failure_count": self.compression_failure_count,
            "should_compress": should_compress,
            "candidate_context_tokens": estimated,
            "compiled_context_tokens": self._count(compiled_text),
            "pinned_tokens": sum(item.token_count(self.token_counter) for item in pinned),
            "working_state_tokens": self._count(working_state.to_text()),
            "recent_verbatim_tokens": sum(item.token_count(self.token_counter) for item in recent_items),
            "compressed_history_tokens": sum(item.token_count(self.token_counter) for item in compressed_history_items),
            "repo_map_tokens": sum(item.token_count(self.token_counter) for item in repo_map_items),
            "raw_history_tokens": (
                self._count(
                    "\n".join(self._render_history_item(item) for item in history)
                )
                if history
                else 0
            ),
            "fresh_fact_count": len(working_state.fresh_facts()),
            "stale_fact_count": len(working_state.stale_facts()),
            "estimated": True,
            "budget_source": self.budget.budget_source,
            "usable_input_budget": self.budget.usable_input_budget,
            "trigger_threshold": self.budget.trigger_threshold,
            "repo_map_selection": repo_map_details,
            "user_request": user_message,
        }


@dataclass
class CompiledContext:
    """一次编译的产物。"""

    text: str
    pinned: list = field(default_factory=list)
    working_state: Optional[WorkingState] = None
    recent_items: list = field(default_factory=list)
    compressed_history_items: list = field(default_factory=list)
    repo_map_items: list = field(default_factory=list)

    def to_dict(self):
        return {
            "text": self.text,
            "pinned_keys": [item.key for item in self.pinned],
            "recent_keys": [item.key for item in self.recent_items],
            "compressed_keys": [item.key for item in self.compressed_history_items],
            "repo_map_keys": [item.key for item in self.repo_map_items],
        }
