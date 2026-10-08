"""Offline shape checks use fabricated data and never initialize payment SDKs."""
import base64
import builtins
import importlib.util
import json
from pathlib import Path
import socket
from types import SimpleNamespace

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "deploy/bin/check_payments.py"


def load_script(monkeypatch):
    original_import = builtins.__import__
    def guarded_import(name, *args, **kwargs):
        assert name.split(".")[0] not in {"payments", "payment_channels", "db", "dotenv", "alipay", "wechatpayv3"}
        return original_import(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", guarded_import)
    def forbidden(*args, **kwargs):
        pytest.fail("Offline payment checks must not open network sockets or resolve DNS")
    monkeypatch.setattr(socket, "socket", forbidden)
    monkeypatch.setattr(socket, "getaddrinfo", forbidden)
    spec = importlib.util.spec_from_file_location("offline_payment_check", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module.metadata, "version", lambda name: "fixture-metadata-only")
    return module


def fixtures():
    # Deliberately not DER/RSA keys. A shape pass must not imply cryptographic validity.
    body = base64.b64encode(b"inert-offline-fixture" * 20).decode("ascii")
    private = "-----BEGIN PRIVATE KEY-----\n" + body + "\n-----END PRIVATE KEY-----"
    public = "-----BEGIN PUBLIC KEY-----\n" + body + "\n-----END PUBLIC KEY-----"
    return {"PAYMENTS_MOCK_ENABLED": "0", "ALIPAY_SANDBOX": "0", "ALIPAY_APP_ID": "2088123456789012", "ALIPAY_SELLER_ID": "2088123456789013",
        "ALIPAY_PRIVATE_KEY": private, "ALIPAY_PUBLIC_KEY": public, "ALIPAY_NOTIFY_URL": "https://payments.owned-domain.net/api/payments/alipay/callback",
        "WECHAT_APP_ID": "wx0123456789abcdef", "WECHAT_MCH_ID": "1234567890", "WECHAT_CERT_SERIAL_NO": "ABCDEF0123456789ABCDEF0123456789",
        "WECHAT_PRIVATE_KEY": private, "WECHAT_PUBLIC_KEY": public, "WECHAT_PUBLIC_KEY_ID": "PUB_KEY_ID_01234567890123456789",
        "WECHAT_API_V3_KEY": "aB3dE6gH9jK2mN5pQ8sT1vW4yZ7cF0iL", "WECHAT_NOTIFY_URL": "https://payments.owned-domain.net/api/payments/wechat/callback"}


def test_default_reads_only_selected_fixed_environment_and_no_files(monkeypatch, capsys):
    module = load_script(monkeypatch)
    values = fixtures()
    permitted = {"PAYMENTS_MOCK_ENABLED", *module.FIELDS["alipay"]}
    class Environment(dict):
        def get(self, key, default=None):
            assert key in permitted
            return super().get(key, default)
    # Scope this strict guard to the script's configuration reads. argparse's
    # locale/terminal handling still legitimately uses the real standard os.
    monkeypatch.setattr(module, "os", SimpleNamespace(environ=Environment(values)))
    def forbidden(*args, **kwargs):
        pytest.fail("Default checks must not read .env or configuration files")
    monkeypatch.setattr(Path, "open", forbidden)
    assert module.main(["--channel", "alipay"]) == 0
    output = capsys.readouterr().out
    assert "ALIPAY_APP_ID" in output and "仅离线" in output
    assert values["ALIPAY_APP_ID"] not in output and "BEGIN PRIVATE" not in output and "owned-domain.net" not in output


def test_explicit_json_does_not_fall_back_to_process_environment(tmp_path, monkeypatch, capsys):
    module = load_script(monkeypatch)
    path = tmp_path / "payment-check.json"
    path.write_text(json.dumps(fixtures()), encoding="utf-8")
    monkeypatch.setattr(module, "os", SimpleNamespace(environ={}))
    assert module.main(["--config-json", str(path)]) == 0
    assert "不验证商户资质" in capsys.readouterr().out


@pytest.mark.parametrize("content", ['{"ALIPAY_APP_ID":"secret-first","ALIPAY_APP_ID":"secret-second"}', '{"SECRET_UNKNOWN":"secret-value"}', '{"PAYMENTS_MOCK_ENABLED":false}', '[]', '{"PAYMENTS_MOCK_ENABLED":NaN}', '{"ALIPAY_APP_ID":"\\ud800"}'])
def test_bad_json_is_generic_and_never_echoes_values(content, tmp_path, monkeypatch, capsys):
    module = load_script(monkeypatch)
    path = tmp_path / "invalid.json"
    path.write_text(content, encoding="utf-8")
    assert module.main(["--config-json", str(path)]) == 2
    output = capsys.readouterr().out
    assert "secret-" not in output and "SECRET_UNKNOWN" not in output and str(path) not in output


def test_oversized_json_and_env_or_unc_paths_are_rejected_before_read(tmp_path, monkeypatch):
    module = load_script(monkeypatch)
    path = tmp_path / "large.json"
    path.write_bytes(b" " * (module.MAX_BYTES + 1))
    with pytest.raises(module.ConfigError, match="64 KiB"):
        module.read_json(path)
    def forbidden(*args, **kwargs):
        pytest.fail("Forbidden paths must never be opened")
    monkeypatch.setattr(Path, "open", forbidden)
    for name in (".env", ".env.prod", r"\\server\share\payment.json"):
        with pytest.raises(module.ConfigError):
            module.read_json(name)


@pytest.mark.parametrize("kind", ["symlink", "junction"])
def test_relative_json_checks_complete_working_directory_ancestors(kind, tmp_path, monkeypatch):
    module = load_script(monkeypatch)
    ancestor = tmp_path / "linked-ancestor"
    working = ancestor / "working"
    working.mkdir(parents=True)
    (working / "payment.json").write_text(json.dumps(fixtures()), encoding="utf-8")
    monkeypatch.chdir(working)
    original_symlink = Path.is_symlink
    original_junction = getattr(Path, "is_junction", lambda path: False)
    monkeypatch.setattr(Path, "is_symlink", lambda path: (kind == "symlink" and path == ancestor) or original_symlink(path))
    monkeypatch.setattr(Path, "is_junction", lambda path: (kind == "junction" and path == ancestor) or original_junction(path), raising=False)
    def forbidden(*args, **kwargs):
        pytest.fail("A linked cwd ancestor must be rejected before opening JSON")
    monkeypatch.setattr(Path, "open", forbidden)
    with pytest.raises(module.ConfigError, match="符号链接或目录联接"):
        module.read_json("payment.json")


def test_deep_json_returns_generic_error_without_traceback(tmp_path, monkeypatch, capsys):
    module = load_script(monkeypatch)
    path = tmp_path / "nested.json"
    path.write_text("[" * 2000 + '"secret-nested-value"' + "]" * 2000, encoding="utf-8")
    assert path.stat().st_size < module.MAX_BYTES
    assert module.main(["--config-json", str(path)]) == 2
    output = capsys.readouterr().out
    assert "配置 JSON" in output
    assert all(word not in output for word in ("secret-nested-value", "Traceback", "RecursionError", str(path)))


@pytest.mark.parametrize("suffix", ["?secret=query", "#fragment", "/extra", "?"])
def test_callback_must_match_exact_path_without_query_or_fragment(suffix, monkeypatch):
    module = load_script(monkeypatch)
    assert not module.callback_shape(fixtures()["ALIPAY_NOTIFY_URL"] + suffix, "alipay")


@pytest.mark.parametrize("host", ["localhost", "127.0.0.1", "127.1", "[::1]", "192.0.2.1", "224.0.0.1", "example.com", "notebook.example.com", "app.invalid", "private.internal"])
def test_local_and_documentation_callback_hosts_are_rejected(host, monkeypatch):
    module = load_script(monkeypatch)
    assert not module.callback_shape("https://" + host + "/api/payments/wechat/callback", "wechat")
    assert not module.callback_shape("https://secret@merchant.net/api/payments/wechat/callback", "wechat")
    assert not module.callback_shape("http://merchant.net/api/payments/wechat/callback", "wechat")


def test_mock_and_sandbox_flags_must_be_explicit_and_valid(monkeypatch):
    module = load_script(monkeypatch)
    values = fixtures()
    for key, bad in (("PAYMENTS_MOCK_ENABLED", "1"), ("PAYMENTS_MOCK_ENABLED", ""), ("ALIPAY_SANDBOX", ""), ("ALIPAY_SANDBOX", "true")):
        changed = {**values, key: bad}
        assert not dict(module.check(changed, ("alipay",)))[key]
    assert module.field_shape("ALIPAY_SANDBOX", "1", "alipay")


def test_pem_and_v3_key_checks_are_shape_only(monkeypatch):
    module = load_script(monkeypatch)
    values = fixtures()
    assert module.pem_shape(values["ALIPAY_PRIVATE_KEY"].replace("\n", "\\n"), True)
    assert not module.pem_shape(values["ALIPAY_PUBLIC_KEY"], True)
    assert not module.pem_shape("-----BEGIN PRIVATE KEY-----\ninvalid!\n-----END PRIVATE KEY-----", True)
    assert not module.field_shape("WECHAT_API_V3_KEY", "汉" * 32, "wechat")
    assert not module.field_shape("ALIPAY_APP_ID", "YOUR_APP_ID", "alipay")
    assert not module.field_shape("ALIPAY_APP_ID", "示例商户", "alipay")


def test_sdk_metadata_failure_is_generic_without_import_or_error_echo(monkeypatch, capsys):
    module = load_script(monkeypatch)
    values = fixtures()
    monkeypatch.setattr(module, "os", SimpleNamespace(environ=values))
    def missing(name):
        raise RuntimeError("secret-metadata-error")
    monkeypatch.setattr(module.metadata, "version", missing)
    assert module.main(["--channel", "wechat"]) == 2
    output = capsys.readouterr().out
    assert "wechatpayv3" in output and "secret-metadata-error" not in output
