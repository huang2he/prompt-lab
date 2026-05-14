# Workspace Layout

skill 在 Phase B 创建的目录结构。**workspace 是项目数据所在；skill 本身只在 ~/.claude/skills/prompt-lab/，不存数据。**

## 标准目录树

```
<workspace>/                                  ← 用户在 Q8 给的路径，默认 ~/prompt-lab-workspaces/<project_id>/
├── config.json                               ← 5 角色模型配置 + 高级参数（包含 API keys，**权限 600 推荐**）
├── leaderboard.json                          ← 跨轮 KPI（每轮跑完自动 append）
├── README.md                                 ← skill 自动写：项目简介 + 配置摘要 + 迭代历史
├── prompts/
│   └── <prompt_id>/                          ← 一个 prompt 项目的整个生命周期（默认 p1）
│       ├── README.md                         ← 该 prompt 简介 + 迭代历史表
│       ├── rubric.md                         ← 4 维 rubric 框架（含权重 + math，跨轮共享）
│       ├── personas/
│       │   ├── pool.jsonl                    ← 该 prompt 的 persona 池
│       │   ├── SCHEMA.md                     ← persona JSON schema 文档
│       │   └── bad_cases/                    ← bad case 备份（待升级为新 persona）
│       └── iterations/
│           ├── dashboard.html                ← skill 生成的可视化仪表盘（每轮重建）
│           ├── round-01/
│           │   ├── prompt.md                 ← 这一轮的基准 prompt（冻结）
│           │   ├── run_plan.json             ← persona 列表 + 每个 K 次 + ASR override
│           │   ├── criteria.json             ← Phase C 抽出的评估标准
│           │   ├── transcripts.jsonl         ← 远端跑回来的对话原文
│           │   ├── auto_check.json           ← Layer 1 客观规则检查（Python regex）
│           │   ├── scores.json               ← 最终评分（Layer 1 + 2 + 3 合并）
│           │   ├── bad_cases.jsonl           ← 失败 transcripts 列表
│           │   ├── suggestions.md            ← 改进建议（Suggester 生成）
│           │   └── diff.md                   ← 与上一轮的 prompt diff（round-01 无）
│           └── round-02/, round-03/...
└── scripts/                                  ← skill 在 bootstrap 时写入的辅助脚本（用户工作区独立）
    ├── run_round.py                          ← 调远端 batch 跑对话
    ├── auto_check.py                         ← Layer 1 客观规则
    ├── prep_judge_batches.py                 ← 切 batch 给 6 subagent
    ├── merge_scores.py                       ← 合并 auto + subagent 评分
    ├── count_tokens.py                       ← prompt token 检查
    ├── update_leaderboard.py                 ← 重建 leaderboard.json
    ├── build_dashboard.py                    ← 重建 dashboard.html
    └── compare_rounds.py                     ← 两轮对比
```

## config.json schema

skill 在 Phase A 后写：

```json
{
  "project_id": "auto-call-20260514",
  "scenario": "外呼销售-汽车线索回访",
  "remote_server": "<必填，用户在 Phase A 提供，如 http://your-dispatcher.example.com:8080>",
  "agent_a": {
    "provider": "openai",
    "model": "qwen-plus",
    "llm_base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "llm_api_key": "sk-...",
    "request": {"temperature": 0.7, "top_p": 0.9, "max_tokens": 280}
  },
  "agent_b": { "...同上格式..." },
  "end_checker": {
    "...同上...",
    "request": {"temperature": 0, "top_p": 1, "max_tokens": 120}
  },
  "judge": {"local": true, "model": "claude-opus-4-7"},
  "suggester": {"local": true, "model": "claude-opus-4-7"},
  "runtime": {
    "max_turns": 20,
    "start_agent": "assistant",
    "min_messages_before_end_check": 6,
    "timeout_seconds": 180
  },
  "greeting": "您好，这边是新车销售线索回访，方便聊两句吗？",
  "iterations_planned": 3,
  "repeats_per_persona": 2,
  "token_ceiling": null   // null = 不限制；用户在 Phase E 末或高级参数里设
}
```

**安全**：建议跑 `chmod 600 <workspace>/config.json` 后再继续。skill 在 Phase B 末尾会建议这步。

## leaderboard.json schema

每轮 merge 后自动更新：

```json
{
  "prompts": {
    "<prompt_id>": {
      "name": "<scenario from config>",
      "rubric_version": "v2",
      "rounds": [
        {
          "round": "round-01",
          "overall_mean": 3.48,
          "pass_rate": 0.596,
          "n_transcripts": 250,
          "hard_fails_total": 91,
          "dim_means": {...}
        }
      ],
      "best_overall": 4.03,
      "best_round": "round-03"
    }
  },
  "updated_at": "ISO-8601"
}
```

## 多 prompt 项目支持

一个 workspace 可承载多个 prompt 项目（如 p1 客服 / p2 销售 / p3 教育）：

```
workspace/
├── prompts/
│   ├── p1/...
│   ├── p2/...
│   └── p3/...
└── leaderboard.json  ← 跨 prompt 横向对比
```

Skill 在 Phase A Q8 问 `prompt_id`（默认 p1），用户可指定。

## skill 永远 read-only 之外的目录

- `~/.claude/skills/prompt-lab/` skill 本身，**永不写入工作区数据**
- `workspace/` 是用户的，skill 在 Phase B 才 bootstrap
- 如果 workspace 已存在 + 有数据 → Phase B 询问继续 / 备份 / 换路径
