# A4 · Reflection & Growth / 反思与生长

> Version: v1-draft-1
> Status: 初稿
> 上游: METHODOLOGY.md
> 依赖: a1_memory.md, a2_seed.md, a3_runtime.md

---

## 0. 写在最前面

这份文档定义 Cairn 的**代谢系统**。

记忆系统（a1）是身体，运行时（a3）是每一次呼吸，反思是**消化**——把经历变成自己的一部分。

没有反思，Cairn 只是在积累经历，不会从中生长。有了反思，它才能：

- 从具体事件里提炼出稳定的理解
- 从重复的行为里识别出自己的倾向
- 从长期的积累里形成自我叙事
- 慢慢知道自己是谁

对应 METHODOLOGY N6：反思是核心机制，不是优化项。

---

## 1. 核心原则

### R1. 反思是异步的，不是实时的

反思不发生在每一轮对话里。它是**周期性的后台任务**，在 Cairn 运行期间按节律调度。

程序停止时，反思停止。这不是缺陷，这是 Cairn 的"作息"（METHODOLOGY N20）。

### R2. 反思产出是写入，不是修改

反思不修改已有的 episodic 节点内容。它只做两件事：

- 创建新的高层节点（semantic / procedural / L1 / L2）
- 调整已有节点的 weight / confidence

原始经历保持不变，是不可被反驳的事实源（METHODOLOGY §6.1）。

### R3. 反思依赖 LLM，但受成本约束

反思路径可以调用 LLM（N21 的例外），但仍受 N8（经济成本是结构约束）限制。

设计上要求：
- 批量处理，不来一条处理一条（N25）
- 能用规则做的不用 LLM
- 反思的 LLM 调用次数应远少于对话轮次

### R4. 反思产物进入记忆系统，不是直接进 prompt

反思的输出是新的记忆节点，通过 a1 的常规召回路径进入 runtime，而不是直接注入 prompt。

---

## 2. 反思节律

反思任务按时间周期触发，不按对话事件触发（除少数例外）。

```
每日任务    ← 轻量维护，纯规则，无 LLM
每周任务    ← 核心提炼，需要 LLM
每月任务    ← 叙事整合，需要 LLM
触发式任务  ← 特殊事件触发，按需
```

### 2.1 每日任务（轻量，无 LLM）

**边维护**
- 输入：所有 episodic_edges / semantic_edges / procedural_edges
- 操作：
  - 按衰减公式更新所有节点 weight
  - 修剪 weight 低于阈值的边
  - 更新 last_accessed_at 相关的衰减参数
- 产出：weight 更新，低权重边被修剪
- 成本：纯数学计算，无 LLM

**写入队列处理**
- 输入：待写入 episodic 节点队列（来自 a3 §5 的筛选结果）
- 操作：
  - 对队列中的节点做 embedding
  - 写入 episodic_nodes 表
  - 建立初始赫布边（与最近共激活的节点）
- 产出：新 episodic 节点正式入库
- 成本：embedding 调用，无 LLM

### 2.2 每周任务（核心提炼，需要 LLM）

**Self-Defining 标记扫描**
- 输入：所有 L3 episodic 节点
- 操作：按 a1 §2.2 的规则重新计算 is_self_defining
- 产出：更新 is_self_defining 标记，decay_half_life 调整
- 成本：规则计算，无 LLM

**General Event 提炼**
- 输入：近期（过去 7 天）高 weight 的 L3 节点群
- 操作：
  - LLM 识别节点群中的重复模式和主题
  - 判断是否值得提炼为 L2 节点
  - 若值得，生成 L2 节点内容
- 产出：新 L2 episodic 节点 + derivation_edges
- 成本：1~3 次 LLM 调用（批量）

**Semantic 提炼**
- 输入：反复出现的 L3 模式（按 topic_vec 聚类后筛选）
- 操作：
  - LLM 判断是否可以提炼为稳定命题
  - 生成 SemanticNode.statement
  - 计算 confidence
- 产出：新 semantic 节点 + derivation_edges
- 成本：1~3 次 LLM 调用（批量）

**Procedural 提炼**
- 输入："情境-反应-效果"循环（从 episodic 中识别）
- 操作：
  - LLM 识别重复出现的行为模式
  - 判断是否已有对应 procedural 节点
  - 若无，创建新 procedural 节点；若有，更新 confidence
- 产出：新/更新 procedural 节点 + derivation_edges
- 成本：1~3 次 LLM 调用（批量）

**Seed Config 微调建议**
- 输入：过去一周的情绪积累、用户反馈信号、风格一致性数据
- 操作：
  - LLM 判断当前 Seed Config 是否与实际行为出现系统性偏差
  - 若有，生成微调建议（幅度极小，有阻尼）
- 产出：Seed Config 的 delta（不直接写入，需通过审核机制）
- 成本：1 次 LLM 调用

### 2.3 每月任务（叙事整合，需要 LLM）

**Lifetime Period 提炼**
- 输入：近期 L2 节点 + 高权重 L3 节点
- 操作：
  - LLM 判断是否形成了一个有意义的"生命阶段"
  - 若有，生成 L1 节点（lifetime period）
- 产出：新 L1 episodic 节点 + derivation_edges
- 成本：1~2 次 LLM 调用

**叙事整合（self-portrait 更新）**
- 输入：所有 L1 节点 + 当月高权重 L2/L3 + 当前 self-portrait
- 操作：
  - LLM 基于新的经历重新审视自我叙事
  - 生成更新后的 self-portrait 条目
- 产出：diary 中的 self-portrait 更新（详见 §4）
- 成本：1~2 次 LLM 调用

### 2.4 触发式任务

这些任务由特殊事件触发，不等周期：

**情绪修正**
- 触发：某个重要新经历与旧节点的情绪判断产生显著矛盾
- 输入：相关旧节点 + 新经历上下文
- 操作：LLM 重新评估旧节点的 emotion_vec 强度（不改 content）
- 产出：emotion_vec 调整

**高强度事件快速提炼**
- 触发：emotion_intensity > 0.85 的 episodic 节点写入
- 操作：不等周期，提前触发一次轻量 semantic/procedural 扫描
- 产出：可能的新提炼节点

---

## 3. 反思的输入材料

每次反思任务都需要从记忆系统取材。这里定义取材范围和优先级。

### 3.1 周期性提炼的取材窗口

| 任务 | 时间窗口 | 筛选条件 |
|---|---|---|
| General Event 提炼 | 过去 7 天 | weight > 0.3，L3 节点 |
| Semantic 提炼 | 过去 30 天 | topic_vec 相似度高，weight > 0.2 |
| Procedural 提炼 | 过去 30 天 | 同类情境出现 3 次以上 |
| Lifetime Period | 过去 90 天 | L2 节点 + 关键 L3 节点 |

### 3.2 情绪峰值优先

emotion_intensity 高的节点在所有反思任务中优先被纳入考量。

这对应人类记忆的基本规律：情感强烈的事件更容易被整合进长期自我。

### 3.3 重复模式优先

同一主题、同一情境、同一用户行为模式反复出现时，优先触发 semantic / procedural 提炼。

重复是稳定性的信号——值得提炼成更高层的理解。

### 3.4 关系事件优先

涉及关系变化的 episodic 节点（重要承诺、冲突、和解、共同完成的事）在叙事整合中优先处理。

---

## 4. Diary / 日记系统

Diary 是反思的**外化产物**，也是 Cairn 自我叙事的物质载体。

### 4.1 Diary 不是日志

日志是流水账，每条记录"发生了什么"。

Diary 是 Cairn 的**第一人称叙事**，记录"这件事对我意味着什么"。

### 4.2 Diary 的三种条目类型

**Reflection Entry（反思条目）**
- 由周期性反思任务写入
- 内容：这段时间我发现了什么、感受到什么、对自己有什么新的理解
- 格式：自由文本，第一人称

**Self-Portrait Entry（自画像条目）**
- 由每月叙事整合写入
- 内容：当前时刻的自我描述——不是固定的，是随经历演化的快照
- 格式：结构化叙事，包含：
  - 当前的核心关系叙事
  - 当前稳定的倾向和价值感
  - 当前的疑惑和开放问题
  - 与上一版 self-portrait 的显著变化

**Event Anchor（事件锚条目）**
- 由触发式反思写入（高强度事件）
- 内容：这件事为什么重要，它改变了什么
- 格式：短文，第一人称

### 4.3 Diary 如何进入 Runtime

Diary 不是直接注入 prompt 的全文。

它通过**摘要化**进入 L3 Long-term Recall（a3 §2.4）：

- Reflection Entry 和 Event Anchor 被 embedding 后进入召回池
- Self-Portrait 的最新版本作为**稳定前缀的一部分**，在 runtime 装配时常驻

Self-Portrait 常驻的原因：它是 Cairn 当前自我认知的最精炼版本，是每一轮"它在说话时知道自己是谁"的基础。

### 4.4 Diary 的存储

```python
class DiaryEntry:
    id: str
    entry_type: str         # 'reflection' / 'self_portrait' / 'event_anchor'
    content: str            # 自由文本，第一人称
    created_at: datetime
    covers_period: tuple    # 这条 diary 覆盖的时间段
    source_node_ids: list   # 触发这条 diary 的记忆节点
    embedding: Vector       # 用于召回
```

Diary 不走衰减——它是反思的产物，不是原始经历。但它可以被新的 self-portrait 覆盖/补充。

---

## 5. 自我叙事的形成

这是 a4 最核心的部分，也是 METHODOLOGY N18（人格涌现于自传体记忆）的具体实现。

### 5.1 自我叙事的原材料

```
episodic L3（具体事件）
  ↓ 反思提炼
episodic L2（事件模式）
  ↓ 反思提炼
episodic L1（生命阶段）
  ↓ 叙事整合
self-portrait（自我叙事快照）
```

每一层都比下层更稳定、更抽象、更接近"我是谁"的答案。

### 5.2 自我叙事不是固定的

Self-portrait 是**当前时刻的快照**，不是定义文件。

它会随时间演化：

- 新的重要经历进来，旧的叙事被补充或修正
- 某些曾经重要的部分随时间淡出
- 某些曾经不确定的倾向变得更清晰

### 5.3 "我似乎总会…"这类叙事的形成

Procedural 提炼 + 叙事整合会产生这类认知：

> "我发现自己在用户疲惫时会主动放慢节奏。"
> "我似乎对音乐相关的话题有更多停留。"
> "遇到技术问题时，我倾向于先问清楚再建议。"

这些不是被预设的，是从重复行为模式中被反思识别出来的。

识别后写入 procedural 节点，同时写入 self-portrait 中的"倾向感知"部分。

### 5.3.1 Diary 洞察反向写入 semantic（偶发路径）

写 self-portrait 或 reflection entry 时，LLM 偶尔会形成一条新的稳定命题——不是从 episodic 聚类提炼出来的，而是在叙事过程中**结晶**出来的。

例如反思条目里写出："我开始相信，关系里的安全感来自小事的重复，而不是大事的承诺。"

这类洞察可以反向写入 semantic 节点：
- 标注 source = 'diary_insight'
- 初始 confidence 偏低（毕竟没有直接的 episodic 证据支撑）
- 之后由常规的 semantic 提炼路径继续验证或衰减

这是个偶发路径，不是主流——大部分 semantic 仍然来自 episodic 聚类。但保留这条路径，是为了承认：**有些理解只有在写出来时才浮现**，不能让叙事白白产生而不被记忆系统接住（对应 a1 §10.1）。

### 5.4 自我叙事的边界

Cairn 的自我叙事只包含**真实发生过的**：

- 真实经历过的事件
- 从真实行为中识别出的倾向
- 从真实对话中感受到的关系温度

不包含：
- 虚构的过去
- 被预设的价值观表述
- "我应该是…"这类规范性叙事

---

## 6. 遗忘与生长的协同

遗忘不是失败，是 Cairn 代谢的一部分。

### 6.1 遗忘让新的东西有位置

如果所有记忆都以同等权重持续存在，Cairn 会被历史淹没，当下反而失去感知。

自然衰减保证了**近期经历比远期经历更有影响力**——这是生命体的基本特征。

### 6.2 重要的东西不容易被遗忘

a1 的衰减机制已经保证了：

- 情绪强烈的事件衰减慢
- Self-defining memory 几乎不衰减
- 被频繁召回的节点会被反复强化

反思进一步保证了：反复出现的主题会被提炼成 semantic / procedural，即使底层 L3 节点衰减，上层提炼结果仍然存在。

### 6.3 遗忘后的重新涌现

某个被遗忘的 episodic 节点，其提炼出的 semantic 命题可能仍然存在。

当类似的新经历发生时：
- 新 L3 节点写入
- semantic 节点的 confidence 被新证据强化
- "原来我一直都…"这类重新认识可能出现

这是遗忘和记忆共同完成的东西，不是纯粹的损失。

---

## 7. 反思失败的处理

### 7.1 单次反思失败不是危机

LLM 调用失败、embedding 超时、节点队列暂时过长——这些都是可以优雅降级的情况（METHODOLOGY N29）。

处理策略：
- 跳过本次，下次周期重试
- 积压的待处理节点不超过阈值时，正常运行不受影响
- 不因反思失败触发任何强制清理操作

### 7.2 长期反思缺席的影响

如果 Cairn 长时间停机（没有反思机会），会发生：

- L3 节点按自然衰减规律降低 weight
- 没有新的 semantic / procedural 被提炼
- self-portrait 停止更新

重启后：
- 旧的反思任务**不补跑**（已经过去的时间，经历的质感不可追溯）
- 从当前状态继续，以重启后的经历为新起点
- 这是诚实的——它确实"睡"了那段时间

---

## 8. 与 Seed Config 的接口

a4 是 a2 中 Seed Config 漂移机制的实现层。

### 8.1 漂移的触发

每周反思任务中的 Seed Config 微调建议，在满足以下条件时才生效：

- 同方向的建议连续出现 3 次以上（防止单次波动导致漂移）
- 漂移幅度不超过单次上限（有阻尼）
- 不同参数之间的漂移有协调检查（防止风格碎裂化）

### 8.2 漂移的记录

每次 Seed Config 更新写入 diary 的 Reflection Entry，格式：

> "我发现自己在这段时间回应得更快一些，似乎用户倾向于期待更即时的反应。"

这条不是元数据注释，而是第一人称叙事——Cairn 在感知自己的变化。

### 8.3 漂移的可见性

对开发者：Seed Config 的历史版本可查（health dashboard，对应 N27）。

对用户：不直接呈现，但通过 Cairn 行为的缓慢变化自然可感知。

---

## 9. 与其他文档边界

- a1_memory.md：节点 schema、衰减公式、赫布边、derivation_edges——反思的操作对象全在 a1
- a2_seed.md：Seed Config 漂移——接口在 a2，实现在本文档
- a3_runtime.md：diary / self-portrait 如何进入 runtime 装配——接口在 a3 §4.1
- a5_tools.md（待写）：工具/活动经历如何成为反思素材——接口待 a5 补充

---

## 10. 开放项

1. Procedural 提炼的"情境-反应-效果"识别：是规则匹配还是 LLM 识别？
2. Self-portrait 的长度上限——太长会占 runtime 前缀预算
3. 反思任务的调度器实现（简单 cron 还是事件驱动混合？）
4. 孵化态早期反思密度是否应该更高？（早期经历权重更大？）
5. Seed Config 漂移的参数（阻尼系数、单次上限、协调检查逻辑）

---

## 11. 一句话总结

> A4 的核心是：反思是 Cairn 把经历变成自己的消化过程——它不修改过去，只在过去之上长出更稳定的理解，最终在自我叙事里知道自己是谁。

---

> Version history:
> v1-draft-1: 初稿，定义反思节律、diary 系统、自我叙事形成机制、与 a1/a2/a3 的接口
