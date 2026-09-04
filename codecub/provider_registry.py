"""Static provider catalog and lookup seam for CodeCub model clients."""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Mapping

from .provider_contract import ProviderCapabilities, ProviderSpec


DEFAULT_PROVIDER_SPECS = (
    ProviderSpec(
        name="ollama",
        display_name="Ollama",
        client_kind="ollama",
        default_model="qwen3.5:4b",
        default_base_url="http://127.0.0.1:11434",
        model_env=("OLLAMA_MODEL",),
        base_url_env=("OLLAMA_HOST",),
        host_env=("OLLAMA_HOST",),
        model_prefixes=("qwen", "llama", "mistral", "deepseek-r", "gemma"),
        capabilities=ProviderCapabilities(
            protocol="ollama-generate",
            supports_streaming=True,
        ),
    ),
    ProviderSpec(
        name="openai",
        display_name="OpenAI-compatible",
        client_kind="openai",
        default_model="gpt-5.4",
        default_base_url="https://www.right.codes/codex/v1",
        model_env=("OPENAI_MODEL",),
        base_url_env=("OPENAI_API_BASE",),
        api_key_envs=("OPENAI_API_KEY", "OPENAI_API_TOKEN"),
        requires_api_key=True,
        model_prefixes=("gpt-", "o1", "o3", "o4", "gpt", "codex"),
        capabilities=ProviderCapabilities(
            protocol="openai-responses-or-chat",
            supports_native_tools=True,
            supports_prompt_cache=True,
            supports_tool_choice=True,
        ),
    ),
    ProviderSpec(
        name="deepseek",
        display_name="DeepSeek",
        client_kind="openai",
        default_model="deepseek-v4-flash",
        default_base_url="https://api.deepseek.com",
        model_env=("DEEPSEEK_MODEL",),
        base_url_env=("DEEPSEEK_API_BASE",),
        api_key_envs=("DEEPSEEK_API_KEY",),
        requires_api_key=True,
        model_prefixes=("deepseek",),
        capabilities=ProviderCapabilities(
            protocol="openai-chat",
            supports_native_tools=True,
            supports_tool_choice=True,
        ),
    ),
    ProviderSpec(
        name="kimi",
        display_name="Kimi / Moonshot",
        client_kind="openai",
        default_model="moonshot-v1-8k",
        default_base_url="https://api.moonshot.cn/v1",
        model_env=("MOONSHOT_MODEL", "KIMI_MODEL"),
        base_url_env=("MOONSHOT_API_BASE", "KIMI_API_BASE"),
        api_key_envs=("MOONSHOT_API_KEY", "KIMI_API_KEY"),
        requires_api_key=True,
        model_prefixes=("moonshot", "kimi"),
        capabilities=ProviderCapabilities(
            protocol="openai-chat",
            supports_native_tools=True,
            supports_tool_choice=True,
        ),
    ),
    ProviderSpec(
        name="minimax",
        display_name="MiniMax",
        client_kind="openai",
        default_model="MiniMax-M3",
        default_base_url="https://api.minimax.io/v1",
        model_env=("MINIMAX_MODEL",),
        base_url_env=("MINIMAX_API_BASE",),
        api_key_envs=("MINIMAX_API_KEY",),
        requires_api_key=True,
        model_prefixes=("minimax",),
        capabilities=ProviderCapabilities(
            protocol="openai-chat",
            supports_native_tools=True,
            supports_tool_choice=True,
        ),
    ),
    ProviderSpec(
        name="anthropic",
        display_name="Anthropic-compatible",
        client_kind="anthropic",
        default_model="claude-sonnet-4-6",
        default_base_url="https://www.right.codes/claude/v1",
        model_env=("ANTHROPIC_MODEL",),
        base_url_env=("ANTHROPIC_API_BASE",),
        api_key_envs=("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "RIGHT_CODES_API_KEY", "OPENAI_API_KEY"),
        requires_api_key=True,
        model_prefixes=("claude",),
        capabilities=ProviderCapabilities(
            protocol="anthropic-messages",
            supports_native_tools=False,
            supports_prompt_cache=True,
        ),
    ),
)


class ProviderRegistry(Mapping[str, ProviderSpec]):
    """Read-only provider catalog with explicit name and model lookup."""

    def __init__(self, specs: Iterable[ProviderSpec] = DEFAULT_PROVIDER_SPECS):
        specs = tuple(specs)
        catalog = {spec.name.lower(): spec for spec in specs}
        if len(catalog) != len(specs):
            raise ValueError("provider names must be unique")
        self._catalog = catalog

    def __getitem__(self, name: str) -> ProviderSpec:
        try:
            return self._catalog[str(name).strip().lower()]
        except KeyError as exc:
            raise KeyError(f"unknown provider: {name}") from exc

    def __iter__(self) -> Iterator[str]:
        return iter(self._catalog)

    def __len__(self) -> int:
        return len(self._catalog)

    def get_spec(self, name: str | None) -> ProviderSpec:
        normalized = str(name or "").strip().lower()
        if not normalized:
            raise ValueError("provider name is required")
        try:
            return self[normalized]
        except KeyError as exc:
            allowed = ", ".join(self._catalog)
            raise ValueError(f"unknown provider {name!r}; choose one of: {allowed}") from exc

    def resolve(self, explicit: str | None, env_value: str | None, default: str = "openai") -> ProviderSpec:
        requested = str(explicit or env_value or default).strip().lower()
        return self.get_spec(requested)

    def match_model(self, model: str) -> ProviderSpec | None:
        normalized = str(model or "").strip().lower()
        if not normalized:
            return None
        candidates = [
            spec
            for spec in self._catalog.values()
            if any(normalized.startswith(prefix.lower()) for prefix in spec.model_prefixes)
        ]
        return candidates[0] if len(candidates) == 1 else None

    def public_catalog(self) -> list[dict]:
        return [self._catalog[name].to_dict() for name in self._catalog]


PROVIDER_REGISTRY = ProviderRegistry()
