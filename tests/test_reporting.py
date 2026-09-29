# -*- coding: utf-8 -*-
"""reporting 模块测试：疑似主题聚类 + 通道级失败信号。
阈值依据（2026-09-28 寻路蝶 117 条疑似实测）：通道足迹是分布式的——机制讲解
横跨多个子话题，单词占比只到 8-12%，但 TOP5 簇联合覆盖率 >60%（正常批 18%），
故通道信号看联合覆盖率，阈值 0.3。"""
from direction_drift.reporting import add_stopwords, channel_failure_signal, cluster_suspects

CHANNEL = [f"根据知识库文档，蝶名公式第{i}条由状态词加意象词构成" for i in range(10)] + \
          [f"根据知识库文档，三停机制映射到记忆更新模块{i}" for i in range(8)] + \
          [f"知识库里蝶卡的格式字段定义如下{i}" for i in range(6)]
# 正常语料：刻意用互不重叠的日常句式（真实散步对话本来就是多样的；
# 若用同一模板复制，词频共现会聚出合法的簇——那是格式的特征，不是通道失败）
NORMAL = [
    "你朝光的方向慢慢走去，路边的影子在拉长。",
    "风从山谷那头来，带着一点凉意。",
    "你停下来，看了看脚下的青石板路。",
    "远处的灯火一盏一盏亮起来了。",
    "雨后的泥土味混着青苔的气息。",
    "你在岔路口站了一会儿，选了左边那条。",
    "桥下的水声比白天轻了许多。",
    "暮色里你的脚步慢了下来。",
    "墙上的影子随着你走动而倾斜。",
    "你抬头看了看云，又低下头继续走。",
    "这条街比记忆里窄了一些。",
    "落叶在你脚边打了个旋。",
    "你在长椅上坐了一会儿，什么也没想。",
    "夜风把灯笼吹得轻轻摇晃。",
    "你听见远处有河水流淌的声音。",
]


def test_cluster_finds_channel_terms():
    clusters = cluster_suspects(CHANNEL, min_cluster=3)
    assert clusters
    assert any("知识库" in c["term"] or "文档" in c["term"] for c in clusters)


def test_channel_signal_triggers_on_channel_corpus():
    add_stopwords({"蝶"})          # 项目专名入停用词
    sig = channel_failure_signal(CHANNEL)
    assert sig, "通道语料应触发通道级失败信号"
    assert sig[0]["coverage"] >= 0.3


def test_channel_signal_stays_silent_on_normal_corpus():
    add_stopwords({"蝶"})
    assert channel_failure_signal(NORMAL) == []
    assert cluster_suspects(NORMAL, min_cluster=3) == []
