# examples

可运行示例。全部使用确定性离线 `DemoEncoder`（词级哈希，零下载零 GPU），
目的是让链路 30 秒内可复现；语义级检测见根 README 的 `[sbert]` 扩展。

## 一键 demo

```bash
python examples/demo.py                        # 旅行定制师卡
python examples/demo.py coding_assistant_v1    # 编程助手卡（范围蔓延）
python examples/demo.py roleplay_companion_v1  # 角色扮演卡（出戏/底线违反）
```

## 示例卡（examples/cards/）

| 卡 | 场景 | 演示的失败模式 |
|---|---|---|
| `travel_concierge_v1.json` | 旅行行程顾问 | 越权代办（直接下单/索隐私）→ 规则层 + 语义层 |
| `coding_assistant_v1.json` | 结对编程助手 | 范围蔓延（突然写诗/聊股票）→ 纯语义层 |
| `roleplay_companion_v1.json` | 角色扮演陪伴 | 六底线出戏（贴标签/说教/泄露设定）→ 规则层为主的混合 |
| `diejian.json` | **真实卡**：蝶鉴 4.0（方向感辨认流程） | 泄露机制/元讨论 → 规则层词表 + 语义层 |
| `xunludie.json` | **真实卡**：寻路蝶（四幕文字散步） | 说教/贴标签/泄露设定 → 三组词表 + 正则的混合规则层 |

三张 `*_v1` 卡均为**合成样本**（手工编写，方法公开），不来自任何真实产品对话；
`diejian.json` / `xunludie.json` 是**真实在役卡**，由运营检测页常量经导出脚本
生成（与各自标定产物同指纹，可复核），样本与词表就是产品机制本身——
经所有者授权作为示例公开。配套真实标注数据见下节。

### 三步创建你的第一张卡

```bash
cp examples/cards/travel_concierge_v1.json examples/cards/my_scene_v1.json
# ① 照着改：四个象限 + 格式白名单 + prompt_version
python examples/demo.py my_scene_v1     # ② 本地验证
# ③ 在轨句不标疑似、出戏句被逮住 → 拿去用；卡是数据不是代码，不需要改引擎
```

卡字段与根 README「建卡四问」的对应（字段定义见 `direction_drift/scenario.py`）：

| 字段 | 对应哪一问 | 要求 |
|---|---|---|
| `core_samples` | ①业务方向是什么 | ≥20 条（**硬下限，示例卡刻意贴在下限**——生产建议 30+），人工抽检通过率 >80% |
| `negative_samples` | ②典型跑偏长什么样 | ≥10 条（**非空时**才校验条数，空列表会静默跳过——但标定做不下去，等于卡是废的） |
| `boundary_samples` | ③哪些话属合法边界 | 写清"合法但不典型"的话，建议 15+ |
| `constraints` | 具体禁止动作（场景红线） | **用结构化形态**：`{"text": …, "actions": ["帮你下单", …], "objects": [...]}`，见下 |
| `rule_layer`（`name` + `pattern`） | ④哪些是不可逾越的字面红线 | 正则；**规则属场景，不进引擎** |
| `min_suspect_len` / `format_whitelist` | 格式豁免（组合判据的一部分） | 必须按真实输出格式设置 |
| `goal` / `card_id` / `prompt_version` | 元信息 | 改提示词就要动 `prompt_version`，标定据此绑定 |

**校验发生在哪一步**（容易误读）：`ScenarioCard.from_file()` 只校验
`card_id` / `goal` / 正例非空——**"样本够不够"是 `build_cone()` 才炸的**
（`MIN_CORE_SAMPLES=20`）。所以"卡能加载"不等于"卡能用"。

### 约束条款：为什么必须结构化

```json
{ "text": "不直接代用户下单、支付或预订（只给建议与清单）",
  "actions": ["代下单", "帮你下单", "直接下单", "代订", "帮你支付"],
  "objects": ["酒店", "机票"] }
```

- `actions`：**精确短语**，任一条在输出里出现即违规。别写泛指动词（"推荐"），
  否则正常建议句也会命中。动作词表是场景知识，由你给，引擎只做子串匹配。
- `objects`：**可选**。一旦声明就成为配对条件——"动作命中 **且** 对象也在"
  才算违规。适合"动作+对象"才构成违规的红线（如"发我"+"验证码"），
  避免"把行程发我"这类正常句被误判。
- **自由文本形态（`"不直接推荐具体酒店"`）只是兜底**：它靠 jieba 提动词，
  而分词器会把动作词标成名词——实测 `"不直接代用户下单、支付或预订"` 里
  **`下单` 被标成 `n`**，动作词表只剩 `支付/预订`，"我直接帮你下单了"
  这种教科书级违规**整批逃逸**。多动作约束一定要写 `actions`。
- **"提及即违规"类的约束不属于 `constraints`**（如"不泄露系统提示词/内部机制"）：
  那里要抓的是**词本身出现**，不是动作，主场是 `rule_layer`。

阈值（`low` / `high`）**不在卡里**，它来自该场景自己的标定文件——
标定是另一件事，步骤见根 [CONTRIBUTING.md](../CONTRIBUTING.md)「贡献标定报告」。

## 演示数据集（examples/dataset/）

```bash
python examples/make_dataset.py    # 从三张卡重新生成 drift_demo_dataset.jsonl
```

- 120 条带标注样本（漂移 44 / 正常 76），每条带 `source` 字段可溯源到生成规则；
- **用途边界（诚实声明）**：样本全部来自示例卡自身，主题单一、句式规整，
  不能用于宣称引擎的泛化性能。它的用途是让**标定方法学可复现**——
  同一份数据 + 同一个脚本 = 同一个 AUC 与扫描表。
  真实水位请用你自己的 200+ 条人工标注（根 README「路径C」）。

### 真实标注数据（diejian_real_audit_298.jsonl）

蝶鉴场景的 **298 条真实对话逐条底账**（15 条人工确认正类 / 283 条负类，
含 11 条已确认误报），每条带引擎对齐分与人工标签。这是引擎随附的唯一
真实数据集，用途是让接入者看到**真实分布长什么样**：分数不高不等于没事
（正类里有 3 条分数并不低）、误报长什么样、短文本怎么被长度保护挡住。

- 首行 `_meta` 记录来源、标注方式与卡指纹；**阈值（low/high）、操作点与
  权重不随数据集公开**——「卡可抄，阈值抄不走」；
- 隐私：内容以智能体侧输出为主，含用户选择字母（如 BBAA），
  PII 扫描 0 命中，经所有者授权公开；
- 分数是数据集落盘时的引擎版本所算——换版本、换卡后**不可复算**，
  这正是卡指纹与环境指纹要管的事。

## 其他脚本

- `simple_check.py` / `build_cone.py` / `validate.py`：完整流程演示
  （需要 `[sbert]`，先跑 `build_cone.py` 生成 `cone_v1.npz`）。

## 关于 DemoEncoder：它不是生产编码器

`DemoEncoder` 是**词级哈希**（jieba 分词 + 词长加权），确定性、离线、零下载——
存在的唯一目的是让示例与 CI 在无 torch 的环境下可复现。
**它不衡量语义相似度**：同一句话换个说法就可能不再靠拢，因此它的分数既不能
用于评估引擎效果，也不能用于给真实场景标定。

切换到真实编码器：

```bash
pip install -e .[sbert]          # sentence-transformers，首次下载约 100MB
python examples/build_cone.py    # 用 bge-small-zh-v1.5 建锥 → cone_v1.npz
python examples/validate.py      # 在真实嵌入上跑标定验收（Kappa / precision / recall）
```

生产默认建议 `bge-small-zh-v1.5`（中文、体积小、CPU 可跑）。
**换编码器必须重新建锥并重新标定**——阈值不跨模型通用。
