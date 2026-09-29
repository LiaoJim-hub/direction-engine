"""冷启动全链路：LLM合成本 → 建锥（留出5个核心样本）→ 初始标定 → 可判定。

循环性说明：初始标定用的是合成样本，AUC 只作健康检查（>0.7），
不作性能估计；真实阈值以 200 段真实标注（validate.py）为准。

用法（在项目根目录 direction-drift/ 下）：
    python -m examples.build_cone
需要：openai 库 + OPENAI_API_KEY 环境变量（或自行改写 llm_call 接入任意 LLM）。
"""
import json
from direction_drift.calibration.roc import calibrate_thresholds
from direction_drift.core.direction_cone import DirectionCone
from direction_drift.core.drift_detector import DriftDetector
from direction_drift.core.sample_builder import SampleBuilder
from direction_drift.utils.encoder import Encoder

GOAL = "帮用户规划旅行"
CONSTRAINTS = ["不直接推荐具体酒店", "每次只问一个问题"]
VALUES = ["引导而非替代"]


def llm_call(prompt: str) -> str:
    """接入任意 LLM。此处以 OpenAI 兼容接口为例。"""
    from openai import OpenAI
    client = OpenAI()
    resp = client.chat.completions.create(
        model="gpt-4o-mini",
        messages=[{"role": "user", "content": prompt}],
        temperature=0.8)
    return resp.choices[0].message.content


def main():
    encoder = Encoder()
    builder = SampleBuilder(llm_call, embed_fn=encoder.encode)
    samples = builder.build(GOAL, CONSTRAINTS)
    print(f"合成样本：core={len(samples['core'])}, boundary={len(samples['boundary'])}, "
          f"negative={len(samples['negative'])}（请人工抽检后再继续）")

    # 留出法：20 个建锥，5 个核心样本 + 全部反面样本做初始标定（降低循环性）
    cone = DirectionCone.from_samples(
        goal=GOAL, core_samples=samples["core"][:20],
        boundary_samples=samples["boundary"], negative_samples=samples["negative"],
        constraints=CONSTRAINTS, values=VALUES, embed_fn=encoder.encode)

    from direction_drift.core.alignment import AlignmentCalculator
    calc = AlignmentCalculator(encoder)
    scores, labels = [], []
    for s in samples["core"][20:]:
        scores.append(calc.compute(cone, s)["overall_alignment"]); labels.append(0)
    for s in samples["negative"]:
        scores.append(calc.compute(cone, s)["overall_alignment"]); labels.append(1)

    result = calibrate_thresholds(scores, labels)
    print(f"初始标定：AUC={result['auc']:.3f}, "
          f"high={result['suggested_high']:.3f}, low={result['suggested_low']:.3f}")
    if result["auc"] <= 0.7:
        print("警告：合成样本 AUC ≤ 0.7，锥或样本质量可疑，请先人工抽检样本")

    detector = DriftDetector(high=result["suggested_high"],
                             low=result["suggested_low"], calibrated=True)
    # ……（detector / cone 可序列化保存，供 simple_check 与 API 使用）……
    import numpy as np
    np.savez("cone_v1.npz",
             axis=cone.axis, aperture=cone.aperture, softness=cone.softness,
             goal=GOAL, constraints=json.dumps(CONSTRAINTS, ensure_ascii=False),
             high=result["suggested_high"], low=result["suggested_low"])
    print("已保存 cone_v1.npz（供 simple_check 使用）")


if __name__ == "__main__":
    main()
