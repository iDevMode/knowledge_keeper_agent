"""Suite-wide fixtures."""

import pytest


@pytest.fixture(autouse=True)
def outbox():
    """No test may send real email, whatever SENDGRID_API_KEY says.

    Every test gets a fresh in-memory outbox; tests that care about email take
    this fixture by name and inspect ``outbox.messages``.
    """
    from api.mailer import OutboxMailer, set_mailer

    mailer = OutboxMailer()
    set_mailer(mailer)
    yield mailer
    set_mailer(None)


@pytest.fixture(autouse=True)
def fresh_account_store():
    """Accounts are per-test, like sessions and documents."""
    from api.accounts import reset_account_store

    reset_account_store()
    yield
    reset_account_store()
