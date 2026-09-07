import copy

import pytest

from app import config as _config_module


@pytest.fixture(autouse=True)
def _clean_operator_auth_env(monkeypatch, request):
    # If the test is not specifically testing operator auth, isolate the test from
    # any developer-local SKC_OPERATOR_PASSWORD in .env.
    if "test_operator_auth" not in request.node.fspath.basename:
        monkeypatch.delenv("SKC_OPERATOR_PASSWORD", raising=False)


@pytest.fixture(autouse=True)
def _isolate_live_config():
    """Snapshot and restore the real config.yaml (and the in-memory config
    cache) around every test.

    Several tests exercise config-writing code paths that intentionally
    target the live default config with no explicit path override — e.g.
    `PUT /api/translation/targets`, or `save_translation_settings()` called
    with no `config_path` — because that IS the production behavior being
    tested. Without this, whichever test runs last "wins" and leaves
    config.yaml (and the process-global `_cfg`) mutated for every test that
    runs after it in the same session, which is exactly the kind of
    order-dependent pollution that turned into 4 failing tests previously.
    This is a safety net independent of any individual test's cleanup.
    """
    config_path = _config_module._CONFIG_PATH
    original_bytes = config_path.read_bytes() if config_path.exists() else None
    original_cfg = copy.deepcopy(_config_module._cfg)

    yield

    _config_module._cfg = original_cfg
    if original_bytes is not None:
        config_path.write_bytes(original_bytes)
    elif config_path.exists():
        config_path.unlink()
