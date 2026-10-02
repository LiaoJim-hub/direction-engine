# -*- coding: utf-8 -*-
"""direction-drift：AI 方向漂移检测引擎（Direction Engine）

版本：2.5.2
蓝本：同目录《方向漂移检测工具：MVP代码框架 v2.2（最终版）.md》
2.5.1 新增：**规范摘要收口** `verify.texts_digest()` / `results_digest()`，以及
      `de check --artifact`（把一次判定写成**判定产物信封** JSON）。
      动因是复算链上缺了产出端：`de verify` 要有两份产物才能比，而此前
      **没有任何一条命令会产出契约定义的产物**（`--jsonl` 是报告输入，不是
      产物信封），PRD §8.4 的成功流当时无处落地。摘要算法只此一处——
      重算端另写一遍，就会把"算法分叉"误报成"输入不同、条目无法对齐"。
      另修 `de check` 的标定透传：此前只取 low/high，`weights` /
      `card_fingerprint` / `prompt_version` 全丢，产出的产物**天生不可复算**
      （契约里 `calibration.weights` 缺失即判不可比）。
2.5.0 新增：**复算内核 `verify.py` 与 `de verify`**——"可验证"不是说我们保证对，
      而是把判定过程打包成一份第三方能自己重算的产物。
      · `build_artifact()` 组装判定产物信封（最小可复算溯源集，**不判定**）；
      · `compare_artifacts()` 三层比对：**先判可比性、再判数值**——顺序不能反，
        否则"换了引擎版本"会被误报成"回归"。退出码 0=一致 / 1=真不一致 /
        2=**不可比** / 3=输入错误 / 4=无法复算；`2` 不是失败，是"条件不同、
        结论不可对话"，脚本必须能区分它。`tolerance` 是**显式参数**而非内置
        常量，且必须列出逐条残差——决定权在人（实测残差分布很宽）。
      · 诚实边界：`encoder.weights_hash` 为 null 时，报告只写"卡同源已证、
        分数可复现**未证**"，不得写成"复算成功"。
      另 **T1b 收敛卡指纹判定为引擎公共函数** `fingerprint_line()`（四值闭集
      ok / mismatch / missing / algo_mismatch）——此前 `card_page.fingerprint_state`
      自成一套 match / incomparable / malformed 词汇，与复算侧**对无冒号输入
      给出不同判定**，属第二处分叉。现页面只做渲染，判定唯一；`malformed`
      并入 `algo_mismatch`（契约 provenance 层闭集无第五值容身处）。
2.4.0 新增：**场景卡指纹** `scenario.card_fingerprint()` / `ScenarioCard.fingerprint()`
      ——标定产物此前绑定了编码器 / 权重 / 提示词版本，**唯独没绑卡本身**：
      卡一改，分数分布随之移动、原阈值失效，而标定文件不报警、仍自称正式标定。
      指纹覆盖一切参与判定的字段，**刻意不含 `prompt_version`**（那是标定侧的输入），
      并以 `FINGERPRINT_VERSION` 前缀区分**算法版本**——否则「换了算法」与
      「这些卡都被改过」在只比字符串时完全同形。
      另新增 `calibration.record`（标定产物的组装 `build_calibration_record` 与
      自校验 `validate_calibration_record`）——在此之前**没有任何脚本会写
      `*_calibration.json`**（元信息层全部手写），故 `card_fingerprint` 只能以
      `backfilled` 形态补记；有了产出脚本，它才能是 `recorded_at_calibration`。
      `ScenarioCard` / `RulePattern` 改 `extra="forbid"`（此前把 `rule_layer`
      写成 `rule_layers` 会被静默丢弃，而加载 / 建锥 / 指纹三项全绿）。
      改场景卡模块请只改 `scenario.py` 一处；卡指纹与标定产物的契约见
      `docs/scenario-card.md`。
2.4.0 新增：**`de` 命令行**（安装后为 `de`，未安装可 `python -m direction_drift`）
      ——三条命令对应建卡之后的全部动作：`de init`（四问建卡 → 输出场景卡 JSON）、
      `de check`（对一批输出跑检测 → 表格 + JSONL）、`de report`（把 JSONL 渲染成
      可交付 HTML）。三条自我约束与引擎同源（不是命令行礼貌）：**不生成正类样本**
      （用 LLM 挑"正确方向"再拿它判定，测出来的是"与模型自身偏好的偏离"）、
      **未标定不判定且显形**（无 `--calibration` 时自标定，分数仅供观察，这条原样
      写进 JSONL 与报告）、**跳过项要计数**（空输出 / 过短 / 零向量不产生分数）。
      用法见 `docs/cli.md`。
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
__version__ = "2.5.2"
