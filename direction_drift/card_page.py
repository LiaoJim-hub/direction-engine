# -*- coding: utf-8 -*-
"""场景卡展示页（HTML）——把一张卡「摊开给人看」。

**它是什么**：卡是数据，但 JSON 文件不是给人读的。这个模块把一张卡渲染成
一页只读 HTML：卡里有什么、每一项归哪条通路用、哪些参与判定、哪些不参与、
样本够不够硬门。用来对照、评审、交付，**不产生任何判定**。

**它不是什么**：不是检测报告。报告讲"这批输出偏没偏"，展示页讲"这张卡长什么样"。
两者共用同一套视觉，但内容正交。

渲染层必须原样透传的五件东西（与 `report_html` 同源，同样不是排版细节）：

1. **阈值不在卡里。** 卡只回答"查什么"，不回答"多低算偏"——这句必须显式写在页面上，
   否则看卡的人会以为有一张卡就够了。
2. **指纹是「现在」算的**，不是标定当时记录的。页面必须区分这两件事：
   展示页算出的指纹只能证明"此刻这张卡是什么"，不能证明"阈值是对着它标的"。
3. **自由文本约束是启发式**，且已知失效模式（分词器把动作词标成名词）。
   必须在页面上标出来——把启发式读成"已声明即已检查"，是这张卡最危险的地方。
4. **卡能加载 ≠ 卡能用。** 样本族的硬门（core ≥ 20、negative ≥ 10）由建锥层拒绝，
   不在加载层。展示页把"当前条数 / 下限"并排印出来，让缺口一眼可见。
5. **`notes` 不参与判定、不进指纹。** 它是唯一的自由文本字段，得说清它的位置。

本模块零依赖（只用标准库），不引入模板引擎。
"""
import html
from datetime import datetime
from typing import Dict, List, Optional, Sequence

from .scenario import FINGERPRINT_VERSION
from .verify import fingerprint_line

# 建锥层的硬门，与 `DirectionCone.from_samples` 一致（此处只用于"并排显示缺口"，不做判定）
MIN_CORE = 20
MIN_NEGATIVE = 10

_STYLE = """
:root{--bg:#fff;--fg:#1f2328;--muted:#57606a;--line:#d8dee4;--key:#f6f8fa;
--sem:#1f4e79;--sembg:#eef4fb;--lit:#7a3e00;--litbg:#fdf3e7;
--ok:#0f6e56;--okbg:#e9f7f2;--warn:#9a3412;--warnbg:#fff4ec;--bad:#a32d2d;--badbg:#fdecec}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
font:15px/1.75 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif}
.wrap{max-width:900px;margin:0 auto;padding:32px 20px 64px}
h1{font-size:22px;font-weight:600;margin:0 0 4px}
h2{font-size:17px;font-weight:600;margin:34px 0 12px;padding-bottom:6px;border-bottom:1px solid var(--line)}
h3{font-size:15px;font-weight:600;margin:18px 0 8px}
.sub{color:var(--muted);font-size:13px;margin:0 0 6px;line-height:1.6}
.pill{display:inline-block;font-family:ui-monospace,Consolas,monospace;font-size:13px;
background:var(--key);border:1px solid var(--line);border-radius:999px;padding:2px 10px;margin:2px 6px 2px 0}
.banner{border-radius:10px;padding:14px 16px;margin:18px 0;font-size:14px;line-height:1.7}
.banner b{font-weight:600}
.b-ok{background:var(--okbg);border-left:4px solid var(--ok)}
.b-warn{background:var(--warnbg);border-left:4px solid var(--warn)}
.b-bad{background:var(--badbg);border-left:4px solid var(--bad)}
.b-key{background:var(--key);border-left:4px solid var(--muted)}
.paths{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin:14px 0}
.path{border-radius:10px;padding:12px 14px;font-size:14px;line-height:1.7}
.path.sem{background:var(--sembg);border-left:4px solid var(--sem)}
.path.lit{background:var(--litbg);border-left:4px solid var(--lit)}
.path .t{font-weight:600;display:block;margin-bottom:4px}
.cols{display:grid;grid-template-columns:repeat(3,1fr);gap:12px;margin:12px 0;align-items:start}
.col{border:1px solid var(--line);border-radius:10px;padding:12px;background:var(--key)}
.col .h{font-weight:600;font-size:14px;margin-bottom:2px}
.col .role{font-size:12px;color:var(--muted);margin-bottom:8px;line-height:1.55}
.col .cnt{font-family:ui-monospace,Consolas,monospace;font-size:13px;margin-bottom:8px}
ol.samples{margin:0;padding-left:20px;font-size:13px;line-height:1.7}
ol.samples li{margin:3px 0;word-break:break-word}
table{width:100%;border-collapse:collapse;margin:10px 0;font-size:14px}
th,td{text-align:left;padding:8px 10px;border-bottom:1px solid var(--line);vertical-align:top}
th{font-weight:600;color:var(--muted);font-size:13px}
code{font-family:ui-monospace,Consolas,monospace;font-size:13px;
background:var(--key);border:1px solid var(--line);border-radius:4px;padding:1px 5px;
word-break:normal;overflow-wrap:break-word}
/* 用 break-word 而非 anywhere：anywhere 会参与 min-content 计算，
   让表格把「min_suspect_len」这种短 token 的列挤到折行。*/
.tag{display:inline-block;font-size:12px;border-radius:4px;padding:1px 7px;margin-left:6px;
border:1px solid var(--line);background:#fff;color:var(--muted)}
.tag.rec{color:var(--ok);border-color:var(--ok)}
.tag.heu{color:var(--warn);border-color:var(--warn)}
.notes{background:var(--key);border:1px solid var(--line);border-radius:10px;
padding:12px 14px;font-size:14px;white-space:pre-wrap;line-height:1.75}
.foot{margin-top:40px;padding-top:16px;border-top:1px solid var(--line);
color:var(--muted);font-size:13px}
.foot ul{margin:8px 0;padding-left:22px}
/* 纯 CSS 页签。**input / label / pane 必须是同一父节点的直接子元素**：
   通用兄弟选择器 `~` 跨不过容器——若把 input 包进一层 div、panel 留在外面，
   `#t0:checked ~ #pane-t0` 永远匹配不上，所有面板停在 display:none，
   页面只剩页脚（2026-10-01 实际翻过这个车，故在此立碑）。 */
.cardtab{display:none}
input.cardtab+label{cursor:pointer;display:inline-block;font-size:13px;
padding:6px 14px;border:1px solid var(--line);border-radius:999px;
background:var(--key);color:var(--fg);margin:20px 8px 14px 0}
.cardtab:checked+label{background:var(--fg);color:#fff;border-color:var(--fg)}
.pane{display:none}
@media (max-width:720px){
 .cols{grid-template-columns:1fr}
 .paths{grid-template-columns:1fr}
 .wrap{padding:20px 14px 48px}
}
@media print{.wrap{padding:0}input.cardtab+label{display:none}.pane{display:block!important}body{font-size:12px}}
"""

_STRUCTURED_ROLE = ("结构化动作声明（推荐）——引擎做<b>精确子串匹配</b>，"
                    "任一声明动作短语出现即违规。声明了 <code>objects</code> 则升级为"
                    "「动作命中 <b>AND</b> 对象也在」的配对条件。")
_FREETEXT_ROLE = ("自由文本（<b>启发式，不保证</b>）——引擎剥掉否定前缀后用分词器取动词。"
                  "<b>已知失效</b>：分词器会把动作词标成名词（「下单」被标 <code>n</code>），"
                  "多动作约束只取到其中一个，真实违规句整批逃逸。")


def _esc(s) -> str:
    return html.escape("" if s is None else str(s))


def _itemize(items: Sequence[str], start: int = 1) -> str:
    if not items:
        return '<p class="sub">（空）</p>'
    lis = "".join(f"<li>{_esc(t)}</li>" for t in items)
    return f'<ol class="samples" start="{start}">{lis}</ol>'


def _count_txt(n: int, floor: int) -> str:
    """条数与硬门并排——缺口一眼可见，不靠读者心算。"""
    if n < floor:
        return f'{n} 条 <span class="tag heu">低于下限 {floor}</span>'
    return f'{n} 条 <span class="tag rec">≥{floor}</span>'


def fingerprint_state(live: str, recorded: Optional[str]) -> Dict[str, str]:
    """比对「现在算的指纹」与「标定产物里记的指纹」——**四值，不是布尔**。

    `未记录` / `不可比` / `不一致` / `一致`。只有最后一种才允许说"阈值与卡同源"。

    `algo_mismatch`（前缀＝算法版本不同）**必须与「不一致」分开**：前者不是卡的问题，
    它的文案必须明确否认"卡被改过"——错误归因会让人去重跑本来正确的标定，
    代价比沉默更大。

    **判定不在这里**：本函数只把判定结果渲染成人读文案，判定统一走
    `verify.fingerprint_line()`——`de verify` 与本页面共用同一份，否则两处实现对
    同一输入给出不同判定（历史上本页曾自成一套 `match`/`incomparable`/`malformed`
    词汇，与复算侧的 `ok`/`algo_mismatch` 分叉，故收敛之）。
    """
    fp = fingerprint_line(recorded, live)
    level = fp["level"]

    if level == "missing":
        return {"state": "missing", "cls": "b-bad",
                "text": "未记录——标定产物里没有卡指纹，<b>无从判断阈值是否对着这张卡标出</b>。"}

    if level == "algo_mismatch":
        # 记录侧没有版本前缀 ⇒ 旧格式/格式异常，算法不明、无从解析。
        # 与"算法版本不同"同属不可比，但文案侧重改为"不猜测"。
        if not fp["recorded_algo"]:
            return {"state": "algo_mismatch", "cls": "b-warn",
                    "text": f"格式异常（{_esc(recorded)}）——无法解析，<b>不猜测</b>。"}
        return {"state": "algo_mismatch", "cls": "b-warn",
                "text": (f"不可比：记录的是 <code>{_esc(recorded)}</code>，算法版本 "
                         f"<code>{_esc(fp['recorded_algo'])}</code> 与当前的 "
                         f"<code>{_esc(fp['live_algo'])}</code> 不同。"
                         f"<b>这<u>不是</u>卡被改过</b>，是比对规则变了——"
                         f"<b>不要</b>因此重跑标定。")}

    if level == "mismatch":
        return {"state": "mismatch", "cls": "b-bad",
                "text": (f"不一致：记录 <code>{_esc(recorded)}</code>，当前 "
                         f"<code>{_esc(live)}</code>。这张卡相对标定时<b>已被改动</b>，"
                         f"原阈值可能失效——<b>分数不可直接用于复核判断</b>。")}

    return {"state": "ok", "cls": "b-ok",
            "text": f"一致（<code>{_esc(live)}</code>）——阈值是对着当前这张卡标出的。"}


def _mode_banner(mode: Optional[str]) -> str:
    """标定性质三值。未知值一律落到「未标注」，**不折叠成任何一边**。"""
    table = {
        "formal": ("b-ok", "正式标定",
                   "阈值来自该场景的真实人工标注，**分数可用于复核判断**；"
                   "但仍受编码器与提示词版本约束。"),
        "synthetic": ("b-warn", "临时标定（合成样本）",
                      "阈值由合成样本自标定。**分数仅供观察，不可用于复核判断**。"),
    }
    cls, title, body = table.get(
        mode or "未标注",
        ("b-bad", "标定性质未标注",
         "标定产物**没有申报自己是正式还是合成**。本页不猜测："
         "**请勿据此判断分数可信度**。"))
    return (f'<div class="banner {cls}"><b>标定性质：{_esc(title)}</b><br>'
            f'{_esc(body).replace("**", "")}</div>')


def _norm_card(card) -> Dict:
    """接受 `ScenarioCard` 或等价字典。

    字典入口是给**把卡常量内嵌在脚本里**的调用方用的（检测页不建 JSON 卡）——
    与 `card_fingerprint` 收等价字典是同一条理由：让内嵌形态能复用同一份实现，
    而不是各自手抄一份渲染。
    """
    if hasattr(card, "model_dump"):
        d = card.model_dump()
    elif isinstance(card, dict):
        d = dict(card)
    else:
        raise TypeError(f"不认识的卡类型：{type(card).__name__}")
    rules = []
    for r in (d.get("rule_layer") or []):
        rules.append(r.model_dump() if hasattr(r, "model_dump") else dict(r))
    return {
        "card_id": d.get("card_id", "?"),
        "goal": d.get("goal", ""),
        "core_samples": list(d.get("core_samples") or []),
        "boundary_samples": list(d.get("boundary_samples") or []),
        "negative_samples": list(d.get("negative_samples") or []),
        "constraints": list(d.get("constraints") or []),
        "rule_layer": rules,
        "min_suspect_len": d.get("min_suspect_len", 12),
        "format_whitelist": d.get("format_whitelist"),
        "prompt_version": d.get("prompt_version", ""),
        "notes": d.get("notes", ""),
    }


def _constraints_html(constraints: List) -> str:
    if not constraints:
        return '<p class="sub">（未声明约束）</p>'
    structured = [c for c in constraints if isinstance(c, dict)]
    free = [c for c in constraints if not isinstance(c, dict)]
    out: List[str] = []
    if structured:
        out.append(f'<h3>结构化声明 <span class="tag rec">{len(structured)} 条</span></h3>'
                   f'<p class="sub">{_STRUCTURED_ROLE}</p>')
        rows = []
        for c in structured:
            acts = c.get("actions") or []
            objs = c.get("objects") or []
            acts_html = "、".join(f"<code>{_esc(a)}</code>" for a in acts) or \
                '<span class="tag heu">无 actions —— 已退回启发式</span>'
            objs_html = "、".join(f"<code>{_esc(o)}</code>" for o in objs) or \
                '<span class="sub">未声明（纯动作匹配，更保守）</span>'
            rows.append(f'<tr><td>{_esc(c.get("text", ""))}</td>'
                        f'<td>{acts_html}</td><td>{objs_html}</td>'
                        f'<td>{_esc(c.get("note", ""))}</td></tr>')
        out.append('<table><tr><th>条款</th><th>动作短语</th><th>对象</th>'
                   '<th>说明</th></tr>' + "".join(rows) + '</table>')
    if free:
        out.append(f'<h3>自由文本 <span class="tag heu">{len(free)} 条</span></h3>'
                   f'<p class="sub">{_FREETEXT_ROLE}</p>')
        out.append("<ul>" + "".join(f"<li>{_esc(t)}</li>" for t in free) + "</ul>")
    return "".join(out)


def _rule_layer_html(rules: List[Dict]) -> str:
    if not rules:
        return ('<p class="sub">（未声明规则层——本卡只有语义通路，'
                '所有判断都要经过分数）</p>')
    rows = "".join(
        f'<tr><td>{_esc(r.get("name", ""))}</td>'
        f'<td><code>{_esc(r.get("pattern", ""))}</code></td></tr>' for r in rules)
    return ('<p class="sub">规则层是<b>字的通路</b>：正则命中即判疑似，'
            '<b>完全不看分数</b>。它管的是「违反明令红线」的字面项——'
            '这类内容不一定语义低分，语义通路会放它过去，两条通路是「取或」的关系。</p>'
            '<table><tr><th>规则名</th><th>正则</th></tr>' + rows + '</table>')


def _card_section(card: Dict, *, calibration: Optional[Dict] = None,
                  live_fp: str = "") -> str:
    p: List[str] = []
    A = p.append

    A('<h1>' + _esc(card["card_id"]) + '</h1>')
    A(f'<p class="sub">方向定义：{_esc(card["goal"])}</p>')
    A('<p class="sub">'
      f'<span class="pill">指纹 {_esc(live_fp)}</span>'
      f'<span class="pill">prompt_version {_esc(card["prompt_version"])}</span>'
      f'<span class="pill">指纹算法 {_esc(FINGERPRINT_VERSION)}</span>'
      '</p>')

    # ---- 卡的位置：只回答「查什么」 ----
    A('<div class="banner b-key"><b>这张卡回答「查什么」，不回答「多低算偏」。</b><br>'
      '阈值（<code>high</code> / <code>low</code>）<b>不在卡里</b>——它属于标定产物，'
      '与该场景的真实语料和人工标注一起产生。所以：<b>换卡必须重标定</b>；'
      '卡可以被人抄走，阈值抄不走。</div>')

    if calibration:
        A(_mode_banner(calibration.get("mode")))
        kv = []
        for k in ("low", "high", "auc"):
            if calibration.get(k) is not None:
                kv.append(f'<span class="pill">{_esc(k)} {_esc(calibration.get(k))}</span>')
        if kv:
            A('<p class="sub">' + "".join(kv) + '</p>')
        st = fingerprint_state(live_fp, calibration.get("card_fingerprint"))
        status = calibration.get("card_fingerprint_status")
        status_note = ""
        if status == "recorded_at_calibration":
            status_note = '　来源：<b>标定当场写下</b>（与新阈值同源）'
        elif status == "backfilled":
            status_note = ('　来源：<b>事后补记</b>——只说明「自补记以来本卡未变」，'
                           '<b>不</b>说明标定当时用的是这张卡')
        elif status:
            status_note = f'　来源：{_esc(status)}'
        A(f'<div class="banner {st["cls"]}"><b>卡指纹比对：{st["text"]}</b>'
          f'{status_note}</div>')

    # ---- 两条通路 ----
    A('<h2>这张卡怎么被用：两条通路</h2>')
    A('<div class="paths">'
      '<div class="path sem"><span class="t">语义通路（软的）</span>'
      '样本族 → 建方向锥 → 输出算对齐分 → 与标定的 <code>low</code> 比较出判定。'
      '受 <code>min_suspect_len</code> 与 <code>format_whitelist</code> 保护。</div>'
      '<div class="path lit"><span class="t">字面通路（硬的）</span>'
      '<code>rule_layer</code> 正则命中 → <b>直接判疑似，不看分数</b>。'
      '两条通路是「取或」——所以会出现「分数看着没事，却被规则层揪出来」。</div>'
      '</div>')

    # ---- 样本三族 ----
    A('<h2>样本三族</h2>')
    A('<p class="sub">三族不是同类样本的三份，它们各喂给锥的一个参数：'
      '<b>core → 锥轴</b>（归一化嵌入的均值）、<b>boundary → softness</b>（夹角标准差）、'
      '<b>negative → 减项</b>。</p>')
    A('<div class="cols">'
      f'<div class="col"><div class="h">core_samples</div>'
      f'<div class="role">标准输出句。锥轴由它们的均值定，是这张卡的方向本体。</div>'
      f'<div class="cnt">{_count_txt(len(card["core_samples"]), MIN_CORE)}</div>'
      f'{_itemize(card["core_samples"])}</div>'
      f'<div class="col"><div class="h">boundary_samples</div>'
      f'<div class="role">合法但不典型。只影响锥的软度，不参与定轴。</div>'
      f'<div class="cnt">{len(card["boundary_samples"])} 条</div>'
      f'{_itemize(card["boundary_samples"])}</div>'
      f'<div class="col"><div class="h">negative_samples</div>'
      f'<div class="role">典型出戏句。给"偏"的一侧一个参照，不参与定轴。</div>'
      f'<div class="cnt">{_count_txt(len(card["negative_samples"]), MIN_NEGATIVE)}</div>'
      f'{_itemize(card["negative_samples"])}</div>'
      '</div>')
    A('<div class="banner b-warn"><b>卡能加载 ≠ 卡能用。</b>'
      '加载层只校验 <code>card_id</code> / <code>goal</code> / 正例非空；'
      f'样本够不够（core ≥ {MIN_CORE}，negative ≥ {MIN_NEGATIVE}）是'
      '<b>建锥那一刻</b>才拒绝的。上表的条数已与下限并排显示。</div>')

    # ---- 约束 ----
    A('<h2>约束条款</h2>')
    A(_constraints_html(card["constraints"]))

    # ---- 规则层 ----
    A('<h2>规则层</h2>')
    A(_rule_layer_html(card["rule_layer"]))

    # ---- 保护参数 ----
    A('<h2>保护参数</h2>')
    A('<table>'
      '<tr><th>字段</th><th>值</th><th>作用</th></tr>'
      f'<tr><td><code>min_suspect_len</code></td><td><code>{_esc(card["min_suspect_len"])}</code></td>'
      '<td>短于此长度的输出不算疑似。这是<b>场景策略</b>（短句不算跑偏），'
      '与引擎的 <code>min_judge_len</code>（短到无法测量方向，属认识论下限）'
      '<b>是两件事</b>。</td></tr>'
      f'<tr><td><code>format_whitelist</code></td>'
      f'<td><code>{_esc(card["format_whitelist"] or "（未声明）")}</code></td>'
      '<td>合法格式行的正则豁免——表头、清单标题这类结构行不该被计成跑偏。'
      '未声明与空串在判定上等价（指纹里折为同一值）。</td></tr>'
      '</table>')

    # ---- notes ----
    if card["notes"]:
        A('<h2>notes（给人读）</h2>')
        A(f'<div class="notes">{_esc(card["notes"])}</div>')
        A('<p class="sub">notes 是唯一的自由文本字段：<b>不参与判定，也不进指纹</b>'
          '——给卡加一行说明，不该被报成"卡被改过"。</p>')
    return "\n".join(p)


def render_cards_page(cards: Sequence, *, calibration: Optional[Dict] = None,
                      title: Optional[str] = None,
                      extra_notes: Optional[List[str]] = None) -> str:
    """渲染场景卡展示页。

    `cards`：`ScenarioCard` 或等价字典的序列。多于一张时自动分页签。
    `calibration`：标定产物（可选）。**多张卡时只允许配一张**——
                 一份标定产物不可能同时属于两张卡，混配是真的会算错的。
    """
    cards = list(cards)
    if not cards:
        raise ValueError("render_cards_page: 至少要有一张卡")
    if calibration is not None and len(cards) > 1:
        raise ValueError(
            "render_cards_page: 多张卡不能共用一份标定产物——"
            "标定是「某一张卡 × 某批数据」的产物，混配会得到看似可信的错误结论。"
            "（这是 fail-closed，不是限制。）")

    norm = [_norm_card(c) for c in cards]
    fps = [c.fingerprint() if hasattr(c, "fingerprint") else "" for c in cards]

    head: List[str] = []
    head.append('<div class="wrap">')
    head.append(f'<h1>{_esc(title or "场景卡展示页")}</h1>')
    head.append(f'<p class="sub">共 {len(norm)} 张卡　｜　生成于 '
                f'{_esc(datetime.now().strftime("%Y-%m-%d %H:%M"))}　｜　'
                f'渲染层：direction_drift.card_page</p>')
    head.append('<p class="sub">卡是数据，不是代码：换场景只换一张 JSON 卡，'
                '不改引擎。本页只把卡摊开给人看，<b>不产生任何判定</b>。</p>')

    body: List[str] = []
    css_extra = ""
    if len(norm) == 1:
        body.append(_card_section(norm[0], calibration=calibration, live_fp=fps[0]))
    else:
        # 纯 CSS 分页签（无 JS）。**input / label / pane 必须同层**：
        # `~` 兄弟选择器跨不过容器，包一层 div 就全灭（2026-10-01 实际翻过车）。
        controls: List[str] = []
        panes: List[str] = []
        for i, (c, fp) in enumerate(zip(norm, fps)):
            cid = f"t{i}"
            controls.append(f'<input class="cardtab" type="radio" name="cardtab" '
                            f'id="{cid}" aria-controls="pane-{cid}"'
                            + (" checked" if i == 0 else "") + '>')
            controls.append(f'<label for="{cid}">{_esc(c["card_id"])}</label>')
            panes.append(f'<div class="pane" id="pane-{cid}">'
                         + _card_section(c, live_fp=fp) + '</div>')
        css_extra = "".join(
            f'#{cid}:checked~#pane-{cid}{{display:block}}' for cid in
            (f"t{i}" for i in range(len(norm))))
        body.append("".join(controls))
        body.extend(panes)

    foot = ['<div class="foot"><p><b>边界声明</b></p><ul>']
    foot.append('<li><b>本页只描述卡，不评价卡。</b>它不说这张卡设计得好不好，'
                '只说它声明了什么、缺了什么。</li>')
    foot.append('<li><b>指纹是「现在」算的。</b>它证明此刻这张卡是什么，'
                '<b>不</b>证明阈值是对着它标的——后者要看标定产物里的 '
                '<code>card_fingerprint</code> 与它的状态。</li>')
    foot.append('<li><b>指纹不覆盖环境。</b>编码器、权重与相关库版本变了，'
                '分数同样会漂移，而卡指纹仍然一致。<b>指纹一致 ≠ 分数可复现。</b></li>')
    foot.append('<li>自由文本约束是<b>启发式</b>，已知有系统性失效；'
                '把所有约束都写成结构化形态，才谈得上"声明即检查"。</li>')
    for n in (extra_notes or []):
        foot.append(f'<li>{_esc(n)}</li>')
    foot.append('</ul></div>')
    foot.append('</div>')

    style = _STYLE + css_extra
    return ("<!DOCTYPE html>\n<html lang=\"zh-CN\">\n<head>\n"
            "<meta charset=\"utf-8\">\n"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
            f"<title>{_esc(title or '场景卡展示页')}</title>\n"
            f"<style>{style}</style>\n</head>\n<body>\n"
            + "\n".join(head) + "\n" + "\n".join(body) + "\n" + "\n".join(foot)
            + "\n</body>\n</html>\n")
