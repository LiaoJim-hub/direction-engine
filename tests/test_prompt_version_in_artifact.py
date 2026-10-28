# -*- coding: utf-8 -*-
"""敌手 D 第 16 轮 P0-1 / P0-2 的守卫（2026-10-28 加）。

**P0-1**：`prompt_version_warning` 的正文曾写「请用当前提示词重跑标定
（`de calibrate`）」——★ **而那个子命令不存在**（实测
`de: error: argument cmd: invalid choice: 'calibrate'`）。
那条提醒是整个机制唯一产出价值的那一次输出，而它给出的唯一下一步不可执行。

**P0-2**：判出来的「过期」**进不了产物**；`verify` 的同源性层**不比它**。
后果：客户改完提示词、重跑检测、把两份产物交给第三方复算 ——
`verify` 会说 `consistent`，因为两份用的都是同一套跨提示词版本的阈值，
而**它没有字段可以看见这件事**。「标定有保质期」这条性质，曾只活在
跑命令那台机器的 console 一次性输出里。

本文件把那两条都变成可核的。
"""
import io
import json
import pathlib
import sys
import contextlib

import pytest

根 = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(根))

from direction_drift.calibration.record import prompt_version_warning  # noqa: E402
from direction_drift.cli import main                                   # noqa: E402
from direction_drift.scenario import ScenarioCard                      # noqa: E402
from direction_drift.verify import build_artifact, compare_artifacts    # noqa: E402

CORE = [f"我们先确定目的地和天数，再逐日规划第{i}天的行程安排" for i in range(20)]
NEG = [f"我直接帮你写一段Python代码实现快速排序算法示例{i}" for i in range(10)]

#: ★ 必须给 `judgment.texts`：不给的话 `compare_artifacts` 会判
#: `judgment.texts_digest` missing ⇒ 整份 `not_comparable` ⇒ **provenance 层根本跑不到**。
#: （2026-10-28 实测：我第一版忘了它，于是 `calibration.prompt_version` 那两项
#: `checks` 里确实有、而 `verdict` 却是 `not_comparable` —— 看着像"检查没跑"。）
JUDGMENT = {"texts": ["我们先确定目的地和天数", "好的"], "stage": "test"}

#: ★ 必须给**非空** `items`：`compare_artifacts` 在"两侧 items 皆空"时
#: 直接返回 `not_comparable`（「本次没有比对任何一条，因此**不存在**
#: 『复算一致』这个结论」）。那是对的，但会让本文件的测走不到 provenance 层。
ITEMS = [{"index": 0, "score": 0.9, "drift_level": "normal", "suspect": False}]



# ══════════════════════════════════════════════════════════════════════════
# P0-1：提醒里的"下一步"必须是本仓真有的命令
# ══════════════════════════════════════════════════════════════════════════
def test_stale_warning_does_not_point_at_a_nonexistent_command():
    """★★ P0-1 的守卫：那句提醒里不得出现 `de calibrate`。

    ★ 为什么这条要存在：`de` 的子命令只有
      `init / check / report / card / verify`。
      而"用户按提示操作，得到 argparse 报错"比"不说话"更容易误导——
      因为它已经成功引起了注意。
    """
    w = prompt_version_warning({"prompt_version": "3.0"}, "4.0")
    assert w is not None, "stale 却没给提醒——那是另一条测的事"
    assert "de calibrate" not in w, (
        "提醒里又出现了 `de calibrate`——而那个子命令不存在"
        "（`de: error: argument cmd: invalid choice: 'calibrate'`）。"
        "实际提醒：\n" + w)


def test_stale_warning_names_a_real_next_step():
    """★ 而它不该只是"删掉了错的那个"，它得**给一条真能走的路**。"""
    w = prompt_version_warning({"prompt_version": "3.0"}, "4.0")
    # ★ 本仓内能走的下一步：改卡的 prompt_version 后重跑 de check
    assert "de check" in w, (
        "提醒里没有一条本仓真有的命令。`de` 的子命令只有 "
        "init / check / report / card / verify。实际提醒：\n" + w)
    assert "prompt_version" in w, "提醒里该说清改哪个字段"


def test_the_cli_actually_has_only_those_subcommands(tmp_path):
    """★★ 探针自检：证明上一条用的"本仓真有的命令"表没写错。

    ★ 若哪天有人加了 `de calibrate`，这条会红——而那时上面的
      `test_stale_warning_does_not_point_at_a_nonexistent_command`
      就该被**反过来改**（提醒可以指它了），而不是删掉。
    """
    import argparse
    from direction_drift.cli import build_parser
    p = build_parser()
    subs = [a for a in p._actions if isinstance(a, argparse._SubParsersAction)]
    assert subs, "build_parser 没有子命令——本仓结构变了，本文件的前提失效"
    choices = sorted(subs[0].choices)
    assert choices == ["card", "check", "init", "report", "verify"], (
        f"子命令表变了：{choices}。★ 若新增了 calibrate，"
        f"`test_stale_warning_does_not_point_at_a_nonexistent_command` 该反向改。")


# ══════════════════════════════════════════════════════════════════════════
# P0-2：过期必须进产物，且 verify 必须比它
# ══════════════════════════════════════════════════════════════════════════
@pytest.fixture
def 一张卡(tmp_path):
    (tmp_path / "core.txt").write_text("\n".join(CORE), encoding="utf-8")
    (tmp_path / "neg.txt").write_text("\n".join(NEG), encoding="utf-8")
    out = tmp_path / "card.json"
    with contextlib.redirect_stdout(io.StringIO()):
        main(["init", "--card-id", "p0", "--goal", "规划",
              "--core-file", str(tmp_path / "core.txt"),
              "--negative-file", str(tmp_path / "neg.txt"),
              "--prompt-version", "4.0", "--out", str(out)])
    return ScenarioCard.from_file(out)


def test_artifact_envelope_carries_both_prompt_versions(一张卡):
    """★★ P0-2 前半：产物信封里必须**两侧都有** `prompt_version`。

    ★ 只有 `calibration` 那侧不够：那样只能比"A 与 B 之间是否同源"，
      而"标定那一版 vs 卡当前这一版"——**那才是"过期"**——仍然判不了。
    """
    art = build_artifact(
        engine_version="test", card=一张卡,
        encoder_info={"name": "demo"},
        judgment=dict(JUDGMENT),
        calibration={"mode": "formal", "low": 0.2, "high": 0.5, "auc": 0.9,
                     "weights": {"cone_alignment": 0.9},
                     "prompt_version": "3.0"},
        items=list(ITEMS),
    )
    assert (art.get("calibration") or {}).get("prompt_version") == "3.0", (
        "calibration.prompt_version 没进信封——那正是敌手 D P0-2 打穿的地方")
    assert (art.get("card") or {}).get("prompt_version") == "4.0", (
        "card.prompt_version 没进信封——只做 calibation 那侧的话，"
        "『标定是否已过期』在产物里仍然判不了")


def test_verify_flags_stale_prompt_version(一张卡):
    """★★ P0-2 后半：`verify` 必须把"标定 3.0 而卡 4.0"判成 differ。"""
    a = build_artifact(engine_version="t", card=一张卡, encoder_info={"name": "demo"},
                       judgment=dict(JUDGMENT),
                       calibration={"mode": "formal", "low": 0.2, "high": 0.5,
                                    "auc": 0.9,
                                    "weights": {"cone_alignment": 0.9},
                                    "prompt_version": "3.0"},
                       items=list(ITEMS))
    r = compare_artifacts(a, a)
    names = {c["key"]: c["status"] for c in r["checks"]}
    assert "calibration.prompt_version_vs_card" in names, (
        "verify 没有 `calibration.prompt_version_vs_card` 这一项——"
        "那说明『标定已过期』在证据层仍然不可见。实有项：\n" + "\n".join(sorted(names)))
    assert names["calibration.prompt_version_vs_card"] == "differ", (
        "标定 3.0 而卡 4.0，却没判成 differ。实判："
        + names["calibration.prompt_version_vs_card"])
    # ★ `provenance_bad` 是**内部变量**，不暴露在返回里（只通过 verdict 表达）。
    #   故这里断言 verdict —— 那才是第三方真会看到的那个结论。
    assert r["verdict"] == "mismatch", (
        "标定已过期而 verdict 仍是 " + r["verdict"]
        + " —— 那正是 P0-2 说的『verify 会说 consistent』")


def test_verify_says_equal_when_versions_match(一张卡):
    """★ 反向：版本一致时**不该**报 differ（否则那一项成噪声）。"""
    a = build_artifact(engine_version="t", card=一张卡, encoder_info={"name": "demo"}, judgment=dict(JUDGMENT),
                       calibration={"mode": "formal", "low": 0.2, "high": 0.5,
                                    "auc": 0.9,
                                    "weights": {"cone_alignment": 0.9},
                                    "prompt_version": "4.0"},
                       items=list(ITEMS))
    names = {c["key"]: c["status"] for c in compare_artifacts(a, a)["checks"]}
    assert names["calibration.prompt_version_vs_card"] == "equal"


def test_verify_says_uncomputable_on_old_artifact(一张卡):
    """★★★ 反测：旧产物没有 `prompt_version` ⇒ `uncomputable`，**不得判绿**。

    ★ 那是把"没记"当"一致"——即把不知道渲染成通过。
    """
    a = build_artifact(engine_version="t", card=一张卡, encoder_info={"name": "demo"}, judgment=dict(JUDGMENT),
                       calibration={"mode": "formal", "low": 0.2, "high": 0.5,
                                    "auc": 0.9,
                                    "weights": {"cone_alignment": 0.9}},
                       items=list(ITEMS))
    r = compare_artifacts(a, a)
    names = {c["key"]: c["status"] for c in r["checks"]}
    assert names["calibration.prompt_version"] == "uncomputable", (
        "旧产物缺字段却判成了 " + names["calibration.prompt_version"]
        + "——那是把「不知道」渲染成通过")
    # ★ `uncomputable` **不该**让 verdict 变 mismatch —— 它不红，只是不可核。
    assert r["verdict"] in ("consistent", "mismatch"), (
        "旧产物缺 prompt_version 却给出了 " + r["verdict"] + " 以外的定性")
    assert any("prompt_version" in n for n in r["notes"]), (
        "缺字段时该在 notes 里说明，且明写「不得表述为同源已验证」")
