"""The one place model clients are created.

Every node gets its model through here, so the eval harness (`evals/`) can run
a whole interview on deterministic fakes by overriding a factory once, instead
of patching each node module. Production behaviour is the default: real
ChatAnthropic / Anthropic clients built from `config.settings`.

Roles, matching the per-job model settings:

    interviewer  Stage 2 questions and follow-ups     settings.interviewer_model
    analyst      Stage 2 follow-up and risk checks    settings.analyst_model
    primary      Stage 1 interview and profile        settings.primary_model
    classifier   Stage 1 classifiers                  settings.classifier_model
    synthesis    Stage 3 (raw Anthropic client)       settings.synthesis_model

The node-level helpers (`nodes._get_primary_llm` and friends) remain and call
into this module, so the many tests that patch them keep working.
"""

from typing import Any, Callable, Dict, Optional

from config.settings import settings

# role -> factory(max_tokens) returning an object with .invoke(messages)
_overrides: Dict[str, Callable[[int], Any]] = {}
_synthesis_client_override: Optional[Callable[[], Any]] = None

_CHAT_ROLES = {
    "interviewer": lambda: settings.interviewer_model,
    "analyst": lambda: settings.analyst_model,
    "primary": lambda: settings.primary_model,
    "classifier": lambda: settings.classifier_model,
}


def set_llm_factories(**factories: Callable[[int], Any]) -> None:
    """Override chat-model factories by role, e.g. set_llm_factories(analyst=fake)."""
    unknown = set(factories) - set(_CHAT_ROLES)
    if unknown:
        raise ValueError(f"unknown model role(s): {sorted(unknown)}")
    _overrides.update({role: f for role, f in factories.items() if f is not None})


def set_synthesis_client_factory(factory: Optional[Callable[[], Any]]) -> None:
    global _synthesis_client_override
    _synthesis_client_override = factory


def reset_llm_factories() -> None:
    """Restore the real clients."""
    global _synthesis_client_override
    _overrides.clear()
    _synthesis_client_override = None


def get_chat_llm(role: str, max_tokens: int):
    if role not in _CHAT_ROLES:
        raise ValueError(f"unknown model role: {role!r}")
    if role in _overrides:
        return _overrides[role](max_tokens)

    from langchain_anthropic import ChatAnthropic

    return ChatAnthropic(
        model=_CHAT_ROLES[role](),
        api_key=settings.anthropic_api_key,
        max_tokens=max_tokens,
    )


def get_interviewer_llm(max_tokens: int = 2048):
    return get_chat_llm("interviewer", max_tokens)


def get_analyst_llm(max_tokens: int = 512):
    return get_chat_llm("analyst", max_tokens)


def get_primary_llm(max_tokens: int = 2048):
    return get_chat_llm("primary", max_tokens)


def get_classifier_llm(max_tokens: int = 512):
    return get_chat_llm("classifier", max_tokens)


def get_synthesis_client():
    if _synthesis_client_override is not None:
        return _synthesis_client_override()

    import anthropic

    return anthropic.Anthropic(
        api_key=settings.anthropic_api_key,
        timeout=600.0,
        max_retries=3,
    )
