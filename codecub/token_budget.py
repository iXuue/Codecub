"""Token 计数抽象：可使用 tiktoken，缺失时保持字符预算回退。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TokenCounter:
    source: str = "unavailable"
    quality: str = "unavailable"
    encoding_name: str = ""

    def count(self, text):
        raise NotImplementedError


class TiktokenCounter(TokenCounter):
    def __init__(self, model=""):
        import tiktoken
        try:
            encoding = tiktoken.encoding_for_model(model) if model else tiktoken.get_encoding("cl100k_base")
        except KeyError:
            encoding = tiktoken.get_encoding("cl100k_base")
        super().__init__("tiktoken", "estimated", encoding.name)
        self.encoding = encoding

    def count(self, text):
        return len(self.encoding.encode(str(text), disallowed_special=()))


def resolve_token_counter(model=""):
    try:
        return TiktokenCounter(model)
    except (ImportError, ModuleNotFoundError):
        return None


def clip_to_budget(text, budget, counter):
    text, budget = str(text), int(budget)
    if budget <= 0:
        return ""
    if counter is None or counter.count(text) <= budget:
        return text
    suffix = "..."
    low, high, best = 0, len(text), ""
    while low <= high:
        middle = (low + high) // 2
        candidate = text[:middle] + suffix
        if counter.count(candidate) <= budget:
            best, low = candidate, middle + 1
        else:
            high = middle - 1
    return best


def resolve_prompt_budget(context_window, max_new_tokens, safety_margin_tokens=256):
    if not context_window:
        return None
    return max(0, int(context_window) - int(max_new_tokens) - int(safety_margin_tokens))
