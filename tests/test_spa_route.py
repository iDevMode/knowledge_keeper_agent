"""SPA fallback route: path containment (review finding C2).

The vulnerability is at the handler level. Over HTTP, Starlette normalises
".." out of the request path, so an HTTP-only test would pass even against the
unguarded handler and prove nothing. These tests therefore call serve_spa()
directly with raw traversal input — the form a proxy forwarding raw dot
segments, a non-normalising ASGI server, or a direct call would produce.
"""

from pathlib import Path

import pytest
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.testclient import TestClient

import api.routes as routes_mod

pytestmark = pytest.mark.skipif(
    not hasattr(routes_mod, "serve_spa"),
    reason="SPA route only mounted when frontend/dist exists",
)

# Depth matters: dist lives at <repo>/frontend/dist, so repo-root files need
# two levels of traversal. One level only reaches frontend/.
TRAVERSAL_PROBES = [
    "../../.env",
    "../../requirements.txt",
    "../../CLAUDE.md",
    "../../../.env",
]


class TestSpaPathContainment:
    def test_traversal_never_returns_a_file_outside_dist(self):
        dist = routes_mod._FRONTEND_DIST.resolve()

        for probe in TRAVERSAL_PROBES:
            response = routes_mod.serve_spa(probe)
            assert isinstance(response, HTMLResponse), (
                f"{probe!r} served a file instead of falling back to index.html"
            )
            served = getattr(response, "path", None)
            if served is not None:
                assert Path(served).resolve().is_relative_to(dist), (
                    f"{probe!r} escaped the dist directory: {served}"
                )

    def test_legitimate_asset_is_still_served(self):
        dist = routes_mod._FRONTEND_DIST.resolve()
        real_files = [p for p in dist.rglob("*") if p.is_file()]
        assert real_files, "expected at least one built asset in frontend/dist"

        relative = real_files[0].relative_to(dist).as_posix()
        response = routes_mod.serve_spa(relative)

        assert isinstance(response, FileResponse), (
            f"legitimate asset {relative!r} was not served"
        )

    def test_unknown_route_falls_back_to_index(self):
        response = routes_mod.serve_spa("some/client/side/route")
        assert isinstance(response, HTMLResponse)

    def test_traversal_over_http_does_not_leak(self):
        # Belt and braces: confirms the full stack stays safe end to end.
        client = TestClient(routes_mod.app)
        for probe in TRAVERSAL_PROBES:
            response = client.get(f"/{probe}")
            assert "ANTHROPIC_API_KEY" not in response.text
            assert "langgraph>=" not in response.text
