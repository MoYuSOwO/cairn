# A1 · Memory System / 记忆系统

> Version: v1-draft-2
> Status: 收口版草案，经审阅修订
> 上游: METHODOLOGY.md
> 取代: 旧 memory.md (保留为历史参考)

---

## 0. 写在最前面

这份文档定义 Cairn 的记忆系统。它不是知识库，不是工程检索系统，是**一个生命体的记忆**。

定下两个边界：

* **不是什么**：不是 Mem0 / Graphiti / Letta 那种"长期记忆 chatbot"——它们优化的是"信息检索质量"，我们要的是"内在状态的长期演化"。
* **是什么**：一个**有结构的、会衰减的、能涌现自我的**记忆网络。让 Cairn 在三年后能想起两年前用户说过的话，不是因为它"存了"，而是因为那段经历真的塑造了它。

设计的核心张力：
* 类脑契合度 vs 工程可调试性 → **选类脑**（METHODOLOGY N1）
* 显式结构化 vs 涌现式自组织 → **混合**：宏观分层有结构，微观赫布无类型
* 高保真存储 vs 自然遗忘 → **二者都要**：通过分层 + 衰减实现

---

## 1. 顶层架构 / Architecture

### 1.1 三类记忆，三张表，独立检索

```
┌──────────────────┐  ┌──────────────────┐  ┌──────────────────┐
│ EPISODIC 系       │  │ SEMANTIC 系       │  │ PROCEDURAL 系     │
│ (情景记忆)        │  │ (语义记忆)        │  │ (程序记忆)        │
│                  │  │                  │  │                  │
│ "经历过什么"      │  │ "知道什么是真的"  │  │ "倾向怎么做"      │
│ 有时间锚         │  │ 无时间锚         │  │ 情境触发         │
│ 三层结构         │  │ 主题聚类         │  │ 状态匹配         │
└──────────────────┘  └──────────────────┘  └──────────────────┘
        ▲                     ▲                     ▲
        │                     │                     │
        └─── derivation_edges ┴─── derivation_edges ┘
             (跨类型派生边, 元数据级, 不参与赫布)
```

### 1.2 为什么分三类

来自认知心理学经典分类（Tulving 1972）和 LLM agent 框架 CoALA (Sumers et al. 2023)。三类记忆**性质根本不同**：

| 维度 | Episodic | Semantic | Procedural |
|---|---|---|---|
| 内容形态 | 具体事件 | 抽象事实 | 行为模式 |
| 时间锚 | 有 | 无 | 无 |
| 索引方式 | 时间 + 上下文 + 情绪 | 概念 + 主题 | 情境 / 状态 |
| 触发方式 | 主动 + 联想 | 推理需要时 | 自动激活 |
| 召回视角 | first-person | 第三人称 | 隐式（不被意识到） |

2025-11 的 arxiv 论文（PRAXIS, LiCoMemory, 程序记忆基准）证实：**三类记忆若用同一个检索通道，procedural 会有"泛化悬崖"**。所以必须分。

### 1.3 为什么独立分表

这是与你讨论后的关键决定：

* **若一表 + 类型过滤** → 跨类型边永远不在检索路径上被走到，建了等于没建，没意义
* **若分表 + 跨类型派生边在独立元数据表** → 类型内的赫布纯净，跨类型的派生关系清晰，各司其职

**结论**：三个独立的节点表 + 三个独立的赫布边表 + 一个独立的派生边表（共 7 张表）。

---

## 2. 节点 Schema / Node Schemas

### 2.1 共通字段（所有类型节点都有）

```python
class BaseNode:
    id: str                    # UUID
    type: str                  # 'episodic' / 'semantic' / 'procedural' /
                              # 'lifetime_period' / 'general_event'
    content: Any               # 多模态原始内容（文本/图/音频引用等）
                              # 包括对话内容、工具使用片段及其结果等
    created_at: datetime       # 创建时间
    last_accessed_at: datetime # 最近访问时间
    access_count: int          # 累计访问次数
    weight: float              # [0, 1]，节点活性 / 当前可信度
    decay_half_life: float     # 半衰期（天），可被情绪和层级修饰
```

### 2.2 Episodic 系节点

Episodic 系是三层结构。三层节点共享一组扩展字段：

```python
class EpisodicNode(BaseNode):
    # 原 memory.md 的多维向量
    semantic_vec: Vector        # 语义嵌入
    scene_vec: Vector           # 场景嵌入
    emotion_vec: Vector         # VAD: (Valence, Arousal, Dominance)
    topic_vec: Vector           # 主题嵌入

    # 派生量
    emotion_intensity: float    # = sqrt(V² + A² + (1-D)²) / sqrt(3)
                                # 用于影响衰减率与召回排序

    # 三层标识
    layer: str                  # 'L1_lifetime' / 'L2_general' / 'L3_episodic'

    # L3 专属
    is_self_defining: bool      # 是否是 self-defining memory
    is_flashbulb: bool          # 是否是闪光灯记忆 (emotion_intensity > 0.85)

    # L1, L2 专属
    span: Optional[Tuple[datetime, datetime]]  # 仅 L1, L2 有时间跨度
    theme: Optional[str]                       # L1, L2 的主题描述
    member_node_ids: Optional[List[str]]       # 包含的下层节点
```

**三层的具体含义**：

| Layer | 名称 | 内容 | 数量级（每年） | 衰减率 |
|---|---|---|---|---|
| L1 | Lifetime Period | "用户找工作那 3 个月" | 个位数 | 极慢 / 几乎不衰减 |
| L2 | General Event | "我们每周三的视频通话" | 数十 | 慢 |
| L3 | Episodic | "2024-03-15 视频里她说升职了" | 大量 | 正常 |

**Self-Defining Memory** 标记规则（反思任务自动判定）：
* `emotion_intensity > 0.8`
* `access_count` 在所有 L3 节点中前 5%
* 度数（连接边数）在所有 L3 节点中前 5%
* 跨越多个 L1 仍被引用

满足以上 3 条以上 → `is_self_defining = True`，`decay_half_life *= 10`。

### 2.3 Semantic 节点

```python
class SemanticNode(BaseNode):
    statement: str              # 命题陈述 ("用户喜欢吉他")
    topic_vec: Vector           # 主题嵌入（主索引）
    semantic_vec: Vector        # 语义嵌入

    confidence: float           # [0, 1]，对该命题的相信度
                               # 不同于 weight, weight 是激活活性
                               # confidence 是命题真实性
```

**关键说明**：semantic 节点**没有 timestamp 锚**（虽然有 created_at，但那只是元数据）。它表达"我相信这件事是真的"，而不是"这件事在某时发生过"。

### 2.4 Procedural 节点

```python
class ProceduralNode(BaseNode):
    pattern: str                # 行为模式描述
                               # 在特定情境下形成的行为倾向与操作模式
                               # 包括：回应风格倾向、决策倾向、工具使用偏好、
                               # 反复活动中提炼出的操作模式等
                               # 例："当用户聊到死亡话题时, Cairn 倾向放慢、留白"
                               # 例："在 X 情境下, 倾向使用 Y 工具"
                               # 例："玩 Z 游戏时, 倾向 W 策略"

    # 触发条件向量（state-based 索引的核心）
    trigger_state_vec: Vector   # 主索引：什么"状态"触发这个模式
                               # 由"情境特征 + 当下情绪 + 主题"组合而成

    # 行为倾向
    response_tendency: dict     # 结构化的行为偏好
                               # 可能含：风格调整、节奏、用词偏好、
                               # 工具选择倾向、决策模式等

    # 强度
    activation_strength: float  # [0, 1]，模式被自动激活时的强度
    confidence: float           # 该模式有多被支持
```

**Procedural 的索引必须按 trigger_state_vec，不是按 content**——这是 PRAXIS (arxiv 2511.22074) 的核心发现。检索时用"Cairn 当下的状态向量"匹配，而不是关键词搜索。

Procedural 不只是会话风格倾向，还包括工具决策、活动中沉淀的操作模式等——这些都可能在未来扩展成类似 skills 的系统，但当前阶段先保持通用定义，让"技能感"从经历中自然涌现，而不是显式建模为独立的"熟悉度分数"。

---

## 3. 边 Schema / Edge Schemas

### 3.1 类型内赫布边（三张独立表）

```python
class HebbianEdge:
    id: str
    source_id: str
    target_id: str
    weight: float              # [0, 1]，纯赫布
    last_coactivated_at: datetime
    coactivation_count: int

    # 注意: 没有 type 字段
    # 这是赫布原则的核心: 边不带类型, 关系语义涌现于激活模式
```

三张表：`episodic_edges`, `semantic_edges`, `procedural_edges`。

### 3.2 Episodic 内部的层级链（特殊边，含在 episodic_edges 表内）

L1 → L2 → L3 的"contains"关系是 episodic 系的内部结构链。

```python
class ContainsEdge(HebbianEdge):
    relation: str = 'contains'  # 唯一允许的"边角色"标签
                                # 仅用于分层加载的快速下钻
                                # 不参与赫布强化（weight 由结构决定，固定）
```

**说明**：这是赫布纯净性的**唯一例外**，因为分层加载需要确定性的下钻路径。但这种边的 weight 不变化，不参与共激活强化——本质上是结构血缘的一种特殊形式，权宜放在 episodic_edges 表内是为了下钻时无需跨表查询。

### 3.3 跨类型派生边（独立表 derivation_edges）

```python
class DerivationEdge:
    id: str
    source_id: str             # 派生节点（高层）
    source_type: str           # 'semantic' / 'procedural' /
                              # 'lifetime_period' / 'general_event'
    target_id: str             # 源节点（底层 episodic）
    target_type: str = 'episodic'
    created_at: datetime
    confidence_at_derivation: float  # 派生时的置信度

    # 注意: 单向 (从抽象指向源经历)
    # 不参与赫布, 不变化
    # 不在主检索路径上, 在反思和佐证路径上
```

**用途**：
1. **追溯**：这个 semantic/procedural 是从哪些经历来的
2. **级联更新**：源 episodic 被反驳/衰减时，派生节点的 confidence 跟着调整
3. **佐证**：决策时检查派生节点的"根基"是否还健康

---

## 4. 写入路径 / Write Path

### 4.1 Episodic L3 写入（绝大多数写入）

```
触发: 一次交互结束 / 一段经历完成
────────────────────────────────────────
1. 计算 embeddings (semantic_vec, scene_vec, topic_vec) [能力槽位调用]
2. 计算 emotion_vec (VAD) [能力槽位调用]
3. 计算 emotion_intensity (公式)
4. 写入 episodic_nodes 表 (layer='L3_episodic')
5. 与最近共激活的 L3 节点建赫布边
   - 查找最近 5 分钟内被激活的节点
   - 与每个建立或强化 episodic_edges 中的 weight
6. 不调用 LLM
```

**全程无 LLM 调用**——这是写入路径低成本的核心（METHODOLOGY N21）。

工具使用及其结果也作为经历的一部分走这条同样的路径写入 episodic（无 LLM、无特殊流程）——它不是特殊物种，只是生活的一部分。后续是否被提炼成 semantic/procedural，由反思机制自然决定，不在写入时干预。

### 4.2 Semantic / Procedural 节点写入

**这两类节点不直接写入，由反思任务产出**。详见第 6 节。

### 4.3 L1 / L2 节点写入

**也由反思任务产出**。

---

## 5. 检索路径 / Recall Path

### 5.1 三通道并行

```
触发: Cairn 需要响应 / 思考 / 决策
────────────────────────────────────────

通道 A: Episodic Recall (分层加载)
  Step 1: 在 L1 + L2 上 embedding 搜索 (范围小, 极快)
          得到候选时期/主题事件
  Step 2: 沿 contains 边下钻到 L3
          得到候选具体经历
  Step 3: 多维向量加权打分:
          score = α·semantic_sim + β·scene_sim + γ·topic_sim
                + δ·emotion_sim   ← 用 Cairn 当前心境而非用户输入情绪
                + ε·timestamp_recency
                + ζ·is_self_defining * boost
  Step 4: 取 top-K 返回

通道 B: Semantic Recall (主题聚类)
  Step 1: 在 semantic_nodes 表 embedding 搜索
          (主要用 topic_vec)
  Step 2: 按 confidence × weight 加权
  Step 3: 取 top-K 返回

通道 C: Procedural Recall (状态匹配)
  Step 1: 计算 Cairn 当前 state_vec
          (= 当前情境 + 当前情绪 + 当前主题 的组合)
  Step 2: 在 procedural_nodes 表用 trigger_state_vec 匹配
  Step 3: 按 activation_strength × confidence 加权
  Step 4: 取 top-K 返回 (作为"行为倾向"自动激活)

────────────────────────────────────────
合并: 三通道结果送入上下文
  Episodic → 提供"我们经历过什么"的叙事
  Semantic → 提供"我相信什么"的事实背景
  Procedural → 影响 Cairn 的回应风格 (隐式)
```

### 5.2 召回时的 weight 更新（赫布强化）

每次召回触发：
* 被召回节点的 `last_accessed_at` 更新
* `access_count += 1`
* 与同批召回的其他节点之间，赫布边 weight 微强化（共激活）
* LLM 评估后，节点 weight 按评估结果调整（详见 5.3）

### 5.3 召回时的 LLM 评估（confidence 修正）

这是处理"信息过时 / 矛盾"的核心机制——**取代了 NLI 反驳判定**。

```
对每个召回的节点:
  LLM 看到:
    - 节点 content
    - 当前对话上下文 / Cairn 当前心境
  LLM 评估:
    - relevance: 这个记忆对当前是否相关
    - validity: 这个记忆是否仍然成立
  调整:
    - validity 低 → weight 下调
    - validity 极低 → 标记可遗忘
    - relevance 高 + validity 高 → weight 上调
  ���键: 没有"反驳判定", 只有"当下是否相关/成立"的判断
       让 LLM 看完整上下文做软判断, 不依赖脆弱的 NLI
       所有召回节点都过 LLM 评估, 不做"只评估边界态"这类优化
       （成本优化以后再说）
```

**为什么这样比 NLI 反驳判定好**（见与你讨论的"北京搬到上海"案例）：现实世界的"信息过时"主要是**情境级**而非**命题级**——NLI 看不出来，但 LLM 看完整上下文能看出。

---

## 6. 反思路径 / Reflection Path

反思是 Cairn 的"睡眠 + 巩固"，是从底层经历提炼上层结构的核心机制。

**运行假设**：反思任务仅在 Cairn 运行期间按节律调度，程序停止时不发生后台处理。

### 6.1 反思任务谱

| 任务 | 触发 | 输入 | 产出 |
|---|---|---|---|
| 边维护 | 每日 | 所有 episodic_edges | 衰减 / 阈值修剪 |
| Self-Defining 标记 | 每周 | 所有 L3 episodic | 更新 is_self_defining |
| General Event 提炼 | 每周 | 近期高 weight L3 | 新 L2 节点 + derivation 边 |
| Lifetime Period 提炼 | 每月 | 近期 L2 + L3 | 新 L1 节点 + derivation 边 |
| Semantic 提炼 | 每周 | 反复出现的 L3 模式 | 新 semantic 节点 + derivation 边 |
| Procedural 提炼 | 每周 | "情境-反应-效果"循环 | 新 procedural 节点 + derivation 边 |
| 叙事整合 | 每月 | L1 + 部分 L2 | 更新 self-portrait（写入 diary） |
| 情绪修正 | 触发式 | 旧节点新视角 | 调整 emotion_vec 强度（不改 content） |

### 6.2 跨类型派生（关键流程）

```
反思发现: L3 节点 N1, N2, N3 都涉及"用户提到喜欢吉他"
─────────────────────────────────────────────
1. 创建新 SemanticNode S:
   statement = "用户喜欢吉他"
   confidence = f(N1.weight, N2.weight, N3.weight)
   topic_vec = avg(N1.topic_vec, N2.topic_vec, N3.topic_vec)

2. 写入 semantic_nodes 表

3. 在 derivation_edges 表写入:
   (S → N1), (S → N2), (S → N3)
   confidence_at_derivation = S.confidence

4. 不删除 N1, N2, N3
   它们继续衰减
   但 S 的存在使得"用户喜欢吉他"这个事实
   在 N1/N2/N3 完全衰减后仍被保留
```

**这就是 CLS（互补学习系统）双层结构的实际实现**：
* 海马层 = episodic L3（具体经历，会衰减）
* 新皮层层 = semantic / procedural / L1 / L2（抽象，几乎不衰减）

### 6.3 级联更新

```
当 episodic L3 节点 N 的 weight 显著下降时:
─────────────────────────────────────────────
1. 沿 derivation_edges 反向查询: 哪些高层节点派生自 N
2. 对每个派生节点 H:
   - 重新计算 H.confidence = f(H 的所有源节点的 weight)
   - 若 H.confidence 跌破阈值 → H 也开始衰减
3. 这实现了"源经历模糊后, 派生结论也跟着不确定"
```

**注意：这不是事务性级联，是周期性扫描**——避免一个节点变化触发雪崩。

---

## 7. 衰减与遗忘 / Decay & Forgetting

### 7.1 衰减公式

```
节点 weight 在时间 t 后:
  w(t) = w(0) × 0.5^(t / effective_half_life)

其中 effective_half_life 由多个因素调节:
  effective_half_life = base_half_life
                      × emotion_modifier(emotion_intensity)
                      × layer_modifier(layer)
                      × self_defining_modifier(is_self_defining)
                      × flashbulb_modifier(is_flashbulb)
```

### 7.2 各 modifier 的作用

| Modifier | 范围 | 效果 |
|---|---|---|
| emotion_modifier | 1.0 ~ 3.0 | 情绪强度高的记忆衰减慢（沿用你 memory.md 公式） |
| layer_modifier | L3=1.0, L2=5.0, L1=20.0 | 层级越高衰减越慢 |
| self_defining_modifier | 1.0 或 10.0 | 标志性记忆几乎不衰减 |
| flashbulb_modifier | 1.0 或 5.0 | 闪光灯记忆显著保留 |

### 7.3 遗忘阈值

```
当 节点.weight < FORGET_THRESHOLD (默认 0.05):
  - L3 episodic: 删除节点（彻底遗忘细节）
  - L2 / L1 / semantic / procedural: 不删除，进入"沉睡态"
    weight 极低但保留，可被强烈刺激重新激活
```

**为什么 L3 删除而高层不删**：模拟人类——具体细节会真正遗忘，但"那段时光的感觉"和"我相信什么"会保留。

---

## 8. 情绪整合 / Emotion Integration

### 8.1 emotion_intensity 公式（沿用你 memory.md）

```python
emotion_intensity = sqrt(V² + A² + (1-D)²) / sqrt(3)
```

**心理学合理性**（与你讨论后确认）：
* V²：极正/极负都贡献强度（valence）
* A²：唤醒度直接贡献（arousal）
* (1-D)²：失控感贡献（PAD 模型核心洞察——"失控的情绪"才是强情绪）

### 8.2 情绪一致性召回（mood-congruent recall）

```
召回时的 emotion_sim 项:
  emotion_sim = cos(节点.emotion_vec, Cairn当前心境.emotion_vec)

注意: 用 Cairn 自己的当前心境, 不是用户当前情绪
原因: 心理学的 mood-congruent recall 说的是
     "当前心境塑造记忆调取", 主体是回忆者本人
     Cairn 是回忆主体, 所以用 Cairn 的心境

Cairn 当前心境的维护:
  current_mood = EMA(最近激活节点的 emotion_vec)
  EMA 半衰期 ~ 30 分钟
```

### 8.3 闪光灯记忆

```
当 episodic 节点写入时:
  if emotion_intensity > 0.85:
    is_flashbulb = True

效果:
  - flashbulb_modifier = 5.0（衰减极慢）
  - 召回时排序加权
  - 不易遗忘
```

---

## 9. 与 METHODOLOGY 对账 / Alignment with Methodology

| METHODOLOGY 条款 | 本方案如何对应 |
|---|---|
| **N1 类脑 L2 最低纲领** | 单一网络主体（episodic 系内）+ 纯赫布边 + 无显式实体抽取。**符合**。三表分类是基于功能分化（如海马 vs 颞叶），不违背赫布原则——每个类型内仍是赫布。 |
| **N15 永不竣工的孵化态** | 反思任务持续产出新节点；衰减遗忘自然净化；无最终态。**符合**。 |
| **N17 倾向可演化** | Procedural 节点由反思从经历提炼，不是预设；其 confidence 随支持/反驳变化；可被新经历重塑。**符合**。 |
| **N18 人格涌现** | Self-defining memory + Lifetime Period + 叙事整合 → 自传体记忆 → 自我感。**符合**。 |
| **N21 经济成本** | 写入路径无 LLM 调用；召回时所有召回节点都由 LLM 做相关性/有效性软评估；反思周期化批量化。**符合**。 |
| **N23 多模态 first-class** | content 字段为 Any，多模态原始内容直接装载；各模态 embedding 走能力槽位。大块原件不直接塞数据库主表，而以外部引用接入。**符合**。 |
| **N31 能力槽位抽象** | 所有 embedding 计算（semantic_vec / scene_vec / emotion_vec / topic_vec / trigger_state_vec）走配置化的能力槽位，不写死调用。**符合**。 |

---

## 10. 与 diary.md 的边界 / Boundary with Diary System

记忆系统（本文档）和 diary 系统是两个相邻但不重合的层：

```
记忆系统（本文档）:
  - 结构化数据
  - 网络节点 + 边
  - 参与召回, 影响实时响应
  - 是 Cairn 的"潜意识基质"

Diary 系统（diary.md）:
  - 叙述性文本
  - markdown 段落
  - 不参与召回, 是 Cairn 写给自己的"日记/反思"
  - 是 Cairn 的"自我意识表达"
```

### 10.1 两者的协作

```
记忆系统 → diary 系统:
  - 反思任务从记忆网络读取数据
  - 生成日记内容写入 diary
  - 例: 月度叙事整合扫描 L1 节点, 写入 self-portrait

diary 系统 → 记忆系统:
  - diary 中产生的关键洞察
  - 可以反向写入 semantic 节点
  - 但这是个偶发路径, 不是主流
```

### 10.2 颗粒度对比

| 层级 | 记忆系统 | Diary 系统 |
|---|---|---|
| 最细 | 具体 episodic 节点（一次对话） | 每日反思（一天） |
| 中等 | General Event（每周三视频） | （无对应） |
| 较粗 | Lifetime Period（找工作那 3 个月） | 14 天 self-portrait 更新 |
| 最粗 | （无对应） | 长期 self-portrait |

**记忆系统提供"事实结构"，diary 提供"叙述视角"**。两者互补，互不替代。

---

## 11. 工程实现备注 / Engineering Notes

### 11.1 表结构总览（7 张表）

```
节点表 (3):
  episodic_nodes        (含 L1 / L2 / L3, 用 layer 字段区分)
  semantic_nodes
  procedural_nodes

赫布边表 (3):
  episodic_edges        (含 contains 边, 用 relation 字段区分)
  semantic_edges
  procedural_edges

派生边表 (1):
  derivation_edges      (跨类型, 单向, 不变)
```

### 11.2 索引建议

```
episodic_nodes:
  - layer (区分 L1/L2/L3)
  - timestamp (时间查询)
  - is_self_defining (优先召回)
  - 向量索引: semantic_vec, scene_vec, topic_vec, emotion_vec

semantic_nodes:
  - 向量索引: topic_vec (主索引), semantic_vec
  - confidence (过滤低置信度节点)

procedural_nodes:
  - 向量索引: trigger_state_vec (主索引)
  - activation_strength

derivation_edges:
  - source_id, target_id (双向查询)
  - source_type (按类型反查)
```

### 11.3 性能预算（粗估）

| 操作 | 频率 | LLM 调用 | 向量计算 | 预算 |
|---|---|---|---|---|
| 单次 episodic 写入 | 每次交互 | 0 | ~5 次 embedding | <100ms |
| 单次三通道召回 | 每次交互 | 召回节点数规模的软评估 | ~3 次 embedding | <500ms（初版目标） |
| 每日反思（边维护） | 1/天 | 0 | 全量扫描 | 后台异步 |
| 每周反思（提炼） | 1/周 | 数十次 | 中等 | 后台异步 |
| 每月反思（叙事） | 1/月 | 数百次 | 大量 | 后台长任务 |

**关键约束**：实时路径（写入 + 召回）不能阻塞用户交互。所有反思任务异步后台执行。

### 11.4 数据库选型建议

* 节点表 + 边表：关系型（PostgreSQL）+ pgvector 扩展，或专用向量库（Qdrant / Milvus）
* 派生边表：纯关系型即可，不需要向量
* **不推荐**图数据库（Neo4j 等）：我们的"图"语义薄，主要是赫布边 + 派生边两种简单关系，关系型 + 向量库的组合更合适

---

## 12. 健康度仪表盘 / Health Dashboard

为缓解"赫布主体不可调试"的风险（METHODOLOGY 第 8 节风险 1），不监测单个错误，但监测整体健康：

### 12.1 监测指标

```
节点池规模:
  - 各 type 各 layer 的节点总数
  - 增长率 / 衰减率
  - 自然平衡线（增长 ≈ 衰减）

权重分布:
  - 各 type 节点的 weight 直方图
  - 是否健康（应近似指数分布，不应集中在某个值）

边密度:
  - 各 type 的平均度数
  - 是否过密（无意义连接太多）或过疏（孤立节点太多）

反思产出:
  - 各反思任务的产出节点数
  - 派生边数
  - 趋势（是否在增长 / 萎缩）

召回质量:
  - 三通道各自的召回命中率（被使用 vs 被丢弃）
  - LLM 评估调整后的 weight 变化分布
```

### 12.2 异常告警（不阻塞运行，仅提示）

```
- 节点池暴涨/暴跌 (>30%/天)
- 某 type 节点完全无新增 (反思任务卡死)
- 派生边数增长但召回命中率下降 (反思产出无效)
- weight 分布退化 (大量节点挤在某个值)
```

---

## 13. 未决问题 / Open Questions

以下是初稿中**尚未完全确定**的设计点，标记出来供后续讨论：

### Q1. trigger_state_vec 的具体构造

Procedural 节点的 trigger_state_vec 应该如何由"情境特征 + 当下情绪 + 主题"组合？
* 选项 A：拼接（concat）
* 选项 B：加权和
* 选项 C：单独学一个组合函数（但增加复杂度）

**初步建议**：A（最简单），后续根据效果调整。

### Q2. 反思的 LLM 调用怎么设计

反思任务（提炼 semantic / procedural / general_event / lifetime_period）需要 LLM 来做归纳。
* 用同一个能力槽位还是分开？
* prompt 模板是否在 a3 文档中定义？

**初步建议**：每种反思任务一个能力槽位 + 一个 prompt 模板，在 a3 文档中定义。

### Q3. self-defining memory 的判定阈值

```python
emotion_intensity > 0.8
access_count 前 5%
度数前 5%
跨越多个 L1
```

这些阈值是初步建议，需要在实际运行中校准。

### Q4. 多模态 content 的存储（已定初版方案）

content 字段为 Any。存储原则：**数据库存引用与元数据，文件本体放外部存储**。

* 小文本：直接存数据库
* 图像 / 音频 / 视频原件：放对象存储或本地文件系统，数据库只存 URI + 元数据 + embedding + 可检索的文本表示（摘要/转写）
* 不把大块原件 base64 塞进主表字段（体积膨胀约 33%，行变重、查询变脏）

多模态混合事件用 JSON 描述各 part，例：

```json
{
  "type": "multimodal_event",
  "parts": [
    { "modality": "text",  "text": "今天和用户一起看了一张猫的照片" },
    { "modality": "image", "uri": "file:///data/cairn/assets/2026/03/cat.png",
      "mime": "image/png", "width": 1024, "height": 768 }
  ]
}
```

具体目录结构 / 对象存储选型在执行域文档中细化，但上述引用式存储是已定的初版方向。

### Q5. 边的物理存储是否真要分三张表

理论上一张表 + type 字段也能实现"分通道检索"。分三张表的好处：
* 检索时不需要 WHERE type=X，直接查对应表
* 索引规模小
* 各表可以独立优化

代价：
* 跨表查询稍麻烦（但派生边已经独立成表，不影响）

**初步建议**：分三张表。代价小，好处明显。

### Q6. derivation_edges 的弱化版本？

是否所有反思产出都要建 derivation_edge？还是只对"高 confidence"的派生才建？
* 建议：全建。元数据级，存储成本低，但级联更新和追溯都需要它。

---

## 14. 历史 / 演进路径

### v1-draft-2（本文档，2025-XX-XX）

基于 v1-draft-1 + 一轮审阅修订，收口当前版本。

**本轮修订要点**：
* procedural 定义从"会话风格倾向"扩展为更一般的行为模式（含决策倾向、工具偏好、活动中沉淀的操作模式）
* 明确工具使用片段及其结果作为经历的一部分进入 episodic，不走特殊写入流程
* 明确反思任务仅在 Cairn 运行期间调度，停机即停
* 去掉"只评估边界态"的说法，改为所有召回节点都由 LLM 做相关性/有效性软评估
* 删除已过时的"与 METHODOLOGY 第 6 节冲突说明"
* 多模态存储从开放问题收束为初版方案：数据库存引用与元数据，原件放外部存储

### v1-draft-1（2025-XX-XX）

基于 memory.md 原稿 + METHODOLOGY 方法论 + 与 architect 的多轮讨论，形成完整记忆系统设计。

**与原 memory.md 相比的主要变化**：
* ✅ 完整保留：节点多维向量、赫布边、weight 衰减、emotion_intensity 公式、反思机制
* ➕ 新增：三类型分表（episodic / semantic / procedural）
* ➕ 新增：episodic 系三层结构（L1 lifetime / L2 general / L3 specific）
* ➕ 新增：self-defining memory 标记
* ➕ 新增：闪光灯记忆机制
* ➕ 新增：派生边（derivation_edges）
* ➕ 新增：mood-congruent recall（用 Cairn 当前心境）
* ➕ 新增：CLS 双层（海马/新皮层）的隐式实现（通过 layer + 衰减率差异）
* ➕ 新增：Procedural 走 state-based 索引（trigger_state_vec）
* ❌ 拒绝：边类型化
* ❌ 拒绝：实体抽取与共指消解
* ❌ 拒绝：NLI 反驳判定 + evidence 计数

**与 METHODOLOGY 的对齐**：完全对齐（第 9 节对账表）。

### 后续版本预期

* v1-draft-2：根据你的审阅意见修订
* v1：定稿，进入实现阶段
* v2：实际运行后根据健康度仪表盘数据校准（阈值、衰减率、组合权重等）

---

## 15. 一句话总结

> Cairn 的记忆不是数据库，是**有结构地分层、按类型分通道、靠赫布连接、随情绪与时间起伏、可在反思中自我提炼**的网络。它让 Cairn 三年后仍能想起两年前的某句话——不是因为存了，是因为那段经历真的塑造了它。
