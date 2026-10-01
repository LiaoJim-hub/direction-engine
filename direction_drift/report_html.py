# -*- coding: utf-8 -*-
"""可交付报告渲染（HTML）。

**为什么单独一个模块**：它是 `de report` 与运营侧巡检报告共用的渲染层。
渲染层只负责"把已有结论排成能给人的样子"，**不产生任何判定**——
所以它可以被两边共用，而两边各自的判定口径仍留在各自那里。

渲染层必须原样透传的四件东西（不是排版细节，是纪律）：

1. **标定性质三值**（`formal` / `synthetic` / `未标注`）——不得折叠成布尔。
   缺字段时必须显示"未标注"，**不许默认成任何一边**（沿用 `calib_meta` 的口径）。
2. **跳过的条数**（`no_output` / `too_short` / `invalid`）——必须显形。
   把它们静默丢掉，会让"疑似率"这个数字在分母上撒谎。
3. **编码器名**——分数水位不可跨编码器比较，报告不写编码器等于给了个无法复现的数。
4. **边界声明**——"未发现 ≠ 保证合规"、"只显形不修正"。这两句是免责线，也是诚实线。

本模块零依赖（只用标准库），不引入任何模板引擎。
"""
import html
from datetime import datetime
from typing import Dict, List, Optional

_STYLE = """
:root{--bg:#fff;--fg:#1f2328;--muted:#57606a;--line:#d8dee4;--warn:#9a3412;--warnbg:#fff4ec;
--ok:#0f6e56;--okbg:#e9f7f2;--bad:#a32d2d;--badbg:#fdecec;--key:#f6f8fa}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
font:15px/1.75 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif}
.wrap{max-width:820px;margin:0 auto;padding:32px 20px 64px}
h1{font-size:22px;font-weight:600;margin:0 0 4px}
h2{font-size:17px;font-weight:600;margin:36px 0 12px;padding-bottom:6px;border-bottom:1px solid var(--line)}
.sub{color:var(--muted);font-size:13px;margin:0 0 8px}
.banner{border-radius:10px;padding:14px 16px;margin:20px 0;font-size:14px;line-height:1.7}
.banner b{font-weight:600}
.b-ok{background:var(--okbg);border-left:4px solid var(--ok)}
.b-warn{background:var(--warnbg);border-left:4px solid var(--warn)}
.b-bad{background:var(--badbg);border-left:4px solid var(--bad)}
.kv{display:flex;flex-wrap:wrap;gap:8px 26px;margin:12px 0}
.kv div{font-size:14px}
.kv span{color:var(--muted)}
table{width:100%;border-collapse:collapse;margin:12px 0;font-size:14px}
th,td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--line);vertical-align:top}
th{font-weight:600;color:var(--muted);font-size:13px}
td.num{font-variant-numeric:tabular-nums;white-space:nowrap}
.item{border:1px solid var(--line);border-radius:8px;padding:10px 12px;margin:8px 0;background:var(--key)}
.item .meta{font-size:12px;color:var(--muted);margin-bottom:6px}
.item .txt{font-size:14px;white-space:pre-wrap;word-break:break-word}
ul{margin:8px 0;padding-left:22px}
li{margin:4px 0}
.foot{margin-top:40px;padding-top:16px;border-top:1px solid var(--line);
color:var(--muted);font-size:13px}
@media print{.wrap{padding:0}body{font-size:12px}}
"""

# 标定性质 → (横幅类, 标题, 正文)。三值，缺一不可。
_MODE_BANNER = {
    "formal": ("b-ok", "正式标定",
               "本页阈值来自该场景的真实人工标注。**分数可用于复核判断**，"
               "但仍受编码器与提示词版本约束——换了任一个，阈值即失效。"),
    "synthetic": ("b-warn", "临时标定（合成样本）",
                  "本页阈值由合成样本自标定。**分数仅供观察，不可用于复核判断**。"
                  "合成样本的 AUC 只作链路健康检查，不构成性能估计。"),
    "未标注": ("b-bad", "标定性质未标注",
               "标定产物**没有申报自己是正式还是合成**。本报告不猜测："
               "**请勿据此判断分数可信度**，并请人工确认后补写 `mode` 字段。"),
}


def _esc(s) -> str:
    return html.escape("" if s is None else str(s))


def mode_banner(mode: Optional[str]) -> str:
    """三值横幅。未知值一律落到"未标注"，**不折叠成任何一边**。"""
    cls, title, body = _MODE_BANNER.get(mode or "未标注", _MODE_BANNER["未标注"])
    return (f'<div class="banner {cls}"><b>标定性质：{_esc(title)}</b><br>'
            f'{_esc(body).replace("**", "")}</div>')


def render_report_html(*, card_id: str, goal: str, encoder: str,
                       calibration: Optional[Dict] = None,
                       results: List[Dict],
                       signals: Optional[List[Dict]] = None,
                       skipped: Optional[List[Dict]] = None,
                       extra_notes: Optional[List[str]] = None,
                       title: Optional[str] = None) -> str:
    """渲染一份可交付报告。

    `results`：已判定条目，每项 `{i, text, score, level, suspect, rule}`。
    `skipped`：**未能判定的条目**（`no_output` / `too_short` / `invalid`），
              必须传进来——它们是分母的一部分，藏起来等于让疑似率撒谎。
    `calibration`：标定产物（至少含 `mode`；有 `low`/`high` 时一并印出）。
    """
    results = results or []
    skipped = skipped or []
    total = len(results) + len(skipped)
    suspects = [r for r in results if r.get("suspect")]

    mode = (calibration or {}).get("mode")
    parts: List[str] = []
    A = parts.append

    A(f'<div class="wrap">')
    A(f'<h1>{_esc(title or "方向引擎 · 检测报告")}</h1>')
    A(f'<p class="sub">卡：{_esc(card_id)}　｜　生成于 '
      f'{_esc(datetime.now().strftime("%Y-%m-%d %H:%M"))}</p>')
    A(f'<p class="sub">方向定义：{_esc(goal)}</p>')

    # ---- 标定性质横幅（三值）----
    A(mode_banner(mode))
    if calibration and calibration.get("low") is not None:
        A(f'<div class="kv">'
          f'<div><span>low</span> {_esc(calibration.get("low"))}</div>'
          f'<div><span>high</span> {_esc(calibration.get("high"))}</div>'
          f'<div><span>AUC</span> {_esc(calibration.get("auc"))}</div>'
          f'<div><span>编码器</span> {_esc(encoder)}</div></div>')
    else:
        A(f'<div class="kv"><div><span>编码器</span> {_esc(encoder)}</div>'
          f'<div><span>阈值</span> 无（未标定）</div></div>')

    # ---- 关键数字：分母必须完整 ----
    A('<h2>汇总</h2>')
    A('<div class="kv">'
      f'<div><span>输入</span> {total} 条</div>'
      f'<div><span>已判定</span> {len(results)} 条</div>'
      f'<div><span>未判定（已跳过）</span> {len(skipped)} 条</div>'
      f'<div><span>疑似</span> {len(suspects)} 条'
      + (f'（占已判定 {len(suspects) / len(results) * 100:.0f}%）' if results else '')
      + '</div></div>')
    if skipped:
        kinds: Dict[str, int] = {}
        for s in skipped:
            kinds[s.get("level", "unknown")] = kinds.get(s.get("level", "unknown"), 0) + 1
        detail = "、".join(f"{k} {v} 条" for k, v in sorted(kinds.items()))
        A(f'<div class="banner b-warn"><b>有 {len(skipped)} 条未能判定</b>（{_esc(detail)}）。'
          f'这些条目的分数是任意的，引擎不把它们计入判定——'
          f'**它们仍占分母**，故上表疑似率以"已判定"为基数。</div>')

    # ---- 疑似清单 ----
    A('<h2>疑似清单</h2>')
    if not suspects:
        A('<p>本次未发现疑似条目。</p>')
    else:
        for r in suspects:
            why = []
            if r.get("rule"):
                why.append(f"规则层命中：{r['rule']}")
            if r.get("score") is not None and calibration and calibration.get("low") is not None:
                try:
                    if float(r["score"]) < float(calibration["low"]):
                        why.append(f"语义低分（{r['score']} &lt; low {calibration['low']}）")
                except (TypeError, ValueError):
                    pass
            if not why:
                why.append("命中组合判据")
            A(f'<div class="item">'
              f'<div class="meta">#{_esc(r.get("i"))}　分 {_esc(r.get("score"))}　'
              f'判定 {_esc(r.get("level"))}　｜　{_esc("；".join(why))}</div>'
              f'<div class="txt">{_esc(r.get("text"))}</div></div>')

    # ---- 通道信号 ----
    if signals:
        A('<h2>通道信号</h2>')
        A('<p>疑似条目在主题上聚集时，说明问题可能不在单条输出，而在于某条通道整体绕过了方向约束。</p>')
        for s in signals:
            terms = "、".join(s.get("terms", []))
            cov = s.get("coverage")
            cov_txt = f"联合覆盖 {cov * 100:.0f}%" if isinstance(cov, (int, float)) else ""
            A(f'<ul><li>主题 [{_esc(terms)}]　{_esc(cov_txt)}——'
              f'建议整段回看该通道，在提示词层加通道级规则，而不是逐句修正。</li></ul>')

    # ---- 归因与建议 ----
    A('<h2>建议（不代改）</h2>')
    A('<ul>')
    if suspects:
        A('<li>先复核规则层命中的条目——违反明令红线的字面项，优先级高于语义低分项。</li>')
        A('<li>语义低分项按其所在通道成组回看，避免逐句修正消耗人力。</li>')
    else:
        A('<li>本次未发现疑似，继续保持积累。</li>')
    for n in (extra_notes or []):
        A(f'<li>{_esc(n)}</li>')
    A('<li>本引擎不修改你的智能体、不自动触发纠正动作。<b>告警归引擎，决定归人。</b></li>')
    A('</ul>')

    # ---- 边界声明 ----
    A('<div class="foot">'
      '<p><b>边界声明</b></p>'
      '<ul>'
      '<li><b>未发现 ≠ 保证合规。</b>本报告只覆盖已输入的条目与当前规则层。</li>'
      '<li>本报告只显形、不修正；不承诺降低事故，只提供看见与证据。</li>'
      '<li>检测结论受编码器、权重、提示词版本约束。上述任一变化后，请重新标定。</li>'
      f'<li>编码器：{_esc(encoder)}。分数水位不可跨编码器比较。</li>'
      '</ul></div>')
    A('</div>')

    return ("<!DOCTYPE html>\n<html lang=\"zh-CN\">\n<head>\n"
            "<meta charset=\"utf-8\">\n"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
            f"<title>{_esc(title or '方向引擎 · 检测报告')}</title>\n"
            f"<style>{_STYLE}</style>\n</head>\n<body>\n"
            + "\n".join(parts) + "\n</body>\n</html>\n")
