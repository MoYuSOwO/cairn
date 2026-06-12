# Cairn → Java 迁移计划

## Context

Python 版 M0-M5 全部完成（242 tests），设计文档齐全。迁移动因：AI + Python 长期维护成本高，Java 编译器能拦住大量低级错误，用户看得懂改得动。

核心事实：
- pydantic-ai 替我们做了 Agent 循环、流式解析、tool call 调度、消息序列化
- Java 21 虚拟线程可以替代 async/await（写法同步，性能不吃亏）
- 现有 242 个测试是最精确的 spec，迁移不是重写，是 port

## 项目路径

Java 项目建在 `E:\code\cairn-java\`，与 Python 项目平级，两边不互相依赖。

---

## Phase A: 核心骨架（先跑通一行对话）

目标：`java -jar cairn.jar "你好"` 调 LLM、流式打印回复、正常退出。

### A.0 构建配置

**Gradle**（比 Maven 简洁，Kotlin DSL 类型安全）：

`settings.gradle.kts`:
```kotlin
rootProject.name = "cairn"
```

`build.gradle.kts`:
```kotlin
plugins {
    application
    id("com.diffplug.spotless") version "7.0.0"  // 自动格式化
}

application.mainClass = "com.cairn.Cairn"

java {
    toolchain.languageVersion = JavaLanguageVersion.of(21)
}

dependencies {
    implementation("com.squareup.okhttp3:okhttp:4.12.0")
    implementation("com.squareup.okhttp3:okhttp-sse:4.12.0")
    implementation("com.fasterxml.jackson.core:jackson-databind:2.18.0")
    implementation("com.fasterxml.jackson.module:jackson-module-kotlin:2.18.0") // record 支持

    testImplementation("org.junit.jupiter:junit-jupiter:5.11.0")
    testImplementation("org.assertj:assertj-core:3.26.0")
}

tasks.jar {
    manifest.attributes["Main-Class"] = "com.cairn.Cairn"
    duplicatesStrategy = DuplicatesStrategy.EXCLUDE
    from(configurations.runtimeClasspath.get().map { if (it.isDirectory) it else zipTree(it) })
}
```

目录结构：
```
cairn-java/
├── settings.gradle.kts
├── build.gradle.kts
├── src/main/java/com/cairn/
│   ├── Cairn.java                  # main() — 入口
│   ├── core/
│   │   ├── Config.java             # record: provider, model, apiKey, baseUrl
│   │   ├── ChatMessage.java        # sealed interface → ModelRequest, ModelResponse
│   │   ├── ChatPart.java           # sealed interface → 6 种 part
│   │   ├── Agent.java              # 核心循环
│   │   └── AgentResult.java        # record: output, messages, usage
│   └── client/
│       ├── LlmClient.java          # 接口
│       └── OpenAiClient.java       # OkHttp + SSE 实现
├── src/test/java/com/cairn/
│   └── core/
│       └── AgentTest.java
└── cairn-config.json               # ~/.cairn/config.json 的 Java 版
```

### A.1 消息模型（9 个文件，全部 record）

Java 21 的 `sealed interface` + `record` 完美替代 Python dataclass：

```java
// ChatMessage.java
public sealed interface ChatMessage permits ModelRequest, ModelResponse {
    List<? extends ChatPart> parts();
}

// ModelRequest.java
public record ModelRequest(List<? extends ChatPart> parts) implements ChatMessage {
    public ModelRequest(ChatPart... parts) { this(List.of(parts)); }
}

// ModelResponse.java
public record ModelResponse(List<? extends ChatPart> parts) implements ChatMessage {
    public ModelResponse(ChatPart... parts) { this(List.of(parts)); }
}

// ChatPart.java
public sealed interface ChatPart
    permits TextPart, ToolCallPart, ToolReturnPart,
            UserPromptPart, SystemPromptPart, RetryPromptPart {}

// TextPart.java
public record TextPart(String content) implements ChatPart {}

// ToolCallPart.java
public record ToolCallPart(String toolCallId, String toolName, String args) implements ChatPart {}

// ToolReturnPart.java
public record ToolReturnPart(String toolCallId, String toolName, Object content) implements ChatPart {}

// UserPromptPart.java
public record UserPromptPart(String content) implements ChatPart {}

// SystemPromptPart.java
public record SystemPromptPart(String content) implements ChatPart {}

// RetryPromptPart.java  
public record RetryPromptPart(String toolCallId, String content) implements ChatPart {}
```

### A.2 配置模型

```java
// Config.java
public record Config(
    String provider,      // "openai"
    String model,         // "mimo-v2.5"
    String apiKey,
    String baseUrl        // "https://api.xiaomi.com/v1"
) {
    public static Config fromFile(Path path) throws IOException {
        var mapper = new ObjectMapper();
        return mapper.readValue(path.toFile(), Config.class);
    }
}
```

### A.3 HTTP Client + SSE 解析（最关键的 ~150 行）

```java
// LlmClient.java — 接口
public interface LlmClient {
    StreamedResponse chatStream(List<ChatMessage> messages, String systemPrompt);
    StreamedResponse chatStream(List<ChatMessage> messages); // 不带 system prompt
}

// StreamedResponse.java — 流式结果的迭代器式封装
public class StreamedResponse implements AutoCloseable {
    private final Response response;           // OkHttp Response
    private final BufferedReader reader;       // 逐行读 body
    private String finishedReason = null;      // null=进行中, "stop"=正常结束
    private final StringBuilder content = new StringBuilder();  // 累积文本
    private final List<ToolCall> toolCalls = new ArrayList<>();  // 累积 tool call
    private String currentToolId = null;
    private String currentToolName = null;
    private StringBuilder currentToolArgs = null;

    // 迭代方法：每次调用返回下一个 delta 事件
    public DeltaEvent nextEvent() throws IOException {
        String line;
        while ((line = reader.readLine()) != null) {
            if (line.isEmpty()) continue;
            if (!line.startsWith("data: ")) continue;
            String data = line.substring(6);
            if ("[DONE]".equals(data)) {
                finishedReason = "stop";
                return new DoneEvent();
            }
            return parseDelta(data);  // Jackson 解析 JSON → TextDelta 或 ToolCallDelta
        }
        return new DoneEvent();
    }

    // ... parseDelta() 内部处理 choices[0].delta.content / tool_calls
}

// OpenAiClient.java — OkHttp 实现
public class OpenAiClient implements LlmClient {
    private final OkHttpClient http;
    private final Config config;
    private final ObjectMapper json;

    public StreamedResponse chatStream(List<ChatMessage> messages, String systemPrompt) {
        var body = buildRequestBody(messages, systemPrompt, stream: true);
        var request = new Request.Builder()
            .url(config.baseUrl() + "/chat/completions")
            .header("Authorization", "Bearer " + config.apiKey())
            .post(RequestBody.create(json.writeValueAsString(body), MediaType.parse("application/json")))
            .build();
        return new StreamedResponse(http.newCall(request).execute());
    }
}
```

SSE 解析细节——OpenAI 兼容 API 的流式 JSON 格式：

```json
// 文本 delta
{"choices":[{"delta":{"content":"你好"},"index":0}],"model":"mimo"}

// tool call 开始  
{"choices":[{"delta":{"tool_calls":[{"index":0,"id":"call_1","type":"function","function":{"name":"read_file","arguments":""}}]}}]}

// tool call 参数片段
{"choices":[{"delta":{"tool_calls":[{"index":0,"function":{"arguments":"{\"file"}}]}}]}

// 结束
{"choices":[{"finish_reason":"stop"}]}
```

解析器状态机：正常 → 收到 tool_calls[0].id → 进入 tool_call 模式 → 累积 arguments → finish_reason → done。

### A.4 Agent 核心循环（~200 行）

```java
// Agent.java
public class Agent {
    private final LlmClient client;
    private final Config config;
    private final List<ChatMessage> history = new ArrayList<>();
    private String systemPrompt = "";

    public Agent(Config config) {
        this.config = config;
        this.client = new OpenAiClient(config);
    }

    public Agent systemPrompt(String prompt) { this.systemPrompt = prompt; return this; }

    // 非流式：等待完成，返回 AgentResult
    public AgentResult run(String userInput) {
        return run(userInput, null);
    }

    // 带回调的流式：每收到 delta 就调 callback
    public AgentResult run(String userInput, Consumer<String> onDelta) {
        history.add(new ModelRequest(new UserPromptPart(userInput)));

        while (true) {
            var response = client.chatStream(history, systemPrompt);
            try (response) {
                DeltaEvent event;
                var output = new StringBuilder();
                var toolCalls = new ArrayList<SerializedToolCall>();

                while (!(event = response.nextEvent()).isDone()) {
                    switch (event) {
                        case TextDelta d -> {
                            output.append(d.content());
                            if (onDelta != null) onDelta.accept(d.content());
                        }
                        case ToolCallStart t -> { /* 记录 tool call */ }
                        case ToolCallDelta t -> { /* 累积 tool args */ }
                    }
                }

                if (toolCalls.isEmpty()) {
                    // 纯文本回复 — 完成
                    history.add(new ModelResponse(new TextPart(output.toString())));
                    return new AgentResult(output.toString(), List.copyOf(history));
                }

                // 有 tool call — 执行工具，将结果追加到 history，继续循环
                history.add(buildToolCallResponse(toolCalls));  // ModelResponse with ToolCallPart
                for (var tc : toolCalls) {
                    var result = dispatchTool(tc);  // 执行工具
                    history.add(new ModelRequest(new ToolReturnPart(
                        tc.id(), tc.name(), result
                    )));
                }
            }
        }
    }

    // 消息历史访问
    public List<ChatMessage> history() { return List.copyOf(history); }
    public void clearHistory() { history.clear(); }
}

// SerializedToolCall.java — SSE 解析出的 tool call 累积结果
record SerializedToolCall(String id, String name, String arguments) {}

// AgentResult.java
public record AgentResult(
    String output,
    List<ChatMessage> messages
) {}
```

### A.5 main() 入口

```java
// Cairn.java
public class Cairn {
    public static void main(String[] args) throws Exception {
        var config = Config.fromFile(Path.of("cairn-config.json"));
        var agent = new Agent(config)
            .systemPrompt("你是 Cairn，一个 AI 伴侣。用中文回复。");

        if (args.length > 0) {
            // 单次对话模式
            var result = agent.run(args[0], System.out::print);
            System.out.println();  // 换行
        } else {
            // 交互模式（简单 REPL）
            try (var scanner = new Scanner(System.in)) {
                while (true) {
                    System.out.print("> ");
                    var input = scanner.nextLine();
                    if ("exit".equals(input)) break;
                    agent.run(input, System.out::print);
                    System.out.println();
                }
            }
        }
    }
}
```

### A.6 配置文件

`cairn-config.json`（放在项目根目录，.gitignore 忽略）：
```json
{
    "provider": "openai",
    "model": "mimo-v2.5",
    "apiKey": "sk-xxx",
    "baseUrl": "https://api.xiaomi.com/v1"
}
```

---

## Phase A 实现步骤（精确顺序）

1. `gradle init` → `settings.gradle.kts` + `build.gradle.kts`
2. `Config.java` — 一个 record，能读 JSON
3. 消息模型 9 个 record — `ChatMessage`, `ChatPart` 及其实现
4. `AgentResult.java` — 一个 record
5. `OpenAiClient.java` — OkHttp + SSE 解析，先写死不流式（`stream: false`），跑通再开流式
6. `Agent.java` — 核心循环，先不含 tool call
7. `Cairn.java` — main() 入口
8. `AgentTest.java` — 单轮对话测试（需要 API key 或 mock server）
9. `./gradlew build` → `java -jar cairn.jar "你好"` → 看到回复

---

## Phase A 测试策略

Phase A 只有 1 个集成测试：`AgentTest`。
- 需要有效的 `cairn-config.json` 或环境变量
- 测试 `agent.run("1+1=?")` 返回非空 output
- 不测流式（那依赖具体 LLM 的流式实现）

如果需要离线测试，用一个简单的 HTTP mock server（OkHttp MockWebServer）：
```java
var mockServer = new MockWebServer();
mockServer.enqueue(new MockResponse()
    .setBody("{\"choices\":[{\"message\":{\"content\":\"2\"}}]}")
    .setHeader("Content-Type", "application/json"));
```

---

## Phase B 概述（详细设计在 Phase A 完成后写）

| 顺序 | 模块 | 对应 Python | 文件数 | 测试数 |
|------|------|------------|--------|--------|
| B1 | 消息持久化 | `core/persistence.py` | 1 | 5 |
| B2 | 记忆数据层 | `memory/*` | 5 | 44 |
| B3 | 嵌入与召回 | `memory/embedder.py` + `vector.py` + `recall.py` | 3 | 20 |
| B4 | Compact | `compact/*` | 2 | 53 |
| B5 | Prompts | `prompts/*` | 2 | 12 |
| B6 | 装配流水线 | `core/assembly.py` | 1 | 29 |
| B7 | 写回筛选 | `core/writeback.py` | 1 | 25 |
| B8 | 反思调度 | `reflection/*` | 2 | 23 |
| B9 | 工具系统 | `tools/*` | 5 | 待定 |
| B10 | TUI/Server | `tui/*` + `server.py` | 多 | 待定 |

---

## 不做的事（明确排除，但在不麻烦的情况下预留拓展点）

- 先不做 Anthropic 特有格式适配——只走 OpenAI 兼容 API
- 暂时不做 MCP 工具加载——工具硬编码注册
- 先不做 Textual TUI——Phase A 是命令行，后续模仿 python 做前后端分离模式
- 不做子 Agent——那依赖工具系统稳定后再说
- 不做 Anthropic prompt cache——那是优化，不是功能
