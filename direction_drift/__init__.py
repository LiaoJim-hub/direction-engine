# -*- coding: utf-8 -*-
"""direction-drift：AI 方向漂移检测引擎（Direction Engine）

版本：2.3.7
蓝本：同目录《方向漂移检测工具：MVP代码框架 v2.2（最终版）.md》
2.3.7 更正：**无代码变更**。更正测试数口径——`docs/persistence.md` 注册进
      `tests/test_docs_consistency.py` 的 `DOCS` 后，该守卫按 `.md` 参数化、
      每份生成 2 条用例，全仓实为 **207**（README 与 CHANGELOG 原写 205）。
      另补 `.zenodo.json`（软件归档元数据）。出这个版本号是为了让归档快照自洽。
2.3.6 新增：会话存储接缝 `core.session_store`（`Session` 容器与三方法的
      `SessionStore` ＋进程内 `InMemorySessionStore`）。`api/server.py` 改为
      `create_app()` 工厂（**启动命令须加 `--factory`**），会话读写全部经 store
      且写回显式化——换成进程外实现（Redis/PG/磁盘）时检测/返回/标定三层不改。
      同时补上该文件此前为零的测试覆盖。迁移指引见 docs/persistence.md。
2.3.5 修复：返回协议的 `frozen` 改为报**实际做了什么**。此前锥为 `None` 时
      `freeze()` 未被调用，响应却硬编码 `"frozen": True`、文案宣称"冻结方向"
      —— 响应在陈述一件没发生的事。现按 `cone is not None` 如实回报，
      文案同步改口（"未冻结方向（无方向锥可冻结）"）。
2.3.4 修复：约束层补掉 2.3.3 的残留——调用方**显式声明** `hypothesis_violation`
      的约束，在 NLI 缺席时此前会被启发式的"没命中"掩盖成 `ok`，即"声明的
      检查手段不在场"被读成"已经查过了"。现在一律显形为 `unverifiable`。
2.3.3 修复：空输出/零向量不再产生 nan（曾被夹成满分）；判定链新增可判定性
      前置检查（no_output / too_short，不进入滑动窗口）；约束层支持结构化
      动作声明并显形 unverifiable（原先"放弃判定"被记成"通过"）。
2.3.2 新增：量程自洽检查 `core.alignment.check_range_consistency` ＋光锥锥轴
      数值守卫；修复 API 在缺 sentence-transformers 时的启动失败
      （fail-closed，而非降级为 DemoEncoder）。
2.3.1 新增：输出性质标注层 `provenance`（无数据不输出 → 有效推论，零依赖）
      ＋检测结果补 `pending_verification` 待核实标注（数据不足时）。
2.3.0 新增：多节点共存层 `coexistence`（自我降级协议 + 同步传播信封，零依赖）。
2.2.4 新增：状态序列化（detector/protocol/session_state），托管层持久化用。
"""
__version__ = "2.3.7"
