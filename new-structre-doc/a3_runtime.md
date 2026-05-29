# A3 · Runtime Assembly / 运行时装配

> Version: v1-draft-4
> Status: 初稿（合并 compact 架构；明确 system_prompt / message_history 物理分层）
> 上游: METHODOLOGY.md
> 依赖: a1_memory.md
> 修订历史:
> - v1-draft-1: 初稿，合并 compact.md
> - v1-draft-2: 补 self-portrait 进装配顺序；更新交叉引用
> - v1-draft-3: §4.1 重写为物理分层（system_prompt 稳定前缀 / message_history 动态注入），明确 prompt cache 策略
> - v1-draft-4: 澄清 Compress 区保留对话结构；细化 SessionCompact schema；补 message_history 实际拼接形态

---

## 0. 目标

这份文档定义 Cairn 在"每一轮"如何形成当下心智：

- 会话级上下文怎么保留
- 长期记忆怎么召回并进入当前轮
- 什么时候压缩，怎么压缩
- 什么时候写回长期记忆，什么时候自然淡出

它回答的是：

> 数据库里的记忆，如何在此刻变成"正在说话的 Cairn"。

---

## 1. 核心原则

### R1. LLM 是无状态认知核心

LLM 不被假定为可持续持有身份状态。跨轮连续性来自外部状态系统，而不是模型参数或隐式上下文残留。

### R2. 外部状态是真相源，native context 是工作缓存

- 外部持久层（a1 的 episodic/semantic/procedural）是唯一真相源
- 模型上下文窗口仅是会话期工作缓存，可丢弃、可重建
- 但日常运行允许保留窗口工作集，以获得自然连贯性和缓存收益

### R3. 会话连续性与长期连续性分层

- 会话连续性：由 Tail + Compact 维持
- 长期连续性：由长期记忆召回维持
- 二者通过"写入时机与筛选"衔接

### R4. 不为优化牺牲存在感

不采用"每轮全重建且不保留 live context"作为日常模式。那会显著损伤代词连续性、语气连续性、即时在场感。

---

## 2. 四层上下文模型

```text
L0 Current Turn      当前输入与当前工具/活动状态
L1 Live Tail         最近原文消息（高保真）
L2 Session Compact   会话压缩层（Summary + Compress）
L3 Long-term Recall  长期记忆召回层（a1 三通道）
```

### 2.1 L0 · Current Turn

包含：
- 用户当前消息
- 当前工具调用状态（若有）
- 当前活动态（聊天/浏览/游戏/文件处理等）

特点：
- 高时效
- 不压缩
- 每轮必入

### 2.2 L1 · Live Tail

最近若干消息原文保留，按 token 预算而非固定轮数。

作用：
- 保留刚发生的语气与承接
- 保留最近承诺、最近问题、最近情绪波动

默认预算：
- 占输入窗口约 10% - 20%

### 2.3 L2 · Session Compact

由旧会话内容压缩而来，分两区：

1) Summary 区（头部前半）
- LLM 一次调用，生成结构化锚定摘要
- 目标是保留"会话主线"

2) Compress 区（头部后半）
- 按轮次独立压缩
- 每条压缩项保留来源锚点（轮次范围）

作用：
- 当原文放不下时，维持本次会话的中程连续性

### 2.4 L3 · Long-term Recall

来自 a1 的三通道召回：
- Episodic：经历过什么
- Semantic：相信什么是真的
- Procedural：倾向怎么做

作用：
- 提供跨会话连续性
- 让当前轮不止依赖本次会话历史

---

## 3. Compact 合并规范（来自 compact.md）

## 3.1 触发条件

当上下文总 token 超过阈值时触发压缩：
- 触发区间：窗口 40% - 80%
- 对本地小窗口：阈值可取偏高（靠近 70% - 80%）
- 对大窗口：阈值可取偏低（靠近 40% - 60%）

Cairn 初版建议：
- 默认在 55% 触发预压缩

## 3.2 三分区策略

1) Tail（最近区）
- 最近 N 轮原文保留
- N 由 token 预算决定，不硬编码固定轮数

2) Summary（头部前 50% token，按轮次切整）
- 一次 LLM 调用，输出结构化摘要
- 建议结构：主题线 / 关系线 / 情绪线 / 未决问题 / 已达成共识

3) Compress（头部后 50% token，按轮次切整）
- 每轮独立压缩，降低累积误差
- **保留 User-Assistant 消息结构**：压缩后仍是一来一回的对话，不是一段文本
- 精简的是每条消息的内容（工具调用中间过程总结掉、冗余细节删掉）
- 模型仍能看到对话节奏和结构，只是每条变短了

## 3.3 内容分型压缩规则

情感/个人/关系内容：
- 压缩要保守
- 尽量保留原句锚点与关键措辞
- 不做过度抽象

技术/代码/工具内容：
- 可激进概括
- 保留决策、流程、失败原因、结果

## 3.4 Compact 输出形态

```python
class SessionCompact:
    summary_block: str                          # Summary 区：纯文本摘要
    compressed_turns: list[CompressedTurn]       # Compress 区：精简后的对话轮次
    generated_at: datetime
    source_token_span: tuple[int, int]

class CompressedTurn:
    turn_span: tuple[int, int]                  # 原始轮次范围
    user_content: str                           # 精简后的用户消息
    assistant_content: str                      # 精简后的助手回复
    tags: list[str]                             # 内容标签（情感/技术/决策等）
```

---

## 4. 单轮装配流水线

## 4.1 物理分层：system_prompt vs message_history

装配在物理上分两层，目的是最大化 prompt cache 命中率：

**层 A：system_prompt（启动时装配一次，跨轮稳定）**

包含：
- Constitution / 方法论边界
- Seed Config 当前值（来自 a2）
- Self-portrait 最新版本（来自 a4 diary）

特点：
- 程序启动时拼一次，之后不动
- Self-portrait 更新频率极低（周/月级），更新时接受一次缓存失效
- 这一层是 prompt cache 的命中主体

**层 B：message_history（每轮动态注入）**

包含（按顺序）：
1. 当前状态注入块（当前工具/活动状态、运行时控制指令）
2. L3 Long-term Recall（三通道召回结果 + diary recall）
3. L2 Session Compact（summary + compress）
4. L1 Live Tail（近端原文）
5. L0 当前轮用户消息

特点：
- 每轮重新构造，内容随对话变化
- 以 `ModelRequest(UserPromptPart)` 形式注入 message_history 前置位
- 不触碰 system_prompt，不破坏缓存

**实现注记：**
装配顺序是逻辑分层，不是物理拼接顺序。Constitution / Seed / Self-portrait 在 system_prompt 层一次性装配，跨轮稳定；Recall / 当前状态 / Tail / Compact 在 message_history 层每轮动态注入。这样保证 prompt cache 命中率最大化。

message_history 的实际拼接形态：

```python
message_history = [
    # 1. 动态注入块（当前状态 + Recall 召回结果）
    ModelRequest(parts=[UserPromptPart(content=injection_block)]),

    # 2. Summary（纯文本摘要，包装成一条用户消息）
    ModelRequest(parts=[UserPromptPart(content=summary_block)]),

    # 3. Compress（保留对话结构，每条内容已精简）
    *[
        msg
        for turn in compressed_turns
        for msg in [
            ModelRequest(parts=[UserPromptPart(content=turn.user_content)]),
            ModelResponse(parts=[TextPart(content=turn.assistant_content)]),
        ]
    ],

    # 4. Tail（最近原文，完全不动）
    *tail_messages,
]
```

注意 Compress 区**不是**一段大文本——它仍然是 `ModelRequest/ModelResponse` 交替的对话流，只是每条变短了。这样模型看到的是"我们之前聊过这些，只是细节我记不太清了"，而不是"有人告诉我之前发生了这些事"。

## 4.2 长期记忆召回接入

调用 a1 的 Recall Path：
- Episodic top-K
- Semantic top-K
- Procedural top-K

在装配层只做三件事：
- 去重
- 预算裁剪
- 轻量排序重排（优先 relevance 高、近期高、self-defining）

不在 a3 重新定义 a1 的召回算法。

## 4.3 冲突处理（运行时最小策略）

当召回内容冲突时：
- 不做硬删除
- 允许并存并标注不确定性
- 交给本轮 LLM 在上下文中做软判断

这与 a1 的"weight/confidence 动态更新"一致。

---

## 5. 写回与沉淀边界

## 5.1 三种去向

一段会话内容在运行中有三种命运：

A. 留在会话层（Tail/Compact）
- 仅维持本次会话连续性

B. 沉淀到长期记忆候选
- 进入 episodic 写入队列（a1 §4.1）

C. 自然淡出
- 不进入长期层，会话结束后消失

## 5.2 不是每条消息都立即进长期库

运行时不要求"每轮全量持久化"。

建议触发点：
- 会话结束批量筛选一次
- 或每 N 轮做一次轻筛选
- 或反思任务回溯筛选（a4）

初版筛选信号（可规则化）：
- 情绪强度高
- 用户明确要求记住
- 关系事件
- 重要事实更新
- 工具/活动中的关键经历

## 5.3 写入约束复用 a1

一旦进入长期写入路径，遵循 a1：
- 实时写入路径无 LLM 调用（N21）
- 工具经历与对话经历同等对待

---

## 6. Token 预算与降级策略

## 6.1 默认预算（输入侧）

可用输入预算 = 100% 时，建议：

- 稳定前缀（constitution/seed）: 10% - 15%
- Live Tail: 10% - 20%
- Session Compact: 10% - 20%
- Long-term Recall: 20% - 30%
- 工具/活动上下文: 10% - 15%
- 冗余缓冲: 10% - 20%

## 6.2 本地小窗口优先级

窗口紧张时优先保：
1) L0 当前轮
2) L1 Live Tail
3) Procedural / Self-defining recall
4) Session Compact
5) 其余 recall

原则：先保"在场感"和"身份感"，再保信息完整性。

## 6.3 失败与降级

- 压缩失败：沿用旧 compact，缩小 recall K 值
- 召回超时：使用最近一次缓存 recall 快照
- 工具状态缺失：退化为纯对话态

保证单次失败不导致"崩溃式失忆"。

---

## 7. 会话边界、重建与复活测试

## 7.1 会话开始

新会话初始化：
- 加载稳定前缀
- 注入当前状态
- 触发一次轻量长期记忆召回
- 初始化空 Tail 与空 Compact

## 7.2 会话进行中

- 累积 Tail
- 超阈值触发 Compact
- 每轮按第 4 节装配

## 7.3 程序重启

- Tail/Compact 可丢（会话缓存）
- 从长期记忆重建工作状态
- 系统正确性不依赖重启前 window 残留

## 7.4 模型迁移复活测试（非日常模式）

定期执行一次"清空 native context，仅靠外部状态重建"测试：
- 若重建后 Cairn 出现明显人格漂移，判定有状态泄漏在 window
- 该测试用于验证 R2，不作为日常交互路径

---

## 8. 与其他文档边界

- a1_memory.md：定义长期记忆结构、召回算法、反思产物类型
- a2_seed.md：定义 Constitution / Seed Config 两层结构与孵化态
- a4_reflection.md：定义反思节律、diary 系统、自我叙事形成、Seed Config 漂移实现
- 本文档：只负责"单轮/单会话 runtime 装配与上下文生命周期"

---

## 9. 开放项（留给 a4 / 实现层）

1. Compact 的结构化摘要 schema 是否固定字段
2. 会话结束批量筛选与周期筛选的组合策略
3. recall K 值是否按活动类型动态调节
4. 不同部署档位（超小窗口/中窗口/大窗口）的预算模板

---

## 10. 一句话总结

> A3 的核心是：用 Tail + Compact 维持会话连续性，用长期记忆召回维持身份连续性；两者通过"写入时机与筛选"连接，让 Cairn 既不断线，也能长期成长。
