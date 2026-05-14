# prompt-lab

End-to-end Claude Code skill for **iterating an agent prompt through automated eval-improve-eval rounds**.

Use cases:
- Outbound voice agents (sales / support / surveys)
- Chatbots / customer support
- Structured task agents (form filling / data extraction)
- Any prompt that benefits from "score → diagnose → improve → repeat"

## What it does

Walks the user through a 6-phase SOP, gating on user confirmation:

```
Phase A · 介绍 + 多轮收集 9 个输入（dispatcher URL / prompt / persona / 5 模型 / N×K / greeting / 场景 / workspace）
Phase B · 建立 workspace + 落盘配置
Phase C · 抽 criteria（评估标准）→ ★ 用户确认
Phase D · 远端完整 smoke probe → ★ gate
Phase E · 主循环 × N 轮（每轮 ★ 给用户看分数 + diff + 问继续/停/微调）
Phase F · 收尾：分数曲线 / 推荐版本 / 生成 dashboard.html
```

3 层探测递进：A.0 healthz / A.3 chat 拿 id / D 完整 smoke。

4 维 rubric（1-5 制）：
- `instruction_adherence` (0.50) — PRIMARY，rule 违反加权
- `goal_completion` (0.25) — business_goal done/partial/none
- `asr_robustness` (0.15) — 仅 ASR 噪声场景
- `naturalness` (0.10) — Judge 主观

6 类 hard_fails 闭枚举：hallucination / out_of_scope_commitment / identity_breach / injection_breach / infinite_loop / early_hangup

## Install

```bash
npx skills add https://github.com/huang2he/prompt-lab --skill prompt-lab
```

或手动：
```bash
git clone https://github.com/huang2he/prompt-lab ~/.claude/skills/prompt-lab
# 重启 Claude Code
```

## 用法

打开 Claude Code，说任一句即可触发：
- "用 prompt-lab 帮我优化这个 prompt"
- "迭代 prompt"
- "我有个 agent prompt 想跑评分"
- "跑 N 轮看 prompt 表现"

skill 会从 Phase A 介绍开始走完整 SOP。

## 你需要先有的东西

1. **dispatcher 服务**：跑双 LLM 模拟对话的 HTTP server（接收 `/chat` 和 `/simulation`）。skill **不内置**任何默认 URL——你需要自己部署。参考服务接口：[AGENT_SERVER_CALL.md](https://github.com/huang2he/prompt-lab/blob/main/AGENT_SERVER_CALL.md)（可选放仓里）
2. **API keys**：3 个对话角色（agent A / agent B / end_checker）都需要远端模型 key（DashScope / OpenAI / Anthropic 等）。Judge / Suggester 可选本地（用 Claude Code 主会话替代）
3. **基准 prompt 文本**：你想优化的那份 prompt

## Skill 文件结构

```
prompt-lab/
├── SKILL.md                          主 SOP 入口
├── references/
│   ├── intake.md                     Phase A 多轮问题模板
│   ├── api-call-params.md            HTTP body 参数 + 高级调节
│   ├── workspace-layout.md           workspace 目录约定 + config.json schema
│   ├── persona-sources.md            3 种 persona 来源（导入 / 从 prompt 抽 / 从 transcripts 抽）
│   ├── rubric-framework.md           4 维公式 + hard_fail 映射
│   ├── failure-types.md              6 类 hard_fails 闭枚举
│   ├── criteria-extraction.md        Phase C 抽 criteria
│   ├── smoke-probe.md                Phase D 完整探测
│   ├── scoring-pipeline.md           Phase E.2 三层评分 + subagent dispatch
│   ├── suggestion-writing.md         Phase E.4 suggestions 模板
│   ├── prompt-iteration.md           Phase E.5 apply + diff + token 检查
│   ├── iterate-loop.md               净计数 + 决策树 + 真实 case study
│   └── dashboard-build.md            Phase F dashboard 生成
└── README.md / LICENSE
```

## 关于具体例子

`references/iterate-loop.md` 中包含一个**实际项目（汽车外呼线索采集）的 5 轮迭代真实数据**作为 case study：

| Round | overall | pass | hard_fails | Δ |
|---|---|---|---|---|
| 01 | 3.48 | 60% | 91 | baseline |
| 02 | 3.58 | 64% | 72 | +0.10 ✓ |
| 03 | 4.03 | 83% | 27 | +0.45 ✓ |
| 04 | 3.73 | 70% | 54 | -0.30 ✗（过严反弹）|
| 05 | 3.93 | 75% | 19 | +0.20 ✓ |

具体的 rule ID 编号、业务词（"汽车外呼/4S店/承接词池"）是那个项目特有。**换其他 prompt 项目时这些都会重抽，skill 框架本身通用**。

## License

MIT — see [LICENSE](./LICENSE)

## 反馈 / 贡献

Issues / PRs welcome at https://github.com/huang2he/prompt-lab/issues
