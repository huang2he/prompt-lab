---
name: prompt-lab
description: End-to-end SOP for iterating an agent prompt through a structured eval-improve-eval loop. Use when the user wants to improve any prompt (dialogue agents / chatbots / voice agents / structured task agents) via automated rounds of simulated dialogues + scoring + automated rewriting. Trigger phrases include "用 prompt-lab", "迭代 prompt", "优化这个 prompt", "prompt eval loop", "跑 N 轮看 prompt 表现", "改 prompt 直到分数稳定". Walk the user through 6 phases (intake → criteria → smoke probe → main loop × N rounds → wrap up), gating on user confirmation between each. Do NOT use for one-shot prompt edits, real-call audio analysis, or evaluating a deployed system without simulated dialogues.
---

> ⚠️ **Disclaimer 一句话**：本 skill 是**通用框架**，但 references 中部分例子（rule 编号 r1-r20、"汽车外呼/4S店/承接词池"业务词、5 轮分数曲线）来自一个具体实例项目（p1）。换 prompt 项目时这些都会重抽，**不要照搬**。skill 通用的是：6 阶段流程 / 4 维 rubric 框架 / hard_fail 闭枚举 / 数学公式 / dispatch 模式 / 净计数迭代逻辑。具体业务 rule 是每个 prompt 项目自己抽出来的。

# prompt-lab

一个**结束到结束的提示词迭代 SOP**。带用户从"我有一个 prompt 想优化"走到"几轮迭代后拿到改进的新 prompt + 全程评分曲线 + 可视化 dashboard"。

## SOP 一图概览

```
Phase A  介绍 + 多轮收集 11 个输入
         ├─ A.-1 ★ Claude Code permission preflight（如宿主是 Claude Code）
         ├─ Q0-A dispatcher URL → A.0 ★ healthz 探测（带 x-access-token，秒回）
         ├─ Q0-B dispatcher access_token（必填，写到 x-access-token header）
         ├─ Q1 基准 prompt
         ├─ Q2 测试集来源 + ASR 噪声
         ├─ Q3 5 个模型配置（每角色 base_url 自动判海外/国内）→ A.3 ★ chat 连通探测
         ├─ Q4 N 轮
         ├─ Q5 K 复跑次数
         ├─ Q6 agent greeting
         ├─ Q7 场景描述
         └─ Q8 workspace 路径
   ↓
Phase B  建立 workspace + 落盘 config.json
   ↓
Phase C  抽 criteria → ★ 用户确认（gate）
   ↓
Phase D  远端 smoke probe（POST /chat 单通 × max_turns=4 验完整对话 + response shape + 单 turn 实际耗时）→ ★ gate
   ↓
Phase E  主循环 × N 轮（POST /simulation 每 persona 一次 count=K；★ 给用户看分数 + ★ 看下轮 prompt diff）
   ↓
Phase F  收尾：分数曲线总结 / 推荐版本 / 生成 dashboard.html
```

**4 个探测**（递进式）：
- **A.-1** Claude Code permission preflight：宿主是 Claude Code 时，先 allowlist dispatcher host（否则 auto-mode 把 key 转发当数据外泄拦截）
- **A.0** healthz：URL 拼写 + access_token 对否（带 `x-access-token` 调 GET `/healthz`）
- **A.3** chat 拿 id：key+schema 全通否（POST `/chat` 拿回 `chat_id` 就够，不等完成）
- **D** 完整 smoke：真 persona 跑完 4 轮验 `result.history` shape + 每 turn 实际耗时 vs `worker_timeout`

**两种 endpoint 用途**（已与 dispatcher 维护者对齐）：
- `/chat` —— 一次产 1 通对话（用于 A.3 + Phase D smoke）
- `/simulation` —— 一次产 `count` 通（用于 Phase E 主跑，每 persona 一次 `count=K`，POST 数从 N×K 降到 N）

`★` 标的是**用户 gate**：skill 必须等用户回应才继续。

## 设计哲学（v3.2 起，必读）

本 skill 的本质**不是"无限优化分数"**，是在**固定的 agent A 模型下**，找出该模型 + 一份 prompt 能稳定承载的**最大指令集**。换更聪明的模型（参数量大、价格贵、响应慢）很容易把分数刷上去，那不是这个 skill 的工作。

**3 个推论**：

1. **prompt 体积有预算**。每轮新 prompt 不应该比上一轮大 >10%。能精简就精简，能合并就合并，不堆 hard rule。因为：模型上下文窗口有限、长 prompt 让中段规则被忽略、用户分发 prompt 时 token 成本也是成本。
2. **end_checker 是辅助挂断工具，不算 agent A 的能力**。end_checker 把"잘 모르겠어요"判为"主题无关 3 次"导致提前结束，这是 end_checker 的问题，跟被测 prompt 的指令遵循没关系。评分时**单独标注**"end_checker 误判通数"，不混入 agent A 的 bad case。
3. **最终交付物是能力地图（capability map），不是单一最佳分数**。把指令分三档（稳定遵循 ≥95% / 不稳定 40-95% / 完全做不到 <40%），告诉用户哪些再迭代也救不了，需要换模型 / 拆任务 / 加 fallback。详见 `references/capability-map.md`。

## 通用约束（Hard Rules）

- **Claude Code 宿主必读**：本 skill 通过 dispatcher 转发 LLM API key（设计如此，不是泄露）。Claude Code 的 auto-mode safety classifier 会把"key → 非 LLM 厂商官网"识别为数据外泄并 HARD BLOCK。**且 skill 自己不能改 settings.json**（系统硬性禁止）。所以 Phase A 第一步是 **A.-1 让用户**跑一行 `! python3 ...` 把 dispatcher host 加入 allowlist。详 `references/intake.md` A.-1 章节。
- **prompt size budget**：round-(K+1) prompt token 数 ≤ round-K × 1.10。超出预算 → Suggester 必须先合并/精简旧规则再加新规则。详见 `references/iterate-loop.md` 的 size budget 章节。
- **end_checker 误判单独诊断**：评分时区分"agent 的 bad case"和"end_checker 的误判"。后者不算 prompt 失败，只在 Phase F 报告里独立列出 + 建议用户换 end_checker model 或修 end_description。
- **Phase F 输出能力地图**：3 档分类 + 每档给具体指令清单 + "做不到"档给 escalation 路径（换 model / 拆 task / fallback）。**不要再吹"分数从 X 到 Y"**，那只对当前 model 有意义。
- **dispatcher schema（2026-05 起）**：
  - 鉴权：`x-access-token: <token>` HTTP header（每个请求必须）
  - 海外模型（OpenAI / Anthropic / Gemini 等域名）：角色块加 `"proxy": true`
  - 国内模型（DashScope / 智谱 / DeepSeek / Kimi / 自部署）：角色块加 `"network": {"mode": "direct"}`（**显式写**，不省略）
  - 响应字段：`status: "succeeded" | "failed" | "timeout"`（不是 `completed`）；transcript 在 `result.history`；turn 数在 `result.turns_used`
  - 服务端 worker 硬超时（默认 120s）独立于客户端轮询；单 turn 估算 > `worker_timeout × 0.7` 必须警告。
- **评分用 1-5 制**，不是 0-10。
- **Bad case = goal_status != pass**，partial 算 bad。
- **criteria 抽不出 → STOP**（不烧 key 跑空标准）。
- **prompt token 上限**（默认无限制；用户可在跑完第一轮看到 prompt token 数后再决定是否设上限，或在 Phase A 高级参数处主动设）。
- **hard_fails 闭枚举 6 类**（hallucination / out_of_scope_commitment / identity_breach / injection_breach / infinite_loop / early_hangup）。任何 subagent 提出新类型，merge 阶段拒绝。
- **rubric 变更 bump 版本**，曲线分段画，不混。
- **每个 prompt 项目独立 workspace 子目录**，persona pool/criteria/rubric 都按项目隔离。

---

# Phase A · 介绍 + 输入收集（多轮交互）

进入 skill 第一件事：

**A.-1（仅 Claude Code 宿主）. Permission preflight**

进入 A0 之前，告诉用户一段话：

> "本 skill 后续会向你提供的 dispatcher URL 发送 LLM API key（dispatcher 设计要求 key 内联在 HTTP body，**不是泄露**）。Claude Code 的 auto-mode safety classifier 会把这类请求识别为数据外泄并 HARD BLOCK。我自己也没权限改你的 settings.json。所以请你**在下一步问 dispatcher URL 之前**，准备好把 dispatcher host 加进 ~/.claude/settings.json 的 allow 列表 —— 我会在你给出 URL 后立刻把命令模板给你复制粘贴。"

详细脚本模板见 `references/intake.md` A.-1 章节。其它宿主（Codex / Cursor / OpenClaw）请参考各自宿主的 permission 模型，本 skill 不能替你做。

---

**A0. 自报家门**（一段话）：
> "我是 prompt-lab，会带你跑 N 轮 prompt 迭代循环：每轮从基准 prompt 抽评估标准 → 用一组模拟客户跑对话 → 评分找 bad case → 自动改 prompt 出下一版。我会一边问你几个问题一边推进，每个关键节点会停下来给你看东西并等你确认。"

**A1-A11. 多轮收集 11 个核心输入**（一个一个问，**不要堆 4 问**）。

详细问题模板、分支逻辑、每个问题的兜底默认值，见 `references/intake.md`。每个问题用户答完显示总结后才进下一问。

简要清单：
- **Q0-A dispatcher 服务 URL（必填）**：远端跑双 LLM 对话的服务地址。**skill 不预装任何默认**——避免共享 skill 后第三方服务被滥用。`PROMPT_LAB_SERVER` 环境变量可覆盖。问完立即跑 **A.-1 Claude Code allowlist 提示**（如适用）+ **A.0 healthz 探测**（带 token）
- **Q0-B dispatcher access_token（必填）**：HTTP header `x-access-token` 的值，从 dispatcher 维护者拿
- **Q0-C 不再询问** —— dispatcher 服务端硬超时是用户/维护者都不一定知道的数字（同事 bug 报告里"120s"其实是从失败时长反推的）。skill 改成 **Phase D smoke 阶段实测 + 绝对阈值告警**：若实测单 turn `latency_ms > 30000` → 警告"接近常见 dispatcher worker_timeout（60-300s），主跑可能撞 signal: killed"，并在主会话用 WebSearch 查"<model_name> disable reasoning thinking"看模型能不能关思考链
- **Q1 基准 prompt 文本**（粘贴或文件路径）。**显示 token 数但暂不设上限**（默认 null）；跑完第一轮后再问是否要限
- **Q2 测试集来源**：3 选 1（导入 persona JSON / 从 prompt 抽 / 从过去 transcripts 抽）+ ASR 噪声 yes/no/level
- **Q3 5 个角色模型配置**：
   - **3 个必须远端（HTTP body 内联 key）**：agent A（被测主体）/ agent B（persona 侧）/ end_checker（判断对话结束的小模型，服务端调）
   - **2 个可远端或本地**：评分 Judge / 优化 Suggester（本地 = 用主会话 Claude）
   - 每个远端角色收到 base_url 后，skill 用 domain 白名单**自动判定海外/国内** → 显示给用户确认 → 落到请求体（`proxy: true` 或 `network.mode: direct`）。判定 helper 见 `references/api-call-params.md` 海外白名单章节
   - 三远端角色可共用 1 个 key（同 provider 如 DashScope 时）
   - GPT-5 系列等 reasoning 模型需注意：响应字段名 `max_completion_tokens`（不是 `max_tokens`）。dispatcher 已能透传该字段
- **Q4 N 轮迭代次数**（默认 3，可改）
- **Q5 K 每个 persona 每轮跑几次**（默认 2）
- **Q6 agent greeting**（用户手写一句开场白）
- **Q7 场景描述**（一句话，用于 persona/criteria 生成时参考）
- **Q8 workspace 路径**（默认 `~/prompt-lab-workspaces/<project_id>/`）

**收集完成后给用户一份配置摘要**（一张表），让用户确认无误再进 Phase B。

---

# Phase B · 建立 workspace + 落盘

**B1**. 判断"新项目 vs 继续旧项目"：
- 用户提供的 workspace 路径**已存在** + 有 `prompts/<id>/iterations/round-*/` → 提示用户"检测到 N 轮历史，继续？还是覆盖新建？"
- 不存在 → bootstrap 新目录

**B2**. 创建目录结构（见 `references/workspace-layout.md`）：

```
<workspace>/
├── prompts/p1/                  # p1 是默认 prompt id，可改
│   ├── README.md                # skill 自动写：项目简介 + 配置摘要
│   ├── rubric.md                # 4 维 rubric 框架（含默认权重）
│   ├── personas/
│   │   ├── pool.jsonl           # Phase A Q2 来源决定
│   │   └── SCHEMA.md
│   └── iterations/
│       └── round-01/
│           ├── prompt.md        # Q1 给的基准 prompt
│           ├── run_plan.json    # 自动生成（用所有 persona × K 次）
│           └── (criteria.json 在 Phase C 写)
├── config.json                  # 4 模型配置 + 远端 URL + token 上限
└── leaderboard.json             # 跨轮 KPI（每轮跑完自动 append）
```

**B3**. 若 Q2 选了"从 prompt 抽 persona"或"从 transcripts 抽 persona"：
- 现在执行 persona 生成（用 Q3 的评分模型或本地 Claude）
- 生成 ~15-30 条 persona 落到 `pool.jsonl`
- 显示前 5 条给用户预览 + 让用户确认数量够不够

详细 persona 生成逻辑见 `references/persona-sources.md`。

---

# Phase C · 抽 criteria → 用户签字 ★

**C1**. 用 Q3 的 Suggester（远端或本地）读 round-01/prompt.md，按 `references/criteria-extraction.md` 抽 criteria.json。

输出包含：
- `scenario`（一句话，复用 Q5 的场景描述）
- `business_goals[]`（per-call 检查的目标）
- `behavior_rules[]`（每条带 `scope` + `severity` + `check_hint`）
- `intent_signals[]` / `conversion_signals[]`（仅信息参考，不直接评分）

**C2. ★ 用户 gate**：显示 criteria.json 给用户，**等用户确认/反馈/要求重抽**：
- "看着对" → 进 Phase D
- "rule X 不对，改一下" → skill 修改后再次确认
- "完全重抽" → 让用户提供改进提示

**Hard gate**: 如果 `business_goals` 或 `behavior_rules` **空**，skill 拒绝继续，写 `criteria_extraction_failed.md` 报告原因。

---

# Phase D · 远端 smoke probe ★

**D1**. 远端 API URL 从 `<workspace>/config.json` 读（Phase A 问用户）。环境变量 `PROMPT_LAB_SERVER` 覆盖 config。**skill 不预装任何默认服务器 URL**——必须用户自己提供。

**D2**. 跑 1 通最短测试（用 `/chat`，不是 `/simulation`）：
- 选 pool 里第一条 persona
- `runtime.max_turns = 4`
- POST `/chat` → 拿 `chat_id` → GET `/chat/<chat_id>` 轮询直到 `status: "succeeded"`

**D3**. 校验：
- `status == "succeeded"` （不是 `completed`）
- response 含 `result.history` 字段且非空
- history 形如 `[{role: "assistant"|"user", content: "...", metrics: {ttfb_ms, latency_ms, ...}}, ...]`
- **从 `metrics.latency_ms` 抽出实际单 turn 耗时**，跟 Phase A Q0-C 的 `worker_timeout` 比对：
  - `max(latency_ms across turns) > worker_timeout × 1000 × 0.7` → 警告用户："实测单轮 X ms 接近服务端 worker timeout（Y ms × 0.7 阈值），主跑时可能撞 `signal: killed`。建议降 max_tokens / 关 reasoning / 换轻量模型"

**D4. ★ Gate**：
- 通过 → 进 Phase E
- 失败 → 显示完整远端响应 + 错误诊断 + 让用户决定（重试 / 换服务器 / 中止）

详见 `references/smoke-probe.md`。

---

# Phase E · 主循环 × N 轮（用户决定何时停 ★）

```
for round in 1..N:
   E1.   run dialogues (固定 model, 当前 persona pool)
   E1.5. ★ 加 3-5 个新 persona 扩边（探未覆盖的指令组合）
   E2.   score
         · agent 行为 → 每条指令 pass_rate
         · end_checker 误判单独标注（不算 agent bad case）
   E3. ★ 显示分档结果（稳定 ≥95% / 不稳定 40-95% / 做不到 <40%）
   E4. 生成 suggestions
       · 只针对"不稳定"档（"稳定"已 OK 不动；"做不到"承认上限）
       · 必须满足 prompt size budget（≤ +10% vs 旧版）
   E5. 应用到 round-(K+1) prompt
   E6. ★ 显示 prompt diff + token delta 给用户
   E7. ★ 问用户：继续？停？看能力地图？
```

## E1. Run dialogues

调远端 batch：**每个 persona 一次 `POST /simulation`，body 顶层 `count = K`**。N persona 总共 N 个 POST（不是 N×K）。
- 每次请求 HTTP header 带 `x-access-token`
- 每个角色块根据 base_url 海外/国内写 `proxy: true` 或 `network.mode: "direct"`
- 拿回 `chat_id` 后轮询 `GET /chat/<chat_id>`（dispatcher 的 simulation 复用同一个轮询端点）
- 终态 `status: "succeeded" | "failed" | "timeout"`；transcripts 在 `result.chats[].history`（simulation 模式数组）或 `result.history`（chat 模式单条）
- 单 turn 实际耗时从 `metrics.latency_ms` 读，若超 `worker_timeout × 0.7` 主动告警
- 详见 `references/scoring-pipeline.md` 的"Step 2 - run"段 + `references/api-call-params.md` 完整 schema
- 大量 timeout 是 server 拥堵常态，K-stretch persona 容易撞 worker_timeout

## E1.5. 加新 persona 扩边（v3.2 新增）

固定 10 persona 跑 N 轮只能验证已知场景。每轮主跑后**加 3-5 个新 persona**：
- 用 Suggester 看本轮 transcripts 找 agent 表现最稳定的指令 → 派生"挑战这条指令"的新 persona
- 也派生覆盖未见过的客户类型（如方言、ASR 噪声 heavy、双语混说）
- 落到 `personas/pool.jsonl`，下轮 run dialogue 时一起跑

详见 `references/persona-sources.md` 的"每轮扩 pool" 章节。

## E2. Score（三档分类）

按 transcripts 数量自动选模式：
- **transcripts ≤ 30**: 主会话 Claude inline 评分
- **transcripts > 30**: 用 Judge 走 6-subagent 并行 dispatch

评分输出两层：

**Layer A · 每条指令的 pass_rate**：
- 从 criteria.json 的 behavior_rules + business_goals 抽出指令列表
- 每条指令算 `pass / total` 比例
- 按 pass_rate 分三档：稳定 ≥95% / 不稳定 40-95% / 做不到 <40%

**Layer B · 失败原因诊断**（每条 bad case 标 1 个 tag）：
- `agent_failure`：agent 自身指令遵循失败（hallucination / 格式违规 / 状态丢失等）→ 计入 agent bad case
- `end_checker_misjudge`：end_checker 提前误判（"잘 모르겠어요" → 主题无关）→ **不计入 agent bad case，独立统计**
- `persona_quit`：persona 自己设计就是中途离开（kr_p10 류）→ 不算失败
- `dispatcher_error`：worker timeout / signal kill → 基础设施问题，独立列

3 层评分流水线（向下兼容）：
- **Layer 1 客观**：Python regex（字数 / 关键词 / 句尾词 / 重复检测等）
- **Layer 2 主观**：行为规则 + 业务目标由 Judge 评
- **Layer 3 聚合**：按指令算 pass_rate，按失败原因分类标 tag

详见 `references/scoring-pipeline.md` + `references/capability-map.md`。

## E3. ★ 显示分档结果

给用户看一个表（不是单一分数）：

```
=== Round-K · gpt-4o-mini · 50 transcripts ===

[稳定遵循 ≥95%] (8 条)
  · DQ3/DQ4 5단계 量表完整 (50/50)
  · 보기 마침표分离 (50/50)
  · SQ1→SQ2→SQ3 顺序  (49/50)
  ...

[不稳定 40-95%] (3 条) ← 下一轮优化重点
  · 终止后不重复 인사 (37/50 = 74%)
  · "잘 모르겠어요" 9-coding (28/50 = 56%)
  ...

[完全做不到 <40%] (1 条) ← 不再改 prompt，建议 escalation
  · 30 turn 内问完整问卷 (16/50 = 32%) → 换 model 或拆 task

[end_checker 误判] 14/50 (独立统计，不算 agent 失败)
[dispatcher 问题]  0/50

token 数：4502 (vs round-1 baseline 4180, +7.7% 在预算内)
```

## E4. 生成 suggestions

按 `references/suggestion-writing.md` 模板，由 Suggester（远端或本地）写出。

## E5. 应用到下轮 prompt

按 `references/prompt-iteration.md`：
1. 创建 round-(K+1)/ 目录
2. 复制本轮 prompt 作起点
3. 应用每条 proposed change
4. 跑 token check (≤ 上限)
5. 写 diff.md
6. 复制 run_plan 和 criteria 到下轮（如果 criteria 没动）

## E6. ★ 显示 prompt diff

给用户看：
- 改了哪几节
- 增删的句子
- token 数变化

## E7. ★ 用户决定下步

3 选 1：
- **继续**：进 round-(K+1)
- **停**：进 Phase F
- **微调本轮 prompt**：用户给修改指令，skill 在 round-(K+1) prompt 上再 Edit，重新 diff 给用户看，再问

---

# Phase F · 收尾 · 能力地图 (capability map)

**F1**. 跨轮聚合每条指令的最终 pass_rate。规则见 `references/capability-map.md`。

**F2**. 生成 `capability_map.md`（在 workspace 顶层）— **这是 Phase F 的主交付物**：

```
## 能力地图 (model = gpt-4o-mini · prompt = round-3/prompt.md · 4502 tokens)

### 稳定遵循（≥95% pass_rate）— 该模型 + 该 prompt 能稳定承载的指令
- DQ3/DQ4 五级量表完整 (50/50)
- 보기用句号分离不用逗号 (49/50)
- ...

### 不稳定（40-95%）— 再迭代可能有空间
- 终止后不重复 인사 (37/50 = 74%) — round-3 比 round-1 提升 +30pp
- ...
建议：第 N+1 轮针对性优化（但注意 size budget）

### 完全做不到（<40%）— 该 model 上限，不要再改 prompt
- 30 turn 内问完整问卷 (16/50 = 32%) 
  → escalation 选项：
    (a) 换更大 model（gpt-4o / gpt-5-chat-latest 预计完成率 ≥ 70%）
    (b) 任务拆分（SQ agent + 본조사 agent + DQ agent 三级流水）
    (c) 接受不完成 + 人工 fallback 跟进未完成通话
- ...

### 辅助工具问题（非 agent prompt 责任）
- end_checker 误判 14/50 → 建议换 end_checker model 或重写 end_description
- dispatcher worker timeout 0/50 → OK
```

**F3**. 推荐部署版本（按业务取向 3 选 1，**不输出"唯一最佳"**）：
- 完成率优先 → 选完成率最高的 round
- 不漏客户优先 → 选 greet-only 最低的 round
- 综合 → 在不稳定指令上 pareto 前沿的 round

**F4**. （可选）生成 dashboard.html，含 capability map 可视化 + 每轮分档对比 + bad case 内联完整对话。

**F5**. 终结消息：
> "N 轮完成。能力地图在 `<workspace>/capability_map.md`。
> · 稳定指令 X 条 · 不稳定 Y 条 · 做不到 Z 条
> · end_checker 误判率 N/M（独立于 agent prompt）
> 推荐部署版本见 F3。"

---

# References（按需 read）

按调用顺序：

| Phase | Reference 文件 | 读它的时机 |
|---|---|---|
| A | `references/intake.md` | 每次 skill 启动，按里头问题模板逐问（**必需**只问 prompt/persona来源/3+2 模型 key/N/K/greeting/场景/路径）|
| A | `references/api-call-params.md` | 高级参数（max_turns/temperature/timeout 等）默认值 + 何时调 |
| B | `references/workspace-layout.md` | 建目录/校验目录时 |
| B | `references/persona-sources.md` | Q2 选了"自动生成 persona"时 |
| B/E | `references/model-config.md` | Q3 4 模型配置 schema + 适配代码 |
| C | `references/criteria-extraction.md` | 抽 criteria 的指引 |
| C | `references/rubric-framework.md` | criteria/scoring/math 共享基础 |
| D | `references/smoke-probe.md` | 远端探测 |
| E | `references/scoring-pipeline.md` | 3 层评分细节 + subagent dispatch 模板 |
| E | `references/suggestion-writing.md` | suggestions.md 模板 + 写法 |
| E | `references/prompt-iteration.md` | apply suggestions + token 检查 + diff |
| E/F | `references/iterate-loop.md` | size budget · 三档分类决策树 · trade-off 陷阱 |
| E/F | `references/capability-map.md` | **三档分类标准 + Phase F 主输出格式 + escalation 路径** |
| F | `references/dashboard-build.md` | dashboard.html 生成约定 |
| 通用 | `references/failure-types.md` | 6 hard_fails 闭枚举 |
| 可选 | `references/PORTING.md` | **仅非 Claude Code 宿主需要**（Codex / OpenClaw / Qwen agent 等）。Claude Code 用户忽略 |

## What this skill does NOT cover

- **跑真实通话录音分析**（属管线 B，需要 ASR + TTS 数据，不在范围）
- **一次性 prompt 编辑**（如"把这句话改短"），太轻量不需要 6 阶段流程
- **prompt 创作**（如"帮我写个外呼 agent prompt"），本 skill 假设基准 prompt 已有
- **替代用户判断**：每个 gate 都让用户拍板，skill 不自动跑完

## When to deviate / 例外场景

- 用户**只想跑评分不想迭代**：跳过 Phase E4-E7，Phase F 直接到 F1
- 用户**已有 criteria.json 不想 skill 重抽**：Phase C 改成"用户提供 criteria → 展示 → 进 D"
- 用户**已有 transcripts.jsonl 不想跑远端**：Phase D 跳过，E1 直接读用户给的 transcripts

每个例外，skill 显式问"你是不是想跳过 X 步？"。
