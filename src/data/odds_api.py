import requests
import json
import os
from datetime import datetime, timezone
from config import ODDS_API_KEY

BASE_URL = "https://api.the-odds-api.com/v4"
CACHE_DIR = "data/cache"

# The Odds API uses its own team name format — map to our config keys
API_NAME_MAP = {
    "United States": "USA",
    "Cote d'Ivoire": "Ivory Coast",
    "Korea Republic": "South Korea",
    "Curacao": "Curacao",
    "DR Congo": "Congo DR",
    "Netherlands": "Netherlands",
    "Iran (Islamic Republic of)": "Iran",
    "Czechia": "Czechia",
    "Bosnia and Herzegovina": "Bosnia",
}


def _cache_path(key: str) -> str:
    return os.path.join(CACHE_DIR, f"{key}.json")


def _load_cache(key: str, ttl_seconds: int = 3600) -> dict | None:
    path = _cache_path(key)
    if not os.path.exists(path):
        return None
    mtime = os.path.getmtime(path)
    if (datetime.now().timestamp() - mtime) > ttl_seconds:
        return None
    try:
        with open(path) as f:
            return json.load(f)
    except (json.JSONDecodeError, OSError):
        os.unlink(path)
        return None


def _save_cache(key: str, data: dict):
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(_cache_path(key), "w") as f:
        json.dump(data, f)


def fetch_live_odds(sport: str = "soccer_fifa_world_cup") -> list[dict]:
    """
    Fetch live odds from The Odds API.
    Supported markets for soccer_fifa_world_cup: h2h, totals (2.5 only), spreads.
    Note: asian_handicaps and alternate_totals are NOT supported by this API for WC.
    AH lines must be entered manually from your betting app.
    """
    if not ODDS_API_KEY:
        print("警告: ODDS_API_KEY 未设置，无法获取实时赔率")
        return []

    cache_key = f"odds_{sport}_v3"
    cached = _load_cache(cache_key, ttl_seconds=900)  # 15min cache
    if cached:
        return cached

    url = f"{BASE_URL}/sports/{sport}/odds"
    params = {
        "apiKey": ODDS_API_KEY,
        "regions": "eu,uk,au",
        "markets": "h2h,totals,spreads",
        "oddsFormat": "decimal",
    }
    resp = requests.get(url, params=params, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    if not data:
        raise ValueError(f"The Odds API 返回空数据（sport={sport}），请检查 API key")
    _save_cache(cache_key, data)
    remaining = resp.headers.get("x-requests-remaining", "?")
    print(f"[odds_api] 抓取成功，剩余请求次数: {remaining}/月")
    return data


def normalize_team(name: str) -> str:
    return API_NAME_MAP.get(name, name)


def get_todays_matches() -> list[dict]:
    """
    Fetch today's WC odds from Pinnacle.
    Returns list of dicts compatible with today.py MATCHES format.
    """
    events = fetch_live_odds()
    matches = []
    now = datetime.now(timezone.utc).timestamp()

    for event in events:
        commence = event.get("commence_time", "")
        try:
            from datetime import datetime as dt
            ct = dt.fromisoformat(commence.replace("Z", "+00:00")).timestamp()
            if ct < now - 7200:   # already started >2h ago, skip
                continue
        except Exception:
            pass

        parsed = parse_match_odds(event)
        if not all([parsed["odds_home"], parsed["odds_draw"], parsed["odds_away"]]):
            continue

        matches.append({
            "home": normalize_team(parsed["home_team"]),
            "away": normalize_team(parsed["away_team"]),
            "odds_home":    parsed["odds_home"],
            "odds_draw":    parsed["odds_draw"],
            "odds_away":    parsed["odds_away"],
            "odds_over25":  parsed["odds_over25"],
            "odds_under25": parsed["odds_under25"],
            "ah_line":      parsed["ah_line"],
            "odds_ah_home": parsed["odds_ah_home"],
            "odds_ah_away": parsed["odds_ah_away"],
            "alt_totals":   parsed["alt_totals"],
            "commence_time": parsed["commence_time"],
        })

    return matches


def _valid_price(p) -> bool:
    return p is not None and 1.01 <= float(p) <= 30.0


def parse_match_odds(event: dict) -> dict:
    """Extract 1X2, O/U 2.5, Asian handicap, and alternate totals from Pinnacle data."""
    home_name = event.get("home_team", "")
    away_name = event.get("away_team", "")
    result = {
        "home_team": home_name,
        "away_team": away_name,
        "commence_time": event.get("commence_time"),
        "odds_home": None,
        "odds_draw": None,
        "odds_away": None,
        "odds_over25": None,
        "odds_under25": None,
        "ah_line": None,
        "odds_ah_home": None,
        "odds_ah_away": None,
        "alt_totals": {},   # {3.0: {"over": 1.56, "under": 2.47}, ...}
    }

    for book in event.get("bookmakers", []):
        for market in book.get("markets", []):
            key = market["key"]
            outcomes = market.get("outcomes", [])

            if key == "h2h":
                for o in outcomes:
                    if o["name"] == home_name and _valid_price(o.get("price")):
                        result["odds_home"] = o["price"]
                    elif o["name"] == away_name and _valid_price(o.get("price")):
                        result["odds_away"] = o["price"]
                    elif o["name"] == "Draw" and _valid_price(o.get("price")):
                        result["odds_draw"] = o["price"]

            elif key == "totals":
                for o in outcomes:
                    if o.get("point") == 2.5 and _valid_price(o.get("price")):
                        if o["name"] == "Over":
                            result["odds_over25"] = o["price"]
                        elif o["name"] == "Under":
                            result["odds_under25"] = o["price"]

            elif key == "alternate_totals":
                raw = {}
                for o in outcomes:
                    pt = o.get("point")
                    if pt is None or not _valid_price(o.get("price")):
                        continue
                    # Only accept clean .0 and .5 lines; skip .25/.75 split lines
                    if round(pt * 4) % 2 != 0:
                        continue
                    raw.setdefault(pt, {})[o["name"]] = o["price"]
                for pt, sides in raw.items():
                    if "Over" in sides and "Under" in sides:
                        result["alt_totals"][pt] = {"over": sides["Over"], "under": sides["Under"]}

            elif key == "spreads":
                # Point spread (closest available to AH on this API)
                home_spreads = {}
                away_spreads = {}
                for o in outcomes:
                    pt = o.get("point")
                    if pt is None or not _valid_price(o.get("price")):
                        continue
                    if o["name"] == home_name:
                        home_spreads[pt] = o["price"]
                    elif o["name"] == away_name:
                        away_spreads[pt] = o["price"]
                # Use as fallback if no AH data
                if home_spreads and result["ah_line"] is None:
                    best_pt = min(home_spreads.keys(), key=lambda x: abs(x))
                    result["ah_line"] = best_pt
                    result["odds_ah_home"] = home_spreads[best_pt]
                    result["odds_ah_away"] = away_spreads.get(-best_pt)

            elif key == "asian_handicaps":
                # Collect all valid home-team handicap lines (negative = home gives)
                home_lines = {}
                away_lines = {}
                for o in outcomes:
                    pt = o.get("point")
                    if pt is None or not _valid_price(o.get("price")):
                        continue
                    # Skip quarter-goal split lines (±0.25, ±0.75)
                    if round(abs(pt) * 4) % 2 != 0:
                        continue
                    if o["name"] == home_name:
                        home_lines[pt] = o["price"]
                    elif o["name"] == away_name:
                        away_lines[pt] = o["price"]
                # Pick the most balanced line (odds closest to even)
                best_pt, best_diff = None, float("inf")
                for pt, home_price in home_lines.items():
                    away_price = away_lines.get(-pt)
                    if away_price is not None:
                        diff = abs(home_price - away_price)
                        if diff < best_diff:
                            best_diff, best_pt = diff, pt
                if best_pt is not None:
                    result["ah_line"] = best_pt
                    result["odds_ah_home"] = home_lines[best_pt]
                    result["odds_ah_away"] = away_lines[-best_pt]

    return result
