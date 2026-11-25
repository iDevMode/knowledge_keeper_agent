"""config.llm_provider: the single seam for model clients.

The eval harness overrides a role once and every node that uses that role
gets the fake. Without overrides each role builds the real client on its own
model setting.
"""

import pytest

from config import llm_provider
from config.settings import settings


@pytest.fixture(autouse=True)
def reset():
    llm_provider.reset_llm_factories()
    yield
    llm_provider.reset_llm_factories()


@pytest.mark.parametrize("getter, model", [
    (llm_provider.get_interviewer_llm, lambda: settings.interviewer_model),
    (llm_provider.get_analyst_llm, lambda: settings.analyst_model),
    (llm_provider.get_primary_llm, lambda: settings.primary_model),
    (llm_provider.get_classifier_llm, lambda: settings.classifier_model),
])
def test_default_clients_use_their_role_model(getter, model):
    assert getter().model == model()


def test_override_reaches_every_node_using_the_role():
    from agents.stage1_business_interview import nodes as stage1
    from agents.stage2_employee_interview import nodes as stage2

    fake = object()
    llm_provider.set_llm_factories(interviewer=lambda n: fake, classifier=lambda n: fake)
    assert stage2._get_primary_llm() is fake
    assert stage1._get_classifier_llm() is fake
    # Untouched roles still build real clients.
    assert stage2._get_classifier_llm() is not fake
    assert stage1._get_primary_llm().model == settings.primary_model


def test_synthesis_client_override():
    from agents.stage3_document_generation import generator

    fake = object()
    llm_provider.set_synthesis_client_factory(lambda: fake)
    assert generator._get_client() is fake


def test_unknown_role_is_rejected():
    with pytest.raises(ValueError):
        llm_provider.set_llm_factories(narrator=lambda n: None)
