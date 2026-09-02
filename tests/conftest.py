import pytest


@pytest.fixture(autouse=True)
def _clean_operator_auth_env(monkeypatch, request):
    # If the test is not specifically testing operator auth, isolate the test from
    # any developer-local SKC_OPERATOR_PASSWORD in .env.
    if "test_operator_auth" not in request.node.fspath.basename:
        monkeypatch.delenv("SKC_OPERATOR_PASSWORD", raising=False)


