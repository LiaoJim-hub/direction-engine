# Direction Engine（方向引擎）

**direction-drift：AI 智能体的方向漂移检测引擎。**
智能体在长对话里会悄悄跑偏——越权代办、泄露内部设定、说教贴标签、整段滑向题外话。
Direction Engine 用"方向锥 + 组合判据 + 场景卡"把每一次偏移显形出来。

> **命名**：项目名 **Direction Engine（方向引擎）**；发行包名 `direction-drift`、
> import 名 `direction_drift`。两者并存是刻意的——包名沿用最初名称，改名会
> 破坏已接入方的依赖与环境变量（如 `DIRECTION_DRIFT_API_KEY`）。

> Direction Engine is a direction-drift detection engine for AI agents.
> It only surfaces, never auto-corrects: **alerts belong to the engine, decisions belong to humans.**
>
> 本仓同时含两个零依赖模块：**多节点共存层**（`direction_drift/coexistence.py`，
> 查"节点有没有自封为根"）与**输出性质标注层**（`direction_drift/provenance.py`，
> 给每条输出标注"事实/推论/假设"）。检测链查"输出有没有跑偏"，三者共享同一套纪律。

## 五条可验证的性质（这是本项目的门面，不是口号）

1. **本地执行**：核心链路不联网、无遥测；嵌入模型（可选）可完全本地运行。
2. **场景卡是数据，不是代码**：接入一个新场景 = 写一张 JSON 卡（样本 + 约束 + 红线），
   不改引擎一行代码。阈值**不在卡里**——它属于标定产物。见 `examples/cards/`。
3. **只显形，不修正**：引擎绝不自动修改你的智能体、绝不自动触发"纠正动作"。
   告警归引擎，决定归人（`return_protocol` 的所有恢复动作都由人工/返回层触发）。
4. **可返回、无锁定**：全部检测轨迹与判定可导出（JSONL/HTML），随时离开。
5. **标定有保质期**：标定文件绑定提示词版本（`prompt_version`），智能体改版后
   引擎主动提醒复检——阈值不是永久的。

## 快速开始（30 秒，零下载零 GPU）

**要求 Python ≥ 3.10**（CI 跑 3.10 / 3.12）。依赖与其版本下限以
[`pyproject.toml`](pyproject.toml) 为准——核心只有四个：
numpy / pydantic / scikit-learn / jieba；`[sbert]`、`[api]`、`[llm]`、`[dev]` 为可选扩展。

```bash
pip install -e .            # 核心依赖仅 numpy/pydantic/scikit-learn/jieba
pytest                      # 207 项测试全离线通过（v2.3.7 实测）
python examples/demo.py     # 一条命令跑通 建锥→标定→检测→通道信号
python examples/demo.py roleplay_companion_v1   # 换一张卡再跑
```

注意：`examples/` 与 `tests/` **不随 wheel 分发**（`packages.find` 只含
`direction_drift*`），示例卡与 demo 数据集以仓库为准——所以请克隆仓库而不是
只 `pip install`。两个零依赖模块（`coexistence.py` / `provenance.py`）是**单文件、
纯标准库**，不想装重依赖的话可以直接复制这两个文件走（它们不 import 检测链）。

demo 输出示例（travel_concierge_v1 卡）：

```
   标签   对齐分  判定             疑似   规则层     输出
   在轨   0.401  normal           —     —         旺季出行建议提前两周订房，我帮你列个比价清单
   偏题   0.099  confirmed_drift  是    —         我直接帮你写一段Python代码实现快速排序吧
   违规   0.281  warning          是    越权代办   这家酒店有空房，我直接帮你下单了
```

两列不一致不是 bug：**"判定"列只看语义对齐**（由自标定出的 low/high 划定），
**"疑似"列是组合判据**（语义低分 + 长度保护 + 格式白名单，或规则层命中）。
"违规"行语义分并不低（0.281 > low=0.207），是规则层把它揪出来的——这就是
"规则属场景"的用处：词法抓不住的东西，场景卡的红线能抓住。

标定出的两个界也一并印出来（上例 `low=0.207 high=0.310`）。`high` 取**正常类的
低分位**（默认 5 分位，与 aperture 取核心样本 95 分位是同一套思路），而不是凭空的
`low + 0.2`——后者会把典型的在轨句也圈进 warning（本 demo 首行曾经正是如此）。

## 它怎么判定：组合判据，不是单一语义分

```
疑似 = (语义对齐分 < low 且 len(text) ≥ min_suspect_len 且 不命中格式白名单)
       或 (场景卡规则层命中)
```

单靠语义阈值会在合法格式行上大量误报（计分行、章节标题），也会漏掉语焉不详的
字面红线。组合判据在两个真实场景的人工复核数据上定型：蝶鉴 298 条（逐组确认）
＋ 寻路蝶 376 条（去重复核）＝ **674 条**，含未去重的原始导出合计 813 条；
综合两条路径后漏网 0、误标 0。**标注数据不随开源发布**（含真实产品对话），
此处为维护者自测口径，外部无法独立复算——请按"未验证的方法学"看待。
详见 `direction_drift/scenario.py` 的 `is_suspect`。

标定不用自动阈值拍板：`scan_operating_points` 输出每个候选 low 的
查全/误标/精确率一张表，**人工看表选点**——因为漏报（出戏到达用户）远贵于误报
（人多看一条），这个权衡不该藏在算法里。

## 三层使用路径

**路径 A：库调用**（单进程内嵌）

```python
from direction_drift.scenario import ScenarioCard
from direction_drift.utils.encoder import Encoder   # 生产：bge-small-zh-v1.5（pip install -e .[sbert]）

card = ScenarioCard.from_file("my_card.json")
cone = card.build_cone(Encoder().encode)
# 对齐计算 → 检测 → 组合判据
```

完整可运行版本：[`examples/demo.py`](examples/demo.py)（50 行）。
卡的字段与"建卡四问"的对应关系见 [`examples/README.md`](examples/README.md)。

**路径 B：场景卡 + 检测循环**（推荐先跑 `examples/demo.py`）

**路径 C：HTTP API**（给外部 Agent/应用调用）

```bash
pip install -e ".[api,sbert]"     # API 需要语义嵌入，两个 extra 都要装
DIRECTION_DRIFT_API_KEY=你的密钥 \
uvicorn "direction_drift.api.server:create_app" --factory --port 8000
# POST /v1/check {system_id, direction?, current_output}
# GET  /v1/state/{system_id}   POST /v1/resume/{system_id}  POST /v1/rebuild/{system_id}
```

（启动命令带 `--factory`：服务是 `create_app()` 造的，不是模块级对象——这样
import 不产生副作用，该层才测得到。）

路径 C 的限度（接入前必读）：①**只装 `[api]` 会启动失败**——缺少语义嵌入时
服务拒绝启动，而不是降级为 DemoEncoder（词级哈希算出的分数无效，
"跑得起来但结论错"远比"启动失败"危险）；②**未标定不判定**：本服务不提供
标定入口，`drift_level` 恒为 `uncalibrated`，只记录轨迹——要真实判定请走
路径 A/B 并注入该场景标定出的阈值；③**非线程安全**：同一 `system_id` 的并发
请求会互相覆盖判定推进（读-改-写不是原子的，这里声明而不掩盖），并发请每租户
独立进程或接入托管层；④**默认单进程内存态**：进程重启即丢。另：路径 C
**不支持场景卡**（`DirectionInput` 不含规则层 / 格式白名单 / `prompt_version`），
要用卡的完整能力请走路径 A/B。

**会话状态存在哪，是一个接口的事**（v2.3.6）。引擎自带
`core/session_store.py`：`SessionStore` 三个方法（`get` / `set` / `sweep`），
默认实现是进程内的 `InMemorySessionStore`。**要持久化就子类化它、传进
`create_app(store=…)`**——检测、返回、标定三层一行不改。引擎刻意**不内置
Redis / PG 实现**：不依赖外部服务，内置一份测不了的代码等于内置一份"看起来
能用"的东西。迁移怎么做、迁移时**会变**什么，见
[`docs/persistence.md`](docs/persistence.md)。

## 多节点共存层：`coexistence`（零依赖，可单独用）

检测链管**内容层**（输出有没有跑偏），`coexistence` 管**身份层**（节点有没有自封为根）。
它不 import 检测链任何部分，纯标准库，可独立复制使用：

```python
from direction_drift.coexistence import (DegradationChecker, DeclarationLedger,
                                        NodeDeclaration, default_statement,
                                        event_from_declaration)

decl = NodeDeclaration(node_id="AgentA",
                       statement=default_statement("AgentA"),   # 须含自指条目
                       return_path="docs/root_protocol.md")     # 须实有且不指向自身
r = DegradationChecker().check(decl, output=agent_output)
DeclarationLedger().record(r)
# r["action_hint"] == "continue" | "record_and_surface"
```

三条本地判据（全部本地执行，不联网、不调度、不比较节点）：

| 判据 | 检查什么 | 失败含义 |
|---|---|---|
| C1 自指条目 | 降级声明里有没有"本声明也是显化"一类 | 声明有把自己当成新正确相的风险 |
| C2 返回路径 | 可解析、非空、不指向节点自身 | 返回路径悬空 = 相干退化 |
| C3 根自称 | 输出命中根自称模式（正则初筛） | 相的偏出，显形 |

**失败只显形**：`record_and_surface`——记录 + 标注，不修正、不惩罚。修正/移除
（"修剪偏离节点"）需要跨节点通信与裁判位，会引入新的中心，本模块明确不实现。

跨节点只提供**事件信封**（六字段 `event / source / target / path /
is_manifestation / verification_path`），不提供传输、路由与仲裁——信封只携带事实，
不携带要求（不要求反应、不要求同步、不要求采用）。

已知限度：C3 目前是正则初筛，语义级冒充辨认属 V2（可接 NLI）；申报可以是假的，
本模块对有意冒充只能显形 + 归档留痕，不能辨认动机。自测：`python -m direction_drift.coexistence`

## 输出性质标注层：`provenance`（零依赖，可单独用）

"未标定不判定"解决的是**不能编造**；但"没有完整数据就什么都不输出"会让 Agent
在现实中无法运行（用户问订单，答"我没有数据，不能回答"）。`provenance` 给出另一种
做法：**推论可以输出，但必须标明性质**。

```python
from direction_drift.provenance import infer_from_structure, no_data, validate

p = infer_from_structure(
    output="用户可能希望预订酒店",
    chain="用户问'有空房吗' → 结构上属预订意图",
    verification_path="询问用户是否希望预订",
    return_path="原始对话记录")
r = validate(p)          # → {"valid": True, ...}；越界只表面化，不改写输出
no_data("order_db")      # L5：输出"无数据"，不编造
```

数据分级 L1–L5，与输出性质一一对应（**这条映射是硬的**）：

| 级别 | 定义 | 输出方式 | 允许性质 |
|---|---|---|---|
| L1 | 有明确数据来源 | 直接输出，标注来源 | `fact` |
| L2 | 从已知结构推演 | 标注"结构推论"，附推演链 | `inference` |
| L3 | 给定假设推演 | 标注"条件推论"，附假设 | `inference` |
| L4 | 提出可检验假设 | 标注"开放假设"，附检验方式 | `hypothesis` |
| L5 | 没有数据来源 | 输出"无数据"，不编造 | `none` |

三条红线由 `validate()` 显形（**只显形，不改写输出、不代补路径**）：
① 推论不能替代事实（推论/假设必须有检验程序与推演链）；
② 推论不能替代决定（本模块没有任何执行出口）；
③ 推论不能替代返回（`return_path` 缺失即越界）。
低置信须标 `pending_verification`（即"待核实"）。

另：引擎判定结果新增 `pending_verification` 字段（数据不足时为 True）——这是标注，
不是动作，不改变 `drift_level`、不触发冻结。
**划界**：检测结果的 `confidence` 指**判定强度**；
`Provenance.confidence` 指**证据强度**。两者含义不同，不得互相赋值。

## 给新场景建卡：建卡四问

引擎只提供"怎么查"，"查什么"永远由场景卡回答。除样本外同步骤四问：

> 模板：复制 [`examples/cards/travel_concierge_v1.json`](examples/cards/travel_concierge_v1.json)
> 改写即可；三步操作见 [`examples/README.md`](examples/README.md)，
> 分步要求与质量红线见 [CONTRIBUTING.md](CONTRIBUTING.md)。

| 组成 | 内容 | 来源 |
|---|---|---|
| 语义样本（正例/边界/反例） | ①业务方向是什么（→正例）②典型跑偏长什么样（→反例）③哪些话属合法边界（→边界样本） | 从提示词与真实对话回填 |
| 约束条款 | **结构化**：`{"text": …, "actions": ["帮你下单", …], "objects": [...]}`；自由文本形态只是兜底 | 场景红线 |
| 规则层声明 | ④哪些是不可逾越的字面红线（如"系统提示词/内部机制"泄露词表） | 逐场景定义，**不进引擎** |
| 阈值（**不在卡里**） | high / low 与 CUSUM 参数 | 该场景自己的 200+ 段真实标注，落在标定文件 |

约束条款为什么必须结构化：自由文本形态靠 jieba 提动词，而分词器会把动作词
标成名词——"不直接代用户下单、支付或预订"里 **`下单` 被标 `n`**，动作词表只剩
`支付/预订`，于是"我直接帮你下单了"这句教科书级违规**完全逃逸**。动作词表是
场景知识，由建卡方给出，引擎只做精确子串匹配。同理，"**提及即违规**"类的约束
（泄露设定、内部机制）不属于 `constraints`，它们是 `rule_layer` 的主场。

## 使用纪律（不遵守则结果无效）

1. **未标定不判定**：`calibrated=False` 只记录轨迹，这是设计不是缺陷。
2. **合成样本 AUC 只作健康检查（>0.7）**，不作性能估计。公开演示数据集
   （`examples/dataset/`）只用于验证标定方法学可复现，**不构成性能宣称**。
3. **正式标定**：200+ 段真实输出 + 人工标注，`examples/validate.py` 验收
   （Kappa≥0.6 前提下 precision、recall >0.75）。
4. **新信号只提级**：`cusum_alarm` 的唯一效果是 normal→warning（且告警即复位）；
   `confidence` 只是呈现标注。看到信号 ≠ 漂移确认，决定权在人。
5. **CUSUM 参数**默认值未经真实数据调优，正式使用在标定阶段一并校准。
6. **权重与阈值必须同源**：权重同样是标定产物——换权重会整体移动分数分布、使原
   阈值失效；而它目前不由 `calibrate_thresholds` 覆盖，只能人工保证同源。仓内默认
   权重 0.4/0.4/0.2 与默认阈值 0.6/0.4 **不自洽**（约束项只罚不奖，量程上界只有
   0.4，`confirmed_drift` 永不触发），接入时请用 `core.alignment.check_range_consistency()`
   自检，别把默认值当可用配置。
7. **量程只作相对比较**：`overall_alignment` 是三项异量纲分数的线性合成分，
   只宜用于排序与阈值比较，**不作绝对解读**（"0.6 分 = 60% 像"是错误读法）。
8. **输入不足不判定**（`no_output` / `too_short`）：空输出、纯空白、编码器返回零向量 →
   `no_output`；短于 `min_judge_len`（默认 4 字）→ `too_short`。两者**不进入滑动窗口、
   不改状态**。理由：空回复（Agent 最常见的真实故障）若走分数通道，会被 nan 夹成
   **满分**；而"好的/收到/嗯嗯"这类最常见合法短回复，会连着把窗口填满并推到
   `confirmed_drift → return → 冻结光锥`。看到这两个级别请去修输入，别去调阈值。
   （卡的 `min_suspect_len` 是场景策略，引擎的 `min_judge_len` 是认识论下限，两回事。）

## 已知边界

- MVP 不含云函数部署与 NLI 辨认（V2 项）；语义级冒充辨认属 V2。
- 光锥重建（rebuild）只由返回层/人工触发，不存在对单条输出的自动更新。
- 检测的是"输出对场景卡的符合性"，不是哲学意义上的长期方向漂移；
  批级走势是雏形，时间序列方向分析在路线图上。
- HTTP 服务为单进程内存态、非线程安全，且不提供标定入口（限度详见路径 C）。
  **会话存储是可替换的**：子类化 `SessionStore` 并传给 `create_app()` 即可换成
  进程外实现（Redis / PG / 磁盘），检测逻辑不动——但换存储会改变
  `evicted_sessions` 的语义、并让"读-改-写非原子"变成真问题，清单见
  [`docs/persistence.md`](docs/persistence.md) 第五节。
- 报告层主题聚类目前**只处理中文词**（`reporting._tokenize` 过滤 ASCII 词），
  英文术语与代码标识不参与聚类。
- **约束层的词法能力有上限**：结构化动作声明只做精确子串匹配，同义改写
  （"我替你把它办了"）抓不住；自由文本形态更弱（分词误标会让动作词直接消失）。
  `ok` 只表示"按当前手段未发现违规"，不表示"保证合规"；要真判需启用 NLI（V2）。
- **示例卡刻意贴着样本下限**（core=20 是硬下限）：作为最小可运行模板演示，
  生产卡建议 30+ 正例 / 15+ 边界 / 15+ 反例。
- 治理现状诚实声明：当前为**单维护者项目 + 项目宪法约束**（见 CONSTITUTION.md），
  社区化是路线图而非现状。

## 版本沿革

当前版本 **v2.3.7**（2026-09-30）。完整沿革、每条变更的理由、以及宪法修订记录，
统一维护在 [`CHANGELOG.md`](CHANGELOG.md)——README 不再重复一份，避免两处失真。
（版本指针由 `tests/test_docs_consistency.py` 断言，不会再悄悄落后。）

## 项目定位与哲学

本项目源自一套关于"方向性先于内容"的哲学工作。为了让工程性质可验证，
README 只陈述上面五条性质；完整的哲学定位、术语与工程对应物的转译表在
[`docs/philosophy.md`](docs/philosophy.md)，行为红线在 [`CONSTITUTION.md`](CONSTITUTION.md)。

## 贡献

见 [`CONTRIBUTING.md`](CONTRIBUTING.md)（开发环境、建卡要求、测试与提交规范）
与 [`CODE_OF_CONDUCT.md`](CODE_OF_CONDUCT.md)（参与准则）。
最高价值的贡献是**新场景卡**与**真实标注下的标定报告**，其次是代码。

## 作者与联系

**单维护者项目**（治理状况见 [`CONSTITUTION.md`](CONSTITUTION.md) 第四节的诚实性声明）。

- 作者：NanshanJim（中文署名「南山Jim」）
- 邮箱：NanshanJim@Outlook.com
- GitHub：[@LiaoJim-hub](https://github.com/LiaoJim-hub)
- 仓库：[LiaoJim-hub/direction-engine](https://github.com/LiaoJim-hub/direction-engine)

引用本项目的推荐格式见 [`CITATION.cff`](CITATION.cff)；安全漏洞请走
[`SECURITY.md`](SECURITY.md) 的私密渠道，**不要开公开 Issue**。

对外一律使用笔名 **NanshanJim**——`authors`、LICENSE、发布元数据三处一致，不提供本名。
学术引用与代码之间的互证靠**同一笔名 + 论文引用本仓库 + 个人主页**维系，不靠同名：
需要在论文中引用时，请以本仓库与 `CITATION.cff` 为准。

## License

[Apache-2.0](LICENSE)
