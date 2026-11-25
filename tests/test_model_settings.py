"""Per-job model settings and their fallbacks.

Interviewing, analysis and synthesis used to share PRIMARY_MODEL /
CLASSIFIER_MODEL. Each now has its own setting; deployments that set only the
old names must keep running exactly the models they chose.
"""

import pytest

from config.settings import Settings

_ENV = (
    "PRIMARY_MODEL", "CLASSIFIER_MODEL",
    "INTERVIEWER_MODEL", "ANALYST_MODEL", "SYNTHESIS_MODEL",
)


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    for name in _ENV:
        monkeypatch.delenv(name, raising=False)


def _settings(**env):
    # _env_file=None: a developer's .env must not change what these assert.
    return Settings(_env_file=None, **env)


def test_defaults_give_each_job_its_own_model():
    s = _settings()
    assert s.interviewer_model == "claude-sonnet-5-5"
    assert s.analyst_model == "claude-haiku-5-5"
    assert s.synthesis_model == "claude-opus-5-5"
    assert s.primary_model == "claude-sonnet-5-5"


def test_old_names_still_choose_the_models(monkeypatch):
    monkeypatch.setenv("PRIMARY_MODEL", "claude-sonnet-4-6")
    monkeypatch.setenv("CLASSIFIER_MODEL", "claude-haiku-4-5-20251001")
    s = _settings()
    assert s.interviewer_model == "claude-sonnet-4-6"
    assert s.synthesis_model == "claude-sonnet-4-6"
    assert s.analyst_model == "claude-haiku-4-5-20251001"


def test_new_names_win_over_old(monkeypatch):
    monkeypatch.setenv("PRIMARY_MODEL", "old-primary")
    monkeypatch.setenv("CLASSIFIER_MODEL", "old-classifier")
    monkeypatch.setenv("INTERVIEWER_MODEL", "new-interviewer")
    monkeypatch.setenv("ANALYST_MODEL", "new-analyst")
    monkeypatch.setenv("SYNTHESIS_MODEL", "new-synthesis")
    s = _settings()
    assert (s.interviewer_model, s.analyst_model, s.synthesis_model) == (
        "new-interviewer", "new-analyst", "new-synthesis",
    )
    assert s.primary_model == "old-primary"


def test_stage2_nodes_use_the_interviewer_and_analyst_models():
    from agents.stage2_employee_interview import nodes
    from config.settings import settings

    assert nodes._get_primary_llm().model == settings.interviewer_model
    assert nodes._get_classifier_llm().model == settings.analyst_model
