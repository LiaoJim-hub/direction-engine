# -*- coding: utf-8 -*-
"""性质⑤「标定有保质期」在命令行那一层的**调用点**（2026-10-28 加）。

**为什么要单独一个文件**：

2026-10-28 的盘点发现 README 门面写「智能体改版后**引擎主动提醒复检**」，
`--prompt-version` 的帮助文本更写「改提示词后同步改它，**否则标定会静默过期**」
——**而全包里一个字节的检查都没有**。

第一轮只补了 `calibration/record.py::check_prompt_version`（**能判**），
而它**没有调用点** ⇒ 「主动提醒」那一半仍然没做。

本文件守的就是那一半：**`de check` 加载标定文件时，必须把过期说出来**。

**为什么这一条必须是"提醒"而不是"拒绝"**：
README 第 3 条性质是「**只显形，不修正**」——引擎绝不自动改你的东西。
故这里**只往 warns 里加一句**，不动阈值、不动文件、不返回非零。
★ 若哪天有人把它改成"过期就拒绝跑"，那条性质就破了，本文件该红。
"""
import json

import pytest

from direction_drift.cli import main

CORE = [f"我们先确定目的地和天数，再逐日规划第{i}天的行程安排" for i in range(20)]
NEG = [f"我直接帮你写一段Python代码实现快速排序算法示例{i}" for i in range(10)]


@pytest.fixture
def workspace(tmp_path):
    (tmp_path / "core.txt").write_text("\n".join(CORE), encoding="utf-8")
    (tmp_path / "neg.txt").write_text("\n".join(NEG), encoding="utf-8")
    (tmp_path / "in.txt").write_text(
        "我们先确定目的地和天数，再逐日规划行程\n好的\n", encoding="utf-8")
    return tmp_path


def _make_card(workspace, prompt_version):
    out = workspace / "card.json"
    rc = main(["init", "--card-id", "t-pv", "--goal", "帮用户规划旅行，不代办",
               "--core-file", str(workspace / "core.txt"),
               "--negative-file", str(workspace / "neg.txt"),
               "--prompt-version", prompt_version,
               "--out", str(out)])
    assert rc == 0
    return out


def _make_calibration(workspace, *, prompt_version, card):
    """造一份最小可用的标定文件（★ 六字段齐，否则 _load_calibration 会先报别的）。"""
    p = workspace / "calib.json"
    p.write_text(json.dumps({
        "created": "2026-10-28",
        "mode": "formal",
        "source": "property-test",
        "n": 30, "n_positive": 10,
        "low": 0.2, "high": 0.5, "auc": 0.9,
        "encoder": "demo", "weights": {"cone_alignment": 0.9},
        "prompt_version": prompt_version,
        "card_fingerprint": card.fingerprint(),
        "card_fingerprint_status": "recorded_at_calibration",
    }, ensure_ascii=False), encoding="utf-8")
    return p


# --------------------------------------------------------------------------
# 正测：版本不同 ⇒ 提醒
# --------------------------------------------------------------------------
def test_check_warns_when_calibration_prompt_version_is_stale(
        workspace, capsys):
    """★ 核心：卡是 1.0，标定是 0.9 ⇒ `de check` 必须把「已过期」说出来。"""
    card_path = _make_card(workspace, "1.0")
    from direction_drift.scenario import ScenarioCard
    card = ScenarioCard.from_file(card_path)
    calib_path = _make_calibration(workspace, prompt_version="0.9", card=card)

    rc = main(["check", "--card", str(card_path),
               "--calibration", str(calib_path),
               "--input", str(workspace / "in.txt")])
    out = capsys.readouterr().out

    assert rc == 0, "★ 过期只该提醒，不该拒绝跑（性质③「只显形，不修正」）"
    assert "标定已过期" in out, (
        "`de check` 没把标定过期说出来——而 README 门面写的是「引擎**主动提醒**复检」。"
        "实际输出：\n" + out)
    assert "0.9" in out and "1.0" in out, (
        "提醒里该同时出现记录的版本与当前版本（否则人不知道该改哪个）。实际：\n" + out)


def test_check_does_not_warn_when_versions_match(workspace, capsys):
    """★ 反向：版本相同时**不该**提醒（否则那句提醒就成了噪声）。"""
    card_path = _make_card(workspace, "1.0")
    from direction_drift.scenario import ScenarioCard
    card = ScenarioCard.from_file(card_path)
    calib_path = _make_calibration(workspace, prompt_version="1.0", card=card)

    rc = main(["check", "--card", str(card_path),
               "--calibration", str(calib_path),
               "--input", str(workspace / "in.txt")])
    out = capsys.readouterr().out

    assert rc == 0
    assert "标定已过期" not in out, (
        "版本相同时却报了过期——那会让这句提醒变成噪声。实际输出：\n" + out)


# --------------------------------------------------------------------------
# 反测：unknown 不提醒，且不阻断
# --------------------------------------------------------------------------
def test_check_does_not_warn_when_calibration_lacks_prompt_version(
        workspace, capsys):
    """★ 旧标定没记 `prompt_version` ⇒ `unknown` ⇒ **不提醒**。

    ★★ 那是刻意的：一句「我不知道」的提醒会让人误以为引擎知道得多。
       `unknown` 该由要判定的那一层显式处理，不该在这里变成一句含糊的话。
    """
    card_path = _make_card(workspace, "1.0")
    from direction_drift.scenario import ScenarioCard
    card = ScenarioCard.from_file(card_path)

    p = workspace / "calib.json"
    p.write_text(json.dumps({
        "created": "2026-10-28", "mode": "formal", "source": "t",
        "n": 30, "n_positive": 10, "low": 0.2, "high": 0.5, "auc": 0.9,
        "encoder": "demo", "weights": {"cone_alignment": 0.9},
        # ★ 没有 prompt_version
        "card_fingerprint": card.fingerprint(),
        "card_fingerprint_status": "recorded_at_calibration",
    }, ensure_ascii=False), encoding="utf-8")

    rc = main(["check", "--card", str(card_path), "--calibration", str(p),
               "--input", str(workspace / "in.txt")])
    out = capsys.readouterr().out

    assert rc == 0
    assert "标定已过期" not in out, (
        "缺 prompt_version 时报了过期——那是把「不知道」渲染成红。实际：\n" + out)


def test_the_stale_warning_is_not_a_refusal(workspace, capsys):
    """★★ 这一条守的是**性质③**：过期是提醒，不是拒绝。

    ★ 若哪天有人把"过期 ⇒ 直接 SystemExit"写进来，本文件该红——
      因为那会让引擎**替人做决定**（「只显形，不修正」）。
    """
    card_path = _make_card(workspace, "2.0")
    from direction_drift.scenario import ScenarioCard
    card = ScenarioCard.from_file(card_path)
    calib_path = _make_calibration(workspace, prompt_version="1.0", card=card)

    # ★ 不该抛
    rc = main(["check", "--card", str(card_path), "--calibration",
               str(calib_path), "--input", str(workspace / "in.txt")])
    out = capsys.readouterr().out
    assert rc == 0, "过期导致 de check 返回非零——那已经不是「只显形」了"
    # ★ 而它仍然要跑出结果（有判定行）
    assert "已判定" in out, "过期之后连结果都不跑了——那是拒绝，不是提醒"


# --------------------------------------------------------------------------
# 探针自检
# --------------------------------------------------------------------------
def test_the_warning_path_is_actually_exercised(workspace, capsys):
    """探针自检：证明上面那些测真的走到了 `_load_calibration` 的提醒分支。

    ★ 若 `--calibration` 这条路根本没被走到（比如参数名改了、或提前 return），
      上面三条会**全部绿着通过**——而它们什么也没测到。
    """
    card_path = _make_card(workspace, "1.0")
    from direction_drift.scenario import ScenarioCard
    card = ScenarioCard.from_file(card_path)
    calib_path = _make_calibration(workspace, prompt_version="9.9", card=card)

    rc = main(["check", "--card", str(card_path), "--calibration",
               str(calib_path), "--input", str(workspace / "in.txt")])
    out = capsys.readouterr().out
    assert rc == 0
    # ★ 这一行只有走 --calibration 那条路才会出现（自标定路径的 source 不同）
    assert "标定性质" in out, "连「标定性质」都没打印——`--calibration` 那条路没走通"
    assert "9.9" in out
