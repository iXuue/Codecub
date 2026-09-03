"""Ordered, failure-isolated hook composition."""

from __future__ import annotations


class HookComposite:
    def __init__(self, hooks=()):
        self.hooks = tuple(hooks)

    def __getattr__(self, name):
        if name not in {"before_turn", "after_turn", "before_context", "after_context", "before_model", "after_model", "before_tool", "after_tool", "on_cancel", "on_error"}:
            raise AttributeError(name)

        def emit(runtime, **payload):
            for hook in self.hooks:
                callback = getattr(hook, name, None)
                if callback is None:
                    continue
                try:
                    callback(runtime, **payload)
                except Exception:
                    if getattr(hook, "critical", False):
                        raise
        return emit
