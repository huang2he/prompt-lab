# 远端 API 调用参数完整参考

skill 跟 dispatcher 之间的协议，2026-05 起的版本。

## 端点与用途

| 端点 | 方法 | 用途 | body |
|---|---|---|---|
| `/healthz` | GET | 探活（A.0） | 无 body |
| `/chat` | POST | 单通对话（A.3 探测 + Phase D smoke） | 见下 schema |
| `/simulation` | POST | 批量对话（Phase E 主跑，per persona 一次 count=K） | 比 /chat 多 `count` 顶层字段 |
| `/chat/<id>` | GET | 轮询单通 chat 状态 | 无 body |
| `/simulation/<id>` | GET | 轮询批 simulation 状态 | 无 body（路径或与 /chat/<id> 同享，按 dispatcher 实现） |

**所有请求**都要带 HTTP header：

```
Content-Type: application/json
x-access-token: <Q0-B 的 token>
```

## 完整请求体 schema（/chat）

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
    "network": {"mode": "direct"},        // 国内（DashScope / 智谱 / DeepSeek / Kimi / 自部署）
    "request": {
      "temperature": 0.7,
      "top_p": 0.9,
      "max_tokens": 280
    },
    "system_prompt": "<round-NN/prompt.md 内容>",
    "greeting": "<Q6 用户给的开场白>"
  },
  "user": {
    "provider": "openai",
    "model": "gpt-5-chat-latest",
    "llm_base_url": "https://api.openai.com/v1",
    "llm_api_key": "sk-proj-...",
    "proxy": true,                         // 海外（OpenAI / Anthropic / Gemini 等）
    "request": {
      "temperature": 0.85,
      "top_p": 0.9,
      "max_tokens": 220
    },
    "system_prompt": "<persona.prompt + asr_noise 噪声块>",
    "greeting": "<复用 assistant 同句作占位>"
  },
  "end_checker": {
    "provider": "openai",
    "model": "qwen-flash",
    "llm_base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
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

**/simulation 比 /chat 多一个顶层字段**：

```json
{
  "count": 5,           // 该 persona 跑 5 通
  "runtime": {...},
  "assistant": {...},
  "user": {...},
  "end_checker": {...}
}
```

## 海外/国内判定（决定 `network` vs `proxy`）

每个角色块（assistant / user / end_checker）根据 `llm_base_url` 自动判定，结果直接写进该块。

**海外白名单**（命中 → 加 `"proxy": true`，**不写 `network` 字段**）：

```python
OVERSEAS_DOMAINS = {
    "api.openai.com",
    "api.anthropic.com",
    "generativelanguage.googleapis.com",
    "api.cohere.com",
    "openrouter.ai",
    "api.together.xyz",
    "api.x.ai",
    "api.mistral.ai",
    "api.deepinfra.com",
    "api.fireworks.ai",
}
```

**未命中**（DashScope / 智谱 / DeepSeek / Kimi / 硅基流动 / 自部署 IP / localhost）→ 国内 → 加 `"network": {"mode": "direct"}`（**显式写**，不省略）。

**Helper 在 `scripts/network_mode.py`**：

```python
from urllib.parse import urlparse

def is_overseas(base_url: str) -> bool:
    host = (urlparse(base_url).hostname or "").lower()
    return any(host == d or host.endswith("." + d) for d in OVERSEAS_DOMAINS)

def role_network_block(base_url: str) -> dict:
    """Return the block to merge into the role: {'proxy': True} OR {'network': {'mode': 'direct'}}."""
    return {"proxy": True} if is_overseas(base_url) else {"network": {"mode": "direct"}}
```

## 字段配置等级

### Tier 1 — 必填（intake 必问）

| 字段 | 来源 | 说明 |
|---|---|---|
| HTTP `x-access-token` header | Q0-B | dispatcher 鉴权 |
| `assistant.system_prompt` | round-NN/prompt.md | 被测 prompt |
| `assistant.greeting` | Q6 | agent 开场白 |
| `user.system_prompt` | persona.prompt + ASR 噪声 | persona 行为定义 |
| `end_checker.end_description` | Phase C 生成 + 用户确认 | 何时停止 |
| `*.llm_api_key` × 3 角色 | Q3 | 3 个角色必须有 key |
| `*.model` × 3 | Q3 | 模型名 |
| `*.provider` + `*.llm_base_url` × 3 | Q3 | provider + URL |
| `*.network` 或 `*.proxy` × 3 | 自动判 + 用户确认 | 国内 direct / 海外 proxy |

### Tier 2 — 高级可调

| 字段 | 默认 | 何时调高 | 何时调低 |
|---|---|---|---|
| `runtime.max_turns` | 20 | 复杂多步骤业务（30-40） | 短 FAQ（8-12） |
| `runtime.start_agent` | "assistant" | 外呼场景 | "user" 内接客服 |
| `runtime.min_messages_before_end_check` | 6 | 长流程（10-12） | 简短任务（4） |
| `runtime.timeout_seconds` | 180 | 服务端拥堵（300） | 快速验证（120） |
| `assistant.request.temperature` | 0.7 | 测多样性（0.9） | 测严格遵循（0.3） |
| `user.request.temperature` | 0.85 | 测 agent 抗变化（1.0） | 锁定 persona（0.5） |
| `end_checker.request.temperature` | 0 | **永远 0**——别调 | — |
| `*.request.max_tokens` | 280/220/120 | agent 输出更长（500） | 输出更精简（150） |

### Tier 3 — 默认即可

| 字段 | 默认 | 调它的极端场景 |
|---|---|---|
| `verbose` | false | 调试时 true 看更多 response 字段 |

## GPT-5 / reasoning 模型字段差异

不同模型对 `request` 里的字段有不同要求：

| 模型 | `max_tokens` | `max_completion_tokens` | `enable_thinking` |
|---|---|---|---|
| qwen-plus / qwen-flash / qwen-max | ✓ | — | — |
| qwen3-* / qwen3.6-* （thinking） | ✓ | — | 必须 `false`（否则 thinking 链吃光 token） |
| gpt-4o / gpt-4o-mini / gpt-4.1 | ✓ | — | — |
| gpt-5-chat-latest / gpt-5.1-chat-latest | ✓ | — | — |
| gpt-5 / gpt-5.x (非 chat-latest) | ✗ | ✓ | — |
| gpt-5*-pro / gpt-5.5-pro | **不能走 chat 端点** | — | — |
| claude-opus-4-7 / claude-haiku-4-5 (Anthropic API) | ✓（通过 OpenAI-compat 层）/`max_tokens` 原生 | — | — |

skill 在 Q3 收到 model 字段后，按这张表自动选字段名，并在请求体里写正确的字段。**用户给的 max_tokens 数值**会同时被映射到 `max_tokens` 或 `max_completion_tokens`（按 model 决定）。

## 响应 schema

### POST /chat 同步返回

```json
{
  "chat_id": "uuid",
  "worker_id": "...",
  "status": "queued",
  "created_at": "2026-05-14T16:07:10Z"
}
```

### GET /chat/<id> 轮询返回（终态）

```json
{
  "chat_id": "uuid",
  "worker_id": "...",
  "status": "succeeded",         // succeeded | failed | timeout（不是 completed！）
  "created_at": "...",
  "started_at": "...",
  "finished_at": "...",
  "result": {
    "history": [
      {
        "role": "assistant",      // 转 transcript 时映射成 "agent"
        "content": "您好。",
        "metrics": {"source": "greeting"}
      },
      {
        "role": "user",
        "content": "你们卖什么车？",
        "metrics": {
          "source": "openai",
          "ttfb_ms": 1433,
          "latency_ms": 1433,
          "input_tokens": 22,
          "output_tokens": 18,
          "total_tokens": 40
        }
      },
      ...
    ],
    "stop_reason": "Reached max_turns.",     // 或 "Ended by end_checker." / 错误描述
    "turns_used": 4,
    "started_role": "assistant",
    "ended_by_checker": false,
    "usage": {
      "conversation": {"input_tokens": 137, "output_tokens": 92, "total_tokens": 229},
      "end_checker": {"input_tokens": 162, "output_tokens": 27, "total_tokens": 189},
      "total": {"input_tokens": 299, "output_tokens": 119, "total_tokens": 418}
    }
  }
}
```

**关键字段映射**（旧 → 新）：

| skill v1/v2 假设 | 实际（v3 dispatcher） |
|---|---|
| `status: "completed"` | `status: "succeeded"` |
| `messages` 顶层数组 | `result.history` |
| `n_turns` | `result.turns_used` |
| `error: null/<str>` | `status == "failed"` + `result.stop_reason` 或顶层 `error` |

### GET /simulation/<id> 轮询返回

按 dispatcher 实现，可能是：
- 同 /chat shape，但 `result.history` 替换为 `result.chats[].history`（每通一个 entry）
- 或直接返回 N 个 chat_id，逐个 GET

skill 兼容两种 shape：先看 `result.chats`，再看 `result.history`。

### 失败终态

```json
{
  "chat_id": "...",
  "status": "failed",
  "error": "signal: killed",      // 或 "worker exited unexpectedly" / openai 拒绝原因
  "result": {
    "stop_reason": "...",
    ...
  }
}
```

常见 error：
- `signal: killed` / `worker exited` → 服务端 worker 进程被杀（撞 worker_timeout 概率最高）
- `openai error: ...` / `dashscope error: ...` → LLM 返回的错误（key 错 / model 错 / 字段错）
- `decode request body` → schema 不对

## ASR 噪声注入位置

`user.system_prompt` = persona 原 prompt + ASR 噪声指令块（如果 persona.asr_noise != "none"）。

注入由**客户端**拼，**不走 HTTP body 字段**。详 `persona-sources.md`。

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

具体例子见旧版本本文档（外呼销售/售后客服/教育辅导 3 个场景模板未变）。

## 客户端轮询节奏（Phase D smoke + Phase E 主跑）

- POST 后立即拿 `chat_id`
- 等 `poll_initial_delay` 秒（默认 3s）再开始 GET
- GET 间隔 `poll_interval_sec` 秒（默认 3s）
- 总等待时长上限 `poll_max_total_sec` 秒（默认 1800s）—— 这是**客户端**的上限，不是服务端
- 终态 `status: succeeded` / `failed` / `timeout` → 停止轮询，记录结果
- 中间态 `status: queued` / `running` → 继续轮询

## 客户端 vs 服务端 timeout

**两件事**，别混：

| | 客户端轮询 timeout | 服务端 worker timeout |
|---|---|---|
| 谁定的 | skill 客户端 | dispatcher 维护者 |
| 配置位置 | `concurrency.poll_max_total_sec`（workspace） | `Q0-C worker_timeout`（dispatcher 端） |
| 触发动作 | 客户端放弃轮询，标记 timeout | 服务端杀 worker 进程，返回 `signal: killed` |
| 默认 | 1800s | 120s |
| 单 turn 估算超过此值的 70% 时 | — | 主动警告（skill 在 Phase D 末尾做） |
