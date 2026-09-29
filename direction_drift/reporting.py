# -*- coding: utf-8 -*-
"""报告层工具（v2.2.3 新增）：疑似条目的主题聚类与"通道级失败"检测。

实践来源：寻路蝶 113 条疑似逐条复核后，发现全部指向同一个根因——
知识库问答通道整体绕过底线，而不是 113 条零散的句子级出戏。
这类"通道级失败"在逐句判定里不可见，只有把疑似条目按主题聚起来才能浮出。

本模块把那次人工洞察反哺进引擎：纯词频共现聚类，零重型依赖（jieba 已是包依赖），
不做语义模型调用——聚类是给报告层的提示，不是判定本身。

通道级判据说明（2026-09-28 实测定版）：通道足迹是分布式的——寻路蝶的机制讲解
横跨蝶名/三停/蝶卡/记忆等多个子话题，没有任何单词覆盖到 20% 以上；
但 TOP 几个主题簇的联合覆盖率显著高于正常批（实测 36% vs 18%）。
故通道信号不看单词占比，看 TOP5 簇覆盖了多少条疑似。
"""

from typing import Dict, List

# 聚类用停用词：虚词 + 通用抽象名词（对话/报告场景下的高频词），避免聚出无意义的大簇。
# 2026-09-28 实测依据：某场景 113 条疑似上，"用户/选择/观察/体验"这类通用词会压过
# 真正的通道标志词，故扩入本表。
# 边界（重要）：**场景专有词（智能体名、业务术语）不属于本表**——请由场景侧用
# add_stopwords() 注入（本文件 _agent_name_stopwords 就是给建卡方复用的入口）。
# 这条边界对应宪法"规则属场景，不进引擎"：引擎只持有语言层面通用的词。
# 反向入口同理：若本表某词在你的领域里是信号词（"方向""结构"在漂移类报告里就是），
# 用 remove_stopwords() 显式移出，不要改本表。
_STOPWORDS = set("""的 了 在 是 我 你 他 她 它 我们 你们 他们 这 那 这个 那个 一个 没有
就是 也不是 什么 怎么 可以 因为 所以 但是 如果 那么 这些 那些 自己 他们 会 能 要 想
可能 一样 一下 一些 这种 通过 进行 对于 作为 每个 使用 可以 包括 例如 以及 或者 比如
用户 选择 观察 体验 记录 系统 模式 内容 描述 信息 问题 回答 输出 生成 设计 结构
流程 过程 时间 场景 意象 状态 方向 确认 提供 对应 表示 意味 相关 例子 示例 维度
核心 以下 如下 以下内容 以下是一些 总结而言 整体而言 根据 基于 交互 体验流程""".split())


def _agent_name_stopwords(names):
    """把智能体自身名称加入停用词（名称出现在每条机制讲解里，不具区分度）。
    场景卡可在导入后调用：reporting.add_stopwords({'寻路蝶', '蝶鉴'})。"""
    _STOPWORDS.update(w for w in names if w)


def add_stopwords(words):
    _STOPWORDS.update(w for w in words if w)


def remove_stopwords(words):
    """从默认表里**移出**词（v2.3.3 新增，场景侧入口）。

    默认表是按"中文对话/报告场景"调的，其中若干抽象名词（如"方向""结构"）
    在特定领域里恰恰是信号词——本引擎自己的报告就属于这种领域。这类情况
    请用本函数显式移出，而不要改引擎默认表：把词表调准是场景的权力，
    也是"规则属场景，不进引擎"的正面做法。
    """
    _STOPWORDS.difference_update(w for w in words if w)


def _tokenize(text: str) -> List[str]:
    import jieba
    return [w for w in jieba.lcut(text)
            if len(w) >= 2 and w not in _STOPWORDS and not w.isascii()]


def _build_clusters(texts: List[str], min_cluster: int,
                    max_clusters: int = 8) -> List[Dict]:
    texts = [t for t in texts if t and t.strip()]
    if len(texts) < min_cluster:
        return []
    term_docs: Dict[str, List[int]] = {}
    for di, t in enumerate(texts):
        for w in set(_tokenize(t)):
            term_docs.setdefault(w, []).append(di)
    clusters = []
    total = len(texts)
    for w, docs in sorted(term_docs.items(), key=lambda kv: -len(kv[1])):
        if len(docs) < min_cluster:
            break
        clusters.append({
            "term": w,
            "count": len(docs),
            "ratio": round(len(docs) / total, 2),
            "sample": texts[docs[0]][:80],
            "docs": docs,
        })
        if len(clusters) >= max_clusters:
            break
    return clusters


def cluster_suspects(texts: List[str], min_cluster: int = 3,
                     max_clusters: int = 5) -> List[Dict]:
    """对疑似条目做主题聚类，返回最大的几簇。

    texts: 疑似条目全文（越长越准，建议传未截断原文）
    min_cluster: 簇最小条数，低于此不报
    返回: [{"term", "count", "ratio", "sample"}, ...] 按条数降序。
    说明：一条疑似可同时属于多个簇（按词共现），这是刻意的——
    通道级失败的信号正是"同一批出戏反复撞同几个词"。
    """
    return [{k: c[k] for k in ("term", "count", "ratio", "sample")}
            for c in _build_clusters(texts, min_cluster, max_clusters)]


def channel_failure_signal(texts: List[str], min_cluster: int = 5,
                           min_coverage: float = 0.3) -> List[Dict]:
    """通道级失败判定：TOP5 主题簇联合覆盖 ≥min_cluster 条起步、
    且覆盖疑似总量 ≥min_coverage 时触发。

    阈值依据（2026-09-28 寻路蝶 117 条疑似 vs 33 条正常句实测）：
    机制讲解通道 TOP5 联合覆盖 36%（单词仅占 8-12%，话题横跨蝶名/三停/蝶卡/记忆），
    真实散步批 TOP5 联合覆盖 18%。取 0.3 为界；且 min_cluster=5 保证
    小批（<5 条同主题疑似）永不触发。这是目前能拿到的最保守可用阈值，
    精度会随更多标注档案回填而收紧。
    命中即建议"整段回看该通道对应的对话流程，而不是逐句修正"。
    """
    clusters = _build_clusters(texts, min_cluster=min_cluster, max_clusters=5)
    if not clusters:
        return []
    union = set()
    for c in clusters:
        union.update(c["docs"])
    coverage = len(union) / len(texts)
    if coverage < min_coverage:
        return []
    return [{
        "terms": [c["term"] for c in clusters],
        "count": len(union),
        "coverage": round(coverage, 2),
        "sample": clusters[0]["sample"],
    }]
