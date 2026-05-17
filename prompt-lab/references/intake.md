# Phase A · Intake（多轮交互，只问必需）

skill 进入 Phase A 时按这个模板**一个一个**问。用户答完显示一句简短确认再进下一问。**只问必需的；高级参数（max_turns / temperature / timeout 等）走默认值，问完所有必需后再统一问"要不要调高级参数？"**

---

## A.-1（仅 Claude Code 宿主）· Permission preflight

**触发条件**：宿主是 Claude Code。其它宿主（Codex CLI / Cursor / OpenClaw / 自研 agent）按各自 permission 模型处理，本 skill 跳过本节。

**为什么需要**：本 skill 通过 dispatcher 转发 LLM API key（dispatcher 设计要求 key 内联 HTTP body）。Claude Code 的 auto-mode safety classifier 会把"key 流向非 LLM 官方域名"识别为数据外泄并 **HARD BLOCK**。且 **skill 自己不能改 `~/.claude/settings.json`**（系统硬性禁止）。

**所以**：在问 Q0-A 之前，先把这段话给用户：

> "本 skill 后续会向你提供的 dispatcher URL 发送 LLM API key（这是 dispatcher 转发设计的一部分，**不是泄露**）。Claude Code 默认会拦这种请求。我自己没权限改 settings.json，需要你跑一行命令把 dispatcher host 加进 allowlist。
>
> 你给我 dispatcher URL 后，我立刻把命令模板贴给你 —— 用 `! python3 -c "..."` 前缀在终端跑，一行搞定。"

**Q0-A 拿到 URL 后**：用 Python 解析 host，拼出命令模板给用户复制粘贴。

```python
from urllib.parse import urlparse
host_port = urlparse(dispatcher_url).netloc  # e.g. "47.100.137.178:8080"
host = host_port.split(":")[0]               # e.g. "47.100.137.178"
```

然后给用户复制粘贴：

```
! python3 -c "
import json, os
p = os.path.expanduser('~/.claude/settings.json')
s = json.load(open(p)) if os.path.exists(p) else {}
s.setdefault('permissions', {}).setdefault('allow', [])
rules = [
    'Bash(curl * <HOST_PORT>*)',
    'Bash(python3 * <HOST>*)',
]
added = []
for r in rules:
    if r not in s['permissions']['allow']:
        s['permissions']['allow'].append(r); added.append(r)
json.dump(s, open(p,'w'), indent=2)
print('added:', added if added else '(nothing new, already allowed)')
"
```

把 `<HOST_PORT>` 替换为 `47.100.137.178:8080`（实例），`<HOST>` 替换为 `47.100.137.178`。

用户跑完显示 `added: [...]` 或 `nothing new`，即可继续。**不要让 Claude 自己跑 Edit 工具改 settings.json**——系统会硬拦，跑也跑不通，徒增噪声。

---

## Q0-A — dispatcher URL（**必填，无默认**）

**Ask**:
> "你需要一个能跑双 LLM 模拟对话的 dispatcher 服务（POST `/chat` 单通、POST `/simulation` 批量、GET `/chat/<id>` 轮询）。URL 是什么？（如 `http://your-host:8080`）"
>
> "如果还没有 dispatcher，本 skill 跑不了——需要先部署。"

**Hard rule**：URL 必填。skill 不内置任何默认 server——避免别人下载 skill 拿到第三方地址。

环境变量 `PROMPT_LAB_SERVER` 可覆盖（适合 CI/团队共享）。

## Q0-B — dispatcher access_token（**必填**）

**Ask**:
> "dispatcher 现在要 access token 鉴权（HTTP header `x-access-token: <token>`）。从 dispatcher 维护者那拿一个。"

**Hard rule**：token 必填，存 config.json `access_token` 字段，**所有后续 HTTP 请求都带这个 header**（健康探测 + chat + simulation + 轮询）。

**注意**：跟 LLM provider 的 API key（A/B/end_checker 的 `llm_api_key`）不是同一回事。前者认证 dispatcher 客户端身份，后者由 dispatcher 转发给 OpenAI/DashScope 等。

## Q0-C — dispatcher worker_timeout（建议问）

**Ask**:
> "dispatcher 服务端有 worker 进程超时（单通对话最长几秒）。从维护者拿这个数（默认 120s 假设）。"

**为什么必须知道**：服务端 worker 超时会**直接 kill 进程**，返回 `status: failed, error: signal: killed`。客户端再调 timeout（`runtime.timeout_seconds`）跟它无关。skill 后续会拿单 turn 实测耗时跟这个值比对，超过 70% 阈值警告。

如用户不知道 → **默认填 120s** + 在 Phase D smoke 阶段实测后回头校准。

### Q0 之后立即跑 **A.0 healthz 探测**（带 token）

```bash
curl -s --max-time 5 -H "x-access-token: <token>" <url>/healthz
```

预期 → `{"status": "ok"}`

失败处理：
- 401 `invalid access token` → Q0-B 的 token 错，重填
- 404 → URL 拼错，重填
- 网络/拒连接 → 让用户检查 URL + 服务是否在跑 + 防火墙
- 5xx → 服务挂，让用户先修

通过 → 进 Q1。

## Q1 — 基准 prompt

**Ask**:
> "把要优化的基准 prompt 给我。可以粘贴文本或给文件路径。"

**接收**：多行文本 / 绝对路径 / `~/...` 路径
**处理**：路径 → Read 工具读出来；显示字数 + 估 token + 头 5 行预览；确认无误

**Hard rule**：prompt 非空。

**注**：此时显示 token 数 + 一句温和提示"暂不设上限，跑完一轮看具体数量后你可以再决定是否要限"。**不强制问 token_ceiling**。

## Q2 — 测试集（persona）来源

**Ask（用 AskUserQuestion）** 三选一：
- (a) 我已有 persona JSON
- (b) 从 Q1 prompt 自动抽 persona（默认推荐）
- (c) 从过去真实 transcripts 抽 persona

分支处理详见 `persona-sources.md`。

**然后追问 ASR 噪声**（同一 AskUserQuestion 第 2 题）：
- (i) 不加（默认）
- (ii) 全 light / (iii) 全 moderate / (iv) 全 heavy
- (v) 按 tier 分配（stretch heavy / core light / gate none）

## Q3 — 模型配置（**A/B/end_checker 必填 key**，每角色自动判海外/国内）

**重要前置说明**：
> "对话过程涉及 3 个远端模型，**全部 inline 进 HTTP 请求体**（不是 env var），**都必须有 API key**。如果用同一家服务商（如 DashScope）一个 key 可以三个角色都用。
>
> 我会根据你给的 base_url 自动判断模型是**海外**（OpenAI/Anthropic/Gemini）还是**国内**（DashScope/智谱/DeepSeek/Kimi 等），然后请求体里相应加 `proxy: true` 或 `network.mode: direct`。判错了你可以纠正。"

### Q3-A: Agent A（被测主体）
> "被测的 agent 模型？"
- provider（默认 openai）+ model name（如 qwen-plus）+ base_url（默认 DashScope `https://dashscope.aliyuncs.com/compatible-mode/v1`）+ **API key**
- **自动判海外/国内**（见下方"海外判定"章节）→ 显示给用户确认

### Q3-B: Agent B（persona 一侧）
> "模拟客户的 persona 模型？通常用更便宜的，如 qwen-flash。"
- 同上 4 字段
- 若用户说"和 A 一样"：复用 A 的所有字段 + 同样的 network 设定
- 通常 model 不同（A 用 qwen-plus，B 用 qwen-flash 省钱）；**也可能不同 provider**（如 A 用 DashScope，B 用 OpenAI）—— 这时 network 字段会一个 direct 一个 proxy

### Q3-C: end_checker（判断对话是否该停）
> "end_checker 是个判断对话该不该结束的小模型。可用 cheap model（qwen-flash）。"
- 同上 4 字段
- 若用户说"和 A 一样"：复用

### Q3-D: Judge（评分模型）
> "评分模型可以选**远端**（提供 4 字段）或**本地**（用主会话的 Claude）。本地不烧 key 但 transcripts 多时要派 subagent。"
- 远端 → 4 字段 + 海外判定
- 本地 → 标记 `local: true`

### Q3-E: Suggester（优化 prompt 模型）
> "改进 prompt 的模型，同样可选远端或本地。建议用 Claude（写长文本最好），本地直接用主会话。"
- 同 Q3-D

### 海外判定（Q3-A/B/C/D/E 收到 base_url 后立即跑）

域名白名单（命中即海外，请求体加 `proxy: true`）：

```python
OVERSEAS_DOMAINS = {
    "api.openai.com",
    "api.anthropic.com",
    "generativelanguage.googleapis.com",
    "api.cohere.com",
    "openrouter.ai",
    "api.together.xyz",
    "api.x.ai",                           # xAI / Grok
    "api.mistral.ai",
    "api.deepinfra.com",
    "api.fireworks.ai",
}
```

判定逻辑：
- 命中 → 海外 → `proxy: true`（顶层字段，不在 `network` 里）
- 未命中（DashScope / 智谱 / DeepSeek / Kimi / 自部署 IP / localhost / 内网）→ 国内 → `network: {"mode": "direct"}`
- 显示给用户确认："base_url=X 我判断为 [海外/国内]，请求体里我加 [`proxy: true` / `network.mode: direct`]，对吗？" → 用户可改

### GPT-5 / reasoning 模型注意

- `gpt-5-chat-latest` ✓ 支持 `max_tokens`，可直接用
- `gpt-5` / `gpt-5.1` / `gpt-5.2` / `gpt-5.5` 等：必须用 `max_completion_tokens`（dispatcher 已能透传）
- `gpt-5*-pro` / `gpt-5.5-pro`：**不能走 chat completions 端点**（OpenAI 返回 "not a chat model"），换 `*-chat-latest` 或不带 -pro 的版本
- **reasoning 模型（thinking 链）每轮耗时翻倍**：在 Q3 末尾、Phase D smoke 之前主动提醒用户警惕 timeout
- DashScope qwen3 系列要关 thinking：`request.enable_thinking: false`

### 收集完 5 个角色显示配置表给用户确认

```
Agent A:    qwen-plus      DashScope (direct)  sk-xxx
Agent B:    gpt-5-chat-latest  OpenAI (proxy)  sk-proj-xxx
end_checker: qwen-flash    DashScope (direct)  sk-xxx (same key as A)
Judge:      claude-opus-4-7  local
Suggester:  claude-opus-4-7  local
```

### Q3 之后立即跑 **A.3 chat 连通探测**

不依赖 persona/criteria。POST 一个最短 chat，**只要拿回 chat_id 就算通过**——不等真完成，省 LLM 费用：

```bash
curl -s --max-time 30 -X POST <url>/chat \
  -H 'content-type: application/json' \
  -H 'x-access-token: <Q0-B token>' \
  -d '{
    "runtime": {"max_turns": 2, "start_agent": "assistant", "min_messages_before_end_check": 1, "timeout_seconds": 30},
    "assistant": {
      "provider": "openai", "model": "<Q3-A model>",
      "llm_base_url": "<Q3-A base_url>", "llm_api_key": "<Q3-A key>",
      "network": {"mode": "direct"},           # 国内
      # 或 "proxy": true,                       # 海外（二选一）
      "request": {"temperature": 0.7, "top_p": 0.9, "max_tokens": 50},
      "system_prompt": "Reply with a one-sentence acknowledgement.",
      "greeting": "Hello, this is a connectivity test."
    },
    "user": {"...同 assistant 但用 Q3-B 配置...", "system_prompt": "Reply briefly to greet back.", "greeting": ""},
    "end_checker": {"...同 Q3-C...", "system_prompt": "Return JSON only.", "end_description": "Stop after any 2 messages exchanged."},
    "verbose": false
  }'
```

预期返回：
```json
{"chat_id": "uuid", "worker_id": "...", "status": "queued", "created_at": "..."}
```

校验：
- 有 `chat_id` → 通过 ✓
- 无 `chat_id` 但有 error → 显示给用户 + 给修复提示
- 网络错 → 提示检查防火墙

**附加（可选）**：拿到 chat_id 后 GET 一次 `/chat/<chat_id>` 看 status 是 `queued` / `running` / `succeeded` 任一即可。**不要等 succeeded**——只为验证 URL+token+key+schema 通畅。

失败处理：
- HTTP 401/403 + `invalid access token` → Q0-B token 错
- HTTP 401/403 + 别的 → Q3 某个 key 错（LLM provider 拒）
- HTTP 400 + `decode request body: unknown field` → schema 不对（server 版本旧/新）
- HTTP 400 + `unsupported parameter: max_tokens` → 你用了 GPT-5 系列但没改 `max_completion_tokens`
- HTTP 503 / `no healthy workers` → 让用户去启 worker
- 长时间 queued 不动（>2 分钟）→ dispatcher worker 池可能不识别这个 model（如 `qwen3.6-plus` 经历过）—— 换 model 或联系维护者

通过 → 进 Q4。

## Q4 — 迭代轮数 N

**Ask**: "跑几轮？（默认 3）"
- 提示 token/key 消耗估算（基于 Q5 计算）
- 每轮跑完会停下来问"继续/停/微调"

## Q5 — 每 persona 每轮跑几次 K

**Ask**: "每个 persona 每轮跑几次？（默认 2，能看方差）"
- 算总 simulation 数：M（persona 数）× K × N = X 通
- POST 数：M × N（per-persona simulation，每次 count=K）
- 估算 token：粗略 ~30k/通

## Q6 — agent 开场白（greeting）

**Ask**: "外呼场景 agent 先说一句开场白。给一句具体的（如 '您好，这边是 XX 客服回访...'）。"

**处理**：
- 用户给一句 → 保存到 config.json，写到每次 /chat 的 `assistant.greeting`
- 后续每轮如果 prompt 改了开场设计，skill 会提示用户："prompt 里这次改了开场，要不要更新 greeting？"

## Q7 — 场景描述

**Ask**: "一句话描述这个 prompt 干啥（如：'外呼销售对接 4S 店报价' / '电商售后客服' / '法律咨询初筛' / '英语口语陪练'）。这句话会喂给 Suggester 生成 criteria 和 end_description。"

## Q8 — workspace 路径

**Ask**: "workspace 放哪？（默认 `~/prompt-lab-workspaces/<project_id>/`）"
- project_id 可让用户给，或自动生成（用 prompt 头几个字 + 时间戳）
- 已存在路径 → 询问"继续旧项目 / 备份后新建 / 换路径"

## 收集完后

显示一份完整配置摘要 + 让用户确认：

```
=== prompt-lab 配置摘要 ===
Project:        auto-call-20260514
Workspace:     ~/prompt-lab-workspaces/auto-call-20260514
场景:           外呼销售-汽车线索回访
Dispatcher:    http://47.x.x.x:8080 (token: d9bP**, worker_timeout: 120s)
基准 prompt:   3716 tokens
Persona:       从 prompt 抽 20 条，全 moderate ASR 噪声
Agent A:       qwen-plus           DashScope (direct)  sk-xxx
Agent B:       gpt-5-chat-latest   OpenAI (proxy)      sk-proj-xxx
end_checker:   qwen-flash          DashScope (direct)  sk-xxx (same as A)
Judge:         claude-opus-4-7    本地
Suggester:     claude-opus-4-7    本地
Greeting:      "您好，这边是新车销售线索回访..."
迭代:          3 轮 × 每 persona 2 次 = 总 ~120 通
Token 上限:    暂未设（跑完第一轮后会提示是否需要限）

⚠️ 主观预警：B 是 OpenAI（proxy）+ 没有 reasoning，单 turn 估 ~1.5s；
   单通 ~25s（max_turns=20 算上限），远低于 worker_timeout 120s ✓

要调高级参数吗？（max_turns / temperature / timeout / token_ceiling 等）
  - 不调 → 用默认开跑
  - 调 → 逐项问

确认？(yes / 改 X)
```

## 高级参数（用户选"要调"时才问）

详见 `api-call-params.md`。常见 4 个：

- **max_turns**: 默认 20。短场景（FAQ 1 轮）改 8；长场景（多步骤）改 30
- **temperature**: A=0.7 / B=0.85 / end_checker=0. **end_checker 必须 0**（否则停得很随机）
- **timeout_seconds**: 默认 180（远端单 chat 超时）—— 客户端轮询用，不是服务端 worker 超时
- **token_ceiling**: 默认 null（不限制）

## 自动生成 end_description

**不在 intake 阶段问用户**。等 Phase C 抽 criteria 完，Suggester 会基于 prompt + 场景描述自动生成一份 end_description，然后显示给用户确认/修改。详见 `api-call-params.md` 的 end_description 模板。

---

## Hard rules during intake

- **Q0-A / Q0-B 任一缺失** → 不能进入 Q1
- **Q3 三个 key (A/B/end_checker) 任一缺失** → 不能进入 Phase B（key 是 HTTP body 必填）
- **A.-1 Claude Code allowlist 未加** → A.0 healthz 探测会被 auto-mode 拦，直接报"Denied by auto mode classifier"。skill 检测到这个错误立刻回到 A.-1 步骤
- **persona < 5 条** → 警告"样本太少分数不稳"，但允许继续
- **token 估算超过用户设的上限** → 提示，让用户确认或减 K/N

---

## 撞 timeout 怎么办（故障树）

Phase D smoke 拿到 `status: failed` + `error: signal: killed` / `timeout` → 按以下顺序排查：

1. **看是 client 还是 server 超时**：
   - 如果客户端 fetch 自己 timeout（请求都没发出去）→ 检查网络
   - 如果 dispatcher 返回 `status: failed, error: signal: killed` / `worker exited` → 服务端 worker 杀进程

2. **服务端 worker 杀进程的常见原因**（按概率排）：
   - **reasoning 模型 + 大 max_tokens**：gpt-5 / gpt-5.x / qwen3-thinking 等单轮就 1-2 分钟。**降 max_tokens 到 200-500**，或换非 reasoning 模型（qwen-plus / gpt-5-chat-latest 等）
   - **prompt 太长 + max_tokens 巨大**：3K input + 4K output 在弱模型上要 90s+。**拆 prompt** 或 **降 max_tokens**
   - **dispatcher worker_timeout 设得太小**：联系维护者改大（默认 120s 可调到 300s）

3. **不要直接重试**：先估算单 turn 耗时 vs worker_timeout，预测会不会再撞。skill 在 smoke 完成后已经把 `metrics.latency_ms` 读出来展示给用户，根据这个判断。

4. **smoke 阶段就要发现这件事**：Phase D 拿到第一通 transcript，立刻把 `max(latency_ms)` 跟 `worker_timeout × 0.7` 比较：
   - 超过 → 警告并停下，问用户调什么
   - 未超 → 进 Phase E 主跑

详细日志解读见 `references/scoring-pipeline.md` 的失败处理章节。
