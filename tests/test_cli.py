# -*- coding: utf-8 -*-
"""`de` 命令行的测试（v2.4.0）。

重点不在"命令能不能跑"，而在**三条纪律在命令行这一层有没有走样**：

1. 未标定不判定，且性质必须显形（`synthetic` / `formal` / `未标注` 三值）；
2. 跳过的条目要计数（`too_short` / `no_output` 不产分数，但占分母）；
3. 标定与卡不同源时要报警（卡指纹比对），缺字段时不猜。

全部用 DemoEncoder（零下载、确定性），故本文件不依赖 [sbert]。
"""
import json

import pytest

from direction_drift.cli import main
from direction_drift.report_html import mode_banner
from direction_drift.scenario import ScenarioCard

CORE = [f"我们先确定目的地和天数，再逐日规划第{i}天的行程安排" for i in range(20)]
NEG = [f"我直接帮你写一段Python代码实现快速排序算法示例{i}" for i in range(10)]


@pytest.fixture
def workspace(tmp_path):
    (tmp_path / "core.txt").write_text("\n".join(CORE), encoding="utf-8")
    (tmp_path / "neg.txt").write_text("\n".join(NEG), encoding="utf-8")
    (tmp_path / "in.txt").write_text(
        "我们先确定目的地和天数，再逐日规划行程\n好的\n", encoding="utf-8")
    return tmp_path


@pytest.fixture
def card_path(workspace):
    out = workspace / "card.json"
    rc = main(["init", "--card-id", "t-cli", "--goal", "帮用户规划旅行，不代办",
               "--core-file", str(workspace / "core.txt"),
               "--negative-file", str(workspace / "neg.txt"),
               "--rule", "越权代办=(我直接帮你下单|我帮你订)",
               "--out", str(out)])
    assert rc == 0
    return out


# ---------------------------------------------------------------- init

def test_init_writes_a_loadable_card(card_path):
    """产出的 JSON 必须能被 ScenarioCard 读回——否则"建了张不能用的卡"。"""
    card = ScenarioCard.from_file(card_path)
    assert card.card_id == "t-cli"
    assert len(card.core_samples) == 20
    assert len(card.negative_samples) == 10
    assert card.rule_layer and card.rule_layer[0].name == "越权代办"


def test_init_warns_but_does_not_block_on_thin_samples(workspace, capsys):
    """样本不足只提示不拦截——真正的硬门在 build_cone，两处报同一个数是浪费。"""
    (workspace / "few.txt").write_text("只有一条样本而已", encoding="utf-8")
    out = workspace / "thin.json"
    rc = main(["init", "--card-id", "thin", "--goal", "测试方向",
               "--core-file", str(workspace / "few.txt"), "--out", str(out)])
    assert rc == 0
    assert out.exists()
    printed = capsys.readouterr().out
    assert "20" in printed and "拒绝建锥" in printed


def test_init_rejects_bad_rule_syntax(workspace):
    with pytest.raises(SystemExit):
        main(["init", "--card-id", "x", "--goal", "g",
              "--rule", "缺少等号的正则", "--out", str(workspace / "x.json")])


# ---------------------------------------------------------------- check

def test_check_without_calibration_declares_synthetic(card_path, workspace, capsys):
    """未给标定 → 性质必须是 synthetic，且警告必须出现（不是默默用自标定阈值）。"""
    rc = main(["check", "--card", str(card_path),
               "--input", str(workspace / "in.txt"),
               "--jsonl", str(workspace / "out.jsonl")])
    assert rc == 0
    out = capsys.readouterr().out
    assert "标定性质：synthetic" in out
    assert "分数仅供观察" in out


def test_check_counts_skipped_items(card_path, workspace):
    """'好的' 太短 → 不进判定，但必须出现在 skipped 里（占分母）。"""
    jsonl = workspace / "out.jsonl"
    main(["check", "--card", str(card_path), "--input", str(workspace / "in.txt"),
          "--jsonl", str(jsonl)])
    lines = [json.loads(x) for x in jsonl.read_text(encoding="utf-8").splitlines() if x]
    meta = lines[0]["_meta"]
    skipped = [x for x in lines[1:] if x.get("_skipped")]
    assert meta["n_input"] == 2
    assert meta["n_skipped"] == 1
    assert skipped and skipped[0]["level"] in ("too_short", "no_output")


def test_check_reports_fingerprint_mismatch(card_path, workspace, capsys):
    """标定绑的是另一张卡 → 必须报警，不得继续宣称这套阈值可用。"""
    calib = workspace / "calib.json"
    calib.write_text(json.dumps({"mode": "formal", "low": 0.05, "high": 0.10,
                                 "auc": 0.9, "card_fingerprint": "c2:deadbeefdeadbeef"}),
                     encoding="utf-8")
    main(["check", "--card", str(card_path), "--input", str(workspace / "in.txt"),
          "--calibration", str(calib)])
    assert "卡指纹不一致" in capsys.readouterr().out


def test_check_missing_mode_shows_unlabeled(card_path, workspace, capsys):
    """没有 mode 字段 → 显示"未标注"，**不许猜成 formal 或 synthetic**。"""
    calib = workspace / "calib.json"
    calib.write_text(json.dumps({"low": 0.05, "high": 0.10, "auc": 0.9}),
                     encoding="utf-8")
    main(["check", "--card", str(card_path), "--input", str(workspace / "in.txt"),
          "--calibration", str(calib)])
    out = capsys.readouterr().out
    assert "标定性质：未标注" in out
    assert "不猜测" in out


def test_check_refuses_calibration_without_thresholds(card_path, workspace):
    """标定文件缺 low/high → 直接失败，不用默认阈值硬跑。"""
    calib = workspace / "calib.json"
    calib.write_text(json.dumps({"mode": "formal", "auc": 0.9}), encoding="utf-8")
    with pytest.raises(SystemExit):
        main(["check", "--card", str(card_path), "--input", str(workspace / "in.txt"),
              "--calibration", str(calib)])


def test_check_rule_layer_catches_literal_redline(card_path, workspace, capsys):
    """字面红线命中即便语义分不低也必须标疑似——这是规则层存在的理由。"""
    arr = workspace / "arr.txt"
    arr.write_text("这家酒店还有空房，我直接帮你下单了", encoding="utf-8")
    main(["check", "--card", str(card_path), "--input", str(arr)])
    assert "越权代办" in capsys.readouterr().out


# ---------------------------------------------------------------- report

def test_report_html_carries_mandatory_disclaimers(card_path, workspace):
    jsonl = workspace / "out.jsonl"
    html_path = workspace / "r.html"
    main(["check", "--card", str(card_path), "--input", str(workspace / "in.txt"),
          "--jsonl", str(jsonl)])
    assert main(["report", "--from", str(jsonl), "--out", str(html_path)]) == 0
    html = html_path.read_text(encoding="utf-8")
    assert html.startswith("<!DOCTYPE html>")
    for must in ("未发现 ≠ 保证合规", "告警归引擎", "分数仅供观察",
                 "未判定（已跳过）", "分数水位不可跨编码器比较"):
        assert must in html, f"报告缺少必需声明：{must}"


def test_check_writes_a_recomputable_artifact(card_path, workspace):
    """PRD §8.4 成功流的**前半段**：`de check` 必须能吐出判定产物信封。

    没有这一步，"离线复算"就缺一个能喂给 `de verify` 的输入——PRD 里写的
    `de verify --input r.jsonl` 没有任何一处实现（JSONL 是报告输入，不是
    产物信封），这条测试把真正存在的那条路钉住。
    """
    a = workspace / "art_a.json"
    rc = main(["check", "--card", str(card_path),
               "--input", str(workspace / "in.txt"),
               "--artifact", str(a)])
    assert rc == 0 and a.is_file()
    art = json.loads(a.read_text(encoding="utf-8"))

    from direction_drift import __version__
    from direction_drift.verify import ARTIFACT_SCHEMA_VERSION

    assert art["artifact_schema_version"] == ARTIFACT_SCHEMA_VERSION
    assert art["engine"]["version"] == __version__
    assert art["card"]["fingerprint"] == ScenarioCard.from_file(
        card_path).fingerprint()
    assert art["judgment"]["texts_digest"].startswith("sha256:")
    assert art["calibration"]["mode"] == "synthetic"      # 未给标定 → 自标定
    # 实际生效的权重必须记进去：记 null 会让产物"天生不可复算"，而它是已知的
    assert art["calibration"]["weights"], "自标定路径漏记了实际生效的权重"
    # 补记的指纹必须是弱证据：状态写 backfilled，不许冒充标定当时记录的
    assert art["calibration"]["card_fingerprint_status"] == "backfilled"


def test_two_runs_of_check_verify_as_consistent(card_path, workspace):
    """PRD §8.4 成功流的**后半段**：同卡同输入跑两遍，`de verify` 判一致（0）。

    这是"可复算"唯一的端到端证明：不是"我们保证对"，而是**两次独立产出的
    产物能被第三方逐条对齐**。
    """
    from direction_drift.verify import EXIT_CODES

    # 必须给**正式标定**：合成标定（mode=synthetic）按契约刻意不可比——
    # 用合成阈值去"验证判定"没有意义，复算内核会直接判 not_comparable。
    fp = ScenarioCard.from_file(card_path).fingerprint()
    calib = workspace / "calib.json"
    calib.write_text(json.dumps({
        "mode": "formal", "low": 0.10, "high": 0.30, "auc": 0.98,
        "source": "测试夹具（不是真实标注，仅供链路验证）",
        "weights": {"cone_alignment": 0.4, "constraint_satisfaction": 0.4,
                    "negative_similarity": 0.2},
        "prompt_version": "1.0", "card_fingerprint": fp,
    }, ensure_ascii=False), encoding="utf-8")

    a, b = workspace / "a.json", workspace / "b.json"
    for out in (a, b):
        assert main(["check", "--card", str(card_path),
                     "--input", str(workspace / "in.txt"),
                     "--calibration", str(calib),
                     "--artifact", str(out)]) == 0
    rc = main(["verify", "--a", str(a), "--b", str(b)])
    assert rc == EXIT_CODES["consistent"] == 0


def test_report_requires_meta_line(card_path, workspace):
    bad = workspace / "bad.jsonl"
    bad.write_text('{"i": 0, "text": "x", "score": 0.5}\n', encoding="utf-8")
    with pytest.raises(SystemExit):
        main(["report", "--from", str(bad), "--out", str(workspace / "x.html")])


@pytest.mark.parametrize("mode,expect", [
    ("formal", "正式标定"), ("synthetic", "临时标定"), (None, "标定性质未标注"),
    ("随便写的", "标定性质未标注"),
])
def test_banner_is_three_valued_and_never_guesses(mode, expect):
    """三值横幅：未知值一律落到"未标注"，**不折叠成任何一边**。"""
    assert expect in mode_banner(mode)
