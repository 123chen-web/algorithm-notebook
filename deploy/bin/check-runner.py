#!/usr/bin/env python3
"""Read two explicitly supplied runner files; never load .env or contact a service."""
import argparse
import ipaddress
import json
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1] / "runner"
FILE_LIMIT = 65536
REQUIRED_CONFIG = {
    "JUDGE0_TELEMETRY_ENABLE": "false", "ENABLE_WAIT_RESULT": "false",
    "ENABLE_COMPILER_OPTIONS": "false", "ENABLE_COMMAND_LINE_ARGUMENTS": "false",
    "ENABLE_BATCHED_SUBMISSIONS": "false", "ENABLE_CALLBACKS": "false",
    "ENABLE_ADDITIONAL_FILES": "false", "ENABLE_SUBMISSION_DELETE": "false",
    "AUTHN_HEADER": "X-Auth-Token", "COUNT": "2", "MAX_QUEUE_SIZE": "10",
    "CPU_TIME_LIMIT": "2", "MAX_CPU_TIME_LIMIT": "2", "CPU_EXTRA_TIME": "0.5",
    "MAX_CPU_EXTRA_TIME": "0.5", "WALL_TIME_LIMIT": "5", "MAX_WALL_TIME_LIMIT": "5",
    "MEMORY_LIMIT": "128000", "MAX_MEMORY_LIMIT": "128000",
    "STACK_LIMIT": "64000", "MAX_STACK_LIMIT": "64000",
    "MAX_PROCESSES_AND_OR_THREADS": "16", "MAX_MAX_PROCESSES_AND_OR_THREADS": "16",
    "ENABLE_PER_PROCESS_AND_THREAD_TIME_LIMIT": "false",
    "ALLOW_ENABLE_PER_PROCESS_AND_THREAD_TIME_LIMIT": "false",
    "ENABLE_PER_PROCESS_AND_THREAD_MEMORY_LIMIT": "false",
    "ALLOW_ENABLE_PER_PROCESS_AND_THREAD_MEMORY_LIMIT": "false",
    "MAX_FILE_SIZE": "64", "MAX_MAX_FILE_SIZE": "64",
    "NUMBER_OF_RUNS": "1", "MAX_NUMBER_OF_RUNS": "1",
    "ALLOW_ENABLE_NETWORK": "false", "ENABLE_NETWORK": "false",
    "REDIS_HOST": "redis", "POSTGRES_HOST": "db", "POSTGRES_DB": "judge0",
    "POSTGRES_USER": "judge0", "RAILS_ENV": "production",
}


class ConfigError(ValueError):
    pass


def read_settings(path):
    try:
        with Path(path).open("rb") as source:
            raw = source.read(FILE_LIMIT + 1)
        if len(raw) > FILE_LIMIT:
            raise ConfigError("配置文件超过 64 KiB。")
        text = raw.decode("utf-8-sig")
    except (OSError, UnicodeError):
        raise ConfigError("无法读取指定的 UTF-8 运行服务配置文件。") from None
    result = {}
    for number, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        match = re.fullmatch(r"([A-Z][A-Z0-9_]*)=([A-Za-z0-9_.:/@-]*)", line)
        if not match:
            raise ConfigError(f"第 {number} 行不是受支持的普通 KEY=value；不接受 shell 展开或命令。")
        key, value = match.groups()
        if key in result:
            raise ConfigError(f"{key} 重复定义。")
        result[key] = value
    return result


def validate(conf, images):
    errors = []
    for key in set(conf) - set(REQUIRED_CONFIG) - {"AUTHN_TOKEN", "REDIS_PASSWORD", "POSTGRES_PASSWORD"}:
        errors.append(f"{key} 不是此固定配置支持的字段。")
    allowed_images = {"JUDGE0_VERSION", "POSTGRES_TAG", "REDIS_TAG", "CADDY_TAG", "JUDGE0_SHA256",
                      "POSTGRES_SHA256", "REDIS_SHA256", "CADDY_SHA256", "RUNNER_DOMAIN", "OY_APP_EGRESS_IP"}
    for key in set(images) - allowed_images:
        errors.append(f"{key} 不是此镜像与网关配置支持的字段。")
    for key, expected in REQUIRED_CONFIG.items():
        if conf.get(key) != expected:
            errors.append(f"{key} 必须符合交付的固定限制。")
    secrets = []
    for key in ("AUTHN_TOKEN", "REDIS_PASSWORD", "POSTGRES_PASSWORD"):
        value = conf.get(key, "")
        if (not re.fullmatch(r"[A-Za-z0-9_-]{32,128}", value) or len(set(value)) < 12
                or any(marker in value.upper() for marker in ("REPLACE", "CHANGE_ME", "PLACEHOLDER", "EXAMPLE", "TEST_ONLY"))):
            errors.append(f"{key} 必须单独生成至少 32 字符的随机值；不接受占位值。")
        secrets.append(value)
    if len(set(secrets)) != len(secrets):
        errors.append("AUTHN_TOKEN、REDIS_PASSWORD、POSTGRES_PASSWORD 必须互不相同。")
    for key in ("JUDGE0_VERSION", "POSTGRES_TAG", "REDIS_TAG", "CADDY_TAG"):
        value = images.get(key, "")
        if not re.fullmatch(r"\d+\.\d+(?:\.\d+)?(?:-alpine)?", value):
            errors.append(f"{key} 必须是显式数字版本，不能使用 latest 或占位值。")
    version = images.get("JUDGE0_VERSION", "")
    if re.fullmatch(r"\d+\.\d+\.\d+", version):
        if tuple(map(int, version.split("."))) < (1, 13, 1):
            errors.append("JUDGE0_VERSION 不能使用存在已知严重漏洞的 1.13.0 或更早版本。")
    else:
        errors.append("JUDGE0_VERSION 必须是完整的三段版本号。")
    if images.get("POSTGRES_TAG", "").split(".")[0] not in ("16", "17"):
        errors.append("POSTGRES_TAG 此模板仅支持已核对数据目录布局的 16/17 主版本。")
    if images.get("CADDY_TAG", "").split(".")[0] != "2":
        errors.append("CADDY_TAG 此网关模板仅支持 Caddy 2。")
    for key in ("JUDGE0_SHA256", "POSTGRES_SHA256", "REDIS_SHA256", "CADDY_SHA256"):
        value = images.get(key, "")
        if not re.fullmatch(r"[a-f0-9]{64}", value) or len(set(value)) < 2:
            errors.append(f"{key} 必须填写由站长审核的真实镜像 digest。")
    domain = images.get("RUNNER_DOMAIN", "")
    labels = domain.split(".")
    if (len(domain) > 253 or len(labels) < 2
            or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in labels)
            or labels[-1] in ("invalid", "example", "test", "localhost")
            or any(domain == reserved or domain.endswith("." + reserved)
                   for reserved in ("example.com", "example.net", "example.org"))
            or domain == "judge0.com" or domain.endswith(".judge0.com")):
        errors.append("RUNNER_DOMAIN 必须填写站长自己的 HTTPS 域名，不接受示例域名或公开 Judge0 服务。")
    try:
        ipaddress.ip_address(domain)
    except ValueError:
        pass
    else:
        errors.append("RUNNER_DOMAIN 必须填写域名，不能使用 IP 地址。")
    try:
        address = ipaddress.ip_address(images.get("OY_APP_EGRESS_IP", ""))
        private_ranges = tuple(ipaddress.ip_network(value) for value in
                               ("10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "fc00::/7"))
        doc_ranges = tuple(ipaddress.ip_network(value) for value in
                           ("192.0.2.0/24", "198.51.100.0/24", "203.0.113.0/24", "2001:db8::/32", "3fff::/20"))
        if (address.is_loopback or address.is_multicast or address.is_unspecified or address.is_link_local
                or address.is_reserved or any(address in network for network in doc_ranges)
                or not (address.is_global or any(address in network for network in private_ranges))):
            raise ValueError()
    except ValueError:
        errors.append("OY_APP_EGRESS_IP 必须是网站的固定公网或 RFC1918/ULA 私网 IP，不接受网段或文档占位地址。")
    return errors


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--conf", type=Path, default=ROOT / "judge0.conf.example")
    parser.add_argument("--images", type=Path, default=ROOT / "images.env.example")
    args = parser.parse_args(argv)
    try:
        errors = validate(read_settings(args.conf), read_settings(args.images))
    except ConfigError as error:
        errors = [str(error)]
    print(json.dumps({"mode": "offline", "passed": not errors, "errors": errors,
                      "scope": "配置格式与固定限制检查；未连接沙箱，未验证镜像内容、DNS、TLS 或隔离。"}, ensure_ascii=False))
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
