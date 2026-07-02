# worldcup2026 模型开发规则

## 新因子/模型修改 — 强制流程

做任何新因子、参数调整、新数据源、新市场建模，必须按以下顺序走，**不允许跳步，每步完成前不进下一步**：

```
① spec       → 写清楚：这个因子改什么、预期效果、可能的副作用
② implement  → 改代码
③ backtest   → 运行 python3 backtest.py，对比改前改后的指标
④ 等我确认   → 把回测结果给我看，等我说"可以"才进下一步
⑤ integrate  → 确认后才算正式合入
```

直接跑回测之前没有 spec 的改动 → 打回重写 spec。
回测没过我确认就继续推进 → 打回。

## Git 提交纪律（2026-07-03 立项）

**每次 spec 级改动完成验证后必须单独 commit，不允许攒批。**

触发条件（满足任一即需 commit）：
- 动 `config.py` 任何参数
- 动 `today.py` kill 逻辑（calibration_gate / 近平规则 / Rule②）
- 动 `predict.py` / `src/models/` λ 生成或 AD 因子
- 动 `walkforward.py` 扫描逻辑

commit message 格式：
```
<动了什么>: <一句话说改了什么>

验证: <Brier/ROI 结论一句话，或"N=X 无统计效力"，或"范围bug修复无ROI目标">
```

**理由**：fence zone 修复（OU_FENCE_WITH_ELO True/False）这类开关 A/B 对比，必须有干净的独立提交才能回退到对照基准。没有 commit 就没有回退点，参数迭代的历史证据也无法追溯。

每次 commit 后同步 push 到 origin（`git push`）。本地 commit 不 push = 没有远程备份，断电/误删无法恢复。

## 参数边界（不经 spec + 回测不能动）

- `BASE_GOALS`：当前 1.32，改动需要回测 Over2.5 准确率对比
- `ELO_SCALE`：当前 400（2026-06-22 从550降至400，Brier 0.4983→0.4761，需回测Brier Score对比）
- `DC_RHO`：当前 -0.20（2026-06-22 从-0.13调整，对应WC小组赛实测高平局率，改动需回测Brier Score）
- `PROB_SHARPENING`：当前 1.3（2026-06-22 新增，>1压缩尾部概率，改动需回测Brier Score）
- `WC_GOAL_DISCOUNT`：当前 1.0，任何非1.0的值需要书面理由
- `KELLY_FRACTION`：当前 0.25，改动需要说明 bankroll 风险敞口变化
- `MIN_EDGE`：当前 0.03，降低需要说明假阳性增加的代价
- `ARTIFACT_GAP`：当前 0.08（2026-06-22 从0.12降至0.08，复盘Uruguay AH -1.25/-1.5/-1.75 HIGH全输），改动需对比历史AH推荐命中率
- `GSV_LAMBDA_FACTOR`：当前 0.80（2026-06-22 新增，Elo>1850强队在Elo差150-300挫败带λ×0.80，只影响AH/O/U矩阵，1X2 Brier不变），改动需回测O2.5准确率 + AH -1.5方向准确率对比
- `GSV_LAMBDA_ELO_MIN/DIFF_MIN/DIFF_MAX`：当前 1850/150/300，与 GSV_LAMBDA_FACTOR 联动，单独调整需同时回测
- `NEAR_EQUAL_AH_DIFF`：当前 100（2026-06-24新增，Elo差≤100时禁止AH Edge推单）。回测根据：28场walk-forward中diff≤100共6条AH注，5LOSS/6bets=83%亏损。改动需对比Edge ROI（v4基准：23注 ROI +46.1%）。
- `NEAR_EQUAL_1X2_WIN_DIFF`：当前 100（2026-06-24新增，Elo差≤100时禁止1X2 Win方向Edge推单）。回测根据：28场walk-forward中diff≤100共3条1X2Win注，3LOSS/3bets=100%亏损（Croatia Win/Turkey Win/Sweden Win全错）。改动需对比Edge ROI（v4基准：23注 ROI +46.1%，P&L +10.60）。
- `HT_LAMBDA_FACTOR`：当前 0.46（2026-06-24新增，WC 2026实测46%进球在上半场），改动需验证England HT胜率落区40-48%且Portugal HT胜率落区55-65%
- `HT_DRAW_KILL_ELO_DIFF`：当前 200（2026-06-24新增，Elo差≥200时禁止推HT平局）。回测根据：24场回测Spain/Saudi(diff≈250)、France/Iraq(diff≈372) HT平局全输；Ecuador/Curacao(diff≈163)HT平局WIN，200是两者中间分割点。改动需对比HT推单 ROI（基准：15注 6W/9L ROI +82.1%）
- `AD_ENABLED/AD_SHRINKAGE_K/AD_BLEND_WEIGHT/AD_MIN_MATCHES/AD_CAP_LO/AD_CAP_HI`：2026-07-01新增，当前 True/8/1.0/3/0.60/1.60。**小组赛全程无效（MIN_MATCHES=3，每队最多n=2赛前记录）**；淘汰赛起生效（n=3+）。改动需对比淘汰赛场次λ偏离量和实际ROI；backtest那组Brier改善含前视不算有效证据。AD_MIN_MATCHES降到2代价>收益（K=8收缩压死）。
- `NEAR_EQUAL_OU_OVER_DIFF`：当前 100（2026-06-28新增，Elo差≤100时禁止OU Over line<2.5推单）。三方案回测（18场walkforward+2场06-27实证）：A=Kill全部ROI+58.1%；B=不加规则ROI+51.5%；C=Kill line<2.5 ROI+59.5% P&L+1131（最优）。历史近平场次Over线≥2.5全部WIN（England/Croatia 2.75、NZ/Egypt 2.5、Norway/Senegal 3.0），06-27 Uruguay/Spain Over 2.25 LOSS被正确Kill。改动需对比diff≤100场次OU Over分线段ROI。
- `OU_FENCE_WITH_ELO`：当前 True（2026-07-02从False改为True）。**开启依据：范围 bug 修复，不追 ROI。** 原实现将"均衡场次Elo差±50"的 fence zone kill 泛化到所有场次（无Elo差条件），方案A还原设计意图：仅 `abs(Elo差)≤FENCE_ELO_DIFF_CAP` 时触发 fence kill。X=50 来自原始依据字面表述"Elo差±50"，非扫描拟合。验证：近平保护完整（3/3 LOSE 仍被kill），Brier 0.4296 不变，无前视引入，N=7 诊断仅证明无恶化不证明收益。若7注回流结果反转，开关不因此回滚。改动需说明原始设计意图变化，不能以ROI为由回退。
- `FENCE_ELO_DIFF_CAP`：当前 50（与 OU_FENCE_WITH_ELO 联动）。X=50 来自"均衡场次Elo差±50"原始设计依据，非扫描拟合。改动需提供新的设计依据字面来源，不能扫描历史ROI拟合。
- `DRAW_MIN_PROB`：当前 35%。`DRAW_MIN_EDGE`：当前 7%。**⚠ 两参数挂起，等待统一评估，见"平局生成与表达问题（收敛病灶）"备忘条目，不单独修。**
- `WIN_MIN_PROB`：当前 25%（1X2胜注模型概率低于此值时kill）。**来源不明：无回测依据，CLAUDE.md首次记录**。2026-07-03对账：walkforward 28场无[20-25%)触发，规则未实际执行。逻辑可辩护（低概率高赔率注方差大），但25%阈值本身未经验证。未来调整须先立spec走fence zone同款流程。
- `ARTIFACT_KILL`：当前 20%（模型-市场gap≥20%时硬杀，比ARTIFACT_GAP=8%更严格的一层）。**来源不明：无回测依据，CLAUDE.md首次记录**。2026-07-03对账：walkforward 28场 0次触发（gap≥20%零命中）。ARTIFACT_GAP=8%的来源是Uruguay AH案例，但20%是另行添加的数字，来源无记录。未来调整须先立spec走fence zone同款流程。
- `UNDER_MKTOVER_KILL`：当前 52%（Under盘：市场Over隐含≥52%且模型Under>50%时kill）。**来源不明：无回测依据，CLAUDE.md首次记录**。2026-07-03对账：walkforward 28场触发2注（Germany/Ivory Coast 小2.75 LOSE，Ecuador/Germany 小2.5 LOSE），两注均为LOSE，kill方向正确，但N=2无统计效力。逻辑可辩护（市场强烈看涨时模型押Under可能是GSV低估进攻artifact），但52%和50%两个阈值均无文档来源。未来调整须先立spec走fence zone同款流程。

## 当日 PDF 入口规则（强制，2026-07-03）

**当日预测唯一入口：`python3 predict_market.py --auto-today`（the-odds-api 拉赔率+自动情报+PDF）。**
`MANUAL_MATCHES`（`today.py` 手填区）为过期小组赛快照，2026-07-03 封存，勿更新勿新增。
`predict_market.py --auto` 为 LEGACY 重放模式（有显著警告头），**禁止用于当日预测**。
裸跑 `python3 predict_market.py` 现已重定向至提示，不再默认落入手填路径。

**事故记录（2026-07-03）**：模糊指令"跑今天的 PDF"落入 `today.py` 老路径，触发向用户索要人肉赔率数据，属系统性流程错误，非用户操作问题。

## 推单输出规则（强制）

**所有正式推单必须来自 `python3 today.py`，不允许用临时脚本或 ad-hoc 代码替代。**

- Edge推单 = today.py 的 `best_bets_report` 输出
- 稳单推单 = today.py 的 `stable_bets_report` 输出（1X2最高模型概率方向）
- OU参考 = stable_bets_report 中的 OU-A（最高model概率≥0.48），仅参考不计盈亏
- CS参考 = stable_bets_report 中的前3波胆，仅参考

违反此规则后果：之前会话出现过 OU-A/OU-B 混用导致输出不一致，属于系统性错误。

## 下注流程（每个分析日）— 混合策略（2026-06-23 固化）

标准推单流程：

```
① 赛前情报搜索（运行today.py之前必做）：
   对每场比赛搜索：
   - "[队名] lineup rotation World Cup 2026"
   - "[组别] standings [日期]"（判断出线/生死战情况）
   情报类型：rotation_home/away（≥5人换）| dead_rubber_*（已出线）| must_win_*（负则出局）| injury_note（主力缺阵）
   有情报 → 填入MANUAL_MATCHES对应场次的 news_flags 字段（today.py会自动展示banner）
   无情报 → 不填，不影响输出
② python3 today.py → 生成候选注单（MED/LOW已自动分级，news_flags自动展示）
③ 混合策略红队过滤（today.py已内置规则②，其余需人工判断）：
   规则①: gap ≥ 8%（LOW ⚠）→ today.py per-match显示供参考，不进最终推单表（已代码固化）
   规则②: Elo差 > 300 且无GSV → today.py自动KILL全场（已代码固化，France/Iraq案例验证）
   规则③: Elo严重失真（新军/改制队）+ 有显性证据 → 人工判断是否override MED
   规则④（新）: news_flags有rotation/dead_rubber → 人工判断是否压制该场OU Over（尤其line≥2.5的边境单）
④ MED注（gap < 8%，✓ 或 ⭐ 标记）：不让红队推翻，除非规则②③④有显性证据
⑤ 同一场多条相关AH线通过：只取edge最高的一条下注（避免同场过度集中）
⑥ 输出推单 + Kelly仓位
```

**回测验证（20场，06-16至06-22）：**
- today.py: 17注 12W 5L ROI +87.4%
- 混合策略: 16注 12W 4L ROI +99.1%（规则②杀掉 Brazil/Haiti +330差无GSV，实际3-0正确KILL）

**v1 vs v2 walk-forward 参数对比验证（2026-06-24，28场 walk-forward Elo 无泄漏）：**
- v1（pre-06-22：ELO=550,RHO=-0.13,无GSV）: Brier 0.4969, O2.5 50.0%, Edge -3.4%(46注), OU -7.9%
- v2（当前：ELO=400,RHO=-0.20,GSV=0.80）: Brier 0.4408, O2.5 62.1%, Edge +12.3%(32注), OU +5.2%
- 结论：v2 全面优于 v1，06-22 参数批量迭代有效。Brier 改善 0.056，O2.5 准确率 +12.1pp。

**v3 walk-forward 黑单优化验证（2026-06-24，28场 walk-forward）：**
- 新增：NEAR_EQUAL_AH_DIFF=100（近平场次AH压制）+ Rule④同向1X2-AH去重
- v2 → v3: Edge ROI +12.3%(32注) → +29.2%(26注)，削减6注（5LOSS/1WIN）
- 稳单/OU参考不受影响（分别+4.0%/+5.2%不变）
- 唯一代价：Ghana/Panama AH -0.5 WIN +1.35被压制（保守trade-off）

**不再强制走 `/analyze-slate` 全红队流水线**（全红队对MED注过度保守）。
`/analyze-slate` 仅在赛前情报特别复杂（伤停/政治/战术突变）时手动触发。

## 已知偏差备忘（红队必查）

- **1X2独赢方向历史命中率显著低于OU/AH（walkforward 2026-07-03：主胜33%/客胜0%，小样本N=3/1）**：这是已放行注的命中率，指向"放行的1X2胜注质量差"——若有含义，方向是kill不够严或模型在独赢方向弱，而非"kill偏保守"（后者逻辑反了）。WIN_MIN_PROB在整个数据集零触发，无从谈保守。积累样本后优先审计1X2的edge兑现率，而非放松kill。
- Over2.5 回测准确率 61.1%（54场，GSV λ修正后，ELO=400/FW=0.10/GSV_PENALTY=0.12/GSV_LAM=0.80）→ 大球注仍需红队审
- λ 下调陷阱：历史上曾系统性低估进球（WC_GOAL_DISCOUNT bug）→ 每次校准参数都要对比市场隐含 λ
- 门将黑天鹅：库拉索 Eloy Room 15扑 0-0 → 极端情况不可建模，大赔率冷门警惕
- 强队打弱队趋向大球（日本4-0突尼斯、荷兰5-1瑞典）→ Elo差 >150 时不要主动押小球
- GSV过拟合风险：GSV_PENALTY=0.12 拟合了比利时连续两场小组赛平局，未来赛段不一定适用
- 40-60%校准区间持续低估：模型在此区间预测46-55%但实际70-73%，对中等强度优势队注意赔率
- FORM_WEIGHT=0.10：形态因子权重减半（从0.20），俱乐部状态在WC迁移率低
- 20-30%校准区间结构性偏差：模型在此区间分配24%，实际发生率9.8%（41样本）——平局预测与实际平局触发事件不相关，这是Poisson模型对平局的结构性局限，参数调整无法根治
- **平局生成与表达问题（收敛病灶，2026-07-03合并三条挂起项）**：三个现象同源，解封条件统一，届时立**单个spec**一起评估，不分开修。
  **优先级声明**：①③平局问题 = **正在亏的钱**（DRAW_MIN_PROB/DRAW_MIN_EDGE 在≥30%区间方向已反向，每次有理由推平局注时系统反而kill）；②GSV/DC出口 = **可能漏赚的钱**（DC无真实盘口，假想ROI不代表可兑现收益）。解封当天按此顺序：先裁决平局参数，再裁决DC出口，不允许DC话题先上桌。
  1. **平局分区间画像（分布根因）**：20-25% 高估（实际0%）/ 25-30% 准确 / 30-35% 低估(+4.8pp) / 35-40% 低估(+5.4pp)。高估仅在20-25%区间，30%以上全面低估。`DRAW_MIN_PROB=35%`的"Poisson高估补偿"设立依据在≥30%区间已反向，规则执行方向与实证矛盾。`DRAW_MIN_EDGE=7%`来源"高估补偿"，同样须与DRAW_MIN_PROB一起重评。当前不动原因：样本N=37不足 + 两参数双重压制下独立信号无法量化（"候选少"是本规则压制结果，不得用循环论证规则无害）。
  2. **GSV压λ后概率质量灌入平局（表达错位）**：GSV触发时强队λ×0.80，Poisson把概率质量转移到平局方向。**✓ 已修复（2026-07-03）**：walkforward._build_mat_custom之前将GSV应用于完整矩阵（含1X2），现已对齐predict.py双矩阵设计（GSV只覆盖AH/OU，1X2用非GSV矩阵+apply_all）。修复后Turkey-USA: WF平局32.5%（vs旧路径39.5%），DC edge=-4.8pp（vs旧+7.5pp），WF ROI从+28.8%(31注)→+24.5%(33注)（差值4.2pp为旧污染贡献虚高）。today.py的DC非共识标注因此在Turkey-USA不触发（生产路径DC=-4.8pp<7%阈值）。4场A-B分歧比赛（Netherlands/Sweden, Paraguay/Australia, Norway/France, Colombia/Portugal）全为diff 58-120的**非GSV触发场次**（diff<150），是基础Poisson在中等Elo差段的结构性平局高估，与GSV机制无关。**猜想重述**：GSV若扩展至1X2，Turkey-USA DC边际=+7.5pp（测试假设，不是当前系统行为）。
  3. **DRAW_MIN_PROB泛化路径**：待淘汰赛平局样本积累后重跑分区间统计，若低估图案持续 → 下调DRAW_MIN_PROB至~25-28%，同步重评DRAW_MIN_EDGE，同步评估DC出口可行性，走fence zone同款流程（spec→implement→walkforward对比→确认→integrate）。
  **解封条件（沿用GSV追踪器门槛）**：2026-07-03后新增GSV触发场次≥8且样本外DC假想ROI为正（**口径=触发即下**，有赔率即计入，无edge门槛，与追踪器代码一致；不得事后切换口径）。届时按优先级：先裁①③平局参数，后裁②DC出口，三件事同一spec不分开动。
- AH raw-Poisson λ膨胀：Elo差150-300区间已用GSV_LAMBDA_FACTOR=0.80修正（Uruguay 2.31→1.85，Belgium 2.18→1.75），Elo差>300场次（Spain vs Qatar等）修正不触发，仍依赖ARTIFACT_GAP=0.08拦截
- 均衡场次O/U fence：Elo差±50范围内7/7场Under 2.5（0球到2球），O/U fence zone已配置[44%,57%]拦截
- 大Elo差（170-330）平局频率33%：模型平均低估到20%——强队对弱队非线性防守效应（巴士战术），AH -1.5未覆盖率39%（7/18场），Elo差200-330是高危区间。稳单在此区间直胜判断6/6全亏（Belgium×2, Portugal, Ecuador, Uruguay, England），GSV修正了Edge/OU但未修正稳单1X2方向。
- **参数已达本地最优（2026-06-22 全参数扫描确认）**：ELO_MIN(1750/1800)、DIFF_MAX(360/400)、DRAW_CALIBRATION(0.75-0.95) 全部测试均不优于当前。DRAW_CALIBRATION尤其逆向：模型draw均值24.5% < 实际29.6%，属于低估而非高估，压缩只会变差。不要再尝试单独调这三个参数。
- Poisson结构性盲区：无法建模弱队主动切换防守战术（Spain 0-0 Cape Verde diff+330，Ecuador 0-0 Curacao diff+210）。这两个失败案例不是参数问题。未来改善需要新数据源（弱队防守历史、赛段系数），不是参数调优。
- HT市场（2026-06-24新增）：`HT_LAMBDA_FACTOR=0.46`（WC 2026实测46/100球在上半场，33场样本）。HT 1X2/OU/AH为纯参考输出，不进Edge推单，不计盈亏。HT平局率结构性偏高（约38-42%），市场通常高估HT主胜概率。需在MANUAL_MATCHES加`ht_1x2_odds`/`ht_ou_odds`字段才显示HT分析。HT_LAMBDA_FACTOR改动需同时验证England HT胜率落区（目标40-48%）和Portugal HT胜率落区（目标55-65%）。
- HT平局KILL（2026-06-24新增）：`HT_DRAW_KILL_ELO_DIFF=200`。Elo差≥200时HT平局edge自动归零，today.py显示"[HT平局KILL:Elo差过大]"标注。回测24场：KILL后ROI从+60.6%→+82.1%（15注 6W/9L）。剩余未能覆盖的损失（Canada/Qatar diff=164、Netherlands/Sweden diff=122）为模型固有误差，不通过继续降阈值解决（会误杀Ecuador/Curacao diff=163的WIN）。
- **攻防分解AD（2026-07-01新增，小组赛全程无效）**：`AD_ENABLED=True`，参数 AD_SHRINKAGE_K=8/AD_BLEND_WEIGHT=1.0/AD_MIN_MATCHES=3/AD_CAP_LO=0.60/AD_CAP_HI=1.60。**三条已知限制：**
  1. AD_MIN_MATCHES=3 → 本届小组赛（含MD3）全程因子=1.0，对小组赛任何预测零影响；1.32 tempo几何均值锁死问题在小组赛阶段仍未解决。
  2. 攻防分解在淘汰赛起生效（每队已有≥3场小组赛记录）。λ偏差量级：如Norway/France Over2.5 55.6%→78.4%，England/Colombia 52.5%→37.4%，信号方向合理但无真实淘汰赛结果可验证准确率。
  3. backtest 显示 Brier 0.4854→0.4296（含前视：用了全72场最终AD state）——这个数字含前视污染，**不作为AD因子有效性的证据**。干净证据以无前视walkforward/实际淘汰赛结果为准，当前状态为"淘汰赛信号待验证"。
  - AD参数改动需同时验证：AD_BLEND_WEIGHT改动对比淘汰赛ROI；AD_SHRINKAGE_K改动对比λ偏离量；AD_MIN_MATCHES降低需说明噪声代价（n=2+K=8收缩后因子偏离量≤20%，代价高于信号）。
- **Turkey-USA 案例（第60场，2026-06-26 重放）：** GSV 触发（USA Elo 1869, diff=158）后 A 与市场大分歧（USA胜 40.8% vs 市场48.3%；平局 32.5% vs 25.1%，生产路径修复后），分歧方向被实际结果支持（Turkey 3-2）。但信号表达集中于"平局"单一出口（Poisson 压 λ 的数学结果），Turkey胜方向 edge 为负（-6.8pp）。平局注 gap=14.4%>8% 被 LOW 拦截是**零代价的正确拦截**——方向对≠标的对，gap 拦截本场无代价。真正兑现的承接标的（1X/Turkey胜）不在当前盘口清单或 edge 为负。**✓ 双路径对账修复完成（2026-07-03）**：walkforward.py已对齐predict.py双矩阵（WF_GSV_MODE="production"，默认）；生产路径DC edge=-4.8pp，today.py非共识标注正确不触发（-4.8pp<7%阈值）。"旧+7.5pp"是legacy路径数字，已废弃。**DC出口猜想并入"平局生成与表达问题（收敛病灶）"**，解封条件：2026-07-03后新增GSV触发场次≥8且样本外DC假想ROI为正。届时立 spec 走 fence zone 流程。AH+0.5方向已审计：+115.5% 为样本期冷门密集6注子集，不可持续，同标准解封。**回归验证记录**：双路径一致性（today.py edge == walkforward production edge）是每场案例的标准验收项目。

## 运营态基准快照（2026-07-03，N=72场小组赛，搭建期收官）

下表是系统进入运营态时的干净基准，未来参数迭代必须对比此表：

| 指标 | 数值 | 备注 |
|------|------|------|
| Brier Score | **0.4296** | backtest 72场，含前视 AD state（不作为AD有效性证据） |
| 1X2 准确率 | **65.3%** (47/72) | backtest |
| WF Edge ROI | **+24.5%** (33注) | walkforward 无前视，v4 参数集，**GSV双矩阵修复后**（生产对齐） |
| WF Edge ROI（存档） | ~~+28.8% (31注)~~ | **pre-fix，GSV全矩阵污染，仅存档**。差值-4.2pp为GSV错误应用于1X2贡献的虚高 |
| Replay 轨迹 | **✓一致** | checksum=86230.0，72场 Elo 序列与全量 replay 逐场一致 |
| GSV DC 无水ROI | **+21.4%** (N=24, 14注有赔率) | 假想追踪器；**已用生产路径重填（2026-07-03）**；无水近似价，真实盘口含vig≈2-4%需下调。**入单口径=触发即下**（GSV触发且有1X2赔率即计入，无edge门槛）；edge>0才下口径=+24.6%(N=10)；两者均正。**解封条件中"DC ROI为正"以触发即下口径结算**，不事后切换为edge过滤。 |
| GSV AH+0.5 ROI | **+115.5%** (6注，真实赔率结算) | **已审计：不可持续。** 6注=14注里命中率最高的子集（5W/1L），恰好是本届小组赛冷门密集场次（Belgium×2平、Uruguay平、Ecuador爆冷）；计算无误，但系样本期选择偏差，非策略属性。**入单口径=有实赔±0.5 AH盘口即下，无edge门槛**，口径与DC一致。同6注套DC无水ROI=+117.8%，差值=vig，两者口径自洽。解封须2026-07-03后新增≥8场样本外GSV触发场次且DC ROI为正。 |

**数据管线状态**：`daily_sync.py` 四步管线 + staging 人工确认闸 + db_health 七项体检全绿。`update_elo.py` 单场直接写入路径已废弃（2026-07-03），仅保留 Elo 预览功能，所有入库统一走 `--commit-results`。
