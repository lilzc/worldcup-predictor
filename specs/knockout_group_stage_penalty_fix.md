# Spec — #1 淘汰赛误施小组赛波动罚分修复

**来源**：adversarial-review-2026-07-10.md #1（P0，B组 HIGH-2）
**类型**：λ/概率级模型修改 → 走 factor-workflow 强制门（spec→implement→backtest→确认→integrate）
**日期**：2026-07-10

## 改什么

`group_stage_volatility`（adjustments.py:70-92）对 Elo>1850 且占优的强队扣 0.12 胜率（0.7 灌平局、0.3 灌对手），本意是"强队小组赛轮换/试验会 underperform"（back-test 依据：Brazil 1-1 Morocco、Spain 0-0 Cape Verde，**均为小组赛**）。

但 `is_group_stage` 参数在全仓库**无一处传 False**（apply_all 默认 True，adjustments.py:104）→ 罚分被无差别施加到**淘汰赛**。淘汰赛是一场定生死、无轮换动机，此罚分语义错误。

**修复**：淘汰赛场次（`date >= KNOCKOUT_START = 2026-06-28`）传 `is_group_stage=False`，罚分只在小组赛生效。

## 为什么

- 今天（07-10）R16 淘汰赛，Spain/France/Argentina 等 >1850 强队每场被压 ~12% 胜率、抬高平局 → 正在影响 live 推单。
- 方法论组独立判断：这很可能是 CLAUDE.md 记录已久的"40-60% 强队胜率低估"的机制根因之一——**不是分布问题，是罚分误施**。
- 罚分对小组赛有 back-test 依据（保留）；对淘汰赛无依据且方向错（移除）。

## 实现方案

predict() **无 date 参数**（predict.py:117），故需贯穿比赛日期：

1. **config.py**：新增 canonical `KNOCKOUT_START = "2026-06-28"`（当前散落在 results_sync.py:17；令 config 为唯一真源，results_sync 改为 import）。
2. **predict.py `predict()`**：新增 `match_date: str = None` 参数；`is_group_stage = (match_date is None) or (match_date < KNOCKOUT_START)`，传入 apply_all（predict.py:148）。
3. **三个调用点传 date**（date 在各调用处均可得）：
   - `today.py` run_matches / `today.py:303` GSV 段的 apply_all —— match dict 有 "date"
   - `predict_market.py` run_auto_today —— 场次 fixture 日期
   - `walkforward.py:373,385` —— `m.get("date")`
4. **默认行为**：`match_date=None → is_group_stage=True`（保守，不改未传 date 的 ad-hoc 调用行为）。**风险点见下**。

## 预期效果

- 淘汰赛 >1850 强队胜率 +~12%（恢复被压部分），平局 -~8pp，弱方胜 -~4pp。
- 小组赛预测**完全不变**（罚分保留）。
- v3 基准（Brier 0.4122 / WF ROI +35.8%）会变——因为它当前含淘汰赛误罚。backtest 必须对比改前改后。

## 风险 / 副作用

1. **v3 基准位移**：淘汰赛 walkforward 注的 λ/概率变化 → ROI/Brier 变。这是"修 bug 导致基准变"，不是回归；需重跑并记录新基准。
2. **默认 True 的隐患**：若某生产调用点漏传 date，该场仍吃罚分（静默）。缓解：实现时逐一核对三个调用点都传了 date，并加一条 import 冒烟 + 针对性单测（淘汰赛日期 → is_group_stage=False）。
3. **KNOCKOUT_START 双源**：迁到 config 需同步改 results_sync，避免两个 06-28 漂移。
4. **方向未经淘汰赛实盘验证**：信号方向合理（强队不该被压），但淘汰赛样本仍小，backtest 改善若来自极少数场次需诚实标注 N。

## 影响文件

- `config.py`（+KNOCKOUT_START）
- `predict.py`（predict 签名 +match_date，apply_all 调用）
- `today.py`（run_matches / GSV 段 apply_all 传 date）
- `predict_market.py`（run_auto_today 传 date）
- `walkforward.py`（apply_all 传 date）
- `src/data/results_sync.py`（KNOCKOUT_START 改 import config）
- `tests/`（新增：淘汰赛日期 → 强队不被罚分 的红先行测试）

## 验收标准（factor-workflow ③④）

- 红先行测试：淘汰赛日期场次强队胜率 > 同队小组赛日期场次（罚分差可测）。
- backtest 对比表：1X2 准确率 / Brier / Over2.5 / 平均进球（小组赛应完全不变，淘汰赛应变）。
- walkforward 重跑：新的 WF ROI（无前视），与 +35.8% 对比，标注淘汰赛注变化归因。
- **停在 ④ 等 Ryan 确认**，方进 integrate。
