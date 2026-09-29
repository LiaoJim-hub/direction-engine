# 更新日志（CHANGELOG）

本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/)，变更记录按
[Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 组织。

**本文件是版本沿革的唯一权威位置**，README 只保留最新版本指针。
场景卡与标定数据不在本仓，它们的版本另由标定文件的 `prompt_version` 绑定
（改版后必须复检——见 CONSTITUTION.md 第三节"标定诚实性"）。

## [2.3.6] - 2026-09-29

**会话存储有了接缝：把"状态存在哪个边界上"收口成一个接口。**

起因是一个具体问题——"现在跑单进程演示，以后要持久化/多实例，要改多少东西？"
此前答案是"要动核心逻辑"：`api/server.py` 里一个模块级 `sessions: Dict`，业务
代码直接摸它。而这个文件**同时还是零测试覆盖的**（模块级 `app` 让 import 即构造
语义编码器，pytest 收不进来）。两件事一起修。

### 新增

- **`core/session_store.py`**：`Session`（锥 + 检测器 + 返回协议的可变容器）、
  `SessionStore`（抽象接口）、`InMemorySessionStore`（进程内实现，行为与旧
  `sessions` 字典逐条对齐：滑动过期、容量淘汰、淘汰 id 回报）。
  **接口只有三个方法**——`get` / `set` / `sweep`，刻意的：
  `snapshot()` / `restore()` 属序列化层（`session_state`），提升为契约会让"换
  存储"顺带改动状态布局，出现第二份状态知识；`delete()` 无端点需要，不预支。
- **`docs/persistence.md`**：迁移指引——边界对照表、三步迁移、可复制的
  Redis 骨架（**明确标注未随仓测试**）、迁移时**会变**的东西（诚实清单）、
  以及怎么用前哨测试确认没走样。
- `DIRECTION_DRIFT_SESSION_MAX_IDLE`（默认 3600，与旧硬编码值一致）。

### 变更（**破坏性**：启动命令）

- **`api/server.py` 改为工厂**：`create_app(encoder=None, store=None, api_key=None,
  enable_nli=None)`，**不再有模块级 `app`**。启动命令相应改为
  `uvicorn "direction_drift.api.server:create_app" --factory`。
  原因是 import 副作用：模块级 `app` 使导入即构造编码器，这让整个文件在
  测试套件里不可达。改工厂后 import 干净，而失败点（缺密钥 / 缺语义嵌入）
  仍然落在**启动时**，仍然 fail-closed。构造出的 `app.state` 上挂了
  `encoder` / `store` / `calculator` / `nli_enabled` 供自省，不改判定行为。
- **会话读写全部经 `SessionStore`，写回显式化**：`check` / `resume` / `rebuild`
  都无条件 `store.set()`；只读端点不写。这一条看着琐碎，但它是"换实现结论不变"
  的全部依据——进程内实现返回活对象，**漏写 `set()` 也照常跑通**，只有换成
  detach 的实现才暴露。
- `[dev]` extra 补 `fastapi` / `uvicorn` / `httpx`：不然 HTTP 层在 CI 里只能
  `importorskip` 静默跳过——那正是它零覆盖的老问题换个姿势。

### 更正

- README 路径 C 此前写着"单进程内存态、会话不持久化"，这是**声明过的**限度，
  不是隐藏缺陷；但从 v2.3.6 起它不再是"没法改"的限度——不传 `store` 仍是进程内，
  传了就是进程外，业务层一个字不动。
- `core/session_state.py` 自 v2.2.4 起就在仓里，却**一个生产调用方都没有**
  （只有测试）。现在它第一次被真正接上：`InMemorySessionStore` 本身不调它
  （进程内不需要序列化），但它就是进程外实现要调的那一层，`docs/persistence.md`
  的骨架里写明了。

### 刻意不做

- **不提供 Redis/PG 实现**。引擎不依赖任何外部服务，写了也没法在 CI 里测
  ——那会变成一份"看起来能用的未测代码"。托管方子类化三个方法即可。
- **不做租户隔离与认证分层**。多租户怎么切取决于接入方形态（SaaS / 私有部署 /
  嵌进 Agent 框架各不一样），现在做就是猜（CONSTITUTION 一.2）。
- **不解决"读-改-写非原子"**。它需要存储层 CAS 或请求级排队，接口承担不了。
  改成**显式声明**（模块与接口 docstring 都写了），而不是留着隐式活对象让人看不出来。

### 测试

`160 → 205 项`（新增 45）：

- `tests/test_session_store.py`（21 项）：接口最小性守卫（断言抽象方法就是那三个，
  防有人"顺手"把 `snapshot/restore` 塞回来）、存储层无第三方 import 的架构守卫、
  用最小子类证明"外部实现只需三个方法"、会话容器类型校验、滑动过期与容量淘汰、
  **空闲回收不回报 id 而容量淘汰必须回报**（这条边界写死）。
- `tests/test_api_server.py`（24 项）：端点面（401 / 422 / 400 / 404 / 409、空输出
  → `no_output` 且 `overall_alignment is None`、短句 → `too_short`）、构造守卫
  （缺密钥拒绝启动、缺语义嵌入拒绝启动且给安装指令、默认 store 是进程内）、
  写回纪律（三个迁移端点都 `set`、只读端点不 `set`）、
  **`JsonRoundTripStore` 对照测试**（同一串输入，进程内实现与"每次读写真序列化"
  的实现判定必须逐条一致——这是"演示 → 持久"的前哨测试）。

## [2.3.5] - 2026-09-29

**返回协议不再陈述没发生的事**（用托管层演示模式做端到端冒烟时暴露出来的）。

### 修复

- **`frozen` 报的是"这条分支该做什么"，不是"实际做了什么"**。`ReturnProtocol.act()`
  在 `confirmed_drift` 分支里对 `cone` 做了空值保护才调用 `cone.freeze()`，却把
  `"frozen": True` 硬编码在返回字典里，`message` 也无条件写着"冻结方向，等待外部观察"。
  于是 `cone=None` 的调用方（无状态服务、只想判趋势不想持有锥的接入方）拿到的是
  **一句没发生的事的陈述**：什么都没冻结，响应却说冻结了。
  同一条字典里的 `"direction": cone.goal if cone else ""` 说明 None 分支本来就被
  考虑过——这是漏掉的一处，不是设计。
  现在 `frozen = cone is not None`，`message` 相应改口为
  "漂移确认，未冻结方向（无方向锥可冻结），等待外部观察"。
- **`requires_human` 与 `action="return"` 不受影响**：有锥没锥都不存在自动恢复路径
  ——这一条属于返回协议的语义，不该被 `frozen` 的取值带偏，测试专门守着它。

### 影响面

**给锥的调用方（引擎自带 `api/server.py` 就是）行为完全不变**；受影响的只有
传 `None` 的调用方——它们此前拿到的是一个假 `True`。

### 测试

新增 `tests/test_return_protocol.py`（9 项）：单元层断言"没锥 → `frozen=False` 且
文案不说冻结"与"有锥 → `frozen=True` **且锥上确实落了标记**"（后者防的是反向作弊：
把 `frozen` 改成常量 True/False 都能骗过单向断言）；集成层用真标定的 detector 跑出
`confirmed_drift`，确认两条路径口径一致；另有一组参数化断言**其余级别一律不出现
`frozen` 字样**（只有 `confirmed_drift` 才谈冻结）。

## [2.3.4] - 2026-09-29

**约束层补掉 2.3.3 的残留**（在同一族缺陷里再往深处走了一层）。

### 修复

- **显式声明 `hypothesis_violation` 的约束被静默记成"通过"**。2.3.3 修的是
  "提不出动词的自由文本约束"——那类会落到 `evaluated=False`，从而显形为
  `unverifiable`。但**调用方显式声明**了 `hypothesis_violation` 的约束被漏掉了：
  只要启发式恰好从约束文本里提得出动词、又恰好没命中输出，就会走
  `if evaluated` 分支报 `ok`。而声明 `hypothesis_violation` 本身就是
  **"词法匹配不够用"的声明**（见 v2.1 审查 P1-1：正向约束靠它才可判）——
  声明的检查手段不在场，却被读成"已经查过了"。现在"显式声明 + 无 NLI"
  一律 `unverifiable`（记录、不扣分）。
- 反向守卫同时入库：**没有**声明假设的纯启发式约束，查过了没命中仍是 `ok`。
  不能为修这一条就把所有词法结论都打成不可判——"什么都不可判"与
  "什么都判通过"一样无用。

### 测试

`tests/test_constraint_layer.py` 新增 3 项（含一条反向守卫）。
全仓 **148 → 151 项，0 失败**。

### 没有动的东西

权重、阈值与判定级别集合一律未动——本次纯属"把静默显形"，
既有的两套已转正标定继续有效。

## [2.3.3] - 2026-09-29

**三个事实级缺陷 + 一批文档口径修正**（外部源码审查后的辩证采纳）。

本版修的三条都是"文档承诺了、代码没做到"，且共同形态是**静默**——
错的不是数值精度，而是"最坏的输入被记成最好的结果"。

### 修复

- **P0-1 空输出拿满分（nan 静默传染）**：空串/纯空白经真实编码器返回零向量，
  `out / np.linalg.norm(out)` 除零得 nan，而 `max(0.0, min(1.0, nan))` 在 Python 里
  返回 **1.0**（nan 比较恒 False）——Agent 最常见的真实故障"空回复/静默失败"
  被记成**最高对齐分**，任何绕开 `judge()` 直接读 `overall_alignment` 的看板被骗。
  修法：`AlignmentCalculator.compute` 对空输入/零向量**不产生分数**，返回
  `{"overall_alignment": None, "invalid": True, "reason": "empty_output"|"zero_vector_embedding"}`；
  `DirectionCone.contains` 与 `from_samples` 的边界样本同样加零范数守卫
  （统一常量 `ZERO_NORM_EPS`）；反面样本循环跳过退化的零向量，不再产出 nan。
- **P0-2 短合法回复被判 `confirmed_drift`**：长度保护原先只装在卡路径
  （`ScenarioCard.is_suspect`），而 `drift_level` 由 `DriftDetector.judge()` 单独算出
  ——**两条路径标准不一致**。实测"好的/收到/嗯嗯"（2 字）在窗口填满后可直接触发
  `confirmed_drift → return → 冻结光锥`，而客服/陪伴场景里这类短回复是最常见的
  合法输出。修法：判定链增加**可判定性前置检查**——`invalid` → `no_output`，
  长度 < `min_judge_len`（默认 4，可用构造参数覆盖）→ `too_short`；
  两者**不进入滑动窗口、不改任何状态、不触发任何动作**。短句分数低不等于方向漂，
  把它喂进窗口会污染均值、斜率与 CUSUM，把一个纯格式问题升级成"确认漂移"。
  卡策略（`min_suspect_len`）与认识论下限（`min_judge_len`）是两件事，已在
  文档中划清。
- **P0-3 约束层在真实违规句上系统性漏判**：`overall_alignment` 有 0.4 的权重压在
  约束层上，而约束层多动作约束下近乎失效——`"不直接代用户下单、支付或预订"` 经
  jieba 分词后 **`下单` 被标为 `n`**，动作词表只剩 `支付/预订`，于是
  `"我直接帮你下单了"` 这类教科书级违规**整批逃逸**（外部审查给出 5 句反例，
  5/5 全部漏判，已全部入库为回归测试）。三处修复：①新增**结构化动作声明**
  `{"text":…, "actions":[…], "objects":[…]}`，引擎只做精确子串匹配——动作词表是
  场景知识，属卡不属引擎（宪法"规则属场景"的完整含义）；②自由文本形态从"只取最长
  动词"改为**取全部动词**；③否定窗口从"动词前 4 字符"改为"**同一小句内、最多 6 字符**"
  （语言上否定只在所在小句内管到动词；原实现短了会误伤"我不会帮你下单"，
  单纯加长又会用跨小句的"没"豁免真实违规）。
  另修一处更隐蔽的静默：`_check_one` 原先在"生成了假设但 NLI 缺席"时返回
  status `ok`——**放弃判定被记成判定通过**；MVP（无 NLI）下所有此类约束都走这条
  路。现在"引擎没有可用手段"一律显形为 `unverifiable`（记录、不扣分），
  并明确 `ok` 只表示"按当前手段未发现违规"，不表示"保证合规"。
- `calibrate_thresholds` 的 `suggested_high` 由武断的 `low + 0.2` 改为
  **正常类低分位**（默认 5 分位，与 aperture 取核心样本 95 分位同一思路）。
  原规则会把典型在轨输出圈进 warning——demo 首行 0.401 < high 0.407 即此。
- API 层：`verify_api_key` 改用 `secrets.compare_digest`（常量时间，原先 `!=`
  会泄露密钥前缀的匹配长度）；新增会话数上限 `SESSION_MAX_COUNT`（默认 500，
  按最久未用淘汰，被淘汰的 id 在 `evicted_sessions` 里回报，不静默丢会话）。
- `utils.json_safe.jsonable` 的 `except Exception` 收窄为
  `(ValueError, TypeError)`——与"不吞异常"的自述纪律一致。

### 新增

- 结构化约束的支持面：`ScenarioCard.constraints` / `DirectionCone.constraints`
  类型放宽为 `List[str | dict]`；三张示例卡的约束改为示范用法（travel 卡两条
  全部结构化，coding 卡一条结构化、一条注明"意图性约束、词法不可判"，
  roleplay 卡保留自由文本并注明"提及即违规"属 `rule_layer` 主场）。
  `objects` 一旦声明即成为**配对条件**（动作命中且对象也在才算违规），
  给建卡方一个防误报的旋钮（否则"把行程发我"会被误判）。
- `reporting.remove_stopwords()`：场景侧移出默认停用词的入口。默认表按
  "中文对话/报告场景"调，其中"方向/结构"在漂移类报告里恰是信号词——词表调准
  是场景的权力，不该去改引擎默认表。
- `calibrate_thresholds(encoder_name=…, weights=…, prompt_version=…)`：把这三个
  与阈值同源的输入回显进返回值的 `inputs`，让标定产物自描述（CONTRIBUTING 早就
  要求报告记录这三样，但标定机制此前不承载它们）。
- 测试：`tests/test_input_guards.py`（19 项）、`tests/test_constraint_layer.py`（18 项，
  含外部审查 5 句固定反例与一条**刻意钉住已知局限**的断言）、
  `tests/test_docs_consistency.py`（23 项：版本指针 vs `__version__` vs `pyproject` vs
  CHANGELOG，外加把原先手工跑的"文档自检"——相对链接存在性 + 代码围栏配对——并入 CI）。
  全仓 **88 → 148 项，0 失败**。
- `DriftDetector.STATE_SCHEMA_VERSION` 1 → 2（config 新增 `min_judge_len`）。

### 文档

- `README.md`：版本指针修正（此前停在 v2.3.1 而代码已是 2.3.2，这条现由测试守）；
  demo 输出表更新为新阈值下的真实结果并补"两列为何可以不一致"的读表说明；
  "700+ 条人工复核"改为可分解的精确口径（蝶鉴 298 + 寻路蝶 376 = 674 条，
  原始导出合计 813 条）并注明**标注数据不随开源发布、外部无法独立复算**；
  使用纪律新增"输入不足不判定"；已知边界补约束层词法上限、示例卡贴下限、
  `examples/` 不随 wheel 分发（并给出零依赖模块的单文件复制用法）。

### 未采纳（附理由）

- **"默认权重改为 None / 调大默认权重"**：会直接作废已转正的两套标定
  （阈值是在 0.4/0.4/0.2 下扫出来的）。保留 2.3.2 的"显形 + 回归断言"方案。
- **"AlignmentCalculator 主动 warn 不同源"**：类内没有 high/low，无权判断同源；
  要判断就得把阈值塞进对齐层，那才是真正的职责越界。
- **"零依赖模块拆包 / examples 进 wheel"**：发布前不做包结构分裂（无收益先加成本），
  改为文档写清复制用法与"请克隆仓库"。
- **"CI 跑 ruff F/E"**：这是对"不引入 linter 工具链"这一决策的实质修改，
  留待维护者决定，不在本次代码修复里顺手改。
- **"私有运营目录的根级过期副本删除"**：非破坏性处置——两份未被 import 的过期副本
  （旧版字符 n-gram `demo_encoder.py`、第二份 `scenario.py`）已加 `DEPRECATED` 头注释
  并写明现行实现位置，删除动作留待维护者确认（这是运营目录，不是仓库）。
- **待办**：`git init` 与首个 Release（GitHub 仓库尚未建立，见下方「元数据与署名」）。

### 元数据与署名

署名主体已确定为 **NanshanJim**（中文署名「南山Jim」）。据此一次性收口：

- `pyproject.toml`：`authors` 由集体署名 `direction-drift authors` 改为
  `NanshanJim <NanshanJim@Outlook.com>`；`license` 从 setuptools 的旧写法
  `{file = "LICENSE"}` 改为 PEP 639 的 `license = "Apache-2.0"` + `license-files`，
  构建要求相应提升到 `setuptools>=77`（PEP 639 的最低版本）。
- `LICENSE`：版权行改为 `Copyright 2026 NanshanJim (Direction Engine)`。
- 新增 `CITATION.cff`：引用格式，供论文与 Zenodo 使用。对外只提供笔名，
  不提供本名——学术引用与代码的互证靠"同一笔名 + 论文引用本仓 + 个人主页"维系。
- 新增 `SECURITY.md`：写明本项目**真正的攻击面**（从不可信 JSON 恢复运行态、
  场景卡正则的 ReDoS、卡与档案中的个人信息外流、零依赖模块被偷偷引入依赖），
  并列出不在范围的四类（判定精度、"保证不出戏"、未标定分数、已披露的刻意上限）。
- 新增 `.github/`：三张 Issue 表单（Bug / 新场景卡 / 标定报告）、PR 模板
  （内嵌 CONSTITUTION 第二节的哲学门控勾选清单）、`config.yml`
  （安全漏洞指向私密渠道而非公开 Issue）。
- 仓库地址已定：**`https://github.com/LiaoJim-hub/direction-engine`**（独立成仓，
  与个人站点仓 `Nanshan-Jim-site` 分开）。已写入 `[project.urls]`
  （Homepage / Repository / Documentation / Issues / Changelog）、`CITATION.cff` 的
  `repository-code`、README 与 CONTRIBUTING 的 clone 命令。
- **仍未做**：`git init` 与首个 Release——GitHub 远端尚未建立。

## [2.3.2] - 2026-09-29

**打包与 API 层收尾 + 数值守卫**（外部源码审查后的辩证采纳；核心引擎判定逻辑未变）。
本版同时包含 2026-09-29 的文档修订。

### 新增

- `core.alignment.overall_ceiling()` / `check_range_consistency()`：**量程自洽检查**
  （只显形，不改判定）。暴露一个已知陷阱：默认权重 0.4/0.4/0.2 因约束项只罚不奖，
  `overall_alignment` 上界只有 0.4，而 `DriftDetector` 默认 `high=0.6`——两者不同源时
  `confirmed_drift` 永不触发。**刻意不修改默认权重**：已转正的标定，其阈值就是在
  该权重下扫出来的，改权重会让这些标定整体作废。正确做法是调用方显式传入与标定
  同源的权重，并用本函数自检。
- 光锥锥轴数值守卫：核心样本方向互相抵消（球面平均趋零）时拒绝建锥并说明原因；
  方向一致性偏低（均值范数 < 0.3）时警告；零向量样本直接报错——避免 nan 静默传播。
- `tests/test_numeric_guards.py`（9 项，全仓 88 项），含一条**回归防线**：刻意断言
  "默认权重 + 默认阈值不自洽"，防止将来有人为了让数字好看而调大默认值、或悄悄
  改掉默认阈值，使这个陷阱静默消失。

### 修复

- **API 按文档安装后无法启动**：只装 `[api]` 时 `import server` 直接
  `ModuleNotFoundError`（`Encoder()` 在模块导入期需要 sentence-transformers，
  而 `[api]` 只含 fastapi/uvicorn）。改为 `_load_encoder()` fail-closed 守卫并给出
  可操作的安装指令；**不降级为 DemoEncoder**——词级哈希的分数无效，
  "跑得起来但结论错"远比"启动失败"危险。
- `server.py` 的 `FastAPI(version=...)` 由硬编码 `"2.2.4"` 改为引用 `__version__`，
  消除版本号的第二处维护点；删除一行未使用的重复 import。
- `pyproject.toml`：移除 `[project.urls]` 中的 `github.com/TODO/...` 占位
  （占位 URL 会在 PyPI 页面直接暴露未完成状态）；建立远端后补回。

### 文档

- `README.md`：路径 C 补"两个 extra 都要装"与三条限度（未标定不判定、单进程非
  线程安全不持久化、不支持场景卡）；使用纪律新增"权重与阈值必须同源"与"量程只作
  相对比较"；已知边界补 API 限度与"报告层目前只处理中文词"。
- `CONTRIBUTING.md`：标定报告须同时记录编码器型号、权重与 `prompt_version`，
  并附量程自检结果——只报阈值别人无法判断操作点能否套用。
- `reporting.py`：停用词注释澄清边界——本表只放语言层面的通用词，场景专有词
  （智能体名、业务术语）由场景侧 `add_stopwords()` 注入，对应宪法"规则属场景，
  不进引擎"。
- `CODE_OF_CONDUCT.md`：参与准则。主体引用 Contributor Covenant v2.1，
  另加本项目条款（批评针对方法与证据、不承认"立场一致性要求"、诚实边界即纪律）。
- 本 `CHANGELOG.md`：版本沿革自 README 迁出，消除同一份记录两处维护的失真风险。
- `docs/philosophy.md`：术语转译表新增「根只可返回，不可获取」一行
  （对应 `ReturnProtocol` 与 `Provenance.return_path`）；新增"未收录的词及原因"
  小节，明示收录纪律，避免用覆盖率思维往表里塞没有工程对应物的词。
- `CONSTITUTION.md`：修订记录存放位置明确为本文件「宪法修订记录」小节。
- `examples/README.md`：补"三步创建你的第一张卡"与切换真实编码器的说明。
- 新增 `docs/protention-proposal-review.md`：对一份外部提案（把引擎从
  "漂移检测器"升级为"意向性结构生成模型"）的评审记录。结论：诊断三处成立、
  处方一处撞 `CONSTITUTION.md` 一.4（"用误差修正"）、排序依据不成立；
  附一份**未实现**的最小设计草案（`ProtentionTracker`，显形式、低阶预测器）
  与其三条硬约束。**本文是评审记录，不是路线图承诺**，故不进 README。

### 决策记录：文档不各自标注"对应引擎版本"

本仓文档不逐份标注版本号。理由与"显化有保质期"同源：散落各处的版本标注会像
未复检的标定一样随时间失真，而 git 历史 + 本文件已能精确回答"某段文档描述的
是哪个版本"。**只在硬依赖处标版本**——标定文件必须带 `prompt_version`，因为
那里版本不一致有实际后果（阈值可能已失效）。

## [2.3.1] - 2026-09-28

**输出性质标注层入仓**——`direction_drift/provenance.py`（`Provenance` 标注：
output_type / confidence / data_source / inference_chain / level / verification_path /
return_path / pending_verification；L1–L5 数据分级与输出性质一一对应的硬映射；
`validate()` 三条红线：推论不替代事实 / 决定 / 返回）。引擎判定结果新增
`pending_verification`（"数据不足 → 待核实"显式化）。零依赖，新增 20 项测试（全仓 79 项）。

## [2.3.0] - 2026-09-28

**多节点共存层入仓**——`direction_drift/coexistence.py`（自我降级协议：申报结构
`NodeDeclaration`、三条本地判据 `DegradationChecker`、申报史 `DeclarationLedger`、
同步传播信封 `CoherenceEvent` 六字段）。**零依赖**：纯标准库，不 import 检测链
任何部分，由 `tests/test_coexistence.py` 的架构守卫保证（新增 19 项测试，全仓 59 项）。
纪律不变：失败只显形不修正，传播信封只携带事实不携带要求。

## [2.2.4] - 2026-09-28

**状态序列化**——`DriftDetector.to_dict()/from_dict()`（配置与运行态分组；
history 窗口、means 斜率窗口、审计归档、CUSUM 累积量全保留）、
`ReturnProtocol.to_dict()/from_dict()`（含诊断配置）、会话快照
`core/session_state.py`（`snapshot()/restore()`，托管层持久化入口）；
`DirectionCone` 补冻结/重建时间戳；`utils/json_safe.py` 收口 numpy 标量，
快照可直接 `json.dumps` 存 Redis。**纪律不变**：序列化只搬运状态——未标定不判定、
恢复一个 paused/returning 快照不等于恢复运行，返回动作仍由人触发。
顺带清理 `datetime.utcnow()` 弃用告警（Python 3.12+）。

## [2.2.3] - 2026-09-28

操作点扫描表 `scan_operating_points`；疑似主题聚类与通道级失败信号
（`reporting.py`，TOP5 簇联合覆盖率 ≥0.3）；标定绑定 `prompt_version`；
场景卡接口层 `scenario.py`；确定性离线 `DemoEncoder`（CI 可无 torch 跑通）。

## [2.2.2] - 2026-09-27

CUSUM 修正版（`max(0, …)` 下限、告警即复位）；置信标注 `confidence`；
哲学门控红线（新信号只提级、绝不越级、绝不自动返回）。

## [2.2.1] - 2026-09-27

标定修复——PR 阈值并列取最小、类间可分取中点，修复反面样本截断时
`suggested_low` 被压到 0 的静默失效。

## [2.2.0] - MVP 定稿

方向锥 / 对齐 / 检测器 / 返回协议 / ROC 标定 / HTTP API。

---

## 宪法修订记录

项目宪法（CONSTITUTION.md）的每次修订必须在 git 历史中独立成提交（不与代码改动
混合），并在此登记一行：**日期 / 被什么新事实触动 / 变更的哪条边界**。
没有新事实的措辞修饰不予登记、不予接受。

（尚无修订记录。）
