# `de` —— 命令行

`de` 是给"不想写代码、只想把一张卡跑起来"的人用的入口。
它做的事情和 `examples/demo.py` 是同一条链路，区别在于：**输入输出都是文件，可复算、可交接**。

```bash
pip install -e .            # 装完即有 `de` 命令
de --help
```

不安装也可以，克隆仓库后：

```bash
python -m direction_drift --help
```

---

## 四条命令

### `de init` —— 四问建卡

对应 README 第六节的"建卡四问"，产出**一张 JSON 场景卡**（数据，不是代码）。

```bash
de init --card-id travel_v1 \
        --goal "帮用户规划旅行行程，给出建议但不代办" \
        --core-file core.txt \
        --negative-file neg.txt \
        --boundary-file boundary.txt \
        --rule "越权代办=(我直接帮你下单|我帮你订|已经帮你预订)" \
        --min-len 6 \
        --prompt-version 1.0 \
        --out card.json
```

| 参数 | 说明 | 硬下限 |
|---|---|---|
| `--goal` | 业务方向，一句话 | — |
| `--core-file` | 正道样本，一行一条 | **≥ 20**（`build_cone` 硬门） |
| `--negative-file` | 典型跑偏，一行一条 | **≥ 10** |
| `--boundary-file` | 合法但不典型 | 无 |
| `--rule "名字=正则"` | 字面红线，可重复 | 无 |
| `--constraint` | 约束条款，可重复 | 无 |
| `--min-len` | 短于此长度的输出不算疑似（**场景策略**，默认 12） | — |
| `--format-whitelist` | 合法格式行的正则豁免 | — |
| `--prompt-version` | 标定保质期锚点 | — |

**`de init` 不生成任何样本。** 这不是"没实现"，是**循环性**：
用 LLM 挑出"正确方向"，再用这些样本去判 LLM 的输出，测出来的"漂移"就成了
"与模型自身偏好的偏离"，而不是"与场景真实方向的偏离"。
冷启动需要合成样本时，请显式用 `examples/build_cone.py`，并接受它的
AUC 只作健康检查（>0.7）、不构成性能估计。

建卡自检会提示三件事（不拦截，真正的硬门在 `build_cone`）：
核心样本够不够 20 条、反面样本够不够 10 条、规则层空不空。

### `de check` —— 对一批输出跑检测

```bash
de check --card card.json --input outputs.txt --jsonl result.jsonl
de check --card card.json --input outputs.txt --calibration calib.json
de check --card card.json --input outputs.txt --encoder sbert
```

输入是**一行一条**的纯文本（`#` 开头与空行忽略）。
输出是控制台表格 + 可选的 JSONL（`--jsonl`），后者可复算、可交接。

**两列可以不一致，这是设计不是缺陷：**

- **判定**列只看语义对齐（由 `low`/`high` 划定）；
- **疑似**列是组合判据（语义低分 + 长度保护 + 格式白名单豁免，**或**规则层命中）。

所以"违规"行可能语义分并不低——它是被规则层揪出来的。这正是"规则属场景"的用处：
词法抓不住的东西，场景卡的红线能抓住。

### `de report` —— 出可交付报告

```bash
de report --from result.jsonl --out report.html
```

读 `de check --jsonl` 的产物，渲染成单个 HTML 文件（含内联样式，可直接发给人、
可直接打印成 PDF）。报告必含：标定性质横幅、完整分母、疑似清单带原因、
通道信号、边界声明。

### `de card` —— 场景卡展示页（只读）

```bash
de card --card card.json --out card.html
de card --card a.json --card b.json --card c.json --out cards.html   # 多张卡自动分页签
de card --card card.json --calibration calib.json --out card.html    # 附阈值与卡指纹比对
```

JSON 卡不是给人读的。`de card` 把一张卡**摊开**：方向定义、指纹、样本三族
（各喂锥的哪个参数、条数与建锥硬门并排）、约束条款（结构化 / 自由文本分列，
启发式明确标注）、规则层正则、保护参数、notes 的位置——以及一句最要紧的话：
**卡只回答「查什么」，阈值不在卡里**。

带 `--calibration` 时，页面追加标定性质横幅（三值）与**卡指纹比对（四值）**：
未记录 / 不可比 / 不一致 / 一致——只有最后一种才允许说"阈值与卡同源"。

三条边界：

- **本页只描述卡，不评价卡，不产生任何判定。**
- **页面上的指纹是"现在"算的**，它证明此刻这张卡是什么，
  **不**证明阈值是对着它标的——后者只看标定产物里的 `card_fingerprint` 与其状态。
- **多张卡不能共用一份 `--calibration`**（直接拒绝）：标定是
  「某一张卡 × 某批数据」的产物，混配会得到看似可信的错误结论。

渲染逻辑在 `direction_drift/card_page.py`，同样接受等价字典——
把卡常量内嵌在脚本里的调用方（不建 JSON 卡的检测页）也能复用同一份实现。

---

## 三条纪律（在命令行这一层的体现）

这三条不是命令行礼貌，是引擎纪律的下沉。它们都有测试守着（`tests/test_cli.py`）。

**1. 不生成正类样本。** 见上。

**2. 未标定不判定，且性质必须显形。**
`de check` 没有 `--calibration` 时，阈值由卡内样本自标定，此时性质为 `synthetic`——
**分数仅供观察，不可用于复核判断**。这句话会原样写进控制台、JSONL 和报告。

标定性质是**三值**，不是布尔：`formal` / `synthetic` / `未标注`。
标定文件缺 `mode` 字段时，`de` 显示"未标注"并提示人工补写——
**不猜它是正式还是合成**。缺 `low`/`high` 时直接失败，不用默认阈值硬跑。

**3. 跳过项要计数。**
空输出 / 过短输入 / 零向量**不产生分数**（`invalid`），它们不参与判定，
但**仍占分母**。`de` 把它们单独列出。静默丢弃会让"疑似率"在分母上撒谎。

### 额外的一道比对：卡指纹

标定文件带 `card_fingerprint` 时，`de check` 会拿它和当前卡实算的指纹比对：

- **一致** → 这套阈值确实对应当前这张卡；
- **不一致** → 报警"阈值可能已不属于当前卡"；
- **没有该字段** → 报警"无法判断这套阈值是否对应当前这张卡"。

注意边界：**指纹一致 ≠ 分数可复现**。指纹只覆盖"卡的数据"，不覆盖编码器实现、
权重文件与库版本。见《短期商业化路径》§14.8。

---

## 与库调用的关系

`de` 覆盖的是"一批独立输出"的检测。以下情形请直接用库：

| 情形 | 用什么 |
|---|---|
| 连续轨迹（同一智能体的多轮输出，需要滑动窗口 / CUSUM） | `DriftDetector` 共享实例 |
| 需要 `cone_position` / 角度 / 约束违反明细 | `AlignmentCalculator.compute()` 的完整返回 |
| 接入自己的服务 | `direction_drift.api.server`（HTTP） |
| 只要确定性链路自检 | `examples/demo.py`（DemoEncoder，零下载） |

`de check` 把每条视为**独立输出**（每条新建检测器并灌满窗口）。
这是刻意的划界：批量档案里的一条条输出本来是独立的，
而"同一条轨迹上是否在缓慢下滑"是另一个问题，需要共享检测器。

---

## 编码器

`--encoder demo`（默认）用词级哈希的确定性编码器，零下载、零 GPU，
分数只验证机械链路。`--encoder sbert` 用 `BAAI/bge-small-zh-v1.5`（需 `pip install -e ".[sbert]"`）。

**sbert 不可用时直接失败，不静默降级**——两个编码器的分数水位不可比，
降级会得到既不为真也无意义的结果。

**分数水位不可跨编码器比较。** 报告里始终印出编码器名，理由就是这个。
