"""Central factory for LLM clients.

Graph nodes obtain their models through this module so that tests and the
eval harness (evals/) can substitute deterministic fakes for the primary and
classifier models without patching every node module individually.

Production behaviour is unchanged: by default the factories build the real
ChatAnthropic clients from settings.
"""

from typing import Any, Callable, Optional

from config.settings import settings

# Overridable factories. Each receives max_tokens and returns an LLM object
# compatible with langchain's Runnable interface (invoke / with_structured_output).
_primary_factory: Optional[Callable[[int], Any]] = None
_classifier_factory: Optional[Callable[[int], Any]] = None


def set_llm_factories(primary=None, classifier=None) -> None:
    """Override the LLM factories (used by tests and the eval harness)."""
    global _primary_factory, _classifier_factory
    if primary is not None:
        _primary_factory = primary
    if classifier is not None:
        _classifier_factory = classifier


def reset_llm_factories() -> None:
    """Restore the default (real ChatAnthropic) factories."""
    global _primary_factory, _classifier_factory
    _primary_factory = None
    _classifier_factory = None


def get_primary_llm(max_tokens: int = 2048):
    if _primary_factory is not None:
        return _primary_factory(max_tokens)
    from langchain_anthropic import ChatAnthropic

    return ChatAnthropic(
        model=settings.primary_model,
        api_key=settings.anthropic_api_key,
        max_tokens=max_tokens,
    )


def get_classifier_llm(max_tokens: int = 512):
    if _classifier_factory is not None:
        return _classifier_factory(max_tokens)
    from langchain_anthropic import ChatAnthropic

    return ChatAnthropic(
        model=settings.classifier_model,
        api_key=settings.anthropic_api_key,
        max_tokens=max_tokens,
    )
