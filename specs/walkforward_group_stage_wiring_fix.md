# Spec — walkforward 生产路径 group_stage 罚分接线补全

**来源**：2026-07-10 knockout_group_stage_penalty_fix 的残欠配线（follow-up）
**类型**：walkforward 扫描逻辑修改 → 走 factor-workflow 强制门（spec→implement→backtest→确认→integrate）；触发 CLAUDE.md commit 纪律（动 walkforward 扫描逻辑）
**日期**：2026-07-23

## 改什么

`walkforward.py:_build_mat_custom`（337-405）的 **production 分支**（line 387-388）调用 `apply_all` 时**未传 `is_group_stage`**，取默认 `True`——即淘汰赛场次照施小组赛波动罚分。

- line 346 已算出 `_is_group = (match_date is None) or (match_date < KNOCKOUT_START)`，但该变量**只在 legacy 分支**（line 376）被消费。
- 呼叫点（line 571-576）已正确传 `match_date=m.get("date")`；`match_date` 一路流入 `_build_mat_custom`，唯独在 production 出口被丢弃。
- 对比 predict.py:150-157（生产路径）已正确接线，两路径不一致。

**修复**：production 分支 line 387-388 的 `apply_all` 补上 `is_group_stage=_is_group`，与 legacy 分支和 predict.py 对齐。

## 为什么

- 2026-07-10 修复把 date 通到了 `_build_mat_custom` 并计算 `_is_group`，但 production 出口的 `apply_all` 没消费它——配线断在最后一步。CLAUDE.md 当时只记了"backtest.py 未接线属预期"，walkforward production 分支这处遗漏未被记录。
- **当前零实际后果**：`MATCHES_ODDS` 全是小组赛场次（2026-06-15~06-27），`_is_group` 恒为 True，接不接线结果逐字相同。
- **未来污染风险**：若把淘汰赛场次加进 `MATCHES_ODDS`，或模型复用到下届赛事，walkforward 的无前视评估会重新受罚分 bug 污染，与 today.py/predict.py 生产路径不一致——违反 CLAUDE.md「双路径一致性是每场案例标准验收项」。

## 实现方案

`walkforward.py` line 387-388，production 分支：

```python
adj = apply_all(home, away, raw["home_win"], raw["draw"], raw["away_win"],
                is_group_stage=_is_group, home_elo=he, away_elo=ae)
```

（唯一改动：新增 `is_group_stage=_is_group`，与同文件 line 376 legacy 分支写法一致。）

回归测试（tests/）：构造一个淘汰赛日期（date >= KNOCKOUT_START）场次，断言 production 分支下强队（Elo>1850 且占优）的 hw **不被扣 0.12 罚分**；同队小组赛日期场次则被扣。可通过对比 `_build_mat_custom` 在 knockout vs group date 下的 `probs["home_win"]` 差值验证。

## 预期效果

- 小组赛回测（当前全部 MATCHES_ODDS）：**完全不变**（`_is_group` 恒 True，罚分照旧生效）。
- 淘汰赛场次（若未来加入）：production 分支不再误施罚分，与 predict.py 生产路径对齐。
- v3 基准（Brier 0.4122 / WF ROI +35.8%）：**不应变**（无淘汰赛场次在 MATCHES_ODDS）。若变 = 意外，需排查。

## 风险 / 副作用

1. **零效果预期**：本修复在当前数据下是 no-op。backtest/walkforward 数字应逐字不变；这本身是验收标准（改前后 checksum + WF ROI 一致证明无回归）。
2. **legacy 分支保持原样**：line 376 已正确，不动。
3. **默认 `match_date=None → is_group_stage=True`**：保守默认不变，与 predict.py 一致。

## 影响文件

- `walkforward.py`（production 分支 apply_all 补 is_group_stage，1 行）
- `tests/`（新增：淘汰赛日期 → production 分支强队不被罚分的回归测试）

## 验收标准（factor-workflow ③④）

- 回归测试通过：淘汰赛日期场次 production 分支强队胜率 > 同队小组赛日期场次（罚分差可测）。
- backtest 对比表：小组赛全场应**逐字不变**（1X2 准确率 / Brier / Over2.5 / 平均进球）。
- walkforward 重跑：WF ROI 与 +35.8% **完全一致**（当前无淘汰赛场次），checksum 不变。
- **停在 ④ 等 Ryan 确认**，方进 integrate + commit。
