# -*- coding: utf-8 -*-
"""返回协议：响应不得陈述没发生的事（v2.3.5）。

`act()` 在 `confirmed_drift` 时返回 `frozen` 与一句人读 `message`。二者都是
**对"刚刚发生了什么"的陈述**，因此必须与实际执行的动作一致：

- 传了锥 → 调用了 `cone.freeze()` → `frozen=True`，文案说"已冻结方向"；
- 锥为 `None` → **什么都没冻结** → `frozen=False`，文案不许说"冻结方向"。

此前 `frozen` 被硬编码为 `True`，锥为 None 时 `freeze()` 并未被调用，响应于是在
陈述一件没发生的事。同一条字典里的 `direction` 用了 `cone.goal if cone else ""`，
说明 None 分支本来就被考虑过——这是漏掉的一处，不是设计。

这属于本项目一直在修的那一族：**"我没做"与"我做了"必须可区分**（v2.3.3 修的是
"放弃判定 ≠ 判定通过"，v2.3.4 修的是"声明的检查手段不在场 ≠ 已经查过了"，
这里是"没冻结 ≠ 已冻结"）。
"""
import pytest

from direction_drift.core.direction_cone import DirectionCone
from direction_drift.core.drift_detector import DriftDetector
from direction_drift.core.return_protocol import (DiagnoseConfig, ResumeFailed,
                                                   ReturnProtocol, ReviseFailed)

CRITICAL = {"drift_level": "confirmed_drift"}


def _minimal_cone():
    """最小的真锥（离线、确定性）。

    刻意不用 `DirectionCone(goal=...)`——构造器要 axis/aperture/softness，
    手搓那三个值会让测试依赖锥的内部几何而不只是"有个锥可冻结"。
    """
    from direction_drift.utils.demo_encoder import DemoEncoder

    enc = DemoEncoder()
    return DirectionCone.from_samples(
        goal="帮用户规划旅行",
        core_samples=[f"帮用户规划第{i}天旅行行程，考虑天气与预算" for i in range(22)],
        negative_samples=[f"帮我写一段Python代码实现排序算法{i}" for i in range(10)],
        embed_fn=enc.encode)


# --------------------------------------------------- 单元层：act() 自身


def test_no_cone_means_nothing_was_frozen_and_the_message_says_so():
    """无锥可冻结：`frozen` 必须是 False，文案不得宣称已冻结。"""
    proto = ReturnProtocol()
    r = proto.act(dict(CRITICAL), None, None)

    assert r["action"] == "return"
    assert r["frozen"] is False, "没有传锥就什么都没冻结，不许报 True"
    assert "未冻结" in r["message"], r["message"]
    assert "已冻结方向" not in r["message"], r["message"]
    assert proto.state == "returning", "状态迁移与人有没有锥无关"


def test_with_cone_the_freeze_really_happened():
    """传了锥：`frozen` 为 True，且锥上确实落了冻结标记。"""
    cone = _minimal_cone()
    proto = ReturnProtocol()
    r = proto.act(dict(CRITICAL), cone, None)

    assert r["frozen"] is True
    assert "已冻结方向" in r["message"]
    assert cone.frozen is True, "报 frozen=True 就必须真的冻结了"
    assert cone.freeze_reason == "confirmed_drift"


def test_whether_frozen_or_not_a_human_must_decide():
    """冻结与否都改变不了"由人决定"：这条不许被 frozen 的取值带偏。"""
    cone = _minimal_cone()
    for c in (None, cone):
        r = ReturnProtocol().act(dict(CRITICAL), c, None)
        assert r["requires_human"] is True
        assert r["action"] == "return", "action 永远是 return，不是 auto-resume"


# --------------------------------------------------- 集成层：真跑出来的漂移


def _cone(encoder, samples):
    return DirectionCone.from_samples(
        goal="帮用户规划旅行", core_samples=samples["core"],
        boundary_samples=samples["boundary"], negative_samples=samples["negative"],
        constraints=["不直接推荐具体酒店"], embed_fn=encoder.encode)


def _detector(cone, align_fn, samples):
    """与 test_full_chain 同法标定，保证这条流确实走得到 confirmed_drift。"""
    from direction_drift.calibration.roc import calibrate_thresholds

    scores = [align_fn(cone, s)["overall_alignment"] for s in samples["core"][20:]]
    scores += [align_fn(cone, s)["overall_alignment"] for s in samples["negative"]]
    calib = calibrate_thresholds(scores, [0] * 5 + [1] * 12, target_precision=0.95)
    return DriftDetector(high=calib["suggested_high"], low=calib["suggested_low"],
                         calibrated=True)


def _drive(cone, align_fn, samples, cone_for_act):
    """12 条反面样本喂进一个新的标定 detector，返回最后一条 act 结果。"""
    det = _detector(cone, align_fn, samples)
    proto = ReturnProtocol(DiagnoseConfig(calibrated=True))
    last = None
    for _ in range(12):
        al = align_fn(cone, samples["negative"][0])
        last = proto.act(det.judge(al), cone_for_act, al)
    return last


def test_a_real_confirmed_drift_also_reports_honestly(encoder, samples, align_fn):
    """真跑出来的 confirmed_drift 走同一条口径（不是只有手工构造的字典才对）。"""
    cone = _cone(encoder, samples)

    with_cone = _drive(cone, align_fn, samples, cone)
    assert with_cone["action"] == "return", "这条流应当走到返回动作，否则本测试空转"
    assert with_cone["frozen"] is True
    assert cone.frozen is True

    without = _drive(cone, align_fn, samples, None)
    assert without["action"] == "return"
    assert without["frozen"] is False
    assert "未冻结" in without["message"]


@pytest.mark.parametrize("level,expected_action", [
    ("normal", "continue"),
    ("warning", "observe"),
    ("drift", "pause"),
    ("uncalibrated", "observe"),
    ("insufficient_history", "continue"),
])
def test_other_levels_do_not_claim_any_freeze(level, expected_action):
    """其余级别一律不出现 `frozen` 字样——只有 confirmed_drift 才谈冻结。"""
    proto = ReturnProtocol()
    r = proto.act({"drift_level": level, "history_size": 0, "needed": 5}, None, None)
    assert r["action"] == expected_action
    assert "frozen" not in r


# ------------------------------------------------- 停态不许被 normal 静默推回
#
# 本模块 docstring 逐字写着：「只有人（或显式调用 `resume()`）能让它转回 running」。
# 而 `if level == "normal"` 那一支原先无条件 `self.state = "running"` ⇒
# 一条 normal 就把 paused / returning 静默推回运行。
# ⇒ 下面三条钉住它；第三条是**负对照**（正常路不许被拦掉）。


def test_normal_does_not_silently_resume_from_paused():
    """drift 停到 paused 之后，再来一条 normal 不许把它推回 running。"""
    proto = ReturnProtocol()
    proto.act({"drift_level": "drift"}, None, None)
    assert proto.state == "paused"

    r = proto.act({"drift_level": "normal"}, None, None)

    assert proto.state == "paused", "返回是人的动作，不许被一条 normal 静默推回"
    assert r["action"] == "observe", "此时不该 continue（那等于说'方向正常'）"
    assert r.get("requires_human") is True
    assert r.get("state_kept") == "paused"


def test_normal_does_not_silently_resume_from_returning():
    """confirmed_drift 停到 returning 之后，同一条 normal 也不许推回。"""
    proto = ReturnProtocol()
    proto.act(dict(CRITICAL), _minimal_cone(), None)
    assert proto.state == "returning"

    r = proto.act({"drift_level": "normal"}, None, None)

    assert proto.state == "returning", "已移交人的状态不许自动解除"
    assert r["action"] == "observe"


def test_negative_control_normal_still_runs_when_already_running():
    """负对照：本来就 running 时，normal 必须照常 continue（否则是把正常路也拦掉了）。"""
    proto = ReturnProtocol()
    assert proto.state == "running"

    r1 = proto.act({"drift_level": "normal"}, None, None)
    r2 = proto.act({"drift_level": "normal"}, None, None)

    assert r1["action"] == "continue"
    assert r2["action"] == "continue"
    assert proto.state == "running"


def test_resume_is_the_only_way_back_and_it_works():
    """显式 `resume()` 之后，normal 应重新能 continue（守卫不许把合法返回堵死）。"""
    proto = ReturnProtocol()
    proto.act({"drift_level": "drift"}, None, None)
    assert proto.state == "paused"

    detector = DriftDetector(high=0.8, low=0.4, calibrated=True)
    proto.resume(_minimal_cone(), detector)

    assert proto.state == "running", "resume() 是回来的唯一正路"
    r = proto.act({"drift_level": "normal"}, None, None)
    assert r["action"] == "continue"


# ------------------------------------------------- resume() 全成败 + 返回落痕（v2.5.3）
#
# 原先 `resume()` 是三行顺序执行 ⇒ **任一步抛就留下半恢复态**：
# 检测器已重置、锥已解冻，而协议仍写着 `paused` —— 状态与实况不符。
# 而"该不该回去"正是这条线的价值所在 ⇒ 返回不许半途而废。
# 下面四条钉住它；★ 第二条是**回滚的真正考题**（让假件**改了一半再抛**）。


class _DetHalfway:
    """一个**改了一半再抛**的检测器替身 —— 专门用来考回滚，而不是考"恰好还没改"。"""

    def __init__(self):
        self.archive: list = []
        self.history: list = [1, 2]
        self.means: list = [0.5]
        self._cusum_s: float = 1.0
        self.state: str = "running"

    def reset(self):
        if self.history:
            self.archive.append(list(self.history))     # ① 先归档
        self.history.clear()                            # ② 清 history
        self.means.clear()                              # ③ 清 means
        raise RuntimeError("模拟：清完之后、归零之前失败")


def _proto_paused():
    proto = ReturnProtocol()
    proto.act({"drift_level": "drift"}, None, None)
    cone = _minimal_cone()
    cone.freeze("confirmed_drift")
    return proto, cone


def test_resume_failure_is_all_or_nothing():
    """★ 回滚的真正考题：假件改了一半再抛 ⇒ 三样都必须回到失败前，一样都不许留半。"""
    proto, cone = _proto_paused()
    det = _DetHalfway()
    det.state = "running"

    with pytest.raises(ResumeFailed) as ei:
        proto.resume(cone, det)

    assert ei.value.state_kept == "paused"
    assert proto.state == "paused", "协议状态不许半恢复"
    assert cone.frozen is True, "锥不许被解冻 —— 那会显形成一次成功"
    assert det.archive == [], "reset 追加的那一条归档必须撤掉"
    assert det.history == [1, 2] and det.means == [0.5], "历史与均值必须原样回来"
    assert det._cusum_s == 1.0 and det.state == "running", "CUSUM 与检测器状态也要回"


def test_resume_failure_is_visible_and_says_nothing_changed():
    """失败必须**可读出来**：异常类型 + `state_kept` + 一句"什么都没改"。"""
    proto, cone = _proto_paused()
    with pytest.raises(ResumeFailed) as ei:
        proto.resume(cone, _DetHalfway())

    msg = str(ei.value)
    assert "什么都没改" in msg
    assert "paused" in msg
    assert isinstance(ei.value.__cause__, RuntimeError), "底层原因不许被吞掉"


def test_both_outcomes_land_in_the_return_log():
    """★ 返回落痕：**两种结局都记** —— 失败不记就等于把"返回失败"整段抹掉。"""
    proto, cone = _proto_paused()
    assert proto.return_log() == [], "还没返回过，落痕应为空"

    with pytest.raises(ResumeFailed):
        proto.resume(cone, _DetHalfway())
    ok_rec = [r for r in proto.return_log()]
    assert len(ok_rec) == 1 and ok_rec[0]["ok"] is False
    assert "RuntimeError" in ok_rec[0]["error"], "失败原因要留原文，不许压成一句笼统话"
    assert ok_rec[0]["state_before"] == "paused"

    proto2 = ReturnProtocol()
    proto2.act({"drift_level": "drift"}, None, None)
    proto2.resume(_minimal_cone(), DriftDetector(high=0.8, low=0.4, calibrated=True))
    good = proto2.return_log()
    assert len(good) == 1 and good[0]["ok"] is True
    assert good[0]["state_after"] == "running"


def test_return_log_is_a_copy_not_the_internal_list():
    """落痕是**只读副本** —— 调用方改它不许动到内部记录。"""
    proto = ReturnProtocol()
    proto.act({"drift_level": "drift"}, None, None)
    proto.resume(_minimal_cone(), DriftDetector(high=0.8, low=0.4, calibrated=True))

    snap = proto.return_log()
    snap.clear()
    assert len(proto.return_log()) == 1, "外部改副本不许影响内部"


# ------------------------------------------------- revise() 全成败（v2.5.3 · 丙案）
#
# 原实现把"解冻"排在"重建"之前 ⇒ 重建一抛，锥就停在「已解冻但没重建」：
# **一次什么都没发生、却解了冻**。而方向是这个引擎的核心物，不该在失败时"半新"。
# 修法：会抛的两件事都排到"提交"之前（重建 → reset → 解冻 → state）。
# 下面三条钉住它；★ 第二、三条分别考**重建抛**与**reset 改了一半再抛**。


class _ConeStub:
    """一个可控的锥替身：能记下 rebuild 调用，也能在 rebuild 时抛。"""

    def __init__(self, fail_on_rebuild=False):
        self.axis, self.aperture, self.softness = [1.0, 0.0], 0.5, 0.1
        self.goal, self.constraints, self.values = "G", [], []
        self.negative_examples = ["old-neg"]
        self.core_sample_count, self.boundary_sample_count = 22, 8
        self.negative_sample_count = 1
        self.frozen, self.frozen_at, self.freeze_reason = True, None, "confirmed_drift"
        self.rebuild_count, self.last_rebuild_time = 3, None
        self.fail_on_rebuild = fail_on_rebuild

    def rebuild_from_samples(self, core_samples, boundary_samples=None,
                             negative_samples=None, embed_fn=None):
        if self.fail_on_rebuild:
            raise ValueError("核心样本不足：需要至少 20 个，当前 3 个。")
        self.axis, self.aperture, self.softness = [0.0, 1.0], 0.9, 0.2
        if negative_samples:
            self.negative_examples = list(negative_samples)
            self.negative_sample_count = len(negative_samples)
        self.core_sample_count = len(core_samples)
        self.boundary_sample_count = len(boundary_samples or [])
        self.rebuild_count += 1
        self.last_rebuild_time = "now"

    def unfreeze(self):
        self.frozen, self.frozen_at, self.freeze_reason = False, None, ""


def _revise_fixture():
    proto = ReturnProtocol()
    proto.act({"drift_level": "drift"}, None, None)
    assert proto.state == "paused"
    return proto


def test_revise_success_rebuilds_and_commits():
    """正常路：锥被重建、检测器重置、协议回 running，且落痕记 trigger=revise()。"""
    proto = _revise_fixture()
    cone = _ConeStub()

    class _DetOK:
        def __init__(self):
            self.archive, self.history, self.means = [], [1], [0.5]
            self._cusum_s, self.state = 1.0, "running"

        def reset(self):
            self.archive.append(list(self.history))
            self.history.clear()
            self.means.clear()
            self._cusum_s, self.state = 0.0, "warmup"

    det = _DetOK()
    r = proto.revise(cone, det, core_samples=["a"] * 24, negative_samples=["n1"])

    assert r["action"] == "continue"
    assert cone.frozen is False and cone.rebuild_count == 4
    assert det.state == "warmup" and det.history == []
    assert proto.state == "running"
    assert proto.return_log()[-1]["trigger"] == "revise()"
    assert proto.return_log()[-1]["ok"] is True


def test_revise_failure_on_rebuild_changes_nothing():
    """★ 重建就抛 ⇒ 锥不许停在「已解冻但没重建」（那是"什么都没发生却解了冻"）。"""
    proto = _revise_fixture()
    cone = _ConeStub(fail_on_rebuild=True)
    before = (list(cone.axis), cone.frozen, cone.rebuild_count, cone.negative_examples)

    with pytest.raises(ReviseFailed) as ei:
        proto.revise(cone, _DetHalfway(), core_samples=["a"] * 3)

    assert ei.value.state_kept == "paused"
    assert (list(cone.axis), cone.frozen, cone.rebuild_count,
            cone.negative_examples) == before, "锥必须一项都没动"
    assert proto.state == "paused"


def test_rollback_works_on_the_real_detector_types():
    """★ 回滚必须在**真件**上成立 —— 因为它踩过这个坑。

    `DriftDetector.history` 与 `.means` 是 **`deque(maxlen=…)`**，不是 `list`：
    最初的回滚写的是 `history[:] = snap`，在 deque 上直接 `TypeError`
    （"sequence index must be integer, not 'slice'"）。而 resume()/revise() 的
    失败测试当时都用**假 detector（history 是 list）**，所以那个形状一直没被测到——
    它是被 **API 层用真件**跑出来的。

    本条用真 `DriftDetector` 钉住类型事实，使同一个坑不会再被"假件"藏起来。
    """
    from collections import deque

    det = DriftDetector(high=0.8, low=0.4, calibrated=True)
    assert isinstance(det.history, deque) and isinstance(det.means, deque), (
        "若这两个不是 deque，回滚的 _restore_seq 说明与实现脱节了")

    # 喂点状态进去，再走一遍真实的 reset + 回滚
    det.history.extend([{"score": 0.9}, {"score": 0.3}])
    det.means.extend([0.9, 0.6])
    det._cusum_s = 0.42
    det.state = "running"
    cone = _ConeStub()
    snap = {
        "cone": (cone.frozen, cone.frozen_at, cone.freeze_reason),
        "cone_axis": cone.axis, "cone_aperture": cone.aperture, "cone_softness": cone.softness,
        "cone_neg": list(cone.negative_examples),
        "cone_counts": (cone.core_sample_count, cone.boundary_sample_count,
                        cone.negative_sample_count),
        "cone_rebuild_count": cone.rebuild_count, "cone_last_rebuild": cone.last_rebuild_time,
        "det_archive_len": len(det.archive), "det_history": list(det.history),
        "det_means": list(det.means), "det_cusum": det._cusum_s, "det_state": det.state,
    }

    det.reset()                                   # ★ 真件的重置
    assert det.history == deque() and det.state == "warmup"

    ReturnProtocol._rollback(cone, det, snap)     # ★ 真件上的回滚（不许抛）

    assert list(det.history) == [{"score": 0.9}, {"score": 0.3}], "history 要逐项回"
    assert list(det.means) == [0.9, 0.6], "means 要逐项回"
    assert det._cusum_s == 0.42 and det.state == "running"
    assert det.history.maxlen == 50, "回滚不许动 deque 的 maxlen"


def test_revise_failure_uses_real_detector_and_leaves_it_untouched():
    """★ 用**真**检测器验"重建失败 ⇒ 检测器一项没动"（不靠假件的形状）。"""
    proto = _revise_fixture()
    cone = _ConeStub(fail_on_rebuild=True)
    det = DriftDetector(high=0.8, low=0.4, calibrated=True)
    det.history.extend([{"score": 0.5}])
    det.means.extend([0.5])
    det._cusum_s, det.state = 0.7, "running"
    before = (list(det.history), list(det.means), det._cusum_s, det.state, det.archive)

    with pytest.raises(ReviseFailed):
        proto.revise(cone, det, core_samples=["太少了"])

    assert (list(det.history), list(det.means), det._cusum_s, det.state,
            det.archive) == before, "重建就失败 ⇒ 检测器不该被动过"
    assert proto.state == "paused"


def test_revise_failure_halfway_rolls_back_cone_and_detector():
    """★ 重建成了、reset 改了一半再抛 ⇒ 锥与检测器都必须回到失败前。"""
    proto = _revise_fixture()
    cone = _ConeStub()
    det = _DetHalfway()
    before = (list(cone.axis), cone.aperture, cone.frozen, cone.rebuild_count,
              cone.negative_examples, cone.core_sample_count)

    with pytest.raises(ReviseFailed):
        proto.revise(cone, det, core_samples=["a"] * 24)

    after = (list(cone.axis), cone.aperture, cone.frozen, cone.rebuild_count,
             cone.negative_examples, cone.core_sample_count)
    assert after == before, "锥必须整只退回旧的那一个"
    assert det.archive == [] and det.history == [1, 2] and det.means == [0.5]
    assert det._cusum_s == 1.0 and det.state == "running"
    assert proto.state == "paused"
    assert proto.return_log()[-1]["ok"] is False
    assert "RuntimeError" in proto.return_log()[-1]["error"]
