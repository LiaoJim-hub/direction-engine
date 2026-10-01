# -*- coding: utf-8 -*-
"""文档一致性测试（v2.3.3 新增）。

起因：README 的版本指针一度停在 v2.3.1，而代码已是 v2.3.2——三处版本号手工
维护必然漏一处，而漏掉的那处恰好在门面上。本项目的所有纪律都建立在"文档说的
和代码做的一致"上，所以这条得由测试守，不靠人记得。

同时把原先手工跑的"文档自检"（相对链接存在性 + 代码围栏配对）并入 CI，
避免再出现"改文档时删掉一个围栏、渲染整段崩掉"这类无声损坏。
"""
import pathlib
import re
import subprocess
import sys

import pytest

from direction_drift import __version__

ROOT = pathlib.Path(__file__).resolve().parents[1]
DOCS = ["README.md", "CHANGELOG.md", "CODE_OF_CONDUCT.md", "CONSTITUTION.md",
        "CONTRIBUTING.md", "SECURITY.md", "docs/philosophy.md",
        "docs/persistence.md", "docs/scenario-card.md",
        "docs/protention-proposal-review.md", "docs/cli.md", "examples/README.md",
        ".github/PULL_REQUEST_TEMPLATE.md"]


def test_version_matches_pyproject():
    toml = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert f'version = "{__version__}"' in toml


def test_readme_version_pointer_matches_code():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    m = re.search(r"当前版本\s*\*\*v(\d+\.\d+\.\d+)\*\*", readme)
    assert m, "README 找不到版本指针（格式：当前版本 **vX.Y.Z**）"
    assert m.group(1) == __version__, (
        f"README 版本指针 v{m.group(1)} 与 __version__ {__version__} 不一致")


def test_changelog_has_entry_for_current_version():
    changelog = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert f"[{__version__}]" in changelog, f"CHANGELOG 缺少 {__version__} 条目"


@pytest.mark.parametrize("doc", DOCS)
def test_doc_has_no_broken_relative_links(doc):
    path = ROOT / doc
    if not path.exists():
        pytest.skip(f"{doc} 不存在")
    text = path.read_text(encoding="utf-8")
    for target in re.findall(r"\]\(([^)#]+?)\)", text):
        target = target.strip()
        if target.startswith(("http://", "https://", "mailto:")):
            continue
        resolved = (path.parent / target).resolve()
        assert resolved.exists(), f"{doc} → 悬空链接：{target}"


@pytest.mark.parametrize("doc", DOCS)
def test_doc_code_fences_are_paired(doc):
    path = ROOT / doc
    if not path.exists():
        pytest.skip(f"{doc} 不存在")
    fences = path.read_text(encoding="utf-8").count("\n```")
    assert fences % 2 == 0, f"{doc} 代码围栏不配对（{fences} 个）"


# --- 测试数守卫 --------------------------------------------------------------
#
# 起因：README「快速开始」第三条命令写着 `pytest  # N 项测试全离线通过`，
# 而这个 N 一直是手工维护的：205 → 207 → 226 → 235 → 273 → 301……每一次改动的
# 人要么忘了改、要么改晚一步。它出现在**门面上**，滞后就等于门面在撒谎。
# 上面两条已经让版本号由测试守，测试数同理——不靠人记得。
#
# 判据只认一句：README 声明的数 = 真跑一次 `pytest` 会收集到的数。

def _collect_full_suite_count() -> int:
    """真实跑一次全量收集，返回用例总数。

    刻意走子进程而不是读 `request.session.items`：README 声明的是「执行 `pytest`
    会跑多少条」，而 session.items 反映的是**这一次调用**收集到多少——只跑单个
    文件时它是个子集。两者不是同一件事，守卫不该把子集当全量。

    另注意 `-o addopts=`：pyproject 里 `addopts = "-q"` 会与命令行传入的 `-q`
    叠成 `-qq`，摘要行整行消失——守卫会「看不到数字」而不是「发现不一致」。
    """
    proc = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q",
         "-o", "addopts=", "-p", "no:cacheprovider"],
        cwd=str(ROOT), capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=600,
    )
    tail = proc.stdout[-2000:]
    # 探针失明一律抛错，绝不当作通过：收集失败 / 无摘要行 / 收集到 0 条，
    # 都只能说明「这次没测到东西」，不能说明「README 是对的」。
    assert proc.returncode == 0, (
        f"子进程收集失败，守卫无法判定（exit={proc.returncode}）：\n"
        f"{tail}\n{proc.stderr[-2000:]}")
    errors = re.search(r"collected,\s*(\d+)\s+errors?", proc.stdout)
    assert not errors, f"收集期有 {errors.group(1)} 个错误：\n{tail}"
    m = re.search(r"(\d+)\s+tests?\s+collected", proc.stdout)
    assert m, f"子进程输出里找不到「N tests collected」摘要行——探针失明：\n{tail}"
    n = int(m.group(1))
    assert n > 0, "收集到 0 条用例——探针失明即失败，不当作通过。"
    return n


def test_readme_test_count_matches_collected():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    m = re.search(r"#\s*(\d+)\s*项测试", readme)
    assert m, "README 找不到测试数（约定格式：`pytest  # N 项测试…`）"
    claimed = int(m.group(1))
    actual = _collect_full_suite_count()
    assert claimed == actual, (
        f"README 写着 {claimed} 项，实测 {actual} 项（差 {actual - claimed:+d}）。"
        f"改了用例就顺手把 README 的 `# {actual} 项测试…` 改过来——"
        "这个数字在门面上，滞后就是撒谎。")
