ODDS_API_KEY = ""  # https://the-odds-api.com 免费注册获取
KELLY_FRACTION = 0.25
MIN_EDGE = 0.05       # 最低5%边际才标记为value bet
PROB_CAP = 0.85       # 单队最高胜率上限
WC_GOAL_DISCOUNT = 0.78   # 世界杯进球数比联赛低22%（回测实测）
BANKROLL = 2000

DEFENDING_CHAMPION = "Argentina"

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

# UCL决赛心态信号（Repo2的因子，手动维护）
UCL_MENTALITY = {
    "France":    -0.16,   # 姆巴佩连续两届QF出局
    "England":   +0.05,   # Palmer/Saka联赛表现稳定
    "Argentina": +0.10,   # 梅西2022冠军加成，但年龄衰减
    "Germany":   +0.05,
    "Portugal":  -0.05,
}
