#!/usr/bin/env python3
"""Offline payment configuration shape checks; never initialize a payment client."""
import argparse
import base64
from importlib import metadata
import ipaddress
import json
import os
from pathlib import Path
import re
from urllib.parse import urlsplit


FIELDS = {
    "alipay": ("ALIPAY_APP_ID", "ALIPAY_SELLER_ID", "ALIPAY_PRIVATE_KEY", "ALIPAY_PUBLIC_KEY", "ALIPAY_NOTIFY_URL", "ALIPAY_SANDBOX"),
    "wechat": ("WECHAT_APP_ID", "WECHAT_MCH_ID", "WECHAT_CERT_SERIAL_NO", "WECHAT_PRIVATE_KEY", "WECHAT_PUBLIC_KEY", "WECHAT_PUBLIC_KEY_ID", "WECHAT_API_V3_KEY", "WECHAT_NOTIFY_URL"),
}
PACKAGES = {"alipay": "python-alipay-sdk", "wechat": "wechatpayv3"}
ALLOWED = {"PAYMENTS_MOCK_ENABLED", "PAYMENTS_MOCK_SECRET", *FIELDS["alipay"], *FIELDS["wechat"]}
MAX_BYTES = 65536
DISCLAIMER = "仅离线格式检查：不验证商户资质、密钥签名匹配、回调公网可达或真实交易；不会读取 .env、初始化 SDK 或联网。"


class ConfigError(Exception):
    pass


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ConfigError("配置 JSON 含重复字段；不会回显字段内容。")
        result[key] = value
    return result


def invalid_constant(value):
    raise ConfigError("配置 JSON 不接受非标准数值。")


def read_json(filename):
    if str(filename).startswith(("\\\\", "//")):
        raise ConfigError("只接受明确指定的本地 .json 文件，不读取 .env 或网络路径。")
    # absolute() exposes every cwd ancestor without resolving away links.
    path = Path(filename).absolute()
    if str(path).startswith(("\\\\", "//")) or path.suffix.lower() != ".json":
        raise ConfigError("只接受明确指定的本地 .json 文件，不读取 .env 或网络路径。")
    for part in (path, *path.parents):
        if part.is_symlink() or (hasattr(part, "is_junction") and part.is_junction()):
            raise ConfigError("配置 JSON 不能经过符号链接或目录联接。")
    if not path.is_file():
        raise ConfigError("指定配置必须是已存在的普通 JSON 文件。")
    with path.open("rb") as handle:
        raw = handle.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ConfigError("配置 JSON 超过 64 KiB。")
    try:
        values = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=unique_object, parse_constant=invalid_constant)
        if type(values) is not dict or any(key not in ALLOWED or type(value) is not str or len(value) > 16384 for key, value in values.items()):
            raise ConfigError("配置 JSON 只接受白名单支付字段及字符串值；不会回显未知字段或值。")
        for value in values.values():
            value.encode("utf-8", errors="strict")
            if "\x00" in value:
                raise ConfigError("配置 JSON 含无效文本。")
    except (UnicodeError, ValueError, RecursionError):
        raise ConfigError("配置 JSON 必须是有效 UTF-8 JSON 文本。") from None
    return values


def configured(value):
    if type(value) is not str or not value.strip() or len(value) > 16384:
        return False
    try:
        value.encode("utf-8", errors="strict")
    except UnicodeError:
        return False
    return not (value.strip().lower() in {"todo", "changeme", "replace_me", "placeholder"}
        or re.match(r"(?i)^(your|replace|example|sample)[_-]", value)
        or any(marker in value for marker in ("你的", "示例", "填入", "...", "<", ">", "\x00")))


def pem_shape(value, private):
    if not configured(value):
        return False
    labels = ("PRIVATE KEY", "RSA PRIVATE KEY") if private else ("PUBLIC KEY", "RSA PUBLIC KEY")
    lines = value.replace("\\n", "\n").strip().splitlines()
    if len(lines) < 3:
        return False
    for label in labels:
        if lines[0] == "-----BEGIN " + label + "-----" and lines[-1] == "-----END " + label + "-----":
            try:
                decoded = base64.b64decode("".join(lines[1:-1]), validate=True)
                return 32 <= len(decoded) <= 12288
            except (ValueError, UnicodeError):
                return False
    return False


def callback_shape(value, channel):
    if not configured(value) or not value.isascii() or any(ord(char) <= 32 or ord(char) == 127 for char in value):
        return False
    try:
        parsed = urlsplit(value)
        host = (parsed.hostname or "").lower()
        if parsed.scheme != "https" or parsed.username is not None or parsed.password is not None or "?" in value or "#" in value:
            return False
        if parsed.path != "/api/payments/" + channel + "/callback" or not host or "%" in host:
            return False
        if parsed.port is not None and not 1 <= parsed.port <= 65535:
            return False
        try:
            address = ipaddress.ip_address(host)
            return address.is_global and not address.is_multicast
        except ValueError:
            blocked = ("localhost", "local", "internal", "invalid", "test", "example", "home.arpa", "example.com", "example.net", "example.org")
            if any(host == name or host.endswith("." + name) for name in blocked):
                return False
            labels = host.split(".")
            return len(host) <= 253 and len(labels) >= 2 and bool(re.fullmatch(r"[A-Za-z]{2,63}", labels[-1])) and all(re.fullmatch(r"(?!-)[a-z0-9-]{1,63}(?<!-)", label) for label in labels)
    except ValueError:
        return False


def field_shape(key, value, channel):
    if key == "ALIPAY_SANDBOX":
        return value in ("0", "1")
    if key.endswith("PRIVATE_KEY") or key.endswith("PUBLIC_KEY"):
        return pem_shape(value, key.endswith("PRIVATE_KEY"))
    if key.endswith("NOTIFY_URL"):
        return callback_shape(value, channel)
    if key == "WECHAT_API_V3_KEY":
        return configured(value) and len(value) == 32 and all(33 <= ord(char) <= 126 for char in value)
    return configured(value) and bool(re.fullmatch(r"[A-Za-z0-9_]{4,128}", value))


def check(values, channels):
    results = [("PAYMENTS_MOCK_ENABLED", values.get("PAYMENTS_MOCK_ENABLED") == "0")]
    for channel in channels:
        results.extend((key, field_shape(key, values.get(key, ""), channel)) for key in FIELDS[channel])
        try:
            installed = bool(metadata.version(PACKAGES[channel]))
        except Exception:
            # Package metadata errors may contain filesystem/configuration data.
            installed = False
        results.append((PACKAGES[channel], installed))
    return results


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--channel", choices=("alipay", "wechat", "all"), default="all")
    parser.add_argument("--config-json", help="Explicit local UTF-8 JSON, at most 64 KiB; values must be strings")
    args = parser.parse_args(argv)
    channels = tuple(FIELDS) if args.channel == "all" else (args.channel,)
    print(DISCLAIMER)
    try:
        if args.config_json:
            values = read_json(args.config_json)
        else:
            keys = {"PAYMENTS_MOCK_ENABLED", *(key for channel in channels for key in FIELDS[channel])}
            values = {key: os.environ.get(key, "") for key in keys}
        results = check(values, channels)
        for key, passed in results:
            print(key + ": " + ("格式通过" if passed else "未通过或缺失"))
        passed = all(passed for _, passed in results)
        print("离线检查通过，仍需另行核实真实支付条件。" if passed else "离线检查未通过；不会自动修改配置或安装依赖。")
        return 0 if passed else 2
    except ConfigError as error:
        print(str(error))
    except (OSError, UnicodeError, ValueError):
        print("无法读取或检查指定配置；不会回显路径、配置值或原始异常。")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
