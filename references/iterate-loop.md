# 迭代循环策略（Phase E 主循环 + 决策树）

> ⚠️ **Disclaimer**：本文档"真实数据案例"中出现的 rule ID（r11/r13-a 等）+ p1 具体场景描述（汽车外呼/承接词/小李）+ 5 轮分数轨迹（3.48 → 3.93）来自**一个具体实例项目**。换 prompt 项目时这些数字和 rule ID 完全无关。
>
> **通用的是**：净计数定义 / 决策树 / token budget 管理 / 何时停止 / 陷阱模式。

## 净成功迭代的定义

用户常见目标："连续 N 次评分提升"。本系统采用**净计数**模型：

```
delta(round_N) = overall_mean(round_N) - overall_mean(round_N-1)

if delta > 0.05:  净计数 +1
if delta < -0.05: 净计数 -1
if |delta| ≤ 0.05: 净计数不变（视为持平）
```

退步轮"扣分"机制保证迭代真有方向，不只是堆轮次。

## Round-to-Round 决策树

每轮跑完 + 评分后，skill 按这张表分类，然后写下一轮的 suggestions：

### Case A · 提升（delta > 0.05）✓

- 路径正确。Suggestions 用以下优先级：
  1. **保持已修复**：suggestions.md 必须有"Nothing to change"区显式列已健康项防回归
  2. **打下一座山**：抓 rule_violation_rate 新 top → 写 surgical change
  3. **维度补强**：若 asr_robustness / naturalness 仍 < 3.5，加针对性优化
- Token budget 谨慎，每轮 +200~300 tokens 内最稳

### Case B · 持平（|delta| ≤ 0.05）→

- 视为没动。Suggestions 必须**深挖原因**：
  - 上轮 proposed change LLM 没遵守？→ 加强措辞 + 加示例
  - 修复了 X 但同时新引入 Y 违规？→ 看 rule_violation_rate 矩阵看抵消处
- 不要轻易再加新 change—— 先理解为什么没动

### Case C · 退步（delta < -0.05）❌

**必须 ANALYZE，不是加新 change**。

skill 在 suggestions.md 引导 Suggester：
1. 对比 round-N 与 round-N-1 的 `rule_violation_rate` → 找 regression rule
2. 看 dim_means 哪个跌（特别注意 naturalness——常被过严规则副作用拖累）
3. 看 hard_fail_freq 是否降了但其他维度跌得更多（trade-off 过头）
4. **可选回滚**：明显是某条 change 引起 regression → 在 round-(N+1) 直接还原该条；diff.md 标 "REVERTED change #X"

净计数 -1 → 需要后续轮**双倍补回**。

## 常见 Trade-off 陷阱

观察到的真实陷阱（来自 p1 5 轮实战）：

1. **强化某 rule 澄清 → 自然度跌**：agent 变得"每个边界都问"，体感像机器。修：澄清话术保持极简，仅高风险场景触发
2. **强化收尾固定句 → 客户感觉敷衍**：客户拽回时 agent 死守固定句。修：固定句最多 1 次，之后真沉默
3. **扩大重复升级路径 → 提前收尾增多**：agent 第 3 次循环就退，但有些场景客户其实在思考。修：第 3 次只在"客户未给任何新信息"时才收尾
4. **加 ASR 残片白名单 → 漏识别新残片**：硬编码列表跟不上 ASR 模型演化。修：用启发式描述（"碎片/单字/纯数字"）而非穷举词
5. **过严措辞反弹（最严重）**：写"任何 ≥X 字段+对吧 都算模板" 等过宽规则 → LLM 反向避免合规复述 → 整体退步 -0.30。修：保留宽容措辞 + 显式给"单字段口头确认允许"出口

## Token Budget 管理

**仅当用户设了 token_ceiling 才管**。如果 `config.token_ceiling = null`（默认），prompt 自由膨胀，但 skill 在每轮末尾提醒"prompt 现在 N tokens 了，要不要现在设上限？"

设了上限后，每轮可加量 ≈ (token_ceiling - 当前) ÷ 剩余预期轮次

举例（用户设了 5000）：
- round-02 = 4424 / 5000，目标做满 5 轮
- 剩余预算 = 576 tokens
- 剩余轮次 = 3
- 平均每轮可加 192 tokens

实战：每轮 +300 tokens 都会快撞顶。**必须 trim 来腾空间**。Trim 优先级：
1. FAQ 表重复回答合并
2. 钩子句库重复表述抽公因式
3. 详细解释段落紧凑化
4. 重复强调（"绝对禁/严禁/不得" 堆叠）→ 一次说清

每条 proposed change **必须**附 token delta 估算和"若超 budget 配套 trim 哪段"提案。

## 何时停止迭代

理论无限但实际有边际递减：
- pass_rate ≥ 90% 后继续收益骤降
- overall_mean ≥ 4.5/5 后边际改进多变成 trade-off（修一个伤一个）
- token budget 撑爆且无法 trim
- 没有新 failure pattern（rule_violation_rate top 项都 < 5%）

到 **3 净成功**后通常已接近稳定区。再迭代建议改 rubric（升 hard_fail 严格度）或扩 persona pool 加新攻击面。

## 数据演进的统计原则

- **同 run_plan 跑同 rubric** = 跨轮分数严格可比
- **rubric 升 v3** = 后续轮按 v3 单独画曲线，不跟 v2 混
- **persona pool 加 +5 条**通常仍可比（影响小）；加 +50 条则单独画段
- **avg overall** 是核心 KPI；**pass_rate** 是产品视角；**rule_violation_rate 热力图**是设计师视角

## 用户每轮的决策点（★ Gate）

Phase E 每轮跑完，skill 给用户 3 选 1：
- **继续**：进 round-(N+1)
- **停**：进 Phase F 总结
- **微调本轮 prompt**：用户给具体修改指令，skill 应用后重 diff 给用户看，再问

## 真实 5-轮分数轨迹（实例）

p1（汽车外呼新车报价对接）完整实战：

| Round | overall | pass% | hard_fails | naturalness | 备注 |
|---|---|---|---|---|---|
| 01 | 3.48 | 60% | 91 | 3.12 | baseline |
| 02 | 3.58 (+0.10) | 64% | 72 | 2.99 | ASR 澄清 / 信息确认前置 / 收尾固定句 |
| 03 | **4.03 (+0.45)** ⭐ | **83%** | **27** | 3.03 | 多 change 协同跳升 |
| 04 | 3.73 (-0.30) ❌ | 70% | 54 | 2.90 | 收紧某规则过严反弹 |
| 05 | 3.93 (+0.20) | 75% | **19** | **3.23** ⭐ | 回退过严措辞 + 保留好改动（终版） |

**净计数**：0 → +1 → +2 → +1 → **+2 (final)**

观察：
- 单轮跳升最大 +0.45（多 change 协同）
- 单 rule 改太严就整体退 -0.30
- 修了 regression + 保留好改动后 hard_fails 创新低（19），naturalness 创新高（3.23），但 overall 没回到峰值（round-03 4.03 vs round-05 3.93）
- **"最稳健 ≠ 最高分"**：peak round 不一定是 deploy 选择，取决于业务优先级

**关键学习**：
- 每轮 prompt 改动控制 2-3 项内
- 每项 estimate 副作用
- 过严措辞陷阱要警惕
