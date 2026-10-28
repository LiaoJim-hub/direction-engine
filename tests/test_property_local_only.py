# -*- coding: utf-8 -*-
"""性质 ① 的可验证性：核心链路不联网、无遥测（2026-10-28）。

**起因**：README 的门面写着「**五条可验证的性质（这是本项目的门面，不是口号）**」，
而第一条是「**本地执行：核心链路不联网、无遥测**」。

2026-10-28 的盘点发现：**五条里只有 ③④ 各有一条名字对得上的测试；
① 一条都没有** —— 用 `socket` / `requests.` / `urllib.request` / `urlopen` /
`no_network` / `telemetry` / `offline` 在 `tests/` 里逐词搜，**各 0 文件**。

⇒ 故「可验证」那三个字，对 ① 来说是**一个承诺，不是一个读数**。本文件把它变成读数。

**为什么用「静态扫 import」而不是「跑起来看有没有发包」**：
后者要真装一个抓包器、要在 CI 上起一个假的服务端，成本高且会引入环境依赖；
而本性质要防的形态是**明确的**——核心链路里出现一个 http 客户端 import。
静态扫足够，且它**在任何机器上结果一致**（同 `test_text_integrity.py` 的口径：
查「写了没有」，不查「跑起来怎样」）。

**可选扩展是白名单，不是例外**：`[api]` / `[sbert]` / `[llm]` 三个 extra 本来就
允许联网（`api` 起 HTTP 服务、`sbert` 首次下载模型、`llm` 调外部模型）。
白名单把它们**显式列出来**，而不是靠"扫不到"蒙混——**列出来才能被审查**。
"""
import ast
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
PKG = ROOT / "direction_drift"

#: ★ 任何一条出现在核心链路的 import 里，都意味着「本地执行」这条性质被打破了。
#:   ★★ 只列**客户端/服务端网络库**——`http` 模块本身是标准库里的 HTTP 状态码与
#:   消息解析（`http.HTTPStatus`），不产生任何网络行为，故**不列**。
NETWORK_CLIENTS = (
    "requests",
    "urllib.request",     # ★ 注意：`urllib.parse` 不联网，故这里限定 `.request`
    "urllib3",
    "httpx",
    "aiohttp",
    "socket",
    "http.client",
    "websockets",
    "grpc",
)

#: ★ 遥测/埋点的常见包名。本仓一个都不用；列出来是为了让"无遥测"也可核。
TELEMETRY_PKGS = (
    "posthog",
    "sentry_sdk",
    "mixpanel",
    "amplitude",
    "segment",
    "datadog",
    "ddtrace",
    "opentelemetry",
)

#: ★★ 可选扩展 —— 它们**允许**联网，故不在本守卫的射程内。
#:   ★ 之所以要一个**显式清单**而不是"跳过 utils/ 与 api/"：清单能被审查，
#:   而"跳过某目录"会在下次加文件时**静默**把它也放过。
OPTIONAL_EXTRAS = {
    "api/server.py",          # [api] —— fastapi，起 HTTP 服务
    "utils/encoder.py",       # [sbert] —— 首次下载约 100MB 模型
    "utils/nli.py",           # 可选语义 NLI
}


def _py_files():
    """核心链路的 .py 文件（★ 排除可选扩展）。"""
    out = []
    for p in sorted(PKG.rglob("*.py")):
        rel = p.relative_to(PKG).as_posix()
        if rel in OPTIONAL_EXTRAS:
            continue
        out.append(p)
    return out


def _imported_modules(path):
    """用 `ast` 取 import 的顶层模块名 —— ★ 不扫字符串，避免把注释/文档里的
    「requests」误判成真的 import（那会让本守卫误报，最后被人关掉）。"""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    mods = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for a in node.names:
                mods.append(a.name)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:       # 相对 import 不涉及外部包
                mods.append(node.module)
    return mods


def _hits(mods, needles):
    """返回被 `needles` 命中的模块（★ 按前缀匹配，`urllib.request` 不会被
    `urllib.parse` 误伤）。"""
    out = []
    for m in mods:
        for n in needles:
            if m == n or m.startswith(n + "."):
                out.append(m)
                break
    return out


# --------------------------------------------------------------------------
# 探针自检：扫不到东西时先怀疑路径
# --------------------------------------------------------------------------
def test_local_only_scan_is_not_vacuous():
    """探针自检：扫到的文件数明显偏少时先怀疑路径，而不是宣布「干净」。

    没有这条，`PKG` 算错一层就会让下面两条永远绿着通过——
    **未被检出的原因是被检对象为空**（同 `test_text_integrity.py` 那条的口径）。
    """
    files = _py_files()
    assert len(files) >= 20, (
        f"只扫到 {len(files)} 个核心链路 .py 文件，明显偏少——先确认 PKG 路径，"
        f"否则所谓「不联网」没有意义")


# --------------------------------------------------------------------------
# 性质 ① 正测
# --------------------------------------------------------------------------
def test_core_chain_imports_no_network_client():
    """核心链路不得 import 任何网络客户端（性质 ① 的前半）。"""
    offenders = {}
    for path in _py_files():
        hit = _hits(_imported_modules(path), NETWORK_CLIENTS)
        if hit:
            offenders[path.relative_to(ROOT).as_posix()] = hit

    assert not offenders, (
        "以下核心链路文件 import 了网络客户端——README 第 1 条性质"
        "「核心链路不联网」因此不成立：\n"
        + "\n".join(f"  {f}: {v}" for f, v in offenders.items())
        + "\n\n若这是可选扩展，请把它加进本文件的 OPTIONAL_EXTRAS 清单"
          "（★ 清单能被审查，「跳过某目录」不能）。")


def test_core_chain_imports_no_telemetry_package():
    """核心链路不得 import 任何遥测/埋点包（性质 ① 的后半）。"""
    offenders = {}
    for path in _py_files():
        hit = _hits(_imported_modules(path), TELEMETRY_PKGS)
        if hit:
            offenders[path.relative_to(ROOT).as_posix()] = hit

    assert not offenders, (
        "以下核心链路文件 import 了遥测/埋点包——README 第 1 条性质"
        "「无遥测」因此不成立：\n"
        + "\n".join(f"  {f}: {v}" for f, v in offenders.items()))


# --------------------------------------------------------------------------
# 核验器自检：证明上面两条真的会红
# --------------------------------------------------------------------------
def test_the_local_only_checker_can_fail():
    """核验器自检：证明上面两条不是永远绿的摆设。

    ★ 同 `test_text_integrity.py::test_the_absolute_path_checker_can_fail` 的口径：
    **若这里不红，上面那两条的绿灯一概不可采信。**
    """
    # ① 必然违规的样本必须被认出
    assert _hits(["requests"], NETWORK_CLIENTS) == ["requests"], (
        "核验器失灵：`import requests` 没被认成网络客户端")
    assert _hits(["httpx"], NETWORK_CLIENTS) == ["httpx"], (
        "核验器失灵：`import httpx` 没被认成网络客户端")

    # ② 子模块前缀匹配要对（★ 这是最容易写错的一处）
    assert _hits(["urllib.request"], NETWORK_CLIENTS) == ["urllib.request"], (
        "核验器失灵：`urllib.request` 没被认出")

    # ③ 反向对照：**不该**被误报的
    assert _hits(["urllib.parse"], NETWORK_CLIENTS) == [], (
        "核验器误报：`urllib.parse` 是纯字符串解析，不联网")
    assert _hits(["http"], NETWORK_CLIENTS) == [], (
        "核验器误报：标准库 `http` 的 HTTPStatus 等不产生网络行为")
    assert _hits(["pathlib", "json", "numpy"], NETWORK_CLIENTS) == [], (
        "核验器误报：常见非网络库被认成了网络客户端")

    # ④ 遥测表也自检一次
    assert _hits(["sentry_sdk"], TELEMETRY_PKGS) == ["sentry_sdk"], (
        "核验器失灵：`sentry_sdk` 没被认成遥测包")
    assert _hits(["logging"], TELEMETRY_PKGS) == [], (
        "核验器误报：标准库 `logging` 不是遥测包")


def test_optional_extras_are_real_files():
    """白名单自检：`OPTIONAL_EXTRAS` 里的每条都必须真的存在。

    ★ 没有这条，改名或删文件之后白名单会静默失效——**而失效的白名单比没有
    白名单更坏**，因为它看起来还在管着事。
    """
    missing = [rel for rel in OPTIONAL_EXTRAS if not (PKG / rel).is_file()]
    assert not missing, (
        f"OPTIONAL_EXTRAS 里这些路径已不存在：{missing}——"
        f"改名或删除后请同步本清单，否则它会静默失效")
