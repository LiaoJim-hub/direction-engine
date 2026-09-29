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

import pytest

from direction_drift import __version__

ROOT = pathlib.Path(__file__).resolve().parents[1]
DOCS = ["README.md", "CHANGELOG.md", "CODE_OF_CONDUCT.md", "CONSTITUTION.md",
        "CONTRIBUTING.md", "SECURITY.md", "docs/philosophy.md",
        "docs/persistence.md",
        "docs/protention-proposal-review.md", "examples/README.md",
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
