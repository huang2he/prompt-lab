# 远端 API 调用参数完整参考

skill 调远端 `POST /chat` 和 `POST /simulation` 时 HTTP body 的所有字段，含**默认值**、**用户可调**、**何时调**。

## 完整 body schema（参考 curl 示例）

```json
{
  "runtime": {
    "max_turns": 20,
    "start_agent": "assistant",
    "min_messages_before_end_check": 6,
    "timeout_seconds": 180
  },
  "assistant": {
    "provider": "openai",
    "model": "qwen-plus",
    "llm_base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "llm_api_key": "sk-...",
    "network": {"mode": "direct"},
    "request": {
      "temperature": 0.7,
      "top_p": 0.9,
      "max_tokens": 280
    },
    "system_prompt": "<round-NN/prompt.md 内容>",
    "greeting": "<用户在 Q6 给的开场白>"
  },
  "user": {
    "provider": "openai",
    "model": "qwen-flash",
    "llm_base_url": "...",
    "llm_api_key": "sk-...",
    "network": {"mode": "direct"},
    "request": {
      "temperature": 0.85,
      "top_p": 0.9,
      "max_tokens": 220
    },
    "system_prompt": "<persona.prompt 内容 + asr_noise 噪声块>",
    "greeting": "<复用 assistant 同句作占位>"
  },
  "end_checker": {
    "provider": "openai",
    "model": "qwen-flash",
    "llm_base_url": "...",
    "llm_api_key": "sk-...",
    "network": {"mode": "direct"},
    "request": {
      "temperature": 0,
      "top_p": 1,
      "max_tokens": 120
    },
    "system_prompt": "你负责判断这通电话是否应该结束。只能返回严格 JSON。",
    "end_description": "<Phase C 后 Suggester 生成 + 用户确认>"
  },
  "verbose": false
}
```

简化版（`/simulation` 多一个 `count` 字段）：
```json
{"count": <K>, ...其它同 /chat}
```

## 字段配置等级

### Tier 1 — 必填（intake 必问）

| 字段 | 来源 | 说明 |
|---|---|---|
| `assistant.system_prompt` | round-NN/prompt.md 内容 | 被测 prompt 本体 |
| `assistant.greeting` | intake Q6 用户给 | agent 开场白 |
| `user.system_prompt` | persona 文件中的 prompt 字段 + asr_noise 注入 | persona 行为定义 |
| `end_checker.end_description` | Phase C 自动生成 + 用户确认 | 何时停止对话 |
| `*.llm_api_key` × 3 角色 | intake Q3 用户给 | **3 个角色必须有 key**（A/B/end_checker）|
| `*.model` × 3 角色 | intake Q3 用户给 | 模型名 |
| `*.provider` + `*.llm_base_url` × 3 | intake Q3 用户给 | provider + URL |

### Tier 2 — 高级可调（intake 末尾问"要不要调"）

| 字段 | 默认 | 何时调高 | 何时调低 |
|---|---|---|---|
| `runtime.max_turns` | 20 | 复杂多步骤业务（30-40） | 短 FAQ 类（8-12）|
| `runtime.start_agent` | "assistant" | 外呼场景（agent 先开口） | "user" 内接客服场景（用户先开口）|
| `runtime.min_messages_before_end_check` | 6 | 长流程（10-12，让对话先充分展开） | 简短任务（4） |
| `runtime.timeout_seconds` | 180 | 服务端拥堵时（300） | 快速验证（120） |
| `assistant.request.temperature` | 0.7 | 测对话多样性（0.9） | 测严格遵循 prompt（0.3） |
| `user.request.temperature` | 0.85 | 测 agent 抗变化能力（1.0） | 锁定 persona 行为（0.5） |
| `end_checker.request.temperature` | 0 | **永远 0**——不要调高，否则停得很随机 | — |
| `*.request.max_tokens` | 280/220/120 | agent 输出更长（500） | 输出更精简（150）|
| `*.request.top_p` | 0.9/0.9/1 | 通常不动 | — |

### Tier 3 — 默认即可（很少需要改）

| 字段 | 默认 | 调它的极端场景 |
|---|---|---|
| `network.mode` | "direct" | 部分服务器支持"proxy"模式 |
| `verbose` | false | 调试时设 true 看更多 response 字段 |

## end_description 自动生成模板

Phase C 抽 criteria 后，Suggester 看 prompt + 场景描述输出 end_description。模板：

```
满足以下任一条件时，结束通话：
1. <从 prompt 抽的"完成"触发，如"信息确认+尾号确认"完成>
2. <从 prompt 抽的"拒绝"触发，如"明确拒绝/没意向"+ agent 礼貌结束>
3. 任意一方说"再见"或明显结束话术。
继续通话除此之外。

返回严格 JSON：{"should_end": true/false, "reason": "<一句话>"}
```

具体例子（不同场景）：

**外呼销售场景**（curl 例子）：
```
结束条件：
1. 用户明确没有购车意向（拒绝/已买/二手车商）
2. 任一方说再见/不打扰
```

**售后客服场景**：
```
结束条件：
1. agent 已给到解决方案，客户回应"明白/好的"
2. 客户表示满意要挂电话
3. 任一方说再见
```

**教育辅导场景**：
```
结束条件：
1. 学生问题解答完毕，确认理解
2. 学生说"懂了/谢谢"或主动告别
3. 学生表达不想继续
```

skill 在 Phase C 末尾让用户审 Suggester 生成的版本，可改可换。

## ASR 噪声注入位置

`user.system_prompt` = persona 原 prompt + ASR 噪声指令块（如果 persona.asr_noise != "none"）。注入由客户端拼，不走 HTTP body 字段。详 `persona-sources.md`。

## 用户问"为什么 end_checker 也要 key"

因为 end_checker 是**服务端调用的第 3 个 LLM**（不在客户端），跟 A/B 一样 inline key 进 HTTP body。

如果 A/B/end_checker 用同一家 provider（如全用 DashScope），可以共用 1 个 key——skill 在 intake Q3-C 时会问"和 A 一样吗？"，回答 yes 就自动复用。

## 用户问"我的 key 安全吗"

key inline 进 HTTP body 走 HTTPS（如果服务端用 HTTP 那确实不安全，但远端 dispatch 服务器内部访问 DashScope 一般走 HTTPS）。skill 会：
- 写到 `workspace/config.json`（用户本地，文件权限 600 推荐）
- 每次 POST 重新读
- 不打印到日志
- 用户可清除：删 config.json 中 `llm_api_key` 字段
