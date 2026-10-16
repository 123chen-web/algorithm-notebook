"""requirements.lock 必须覆盖 requirements.txt 的每个直接依赖，且钉的版本落在允许范围内。"""
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

ROOT = Path(__file__).resolve().parent.parent


def _requirements(name):
    items = []
    for raw in (ROOT / name).read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if line and not line.startswith("-"):
            items.append(Requirement(line))
    return items


def test_lock_pins_every_package_exactly():
    for req in _requirements("requirements.lock"):
        specs = list(req.specifier)
        assert len(specs) == 1 and specs[0].operator == "==", f"{req} 必须是 == 钉死的版本"


def test_lock_covers_and_satisfies_requirements_txt():
    locked = {canonicalize_name(req.name): next(iter(req.specifier)).version for req in _requirements("requirements.lock")}
    for req in _requirements("requirements.txt"):
        name = canonicalize_name(req.name)
        assert name in locked, f"{req.name} 不在 requirements.lock 里，改了 requirements.txt 要重新生成锁文件"
        assert req.specifier.contains(locked[name], prereleases=True), (
            f"{req.name}=={locked[name]} 不满足 requirements.txt 里的 {req.specifier}")

