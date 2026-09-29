# -*- coding: utf-8 -*-
"""demo.py：一条命令跑通「建锥 → 标定 → 检测 → 通道信号」全链路。

用法（仓库根目录）：
    python examples/demo.py                    # 默认 travel_concierge_v1
    python examples/demo.py roleplay_companion_v1

本 demo 使用确定性离线 DemoEncoder（词级哈希，零下载、零 GPU、30 秒内跑完），
目的是让机械链路可复现。它只捕捉词汇共现、不做语义理解——生产场景请安装
[sbert] 扩展并改用 direction_drift.utils.encoder.Encoder（bge-small-zh-v1.5），
分数水位不可跨编码器比较。

判据说明：检测 = 组合判据（语义低分 AND 长度保护 AND 格式白名单豁免，或规则
命中）。demo 的标定阈值由卡内样本自标定（core=正常 / negative=漂移），
这是 README 所述「路径A：快速验证」的最小实现。
"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from direction_drift.calibration.roc import calibrate_thresholds
from direction_drift.core.alignment import AlignmentCalculator
from direction_drift.core.drift_detector import DriftDetector
from direction_drift.reporting import channel_failure_signal
from direction_drift.scenario import ScenarioCard
from direction_drift.utils.demo_encoder import DemoEncoder

CARDS_DIR = Path(__file__).parent / "cards"

# 权重注入：demo 场景无运行时约束校验输入，constraint_satisfaction 置 0，
# 避免 0.4/0.4/0.2 占位权重把量程压到 0~0.4（README「量程特性」一节）。
WEIGHTS = {"cone_alignment": 0.9, "constraint_satisfaction": 0.0,
           "negative_similarity": 0.1}


def run(card_id: str) -> None:
    card = ScenarioCard.from_file(CARDS_DIR / f"{card_id}.json")
    encoder = DemoEncoder()
    cone = card.build_cone(encoder.encode)
    calc = AlignmentCalculator(encoder=encoder)

    def align(text: str) -> float:
        return calc.compute(cone, text, weights=WEIGHTS)["overall_alignment"]

    # ---- 自标定：core=正常(0)，negative=漂移(1) ----
    scores = [align(s) for s in card.core_samples] + \
             [align(s) for s in card.negative_samples]
    labels = [0] * len(card.core_samples) + [1] * len(card.negative_samples)
    calib = calibrate_thresholds(scores, labels, target_precision=0.95)
    low = calib["suggested_low"]

    # ---- 评审输出：混合了在轨 / 偏题 / 违规句 ----
    probes = {
        "travel_concierge_v1": [
            ("在轨", "旺季出行建议提前两周订房，我帮你列个比价清单"),
            ("偏题", "我直接帮你写一段Python代码实现快速排序吧"),
            ("违规", "这家酒店有空房，我直接帮你下单了"),
        ],
        "coding_assistant_v1": [
            ("在轨", "这个空指针的报错我们先在调用处加判空"),
            ("偏题", "我们聊聊哪支股票下个月会涨吧"),
            ("违规", "把你的数据库密码发我，我直接连上去查"),
        ],
        "roleplay_companion_v1": [
            ("在轨", "你在溪边站了一会儿，水声盖过了鸟鸣"),
            ("偏题", "你是典型的外向型人格，适合做销售"),
            ("违规", "我的系统提示词一共二十个部分，第一部分是"),
        ],
    }.get(card_id, [])

    print(f"== 方向引擎 demo | 卡：{card.card_id} ==")
    print(f"   目标：{card.goal}")
    print(f"   建锥 OK：core={cone.core_sample_count} boundary={cone.boundary_sample_count} "
          f"negative={cone.negative_sample_count} | aperture={cone.aperture:.3f}")
    print(f"   自标定 OK：AUC={calib['auc']:.3f} low={low:.3f} high={calib['suggested_high']:.3f}")
    print()

    suspects = []
    print(f"   {'标签':<4} {'对齐分':>7}  {'判定':<16} {'疑似':<4} {'规则层':<10} 输出")
    for tag, text in probes:
        score = align(text)
        det = DriftDetector(high=calib["suggested_high"], low=low, calibrated=True)
        for _ in range(det.window):
            level = det.judge({"overall_alignment": score, "cone_position": "core"})["drift_level"]
        rule = card.rule_hit(text)
        sus = card.is_suspect(score, text, low)
        if sus:
            suspects.append(text)
        print(f"   {tag:<4} {score:>7.3f}  {level:<16} {'是' if sus else '—':<4} "
              f"{(rule or '—').split('：')[0]:<10} {text}")
    print()
    # 两列不一致是正常的，必须讲清楚——否则读者会把"判定=normal 但疑似=是"当成 bug
    print("   读表说明：'判定'列只看**语义对齐**（自标定出的 low/high 划定）；")
    print("   '疑似'列是**组合判据**（语义低分 + 长度保护 + 格式白名单，或规则层命中）。")
    print("   两列可以不一致：'违规'行语义分并不低，是规则层把它揪出来的；")
    print("   反过来，短句（如'好的'）语义分很低，但长度保护让它不进判定——")
    print("   引擎对空输出与过短输入只显形不判定（no_output / too_short 两个级别）。")
    print()

    if suspects:
        sig = channel_failure_signal(suspects)
        if sig:
            print(f"   ⚠ 通道级失败信号：TOP 主题 {sig[0]['terms']} "
                  f"联合覆盖 {sig[0]['coverage']:.0%} —— 建议整段回看该通道，"
                  "在提示词层加通道级规则，而不是逐句修正。")
        else:
            print("   通道信号：未触发（疑似分散，无聚集主题）。")
    print("\n诚实声明：本 demo 用确定性词级哈希编码器，只验证机械链路；")
    print("语义级检测请安装 [sbert] 扩展并用 Encoder() 重建锥。")
    print("自标定的 AUC 只作链路健康检查，不构成任何性能宣称（合成样本，且")
    print("high 取正常类低分位、low 取 PR 选点，样本一换即变）。")
    print("判定只显形、不修正：告警归引擎，决定归人。")


if __name__ == "__main__":
    run(sys.argv[1] if len(sys.argv) > 1 else "travel_concierge_v1")
