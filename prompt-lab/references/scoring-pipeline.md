# Scoring Pipeline (Phase E.2)

每轮跑完对话拿到 transcripts.jsonl 后，按 **3 层评分**算出 scores.json。

```
transcripts.jsonl
   ↓
Layer 1 — 客观 rules（Python regex，instant）
   ↓
auto_check.json
   ↓
Layer 2 — 主观 rules + goals + dims（Judge 模型，远端或本地 inline 或 subagent dispatch）
   ↓
judgments.json (× N batches if subagent)
   ↓
Layer 3 — 合并 + 数学（Python instant）
   ↓
scores.json + bad_cases.jsonl
```

## Layer 1：客观 regex 检查

scripts/auto_check.py 跑。每条 agent utterance 应用以下正则套件，找客观可机器判的违规：

### 通用客观 rule（适合大多数对话 agent）

| Check | 实现 |
|---|---|
| 字数上限 | `count_chars(utterance) > threshold` 按 rule.check_hint 阈值 |
| 禁词开头 | `re.match(r"^(禁词1|禁词2)", utterance)` |
| 单独 token | `utterance.strip() in 禁词集` |
| 句尾词 | `re.search(r"(对吗|是吧|喂)[？\.]?$", utterance)` |
| 关键词频次（如承接词） | 全通 sum count of words in 词池 |
| 自报名次数 | regex match count |
| 收尾后重启 | 找"再见"位置 → 检查其后是否还有 agent utterance |
| 输出禁项 | markdown / emoji / 英文 / 占位符 等 regex |
| 数字格式 | 中文数字 / 英文符号 等 regex |

具体 regex 模板见 v1 中 `scripts/auto_check.py`，本 skill 在 bootstrap 时直接复制。

### 重要：FP 防御

某些 regex 容易**误报**：
- "您说" 是承接词也是疑问句引导（"您说的是 X 还是 Y？"）→ 加 lookahead 排除问句
- 中文数字"一下/一定"被当数字 → 用 negative lookbehind 排除常用词
- "是吧" 在 ASR 澄清问句中合规 → 同句含"还是"豁免

每条 regex 加 `confidence: "low" | "medium" | "high"`。merge 阶段对 low confidence 项打折。

### 输出 auto_check.json

```json
{
  "rule_violation_rate_auto": {"r1": 0.067, "r3": 0.144, ...},
  "per_transcript": [
    {
      "transcript_id": "...",
      "rule_violations": {"r1": 2, "r3": 1, ...},
      "per_utt_violations": [{"rule_id": "r1", "turn": 5, "u": "...", "why": "..."}],
      "per_call_violations": [{"rule_id": "r3", "violation_count": 4, "evidence": [...], "why": "..."}],
      "g7_status_auto": "done" | "partial" | "none"
    }
  ]
}
```

## Layer 2：主观判断（Judge 模型）

13 类主观 rule + 7 类 goal + 2 dim + hard_fails，**不能纯 regex 做**，需要 Judge 模型读完整 transcript 语义理解。

### 决策：inline 还是 subagent dispatch？

按 transcripts 数量自动选：
- **≤ 30 通**：主会话 Claude inline 评分（直接读所有 transcripts）
- **> 30 通**：分 6 batch 派 subagent 并行评

用户在 Q3-D 选了**远端 Judge**：HTTP 调远端 → 同样按数量切策略

### Inline 评分模板（小样本）

主会话 Claude 直接读 transcripts 数组 + criteria + rubric，对每通输出：

```json
{
  "transcript_id": "...",
  "persona_id": "...",
  "subjective_violations": [
    {"id": "rX", "n": N, "evidence": [{"turn": T, "u": "原句", "why": "一句话"}]}
  ],
  "goal_statuses": {"g1": "done|partial|none", "g2": "...", ...},
  "asr_robustness": 1-5,
  "naturalness": 1-5,
  "hard_fails": ["..."],
  "notable_moments": [{"turn": T, "issue": "..."}],
  "bad_case_summary": "一句话"
}
```

汇总成 `judgments.json`（无 batch 后缀）。

### Subagent dispatch（大样本）

scripts/prep_judge_batches.py 切 batch → 主会话发 6 个 Agent 调用。

dispatch 模板见下方"6 Agent 调用模板"。

#### 6 Agent 调用模板

每个 Agent 收到（参数化 batch 号）：

```
You are a Judge LLM scoring N transcripts (rubric v2, batch <N> of 6).

═══ YOUR BATCH ═══
- Transcripts (readable): /tmp/prompt_lab_batches_<round>/batch_<N>/transcripts.md
- Persona metadata: /tmp/prompt_lab_batches_<round>/batch_<N>/personas.json
- Objective rules pre-checked: /tmp/prompt_lab_batches_<round>/batch_<N>/auto_check.json

═══ REFERENCE FILES ═══
1. <workspace>/prompts/<id>/rubric.md
2. <workspace>/prompts/<id>/iterations/<round>/criteria.json
3. ~/.claude/skills/prompt-lab/references/failure-types.md
4. <workspace>/prompts/<id>/iterations/<round>/prompt.md

═══ TASK ═══
对每条 transcript 输出：
- 主观 rules 违规（dedupe auto_check 已列项）
- 7 goals done/partial/none
- 2 dims 1-5
- hard_fails closed enum

═══ PERSONA 特殊规则 ═══
- non_engageable persona: 优雅早退 = pass，g1-g6 默认 none 不算 fail
- FAQ 中允许的 deflection（如撒谎兜底回答"你是机器人吗"）不算 identity_breach
- 商用车/范围外 special_exit 不算 early_hangup

═══ OUTPUT to /tmp/prompt_lab_batches_<round>/batch_<N>/judgments.json ═══
JSON 格式见 schema。

QUALITY: 每违规带 turn+原文+why；hard_fails 保守；dedupe auto_check。Validate JSON.
```

### 评分等级锚点（Judge 用）

#### asr_robustness 1-5

仅当 persona.asr_noise != "none"：
- 5：每次 ASR 失真都被 agent 澄清/复述确认，无幻觉
- 4：多数失真处理良好，1 处轻度问题
- 3：混合，一些失真被默默接受
- 2：多处失真被默默接受，agent 从失真中编补
- 1：agent 完全从失真编造（"卡。 体验" → "问界 M5"）

#### naturalness 1-5

- 5：像真人，节奏自然
- 4：大体自然，偶有模板感
- 3：模板感明显但不刺耳
- 2：机械重复
- 1：完全脱节

## Layer 3：合并 + 数学

scripts/merge_scores.py 跑：

1. 读 auto_check.json + 所有 judgments.json
2. 对每 transcript：
   - 合并客观 + 主观违规
   - 按 rubric-framework.md 公式算 instruction_adherence / goal_completion / overall
   - 确定 goal_status (pass/partial/fail)
3. 算 round-level summary（mean / max / min / pass_rate / dim_means / rule_violation_rate / hard_fail_freq / goal_completion_rate）
4. 输出 scores.json + bad_cases.jsonl

scores.json schema 见 rubric-framework.md。

## Rate Limit 恢复

如果 subagent 撞 Claude API rate limit："You've hit your limit · resets X:XX am"：

1. 检查 judgments.json 是否已写出（很多情况 agent 已完成写入再撞限）
2. 有 → merge 可继续；无 → 等 reset 后重派单 batch
3. 长时间跑建议 stagger 启动（每个 agent 间隔 5-10 秒，避免突发）

## 跨轮可比性

同一份 criteria + rubric 跨轮，scores 严格可比。

如果某轮中 rubric / criteria 改了 → bump 版本号 → 后续轮按新版本算，dashboard 分段画。

## 失败模式：Judge 不一致

不同 batch 的 Judge 可能对同类 case 评分不一致。缓解：
- 在 dispatch prompt 里给 1-2 个**清晰的 anchor 例子**（pass 一通 + fail 一通）
- merge 阶段抽样人审 2-3 通 cross-batch check 一致性
- 若发现不一致 → 同 batch 重派单一 Judge 评全部
