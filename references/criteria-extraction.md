# Criteria Extraction (Phase C)

skill 在 Phase C 把 base prompt 拆解成"评估标准"（criteria.json）。后续所有评分都参照它。

## criteria.json schema

```json
{
  "rubric_version": "v2",
  "extracted_at": "ISO-8601",
  "scenario": "<Q7 用户给的场景描述>",
  "business_goals": [
    {
      "id": "g1",
      "desc": "<这通对话 agent 必须完成的事>",
      "prompt_source": "<源 prompt 哪节/行>",
      "checkable_per_call": true
    }
  ],
  "behavior_rules": [
    {
      "id": "r1",
      "desc": "<规则文本>",
      "scope": "per_utterance | per_call",
      "severity": "minor | major | hard_fail_boundary",
      "check_hint": "<给 Judge 看的具体检查方法>",
      "prompt_source": "<源 prompt 哪节/行>"
    }
  ],
  "intent_signals": [
    "<客户行为信号，仅 Judge 参考，不直接评分>"
  ],
  "conversion_signals": [
    "<真转换标志，仅 Judge 参考>"
  ],
  "extra_rules": []
}
```

## Phase C 流程

1. **Suggester 读 prompt**（用 Q3-E 模型，远端或本地）
2. **按下方 prompt 模板抽 criteria.json**
3. **显示给用户**：列出 goals + rules 的清单 + 严重度分布
4. ★ **用户 gate**：审一遍签字才进 Phase D

## Suggester 抽 criteria 的 prompt 模板

```
你是 prompt 评估标准设计师。读这个 agent prompt + 场景描述，按 schema 抽出可评估的标准。

输入：
  prompt: <base_prompt 全文>
  scenario: <Q7 场景描述>

输出严格 JSON，含：

1. business_goals[] — agent **每通对话必须完成**的目标
   - 每条带 prompt_source（节名/行号/原句）
   - 每条 checkable_per_call=true（不能是只能跨多通才能验的 KPI）

2. behavior_rules[] — agent **必须遵守**的硬规则
   - 每条带 scope (per_utterance / per_call)
   - 每条带 severity:
     * minor：风格违反（如多余承接词），不阻塞业务
     * major：偏离主要行为（如未先答后问），损失分但可恢复
     * hard_fail_boundary：致命违反（编造/越权/暴露身份/范围外承诺等），同时触发 hard_fail
   - 每条带 check_hint：具体可执行的检查方法（如"数 agent utterance 中是否含 X" 而非"判断礼貌度"）
   - 每条带 prompt_source

3. intent_signals[] — 客户表达"真有兴趣"的信号（仅参考，不评分）

4. conversion_signals[] — 客户做出"真转换承诺"的信号（仅参考）

5. extra_rules[] — 用户后加的临时规则（一般 []）

注意：
- prompt 中 "禁止/不得/绝不" 句式通常映射为 hard_fail_boundary 或 major rule
- prompt 中"≤N字" "整通≤N次"等数字阈值要在 check_hint 里说清怎么数
- prompt 中"五个不"/"X 个原则"等并列规则：每项独立成一条
- 同源 rule 有 per_utterance + per_call 两面 → 拆 `-a` `-b` 后缀

输出严格 JSON，无注释无 markdown。
```

## 抽完后显示给用户

```
=== Criteria 抽取结果 ===

Business Goals (N 条):
  g1: <desc>
  g2: <desc>
  ...

Behavior Rules (M 条，分布)：
  HF (hard_fail_boundary): <count>
  MAJ (major):              <count>
  MIN (minor):              <count>

🔴 hard_fail_boundary rules:
  r1 [per_utterance] <desc>
  r3 [per_call]      <desc>
  ...

🟡 major rules:
  ...

⚪ minor rules:
  ...

Intent signals (P 条): ...
Conversion signals (Q 条): ...

满意吗？
(yes) → 进 Phase D 远端探测
(改 X) → 修改某条 rule 后重显
(重抽) → 让 Suggester 重新生成
```

## ★ Hard Gate

如果以下任一为真，**拒绝进入 Phase D**：
- `business_goals` 空（agent 没目标 = 评不出 goal_completion）
- `behavior_rules` 空（agent 没约束 = 评不出 instruction_adherence）
- prompt 完全没识别出场景（罕见，但发生时 Suggester 输出会很空）

写 `criteria_extraction_failed.md` 到 round-NN/，告诉用户："Suggester 没抽出有效 criteria。可能原因 X Y Z。让我看你 prompt 想测哪些维度，或者重新组织 prompt 后再来。"

## 多次抽取 / 迭代

- 第 1 次抽完用户不满意 → 用户给具体反馈 → 再让 Suggester 重抽
- 用户给"已有 criteria.json" → skill 跳过抽取，直接验证 schema 后用

## 跨轮 criteria 演化

- round-01 抽一次后，**round-02+ 默认复用同一份 criteria**（因为 prompt 改了但 rule 集合大多不变）
- 如果某轮 prompt 大改（结构变了/加了新章节）→ skill 提示"prompt 结构变了，要重抽 criteria 吗？"
- 如果 bad case 暴露**新 failure pattern** 不在现有 criteria 内 → 自动加进 `extra_rules[]`，并提示用户

## Suggester 是远端还是本地

- 远端（Q3-E 选了远端）：HTTP 调 Suggester API（claude-api / openai api / DashScope）
- 本地（Q3-E 选了本地）：直接由主会话 Claude 读 prompt → 写 JSON

两者输出完全一样的 schema。skill 在调用时透明切换。
