"""Failure-isolated runtime hook contract."""

from __future__ import annotations


class RuntimeHook:
    critical = False

    def before_turn(self, runtime, **payload): pass
    def after_turn(self, runtime, **payload): pass
    def before_context(self, runtime, **payload): pass
    def after_context(self, runtime, **payload): pass
    def before_model(self, runtime, **payload): pass
    def after_model(self, runtime, **payload): pass
    def before_tool(self, runtime, **payload): pass
    def after_tool(self, runtime, **payload): pass
    def on_cancel(self, runtime, **payload): pass
    def on_error(self, runtime, **payload): pass
