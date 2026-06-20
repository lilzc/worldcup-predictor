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
    with open(path) as f:
        return json.load(f)


def _save_cache(key: str, data: dict):
    os.makedirs(CACHE_DIR, exist_ok=True)
    with open(_cache_path(key), "w") as f:
        json.dump(data, f)


def fetch_live_odds(sport: str = "soccer_fifa_world_cup") -> list[dict]:
    if not ODDS_API_KEY:
        print("警告: ODDS_API_KEY 未设置，无法获取实时赔率")
        return []

    cache_key = f"odds_{sport}"
    cached = _load_cache(cache_key, ttl_seconds=900)  # 15min cache
    if cached:
        return cached

    url = f"{BASE_URL}/sports/{sport}/odds"
    params = {
        "apiKey": ODDS_API_KEY,
        "regions": "eu,uk",
        "markets": "h2h,totals",
        "oddsFormat": "decimal",
        "bookmakers": "pinnacle",
    }
    resp = requests.get(url, params=params, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    _save_cache(cache_key, data)
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
        # Only include games starting within next 48h
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
            "odds_home":   parsed["odds_home"],
            "odds_draw":   parsed["odds_draw"],
            "odds_away":   parsed["odds_away"],
            "odds_over25": parsed["odds_over25"],
            "odds_under25":parsed["odds_under25"],
            "commence_time": parsed["commence_time"],
        })

    return matches


def parse_match_odds(event: dict) -> dict:
    """Extract home/draw/away + over2.5 from Pinnacle data."""
    result = {
        "home_team": event.get("home_team"),
        "away_team": event.get("away_team"),
        "commence_time": event.get("commence_time"),
        "odds_home": None,
        "odds_draw": None,
        "odds_away": None,
        "odds_over25": None,
        "odds_under25": None,
    }

    for book in event.get("bookmakers", []):
        for market in book.get("markets", []):
            if market["key"] == "h2h":
                for outcome in market["outcomes"]:
                    if outcome["name"] == event["home_team"]:
                        result["odds_home"] = outcome["price"]
                    elif outcome["name"] == event["away_team"]:
                        result["odds_away"] = outcome["price"]
                    elif outcome["name"] == "Draw":
                        result["odds_draw"] = outcome["price"]
            elif market["key"] == "totals":
                for outcome in market["outcomes"]:
                    if outcome.get("point") == 2.5:
                        if outcome["name"] == "Over":
                            result["odds_over25"] = outcome["price"]
                        elif outcome["name"] == "Under":
                            result["odds_under25"] = outcome["price"]
    return result
