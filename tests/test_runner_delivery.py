"""Deployment checks are offline; mock submissions never execute their source."""
import importlib.util
import hashlib
from pathlib import Path
from urllib.parse import urlsplit
from urllib.request import build_opener

import pytest


ROOT = Path(__file__).resolve().parents[1]


def load_script(filename):
    spec = importlib.util.spec_from_file_location(filename.replace("-", "_"), ROOT / "deploy" / "bin" / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def good_settings(module):
    conf = dict(module.REQUIRED_CONFIG)
    conf.update(AUTHN_TOKEN="aB0123456789cDefghijklmnopqrstuvWx", REDIS_PASSWORD="rB0123456789cDefghijklmnopqrstuvWx",
                POSTGRES_PASSWORD="pB0123456789cDefghijklmnopqrstuvWx")
    images = {"JUDGE0_VERSION": "1.13.1", "POSTGRES_TAG": "16.9", "REDIS_TAG": "7.4.2", "CADDY_TAG": "2.10.0",
              "JUDGE0_SHA256": hashlib.sha256(b"shape fixture judge0").hexdigest(),
              "POSTGRES_SHA256": hashlib.sha256(b"shape fixture postgres").hexdigest(),
              "REDIS_SHA256": hashlib.sha256(b"shape fixture redis").hexdigest(),
              # Shape-only fixture, never used for a socket or HTTP request.
              "CADDY_SHA256": hashlib.sha256(b"shape fixture caddy").hexdigest(),
              "RUNNER_DOMAIN": "runner.owned-domain.net", "OY_APP_EGRESS_IP": "1.2.3.4"}
    return conf, images


def test_deployment_check_only_validates_supplied_files(tmp_path, monkeypatch):
    module = load_script("check-runner.py")
    conf, images = good_settings(module)
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-be-read")
    monkeypatch.setenv("CODE_RUNNER_TOKEN", "must-not-be-read")
    assert module.validate(conf, images) == []
    path = tmp_path / "runner.conf"
    path.write_text("AUTHN_TOKEN=first\nAUTHN_TOKEN=second\n", encoding="utf-8")
    with pytest.raises(module.ConfigError, match="重复"):
        module.read_settings(path)


@pytest.mark.parametrize("field,value", [("ENABLE_NETWORK", "true"), ("ALLOW_ENABLE_NETWORK", "true"),
    ("ENABLE_CALLBACKS", "true"), ("MAX_WALL_TIME_LIMIT", "60"), ("AUTHN_TOKEN", "short"),
    ("COUNT", "100")])
def test_unsafe_runner_settings_are_rejected(field, value):
    module = load_script("check-runner.py")
    conf, images = good_settings(module)
    conf[field] = value
    errors = module.validate(conf, images)
    assert errors and any(field in error for error in errors)
    if field == "AUTHN_TOKEN":
        assert value not in "\n".join(errors)


@pytest.mark.parametrize("field,value", [("JUDGE0_VERSION", "latest"), ("JUDGE0_VERSION", "1.13.0"),
    ("JUDGE0_SHA256", ""), ("REDIS_SHA256", "NOT-A-DIGEST"), ("POSTGRES_TAG", "latest"),
    ("JUDGE0_SHA256", "1" * 64)])
def test_unpinned_or_known_old_images_are_rejected(field, value):
    module = load_script("check-runner.py")
    conf, images = good_settings(module)
    images[field] = value
    assert any(field in error for error in module.validate(conf, images))


def test_shipped_templates_cannot_pass_with_placeholder_secrets_or_digests():
    module = load_script("check-runner.py")
    conf = module.read_settings(ROOT / "deploy" / "runner" / "judge0.conf.example")
    images = module.read_settings(ROOT / "deploy" / "runner" / "images.env.example")
    errors = module.validate(conf, images)
    assert any("AUTHN_TOKEN" in error for error in errors)
    assert any("JUDGE0_SHA256" in error for error in errors)


@pytest.mark.parametrize("field,value", [("RUNNER_DOMAIN", "runner.example.invalid"), ("RUNNER_DOMAIN", "ce.judge0.com"),
    ("RUNNER_DOMAIN", "runner.example.com"), ("RUNNER_DOMAIN", "1.2.3.4"),
    ("RUNNER_DOMAIN", "bad-host/?token=secret"), ("OY_APP_EGRESS_IP", "0.0.0.0/0"), ("OY_APP_EGRESS_IP", "127.0.0.1"),
    ("OY_APP_EGRESS_IP", "192.0.2.1"), ("OY_APP_EGRESS_IP", "198.51.100.20"), ("OY_APP_EGRESS_IP", "203.0.113.1"),
    ("OY_APP_EGRESS_IP", "2001:db8::1")])
def test_gateway_configuration_refuses_public_demos_placeholders_and_broad_sources(field, value):
    module = load_script("check-runner.py")
    conf, images = good_settings(module)
    images[field] = value
    assert any(field in error for error in module.validate(conf, images))


def test_unknown_config_keys_fail_and_fixed_private_vpc_peer_is_allowed():
    module = load_script("check-runner.py")
    conf, images = good_settings(module)
    images["OY_APP_EGRESS_IP"] = "10.5.1.20"
    assert module.validate(conf, images) == []
    conf["OTHER_UNREVIEWED_SWITCH"] = "true"
    assert any("OTHER_UNREVIEWED_SWITCH" in error for error in module.validate(conf, images))


def test_fake_protocol_refuses_unknown_source_and_does_not_execute_it():
    module = load_script("runner-selftest.py")
    fake = module.FakeProtocol("test-only-token")
    status, result = fake.submit({"X-Auth-Token": "test-only-token"}, b'{"source_code":"ZXhlYygnaW1wb3J0IG9zJyk="}')
    assert status == 400
    assert result == {"error": "mock request rejected"}
    assert fake.tasks == {}


def test_fake_protocol_requires_header_auth_and_bounded_payload():
    module = load_script("runner-selftest.py")
    fake = module.FakeProtocol("test-only-token")
    assert fake.submit({}, b"{}")[0] == 401
    assert fake.submit({"X-Auth-Token": "test-only-token"}, b"x" * (module.BODY_LIMIT + 1))[0] == 413


def test_explicit_live_mode_rejects_known_public_demo_without_network(monkeypatch, capsys):
    module = load_script("runner-selftest.py")
    monkeypatch.setenv("CODE_RUNNER_URL", "https://ce.judge0.com")
    monkeypatch.setenv("CODE_RUNNER_TOKEN", "test-only-token")
    monkeypatch.setattr(module.code_runner, "run", lambda *args: pytest.fail("must not submit to a public demo"))
    assert module.main(["--live", "--confirm-isolated-service"]) == 1
    output = capsys.readouterr().out
    assert "test-only-token" not in output
    assert "ce.judge0.com" not in output


def test_live_mode_requires_confirmation_before_any_execution(monkeypatch, capsys):
    module = load_script("runner-selftest.py")
    monkeypatch.setenv("CODE_RUNNER_URL", "https://runner.owned-domain.net")
    monkeypatch.setenv("CODE_RUNNER_TOKEN", "test-only-token")
    monkeypatch.setattr(module.code_runner, "run", lambda *args: pytest.fail("must require independent sandbox confirmation"))
    assert module.main(["--live"]) == 1
    assert '"passed": false' in capsys.readouterr().out


@pytest.mark.parametrize("host", ["ce.judge0.com", "judge0.com", "CE.JUDGE0.COM."])
def test_application_adapter_rejects_known_public_judge0_endpoint(monkeypatch, host):
    module = load_script("runner-selftest.py")
    monkeypatch.setenv("CODE_RUNNER_URL", f"https://{host}")
    monkeypatch.setenv("CODE_RUNNER_TOKEN", "test-only-token")
    with pytest.raises(module.code_runner.RunnerUnavailable):
        module.code_runner.configuration()


def test_default_selftest_uses_only_fixed_loopback_mock_and_restores_environment(monkeypatch, capsys):
    module = load_script("runner-selftest.py")
    # The suite blocks all runner HTTP by default. Permit this owned loopback only.
    class LoopbackOnly:
        def __init__(self, opener):
            self.opener = opener

        def open(self, request, **kwargs):
            assert urlsplit(request.full_url).hostname == "127.0.0.1"
            return self.opener.open(request, **kwargs)

    monkeypatch.setattr(module.code_runner, "build_opener", lambda *handlers: LoopbackOnly(build_opener(*handlers)))
    monkeypatch.setenv("CODE_RUNNER_URL", "https://must-not-be-contacted.invalid")
    monkeypatch.setenv("CODE_RUNNER_TOKEN", "must-not-be-sent")
    assert module.main([]) == 0
    output = capsys.readouterr().out
    assert '"mode": "mock"' in output
    assert '"executed_source": false' in output
    assert '"passed": true' in output
    assert "must-not-be" not in output
    assert module.os.environ["CODE_RUNNER_URL"] == "https://must-not-be-contacted.invalid"
    assert module.os.environ["CODE_RUNNER_TOKEN"] == "must-not-be-sent"


def test_startup_overlays_suppress_environment_and_payload_logs():
    # Source contract only: no Docker or Ruby runtime is installed for these checks.
    compose = (ROOT / "deploy/runner/compose.yaml").read_text(encoding="utf-8")
    for name in ("server", "workers"):
        script = (ROOT / f"deploy/runner/safe-{name}.sh").read_text(encoding="utf-8")
        assert "sudo install -m 600 /dev/null /api/environment" in script
        assert "export | sudo tee /api/environment >/dev/null" in script
        assert "export RAILS_LOG_TO_STDOUT=" in script
        assert f"/api/oy-safe-{name}.sh" in compose
    initializer = (ROOT / "deploy/runner/oy_logging.rb").read_text(encoding="utf-8")
    assert "Logger.new(File::NULL)" in initializer
    assert "ActiveRecord::Base.logger = nil" in initializer
    assert ":source_code" in initializer and ":stdin" in initializer
    assert compose.count("./oy_logging.rb:/api/config/initializers/oy_logging.rb:ro") == 2
