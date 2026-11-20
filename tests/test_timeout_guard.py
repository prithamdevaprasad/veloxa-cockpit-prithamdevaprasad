"""The per-test timeout is configured (pytest-timeout), so a hang fails visibly instead of stalling CI."""


def test_a_per_test_timeout_is_configured(pytestconfig):
    assert pytestconfig.pluginmanager.hasplugin("timeout")
    assert float(pytestconfig.getini("timeout")) == 300
