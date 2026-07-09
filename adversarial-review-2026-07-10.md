# 对抗性代码审查报告 — worldcup2026

**日期**：2026-07-10
**范围**：全库代码审查（4 组并行）+ 建模方法论批判（1 组）
**方式**：只读。工作区代码干净（仅 `data/*.json` 运营态漂移，非代码改动）。每条 finding 带 `file:line` 证据，跨组交叉确认项已合并标注。
**铁律**：本报告只诊断，不改代码。修复由用户逐项批准后分批进行。

---

## 一、严重度排序（合并去重后）

### 🔴 P0 — 正在影响今天推单（live 生产正确性 + 资金）

**#1 [B-HIGH2] `is_group_stage` 永不置 False → 淘汰赛强队被误施小组赛波动罚分（LIVE）**
- 证据：`adjustments.py:104` `apply_all(is_group_stage=True)` 默认 True，**全仓库无任何 caller 传 False**（grep 确认零处 `=False`）。`group_stage_volatility`（:70-92）对 `home_elo>1850` 的 favorite 扣 0.12 胜率、灌入平局。
- 影响：**今天（07-10）R16 淘汰赛**，Spain/France/Argentina 等 >1850 强队每场被压 ~12% 胜率、抬高平局。经 `today.py:282 apply_all` 进入正式 Edge/稳单推单，也污染 walkforward.py 淘汰赛注（:201-263）→ v3 基准 +35.8% 亦含此系统性罚分。
- **交叉印证**：这很可能是 CLAUDE.md 记录已久的"40-60% 强队胜率低估"的一个**机制根因**（方法论组独立指出该问题非分布导致）——不是 Poisson 无能，是罚分误施到淘汰赛。
- 定级理由：淘汰赛推单正在进行，live 语义错误，直接影响资金决策 → 提到 P0。

---

### 🟠 HIGH

**#2 [A-HIGH1] kill 关卡用静态 `TEAM_ELO`，模型用动态 live Elo — 两把尺淘汰赛已漂移**
- 证据：`today.py:354-356/466-467/533-535/693-696` 的 kill 判定（near-equal≤100 / Rule②>450 / bus-zone）全读 `TEAM_ELO`（config.py:80 赛前静态）；而 `predict.py:144-153` 的 λ/GSV/DC 用 `get_elo()`（elo_state.json 动态）。同一场 `_gsv_trigger_info`（today.py:176-179）已改用 live Elo，Rule② 仍用静态 → **同一场可自相矛盾**（DC 标注"GSV triggered"而 Rule② 按静态判 KILL）。
- 影响：淘汰赛 live Elo 已多场偏离赛前值，kill 阈值判在过期 Elo 上。

**#3 [C-HIGH1] `today.py --sync` 未封的直写路径，绕过 staging 全部关卡**
- 证据：`today.py:837-841` → `results_sync.run()` → `sync_to_json()`（:154-195）**单源 martj42 直写 `wc2026_results.json`**，绕过①双源交叉验证 ②staging ③人工确认闸 ④replay，且只 `print("建议重跑")` 不触发 replay → 写完 elo_state 立即陈旧。
- 影响：CLAUDE.md 称"单场直写已废弃"只封了 `update_elo.py`（:159-175 确已封），这条平行路径漏封，与"无静默 DB 写路径"核心不变量冲突。

**#4 [A-HIGH2] `--auto-today` 控制台 `[A推单]` 展示未过滤 portfolio，绕过全部 kill**
- 证据：`predict_market.py:271-274` `a_labels` 直取 `predict()` 的 build_portfolio，`:521` 打印，**未经** best_bets_report 的 Rule②/near-equal/OU-fence/calibration_gate（today.py:538-571）。PDF 侧只对 1X2 套了 kill（pdf_report.py:811/845），AH/OU/CS 的 A 注在 auto-today 既不进 PDF 也不过滤。
- 影响：当日唯一入口的控制台会把一条 today.py 本会 KILL 的注原样标成"[A推单]"——正是 CLAUDE.md「推单输出规则」警告的输出不一致。

**#5 [三组交叉确认] GSV 影子追踪器 `except: pass` 静默吞错 → 污染 N≥8 解封计数**
- 证据（三处独立命中，同一病灶）：`today.py:259-273`（A-M2，`_count_oos_gsv_n` 双层吞异常）、`walkforward.py:646-647`（B-MED1，追踪器旁路整体吞）、`gsv_shadow_tracker.py:261-262`（D-MED2，`log_gsv_match` 整体吞，docstring 明写"任何异常静默忽略"）。
- 影响：三处都喂 `UNLOCK_N=8` 的平局/DC 出口**解封裁决**（gsv_shadow_tracker.py:460）。任一处 JSON 坏行/IO 失败被静默丢弃 → 正式 OOS N 被无声少计，解封判断失真。**三组独立发现同类 → 提级 HIGH**。直接违反「禁止静默失效」纪律，且关系资金决策解封门。

**#6 [B-HIGH1] `daily_walkforward.py` AD 状态前视泄漏，"walk-forward"名不副实**
- 证据：`daily_walkforward.py:311` `process_match` 只重置 `_ELO_CACHE`，**从不重置 `_AD_CACHE`** → `get_ad_state()`（poisson.py:38-42）读磁盘最终 AD state（含 7 月淘汰赛）。对照主 `walkforward.py:359-361` 用 `custom_att/def` 注入点对点状态，干净无前视。
- 影响：该文件产出的"28场 walk-forward" ROI 被未来信息污染，不能当无前视证据。

**#7 [D-HIGH1] 资金/下注/回测系统零自动化测试（无回归防护）**
- 证据：全库 `find test_*.py` 零命中，无 pytest/conftest 任何基建。
- 影响：kelly 分仓、value de-vig、AH/OU quarter-line 结算折算（full_backtest.py:44-56）、GSV N 计数——决定下注和解封的逻辑，任何重构无红灯拦截。按项目"资金系统无回归防护 = high"定级。

**#8 [D-HIGH2 + 方法论 A4/A5] 回测前视污染代码层无标注 + kill 规则样本内过拟合**
- 证据：`backtest.py:160-167` 无条件加载最终 AD state、`--use-live-elo` 套最终 Elo，输出头（:222-224）**零前视警告**；`retrospective.py:223/792` 用实时 Elo 预测 6 月场次直接打印"ROI"无标注；`backtest_historical.py:15` 同。对照 `ab_compare.py:112/143-148` 是正确 walk-forward（证明项目有能力做干净隔离，但三个 backtest 脚本没做）。
- 方法论补充：**Brier 0.4122 的前视主项是 Elo（poisson.py:9-24 读最终 elo_state），不是 AD**——CLAUDE.md 脚注"含前视 AD state"归错因（小组赛 AD 全程=1.0，85 场绝大多数是小组赛，AD 前视可忽略）。且 `NEAR_EQUAL_AH/1X2/OU_DIFF`、`UNDER_MKTOVER_KILL`、`HT_DRAW_KILL`、GSV 边界这些 kill 阈值**都是在同一个 28-32 场 walkforward 上用 <10 注子切片拟合**（garden-of-forking-paths），walkforward 对它们是**样本内**，+35.8% 系统性高估真实 OOS。真样本外只有 7-02+ 实盘（N 极小）。

---

### 🟡 MEDIUM

**#9 [D-M1] Kelly 无任何仓位上限（单注/单场/单日敞口不封顶）**
- 证据：`kelly.py:20-27` 仅 `max(0.0, f*KELLY_FRACTION)` 无上钳；`build_portfolio:43-44` 只把单场总仓压到 ≤100% bankroll。实测 `p=0.9,odds=10 → 单注 22.2% bankroll`。`predict.py:268` 每场独立调 build_portfolio **跨场不聚合** → 一天 3 场合计敞口可达 300% bankroll。（今日 today.py 是否再做日级封顶在 D 组范围外，未核。）

**#10 [方法论 A3] 比例法 de-vig 污染每一个 edge**
- 证据：`kelly.py:14-17` `remove_margin` 是 `p/sum` 比例归一，`value.py:44` 全程用它。比例法对三路市场系统性偏差（高估热门、低估冷门）→ `edge=model−market_true`（value.py:9）**每个 edge/ROI/kill 阈值都被污染**。且模型侧做了 FLB 修正（adjustments.py:12-17 压热门），市场侧 devig 自带相反偏差，净效应无人核对。**Shin/power devig 是纯赚的低成本升级，应先于任何分布改动做**。（另 D-L1：单边 devig `value.py:79 /1.06`、`:142 CS /1.15` 是无据 magic number。）

**#11 [C 组多项] db_health 多项"假绿"**
- `db_health.py:225-258` check7（odds 覆盖率）恒 `return ok:True`，唯一变红是文件读异常 → 假体检虚增七项全绿可信度。
- `:112-124` check_duplicates 去重键 `(date,home,away)` 无主客归一化 → **主客翻转重复漏检**（113→85 去重事故正是这类；写入侧 results_sync.py:533-535 反而防了，体检侧没防，不对称）。
- `:200-210` freshness 用 mtime 当"已 replay"证据，touch/checkout 可伪造 → 陈旧 elo 可能通过 `assert_elo_fresh` 硬门。
- `:66-67` martj42 缓存缺失时 check_missing_results 判绿（最需要时反绿灯）。

**#12 [C-M3] commit+replay 失败仍返回成功、退出码恒 0**
- 证据：`results_sync.py:591-592` replay `returncode!=0` 只 print，`commit_from_staging` 仍 return len；`daily_sync.py:289-330 main()` 从不 `sys.exit(非0)`。赛果已写但 replay 崩 → elo 与赛果库不一致，命令却 exit 0 收尾。违反「退出码逐条累计不得丢弃」。

**#13 [A-M1] `python3 today.py` 裸跑仍从封存 MANUAL_MATCHES 出完整推单**
- 证据：`today.py:855-861` stale 判断只 `print` 不 `sys.exit`，随后照常出 Edge/稳单表。predict_market 侧已硬挡裸跑（:688-696），today.py 这个孪生入口没同等守卫 → 模糊指令"跑今天"落此路径会拿 06-28 封存快照出正式推单（2026-07-03 同款事故路径）。

**#14 [方法论 A6] DC rho=-0.20 赛段过拟合 + 三旋钮不可辨识**
- 证据：`config.py:24` rho=-0.20（注释"对应 WC 小组赛实测高平局率"）是 published 估计（-0.03~-0.13）的 1.5-3 倍，按小组赛反拟合。淘汰赛有加时/点球，平局动力学不同 → regime-overfit。叠加 `DC_RHO`+`DRAW_CALIBRATION`+`PROB_SHARPENING` 三个旋钮都在动平局，85 场无法把它们分开辨识。

---

### ⚪ LOW（记录备查）

- [B-L1] GSV 触发逻辑三副本逐字复制（predict.py:162-169 / walkforward.py:348-355 / :627-634），未抽公共函数 → 未来改一处漏两处即重现"双路径不一致"事故。建议抽 `poisson.compute_gsv_scale`。
- [B-L2] `market_model.py:145` solve_lambdas 不校验 `result.success` 即返回；且 :160-167 诊断每次多跑一次 minimize，`--auto-today` 每场翻倍求解成本。
- [B-L3 / D-L2] `poisson.py:153` / `kelly.py:48` 无零/NaN 兜底（当前 λ 恒>0 不触发，防御缺口）。odds=0 时 `kelly.py:48` 抛 ZeroDivisionError。
- [A-L1] `today.py:377` calibration_gate 缺 market_true 时回退含 vig 的 `1/odds`，gap 偏小可能升级注等级，静默无告警。
- [A-L2] `today.py:555-561` near-equal OU 解析失败静默 KILL 且理由误写"line<2.5"（方向保守但复盘误导）。
- [C-L7] `odds_source.py:125` 生产入口 `_extract_h2h` 无价格 sanity check（对照 odds_api.py:128 有 `_valid_price` 1.01-30.0）。
- [C-L9] `results_sync.py:227` `RESULTS_AUTO_COMMIT=False` 死常量，全库无读取处，注释暗示可切换但不生效（脚枪）。
- [C-L10] `daily_sync.py:50-80` `_tag_date_drift_dups` 极低概率误判（DB 存错日期时真实场次可能被当 dup 丢弃）。
- [C-L8] `daily_sync.py:116` 把 API 失败/无赛果/无 key 三态合并为一条信息（降级方向保守）。
- [D-L3] `gsv_shadow_tracker.py:460` 解封用"无水 ROI>5%"，同文件 `_VIG_NOTE` 自述真实 vig 需下调 2-4% → 门槛偏松。
- [方法论 A7-A9] GSV 4 条手画阶跃边界（config.py:56-65，宜换单参数连续衰减）；GSV 压强队 λ 与 AD 的 def 因子淘汰赛起可能同向复合双计"强队兑现不足"，代码无防护；手设 `TEAM_ELO`（config.py:80-130）是最未校验的输入却是一切 λ 的源头，40-60% 低估可能来自 Elo 先验设错而非分布。
- [方法论 A8] 双矩阵 1X2 与 AH 来自互相不自洽的联合分布（P(主胜)_1x2 ≠ AH 隐含 P(净胜>0)），代码"正确实现了设计"但设计本身可能同时推荐矛盾两边。**注：这是设计层 tradeoff，代码实现正确（A/B 组均确认双矩阵按设计工作）。**
- [方法论 A10] 从未做 reliability diagram / Brier 分解，"40-60% 低估"是肉眼分桶估的，动分布前无证据判断病根是校准还是区分度。

---

## 二、正面确认（核过无问题的模块）

跨组交叉确认，以下**符合设计、无缺陷**：

- **F3 GSV 双矩阵**（A+B 双组确认）：`predict.py:155-177` 1X2 走非 GSV 矩阵，GSV 仅 override `ah/over/under` 键；`walkforward._build_mat_custom` production 模式（:378-403）严格对齐。legacy 模式（GSV 灌满含 1X2）是被 `WF_GSV_MODE="production"` 关闭的**死开关**，非活路径。2026-07-03 修复彻底，生产无残留。
- **F3 主客 home_advantage**（A+B 双组确认）：`poisson.py:113-114` 只判 `home_team in {USA,Canada,Mexico}`，`lam` 乘 `mu` 不乘，host 居 away 得 1.0。东道主身份+home 槽位双条件成立，与 CLAUDE.md 裁决一致。
- **F2 predict_market 入口守卫真实存在**（A 组）：`--auto-today`=run_auto_today；裸跑提示后 return 不落手填；`--auto` LEGACY 有显著警告头。均代码实现非仅文档。
- **F2 OU-A/OU-B 无混用**（A 组）：OU 参考仅 stable_bets_report 一处计算，over→index0/under→index1 映射正确。
- **F5 +30h 窗口、两源 AGREE 才 confirmed、已入库比分绝不覆盖、update_elo 单场直写已封**（C 组）：均确认正确。
- **资金公式无致命缺陷**（D 组实测）：负 edge → 钳为 0（非正仓位）；nan/0/负赔率不爆仓（kelly_fraction 本体返 0）；Kelly 用真实赔率而非去水价分仓（正确）；"触发即下"口径无隐藏 edge 门槛（与 CLAUDE.md 一致）；`ab_compare.py`/追踪器 backfill 无前视。

---

## 三、未核清单（如实列出，未猜测凑数）

1. **#1 罚分对 v3 基准 +35.8% 的贡献量级**：未做 `is_group_stage=False` 的淘汰赛注 A/B 回测（只读，逻辑已确证 live）。
2. **#6 前视泄漏的 ROI 数值差**：未实跑 daily_walkforward 对比"重置 _AD_CACHE vs 不重置"。
3. **H2/#4 的实际影响面**：pdf_report.py 是否在别处对 AH/OU A 注补 kill 未通读全份。
4. **#9 今日 today.py 是否有日级/跨场敞口封顶**：M1 缺口只在 predict.py→build_portfolio 层证实，today.py 上层未查。
5. **#3 today.py --sync 实操触发面**：路由表指向 /matchday、/daily-settle，理论上不经此；若确认永不手调可降级为"移除死路径"。
6. `odds_source.fetch_today_matches` 的 `ok/error` 语义（配额耗尽 vs 网络错误）、`market_model` λ 边界 [0.20,4.00] 生产是否触顶、quarter-line 折半阈值数值正确性——均未动态执行确认。

---

## 四、建议修复顺序（资金/live 优先）

**第一批（立即，影响今天推单/资金）：**
1. #1 `is_group_stage` 淘汰赛罚分 —— 需你先拍板：淘汰赛应传 `False` 还是改判据。**这是 factor-workflow 级改动**（动 λ 生成），要走 spec→backtest→确认。修前今天的 R16 推单要知道强队胜率被系统性压低。
2. #5 GSV 追踪器三处 `except: pass` —— 改为 stderr 记录带原因的告警（不中断主流程，但不再静默）。影响解封治理，低风险修复。
3. #2 kill 关卡静态 vs 动态 Elo 口径统一 —— 需你决策：kill 也改用 live Elo，还是保持静态并文档化理由。

**第二批（数据完整性/静默失效）：**
4. #3 封 today.py --sync 直写路径；#13 today.py 裸跑加 sys.exit 守卫（与 predict_market 对齐）。
5. #12 commit+replay 失败退出码累计；#11 db_health 四项假绿修复（尤其主客翻转去重）。
6. #4 [A推单] console 过滤对齐 today.py kill。

**第三批（护栏/证据基建）：**
7. #7 补最小回归测试套（kelly/value/结算折算/N 计数）——纯正收益，先于任何模型改动。
8. #8 backtest 脚本输出加前视警告头 + kill 规则从 walkforward 剥离，重建干净 OOS 度量。
9. #9 Kelly 加单注/单日敞口封顶（需你定阈值：fail-fast 还是 warn）。

**需你先拍板决策的阈值/策略类**：#1（淘汰赛罚分怎么改）、#2（kill 用哪个 Elo）、#9（敞口封顶阈值）、#10（是否换 Shin devig）。

---

## 五、方法论 verdict —— 直接回答"二项/黑暗森林/负二项是否采取"

- **二项分布**：用错工具。进球不是固定 n 伯努利试验，标准是 Poisson（本系统已用 Poisson+DC，正确）。
- **黑暗森林法则**：《三体》的宇宙社会学设定，**不是任何统计预测模型**，无对应公式可"采取"。不投入蒸馏。
- **负二项分布**：**否决**。足球进球很接近 Poisson，过离散是次要问题；且 NB 加大边际方差会**减少**对角线平局质量——与系统要解决的"平局超发"方向相反。

**真正对症的下一步（方法论组排序，均不碰边际分布）：**
1. **换 de-vig 为 Shin/power**（#10）——影响每一个 edge，纯赚，约一天工作量。
2. **测 dead-rubber/game-state 平局协变量**（病根是"用无条件常数 rho 建模条件效应"——平局超发主要发生在双方满足平局的比赛；信号 `news_flags` 现成，只是没进 λ/平局通道）。直击自述"正在亏钱"的平局病灶。
3. **若确要动分布，唯一值得的是 DIBP（对角膨胀双变量 Poisson，Karlis-Ntzoufras 2003）**，不是 NB/普通 BP——它加一个可 MLE 估计的显式对角膨胀参数，能把 rho/DRAW_CALIBRATION/PROB_SHARPENING 三个打架的旋钮收敛成一个，比 DC 4 格 hack 干净。
4. **进 factor-workflow 前的硬门**：先在真 OOS（7-03+ 实盘）画 reliability diagram + Brier 分解（否则不知在修校准还是区分度）；把 kill 规则从 walkforward 拟合集剥离（承认 +35.8% 对它们是样本内）；DIBP 膨胀参数必须能条件化（dead-rubber vs 其它），否则被 regime 异质性吃掉。

**一句话**：别先换分布。先修 #1 罚分 bug（live）+ Shin devig + dead-rubber 平局协变量；分布层若动，只走 DIBP 且必须在 kill 规则冻结后的干净 walkforward 上验证。

---

*报告结束。修复需逐项批准、分批进行、每项带"删掉即变红"的回归测试、每批过一轮对抗复查。*
