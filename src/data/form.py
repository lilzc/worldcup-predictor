import csv
import io
import os
from datetime import datetime

import requests

CSV_URL = "https://raw.githubusercontent.com/martj42/international_results/master/results.csv"
CACHE_PATH = "data/cache/international_results.csv"
CACHE_TTL = 43200  # 12h — martj42 每日更新，3天 TTL 导致赛果滞后

from config import BASE_GOALS as INTL_GOALS_AVG

# martj42数据集队名 → 本地config队名
NAME_MAP = {
    "United States":     "USA",
    "Ivory Coast":       "Ivory Coast",
    "Côte d'Ivoire":     "Ivory Coast",
    "South Korea":       "South Korea",
    "Korea Republic":    "South Korea",
    "DR Congo":          "Congo DR",
    "Curaçao":           "Curacao",
    "Iran":              "Iran",
    "Bosnia and Herzegovina": "Bosnia",
    "Czech Republic":    "Czechia",
    "Netherlands":       "Netherlands",
}

# 本地config队名 → martj42数据集队名（反向查找用）
_REVERSE_MAP: dict | None = None


def _reverse_name_map() -> dict:
    global _REVERSE_MAP
    if _REVERSE_MAP is None:
        _REVERSE_MAP = {v: k for k, v in NAME_MAP.items()}
    return _REVERSE_MAP


def _fetch_csv() -> str:
    os.makedirs("data/cache", exist_ok=True)
    if os.path.exists(CACHE_PATH):
        if (datetime.now().timestamp() - os.path.getmtime(CACHE_PATH)) < CACHE_TTL:
            with open(CACHE_PATH, encoding="utf-8") as f:
                return f.read()
    resp = requests.get(CSV_URL, timeout=30)
    resp.raise_for_status()
    content = resp.text
    with open(CACHE_PATH, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"[form] 已更新国际比赛记录缓存")
    return content


def _default_form() -> dict:
    return {
        "goals_for_avg": INTL_GOALS_AVG,
        "goals_against_avg": INTL_GOALS_AVG,
        "win_rate": 0.33,
        "form_factor": 1.0,
        "matches_found": 0,
    }


def get_team_form(team_name: str, n: int = 7, before_date: str = None) -> dict:
    """
    从国际比赛历史CSV中提取队伍近n场数据，返回形态系数。
    form_factor > 1.0 表示近期进球高于均值，< 1.0 表示低于均值。
    """
    try:
        content = _fetch_csv()
    except Exception as e:
        print(f"[form] 获取数据失败({team_name}): {e}，使用默认值")
        return _default_form()

    # 构建数据集中可能用的名字（含别名）
    reverse = _reverse_name_map()
    dataset_aliases = set()
    dataset_aliases.add(team_name)                          # 直接用本地名
    dataset_aliases.add(reverse.get(team_name, team_name)) # 反查别名
    for ds_name, local_name in NAME_MAP.items():
        if local_name == team_name:
            dataset_aliases.add(ds_name)

    reader = csv.DictReader(io.StringIO(content))
    matches = []
    for row in reader:
        home = row.get("home_team", "")
        away = row.get("away_team", "")
        if home not in dataset_aliases and away not in dataset_aliases:
            continue
        if before_date and row.get("date", "") >= before_date:
            continue
        try:
            date = datetime.strptime(row["date"], "%Y-%m-%d")
            hs = int(row["home_score"])
            as_ = int(row["away_score"])
        except (ValueError, KeyError):
            continue
        if home in dataset_aliases:
            gf, ga = hs, as_
        else:
            gf, ga = as_, hs
        matches.append({"date": date, "gf": gf, "ga": ga})

    if len(matches) < 3:
        return _default_form()

    matches.sort(key=lambda x: x["date"])
    recent = matches[-n:]

    total = len(recent)
    gf_avg = sum(m["gf"] for m in recent) / total
    ga_avg = sum(m["ga"] for m in recent) / total
    win_rate = sum(1 for m in recent if m["gf"] > m["ga"]) / total

    # 形态系数：近期进球 vs 国际均值，用0.3次方弱化极端值（避免暴涨暴跌）
    raw_factor = gf_avg / INTL_GOALS_AVG
    form_factor = raw_factor ** 0.3
    form_factor = max(0.75, min(1.35, form_factor))  # 限制在±35%以内

    return {
        "goals_for_avg": round(gf_avg, 2),
        "goals_against_avg": round(ga_avg, 2),
        "win_rate": round(win_rate, 2),
        "form_factor": round(form_factor, 3),
        "matches_found": total,
    }


def print_form_report(teams: list[str]):
    """调试用：打印各队形态数据"""
    print(f"\n  {'队伍':<20} {'近7场进球/场':>12} {'失球/场':>10} {'胜率':>8} {'形态系数':>10}")
    print(f"  {'─'*62}")
    for team in teams:
        f = get_team_form(team)
        bar = "▲" if f["form_factor"] > 1.0 else "▼"
        print(
            f"  {team:<20} {f['goals_for_avg']:>12.2f} {f['goals_against_avg']:>10.2f}"
            f" {f['win_rate']:>8.0%} {bar}{f['form_factor']:>9.3f}"
            f"  ({f['matches_found']}场)"
        )
