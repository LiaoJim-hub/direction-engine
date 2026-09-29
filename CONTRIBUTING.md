# 贡献指南（CONTRIBUTING）

欢迎。最高价值的贡献排序：**新场景卡 > 真实标注下的标定报告 > 代码**。
参与本项目即视为接受 [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md)。

## 开发环境

```bash
git clone https://github.com/LiaoJim-hub/direction-engine && cd direction-engine
pip install -e .[dev]      # 核心依赖 + pytest，无 torch
pytest                     # 全部离线，秒级
# 语义嵌入开发（可选）：pip install -e .[sbert] 后设置 HF_HOME 指到大容量磁盘
```

CI 在 Python 3.10 / 3.12 上跑全量测试（无 torch），提交前请本地先过。

## 贡献场景卡（最欢迎）

1. 复制 `examples/cards/travel_concierge_v1.json` 为模板；
2. 按"建卡四问"填四个象限：
   - ①业务方向是什么 → `core_samples`（≥20 条是**硬下限**，先人工抽检通过率 >80%；
     建议 30+，示例卡贴下限只是为了演示最小可运行配置）
   - ②典型跑偏长什么样 → `negative_samples`（≥10 条；**留空会跳过校验但卡等于废的**，
     标定做不下去）
   - ③哪些话属合法边界 → `boundary_samples`（写清"合法但不典型"的话）
   - ④哪些是不可逾越的字面红线 → `rule_layer`（正则；**规则属场景，不进引擎**）
3. 具体禁止动作写 `constraints`，且**用结构化形态**
   （`{"text": …, "actions": [...], "objects": [...]}`）——自由文本形态靠分词提动词，
   动作词一旦被标成名词就整批漏判（详见 `examples/README.md`）；
   "提及即违规"类（泄露设定/内部机制）归 `rule_layer`，不归 `constraints`；
4. 本地验证：`python examples/demo.py 你的卡名`，在轨句不应标疑似、出戏句应被逮住；
5. 提交时注明样本来源（合成/脱敏真实对话）。

注意：**"卡能加载"不等于"卡能用"**——`from_file()` 只校验 `card_id`/`goal`/正例非空，
样本条数的硬校验发生在 `build_cone()`（`MIN_CORE_SAMPLES=20`）。

**卡质量红线**：`min_suspect_len` 与 `format_whitelist` 必须按真实输出格式设置
（合法格式行豁免是组合判据的一部分）；rule_layer 正则不得过宽（先在
regex101 类工具上跑一遍负例反例）。

## 贡献标定报告

用你自己的 200+ 段真实输出 + 人工标注跑标定，欢迎提交"标定报告 Issue"：
AUC、操作点扫描表（`scan_operating_points`）、选点理由、踩到的坑。
这是社区里最稀缺的数据，哪怕只有结论摘要也有价值。

**报告必须同时记录三样东西**：编码器型号、对齐权重、`prompt_version`。
三者任一改变都会移动分数分布、使原阈值失效——只报阈值而不报这三样，
别人无从判断你的操作点能否套用。请用
`core.alignment.check_range_consistency(weights, high, low)` 自检量程是否与阈值
同源，并把结果一并贴上（该函数只显形、不改判定）。

## 贡献代码

- 提交前：`pytest` 全过；新功能必须带测试；类型标注尽量补全；
- 新信号/自动化能力**必须先过 [CONSTITUTION.md](CONSTITUTION.md) 第二节
  "哲学门控"清单**，并在 PR 描述里逐条勾选；
- 触碰"只显形不修正"边界的改动（如任何自动修正路径）会被直接驳回。

### 代码风格

本项目**不引入 formatter / linter 工具链**（保持零工具依赖），风格按现有代码走：

- PEP 8 基线，4 空格缩进；行长不硬性限制，优先 ≤ 100；
- 注释与文档字符串用中文，写"为什么"，不复述代码；
- 公共函数尽量带类型标注，私有辅助函数不强制；
- 新增**零依赖模块**（如 `coexistence` / `provenance`）不得 import 任何第三方库，
  也不得 import 检测链——这条有测试守卫（`tests/test_coexistence.py` 的架构守卫），
  加新模块时照抄该守卫模式。

### 测试要求

不设覆盖率数字门槛（数字会变成被优化的目标而不是被理解的对象），
改为三条可执行要求：

1. **新行为必须有断言**：新增判定、新增字段、新增分支，都要有能真正失败的测试；
2. **测试必须离线、秒级**：CI 不装 torch，不得引入联网或大模型下载；
   需要嵌入的场景用 `DemoEncoder` 或伪编码器；
3. **边界要有回归防线**：凡触碰"只显形不修正""零依赖""未标定不判定"这类边界，
   必须补一条守卫测试（例：断言失败结果里不含任何修正类字段）。

### 提交与分支

- **提交信息**：一行主题 + 必要时正文说明"为什么"。类型前缀
  （`feat:` / `fix:` / `docs:` / `test:`）可选但推荐，便于日后过滤。
- **分支策略**：主分支 `main`，维护者直接提交；外部贡献走 fork + PR。
  改动较大（新模块、新信号）请先开 Issue 说明动机，避免白写。
- **文档不各自标注引擎版本**：文档与代码同仓演进，git 历史本身就是版本对应关系，
  版本沿革统一在 [`CHANGELOG.md`](CHANGELOG.md)。只有硬依赖处才标版本——
  标定文件必须带 `prompt_version`，因为那里版本不一致有实际后果。

## Issue 模板

仓库里已有三张表单，**按类型选**（`.github/ISSUE_TEMPLATE/`）：

- **Bug 报告**：最小复现代码 / 卡 JSON / 期望与实际 / Python 版本；
  先确认不是 README「已知边界」里已披露的刻意上限。
- **新场景卡**：直接附 JSON + `examples/demo.py` 输出；说清样本来源与条数。
- **标定报告**：数据规模、标注口径、扫描表、同源三样（编码器 / 权重 / `prompt_version`）、结论。
- **安全漏洞**：走 [`SECURITY.md`](SECURITY.md) 的私密渠道，**不要开公开 Issue**。

## 宪法修订

见 CONSTITUTION.md 第五节：独立提交、说明被触动的事实，不接受纯措辞修饰。
