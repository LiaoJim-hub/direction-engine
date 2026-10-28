# -*- coding: utf-8 -*-
"""文本完整性守卫（2026-10-01）。

**起因是一次真实的静默损坏**：`docs/scenario-card.md` 里所有被引号标注的术语
内容整体变成了一个 U+0001（SOH）——29 处，分布在 24 行，连 H3 标题里的
`extra="forbid"` 都只剩 `extra=""`。

这次损坏的可怕之处不在量，在**它没有任何一处会喊**：

  · 文件仍是合法 UTF-8（U+0001 是合法码点，不是解码错误）；
  · Markdown 照样渲染，readme 照样显示，只是关键术语全空；
  · `git diff` 里它就是一行普通的中文改动；
  · 目录里唯一的征兆是 Read 工具显示 `""`——而空引号看起来像排版习惯。

发现它靠的是人工**逐码点统计**，不是任何自动化。这个测试就是把那次统计固化，
免得下一个人还得靠眼睛。

与运营仓 `test_artifact_integrity.py` 对称：那边守 JSON 能不能被解析、
副本有没有跟上公开仓；这边守文本里有没有**被吞掉的内容**，
以及有没有把仓库**钉死在一台机器的目录布局上**（写死带盘符的绝对路径）。
"""
import pathlib
import re

TEXT_SUFFIXES = {".md", ".py", ".json", ".toml", ".cfg", ".txt", ".yml", ".yaml",
                 ".cff", ".in", ".template"}
SKIP_DIRS = {".git", "__pycache__", ".pytest_cache", ".venv", "venv",
             "build", "dist", ".mypy_cache", ".ruff_cache"}

# 允许出现的控制字符只有这三个；其余（含 U+0001、U+0000、U+001B、退格…）
# 都不是人手写出来的，一律视为"某次生成或替换把内容吞掉了"。
ALLOWED_CONTROL = "\t\n\r"

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _text_files():
    for p in sorted(ROOT.rglob("*")):
        if not p.is_file():
            continue
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        if p.suffix.lower() not in TEXT_SUFFIXES:
            continue
        yield p


def test_repo_actually_has_text_to_scan():
    """探针自检：扫不到东西时先怀疑路径，而不是宣布"干净"。

    没有这条，`TEXT_SUFFIXES` 写错一个字母（或 ROOT 算错一层）就会让下面那条
    测试永远绿着通过——**未被检出的原因是被检对象为空**。
    """
    files = list(_text_files())
    assert len(files) >= 50, (
        f"只扫到 {len(files)} 个文本文件，明显偏少——先确认 ROOT 与后缀集，"
        f"否则所谓「干净」没有意义")


def test_no_control_characters_in_any_text_file():
    offenders = {}
    for path in _text_files():
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue                      # 二进制误入或读取失败：另由别的守卫管
        for ln, line in enumerate(text.splitlines(), 1):
            bad = [f"U+{ord(ch):04X}" for ch in line
                   if ord(ch) < 0x20 and ch not in ALLOWED_CONTROL]
            if bad:
                offenders.setdefault(str(path.relative_to(ROOT)), []).append(
                    f"  第 {ln} 行：{sorted(set(bad))} —— {line.strip()[:80]}")

    assert not offenders, (
        "以下文件含控制字符（多半是某次生成/替换把引号内的内容吞掉了）：\n"
        + "\n".join(f"{f}\n" + "\n".join(v) for f, v in offenders.items())
        + "\n\n注：U+0001 这类字符**不会**让文件变成非法 UTF-8，Markdown 也照样"
          "渲染——它只会让内容安静地消失。")


# --------------------------------------------------------------------------
# 路径可移植性：仓内不得写死本机绝对路径
# --------------------------------------------------------------------------
# 起因：2026-10-01 三仓搬家（`工程化落地/direction-engine` →
# `工程化落地/方向引擎工程商业/direction-engine`），两个探针脚本里写死的
# `E:/.../工程化落地/direction-engine` 全部失效，脚本一跑就 ModuleNotFoundError
# ——而在此之前没有任何自动化会喊。
#
# **为什么只查「写了没有」，不查「路径存在不存在」**：本仓在 GitHub CI 上跑，
# 那里既没有 E 盘也没有 D:\workbuddy-tools。查存在性会变成"在本机绿、在 CI 红"
# （或反过来）——那不是守卫，是环境探测。真正要防的是**写死**这件事本身，
# 它与在哪台机器上运行无关。
#
# 正则要防一个陷阱：`https://` 里的 `s://` 长得像盘符路径。故要求盘符**前面
# 不是字母数字**（`(?<![A-Za-z0-9])`）——URL 里的盘符片段前面总还有字母。
ABS_PATH = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]:[\\/]")

# 唯一豁免：`Z:` 是不存在于任何真实机器的虚拟盘符，被
# `direction_drift/coexistence.py` 用来构造「返回路径悬空」的夹具。
# 它钉不死仓库，故不属本守卫要防的形态。
ABS_PATH_ALLOW = ("Z:/", "Z:\\")


def test_no_hardcoded_local_absolute_paths():
    """仓内不得写死带盘符的本机绝对路径。

    要定位目录请从 `__file__` 向上找（做法见运营仓探针脚本里的 `_locate()`），
    或走 `pathlib.Path(__file__).parents[n]`。**写死盘符 = 把仓库钉死在
    一台机器的目录布局上**，搬家时它会静默失效，而测试、文档、CI 都不会告诉你。
    """
    offenders = {}
    self_name = pathlib.Path(__file__).name
    for path in _text_files():
        if path.name == self_name:
            continue                      # 本文件的正则与豁免表里必然含盘符模式
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for ln, line in enumerate(text.splitlines(), 1):
            for m in ABS_PATH.finditer(line):
                if line[m.start():m.start() + 3] in ABS_PATH_ALLOW:
                    continue
                offenders.setdefault(str(path.relative_to(ROOT)), []).append(
                    f"  第 {ln} 行：{line.strip()[:90]}")
                break

    assert not offenders, (
        "以下文件写死了本机绝对路径（含盘符）：\n"
        + "\n".join(f"{f}\n" + "\n".join(v) for f, v in offenders.items())
        + "\n\n改用相对路径，或从 `__file__` 向上定位（参考运营仓探针脚本的 "
          "`_locate()`）。这类路径在本机能跑、换台机器或搬个家就**静默失效**。")


def test_the_absolute_path_checker_can_fail():
    """核验器自检：证明上面那条真的会红，而不是一条永远绿的摆设。

    用一个**必然违规**的样本跑同一套判断逻辑。若这里不红，
    上面那条的绿灯一概不可采信。
    """
    sample = r'ROOT = "E:\2024年女装\project"'
    hits = [m.start() for m in ABS_PATH.finditer(sample)]
    assert hits, "核验器失灵：明明写着 E:\\... 却没被正则认出来"
    assert sample[hits[0]:hits[0] + 3] not in ABS_PATH_ALLOW, (
        "核验器失灵：真实盘符路径被豁免表放过了")

    # 反向对照：URL 与虚拟盘符**不该**被认成违规
    for ok in ("https://github.com/x", 'NodeDeclaration(ok, "Z:/abc.md")'):
        bad = [m for m in ABS_PATH.finditer(ok)
               if ok[m.start():m.start() + 3] not in ABS_PATH_ALLOW]
        assert not bad, f"核验器误报：{ok!r} 被判成写死路径（{bad}）"


# --------------------------------------------------------------------------
# UTF-8 BOM 守卫（2026-10-28 加）
# --------------------------------------------------------------------------
# 起因：2026-10-28 两次同一个坑 ——
#   ① 修一个源文件时用 PowerShell 的 `Set-Content -Encoding UTF8` 写回，
#      它在文件头加了 BOM（EF BB BF）⇒ 该文件的 `ast.parse` 直接抛
#      `SyntaxError: invalid non-printable character U+FEFF`。
#   ② 改 README 里一行测试数时用了同一个写法 ⇒ README 也带了 BOM。
#
# **为什么上面那条 `test_no_control_characters_in_any_text_file` 抓不到它**：
# 它查的是 `ord(ch) < 0x20` 的控制字符，而 **U+FEFF 不在那个范围**（它是
# 0xFEFF）。故 BOM 是**一个现有守卫抓不到的形态**——这正是本条存在的理由。
#
# **为什么 BOM 本身是错的**（而不是"无害的三字节"）：
#   · 它让 `python xxx.py` 在某些工具链下报 SyntaxError（见①的实测）；
#   · 它让 `git diff` 把整个文件当成改过（第一行变了）；
#   · 它是**编辑器/工具链的指纹**，不是内容——而本仓不允许"内容之外的东西"
#     出现在文本件里（同 `test_no_hardcoded_local_absolute_paths` 的口径）。
BOM = b"\xef\xbb\xbf"


def test_no_utf8_bom_in_any_text_file():
    """任何文本件都不得以 UTF-8 BOM 开头。

    ★ 判法：按**字节**读，看前 3 字节是不是 EF BB BF —— 不走文本解码，
      因为 `read_text(encoding="utf-8")` 会把 BOM 当成一个 `\ufeff` 字符
      **静默留在正文里**，而正文里那个字符肉眼看不见。
    """
    offenders = []
    for path in _text_files():
        try:
            head = path.open("rb").read(3)
        except OSError:
            continue
        if head == BOM:
            offenders.append(str(path.relative_to(ROOT)))

    assert not offenders, (
        "以下文件以 UTF-8 BOM 开头：\n  "
        + "\n  ".join(offenders)
        + "\n\nBOM 会让 `python 文件.py` 在某些工具链下报 "
          "`SyntaxError: invalid non-printable character U+FEFF`（2026-10-28 实测），"
          "也会让 git 把整个文件当成改过。"
          "★ 用 `Path.write_bytes(text.encode('utf-8'))` 写回，不要用 "
          "PowerShell 的 `Set-Content -Encoding UTF8`（它会加 BOM）。")


def test_the_bom_checker_can_fail(tmp_path):
    """核验器自检：证明上面那条真会红，而不是一条永远绿的摆设。

    ★ 同本文件里 `test_the_absolute_path_checker_can_fail` 的口径：
      **若这里不红，上面那条的绿灯一概不可采信。**
    """
    with_bom = tmp_path / "with_bom.md"
    with_bom.write_bytes(BOM + b"# hello\n")
    assert with_bom.open("rb").read(3) == BOM, "核验器失灵：写进去的 BOM 读不出来"

    without_bom = tmp_path / "without_bom.md"
    without_bom.write_bytes(b"# hello\n")
    assert without_bom.open("rb").read(3) != BOM, "核验器误报：没有 BOM 被判成有"

    # ★★ 关键反向对照：文本层读法会**看不见** BOM，故本守卫必须走字节层
    text_layer = with_bom.read_text(encoding="utf-8")
    assert text_layer.startswith("\ufeff"), (
        "这一条不是在测守卫，是在记录那个坑：`read_text(utf-8)` 会把 BOM "
        "当成正文第一个字符静默留下——故字节层检查不可省")
    assert "\ufeff" not in without_bom.read_text(encoding="utf-8")
