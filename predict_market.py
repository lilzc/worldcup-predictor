#!/usr/bin/env python3
"""
B 系统：市场锚定确信度预测器  (B1 路线)

B 系统与 A 系统（today.py / predict.py）物理隔离：
- B 的 λ 来自 1X2 赔率反解，不使用 Elo/AD/apply_all
- B 不产生任何 edge / Kelly / stake 信号
- 合并展示仅在本文件末尾的分歧栏（单向只读 A 的推单标签）

用法:
  python3 predict_market.py --auto-today— 当日预测唯一入口：API拉赔率+情报+PDF（无需手填）
  python3 predict_market.py --hist      — 历史 ROI 诊断（walkforward 赔率）
  python3 predict_market.py --auto      — ⚠ LEGACY：MANUAL_MATCHES手填重放+情报，禁止用于当日预测
  python3 predict_market.py             — 重定向到 --auto-today 提示（裸跑不落手填路径）
"""
import sys
import argparse
import json
import io
import contextlib
sys.path.insert(0, ".")

from src.models.market_model import (
    market_predict, remove_margin_3way, apply_news_adj,
    build_b_matrix, _matrix_probs,
)

DISCLAIMER = (
    "本表为市场共识镜像的确信度分档，非价值/EV；"
    "高概率≠高价值，下注前自行对实时赔率。"
)
STAKE = 100  # 仅用于历史 ROI 诊断，不产生真实下注建议


# ── 展示辅助 ──────────────────────────────────────────────────────────────

def _bar(p: float, width: int = 16) -> str:
    filled = round(p * width)
    return "█" * filled + "░" * (width - filled)


def _fp(p: float) -> str:
    return f"{p:.1%}"


# ── 三档选取逻辑 ──────────────────────────────────────────────────────────

def _build_tiers(home: str, away: str, probs: dict) -> dict:
    p = probs

    # 保守：最高双重机会 + Over1.5
    dc_opts = [
        (f"双重机会 1X ({home}胜/平)", p["dc_1x"]),
        (f"双重机会 X2 (平/{away}胜)", p["dc_x2"]),
        (f"双重机会 12 (不平局)",       p["dc_12"]),
    ]
    best_dc = max(dc_opts, key=lambda x: x[1])
    conservative = [best_dc, ("大于1.5球", p["over15"])]

    # 综合：最可能 1X2 + OU 2.5 + BTTS
    one_x_two = max(
        [(f"{home}胜", p["home_win"], "H"),
         ("平局",       p["draw"],    "D"),
         (f"{away}胜",  p["away_win"], "A")],
        key=lambda x: x[1],
    )
    ou_25  = ("大于2.5球", p["over25"]) if p["over25"] >= 0.50 \
             else ("小于2.5球", 1 - p["over25"])
    btts_e = ("BTTS 双方进球", p["btts"]) if p["btts"] >= 0.50 \
             else ("BTTS 至少一方零封", 1 - p["btts"])
    combined = [(one_x_two[0], one_x_two[1]), ou_25, btts_e]

    # 激进：最强让球线 + Top 2 比分
    if p["home_win"] >= 0.70:
        ah_lbl, ah_p = f"{home} AH -1.5", p["ah15"]
    elif p["home_win"] >= 0.55:
        ah_lbl, ah_p = f"{home} AH -1.0", p["ah10"]
    elif p["away_win"] >= 0.55:
        ah_lbl, ah_p = f"{away} AH -1.0 (主受让)", 1 - p["ah10"]
    else:
        ah_lbl, ah_p = f"{home} AH -0.5", p["ah05"]
    ts = p["top_scores"]
    aggressive = [
        (ah_lbl, ah_p),
        (f"比分 {ts[0][0]}-{ts[0][1]}", ts[0][2]),
        (f"比分 {ts[1][0]}-{ts[1][1]}", ts[1][2]),
    ]

    return {
        "conservative": conservative,
        "combined":     combined,
        "combined_1x2": one_x_two,   # (label, prob, direction_key) for divergence
        "combined_ou":  ou_25,
        "aggressive":   aggressive,
        "best_dc":      best_dc,
    }


# ── 结果网格 ──────────────────────────────────────────────────────────────

def _results_grid(home: str, away: str, probs: dict, tiers: dict) -> None:
    ts = probs["top_scores"][:6]
    dc_lbl   = tiers["conservative"][0][0]
    comb_lbl = tiers["combined"][0][0]
    ou_lbl   = tiers["combined"][1][0]
    ah_lbl   = tiers["aggressive"][0][0]
    cs1_lbl  = tiers["aggressive"][1][0]

    print(f"  {'比分':<6} {'P':>6}  {'DC':^4} {'O1.5':^4} {comb_lbl[:6]:^6} {ou_lbl[:4]:^4} {ah_lbl[:8]:^8} {cs1_lbl[:6]:^6}")
    print(f"  {'─'*62}")

    for hg, ag, prob in ts:
        total = hg + ag
        # DC
        if "1X" in dc_lbl:    dc_ok = (hg >= ag)
        elif "X2" in dc_lbl:  dc_ok = (hg <= ag)
        else:                  dc_ok = (hg != ag)
        ou15_ok = total > 1.5
        # 综合 1X2
        if home + "胜" == comb_lbl:  c1_ok = hg > ag
        elif "平局" == comb_lbl:      c1_ok = hg == ag
        else:                          c1_ok = hg < ag
        # OU 2.5
        c2_ok = (total > 2.5) if "大于" in ou_lbl else (total < 2.5)
        # AH
        if "-1.5" in ah_lbl:        ah_ok = hg - ag > 1.5
        elif "-1.0" in ah_lbl and "受让" not in ah_lbl: ah_ok = hg - ag > 1.0
        elif "-1.0" in ah_lbl:      ah_ok = ag - hg < 1.0
        else:                        ah_ok = hg > ag
        # CS
        try:
            cs_hg, cs_ag = [int(x) for x in cs1_lbl.replace("比分 ", "").split("-")]
            cs_ok = (hg == cs_hg and ag == cs_ag)
        except Exception:
            cs_ok = False

        wins = sum([dc_ok, ou15_ok, c1_ok, c2_ok, ah_ok, cs_ok])
        fatal = "  ← 致命" if wins <= 1 else ""
        print(f"  {hg}-{ag}      {prob:.1%}  "
              f"{'✓' if dc_ok else '✗'}    "
              f"{'✓' if ou15_ok else '✗'}    "
              f"{'✓' if c1_ok else '✗'}      "
              f"{'✓' if c2_ok else '✗'}    "
              f"{'✓' if ah_ok else '✗'}         "
              f"{'✓' if cs_ok else '✗'}{fatal}")


# ── 单场 B 输出 ───────────────────────────────────────────────────────────

def print_match_b(m: dict, a_portfolio: list = None, news_data: dict | None = None) -> dict:
    """输出单场 B 预测。a_portfolio 为 A 系统的推单列表（仅用于分歧展示）。
    news_data = {flags: list[NewsFlag], adj: dict} 由 --auto 传入。"""
    home, away = m["home"], m["away"]
    ho = m.get("odds_home")
    do = m.get("odds_draw")
    ao = m.get("odds_away")
    flags = m.get("news_flags", [])

    if not all([ho, do, ao]):
        print(f"\n  {home} vs {away}: 缺少 1X2 赔率，跳过 B 输出")
        return {}

    r      = market_predict(home, away, ho, do, ao)
    probs  = r["probs"]
    diag   = r["diag"]
    mkt    = r["market"]
    tiers  = _build_tiers(home, away, probs)

    sep = "─" * 68
    print(f"\n  {sep}")
    print(f"  ⚖ B系统  {home}  vs  {away}  [{ho}/{do}/{ao}]")
    if flags:
        print(f"  ⚡ 情报: {' | '.join(flags)}")
    print(f"  {sep}")

    # 拟合残差 + DC管线自证（每场必打印）
    lam_val = diag["lam"]; mu_val = diag["mu"]
    print(f"  B1反解: λ={lam_val:.6f}(主)  μ={mu_val:.6f}(客)  λ+μ={lam_val+mu_val:.4f}")
    print(f"  DC管线证: Poisson等效 λ={diag['lam_poisson']:.6f}  Δλ={diag['delta_lam']:+.5f}  Δμ={diag['delta_mu']:+.5f}")
    if abs(diag["delta_lam"]) < 0.001 and abs(diag["delta_mu"]) < 0.001:
        print(f"  ⚠ Δλ/Δμ≈0：DC修正几乎无效（可能λ/μ极大或极小，DC项贡献可忽略）")
    print(f"  市场去水:  H {mkt['p_home']:.6f} / D {mkt['p_draw']:.6f} / A {mkt['p_away']:.6f}")
    print(f"  DC矩阵输出: H {probs['home_win']:.6f} / D {probs['draw']:.6f} / A {probs['away_win']:.6f}")
    print(f"  残差(DC-市场): H{diag['res_home']:+.6f}  D{diag['res_draw']:+.6f}  A{diag['res_away']:+.6f}")
    # 解释残差状态
    if abs(diag["res_home"]) < 1e-5 and abs(diag["res_away"]) < 1e-5:
        print(f"  残差≈0含义: 市场分布落在DC可达集内，精确解存在；"
              f"非管线绕过（管线证据:Δλ={diag['delta_lam']:+.3f}≠0）")
        print(f"  D残差由H+D+A=1确定，等价于-（H残差+A残差）≈0；边界命中场次(H>96%/D>70%)会有非零残差")
    else:
        print(f"  ⚠ H/A残差非零: 市场分布在DC可达集边界外（λ/μ被截断），DC最优近似而非精确拟合")
    if abs(diag["res_home"]) > 0.005 or abs(diag["res_away"]) > 0.005:
        print(f"  ⚠ 拟合残差>0.5%：市场落在DC可达集外，推导概率含近似误差，慎用")
    if not diag["converged"]:
        print(f"  ⚠ 优化未完全收敛，λ 为边界内最优近似")

    # 1X2
    print(f"\n  1X2 概率:")
    for lbl, p in [(f"{home}胜", probs["home_win"]),
                   ("平局",       probs["draw"]),
                   (f"{away}胜",  probs["away_win"])]:
        print(f"    {lbl:<22} {_bar(p)}  {_fp(p)}")

    # Top 8 比分
    print(f"\n  最可能比分 Top 8:")
    buf = []
    for hg, ag, p in probs["top_scores"]:
        buf.append(f"{hg}-{ag} {p:.1%}")
        if len(buf) == 4:
            print(f"    {'   │   '.join(buf)}")
            buf = []
    if buf:
        print(f"    {'   │   '.join(buf)}")

    # 衍生概率
    print(f"\n  衍生概率:")
    rows = [
        (f"大于1.5", probs["over15"]),   (f"大于2.5", probs["over25"]),
        (f"大于3.5", probs["over35"]),   (f"BTTS",    probs["btts"]),
        (f"双重机会1X", probs["dc_1x"]), (f"双重机会X2", probs["dc_x2"]),
        (f"双重机会12", probs["dc_12"]),
    ]
    line = ""
    for i, (lbl, p) in enumerate(rows):
        line += f"  {lbl}: {_fp(p)}"
        if (i + 1) % 3 == 0:
            print(f"   {line}")
            line = ""
    if line:
        print(f"   {line}")

    # 三档
    print(f"\n  🛡 保守 (高确信度):")
    for lbl, p in tiers["conservative"]:
        print(f"    {lbl:<42} {_bar(p)}  {_fp(p)}")

    print(f"\n  ⚖️ 综合 (主线):")
    for lbl, p in tiers["combined"]:
        print(f"    {lbl:<42} {_bar(p)}  {_fp(p)}")

    print(f"\n  🎲 激进 (高方差):")
    for lbl, p in tiers["aggressive"]:
        print(f"    {lbl:<42} {_bar(p)}  {_fp(p)}")

    # 结果网格
    print(f"\n  结果网格 (Top 6 比分 × 各档，致命=≤1档存活):")
    _results_grid(home, away, probs, tiers)

    # 一句话研判
    lam_mu = r["lam"] + r["mu"]
    dom    = home if probs["home_win"] > probs["away_win"] else away
    dom_p  = max(probs["home_win"], probs["away_win"])
    draw_p = probs["draw"]
    mkt_draw_diff = mkt["p_draw"] - probs["draw"]
    if dom_p > 0.60:
        verdict = f"{dom}主导，λ+μ={lam_mu:.2f}；平局B={draw_p:.0%} vs 市场={mkt['p_draw']:.0%}（差{mkt_draw_diff:+.0%}，Poisson结构偏差）"
    elif draw_p > 0.29:
        verdict = f"均势战，平局B={draw_p:.0%} vs 市场={mkt['p_draw']:.0%}（差{mkt_draw_diff:+.0%}）；λ+μ={lam_mu:.2f}"
    else:
        verdict = f"偏向客队，λ+μ={lam_mu:.2f}；平局B={draw_p:.0%} vs 市场={mkt['p_draw']:.0%}"
    print(f"\n  ★ {verdict}")

    # 自动情报（--auto 时显示）
    if news_data is not None:
        _print_auto_section(news_data, r)

    # A-vs-B 分歧
    b_comb = tiers["combined_1x2"][0]
    b_ou   = tiers["combined_ou"][0]
    if a_portfolio is not None:
        a_labels = [b.get("label", "") for b in a_portfolio if b.get("stake", 0) > 0]
        if a_labels:
            for al in a_labels[:4]:
                print(f"  [A推单] {al}")
            print(f"  [B综合] {b_comb}  /  {b_ou}")
            # 简单分歧检测：方向词匹配
            a_main = next((al for al in a_labels if "胜" in al or "Over" in al or "Under" in al), None)
            if a_main:
                a_dir = "H" if "主场胜" in a_main else ("A" if "客场胜" in a_main else "other")
                b_dir = tiers["combined_1x2"][2]
                if a_dir in ("H", "A") and b_dir in ("H", "A") and a_dir != b_dir:
                    print(f"  ⚡ A-vs-B 分歧: A 押{a_main[:8]} vs B综合{b_comb}")
                else:
                    print(f"  [A-B 方向一致或无法比较]")
        else:
            print(f"  [A: NO BET]  B综合方向: {b_comb}  /  {b_ou}")

    return {"home": home, "away": away, "tiers": tiers, "probs": probs,
            "lam": r["lam"], "mu": r["mu"], "market": r["market"]}


# ── 自动情报展示辅助 ─────────────────────────────────────────────────────

def _print_auto_section(news_data: dict, b_result: dict) -> None:
    """在 print_match_b 内部打印情报摘要 + λ调整对比。"""
    from src.data.auto_news import CONF_CONFIRMED, CONF_PREDICTED, CONF_SINGLE_SRC, CONF_INFERRED, CONF_NOT_FOUND

    flags = news_data.get("flags", [])
    adj   = news_data.get("adj", {})

    _CONF_ICON = {
        CONF_CONFIRMED:  "✓✓",
        CONF_PREDICTED:  "✓ ",
        CONF_SINGLE_SRC: "? ",
        CONF_INFERRED:   "≈ ",
        CONF_NOT_FOUND:  "— ",
    }

    ts = flags[0].timestamp if flags else "N/A"
    print(f"\n  ── 自动情报 [{ts}] ──────────────────────────────────────────")
    for flag in flags:
        icon  = _CONF_ICON.get(flag.confidence, "? ")
        orgs  = ", ".join(flag.sources[:2]) if flag.sources else "无来源"
        n_str = f"({flag.n_independent_orgs}独立源)" if flag.n_independent_orgs > 0 else "(0源)"
        lam   = f"  → {flag.lambda_impact}" if flag.lambda_impact else ""
        print(f"  [{icon}] {flag.confidence:<12}  {flag.content[:55]}")
        print(f"           来源: {orgs} {n_str}{lam}")

    # λ 调整汇总
    home_m = adj.get("home_mult", 1.0)
    away_m = adj.get("away_mult", 1.0)
    d_skew = adj.get("draw_skew", 0.0)
    audit  = adj.get("audit", [])

    if audit:
        print(f"\n  λ 调整汇总（仅 确定/预计 flags 生效）:")
        for a in audit:
            print(f"    {a}")
        if home_m != 1.0 or away_m != 1.0:
            print(f"    主队合计 ×{home_m:.3f}  客队合计 ×{away_m:.3f}")
        if d_skew != 0.0:
            print(f"    平局倾斜 {d_skew:+.3f}（不改λ矩阵，仅参考）")
    else:
        print(f"\n  λ 调整: 无（所有 flags 为 存疑/推理/未覆盖，不调整λ）")

    # 新闻调整后概率对比（仅当有实际λ调整时）
    if home_m != 1.0 or away_m != 1.0:
        lam_adj_val, mu_adj_val = apply_news_adj(b_result["lam"], b_result["mu"], home_m, away_m)
        mat_adj  = build_b_matrix(lam_adj_val, mu_adj_val)
        probs_adj = _matrix_probs(mat_adj)
        orig = b_result["probs"]
        home = b_result["home"]
        away = b_result["away"]
        print(f"\n  新闻调整概率对比 (λ ×{home_m:.3f} / μ ×{away_m:.3f}):")
        print(f"  {'':18} {'原始B':>7} {'新闻调后':>8} {'变化':>7}")
        for lbl, op, ap in [
            (home + "胜",  orig["home_win"], probs_adj["home_win"]),
            ("平局",         orig["draw"],    probs_adj["draw"]),
            (away + "胜",   orig["away_win"], probs_adj["away_win"]),
            ("Over 2.5",    orig["over25"],   probs_adj["over25"]),
        ]:
            print(f"  {lbl:<18} {op:>7.1%} {ap:>8.1%} {ap-op:>+7.1%}")

    print(f"  ── 情报结束 ────────────────────────────────────────────────────")


def _print_info_quality_summary(auto_news_map: dict) -> None:
    """打印所有场次情报质量汇总。"""
    from src.data.auto_news import (CONF_CONFIRMED, CONF_PREDICTED, CONF_SINGLE_SRC,
                                     CONF_INFERRED, CONF_NOT_FOUND)

    all_flags = [f for nd in auto_news_map.values() for f in nd["flags"]]
    counts = {c: 0 for c in [CONF_CONFIRMED, CONF_PREDICTED, CONF_SINGLE_SRC,
                               CONF_INFERRED, CONF_NOT_FOUND]}
    for f in all_flags:
        counts[f.confidence] = counts.get(f.confidence, 0) + 1

    n_adj = sum(1 for nd in auto_news_map.values()
                if nd["adj"]["home_mult"] != 1.0 or nd["adj"]["away_mult"] != 1.0)
    n_matches = len(auto_news_map)

    print(f"\n{'═'*70}")
    print(f"  自动情报质量汇总（{n_matches}场，共{len(all_flags)}条 flags）")
    print(f"{'═'*70}")
    print(f"  ✓✓ 确定      : {counts[CONF_CONFIRMED]:3d}  (≥2独立机构，内容明确)")
    print(f"  ✓  预计      : {counts[CONF_PREDICTED]:3d}  (1机构强信号或多机构模糊)")
    print(f"  ?  存疑(单源): {counts[CONF_SINGLE_SRC]:3d}  (仅1个机构，不动λ)")
    print(f"  ≈  推理      : {counts[CONF_INFERRED]:3d}  (积分/赛制推理，不动λ)")
    print(f"  —  搜索未覆盖: {counts[CONF_NOT_FOUND]:3d}  (空结果，不静默当作健康)")
    print(f"  实际调整λ场次: {n_adj}/{n_matches}")

    # 列出有实际λ调整的审计记录
    adj_rows = [(k, nd["adj"]) for k, nd in auto_news_map.items() if nd["adj"]["audit"]]
    if adj_rows:
        print(f"\n  λ 调整明细:")
        for (home, away), adj in adj_rows:
            print(f"    {home} vs {away}:")
            for a in adj["audit"]:
                print(f"      {a}")
    print(f"{'═'*70}")
    print(f"  ⚠ 情报均来自公开网络搜索，可能含过时/错误信息，关键注单请自行核验首发。")
    print(f"{'═'*70}")


# ── auto-today：API 数据源全自动模式 ─────────────────────────────────────

def run_auto_today() -> None:
    """
    --auto-today 主流程：
      fetch_today_matches() → B1 反解 → gather_match_news() → PDF 输出
    A 系统不参与（依赖 MANUAL_MATCHES，auto-today 不用手填）。
    """
    from src.data.odds_source import fetch_today_matches
    from src.data.auto_news import gather_match_news

    sep = "═" * 70

    print(f"\n{sep}")
    print(f"  B系统 --auto-today  |  数据源: the-odds-api / soccer_fifa_world_cup")
    print(f"  {DISCLAIMER}")
    print(f"{sep}")
    print()

    # ── 拉今日赛程 + 赔率 ────────────────────────────────────────────────
    result = fetch_today_matches(verbose=True)

    if not result.ok:
        # 配额耗尽/无赛程/网络错误 → 明确报错，不跌回旧数据
        print(f"\n  ✗ 无法获取今日赛程: {result.error}")
        print(f"  ⚠ 不跌回 MANUAL_MATCHES — 请检查 key/配额/网络后重试")
        print(f"{sep}")
        return

    if not result.matches:
        print(f"\n  今日 ({result.today_utc}) 无赛程（{result.error}）")
        print(f"{sep}")
        return

    # ── 顶部信息栏 ───────────────────────────────────────────────────────
    bms_used = list(dict.fromkeys(m.bookmaker for m in result.matches))  # 保序去重
    print(f"\n{sep}")
    print(f"  当前 UTC: {result.now_utc_iso}  |  今日场次: {len(result.matches)}  "
          f"|  剩余配额: {result.quota_remaining}")
    print(f"  赔率取自: {', '.join(bms_used)}")
    print(f"  B系统 = B1 路线（λ 来自市场 1X2 赔率反解，只拟合主客胜）")
    print(f"{sep}")

    # ── 自动搜情报 ───────────────────────────────────────────────────────
    print(f"\n  正在搜集赛前情报（每场约 4 条搜索）...")
    auto_news_map: dict = {}
    for mt in result.matches:
        sys.stderr.write(f"    搜索: {mt.home} vs {mt.away}...\n")
        sys.stderr.flush()
        flags, adj = gather_match_news(mt.home, mt.away, result.today_utc)
        auto_news_map[(mt.home, mt.away)] = {"flags": flags, "adj": adj}
    sys.stderr.write(f"    情报搜集完成\n")

    # ── 每场 B 输出 ──────────────────────────────────────────────────────
    b_results   = []
    match_infos = []   # for PDF
    for mt in result.matches:
        # 队名未匹配警告（仍出 B 预测）
        unmatched = []
        if not mt.home_matched:
            unmatched.append(f"{mt.home_raw}→{mt.home}(未在TEAM_ELO)")
        if not mt.away_matched:
            unmatched.append(f"{mt.away_raw}→{mt.away}(未在TEAM_ELO)")
        if unmatched:
            print(f"\n  ⚠ 队名未匹配 TEAM_ELO: {', '.join(unmatched)}")
            print(f"    B预测照常输出（市场λ反解不依赖TEAM_ELO），A系统无法对照")

        m_dict = {
            "home":       mt.home,
            "away":       mt.away,
            "odds_home":  mt.odds_home,
            "odds_draw":  mt.odds_draw,
            "odds_away":  mt.odds_away,
            "news_flags": [],
        }
        news_data = auto_news_map.get((mt.home, mt.away))

        # A 系统独立分析 — 必须在 print_match_b 之前，a_portfolio 才能传进去
        a_data = None
        a_portfolio = None
        try:
            import contextlib as _cl, io as _io
            from predict import predict as _a_predict
            from today import (compute_1x2_kill_results as _kill_fn,
                               _print_dc_nonconsensus, _print_gsv_experiment_line)
            _abuf = _io.StringIO()
            with _cl.redirect_stdout(_abuf):
                _ar = _a_predict(
                    home_team=mt.home, away_team=mt.away,
                    odds_home=mt.odds_home, odds_draw=mt.odds_draw,
                    odds_away=mt.odds_away,
                )
            if _ar:
                a_portfolio = _ar.get("portfolio", [])
                _kr = _kill_fn(
                    mt.home, mt.away,
                    _ar.get("value", {}),
                    mt.odds_home, mt.odds_draw, mt.odds_away,
                )
                a_data = {
                    "probs": _ar["probs"],
                    "value": _ar.get("value", {}),
                    "kill_results": _kr,
                }
        except Exception as _ae_err:
            sys.stderr.write(f"    ⚠ A分析失败({mt.home} vs {mt.away}): {_ae_err}\n")

        # 在 B1 输出块前打印 kickoff 信息
        print(f"\n  开球: {mt.commence_time}  ({mt.kickoff_delta})  "
              f"赔率 [{mt.odds_home}/{mt.odds_draw}/{mt.odds_away}] @{mt.bookmaker}")
        res = print_match_b(m_dict, a_portfolio=a_portfolio, news_data=news_data)
        if res:
            b_results.append(res)

        # DC 非共识标注 + GSV 实验假设对照（B 输出后每场末尾）
        if a_data:
            try:
                _probs = a_data["probs"]
                _print_dc_nonconsensus(
                    mt.home, mt.away,
                    _probs.get("home_win", 0), _probs.get("draw", 0), _probs.get("away_win", 0),
                    m_dict,
                )
                _print_gsv_experiment_line(
                    mt.home, mt.away,
                    _probs.get("home_win", 0), _probs.get("draw", 0), _probs.get("away_win", 0),
                    m_dict,
                )
            except Exception as _ann_err:
                sys.stderr.write(f"    ⚠ 非共识/GSV标注失败: {_ann_err}\n")

        match_infos.append({"mt": mt, "b": res, "news": news_data, "a": a_data})

    # ── 情报质量汇总 ─────────────────────────────────────────────────────
    if auto_news_map:
        _print_info_quality_summary(auto_news_map)

    # ── PDF 导出 ─────────────────────────────────────────────────────────
    if match_infos:
        try:
            from src.output.pdf_report import generate_pdf
            pdf_path = generate_pdf(result, match_infos, output_dir="output")
            print(f"\n  PDF报告已生成: {pdf_path}")
        except Exception as _pdf_err:
            print(f"\n  ⚠ PDF生成失败: {_pdf_err}")

    print(f"\n{sep}")
    print(f"  {DISCLAIMER}")
    print(f"  数据源: the-odds-api  |  剩余配额: {result.quota_remaining}")
    print(f"{sep}")


# ── 历史 ROI 诊断（只读，不产生下注信号）────────────────────────────────

def run_hist_roi_diagnostic():
    """
    用 walkforward.py 的历史赔率，诊断「机械跟 B 综合档 1X2 方向」的理论 ROI。
    结论预期为负（≈−vig）——这是市场定价必然，不是 B 的 bug。
    """
    from walkforward import MATCHES_ODDS

    with open("data/wc2026_results.json") as f:
        results_raw = json.load(f)["matches"]
    results_map = {(m["home"], m["away"]): (m["hg"], m["ag"]) for m in results_raw}

    print(f"\n{'═'*70}")
    print(f"  B系统 历史 ROI 诊断  (walkforward 41场历史赔率)")
    print(f"  方法: 机械跟 B综合档最可能 1X2 方向 + 保守档最高DC，平注 ¥{STAKE}")
    print(f"  警告: 市场锚定模型 ROI ≈ −vig 是市场定价必然，不是 B 的 bug")
    print(f"{'═'*70}")

    tiers_roi  = {"保守DC": [0, 0, 0, 0], "综合1X2": [0, 0, 0, 0]}  # stake,pnl,wins,n
    flat_combined = flat_conservative = 0
    n_total = n_no_result = 0

    rows = []
    for (home, away, date), odds_e in sorted(MATCHES_ODDS.items(), key=lambda x: x[0][2]):
        ho, do, ao = odds_e["1x2"]
        key = (home, away)
        if key not in results_map:
            n_no_result += 1
            continue
        hg, ag = results_map[key]
        n_total += 1

        r     = market_predict(home, away, ho, do, ao)
        probs = r["probs"]
        tiers = _build_tiers(home, away, probs)

        # 综合档：最可能 1X2 方向
        comb_dir, comb_p, comb_key = tiers["combined_1x2"]
        if comb_key == "H":    comb_odds = ho; comb_won = (hg > ag)
        elif comb_key == "A":  comb_odds = ao; comb_won = (hg < ag)
        else:                   comb_odds = do; comb_won = (hg == ag)

        # 保守档：最高双重机会
        dc_lbl, dc_p = tiers["best_dc"]
        if "1X" in dc_lbl:    dc_won = (hg >= ag)
        elif "X2" in dc_lbl:  dc_won = (hg <= ag)
        else:                  dc_won = (hg != ag)
        # DC 赔率 = 对应两向组合，用三向赔率近似（去水后）
        p_h_m, p_d_m, p_a_m = remove_margin_3way(ho, do, ao)
        if "1X" in dc_lbl:    dc_odds_approx = 1 / (p_h_m + p_d_m)
        elif "X2" in dc_lbl:  dc_odds_approx = 1 / (p_d_m + p_a_m)
        else:                  dc_odds_approx = 1 / (p_h_m + p_a_m)

        pnl_comb = STAKE * (comb_odds - 1) if comb_won else -STAKE
        pnl_dc   = STAKE * (dc_odds_approx - 1) if dc_won else -STAKE

        flat_combined    += pnl_comb
        flat_conservative += pnl_dc

        rows.append({
            "match": f"{home[:10]} v {away[:10]}",
            "date": date,
            "actual": f"{hg}-{ag}",
            "comb": (comb_dir[:12], comb_odds, comb_won, pnl_comb),
            "dc":   (dc_lbl[:18], dc_odds_approx, dc_won, pnl_dc),
        })

    total_stake = n_total * STAKE
    roi_comb = flat_combined / total_stake * 100
    roi_dc   = flat_conservative / total_stake * 100

    print(f"\n  {'对阵':<22} {'实际':>5}  {'综合方向':<14} {'赔率':>5} {'结果':>5} {'P&L':>7}"
          f"  {'保守DC':<18} {'近似赔率':>6} {'结果':>5} {'P&L':>7}")
    print(f"  {'─'*100}")
    for r in rows:
        cd, co, cw, cp = r["comb"]
        dd, da, dw, dp = r["dc"]
        sym_c = "✓" if cw else "✗"
        sym_d = "✓" if dw else "✗"
        print(f"  {r['match']:<22} {r['actual']:>5}  {cd:<14} {co:>5.2f} {sym_c:>5} {cp:>+7.0f}"
              f"  {dd:<18} {da:>6.2f} {sym_d:>5} {dp:>+7.0f}")

    import math as _math
    c_wins = sum(1 for r in rows if r["comb"][2])
    d_wins = sum(1 for r in rows if r["dc"][2])
    avg_c_odds = sum(r["comb"][1] for r in rows) / n_total
    avg_d_odds = sum(r["dc"][1] for r in rows) / n_total
    d_sigma = _math.sqrt(d_wins/n_total * (1 - d_wins/n_total) / n_total) * avg_d_odds * 100

    print(f"\n{'─'*70}")
    print(f"  总计 {n_total} 场（{n_no_result} 场无结果跳过）")
    print(f"\n  赔率基准说明:")
    print(f"    综合档1X2: 使用真实市场赔率（含庄家vig）→ 理论基准 ≈ −vig")
    print(f"    保守档DC:  使用理论无水价格 1/(p_dc)（非真实DC市场赔率）→ 理论基准 = 0%")
    print(f"\n  综合档1X2  {c_wins}W/{n_total-c_wins}L  平均赔率{avg_c_odds:.3f}  P&L¥{flat_combined:+.0f}  ROI {roi_comb:+.1f}%")
    print(f"    基准: −vig（约−5至−8%）  实际: {roi_comb:+.1f}%  超出基准≈{roi_comb-(-6):+.1f}pp")
    print(f"    σ≈{avg_c_odds * _math.sqrt(c_wins/n_total*(1-c_wins/n_total)/n_total)*100:.1f}%  →  结论: 随机波动，无超额alpha")
    print(f"\n  保守档DC   {d_wins}W/{n_total-d_wins}L  平均赔率{avg_d_odds:.3f}  P&L¥{flat_conservative:+.0f}  ROI {roi_dc:+.1f}%")
    n_sigma = roi_dc / d_sigma
    print(f"    基准: 0%（无水公平赔率）  实际: {roi_dc:+.1f}%  {n_sigma:.2f}σ")
    print(f"    σ={d_sigma:.1f}%  →  {abs(n_sigma):.1f}σ {'<2σ 噪声范围内' if abs(n_sigma)<2 else '≥2σ 统计显著，需追查'}")
    print(f"    偏高原因: ①始终选最高DC档(>65%)造成选择偏差 ②无水基准已去掉DC真实vig(≈2-4%)")
    print(f"              ③若用真实DC市场赔率，+{roi_dc:.1f}%会降至约+{max(0,roi_dc-3):.1f}%至+{max(0,roi_dc-2):.1f}%，回到噪声区间")


# ── 主函数 ────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--hist",       action="store_true", help="历史 ROI 诊断")
    parser.add_argument("--auto",       action="store_true",
                        help="[LEGACY] MANUAL_MATCHES手填重放+情报，禁止当日预测")
    parser.add_argument("--auto-today", action="store_true", dest="auto_today",
                        help="当日预测唯一入口：API拉今日赔率+情报+PDF")
    args = parser.parse_args()

    if args.hist:
        run_hist_roi_diagnostic()
        return

    if args.auto_today:
        run_auto_today()
        return

    # 裸跑（无参数）→ 提示正确入口，拒绝落入 MANUAL_MATCHES
    if not args.auto:
        print(f"\n{'═'*70}")
        print(f"  当日预测唯一入口: python3 predict_market.py --auto-today")
        print(f"  裸跑已禁止落入 MANUAL_MATCHES（过期快照，2026-07-03 封存）")
        print(f"  如需历史场次重放: python3 predict_market.py --auto  (LEGACY)")
        print(f"  如需历史 ROI 诊断: python3 predict_market.py --hist")
        print(f"{'═'*70}")
        return

    # ── LEGACY --auto 重放路径 ──────────────────────────────────────────────
    from today import MANUAL_MATCHES, run_matches, BANKROLL

    print(f"\n{'═'*70}")
    print(f"  ⚠ LEGACY: --auto 模式仅供指定场次重放/回测，禁止用于当日预测")
    print(f"  ⚠ 当日预测唯一入口 = python3 predict_market.py --auto-today")
    print(f"  ⚠ MANUAL_MATCHES 为过期小组赛快照（2026-07-03 封存），数据已失效")
    print(f"{'─'*70}")
    print(f"  {DISCLAIMER}")
    print(f"  ⚠ 情报由 DDG 搜索，多源交叉但可能含过时信息，关键注单请自行核验。")
    print(f"{'═'*70}")
    print(f"  B系统 = B1 路线（λ 来自市场 1X2 赔率反解，选项 B：只拟合主客胜）")
    print(f"  平局及所有下游概率由反解 λ 的 DC score_matrix 统一产出")
    print(f"{'═'*70}")

    # --auto：先搜情报（每场约 4×1s 搜索）
    auto_news_map: dict = {}
    if args.auto:
        from src.data.auto_news import gather_match_news
        print(f"\n  正在搜集赛前情报（每场约 4 条搜索，每条间隔 ~1s）...")
        for m in MANUAL_MATCHES:
            home, away = m["home"], m["away"]
            sys.stderr.write(f"    搜索: {home} vs {away}...\n")
            sys.stderr.flush()
            flags, adj = gather_match_news(home, away, m.get("date", ""))
            auto_news_map[(home, away)] = {"flags": flags, "adj": adj}
        sys.stderr.write(f"    情报搜集完成 ({len(MANUAL_MATCHES)} 场)\n")

    # 先跑 A 的 run_matches（静默，A 的 print 被捕获到 /dev/null）
    _buf = io.StringIO()
    with contextlib.redirect_stdout(_buf):
        a_results = run_matches(MANUAL_MATCHES, BANKROLL)
    a_map = {(r["home"], r["away"]): r["result"].get("portfolio", [])
             for r in a_results}

    b_results = []
    for m in MANUAL_MATCHES:
        a_port   = a_map.get((m["home"], m["away"]), [])
        news_data = auto_news_map.get((m["home"], m["away"])) if args.auto else None
        res = print_match_b(m, a_portfolio=a_port, news_data=news_data)
        if res:
            b_results.append(res)

    # 汇总分歧
    print(f"\n{'═'*70}")
    print(f"  A-vs-B 分歧汇总（仅供人工甄别）")
    print(f"{'═'*70}")
    for br in b_results:
        b_dir = br["tiers"]["combined_1x2"][0]
        b_ou  = br["tiers"]["combined_ou"][0]
        home, away = br["home"], br["away"]
        a_port = a_map.get((home, away), [])
        a_active = [b for b in a_port if b.get("stake", 0) > 0]
        a_str = ", ".join(b["label"][:20] for b in a_active[:3]) if a_active else "NO BET"
        print(f"  {home:<14} vs {away:<14}  B综合={b_dir}/{b_ou:<10}  A推={a_str}")

    # --auto：信息质量汇总
    if args.auto and auto_news_map:
        _print_info_quality_summary(auto_news_map)

    print(f"\n{'═'*70}")
    print(f"  {DISCLAIMER}")
    print(f"{'═'*70}")


if __name__ == "__main__":
    main()
