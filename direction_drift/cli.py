# -*- coding: utf-8 -*-
"""`de` —— 方向引擎命令行（v2.4.0 新增）。

三条命令，对应建卡三问之后的全部动作：

    de init     四问建卡 → 输出一张 JSON 场景卡
    de check    对一批输出跑检测 → 表格 + JSONL（可复算的中间产物）
    de report   把 check 的 JSONL 渲染成可交付 HTML
    de card     把卡摊开给人看 → 只读展示页 HTML（不产生任何判定）

**本 CLI 的三条自我约束**（与引擎同源，不是命令行礼貌）：

1. **不生成正类样本。** `de init` 只把你给的样本装进卡，不做任何合成。
   理由不是"没实现"，而是**循环性**：用 LLM 挑出"正确方向"，再用这些样本去判
   LLM 的输出，测出来的"漂移"就成了"与模型自身偏好的偏离"，不是"与场景真实方向的
   偏离"。需要冷启动合成时，请显式用 `examples/build_cone.py`，并接受它的
   AUC 只作健康检查（>0.7）这一前提。

2. **未标定不判定，且必须显形。** `de check` 没有 `--calibration` 时用卡内样本
   自标定，此时标定性质是 `synthetic`，分数**仅供观察**——这条会原样写进 JSONL
   与报告，不是打印一句就过去。

3. **跳过项要计数。** 空输出 / 过短输入 / 零向量不产生分数（`invalid`），
   它们不参与判定，但**仍占分母**。CLI 把它们单独列出，不静默丢弃——
   静默丢弃会让"疑似率"这个数字在分母上撒谎。

零新增依赖：只用标准库 + 引擎自身。
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from . import __version__ as _ENGINE_VERSION
from .report_html import render_report_html
from .verify import (DEFAULT_TOLERANCE, EXIT_CODES, build_artifact,
                     compare_artifacts)

# 与 README「建卡四问」对齐的硬门（build_cone 层拒绝，不是本 CLI 发明的）
_MIN_CORE = 20
_MIN_NEGATIVE = 10

_ENCODERS = {
    "demo": "demo(word-hash, 仅机械链路)",
    "sbert": "BAAI/bge-small-zh-v1.5",
}


# ---------------------------------------------------------------- 公共工具

def _make_encoder(name: str):
    """取编码器。**fail-closed**：sbert 不可用时直接失败，不降级。

    降级的代价是不出声的：DemoEncoder 与 bge 的分数水位不可比，
    用 demo 跑出的分数配 sbert 标定出的阈值，结果既不为真也不为假，只是无意义。
    """
    if name == "demo":
        from .utils.demo_encoder import DemoEncoder
        return DemoEncoder(), _ENCODERS["demo"]
    if name == "sbert":
        try:
            from .utils.encoder import Encoder
        except ImportError as exc:                      # pragma: no cover
            raise SystemExit(
                "de: 需要 sentence-transformers 才能用 --encoder sbert。\n"
                "    请先安装：pip install -e \".[sbert]\"\n"
                "    （不改用 demo 编码器是刻意的：两者的分数水位不可比，"
                "静默降级会得到既不为真也无意义的结果。）"
            ) from exc
        return Encoder(), _ENCODERS["sbert"]
    raise SystemExit(f"de: 未知编码器 {name!r}；可选 {sorted(_ENCODERS)}")


def _read_samples(path: Optional[str]) -> List[str]:
    """读样本文件：一行一条，忽略空行与 `#` 注释。"""
    if not path:
        return []
    p = Path(path)
    if not p.exists():
        raise SystemExit(f"de: 找不到文件 {p}")
    out = []
    for raw in p.read_text(encoding="utf-8").splitlines():
        s = raw.strip()
        if s and not s.startswith("#"):
            out.append(s)
    return out


def _ask(prompt: str) -> str:
    try:
        return input(prompt).strip()
    except EOFError:
        return ""


def _parse_rules(items: Optional[List[str]]) -> List[Dict[str, str]]:
    """`--rule "名字=正则"` → rule_layer 条目。"""
    rules = []
    for it in (items or []):
        if "=" not in it:
            raise SystemExit(f'de: --rule 需要 "名字=正则" 形式，收到 {it!r}')
        name, pattern = it.split("=", 1)
        rules.append({"name": name.strip(), "pattern": pattern.strip()})
    return rules


def _card_to_dict(card) -> Dict:
    return {
        "card_id": card.card_id, "goal": card.goal,
        "core_samples": card.core_samples,
        "boundary_samples": card.boundary_samples,
        "negative_samples": card.negative_samples,
        "constraints": card.constraints,
        "rule_layer": [r.model_dump() for r in card.rule_layer],
        "min_suspect_len": card.min_suspect_len,
        "format_whitelist": card.format_whitelist,
        "prompt_version": card.prompt_version,
        "notes": getattr(card, "notes", ""),
    }


# ---------------------------------------------------------------- de init

def cmd_init(args) -> int:
    goal = args.goal or _ask("① 这个智能体的业务方向是什么？（一句话）\n> ")
    if not goal:
        raise SystemExit("de init: 必须给出方向（--goal 或交互输入）")

    core = _read_samples(args.core_file)
    if not core:
        print("② 正道样本：这些是「这个方向该说的话」。")
        print(f"   至少 {_MIN_CORE} 条才能建锥（build_cone 硬门）。")
        print("   建议写进文件后用 --core-file 传入（本命令不做任何合成，原因见 --help）。")
        if sys.stdin.isatty():
            print("   现在逐条输入，空行结束：")
            while True:
                line = _ask("   > ")
                if not line:
                    break
                core.append(line)

    boundary = _read_samples(args.boundary_file)
    negative = _read_samples(args.negative_file)
    rules = _parse_rules(args.rule)
    constraints = list(args.constraint or [])

    # ---- 自检：只提示，不代改（真正的硬门在 build_cone）----
    problems = []
    if len(core) < _MIN_CORE:
        problems.append(f"core_samples 只有 {len(core)} 条，少于 {_MIN_CORE}——"
                        f"build_cone 会直接拒绝建锥")
    if len(negative) < _MIN_NEGATIVE:
        problems.append(f"negative_samples 只有 {len(negative)} 条，少于 {_MIN_NEGATIVE}——"
                        f"同上")
    if not rules:
        problems.append("rule_layer 为空：字面红线（如泄题、越权代办、泄露设定）"
                        "是语义层抓不住的东西，建议用 --rule \"名字=正则\" 补上")
    if not constraints:
        problems.append("constraints 为空：若你的红线是「动作」（如不代下单），"
                        "结构化形态是 actions 词表；自由文本形态会被 jieba 误判为名词")

    payload = {
        "card_id": args.card_id,
        "goal": goal,
        "core_samples": core,
        "boundary_samples": boundary,
        "negative_samples": negative,
        "constraints": constraints,
        "rule_layer": rules,
        "min_suspect_len": args.min_len,
        "format_whitelist": args.format_whitelist,
        "prompt_version": args.prompt_version,
        "notes": args.notes or "",
    }

    # 写盘前先过一遍模型校验（多一个字段、少一个必填都会在这里炸，不静默丢弃）
    from .scenario import ScenarioCard
    try:
        card = ScenarioCard(**payload)
    except Exception as exc:
        raise SystemExit(f"de init: 卡未通过校验——{exc}")

    out = Path(args.out)
    out.write_text(json.dumps(_card_to_dict(card), ensure_ascii=False, indent=2),
                   encoding="utf-8")
    print(f"\n已写出：{out}")
    print(f"  core={len(core)}  boundary={len(boundary)}  negative={len(negative)}  "
          f"rule_layer={len(rules)}")

    if problems:
        print("\n建卡自检（以下不是错误，是你接下来会遇到的事）：")
        for p in problems:
            print(f"  · {p}")
    else:
        print("\n建卡自检通过。下一步：")
    print("\n下一步：")
    print(f"  de check --card {out} --input 你的输出.txt")
    print("  （阈值由卡内样本自标定，性质为 synthetic——"
          "要正式判定请用真实标注标定后再传 --calibration）")
    return 0


# ---------------------------------------------------------------- de check

def _load_calibration(args, card, align) -> Tuple[Dict, List[str]]:
    """返回 (标定信息, 警告列表)。**不在缺省时假装有标定。**"""
    warns: List[str] = []
    if args.calibration:
        p = Path(args.calibration)
        if not p.exists():
            raise SystemExit(f"de check: 找不到标定文件 {p}")
        calib = json.loads(p.read_text(encoding="utf-8"))
        low, high = calib.get("low"), calib.get("high")
        if low is None or high is None:
            raise SystemExit(
                f"de check: 标定文件缺少 low/high，无法判定。"
                f"（这是刻意的：缺阈值时本命令不猜一个默认值。）")
        rec_enc = calib.get("encoder")
        if rec_enc and rec_enc not in _ENCODERS["sbert"] and args.encoder == "sbert":
            warns.append(f"标定记录的编码器是 {rec_enc!r}，本次用的是 "
                         f"{_ENCODERS['sbert']!r}——分数水位不可跨编码器比较，"
                         f"该阈值很可能不适用于本次分数。")
        if not calib.get("card_fingerprint"):
            warns.append("标定文件没有 card_fingerprint："
                         "无法判断这套阈值是否对应当前这张卡。")
        else:
            try:
                live = card.fingerprint()
                if live != calib["card_fingerprint"]:
                    warns.append(f"卡指纹不一致（标定 {calib['card_fingerprint']} "
                                 f"≠ 当前 {live}）——阈值可能已不属于当前卡。")
            except Exception:                            # pragma: no cover
                warns.append("卡指纹比对失败（算法版本可能不同），请人工确认。")
        if not calib.get("mode"):
            warns.append("标定文件没有 mode 字段：标定性质未标注，"
                         "本命令不猜测它是正式还是合成。")
        # `weights` / `card_fingerprint` 从这里起**带进产物**：它们是契约里的
        # 可比性条件（calibration.weights 缺失 ⇒ not_comparable），此前本命令
        # 只取 low/high，产出的产物天生不可复算——挑刺 S4 指出的三处未传之一。
        # 标定文件没记 weights 时**不猜**：记 null，让复算端判不可比。
        return {"low": float(low), "high": float(high),
                "auc": calib.get("auc"), "mode": calib.get("mode"),
                "source": calib.get("source", ""),
                "weights": calib.get("weights"),
                "prompt_version": calib.get("prompt_version"),
                "card_fingerprint": calib.get("card_fingerprint")}, warns

    # 无标定 → 卡内样本自标定，性质 synthetic
    from .calibration.roc import calibrate_thresholds
    scores = ([align(s) for s in card.core_samples]
              + [align(s) for s in card.negative_samples])
    labels = [0] * len(card.core_samples) + [1] * len(card.negative_samples)
    valid = [(s, l) for s, l in zip(scores, labels) if s is not None]
    if len(valid) < 2:
        raise SystemExit("de check: 卡内样本无法算出有效分数，无法自标定。")
    calib = calibrate_thresholds([s for s, _ in valid], [l for _, l in valid])
    warns.append("未提供 --calibration：阈值由卡内样本自标定，性质为 synthetic。"
                 "合成样本的 AUC 只作链路健康检查（>0.7），不构成性能估计——"
                 "分数仅供观察，不可用于复核判断。")
    # 自标定路径同样要记**实际生效的权重**：打分没传 weights，引擎回落的
    # 就是 DEFAULT_WEIGHTS（与私有仓 build_calibration.py:191 同一口径）。
    # 记 null 会让产物"天生不可复算"，而它其实是已知的。
    from .core.alignment import DEFAULT_WEIGHTS
    return {"low": calib["suggested_low"], "high": calib["suggested_high"],
            "auc": calib["auc"], "mode": "synthetic",
            "source": "卡内样本自标定（core=正常 / negative=漂移）",
            "weights": dict(DEFAULT_WEIGHTS), "prompt_version": None,
            "card_fingerprint": None}, warns


def cmd_check(args) -> int:
    from .core.alignment import AlignmentCalculator
    from .core.drift_detector import DriftDetector
    from .scenario import ScenarioCard

    card = ScenarioCard.from_file(args.card)
    enc, enc_name = _make_encoder(args.encoder)
    cone = card.build_cone(enc.encode)
    calc = AlignmentCalculator(encoder=enc)

    def align(text: str):
        return calc.compute(cone, text)["overall_alignment"]

    calib, warns = _load_calibration(args, card, align)

    texts = _read_samples(args.input)
    if not texts:
        raise SystemExit(f"de check: {args.input} 里没有可检测的文本行")

    results, skipped = [], []
    for i, text in enumerate(texts):
        a = calc.compute(cone, text)
        if a.get("invalid"):
            # 空输出 / 零向量：不产生分数，也不进判定窗口
            skipped.append({"i": i, "text": text, "level": "no_output",
                            "reason": a.get("reason"), "score": None})
            continue
        det = DriftDetector(high=calib["high"], low=calib["low"], calibrated=True)
        level = None
        for _ in range(det.window):                      # 每条按独立输出判定
            level = det.judge(a)["drift_level"]
        if level in ("too_short", "no_output"):
            skipped.append({"i": i, "text": text, "level": level, "score": None})
            continue
        rule = card.rule_hit(text)
        results.append({"i": i, "text": text,
                        "score": round(float(a["overall_alignment"]), 6),
                        "level": level, "rule": rule,
                        "suspect": bool(card.is_suspect(a["overall_alignment"], text,
                                                        calib["low"]))})

    # ---- 控制台输出 ----
    print(f"== de check ｜ 卡 {card.card_id} ｜ 编码器 {enc_name} ==")
    mode = calib.get("mode") or "未标注"
    print(f"   标定性质：{mode}"
          + (f"（AUC={calib['auc']:.3f}）" if isinstance(calib.get("auc"), (int, float))
             else "")
          + f"　low={calib['low']:.3f} high={calib['high']:.3f}")
    print(f"   输入 {len(texts)} 条：已判定 {len(results)}，跳过 {len(skipped)}，"
          f"疑似 {sum(1 for r in results if r['suspect'])}")
    for w in warns:
        print(f"   ⚠ {w}")
    print()
    if results:
        print(f"   {'#':>3} {'分':>7}  {'判定':<16} {'疑似':<4} {'规则层':<10} 输出")
        for r in results:
            print(f"   {r['i']:>3} {r['score']:>7.3f}  {r['level']:<16} "
                  f"{'是' if r['suspect'] else '—':<4} "
                  f"{(r['rule'] or '—').split('：')[0]:<10} {r['text'][:40]}")
    if skipped:
        print(f"\n   跳过 {len(skipped)} 条（不产生分数，但**仍占分母**）：")
        kinds: Dict[str, int] = {}
        for s in skipped:
            kinds[s["level"]] = kinds.get(s["level"], 0) + 1
        for k, v in sorted(kinds.items()):
            print(f"     · {k}：{v} 条")
    print("\n   读表说明：'判定'列只看语义对齐；'疑似'列是组合判据"
          "（语义低分 + 长度保护 + 格式白名单，或规则层命中）。两列可以不一致。")

    if args.jsonl:
        meta = {"card_id": card.card_id, "goal": card.goal, "encoder": enc_name,
                "calibration": calib, "warnings": warns,
                "n_input": len(texts), "n_judged": len(results),
                "n_skipped": len(skipped)}
        with open(args.jsonl, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"_meta": meta}, ensure_ascii=False) + "\n")
            for r in results:
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
            for s in skipped:
                fh.write(json.dumps({**s, "_skipped": True}, ensure_ascii=False) + "\n")
        print(f"\n   已写出 JSONL：{args.jsonl}（可复算的中间产物，"
              f"de report 读它出报告）")

    if getattr(args, "artifact", None):
        _write_artifact(args.artifact, card=card, enc_name=enc_name,
                        calib=calib, texts=texts, results=results)
        print(f"   已写出判定产物：{args.artifact}"
              f"（可被 de verify 或 POST /v1/verify 独立复算）")
    return 0


def _write_artifact(path, *, card, enc_name, calib, texts, results) -> None:
    """把这次判定装进**判定产物信封**（`verify.build_artifact`）。

    为什么单列一个开关而不是塞进 `--jsonl`：两者不是一个东西。`--jsonl` 是
    **逐条明细 + _meta**，`de report` 读它出报告；产物信封是**最小可复算溯源集**
    （判定器版本 / 卡指纹 / 编码器 / 标定 / 判定器配置 / 输入与结果摘要），
    契约由 `judgment-artifact.schema.json` 定死，第三方照它能自己复算。
    把信封并进 JSONL 会让"报告输入"和"可复算证据"两种语义互相污染。

    **不造溯源字段**：`weights_hash` 恒 None（`environment_fingerprint()`
    未接入流程），卡指纹是这次实时算的——标定文件里若有就用标定文件的，
    否则标 `backfilled`（补记的一致是弱证据，必须说出来）。
    """
    from .core.drift_detector import DriftDetector

    det = DriftDetector(high=calib["high"], low=calib["low"], calibrated=True)
    det_config = det.to_dict()["config"]

    live_fp = card.fingerprint()
    recorded_fp = calib.get("card_fingerprint")
    art = build_artifact(
        engine_version=_ENGINE_VERSION,
        card=card,
        encoder_info={"name": enc_name, "is_semantic": enc_name != "demo",
                      "weights_hash": None, "library_versions": {},
                      "notice": None},
        calibration={
            "mode": calib.get("mode"),
            "low": calib.get("low"), "high": calib.get("high"),
            "auc": calib.get("auc"), "source": calib.get("source"),
            "weights": calib.get("weights"),
            "card_fingerprint": recorded_fp or live_fp,
            "card_fingerprint_status": ("recorded_at_calibration" if recorded_fp
                                        else "backfilled"),
        },
        detector_config=det_config,
        judgment={"texts": texts, "n_texts": len(texts), "stage": "cli"},
        items=[{"index": r["i"], "score": r["score"],
                "drift_level": r["level"], "suspect": r["suspect"]}
               for r in results],
    )
    Path(path).write_text(json.dumps(art, ensure_ascii=False, indent=2),
                          encoding="utf-8")


# ---------------------------------------------------------------- de report

def _read_check_jsonl(path: str) -> Tuple[Dict, List[Dict], List[Dict]]:
    meta, results, skipped = {}, [], []
    p = Path(path)
    if not p.exists():
        raise SystemExit(f"de report: 找不到 {p}")
    for line in p.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        obj = json.loads(line)
        if "_meta" in obj:
            meta = obj["_meta"]
        elif obj.get("_skipped"):
            skipped.append(obj)
        else:
            results.append(obj)
    return meta, results, skipped


def cmd_report(args) -> int:
    from .reporting import channel_failure_signal

    meta, results, skipped = _read_check_jsonl(args.from_)
    if not meta:
        raise SystemExit("de report: JSONL 缺少 _meta 行（请用 de check --jsonl 生成）")

    suspects = [r["text"] for r in results if r.get("suspect")]
    signals = []
    if suspects:
        sig = channel_failure_signal(suspects)
        signals = sig or []

    html_doc = render_report_html(
        card_id=meta.get("card_id", "?"), goal=meta.get("goal", ""),
        encoder=meta.get("encoder", ""), calibration=meta.get("calibration"),
        results=results, skipped=skipped, signals=signals,
        title=args.title or f"方向引擎 · 检测报告（{meta.get('card_id', '?')}）",
        extra_notes=meta.get("warnings"))

    out = Path(args.out)
    out.write_text(html_doc, encoding="utf-8")
    print(f"已写出报告：{out}")
    if skipped:
        print(f"  注意：报告里有 {len(skipped)} 条未判定条目被显式列出——"
              f"它们不参与判定，但仍占分母。")
    return 0


# ---------------------------------------------------------------- de card

def cmd_card(args) -> int:
    from .card_page import render_cards_page
    from .scenario import ScenarioCard

    cards = [ScenarioCard.from_file(p) for p in args.card]

    # 多张卡与一份标定产物不能混配——标定是「某一张卡 × 某批数据」的产物。
    if args.calibration and len(cards) > 1:
        raise SystemExit(
            "de card: 多张卡不能共用一份 --calibration。"
            "标定产物只属于某一张卡，混配会得到看似可信的错误结论。")

    calib = None
    if args.calibration:
        p = Path(args.calibration)
        if not p.exists():
            raise SystemExit(f"de card: 找不到标定产物 {p}")
        calib = json.loads(p.read_text(encoding="utf-8"))

    doc = render_cards_page(cards, calibration=calib, title=args.title)
    out = Path(args.out)
    out.write_text(doc, encoding="utf-8")

    print(f"== de card ｜ {len(cards)} 张卡 → {out} ==")
    for c in cards:
        print(f"   {c.card_id}")
        print(f"     指纹 {c.fingerprint()}　"
              f"（现在算的；标定产物里记的不一定是这个）")
        print(f"     样本 core={len(c.core_samples)} "
              f"boundary={len(c.boundary_samples)} "
              f"negative={len(c.negative_samples)}"
              + ("　⚠ core 低于建锥下限 20" if len(c.core_samples) < _MIN_CORE else ""))
        print(f"     规则层 {len(c.rule_layer)} 条　约束 {len(c.constraints)} 条")
    if calib:
        print(f"   标定性质：{calib.get('mode') or '未标注'}"
              f"　卡指纹状态：{calib.get('card_fingerprint_status') or '（未记录该字段）'}")
    print("   本页只描述卡，不评价卡；不产生任何判定。")
    return 0


def cmd_verify(args) -> int:
    """比对两份判定产物：条件是否同源、数值是否对得上。

    模式 A（A/B 比对）**零依赖**：只读 JSON 与引擎的纯函数，不加载编码器，
    可在无网络、无模型的机器上跑——"可独立复算"的前提是验证方不必拥有
    与我相同的环境。

    模式 B（`--replay` 就地复算）本版未实现：它需要加载编码器重放整条判定链，
    做成半成品等于让人以为自己在复算。未实现就直说，不静默降级。
    """
    if getattr(args, "replay", None):
        print("de verify --replay 尚未实现：就地复算需要加载编码器重放整条判定链，"
              "本版只提供模式 A（A/B 比对，零依赖、离线可跑）。", file=sys.stderr)
        return EXIT_CODES["input_error"]

    if not args.a or not args.b:
        print("模式 A 需要 --a 与 --b 两份产物（或改用 --replay）。",
              file=sys.stderr)
        return EXIT_CODES["input_error"]

    try:
        a = json.loads(Path(args.a).read_text(encoding="utf-8"))
        b = json.loads(Path(args.b).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"读取产物失败：{exc}", file=sys.stderr)
        return EXIT_CODES["input_error"]

    out = compare_artifacts(a, b, tolerance=args.tolerance)

    print(f"复算结论：{out['verdict']}（exit {out['exit_code_equivalent']}）")
    if out["verdict"] == "not_comparable":
        print("  **不可比不是不一致**：条件不同则结论不可对话，别把它读成回归。")
    for layer, title in (("comparability", "可比性"), ("provenance", "同源性"),
                         ("numeric", "数值")):
        rows = [c for c in out["checks"] if c["layer"] == layer]
        if not rows:
            continue
        print(f"\n[{title}]")
        for c in rows:
            ok = c["status"] in ("equal", "within_tolerance")
            delta = f"（Δ={c['delta']}）" if c.get("delta") is not None else ""
            print(f"  {'=' if ok else '!'} {c['key']}: {c['status']}{delta}")
    if out["residuals"]["per_item"]:
        print(f"\n[残差] max_abs_delta={out['residuals']['max_abs_delta']}"
              f"（tolerance={args.tolerance}）")
        for r in out["residuals"]["per_item"]:
            print(f"  items[{r['index']}] Δ={r['delta']}")
    for n in out["notes"]:
        print(f"\n告知：{n}")

    if args.json_out:
        Path(args.json_out).write_text(json.dumps(out, ensure_ascii=False, indent=2),
                                       encoding="utf-8")
        print(f"\n机器可读报告已写入：{args.json_out}")

    return out["exit_code_equivalent"]


# ---------------------------------------------------------------- 入口

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="de", description="方向引擎命令行：建卡 / 检测 / 出报告。只显形，不修正。")
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("init", help="四问建卡，输出一张 JSON 场景卡")
    a.add_argument("--card-id", required=True)
    a.add_argument("--goal", help="业务方向（不给则交互提问）")
    a.add_argument("--core-file", help="正道样本，一行一条（≥20 条才能建锥）")
    a.add_argument("--boundary-file", help="边界样本，一行一条")
    a.add_argument("--negative-file", help="典型跑偏样本，一行一条（≥10 条）")
    a.add_argument("--constraint", action="append",
                   help="约束条款，可重复。动作类红线请用结构化形态（见文档）")
    a.add_argument("--rule", action="append",
                   help='字面红线，"名字=正则"，可重复。例：--rule "泄题=(提示词|系统指令)"')
    a.add_argument("--min-len", type=int, default=12, dest="min_len",
                   help="短于此长度的输出不算疑似（场景策略，默认 12）")
    a.add_argument("--format-whitelist", dest="format_whitelist",
                   help="合法格式行的正则豁免")
    a.add_argument("--prompt-version", default="1.0", dest="prompt_version",
                   help="标定保质期锚点：改提示词后同步改它，否则标定会静默过期")
    a.add_argument("--notes", help="给人读的说明（不参与判定，也不进指纹）")
    a.add_argument("--out", required=True)
    a.set_defaults(func=cmd_init)

    b = sub.add_parser("check", help="对一批输出跑检测")
    b.add_argument("--card", required=True)
    b.add_argument("--input", required=True, help="待检测文本，一行一条")
    b.add_argument("--calibration", help="标定产物 JSON；不给则用卡内样本自标定")
    b.add_argument("--encoder", choices=sorted(_ENCODERS), default="demo")
    b.add_argument("--jsonl", help="把可复算的中间产物写到该文件")
    b.add_argument("--artifact",
                   help="把本次判定写成**判定产物信封** JSON（最小可复算溯源集，"
                        "供 de verify / POST /v1/verify 独立复算）")
    b.set_defaults(func=cmd_check)

    c = sub.add_parser("report", help="把 check 的 JSONL 渲染成可交付 HTML")
    c.add_argument("--from", required=True, dest="from_", help="de check 的 --jsonl 产物")
    c.add_argument("--out", required=True)
    c.add_argument("--title")
    c.set_defaults(func=cmd_report)

    d = sub.add_parser("card", help="渲染场景卡展示页（把卡摊开给人看，只读）")
    d.add_argument("--card", required=True, action="append",
                   help="卡 JSON 文件，可重复（多于一张时自动分页签）")
    d.add_argument("--calibration",
                   help="标定产物 JSON；只可与单张卡同时使用（会显示阈值与卡指纹比对）")
    d.add_argument("--out", required=True)
    d.add_argument("--title")
    d.set_defaults(func=cmd_card)

    e = sub.add_parser("verify",
                       help="比对两份判定产物：条件是否同源、数值是否对得上")
    e.add_argument("--a", help="基准产物 JSON（模式 A）")
    e.add_argument("--b", help="对照产物 JSON（模式 A）")
    e.add_argument("--replay", help="模式 B：就地复算（本版未实现）")
    e.add_argument("--tolerance", type=float, default=DEFAULT_TOLERANCE,
                   help=f"逐条分数的容差（默认 {DEFAULT_TOLERANCE}）——实测残差"
                        "分布很宽，它是显式参数而非内置常量")
    e.add_argument("--json", dest="json_out", metavar="PATH",
                   help="机器可读报告输出路径")
    e.set_defaults(func=cmd_verify)

    return p


def main(argv: Optional[List[str]] = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:                                    # pragma: no cover
        pass
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":                               # pragma: no cover
    raise SystemExit(main())
