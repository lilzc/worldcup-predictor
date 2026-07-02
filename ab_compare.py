#!/usr/bin/env python3
"""
A vs B 1X2 回测对比
A = 模型 (replay截断 Elo/AD，无前视)
B = 市场去水概率 (MATCHES_ODDS 1X2 赔率，norm 去vig)

输出：①各自成绩单  ②分歧场次专项  ③一致场次汇总
"""
import sys
import math
import json

sys.path.insert(0, ".")

from src.models.poisson import score_matrix, matrix_to_probs, compute_ad_factor
from src.models.adjustments import apply_all
from src.betting.kelly import remove_margin
from config import (
    TEAM_ELO, BASE_GOALS, ELO_SCALE,
    GSV_LAMBDA_FACTOR, GSV_LAMBDA_ELO_MIN,
    GSV_LAMBDA_DIFF_MIN, GSV_LAMBDA_DIFF_MAX,
    GSV_LAMBDA_FACTOR_EXTENDED, GSV_LAMBDA_DIFF_EXTENDED,
    AD_ENABLED,
)
from walkforward import MATCHES_ODDS, _wf_elo_update, _wf_ad_update, _wf_ad_exp, _build_mat_custom

_K = 60


def _actual_outcome(hg, ag):
    if hg > ag:  return "H"
    if hg == ag: return "D"
    return "A"


def _argmax_label(h, d, a):
    if h >= d and h >= a: return "H"
    if d >= h and d >= a: return "D"
    return "A"


def _brier(ph, pd, pa, outcome):
    sh = 1.0 if outcome == "H" else 0.0
    sd = 1.0 if outcome == "D" else 0.0
    sa = 1.0 if outcome == "A" else 0.0
    return (ph - sh)**2 + (pd - sd)**2 + (pa - sa)**2


def _label(code, home, away):
    return {"H": f"{home}胜", "D": "平局", "A": f"{away}胜"}[code]


# 已知 walkforward 哪些注属于推单
# 重跑 walkforward 收集 rows → 用于交叉分析
def collect_wf_bets():
    from walkforward import run_walkforward
    import io, contextlib
    buf = io.StringIO()
    rows = []
    with open("data/wc2026_results.json") as f:
        all_results = json.load(f)["matches"]

    odds_lookup = {(h, a): v for (h, a, _d), v in MATCHES_ODDS.items()}
    elo = dict(TEAM_ELO)
    ad_state = {}
    for m in all_results:
        home, away = m["home"], m["away"]
        hg, ag = m["hg"], m["ag"]
        he = elo.get(home, 1700.0)
        ae = elo.get(away, 1700.0)
        if (home, away) in odds_lookup:
            mat, probs, diff, gsv = _build_mat_custom(home, away, he, ae, ad_state if AD_ENABLED else {})
            from walkforward import _scan_from_probs
            cands = _scan_from_probs(home, away, hg, ag, odds_lookup[(home, away)], mat, probs)
            if cands:
                b = cands[0]
                edge, lbl, odds_val, mo, tm, gap, outcome, pv, mtype = b
                rows.append({
                    "home": home, "away": away,
                    "bet_label": lbl, "mtype": mtype,
                    "outcome": outcome, "pnl": pv,
                })
        exp_h = _wf_ad_exp(he, ae)
        exp_a = _wf_ad_exp(ae, he)
        _wf_ad_update(ad_state, home, hg, ag, exp_h, exp_a)
        _wf_ad_update(ad_state, away, ag, hg, exp_a, exp_h)
        elo = _wf_elo_update(elo, home, away, hg, ag)
    return rows


def run():
    with open("data/wc2026_results.json") as f:
        all_results = json.load(f)["matches"]

    odds_lookup = {(h, a): (d, v) for (h, a, d), v in MATCHES_ODDS.items()}

    elo = dict(TEAM_ELO)
    ad_state = {}

    records = []  # 每条：有赔率场次完整对比记录

    for m in all_results:
        home, away = m["home"], m["away"]
        hg, ag = m["hg"], m["ag"]
        he = elo.get(home, 1700.0)
        ae = elo.get(away, 1700.0)

        if (home, away) in odds_lookup:
            date_key, odds_entry = odds_lookup[(home, away)]
            result_date = m.get("date", "")

            # ── A：模型预测（赛前 Elo/AD，无前视）
            mat, probs, diff, gsv = _build_mat_custom(
                home, away, he, ae, ad_state if AD_ENABLED else {}
            )
            a_h = probs["home_win"]
            a_d = probs["draw"]
            a_a = probs["away_win"]

            # ── B：市场去水概率
            ho, do, ao = odds_entry["1x2"]
            b_h, b_d, b_a = remove_margin([1/ho, 1/do, 1/ao])

            actual = _actual_outcome(hg, ag)
            a_pick = _argmax_label(a_h, a_d, a_a)
            b_pick = _argmax_label(b_h, b_d, b_a)

            records.append({
                "home": home, "away": away,
                "date": result_date,
                "hg": hg, "ag": ag,
                "actual": actual,
                "a_h": a_h, "a_d": a_d, "a_a": a_a,
                "b_h": b_h, "b_d": b_d, "b_a": b_a,
                "a_pick": a_pick, "b_pick": b_pick,
                "a_hit": a_pick == actual,
                "b_hit": b_pick == actual,
                "diverge": a_pick != b_pick,
                "a_brier": _brier(a_h, a_d, a_a, actual),
                "b_brier": _brier(b_h, b_d, b_a, actual),
            })

        # 赛后更新
        exp_h = _wf_ad_exp(he, ae)
        exp_a = _wf_ad_exp(ae, he)
        _wf_ad_update(ad_state, home, hg, ag, exp_h, exp_a)
        _wf_ad_update(ad_state, away, ag, hg, exp_a, exp_h)
        elo = _wf_elo_update(elo, home, away, hg, ag)

    # ── 收集 walkforward 推单（用于交叉分析）
    wf_bets = collect_wf_bets()
    wf_set = {(b["home"], b["away"]): b for b in wf_bets}

    N = len(records)
    div_records  = [r for r in records if r["diverge"]]
    agree_records = [r for r in records if not r["diverge"]]

    # ────────────────────────────────────────────────────────────────────────────
    print("=" * 72)
    print(f"  A vs B 1X2 回测对比  |  N={N} 场（有赛前1X2赔率）")
    print("=" * 72)

    # ① 各自成绩单
    a_hits = sum(1 for r in records if r["a_hit"])
    b_hits = sum(1 for r in records if r["b_hit"])
    a_brier = sum(r["a_brier"] for r in records) / N
    b_brier = sum(r["b_brier"] for r in records) / N

    print("\n─────────────────────────────────────────────────────────────────────")
    print("  ① 各自成绩单")
    print("─────────────────────────────────────────────────────────────────────")
    print(f"  {'':30s}  {'1X2命中率':>10}  {'Brier↓':>8}")
    print(f"  {'─'*58}")
    print(f"  {'A  (模型，replay截断Elo/AD)':30s}  {a_hits}/{N}={a_hits/N*100:.1f}%  "
          f"  {a_brier:.4f}")
    print(f"  {'B  (市场去水概率)':30s}  {b_hits}/{N}={b_hits/N*100:.1f}%  "
          f"  {b_brier:.4f}")
    print()
    print("  注: B ≈ 市场去水概率，此成绩即市场基准线。")
    print("      B 高于 A 是结构必然（镜像 vs 独立模型），不构成 B 更优的证据。")
    print("      市场定价本身融合了海量信息，B 的 Brier 接近理论下界；")
    print("      A 与 B 的有效对比单元是②分歧场次，不是总准确率排名。")

    # ② 分歧场次专项
    print("\n─────────────────────────────────────────────────────────────────────")
    print(f"  ② 分歧场次专项（A 与 B 最高概率方向不一致，N={len(div_records)}）")
    if len(div_records) < 10:
        print("  ⚠ 分歧样本不足 (<10)，以下数据仅供参考，无统计效力。")
    print("─────────────────────────────────────────────────────────────────────")

    if div_records:
        print(f"  {'场次':32s}  {'A押':8s}  {'B押':8s}  {'实际':6s}  {'A':3s}  {'B':3s}")
        print(f"  {'─'*68}")
        a_div_hit = 0
        b_div_hit = 0
        for r in div_records:
            home, away = r["home"], r["away"]
            a_lbl = _label(r["a_pick"], home, away)
            b_lbl = _label(r["b_pick"], home, away)
            act_lbl = _label(r["actual"], home, away)
            a_ok = "✓" if r["a_hit"] else "✗"
            b_ok = "✓" if r["b_hit"] else "✗"
            if r["a_hit"]: a_div_hit += 1
            if r["b_hit"]: b_div_hit += 1
            match_str = f"{home} vs {away}"
            print(f"  {match_str:32s}  {a_lbl:8s}  {b_lbl:8s}  {act_lbl:6s}  {a_ok}    {b_ok}")

        n_div = len(div_records)
        print(f"\n  分歧场次 N={n_div}，A 对 {a_div_hit} 场，B 对 {b_div_hit} 场")

        # 交叉：这些分歧场次里有多少进了 walkforward 推单
        div_in_wf = []
        for r in div_records:
            key = (r["home"], r["away"])
            if key in wf_set:
                wb = wf_set[key]
                div_in_wf.append((r, wb))

        print(f"\n  ── 交叉：分歧场次 × walkforward 推单 ──")
        if div_in_wf:
            wf_pnl_total = 0.0
            print(f"  {'场次':28s}  {'A押/B押':18s}  {'WF推单':22s}  {'结果':8s}  {'P&L':>8s}")
            print(f"  {'─'*92}")
            for r, wb in div_in_wf:
                match_str = f"{r['home']} vs {r['away']}"
                ab_str = f"A:{_label(r['a_pick'],r['home'],r['away'])}/B:{_label(r['b_pick'],r['home'],r['away'])}"
                wf_pnl_total += wb["pnl"]
                print(f"  {match_str:28s}  {ab_str:18s}  {wb['bet_label']:22s}  "
                      f"{wb['outcome']:8s}  ¥{wb['pnl']:+.0f}")
            print(f"\n  分歧场次中有 {len(div_in_wf)}/{n_div} 场进入WF推单，"
                  f"P&L合计: ¥{wf_pnl_total:+.0f}")
            print()
            if a_div_hit > b_div_hit:
                print("  解读: A 在分歧场次命中率高于 B——若 alpha 成立，应在分歧场次贡献正 P&L。")
            elif a_div_hit < b_div_hit:
                print("  解读: A 在分歧场次命中率低于 B——分歧场次 A 的判断未跑赢市场共识。")
            else:
                print("  解读: A、B 在分歧场次命中相同——无方向证据。")
            if len(div_in_wf) < 5:
                print(f"  ⚠ 分歧场次中进推单的仅 {len(div_in_wf)} 场，P&L 参考价值有限。")
        else:
            print(f"  分歧场次 {n_div} 场中无一进入 walkforward 推单（edge/gap条件均不满足）。")
            print("  A 的分歧尚无推单层面的 P&L 证据，alpha/噪声待判。")
    else:
        print("  无分歧场次。")

    # ③ 一致场次
    n_agree = len(agree_records)
    agree_hit = sum(1 for r in agree_records if r["a_hit"])
    print("\n─────────────────────────────────────────────────────────────────────")
    print(f"  ③ 一致场次（A、B 同向，N={n_agree}）")
    print("─────────────────────────────────────────────────────────────────────")
    if n_agree:
        print(f"  共同命中率: {agree_hit}/{n_agree} = {agree_hit/n_agree*100:.1f}%  "
              f"（双方在此无差别，仅报数字）")
    else:
        print("  无一致场次。")

    print()
    print("─────────────────────────────────────────────────────────────────────")
    print("  结论说明（禁区）：")
    print("  · 不以 B 准确率高低评判 B 优于 A——市场去水≡参照系，不是竞争者")
    print("  · 分析焦点唯一: ②分歧场次 A 的命中 + 推单 P&L 是否显著正")
    print("─────────────────────────────────────────────────────────────────────")


if __name__ == "__main__":
    run()
