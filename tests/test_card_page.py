# -*- coding: utf-8 -*-
"""场景卡展示页的测试。

重点不在"页面能生成"，而在**页面有没有把不许撒谎的几件事显形**：
阈值不在卡里、标定性质三值、卡指纹四值、自由文本约束是启发式、
样本条数与建锥下限并排。这些每一条都可以失败，所以它们才值得测。
"""
import re

import pytest

from direction_drift.card_page import (MIN_CORE, fingerprint_state,
                                       render_cards_page)
from direction_drift.scenario import ScenarioCard


def _card(**over):
    base = dict(
        card_id="demo-v1", goal="演示方向：只给建议不代办",
        core_samples=[f"标准输出句 {i}" for i in range(20)],
        boundary_samples=["合法但不典型", "换个说法的同类句"],
        negative_samples=[f"出戏句 {i}" for i in range(10)],
        constraints=[{"text": "不代下单", "actions": ["帮你下单"],
                      "objects": ["订单"], "note": "只给清单"}],
        rule_layer=[{"name": "越权代办", "pattern": "直接帮你(订|下单)"}],
        min_suspect_len=12, format_whitelist="^(清单)[:：]",
        prompt_version="1.0", notes="给人读的说明",
    )
    base.update(over)
    return ScenarioCard(**base)


DEFAULT_CALIB = {"mode": "formal", "low": 0.12, "high": 0.25, "auc": 0.93}


# ---------------------------------------------------------------- 基本形态

def test_single_card_renders_without_tabs():
    doc = render_cards_page([_card()])
    assert doc.startswith("<!DOCTYPE html>")
    assert doc.rstrip().endswith("</html>")
    assert 'class="tabs"' not in doc            # 单张卡不需要分页签
    assert "demo-v1" in doc


def test_multi_card_gets_tabs_and_all_cards_present():
    doc = render_cards_page([_card(card_id="a-v1"), _card(card_id="b-v1")])
    assert 'class="cardtab"' in doc
    for i, cid in enumerate(("a-v1", "b-v1")):
        assert f'id="pane-t{i}"' in doc
        assert cid in doc
    # 分页签纯 CSS，不许出现脚本
    assert "<script" not in doc


def test_tab_inputs_and_panes_are_siblings():
    """**回归守卫（2026-10-01 真实翻车）**：`~` 兄弟选择器跨不过容器。

    若 input 被包进一层 div 而 pane 留在外面，`#t0:checked ~ #pane-t0`
    永远匹配不上，所有 pane 停在 display:none——页面只剩页脚，
    且"渲染出了内容"类断言全部照绿。

    判据用 **div 配平**：从某 input 到对应 pane 之间，`<div` 与 `</div>`
    数量必须相等（深度差 0）。注意不能用"之间不许出现 </div>"——
    前面 pane 的**内容**里本来就有闭合标签，那是合法的兄弟，不是包裹。
    翻车版本中 input 与 pane 之间会有一个未配平的 `</div>`（关闭包裹层），
    深度差 -1，被本测试逮住。
    """
    doc = render_cards_page([_card(card_id="a-v1"), _card(card_id="b-v1")])

    def depth_delta(s: str) -> int:
        return len(re.findall(r"<div\b", s)) - len(re.findall(r"</div>", s))

    for i in range(2):
        m = re.search(rf'<input class="cardtab"[^>]*id="t{i}"', doc)
        assert m, f"t{i} 的 input 未找到"
        # 锚点取 pane **开标签起点**——若取到标签内部的 id=，会把 pane 自己
        # 的 <div> 算进深度，恒差 +1（第一版守卫就栽在这）。
        pane_at = doc.find(f'<div class="pane" id="pane-t{i}">', m.end())
        assert pane_at >= 0, f"pane-t{i} 未找到"
        assert depth_delta(doc[m.end():pane_at]) == 0, (
            f"t{i} 与 pane-t{i} 之间 div 未配平——它们不在同一层，"
            "纯 CSS 页签会整体失效（页面变空）")
    # 选择器本身也要在：checked 态指向自己的 pane
    assert "#t0:checked~#pane-t0{display:block}" in doc.replace(" ", "")
    assert doc.count('class="pane"') == 2


def test_empty_card_list_is_rejected():
    with pytest.raises(ValueError):
        render_cards_page([])


def test_multi_card_with_calibration_is_rejected():
    """一份标定产物不可能属于两张卡——混配会得到看似可信的错误结论。"""
    with pytest.raises(ValueError) as e:
        render_cards_page([_card(card_id="a"), _card(card_id="b")],
                          calibration=DEFAULT_CALIB)
    assert "标定" in str(e.value)


def test_dict_entry_works_for_embedded_card_constants():
    """把卡常量内嵌在脚本里的调用方（检测页）也要能渲染同一份实现。"""
    c = _card()
    doc = render_cards_page([c.model_dump()])
    assert "demo-v1" in doc
    assert "现在算的" not in doc            # 指纹来自 dict 入口时无 card 对象，只显示空
    assert 'class="pill">指纹 ' in doc


# ---------------------------------------------------------------- 纪律显形

def test_threshold_not_in_card_is_stated():
    doc = render_cards_page([_card()])
    assert "阈值（<code>high</code> / <code>low</code>）<b>不在卡里</b>" in doc
    assert "换卡必须重标定" in doc


def test_card_loadable_not_equal_usable_is_stated():
    doc = render_cards_page([_card()])
    assert "卡能加载 ≠ 卡能用" in doc


def test_short_core_is_flagged_against_floor():
    doc = render_cards_page([_card(core_samples=[f"句{i}" for i in range(5)])])
    assert f"低于下限 {MIN_CORE}" in doc


def test_free_text_constraint_is_marked_heuristic():
    doc = render_cards_page([_card(constraints=["不直接推荐具体酒店"])])
    assert "启发式，不保证" in doc
    assert "tag heu" in doc


def test_structured_constraint_shows_actions_and_objects():
    doc = render_cards_page([_card()])
    assert "<code>帮你下单</code>" in doc
    assert "对象" in doc


def test_structured_constraint_without_actions_is_flagged():
    """声明成 dict 却没给 actions = 静默退回启发式，页面必须显形。"""
    doc = render_cards_page([_card(constraints=[{"text": "不代下单"}])])
    assert "无 actions" in doc


def test_notes_is_stated_as_not_in_fingerprint():
    doc = render_cards_page([_card()])
    assert "不参与判定，也不进指纹" in doc


def test_notes_does_not_change_fingerprint():
    """notes 不进指纹：改说明不该被报成"卡被改过"。"""
    a = _card(notes="说明甲").fingerprint()
    b = _card(notes="说明乙").fingerprint()
    assert a == b


def test_escape_prevents_injection():
    doc = render_cards_page([_card(goal='<img src=x onerror="boom()">',
                                   core_samples=["<b>不转义就完了</b>"] + ["x"] * 19)])
    assert "<img src=x" not in doc
    assert "&lt;img src=x" in doc
    assert "<b>不转义就完了</b>" not in doc


# ---------------------------------------------------------------- 三值 / 四值

@pytest.mark.parametrize("mode,expect", [
    ("formal", "正式标定"),
    ("synthetic", "临时标定（合成样本）"),
    (None, "标定性质未标注"),
    ("", "标定性质未标注"),          # 空串也是未申报，不许折叠成任何一边
    ("unknown-值", "标定性质未标注"),  # 未知值落到未标注，不猜
])
def test_mode_banner_is_three_valued(mode, expect):
    doc = render_cards_page([_card()], calibration={**DEFAULT_CALIB, "mode": mode})
    assert expect in doc


def test_no_calibration_means_no_threshold_shown():
    doc = render_cards_page([_card()])
    assert "标定性质" not in doc
    assert "AUC" not in doc


def test_fingerprint_state_missing():
    st = fingerprint_state("c2:aaaa", None)
    assert st["state"] == "missing"
    assert "无从判断" in st["text"]


def test_fingerprint_state_ok():
    st = fingerprint_state("c2:aaaa", "c2:aaaa")
    assert st["state"] == "ok"
    assert "是对着当前这张卡标出的" in st["text"]


def test_fingerprint_state_mismatch():
    st = fingerprint_state("c2:aaaa", "c2:bbbb")
    assert st["state"] == "mismatch"
    assert st["cls"] == "b-bad"
    assert "已被改动" in st["text"]


def test_fingerprint_state_version_change_is_algo_mismatch_not_mismatch():
    """算法版本不同**不是卡的问题**——绝不能被读成"卡被改过"。

    词汇经 T1b 收敛为契约的 `algo_mismatch`（原 `incomparable`）：复算侧的
    `de verify` 与本页面共用 `verify.fingerprint_line()`，两处词汇必须同一套，
    否则"report 与 verify 对同一输入给出一致判定"无法机械检验。
    """
    st = fingerprint_state("c2:aaaa", "c1:aaaa")
    assert st["state"] == "algo_mismatch"
    assert "不是" in st["text"]
    assert "不要" in st["text"]


def test_incomparable_is_not_styled_as_error():
    """不可比刻意不用红色：错误归因会让人重跑本来正确的标定。"""
    doc = render_cards_page([_card()],
                            calibration={"mode": "formal", "low": 0.1, "high": 0.2,
                                         "card_fingerprint": "c1:deadbeefdeadbeef"})
    banner = re.search(r'<div class="banner (b-\w+)"><b>卡指纹比对', doc)
    assert banner, "指纹比对横幅未渲染"
    assert banner.group(1) == "b-warn"


def test_fingerprint_status_source_is_stated():
    doc = render_cards_page(
        [_card()], calibration={**DEFAULT_CALIB,
                                "card_fingerprint": _card().fingerprint(),
                                "card_fingerprint_status": "backfilled"})
    assert "事后补记" in doc
    assert "不</b>说明标定当时" in doc


def test_malformed_recorded_fingerprint_is_not_guessed():
    """记录侧没有版本前缀 ⇒ 旧格式/格式异常，**不猜测**。

    归入 `algo_mismatch`（不是单独第五值）：算法版本不明 ⇒ 无法与当前算法比较
    ⇒ 不可比。契约 `Check.status` 在 provenance 层的闭集里没有第五值的容身处，
    且此类情形同样需要"不要因此重跑标定"的警示。

    文案侧重与"算法版本不同"不同：这里强调的是**无法解析、不猜测**，
    由渲染层据 `recorded_algo` 是否为空来区分——两者 level 相同，措辞不同。
    """
    st = fingerprint_state("c2:aaaa", "没有冒号的字符串")
    assert st["state"] == "algo_mismatch"
    assert "不猜测" in st["text"]


def test_boundary_statement_lists_fingerprint_limits():
    doc = render_cards_page([_card()])
    assert "指纹一致 ≠ 分数可复现" in doc
    assert "本页只描述卡，不评价卡" in doc
