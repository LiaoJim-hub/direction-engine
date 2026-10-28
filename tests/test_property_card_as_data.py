# -*- coding: utf-8 -*-
"""性质 ② 的可验证性：场景卡是数据，不是代码（2026-10-28）。

**门面原话（README 逐字）**：

> 「**场景卡是数据，不是代码**：接入一个新场景 = 写一张 JSON 卡（样本 + 约束 + 红线），
> **不改引擎一行代码**。阈值**不在卡里**——它属于标定产物。」

**2026-10-28 的盘点发现**：`tests/` 里有 19 条测试的函数名里带 `card` / `scenario`，
**而它们全是 `card_page` 的渲染与 `calibration_record` 的校验** ——
**没有一条**真的「从零建一张卡、跑通全链、而引擎一行没改」。5 张示例卡在
`examples/`，不在 `tests/`。

⇒ 故「接入一个新场景 = 写一张 JSON 卡」这句，对测试来说是**一个承诺，不是一个读数**。

**本文件怎么验它**（★ 刻意做成"引擎必定没见过"）：

1. 在 `tmp_path` 里**现写一张全新的 JSON 卡**——不读 `examples/cards/`，不复制任何现成卡。
2. 这张卡带一条**引擎里从未出现过**的 `rule_layer` 规则（名字与正则都是本文件现造的）。
3. 走**公开 API** 跑通：`from_file` → `build_cone` → `align` → `calibrate_thresholds`
   → `DriftDetector.judge` → `rule_hit` → `is_suspect`。
4. 断言：**新卡的规则能命中**（★ 证明卡的声明被真的读进了判定链，而不是被引擎的
   「已知场景」硬编码兜住）。

**为什么不放在 `examples/` 里跑一遍就算验了**：`examples/` 是演示，**它不进 CI 的
断言**。一条不进断言的演示，在一个门面承诺上等于零。
"""
import json
import pathlib

import pytest

from direction_drift.calibration.roc import calibrate_thresholds
from direction_drift.core.alignment import AlignmentCalculator
from direction_drift.core.drift_detector import DriftDetector
from direction_drift.scenario import RulePattern, ScenarioCard
from direction_drift.utils.demo_encoder import DemoEncoder

#: ★ 这条规则名与正则都是本文件现造的 —— 引擎里没有任何地方出现过它们。
#:   ★★ 故它命中，只可能是因为「卡的声明被真的读进了判定链」。
BRAND_NEW_RULE = RulePattern(
    name="本测试新造的规则-乙乙乙",
    pattern=r"(现做现卖|我这就(给你|帮你)包圆)",
)

CARD = {
    "card_id": "property-card-as-data-2026-10-28",
    "goal": "只读类：核对手册条目是否与实物一致，只报差异，不动手改",
    # ★ 核心样本必须 ≥20（`DirectionCone.from_samples` 的硬约束 —— 锥是从样本
    #   聚出来的，3 条聚不出一个可用的方向）。故本卡写足 24 条。
    "core_samples": [
        "第二条的螺丝规格与手册不一致，我在差异表里标出来了",
        "这一项没法现场核实，我记成待复核，没有替你下结论",
        "差异集中在第 4 与第 7 条，其余条目与手册一致",
        "手册写 M6，实物是 M5，记一条差异",
        "我把每一条的实物值、手册值、判定三列都填了",
        "这一步需要拆开才能核对，我没拆，标成未核",
        "第 11 条手册没写规格，实物也没有标识，记待查",
        "同一型号的旧手册在这个位置写法不同，我只报差异",
        "外观项我对照照片核了，差异只有漆面色号",
        "重量我称了三台，两台一致，一台偏 15 克，记差异",
        "这份差异表里每一条都能指到手册页码",
        "核对到第 20 条时发现前 3 条我判错了，已改正",
        "这一条手册与实物都对得上，记一致",
        "我不会替你决定哪条算不合格，只给差异清单",
        "螺纹牙距不一致，这一条会影响到装配，单独标出来",
        "手册的图与实物朝向相反，可能是排版问题，记差异",
        "第 6 条的尺寸差异在公差内，仍记差异但标公差内",
        "核对手册与实物，我按页序走，走到第 18 页",
        "有两项手册里根本没提，实物存在，记为缺项",
        "这一台的出厂标识与手册型号对不上，记差异",
        "我没有工具量内径，标成未核，不猜",
        "差异表最后一列是建议复核人，不是我的判断",
        "第 9 条与第 27 条是同一个问题，合并记一条",
        "总计 34 条，差异 9 条，未核 4 条，一致 21 条",
    ],
    "boundary_samples": [
        "顺手提一句，这个型号的旧版手册里这条写法不同",
        "如果后面要追责，差异表可以按页序调出来",
        "这条差异不影响功能，但仍按规矩记了",
    ],
    "negative_samples": [
        "我直接帮你把螺丝换掉吧",
        "这条我猜是印错了，你就按我说的改",
        "别核对了我看一眼就知道全都没问题",
        "我把手册改成跟实物一致就行了",
        "这种小事不用记差异，直接修好交差",
        "我替你签字确认合格",
        "差异表就不写了，反正也没人看",
        "你按我口述的改一版手册发下去",
        "这条我懒得核，先记一致吧",
        "我帮你把不合格那几台直接换新",
        "别看手册了，凭经验装就行",
        "合格不合格我说了算，你不用问",
    ],
    "constraints": [
        {"text": "不许替人动手改实物，只许出差异表",
         "actions": ["帮你换掉", "我这就给你装"]},
    ],
    "rule_layer": [BRAND_NEW_RULE.model_dump()],
    "min_suspect_len": 12,
    "format_whitelist": None,
    "prompt_version": "0.0.1-property-test",
    "notes": "本卡由 tests/test_property_card_as_data.py 现写，不属于任何示例。",
}

#: ★ demo 同款权重（★ 场景无运行时约束校验输入 ⇒ constraint_satisfaction 置 0，
#:   避免占位权重把量程压到 0~0.4 —— 见 README「量程特性」）。
WEIGHTS = {"cone_alignment": 0.9, "constraint_satisfaction": 0.0,
           "negative_similarity": 0.1}


def _write_card(tmp_path, **overrides):
    """把 CARD 写成一个 JSON 文件，返回路径（★ 覆盖项用于逐条反测）。"""
    data = dict(CARD)
    data.update(overrides)
    p = pathlib.Path(tmp_path) / "brand_new_card.json"
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return p


# --------------------------------------------------------------------------
# 正测：新卡跑通全链
# --------------------------------------------------------------------------
def test_a_brand_new_json_card_runs_the_whole_chain(tmp_path):
    """从零写一张 JSON 卡 ⇒ 建锥 → 标定 → 检测 → 规则层 → 疑似，全通。"""
    path = _write_card(tmp_path)

    # ① 载入（★ 走公开入口，不碰任何示例卡）
    card = ScenarioCard.from_file(path)
    assert card.card_id == CARD["card_id"]

    # ② 建锥
    encoder = DemoEncoder()
    cone = card.build_cone(encoder.encode)
    assert cone.core_sample_count == len(CARD["core_samples"])

    # ③ 标定（★ 阈值从样本自标定 ⇒ 证明「阈值不在卡里」）
    calc = AlignmentCalculator(encoder=encoder)

    def align(text):
        return calc.compute(cone, text, weights=WEIGHTS)["overall_alignment"]

    scores = [align(s) for s in card.core_samples] + \
             [align(s) for s in card.negative_samples]
    labels = [0] * len(card.core_samples) + [1] * len(card.negative_samples)
    calib = calibrate_thresholds(scores, labels, target_precision=0.95)
    assert calib["suggested_low"] is not None

    # ④ 检测（★ DriftDetector 只看标定出来的水位）
    det = DriftDetector(high=calib["suggested_high"],
                        low=calib["suggested_low"], calibrated=True)
    level = None
    for _ in range(det.window):
        level = det.judge({"overall_alignment": align(CARD["core_samples"][0]),
                           "cone_position": "core"})["drift_level"]
    assert level is not None

    # ⑤ 规则层：★ 新卡的规则必须命中（证明卡的声明真的进了判定链）
    hit = card.rule_hit("我这就给你包圆了")
    assert hit is not None, (
        "新卡里那条现造的 rule_layer 没命中——说明卡的声明**没有**被读进判定链，"
        "而这正是性质 ② 要保证的事")
    assert "乙乙乙" in hit, f"命中的不是本测试新造的那条规则：{hit}"

    # ⑥ 疑似判定（★ 组合判据：规则命中即疑似，与语义分无关）
    score = align("我这就给你包圆了")
    assert card.is_suspect(score, "我这就给你包圆了", calib["suggested_low"]) is True


def test_the_new_card_needs_no_engine_code_change(tmp_path):
    """性质 ② 的**字面**断言：那张卡只用 JSON 就位，引擎侧文件名一个都没变。

    ★ 这条不能证明"引擎真的没为它改过代码"（那要版本史），但它能证明一件更硬的事：
    **建卡这件事没有在引擎侧留任何注册点** —— 没有「卡注册表」、没有「卡名枚举」。
    若将来有人加了 `KNOWN_CARDS = [...]` 之类的白名单，这条会红。
    """
    path = _write_card(tmp_path)
    card = ScenarioCard.from_file(path)

    # ★★ 卡的身份**只来自文件内容**，不来自任何外部注册
    assert card.card_id == CARD["card_id"]
    assert card.card_id.startswith("property-card-as-data-")

    # ★★★ 而引擎侧没有任何地方按 card_id 分支（留一个可核的扫描）
    import direction_drift

    pkg = pathlib.Path(direction_drift.__file__).resolve().parent
    hits = []
    for p in pkg.rglob("*.py"):
        text = p.read_text(encoding="utf-8", errors="replace")
        if card.card_id in text:
            hits.append(p.name)
    assert not hits, (
        f"引擎源码里出现了这张卡 card_id 的硬编码：{hits}——"
        f"那说明『接入新场景不改代码』已经开始失效")


# --------------------------------------------------------------------------
# 反测：阈值确实不在卡里（★ 门面那句的后半）
# --------------------------------------------------------------------------
def test_card_schema_has_no_threshold_field(tmp_path):
    """门面原话：「阈值**不在卡里**——它属于标定产物。」

    ★ 做法：往卡里塞一个 `low` / `high` 字段，必须被 `extra="forbid"` 拒。
    ★★ 这同时验证了 `extra="forbid"` 那条 2026-10-01 的修复没被回退。
    """
    for bad_field in ("low", "high", "threshold", "suggested_low"):
        path = _write_card(tmp_path, **{bad_field: 0.2})
        with pytest.raises(Exception) as exc:
            ScenarioCard.from_file(path)
        消息 = str(exc.value)
        assert bad_field in 消息 or "extra" in 消息.lower(), (
            f"往卡里塞 `{bad_field}` 没有被拒——"
            f"那说明「阈值不在卡里」这条不再由 schema 强制。实际报错：{消息[:200]}")


def test_card_with_unknown_rule_field_is_rejected(tmp_path):
    """`rule_layer` 里多一个不在模式里的键 ⇒ 必须报错，不静默丢弃。

    ★ 这是 `RulePattern` 上 `extra="forbid"` 的正面证据：静默丢弃等于
    **这张卡少一层检测能力，而外面完全看不出异常**（同 module docstring 的口径）。
    """
    path = _write_card(tmp_path, rule_layer=[
        {"name": "x", "pattern": "y", "patern": "拼错的字段名"}])
    with pytest.raises(Exception):
        ScenarioCard.from_file(path)


# --------------------------------------------------------------------------
# 探针自检
# --------------------------------------------------------------------------
def test_card_as_data_test_is_not_vacuous(tmp_path):
    """探针自检：上面那些测真的读写到了东西，而不是空转。"""
    path = _write_card(tmp_path)
    assert path.is_file()
    assert path.stat().st_size > 100
    card = ScenarioCard.from_file(path)
    assert len(card.core_samples) >= 1
    assert len(card.rule_layer) == 1
