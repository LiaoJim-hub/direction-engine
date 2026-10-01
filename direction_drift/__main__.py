# -*- coding: utf-8 -*-
"""`python -m direction_drift` 等价于命令行 `de`。

存在的理由：CLI 不该要求先 `pip install` 才能用——克隆仓库的人应该能
直接 `python -m direction_drift --help` 看到它能做什么。
"""
from .cli import main

if __name__ == "__main__":                               # pragma: no cover
    raise SystemExit(main())
