# 依赖版本

- `requirements.txt`：人改的那份，只写直接依赖和允许的版本范围。
- `requirements.lock`：从 `requirements.txt` 解析出的**全部**依赖（含间接依赖），每个都钉死 `==`。
  Dockerfile、CI、`requirements-dev.txt` 都从它装，所以服务器每次 `update.sh` 重建镜像装到的是同一套版本，
  不会因为某个间接依赖悄悄发了新版而在服务器上出问题。
- `tests/test_requirements_lock.py` 守着：`requirements.txt` 里的每个包都必须在锁文件里，且钉的版本落在允许范围内。

## 改依赖之后重新生成锁文件

改完 `requirements.txt` 后，用 Python 3.13 跑（国内网络连不上 PyPI 时设镜像；`PIP_INDEX_URL` 要用环境变量，
因为 `python-alipay-sdk` 只有源码包，构建它的子进程也得走镜像）：

```bash
PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple \
  python -m pip install --dry-run --ignore-installed --quiet --report lock-report.json -r requirements.txt
```

再把 `lock-report.json` 里 `install` 列表的每一项写成 `名字==版本` 一行（按名字排序），
只在 Windows 上才需要的包（目前只有 `colorama`）加上 ` ; sys_platform == "win32"`。
`lock-report.json` 放在系统临时目录，不要留在仓库里。生成后跑一遍全量测试，CI 的 Linux 环境会按锁文件再装一次验证。
