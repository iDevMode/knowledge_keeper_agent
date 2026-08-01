"""A TestClient that carries session tokens the way the real UI does.

Every endpoint except session creation now requires a stage-scoped token
(review finding H5). Rather than thread an Authorization header through several
hundred existing call sites, this wrapper learns tokens from the responses that
issue them — exactly as the browser does — and attaches the right one based on
the URL being called.

It deliberately mirrors who holds what in production:

  * the token minted for a session is the one used for that session's own
    endpoints, so a Stage 2 call is made with the EMPLOYEE token;
  * /generate and the document endpoints attach a MANAGER token, because that
    is who is allowed to invoke them.

Tests that assert on the auth boundary itself should use a bare TestClient and
set headers explicitly — see tests/test_auth.py.
"""

import re
from typing import Dict, List, Optional

from fastapi.testclient import TestClient

from api.auth import EMPLOYEE, MANAGER, Scope, issue_token

_SESSION_URL = re.compile(r"/api/sessions/([^/?]+)")
_DOCUMENT_URL = re.compile(r"/api/documents/")

# Session-creation endpoints: the path segment after /sessions/ is a literal,
# not a session id.
_CREATE_PATHS = {"stage1", "stage2"}


class AuthedTestClient(TestClient):
    def __init__(self, app):
        super().__init__(app)
        self._tokens: Dict[str, str] = {}
        self._manager_tokens: List[str] = []

    # -- token management ------------------------------------------------

    def adopt(self, session_id: str, scope: Scope = MANAGER) -> str:
        """Mint and register a token for a session created outside the API.

        Several tests seed a Stage 1 session straight into the store rather than
        running the manager interview. They still need a credential.
        """
        token = issue_token(session_id, scope)
        self.register(session_id, token, scope)
        return token

    def register(self, session_id: str, token: str, scope: Scope) -> None:
        self._tokens[session_id] = token
        if scope == MANAGER:
            self._manager_tokens.append(token)

    @property
    def manager_token(self) -> Optional[str]:
        return self._manager_tokens[-1] if self._manager_tokens else None

    def token_for(self, session_id: str) -> Optional[str]:
        return self._tokens.get(session_id)

    # -- request plumbing ------------------------------------------------

    def _select_token(self, url: str) -> Optional[str]:
        if _DOCUMENT_URL.search(url):
            return self.manager_token

        match = _SESSION_URL.search(url)
        if not match:
            return None

        session_id = match.group(1)
        if session_id in _CREATE_PATHS:
            # POST /sessions/stage2 authorises against the Stage 1 id in the
            # body; POST /sessions/stage1 needs nothing.
            return self.manager_token if session_id == "stage2" else None

        if url.rstrip("/").endswith("/generate"):
            return self.manager_token or self._tokens.get(session_id)

        return self._tokens.get(session_id) or self.manager_token

    def _learn_from(self, response) -> None:
        try:
            body = response.json()
        except Exception:
            return
        if not isinstance(body, dict):
            return

        session_id = body.get("session_id")
        if not isinstance(session_id, str):
            return

        if isinstance(body.get("token"), str):
            self.register(session_id, body["token"], MANAGER)
        if isinstance(body.get("employee_token"), str):
            self.register(session_id, body["employee_token"], EMPLOYEE)

    def request(self, method, url, **kwargs):
        headers = dict(kwargs.pop("headers", None) or {})
        if not any(key.lower() == "authorization" for key in headers):
            token = self._select_token(str(url))
            if token:
                headers["Authorization"] = f"Bearer {token}"

        response = super().request(method, url, headers=headers, **kwargs)
        self._learn_from(response)
        return response
