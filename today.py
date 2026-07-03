#!/usr/bin/env python3
"""
python3 today.py              — 全盘口扫描 + 最优下注
python3 today.py --auto       — 从 The Odds API 自动抓取赔率
python3 today.py --bankroll 5000  — 临时覆盖本金
"""

import sys
import json
import argparse
from pathlib import Path
sys.path.insert(0, ".")

from predict import predict
from src.betting.kelly import american_to_decimal
from config import (BANKROLL, ODDS_API_KEY, MIN_EDGE, TEAM_ELO,
                    GSV_LAMBDA_ELO_MIN, GSV_LAMBDA_DIFF_MIN, GSV_LAMBDA_DIFF_MAX,
                    GSV_LAMBDA_DIFF_EXTENDED,
                    GSV_LAMBDA_FACTOR, GSV_LAMBDA_FACTOR_EXTENDED,
                    NEAR_EQUAL_AH_DIFF, NEAR_EQUAL_1X2_WIN_DIFF,
                    NEAR_EQUAL_OU_OVER_DIFF,
                    OU_FENCE_WITH_ELO, FENCE_ELO_DIFF_CAP)

# ── 手动赔率（LEGACY 重放专用） ─────────────────────────────────────────────
# ⚠ 过期小组赛快照，仅历史重放用，勿更新，勿新增。
# 当日预测唯一入口：python3 predict_market.py --auto-today
# AH: {line: (home_odds, away_odds)}  line=正数表示主队让球
# ou_odds: {line: (over_odds, under_odds)}
# cs_odds: {"hg-ag": decimal_odds}
MANUAL_MATCHES = [
    # ── 06-28 Group L MD3 (09:00 Beijing) ──────────────────────────────────
    # Elo(England=1946, Panama=1595) diff=351 → GSV扩展区(300-450)触发
    {
        "home": "England", "away": "Panama", "date": "2026-06-28",
        "odds_home": 1.17, "odds_draw": 7.50, "odds_away": 15.00,
        "ou_odds": {
            3.5:  (2.20, 1.68),
        },
        "ah_odds": {
            2.5: (2.35, 1.57),   # England -2.5（需赢3+球）
        },
        "cs_odds": {},
    },
    # Elo(Croatia=1849, Ghana=1755) diff=94 → near-equal（AH/1X2-Win/OU-Over<2.5全压制）
    {
        "home": "Croatia", "away": "Ghana", "date": "2026-06-28",
        "odds_home": 1.71, "odds_draw": 3.75, "odds_away": 5.50,
        "ou_odds": {
            2.5:  (2.35, 1.74),
        },
        "ah_odds": {
            0.5:  (1.74, 2.05),   # Croatia -0.5
        },
        "cs_odds": {},
    },
    # ── 06-28 Group J MD3 (10:00 Beijing) ──────────────────────────────────
    # Elo(Algeria=1710, Austria=1779) diff=-69 → near-equal（AH/1X2-Win/OU-Over<2.5全压制）
    {
        "home": "Algeria", "away": "Austria", "date": "2026-06-28",
        "odds_home": 3.65, "odds_draw": 3.30, "odds_away": 2.15,
        "ou_odds": {
            2.5:  (2.15, 1.74),
        },
        "ah_odds": {},  # Austria受让（away更强），系统不支持负线
        "cs_odds": {},
    },
    # Elo(Jordan=1605, Argentina=1991) diff=-386 → Argentina已6分出线，Jordan已淘汰
    # 赛前情报：Argentina大轮换，Messi等坐板凳（dead rubber）
    {
        "home": "Jordan", "away": "Argentina", "date": "2026-06-28",
        "odds_home": 15.00, "odds_draw": 7.50, "odds_away": 1.17,
        "ou_odds": {
            2.5:  (1.61, 2.43),
            3.5:  (2.00, 1.90),
        },
        "ah_odds": {},  # Argentina受让（away），系统不支持负线
        "cs_odds": {},
        "news_flags": [
            "dead_rubber_away:Argentina已6分头名出线",
            "rotation_away:Messi/主力大规模坐板凳（CBS Sports确认）",
        ],
    },
    # ── 06-28 Group K MD3 (11:30 Beijing) ──────────────────────────────────
    # Elo(Colombia=1826, Portugal=1910) diff=-84 → near-equal（AH/1X2-Win/OU-Over<2.5全压制）
    {
        "home": "Colombia", "away": "Portugal", "date": "2026-06-28",
        "odds_home": 3.50, "odds_draw": 3.80, "odds_away": 1.95,
        "ou_odds": {
            2.5:  (2.08, 1.77),
        },
        "ah_odds": {},  # Portugal受让（away更强），系统不支持负线
        "cs_odds": {},
    },
    # Elo(Congo DR=1638, Uzbekistan=1617) diff=21 → near-equal（AH/1X2-Win/OU-Over<2.5全压制）
    {
        "home": "Congo DR", "away": "Uzbekistan", "date": "2026-06-28",
        "odds_home": 2.55, "odds_draw": 3.30, "odds_away": 5.00,
        "ou_odds": {
            2.5:  (2.20, 1.63),
        },
        "ah_odds": {
            0.5:  (1.69, 2.15),   # Congo DR -0.5
        },
        "cs_odds": {},
    },
    # ── 06-29 03:00 Beijing 32强（竞彩Match 073）──────────────────────────
    # 竞彩胜平负: SA 4.80/3.25 Canada 1.63  让球(+1): 2.00/3.30/3.07
    # OU来源国际市场(CBS/ESPN): O2.5@2.15 U2.5@1.72
    # Elo(Canada=1721, SA=1690) diff=31 → near-equal
    {
        "home": "Canada", "away": "South Africa", "date": "2026-06-29",
        "odds_home": 1.63, "odds_draw": 3.25, "odds_away": 4.80,
        "ou_odds": {
            2.5: (2.15, 1.72),
        },
        "ah_odds": {
            1.0:  (3.07, 2.00),   # Canada -1（需赢2+球）
        },
        "cs_odds": {},
    },
]

# ── 校准关卡阈值 ─────────────────────────────────────────────────────────────
# 基于54场回测的实证结论
DRAW_MIN_PROB   = 0.35  # 平局模型概率 < 35% 视为结构性高估，直接过滤
DRAW_MIN_EDGE   = 0.07  # 平局需更高边际（结构性高估补偿）
WIN_MIN_PROB    = 0.25  # 1X2胜注：模型概率 < 25% → 低置信高赔率，优先找OU替代
OU_FENCE_LO     = 0.44  # O/U fence zone 下限（历史 Over 率 ≈ 0%）
OU_FENCE_HI     = 0.57  # O/U fence zone 上限（多一格避免浮点边界问题）
ARTIFACT_KILL   = 0.20  # 模型-市场差 > 20% → 直接过滤（强artifact信号）
ARTIFACT_GAP    = 0.08  # 模型-市场差 8-20% → LOW 警告
#   AH 市场 vig 仅 2-4%，raw gap ≈ de-vigged edge。8% gap 在 AH 意味着
#   模型显著超越高效市场，来源是原始 Poisson 无 GSV 调整导致的 λ 膨胀。
#   复盘：Uruguay AH -1.25/-1.5/-1.75 gap 均 8-10%，全部 HIGH 推荐全部输。
CS_MIN_EDGE     = 0.08  # 正确比分需更高边际（高方差市场）
UNDER_MKTOVER_KILL = 0.52  # Under 盘：市场Over隐含>52% 而模型仍押Under → artifact（GSV低估进攻）

# DC 非共识边际阈值（独立参数，注释"待 GSV 追踪器样本校准"）
# 组合概率天然量级大(50-90%)，同绝对pp在DC上信息量低于1X2，故比MIN_EDGE略高
# 仅 GSV 触发场次开口（弱方不败方向），防止全场泛滥成噪声
NONCONSENSUS_DC_EDGE = 0.07  # 待 GSV 追踪器 N≥30 后校准


def _market_implied(dec_odds: float) -> float:
    return 1.0 / dec_odds


def _print_news_flags(flags: list, compact: bool = False) -> None:
    """展示赛前情报flags（不改λ，仅提示人工判断）。
    flags格式: ["rotation_home:Norway轮换9人", "dead_rubber_away:France已出线"]
    """
    if not flags:
        return
    icons = {"rotation": "⚡", "dead_rubber": "💤", "must_win": "🔥", "injury": "🚑"}
    if compact:
        tags = " | ".join(f.split(":")[0] for f in flags)
        print(f"    [情报: {tags}]")
    else:
        print(f"  {'─'*46}")
        print(f"  NEWS FLAGS")
        for f in flags:
            key = f.split(":")[0].split("_")[0] if ":" in f else f
            icon = icons.get(key, "ℹ")
            label, _, detail = f.partition(":")
            print(f"    {icon} {label}: {detail}" if detail else f"    {icon} {f}")
        print(f"  [模型λ未修正 — 推单结论需结合情报人工判断]")
        print(f"  {'─'*46}")


def _gsv_trigger_info(home: str, away: str) -> dict:
    """GSV 触发检查（使用动态 elo_state.json，与 predict() 内部口径一致）。
    注意：kill 关卡（_hybrid_rule2_kill / 近平压制）仍用静态 TEAM_ELO；
    DC 标注使用动态 Elo，使 GSV 触发判断与 score_matrix 内部一致。
    返回 dict：triggered / zone / strong / weak / diff / diff_abs
    """
    from src.models.poisson import get_elo as _get_elo
    _live = _get_elo()
    he = _live.get(home, TEAM_ELO.get(home, 1700))
    ae = _live.get(away, TEAM_ELO.get(away, 1700))
    diff = he - ae
    gsv_std_h = he > GSV_LAMBDA_ELO_MIN and GSV_LAMBDA_DIFF_MIN <= diff <= GSV_LAMBDA_DIFF_MAX
    gsv_std_a = ae > GSV_LAMBDA_ELO_MIN and GSV_LAMBDA_DIFF_MIN <= -diff <= GSV_LAMBDA_DIFF_MAX
    gsv_ext_h = (he > GSV_LAMBDA_ELO_MIN
                 and GSV_LAMBDA_DIFF_MAX < diff <= GSV_LAMBDA_DIFF_EXTENDED)
    gsv_ext_a = (ae > GSV_LAMBDA_ELO_MIN
                 and GSV_LAMBDA_DIFF_MAX < -diff <= GSV_LAMBDA_DIFF_EXTENDED)
    triggered = gsv_std_h or gsv_std_a or gsv_ext_h or gsv_ext_a
    if not triggered:
        return {"triggered": False}
    zone = "standard" if (gsv_std_h or gsv_std_a) else "extended"
    if gsv_std_h or gsv_ext_h:
        strong, weak = home, away
    else:
        strong, weak = away, home
    return {
        "triggered": True, "zone": zone,
        "strong": strong, "weak": weak,
        "diff": diff, "diff_abs": abs(diff),
    }


def _print_dc_nonconsensus(
    home: str, away: str,
    model_hw: float, model_d: float, model_aw: float,
    cfg: dict,
) -> None:
    """DC 非共识标注。仅 GSV 触发场次且弱方DC edge ≥ NONCONSENSUS_DC_EDGE 时输出。
    纯展示层，不影响任何推单 / Kelly / portfolio。
    """
    gsv = _gsv_trigger_info(home, away)
    if not gsv["triggered"]:
        return
    odds_h = cfg.get("odds_home", 0)
    odds_d = cfg.get("odds_draw", 0)
    odds_a = cfg.get("odds_away", 0)
    if not (odds_h and odds_d and odds_a):
        return

    vig = 1/odds_h + 1/odds_d + 1/odds_a
    mkt_hw = (1/odds_h) / vig
    mkt_d  = (1/odds_d) / vig
    mkt_aw = (1/odds_a) / vig

    strong, weak = gsv["strong"], gsv["weak"]
    zone_str = f"{gsv['zone']},diff={gsv['diff_abs']:.0f}"

    if strong == home:
        dc_model  = model_d  + model_aw
        dc_market = mkt_d    + mkt_aw
        dc_label  = f"{away}不败(X2)"
    else:
        dc_model  = model_hw + model_d
        dc_market = mkt_hw   + mkt_d
        dc_label  = f"{home}不败(1X)"

    dc_edge = dc_model - dc_market
    if dc_edge < NONCONSENSUS_DC_EDGE:
        return

    print(f"    ⚡非共识[DC]: A看好 {dc_label}"
          f" | 模型{dc_model*100:.1f}% vs 市场{dc_market*100:.1f}%"
          f" | edge +{dc_edge*100:.1f}pp")
    print(f"       凭据: GSV触发({zone_str}) | 未经kill关卡(组合方向无适用规则)")
    print(f"       ⚠ 无真实DC盘口赔率，edge基于无水近似，真实盘口含vig≈2-4%，可下注edge需下调")
    print(f"       GSV假想追踪: python3 -m src.analysis.gsv_shadow_tracker --report")


# GSV 1X2 实验假设对照日期基准（样本外计数起点）
_GSV_OOS_CUTOFF = "2026-07-03"
_GSV_OOS_THRESHOLD = 8


def _count_oos_gsv_n() -> int:
    """读 gsv_shadow_log.jsonl，统计 _GSV_OOS_CUTOFF 之后的样本外场次数。"""
    log_path = Path("data/gsv_shadow_log.jsonl")
    if not log_path.exists():
        return 0
    n = 0
    try:
        with open(log_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    r = json.loads(line)
                    if r.get("date", "") > _GSV_OOS_CUTOFF:
                        n += 1
                except Exception:
                    pass
    except Exception:
        pass
    return n


def _compute_gsv_legacy_dc_edge(home: str, away: str, strong: str,
                                 dc_market_prob: float) -> float:
    """计算 GSV 应用于完整 1X2 矩阵（实验假设路径）的 DC edge。
    此路径与 walkforward legacy 模式一致，非当前生产设计。
    """
    from src.models.poisson import score_matrix, matrix_to_probs, get_elo as _get_elo
    from src.models.adjustments import apply_all
    live = _get_elo()
    he = live.get(home, TEAM_ELO.get(home, 1700))
    ae = live.get(away, TEAM_ELO.get(away, 1700))
    diff = he - ae
    lh = la = 1.0
    if he > GSV_LAMBDA_ELO_MIN and GSV_LAMBDA_DIFF_MIN <= diff <= GSV_LAMBDA_DIFF_MAX:
        lh = GSV_LAMBDA_FACTOR
    elif ae > GSV_LAMBDA_ELO_MIN and GSV_LAMBDA_DIFF_MIN <= -diff <= GSV_LAMBDA_DIFF_MAX:
        la = GSV_LAMBDA_FACTOR
    elif he > GSV_LAMBDA_ELO_MIN and GSV_LAMBDA_DIFF_MAX < diff <= GSV_LAMBDA_DIFF_EXTENDED:
        lh = GSV_LAMBDA_FACTOR_EXTENDED
    elif ae > GSV_LAMBDA_ELO_MIN and GSV_LAMBDA_DIFF_MAX < -diff <= GSV_LAMBDA_DIFF_EXTENDED:
        la = GSV_LAMBDA_FACTOR_EXTENDED
    mat_gsv = score_matrix(home, away, lam_scale_home=lh, lam_scale_away=la)
    raw_gsv = matrix_to_probs(mat_gsv)
    adj_gsv = apply_all(home, away, raw_gsv["home_win"], raw_gsv["draw"], raw_gsv["away_win"],
                        home_elo=he, away_elo=ae)
    leg_hw = adj_gsv.get("home_win", raw_gsv["home_win"])
    leg_d  = adj_gsv.get("draw",     raw_gsv["draw"])
    leg_aw = adj_gsv.get("away_win", raw_gsv["away_win"])
    if strong == home:
        dc_legacy = leg_d + leg_aw
    else:
        dc_legacy = leg_hw + leg_d
    return dc_legacy - dc_market_prob


def _print_gsv_experiment_line(
    home: str, away: str,
    prod_hw: float, prod_d: float, prod_aw: float,
    cfg: dict,
) -> None:
    """GSV 1X2 实验假设对照行（对所有 GSV 触发场次展示，不受 NONCONSENSUS_DC_EDGE 门槛限制）。
    并列打印生产路径 DC edge 与全矩阵 GSV 假设路径 DC edge，固定标注"实验假设，非生产观点"。
    纯展示，不进非共识标注体系，不带任何推荐色彩。
    样本外 N 达 _GSV_OOS_THRESHOLD 且立 spec 裁决后，此行按裁决转正或删除。
    """
    gsv = _gsv_trigger_info(home, away)
    if not gsv["triggered"]:
        return
    odds_h = cfg.get("odds_home", 0)
    odds_d = cfg.get("odds_draw", 0)
    odds_a = cfg.get("odds_away", 0)
    if not (odds_h and odds_d and odds_a):
        return
    vig = 1/odds_h + 1/odds_d + 1/odds_a
    mkt_hw = (1/odds_h) / vig
    mkt_d  = (1/odds_d) / vig
    mkt_aw = (1/odds_a) / vig
    strong = gsv["strong"]
    if strong == home:
        dc_prod   = prod_d  + prod_aw
        dc_market = mkt_d   + mkt_aw
    else:
        dc_prod   = prod_hw + prod_d
        dc_market = mkt_hw  + mkt_d
    edge_prod   = dc_prod - dc_market
    edge_legacy = _compute_gsv_legacy_dc_edge(home, away, strong, dc_market)
    n_oos = _count_oos_gsv_n()
    print(f"    ℹ GSV参考: 若GSV应用于1X2(实验假设,非生产设计)"
          f",弱方不败edge为{edge_legacy*100:+.1f}pp"
          f" | 生产设计下该edge为{edge_prod*100:+.1f}pp"
          f" | 该假设由GSV追踪器验证中(样本外N={n_oos}/{_GSV_OOS_THRESHOLD})")


def _hybrid_rule2_kill(home: str, away: str) -> tuple[bool, str]:
    """混合策略规则②：Elo差>GSV_LAMBDA_DIFF_EXTENDED且无任何GSV → KILL
    diff 150-300: 标准GSV λ×0.80（已修正）
    diff 300-450: 扩展GSV λ×0.90（已修正，2026-06-24新增）
    diff >450:    无GSV修正 → KILL"""
    he = TEAM_ELO.get(home, 1700)
    ae = TEAM_ELO.get(away, 1700)
    diff = he - ae
    gsv_standard = ((he > GSV_LAMBDA_ELO_MIN and GSV_LAMBDA_DIFF_MIN <= diff <= GSV_LAMBDA_DIFF_MAX) or
                    (ae > GSV_LAMBDA_ELO_MIN and GSV_LAMBDA_DIFF_MIN <= -diff <= GSV_LAMBDA_DIFF_MAX))
    gsv_extended = ((he > GSV_LAMBDA_ELO_MIN and GSV_LAMBDA_DIFF_MAX < diff <= GSV_LAMBDA_DIFF_EXTENDED) or
                    (ae > GSV_LAMBDA_ELO_MIN and GSV_LAMBDA_DIFF_MAX < -diff <= GSV_LAMBDA_DIFF_EXTENDED))
    if abs(diff) > GSV_LAMBDA_DIFF_EXTENDED and not gsv_standard and not gsv_extended:
        stronger = home if diff > 0 else away
        return True, f"Elo差{diff:+d}>{GSV_LAMBDA_DIFF_EXTENDED}且无GSV（{stronger} λ未修正，混合策略规则②）"
    return False, ""


def calibration_gate(label: str, model_prob: float, edge: float,
                      dec_odds: float, market_true: float = None,
                      elo_diff: float | None = None) -> tuple[bool, str, str]:
    """
    Returns (pass, confidence, kill_reason).
    confidence: "HIGH" | "MED" | "LOW"
    kill_reason: 非空则过滤
    market_true: de-vigged market probability; falls back to 1/dec_odds if absent.
    elo_diff: home_elo - away_elo（仅 OU_FENCE_WITH_ELO=True 时必须传入；
              None 且开关开启 → raise ValueError，不静默 fallback）
    """
    market_p = market_true if market_true is not None else _market_implied(dec_odds)
    gap = abs(model_prob - market_p)

    # ── 平局结构性过滤 ──
    if "平局" in label:
        if model_prob < DRAW_MIN_PROB:
            return False, "", f"平局模型{model_prob:.0%}<{DRAW_MIN_PROB:.0%}，Poisson结构性高估区间"
        if edge < DRAW_MIN_EDGE:
            return False, "", f"平局需边际≥{DRAW_MIN_EDGE:.0%}，当前{edge:.1%}"

    # ── 1X2 胜注低置信过滤 ──
    if ("胜" in label and "平局" not in label and "受让" not in label and "让" not in label):
        if model_prob < WIN_MIN_PROB:
            return False, "", f"1X2胜注模型{model_prob:.0%}<{WIN_MIN_PROB:.0%}，低置信高赔率，优先找OU替代"

    # ── O/U fence zone（Over/Under 共用）──
    # 原始设计意图："均衡场次Elo差±50"内 OU 不可信（7/7 场 Under 2.5）
    # OU_FENCE_WITH_ELO=False（默认）：当前行为，无 Elo 条件，全场 fence kill
    # OU_FENCE_WITH_ELO=True（方案A）：还原设计意图，仅 abs(elo_diff)≤FENCE_ELO_DIFF_CAP 时 kill
    #   elo_diff=None 时 raise ValueError（调用方 bug，不静默处理）
    if ("Over" in label or "Under" in label) and "比分" not in label:
        if OU_FENCE_LO <= model_prob <= OU_FENCE_HI:
            if OU_FENCE_WITH_ELO:
                if elo_diff is None:
                    raise ValueError(
                        f"calibration_gate: OU_FENCE_WITH_ELO=True 但 elo_diff 未传入"
                        f"（bet='{label}'）。所有 OU 调用方在开关开启时必须传 elo_diff。"
                    )
                if abs(elo_diff) <= FENCE_ELO_DIFF_CAP:
                    return False, "", (
                        f"O/U fence zone 近平(diff={elo_diff:+.0f},|diff|≤{FENCE_ELO_DIFF_CAP})"
                        f"，模型{model_prob:.0%}∈[{OU_FENCE_LO:.0%},{OU_FENCE_HI:.0%}]，历史Over率0%"
                    )
                # abs(elo_diff) > FENCE_ELO_DIFF_CAP → 大差距场次，放行（原始设计不覆盖）
            else:
                return False, "", f"O/U fence zone（模型{model_prob:.0%} ∈ [{OU_FENCE_LO:.0%},{OU_FENCE_HI:.0%}]），历史Over率0%"

    # ── Under 反向信号：市场强烈偏大球而模型押小球 ──
    if "Under" in label and "比分" not in label:
        market_over_implied = 1.0 - market_p
        if market_over_implied >= UNDER_MKTOVER_KILL and model_prob > 0.50:
            return False, "", (f"市场大球意图强({market_over_implied:.0%}≥{UNDER_MKTOVER_KILL:.0%})"
                               f"而模型押Under，可能是进攻力低估artifact")

    # ── 正确比分高方差过滤 ──
    if "比分" in label and edge < CS_MIN_EDGE:
        return False, "", f"CS边际{edge:.1%}<{CS_MIN_EDGE:.0%}，单场方差过高"

    # ── 模型-市场artifact大间距 ──
    if gap >= ARTIFACT_KILL:
        return False, "", (f"模型({model_prob:.0%})与市场({market_p:.0%})"
                           f"差{gap:.0%}≥{ARTIFACT_KILL:.0%}，可能是GSV/ELO参数artifact")

    # ── 基础边际 ──
    if edge < MIN_EDGE:
        return False, "", f"边际{edge:.1%}<MIN_EDGE {MIN_EDGE:.0%}"

    # ── 确定置信度 ──
    if gap >= ARTIFACT_GAP:
        conf = "LOW"
    elif edge >= 0.08:
        conf = "HIGH"
    else:
        conf = "MED"

    return True, conf, ""


def compute_1x2_kill_results(
    home: str,
    away: str,
    value_dict: dict,
    odds_home: float,
    odds_draw: float,
    odds_away: float,
) -> dict:
    """
    对 A 系统 1X2 三方向运行完整 kill 关卡，返回结构化数据（不打印）。

    Elo 口径说明：
      - edge/概率 来自调用方传入的 value_dict（由 predict() 读 elo_state.json 动态 Elo 计算）
      - kill 关卡（Rule②/近平/calibration_gate）用 TEAM_ELO（静态 config.py）
        ← 与 best_bets_report() 完全一致，两者 kill 判定不会出现 Elo 口径不同步

    返回 {direction: {"passed": bool, "conf": str, "kill_reason": str}}
    directions: home_win / draw / away_win
    """
    rule2_kill, rule2_reason = _hybrid_rule2_kill(home, away)
    _he = TEAM_ELO.get(home, 1700)
    _ae = TEAM_ELO.get(away, 1700)
    near_equal_win = abs(_he - _ae) <= NEAR_EQUAL_1X2_WIN_DIFF

    dir_cfg = {
        "home_win": ("主场胜", odds_home, near_equal_win),
        "draw":     ("平局",   odds_draw, False),
        "away_win": ("客场胜", odds_away, near_equal_win),
    }

    results = {}
    for direction, (label, dec_odds, near_kill) in dir_cfg.items():
        v = value_dict.get(direction, {})
        if not v or v.get("edge", 0.0) < MIN_EDGE:
            e_val = v.get("edge", 0.0) if v else 0.0
            results[direction] = {
                "passed": False, "conf": "",
                "kill_reason": f"edge {e_val*100:.1f}%<MIN_EDGE {MIN_EDGE*100:.0f}%",
            }
            continue

        if rule2_kill:
            results[direction] = {"passed": False, "conf": "", "kill_reason": rule2_reason}
            continue

        if near_kill and direction in ("home_win", "away_win"):
            results[direction] = {
                "passed": False, "conf": "",
                "kill_reason": f"近平场次1X2Win压制(Elo差≤{NEAR_EQUAL_1X2_WIN_DIFF})",
            }
            continue

        ok, conf, kill_reason = calibration_gate(
            label, v["model"], v["edge"], dec_odds, v.get("market_true"),
            elo_diff=_he - _ae,
        )
        results[direction] = {"passed": ok, "conf": conf, "kill_reason": kill_reason or ""}

    return results


def best_bets_report(all_match_results: list[dict], matches: list[dict] = None):
    """
    扫描所有比赛的全盘口Kelly结果，经校准关卡过滤后输出最优下注。
    无有效信号则输出 NO BET。
    """
    print(f"\n{'═'*70}")
    print(f"  全盘口最优下注 — 校准关卡已应用")
    print(f"{'═'*70}")
    print(f"  过滤规则: 平局<{DRAW_MIN_PROB:.0%}杀 | O/U fence±{int((OU_FENCE_HI-0.5)*100)}%杀 |"
          f" 模型-市场差>{ARTIFACT_KILL:.0%}杀 | CS边际<{CS_MIN_EDGE:.0%}杀")
    print(f"{'─'*70}")

    grand_bets = []
    killed_summary = []

    _bbr_cfg = {(m["home"], m["away"]): m for m in (matches or [])}

    for res in all_match_results:
        home, away = res["home"], res["away"]
        result = res.get("result", {})
        portfolio = result.get("portfolio", []) if isinstance(result, dict) else []
        _news = _bbr_cfg.get((home, away), {}).get("news_flags", [])

        # 混合策略规则②：Elo差>300且无GSV → 本场全KILL
        rule2_kill, rule2_reason = _hybrid_rule2_kill(home, away)

        # 近平场次 AH 压制：Elo差≤100时AH边际不可靠（K=60噪声占diff10-15%）
        _he = TEAM_ELO.get(home, 1700)
        _ae = TEAM_ELO.get(away, 1700)
        near_equal_kill = abs(_he - _ae) <= NEAR_EQUAL_AH_DIFF

        passed, killed = [], []
        for b in portfolio:
            if b.get("stake", 0) <= 0:
                continue
            if rule2_kill:
                killed.append((b["label"], b["edge"], rule2_reason))
                continue
            if near_equal_kill and b["label"].startswith("AH "):
                killed.append((b["label"], b["edge"],
                                f"近平场次AH压制(Elo差≤{NEAR_EQUAL_AH_DIFF})"))
                continue
            if (abs(_he - _ae) <= NEAR_EQUAL_1X2_WIN_DIFF
                    and (b["label"].startswith("主场胜") or b["label"].startswith("客场胜"))):
                killed.append((b["label"], b["edge"],
                                f"近平场次1X2Win压制(Elo差≤{NEAR_EQUAL_1X2_WIN_DIFF})"))
                continue
            if (abs(_he - _ae) <= NEAR_EQUAL_OU_OVER_DIFF
                    and "Over" in b["label"]):
                try:
                    _ou_line = float(b["label"].split()[1])
                except (IndexError, ValueError):
                    _ou_line = 0.0
                if _ou_line < 2.5:
                    killed.append((b["label"], b["edge"],
                                    f"近平场次OU Over压制(line<2.5,Elo差≤{NEAR_EQUAL_OU_OVER_DIFF})"))
                    continue
            ok, conf, reason = calibration_gate(
                b["label"], b["model_prob"], b["edge"], b["decimal_odds"],
                b.get("market_true"),
                elo_diff=_he - _ae,
            )
            if ok:
                passed.append({**b, "conf": conf, "match": f"{home} vs {away}"})
            else:
                killed.append((b["label"], b["edge"], reason))

        # Per-match output
        print(f"\n  {home} vs {away}")
        _print_news_flags(_news, compact=True)
        if not passed:
            print(f"    ✗ NO BET — 无通过校准关卡的正向边际")
            if killed:
                print(f"    过滤明细 ({len(killed)}注):")
                for lbl, e, r in killed[:6]:
                    print(f"      ✗  {lbl:<30} edge={e*100:+.1f}%  原因: {r}")
        else:
            passed.sort(key=lambda x: -x["ev"])
            print(f"    {'标的':<32} {'赔率':>6} {'模型':>7} {'边际':>7} {'置信':>5} {'Kelly¥':>7} {'EV¥':>6}")
            print(f"    {'─'*70}")
            for b in passed:
                star = {"HIGH": "⭐", "MED": " ✓", "LOW": " ⚠"}[b["conf"]]
                print(f"    {star} {b['label']:<30} {b['decimal_odds']:>6.2f}"
                      f" {b['model_prob']*100:>6.1f}%"
                      f" {b['edge']*100:>+6.1f}%"
                      f" {b['conf']:>5}"
                      f" ¥{b['stake']:>5.0f} ¥{b['ev']:>5.1f}")
            if killed:
                print(f"    过滤掉 {len(killed)} 注 (最大artifact: {max(b[1] for b in killed)*100:+.1f}%)")

            grand_bets.extend(b for b in passed if b["conf"] != "LOW")

        # DC 非共识标注 + GSV 1X2 实验假设对照（仅 GSV 触发场次）
        _bbr_m = _bbr_cfg.get((home, away), {})
        _bbr_probs = result.get("probs", {}) if isinstance(result, dict) else {}
        _print_dc_nonconsensus(
            home, away,
            _bbr_probs.get("home_win", 0), _bbr_probs.get("draw", 0), _bbr_probs.get("away_win", 0),
            _bbr_m,
        )
        _print_gsv_experiment_line(
            home, away,
            _bbr_probs.get("home_win", 0), _bbr_probs.get("draw", 0), _bbr_probs.get("away_win", 0),
            _bbr_m,
        )
        killed_summary.append((f"{home} vs {away}", killed))

    # ── 全局最优排名 ──────────────────────────────────────────────────────
    print(f"\n{'═'*70}")
    if not grand_bets:
        print(f"  今日全场 NO BET — 所有盘口均被校准关卡过滤")
        print(f"{'═'*70}")
        return

    grand_bets.sort(key=lambda x: -x["ev"])
    total_stake = sum(b["stake"] for b in grand_bets)
    total_ev    = sum(b["ev"]    for b in grand_bets)

    print(f"  今日推荐下注 ({len(grand_bets)}注) — 按EV排序")
    print(f"{'─'*70}")
    for b in grand_bets:
        star = {"HIGH": "⭐", "MED": " ✓", "LOW": " ⚠"}[b["conf"]]
        match_short = b["match"].replace(" vs ", "/")[:20]
        print(f"  {star} {match_short:<20} {b['label']:<28}"
              f" @{b['decimal_odds']:.2f}  ¥{b['stake']:>5.0f}  EV ¥{b['ev']:>5.1f}")

    print(f"{'─'*70}")
    print(f"  合计投注: ¥{total_stake:.0f}  预期盈利: ¥{total_ev:.1f}"
          f"  ROI: {total_ev/total_stake*100:+.1f}%")
    print(f"\n  置信图例: ⭐ HIGH(edge≥8%∧gap<8%) ✓ MED(3-8%)  ⚠ LOW(gap≥8%警告)")
    print(f"{'═'*70}")


def to_dec(v):
    if v is None:
        return None
    f = float(v)
    if abs(f) >= 100:
        return american_to_decimal(int(f))
    return f


def _edge_tag(edge: float) -> str:
    if edge >= 0.03:   return ""
    if edge >= 0:      return "  ✓小优势"
    return             "  ✗市场已定价"


def stable_bets_report(all_match_results: list[dict], matches: list[dict]):
    """
    稳单模式：
      - 稳单 = 每场1X2最高模型概率方向，不加OU/AH
      - OU参考 = OU-A（模型概率最高且≥0.48），仅展示，不计入稳单盈亏
      - CS参考 = 前3波胆，仅展示
    """
    match_cfg = {(m["home"], m["away"]): m for m in matches}

    print(f"\n{'═'*70}")
    print(f"  稳单推单 — 每场1X2最高模型概率方向")
    print(f"  OU/CS为参考信息，不计入稳单盈亏统计")
    print(f"{'═'*70}")

    for res in all_match_results:
        home, away = res["home"], res["away"]
        result    = res.get("result", {})
        probs     = result.get("probs", {})
        value     = result.get("value", {})
        cfg       = match_cfg.get((home, away), {})

        rule2, _ = _hybrid_rule2_kill(home, away)
        r2tag = "  [⚠ λ未修正 参考概率偏高]" if rule2 else ""
        print(f"\n  {home} vs {away}{r2tag}")
        _print_news_flags(cfg.get("news_flags", []))

        # ── 稳单：1X2 最高概率方向 ─────────────────────────────────────
        dirs = [
            (probs.get("home_win", 0), f"{home}胜",
             cfg.get("odds_home", 0), value.get("home_win", {}).get("edge", 0)),
            (probs.get("draw", 0), "平局",
             cfg.get("odds_draw", 0), value.get("draw", {}).get("edge", 0)),
            (probs.get("away_win", 0), f"{away}胜",
             cfg.get("odds_away", 0), value.get("away_win", {}).get("edge", 0)),
        ]
        dirs.sort(reverse=True)
        mp, lbl, odds, edge = dirs[0]
        draw_p = probs.get("draw", 0)
        draw_warn = f"  ⚠平局风险{draw_p*100:.0f}%" if draw_p >= 0.18 else ""
        _he = TEAM_ELO.get(home, 1700)
        _ae = TEAM_ELO.get(away, 1700)
        _diff = abs(_he - _ae)
        bus_zone_warn = (f"  ⚠Bus Zone直胜风险(diff{_diff:+d})"
                         if 150 <= _diff <= 300 and lbl.endswith("胜") else "")
        if odds:
            print(f"    稳单  {lbl:<22} @{odds:.2f}  模型{mp*100:.1f}%  "
                  f"edge{edge*100:+.1f}%{_edge_tag(edge)}{draw_warn}{bus_zone_warn}")

        # ── DC 非共识标注 + GSV 1X2 实验假设对照（仅 GSV 触发场次）──
        _print_dc_nonconsensus(
            home, away,
            probs.get("home_win", 0), probs.get("draw", 0), probs.get("away_win", 0),
            cfg,
        )
        _print_gsv_experiment_line(
            home, away,
            probs.get("home_win", 0), probs.get("draw", 0), probs.get("away_win", 0),
            cfg,
        )

        # ── OU参考：OU-A（模型概率最高，≥0.48）─────────────────────────
        ou_opts = []
        ou_lines = value.get("ou_lines", {})
        ou_cfg   = cfg.get("ou_odds", {})
        for line, data in ou_lines.items():
            for side, key in [("over", 0), ("under", 1)]:
                mp_ou  = data[side]["model"]
                ed_ou  = data[side]["edge"]
                odds_ou = ou_cfg.get(line, (0, 0))[key]
                lbl_ou  = f"{'大' if side=='over' else '小'}{line}"
                if odds_ou and mp_ou >= 0.48:
                    ou_opts.append((mp_ou, lbl_ou, odds_ou, ed_ou))
        if ou_opts:
            ou_opts.sort(reverse=True)  # OU-A: 按模型概率降序
            mp, lbl, odds, edge = ou_opts[0]
            ou_bz_warn = "  ⚠Bus Zone双峰分布(OU参考)" if 150 <= _diff <= 300 else ""
            ou_ne_warn = "  ⚠近平场次OU参考(diff≤100)" if abs(_diff) <= 100 else ""
            print(f"    OU参考{lbl:<20} @{odds:.2f}  模型{mp*100:.1f}%  "
                  f"edge{edge*100:+.1f}%{_edge_tag(edge)}{ou_bz_warn}{ou_ne_warn}")

        # ── CS参考：前3波胆 ─────────────────────────────────────────────
        top_scores = probs.get("top_scores", [])
        cs_cfg     = cfg.get("cs_odds", {})
        cs_out = []
        for hg, ag, mp_cs in top_scores:
            key  = f"{hg}-{ag}"
            odds_cs = cs_cfg.get(key, 0)
            if odds_cs and mp_cs >= 0.05:
                edge_cs = mp_cs - 1 / odds_cs
                cs_out.append((mp_cs, f"比分 {hg}-{ag}", odds_cs, edge_cs))
        cs_out.sort(reverse=True)
        for i, (mp, lbl, odds, edge) in enumerate(cs_out[:3]):
            star = "★" if i == 0 else " "
            print(f"    CS参考{star}{lbl:<20} @{odds:.2f}  模型{mp*100:.1f}%  "
                  f"edge{edge*100:+.1f}%{_edge_tag(edge)}")

        # ── HT参考（仅当用户提供了HT赔率时显示）──────────────────────────
        ht_probs_r = result.get("ht_probs", {})
        ht_value_r = result.get("ht_value", {})
        if ht_probs_r:
            # HT 1X2 最高概率方向
            ht_dirs = [
                (ht_probs_r.get("ht_home_win", 0), f"HT {home}胜",
                 cfg.get("ht_1x2_odds", (0,0,0))[0] if cfg.get("ht_1x2_odds") else 0,
                 ht_value_r.get("home_win", {}).get("edge", 0)),
                (ht_probs_r.get("ht_draw", 0), "HT平局",
                 cfg.get("ht_1x2_odds", (0,0,0))[1] if cfg.get("ht_1x2_odds") else 0,
                 ht_value_r.get("draw", {}).get("edge", 0)),
                (ht_probs_r.get("ht_away_win", 0), f"HT {away}胜",
                 cfg.get("ht_1x2_odds", (0,0,0))[2] if cfg.get("ht_1x2_odds") else 0,
                 ht_value_r.get("away_win", {}).get("edge", 0)),
            ]
            ht_dirs.sort(reverse=True)
            ht_mp, ht_lbl, ht_odds, ht_edge = ht_dirs[0]
            ht_draw_p = ht_probs_r.get("ht_draw", 0)
            ht_draw_warn = f"  ⚠HT平局风险{ht_draw_p*100:.0f}%" if ht_draw_p >= 0.28 else ""
            _ht_draw_killed = bool(ht_value_r.get("draw", {}).get("killed"))
            if _ht_draw_killed:
                ht_draw_warn += "  [HT平局KILL:Elo差过大]"
            if ht_odds:
                print(f"    HT稳单{ht_lbl:<20} @{ht_odds:.2f}  模型{ht_mp*100:.1f}%  "
                      f"edge{ht_edge*100:+.1f}%{_edge_tag(ht_edge)}{ht_draw_warn}")
            else:
                print(f"    HT模型 {ht_lbl:<18} 模型{ht_mp*100:.1f}%"
                      f"  平{ht_draw_p*100:.1f}%{ht_draw_warn}")

            # HT OU参考：最高model概率≥0.50
            ht_ou_lines_v = ht_value_r.get("ou_lines", {})
            ht_ou_cfg     = cfg.get("ht_ou_odds", {})
            ht_ou_opts = []
            for line, data in ht_ou_lines_v.items():
                for side, key in [("over", 0), ("under", 1)]:
                    mp_ht = data[side]["model"]
                    ed_ht = data[side]["edge"]
                    odds_ht = ht_ou_cfg.get(line, (0, 0))[key]
                    lbl_ht  = f"HT{'大' if side=='over' else '小'}{line}"
                    if odds_ht and mp_ht >= 0.50:
                        ht_ou_opts.append((mp_ht, lbl_ht, odds_ht, ed_ht))
            if ht_ou_opts:
                ht_ou_opts.sort(reverse=True)
                mp, lbl, odds, edge = ht_ou_opts[0]
                print(f"    HTOU参考{lbl:<18} @{odds:.2f}  模型{mp*100:.1f}%  "
                      f"edge{edge*100:+.1f}%{_edge_tag(edge)}")

    print(f"{'═'*70}")


def run_matches(matches: list[dict], bankroll: float) -> list[dict]:
    results = []
    for m in matches:
        result = predict(
            home_team=m["home"],
            away_team=m["away"],
            odds_home=to_dec(m.get("odds_home")),
            odds_draw=to_dec(m.get("odds_draw")),
            odds_away=to_dec(m.get("odds_away")),
            odds_over25=to_dec(m.get("odds_over25")),
            odds_under25=to_dec(m.get("odds_under25")),
            bankroll=bankroll,
            ou_odds=m.get("ou_odds"),
            ah_odds=m.get("ah_odds"),
            cs_odds=m.get("cs_odds"),
            ht_1x2_odds=m.get("ht_1x2_odds"),
            ht_ou_odds=m.get("ht_ou_odds"),
            ht_ah_odds=m.get("ht_ah_odds"),
        )
        results.append({
            "home": m["home"],
            "away": m["away"],
            "result": result or {},
        })
    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--auto",     action="store_true", help="从 The Odds API 自动抓取赔率")
    parser.add_argument("--sync",     action="store_true", help="自动同步最新比赛结果")
    parser.add_argument("--bankroll", type=float, default=BANKROLL)
    parser.add_argument("--no-best",  action="store_true", help="只显示原始模型输出，不显示最优下注")
    parser.add_argument("--stable",   action="store_true", help="只显示稳单模式（高概率方向）")
    args = parser.parse_args()

    if args.sync:
        from src.data.results_sync import run as sync_results
        added = sync_results()
        if added:
            print(f"\n[sync] 新增 {added} 场赛果，建议重跑 python3 update_elo.py\n")

    if args.auto:
        if not ODDS_API_KEY:
            print("错误: config.py 中 ODDS_API_KEY 为空")
            sys.exit(1)
        from src.data.odds_api import get_todays_matches
        from src.data.odds_tracker import save_snapshot, print_movement_summary
        matches = get_todays_matches()
        if not matches:
            print("今日暂无即将开始的世界杯比赛")
            sys.exit(0)
        save_snapshot(matches)
        print_movement_summary(matches)
    else:
        matches = MANUAL_MATCHES
        from datetime import date as _date
        today_str = _date.today().isoformat()
        stale = [m for m in matches if m.get("date", "9999-99-99") < today_str]
        if stale:
            print(f"\n⚠  {len(stale)} 场比赛日期已过期，请更新 MANUAL_MATCHES\n")

    # ── Elo 新鲜度守卫（预测前必查，过期则拒跑）──────────────────────────
    from src.analysis.db_health import assert_elo_fresh
    assert_elo_fresh()

    results = run_matches(matches, args.bankroll)

    if not args.no_best:
        best_bets_report(results, matches)
        stable_bets_report(results, matches)


if __name__ == "__main__":
    main()
