# Phase A · Intake（多轮交互，只问必需）

skill 进入 Phase A 时按这个模板**一个一个**问。用户答完显示一句简短确认再进下一问。**只问必需的；高级参数（max_turns / temperature / timeout 等）走默认值，问完所有必需后再统一问"要不要调高级参数？"**

## Q0 — 远端 dispatcher URL（**必填，无默认**）

**Ask**:
> "你需要一个能跑双 LLM 模拟对话的 dispatcher 服务（接收 /chat 和 /simulation 请求，内部并发跑两个 LLM）。这个服务的 URL 是什么？（如 `http://your-host:8080`）"
>
> "如果还没有 dispatcher 服务，本 skill 跑不了——需要先部署。参考 EVAL_METHODS pipeline A 的 dispatcher 实现规范。"

**保存到 `<workspace>/config.json` 的 `remote_server` 字段**。

**Hard rule**：URL 必填。skill 不内置任何默认 server——避免别人下载 skill 就拿到第三方服务地址。

环境变量 `PROMPT_LAB_SERVER` 可覆盖（适合 CI/团队共享服务）。

### Q0 之后立即跑 **A.0 healthz 探测**（不要 key，秒回）

```bash
curl -s --max-time 5 <user_provided_url>/healthz
```

预期 → `{"status": "ok"}`

失败处理：
- 网络/拒连接 → 让用户检查 URL 拼写 + 服务是否在跑 + 防火墙
- 200 但不是预期格式 → 警告"远端可能不是 prompt-lab dispatcher，schema 可能不兼容；要不要继续？"
- 5xx → 服务挂了，让用户先修服务

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

## Q3 — 模型配置（**A/B/end_checker 必填 key**）

**重要前置说明**：
> "对话过程涉及 3 个远端模型，**全部 inline 进 HTTP 请求体**（不是 env var），**都必须有 API key**。如果用同一家服务商（如 DashScope）一个 key 可以三个角色都用。"

**Ask 4 次（一次一个角色）**：

### Q3-A: Agent A（被测主体）
> "被测的 agent 模型？"
- provider（默认 openai）+ model name（如 qwen-plus）+ base_url（默认 DashScope `https://dashscope.aliyuncs.com/compatible-mode/v1`）+ **API key**

### Q3-B: Agent B（persona 一侧）
> "模拟客户的 persona 模型？通常用更便宜的，如 qwen-flash。"
- 同上 4 字段
- 若用户说"和 A 一样"：复用 A 的所有 4 字段
- 通常 model 不同（A 用 qwen-plus，B 用 qwen-flash 省钱）

### Q3-C: end_checker（判断对话是否该停）
> "end_checker 是个判断对话该不该结束的小模型。可用 cheap model（qwen-flash）。"
- 同上 4 字段
- 若用户说"和 A 一样"：复用

### Q3-D: Judge（评分模型）
> "评分模型可以选**远端**（提供 4 字段）或**本地**（用主会话的 Claude）。本地不烧 key 但 transcripts 多时要派 subagent。"
- 远端 → 4 字段
- 本地 → 标记 `local: true`

### Q3-E: Suggester（优化 prompt 模型）
> "改进 prompt 的模型，同样可选远端或本地。建议用 Claude（写长文本最好），本地直接用主会话。"
- 同 Q3-D

**收集完 5 个角色显示配置表给用户确认**：
```
Agent A:    qwen-plus      DashScope  sk-xxx
Agent B:    qwen-flash     DashScope  sk-xxx (same as A)
end_checker: qwen-flash    DashScope  sk-xxx (same as A)
Judge:      claude-opus-4-7  local
Suggester:  claude-opus-4-7  local
```

### Q3 之后立即跑 **A.3 chat 连通探测**（验 URL + key + schema 全通）

不依赖 persona/criteria，用极简 stub。POST 一个最短 chat，**只要拿回 chat_id 就算通过**——不等真完成，省 LLM 费用：

```bash
curl -s --max-time 30 -X POST <url>/chat \
  -H 'content-type: application/json' \
  -d '{
    "runtime": {"max_turns": 2, "start_agent": "assistant", "min_messages_before_end_check": 1, "timeout_seconds": 30},
    "assistant": {
      "provider": "<Q3-A>",
      "model": "<Q3-A>",
      "llm_base_url": "<Q3-A>",
      "llm_api_key": "<Q3-A>",
      "network": {"mode": "direct"},
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
- 无 `chat_id` 但有 error → 显示给用户 + 给修复提示（key 错？schema 不对？）
- 网络错 → 提示检查防火墙

可选附加：拿到 chat_id 后 GET 一次 `/chat/{chat_id}` 看 status 是 `queued` / `running` / `succeeded` 任一即可（说明 server 在正常处理）。**不要等 succeeded**——只为验证 URL+key+schema 通畅。

失败处理：
- HTTP 401/403 → key 错 → 让用户检查 Q3 哪个 key 出错（一般是 Agent A 的 DashScope key）
- HTTP 400 + `decode request body: unknown field` → schema 不对（server 版本旧），中止
- HTTP 503 / `no healthy workers` → server 没起 worker → 让用户去启 worker
- timeout → 服务慢/网络差，让用户重试

通过 → 进 Q4。

## Q4 — 迭代轮数 N

**Ask**: "跑几轮？（默认 3）"
- 提示 token/key 消耗估算（基于 Q5 计算）
- 每轮跑完会停下来问"继续/停/微调"

## Q5 — 每 persona 每轮跑几次 K

**Ask**: "每个 persona 每轮跑几次？（默认 2，能看方差）"
- 算总 simulation 数：M（persona 数）× K × N = X 通
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
基准 prompt:   3716 tokens
Persona:       从 prompt 抽 20 条，全 moderate ASR 噪声
Agent A:       qwen-plus    DashScope    sk-xxx
Agent B:       qwen-flash   DashScope    sk-xxx (same)
end_checker:   qwen-flash   DashScope    sk-xxx (same)
Judge:         claude-opus-4-7    本地
Suggester:     claude-opus-4-7    本地
Greeting:      "您好，这边是新车销售线索回访..."
迭代:          3 轮 × 每 persona 2 次 = 总 ~120 通
Token 上限:    暂未设（跑完第一轮后会提示是否需要限）

要调高级参数吗？（max_turns / temperature / timeout / token_ceiling 等，详见 api-call-params.md）
  - 不调 → 用默认开跑
  - 调 → 逐项问

确认？(yes / 改 X)
```

## 高级参数（用户选"要调"时才问）

详见 `api-call-params.md`。常见 4 个：

- **max_turns**: 默认 20。短场景（FAQ 1 轮）改 8；长场景（多步骤）改 30
- **temperature**: A=0.7 / B=0.85 / end_checker=0. **end_checker 必须 0**（否则停得很随机）
- **timeout_seconds**: 默认 180（远端单 chat 超时）
- **token_ceiling**: 默认 null（不限制）。跑完第一轮后 skill 会主动建议（"本轮 prompt = N tokens，要不要设上限？"）；或在此 advanced 阶段主动设

## 自动生成 end_description

**不在 intake 阶段问用户**。等 Phase C 抽 criteria 完，Suggester 会基于 prompt + 场景描述自动生成一份 end_description，然后显示给用户确认/修改。详见 `api-call-params.md` 的 end_description 模板。

---

## Hard rules during intake

- **Q1 / Q3 三个 key (A/B/end_checker) 任一缺失** → 不能进入 Phase B（key 是 HTTP body 必填）
- **persona < 5 条** → 警告"样本太少分数不稳"，但允许继续
- **token 估算超过用户设的上限** → 提示，让用户确认或减 K/N
- 任何用户答案不清晰 → 重新问，不要硬塞默认值
