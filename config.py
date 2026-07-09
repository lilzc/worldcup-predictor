import os

# 自动加载 .env（本地密钥，不进版本库）
_env_path = os.path.join(os.path.dirname(__file__), ".env")
if os.path.exists(_env_path):
    with open(_env_path) as _f:
        for _line in _f:
            _line = _line.strip()
            if _line and not _line.startswith("#") and "=" in _line:
                _k, _v = _line.split("=", 1)
                os.environ.setdefault(_k.strip(), _v.strip())

ODDS_API_KEY = os.environ.get("ODDS_API_KEY", "")  # 存 .env，不硬编码
KELLY_FRACTION = 0.25
MIN_EDGE = 0.03       # 最低3%边际才标记为value bet
OVER25_BACKTEST_ACC = 0.593  # 54场回测: 59.3% — 押大球时强制显示此校准警告
PROB_CAP = 0.85       # 单队最高胜率上限
WC_GOAL_DISCOUNT = 1.0    # BASE_GOALS=1.32 已是世界杯实际均值，无需再折扣
BASE_GOALS = 1.32     # WC每队平均进球（2014-2022实测 2.64球/场÷2）; 改动需回测Over2.5准确率
ELO_SCALE  = 400      # Elo差分化系数（400分差≈e^1≈2.7x进球差）; 改动需回测Brier Score
KNOCKOUT_START = "2026-06-28"  # 淘汰赛起始日（唯一真源）; date>=此值为淘汰赛，小组赛波动罚分不生效

DRAW_CALIBRATION = 1.0  # 平局概率显式缩减因子（<1.0则压低draw，>1.0则抬高draw）
PROB_SHARPENING  = 1.3  # 概率锐化幂次（>1.0压缩尾部、抬高强队；改动需回测Brier Score）
DC_RHO           = -0.20 # Dixon-Coles低分相关系数（越负0-0越膨胀；改动需回测Brier Score）
BANKROLL = 2000

# 近平场次压制（2026-06-24）：
# Elo差≤100 时 K=60更新噪声占diff本身10-15%，方向性信号不可靠。
# AH：回测28场 5L/6bets=83%亏损率
# 1X2 Win：回测28场 3L/3bets=100%亏损率（Croatia/Turkey/Sweden方向全错）
# 改动需对比Edge ROI（v3基准+29.2%，v4+46.1%）。
NEAR_EQUAL_AH_DIFF       = 100   # AH压制阈值
NEAR_EQUAL_1X2_WIN_DIFF  = 100   # 1X2 Win方向压制阈值
# OU Over压制（2026-06-28）：
# 近平场次（Elo差≤100）低线Over(line<2.5)结构性亏损：06-27 Uruguay/Spain Over 2.25 LOSS
# 三方案回测（18场walkforward+2场06-27）：
#   A=Kill全部 ROI+58.1%/P&L+814；B=不加规则 ROI+51.5%/P&L+1031；C=Kill line<2.5 ROI+59.5%/P&L+1131
# 历史近平场次OU Over线≥2.5全部WIN（England/Croatia 2.75、NZ/Egypt 2.5、Norway/Senegal 3.0）→ 不kill高线
# 选择Option C（最优）：只压制line<2.5的Over注，高线Over正常走calibration_gate
# 改动需对比walkforward中diff≤100 OU Over注分线段ROI
NEAR_EQUAL_OU_OVER_DIFF  = 100   # OU Over方向压制阈值（仅line<2.5触发）

# Fence zone 加 Elo 差条件（方案A，2026-07-02诊断立项）：
# 原始设计意图"均衡场次Elo差±50范围内7/7场Under 2.5"→ fence zone 只应保护近平场次
# 当前实现泛化 bug：无 Elo 差条件，任何场次 model_prob∈[44%,57%] 均被 kill
# 方案A 还原设计意图：仅 abs(Elo差)≤50 时触发 fence kill，大差距场次放行
# X=50 来自原始依据字面表述"Elo差±50"，非扫描/拟合得出
# 开关默认 False（现状零改动），True=修复版（待 walkforward 对比验证后开启）
OU_FENCE_WITH_ELO  = True   # True=方案A（2026-07-02启用）：仅 abs(Elo差)≤50 时 fence kill，还原设计意图
                            # 开启依据：范围 bug 修复，X=50 来自原始依据非拟合，N=7 验证仅证明无恶化不证明收益
FENCE_ELO_DIFF_CAP = 50     # 来自"均衡场次Elo差±50"原始设计依据，不扫描拟合

# GSV lambda frustration-zone correction (2026-06-22):
# Elo>1850强队在Elo差150-300区间("挫败带")实际进球/模型λ = 0.79
# 只影响AH/O/U（predict.py双矩阵机制），不影响1X2 Brier
GSV_LAMBDA_FACTOR   = 0.80   # 强队λ修正因子（实证值0.79，取整）
GSV_LAMBDA_ELO_MIN  = 1850   # 触发门槛：主队Elo>此值
GSV_LAMBDA_DIFF_MIN = 150    # 触发门槛：Elo差下限
GSV_LAMBDA_DIFF_MAX = 300    # 触发门槛：Elo差上限（标准区间150-300）

# GSV扩展区（2026-06-24新增）：diff 300-450强队仍有挫败风险，但弱于150-300区间
# Spain 0-0 Cape Verde (diff≈330) / Ecuador 0-0 Curacao (diff≈210)案例驱动
# 修正更温和（0.90 vs 0.80），因为diff>300强队更难被完全压制
GSV_LAMBDA_FACTOR_EXTENDED = 0.90   # diff 300-450区间的λ修正因子
GSV_LAMBDA_DIFF_EXTENDED   = 450    # 扩展区上限（>450不修正，强队必然打穿）

DEFENDING_CHAMPION = "Argentina"

UCL_MENTALITY_ENABLED = True   # False → disable UCL signal (use to measure independent contribution)
FLB_ENABLED = False            # A/B test (2026-06-24): FLB+Sharp=1.3对冲→Brier 0.4408; OFF+Sharp=1.3→0.4397; 关闭更优

# 上半场λ折算因子 — WC 2026实测：上半场46球/总102球 = 0.46（33场样本）
HT_LAMBDA_FACTOR = 0.46
# HT平局KILL：Elo差≥此值时禁止推HT平局——对标FT Rule②（diff>300 KILL全场）
# 回测根据：Spain/Saudi(diff≈250)、France/Iraq(diff≈372)HT平局全输；Ecuador/Curacao(diff≈163)HT平局WIN保留
# 200分割点：高于Ecuador(163)/Canada(165)但低于Spain(250)/France(372)
HT_DRAW_KILL_ELO_DIFF = 200

# Elo评分 — 基于2025/26赛季FIFA排名估算，可手动更新
TEAM_ELO = {
    "France":       1975,
    "Spain":        1950,
    "England":      1940,
    "Argentina":    1965,
    "Brazil":       1930,
    "Portugal":     1920,
    "Germany":      1910,
    "Netherlands":  1900,
    "Belgium":      1880,
    "Uruguay":      1870,
    "Croatia":      1860,
    "Norway":       1840,
    "USA":          1825,
    "Morocco":      1820,
    "Japan":        1815,
    "Mexico":       1810,
    "Switzerland":  1800,
    "Sweden":       1800,
    "Colombia":     1790,
    "Turkey":       1780,
    "Austria":      1775,
    "Denmark":      1780,
    "South Korea":  1745,
    "Senegal":      1740,
    "Canada":       1730,
    "Scotland":     1720,
    "Ghana":        1715,
    "Egypt":        1710,
    "Saudi Arabia": 1700,
    "Algeria":      1695,
    "Ecuador":      1750,
    "Iran":         1690,
    "Czechia":      1685,
    "Bosnia":       1680,
    "Paraguay":     1690,
    "Australia":    1760,
    "South Africa": 1670,
    "Tunisia":      1665,
    "Cape Verde":   1620,
    "Haiti":        1600,
    "Qatar":        1590,
    "Curacao":      1540,
    "Iraq":         1660,
    "Jordan":       1650,
    "Uzbekistan":   1645,
    "Congo DR":     1635,
    "Panama":       1630,
    "New Zealand":  1620,
    "Ivory Coast":  1760,
}

# Attack/Defense tempo decomposition (2026-07-01)
# 解耦 tempo：att_i/def_i 捕捉相对 Elo 期望的进球风格残差
# AD_ENABLED=False 可精确退化回当前纯 Elo λ（A/B 基线）
AD_ENABLED      = True   # 主开关
AD_SHRINKAGE_K  = 8      # 向 1.0 收缩的先验强度（≈8 场中性先验）
AD_BLEND_WEIGHT = 1.0    # 残差应用比例（先 1.0，按 walkforward 调）
AD_MIN_MATCHES  = 3      # 低于此场次强制 = 1.0
AD_CAP_LO       = 0.60   # 因子下钳
AD_CAP_HI       = 1.60   # 因子上钳

# 自动情报搜集参数（predict_market.py --auto，2026-07-01）
AUTO_NEWS_ENABLED    = True
NEWS_SINGLE_MULT_MIN = 0.80   # 单条 flag λ乘数下限
NEWS_SINGLE_MULT_MAX = 1.20   # 单条 flag λ乘数上限
NEWS_TEAM_MULT_MIN   = 0.75   # 单队合计λ乘数下限
NEWS_TEAM_MULT_MAX   = 1.25   # 单队合计λ乘数上限

# UCL决赛心态信号（Repo2的因子，手动维护）
UCL_MENTALITY = {
    "France":    -0.16,   # 姆巴佩连续两届QF出局
    "England":   +0.05,   # Palmer/Saka联赛表现稳定
    "Argentina": +0.10,   # 梅西2022冠军加成，但年龄衰减
    "Germany":   +0.05,
    "Portugal":  -0.05,
}
