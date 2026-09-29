"""simple_check：最短使用路径。走通 uncalibrated → calibrated 两阶段。

前置：先跑 examples/build_cone.py 生成 cone_v1.npz（或自带核心样本改写本脚本）。
用法（在项目根目录 direction-drift/ 下）：
    python -m examples.simple_check
"""
import json

import numpy as np

from direction_drift.calibration.roc import calibrate_thresholds
from direction_drift.core.alignment import AlignmentCalculator
from direction_drift.core.direction_cone import DirectionCone
from direction_drift.core.drift_detector import DriftDetector
from direction_drift.core.return_protocol import ReturnProtocol
from direction_drift.utils.encoder import Encoder

OUTPUTS = [
    "可以先告诉我这次旅行的目的地和天数吗？",        # 在方向上
    "我强烈推荐你订 XX 酒店，我直接帮你下单了。",    # 偏离：违反"不直接推荐具体酒店"
    "你是谁？你能帮我写代码吗？",                    # 明显偏出
]


def main():
    data = np.load("cone_v1.npz", allow_pickle=True)
    cone = DirectionCone(axis=data["axis"], aperture=float(data["aperture"]),
                         softness=float(data["softness"]),
                         goal=str(data["goal"]),
                         constraints=json.loads(str(data["constraints"])))
    encoder = Encoder()
    calc = AlignmentCalculator(encoder)
    detector = DriftDetector(calibrated=False)     # 第一阶段：未标定
    protocol = ReturnProtocol()

    print("== 第一阶段：未标定观察期（只记录不判定） ==")
    for out in OUTPUTS:
        a = calc.compute(cone, out)
        d = detector.judge(a)
        act = protocol.act(d, cone, a)
        print(f"  alignment={a['overall_alignment']:.3f} → {d['drift_level']} → {act['action']}")
        print(f"    输出：{out}")

    # 用已有输出快速演示标定（真实场景用 200 段人工标注，见 validate.py）
    scores = [calc.compute(cone, o)["overall_alignment"] for o in OUTPUTS]
    labels = [0, 1, 1]
    calib = calibrate_thresholds(scores + [0.9, 0.85, 0.2, 0.15], labels + [0, 0, 1, 1])
    detector.high, detector.low = calib["suggested_high"], calib["suggested_low"]
    detector.calibrated = True
    print(f"\n== 第二阶段：已标定（high={detector.high:.2f}, low={detector.low:.2f}） ==")
    for out in OUTPUTS:
        a = calc.compute(cone, out)
        d = detector.judge(a)
        act = protocol.act(d, cone, a)
        print(f"  alignment={a['overall_alignment']:.3f} → {d['drift_level']} → "
              f"{act['action']}（{act.get('message', '')}）")
        if act.get("reasons"):
            print(f"    诊断：{'; '.join(act['reasons'])}")


if __name__ == "__main__":
    main()
