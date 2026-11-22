#!/usr/bin/env python3
"""Sidequest-Radar fuer Leonida Nobody FM.

Sucht GTA-6-Shorts der letzten 24 Stunden auf YouTube, ordnet sie Themen zu
und schreibt radar.md (zum Lesen) + radar.json (fuer Winston) + verlauf.csv.

Sicherheitsregeln (fest eingebaut):
- gespeichert werden nur Zahlen, gekuerzte Titel und Video-IDs
  (keine Beschreibungen, keine Links, keine Kanalnamen, keine Downloads)
- ein Thema gilt erst ab 3 Videos von 3 verschiedenen Kanaelen als Trend
- einzelne Ausreisser erscheinen nur als "Geruecht - unbestaetigt"
"""
import json
import os
import re
import sys
import urllib.parse
import urllib.request
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from statistics import median
from zoneinfo import ZoneInfo

KEY = os.environ.get("YT_API_KEY", "").strip()
API = "https://www.googleapis.com/youtube/v3/"
QUERIES = ["GTA 6", "GTA VI", "GTA 6 shorts", "GTA 6 funny"]  # 4 x 100 Einheiten je Lauf
MAX_SEC = 180  # Shorts: hoechstens 3 Minuten
GTA = re.compile(r"\bgta\s*(6|vi)\b|\bgta6\b|grand theft auto (6|vi)\b", re.I)

THEMES = {
    "✈️ Kampfjets & Militär": ["jet", "jets", "fighter", "military", "army", "base", "tank", "kampfjet", "militär", "hydra", "lazer"],
    "🚁 Flugzeuge & Helis": ["plane", "planes", "helicopter", "heli", "pilot", "flug", "airport", "flughafen", "skydive", "skydiving", "parachute"],
    "🧍 NPC-Reaktionen": ["npc", "npcs", "reaction", "reacts", "pedestrian", "pedestrians", "passant"],
    "🚓 Polizei & Verfolgung": ["police", "cop", "cops", "wanted", "chase", "polizei", "sheriff", "arrest", "arrested"],
    "🚗 Autos & Crashs": ["car", "cars", "crash", "crashes", "drift", "race", "racing", "auto", "unfall", "supercar", "tuning", "customization"],
    "🏗️ Baumaschinen & Jobs": ["excavator", "bagger", "truck", "tow", "job", "jobs", "construction", "dump", "forklift", "crane", "side job", "side jobs"],
    "🐊 Tiere & Natur": ["gator", "gators", "alligator", "alligators", "crocodile", "animal", "animals", "fish", "fishing", "shark", "angeln", "wildlife", "swamp", "sumpf", "flamingo"],
    "🚤 Boote & Wasser": ["boat", "boats", "jetski", "jet ski", "airboat", "yacht", "beach", "ocean", "strand", "boot", "surf", "surfing"],
    "💥 Physik & Glitches": ["physics", "glitch", "glitches", "bug", "ragdoll", "fail", "fails"],
    "🗺️ Orte & Geheimnisse": ["secret", "secrets", "hidden", "easter egg", "easter eggs", "location", "where to", "map", "found", "geheim", "versteck", "mystery"],
    "🔫 Waffen & Überfälle": ["gun", "guns", "weapon", "weapons", "shootout", "robbery", "heist", "raub", "überfall"],
    "🎱 Aktivitäten & Alltag": ["pool", "gym", "billiard", "billiards", "bowling", "golf", "haircut", "barber", "tattoo", "shop", "club", "dance", "bar", "activity", "activities", "workout"],
    "📱 Handy & Social Media": ["phone", "social media", "app", "bleeter", "lifeinvader", "stream", "streamer"],
    "🧑 Jason & Lucia": ["jason", "lucia"],
    "🌴 Vice City & Leonida": ["vice city", "leonida", "miami", "leonida keys", "grassrivers", "port gellhorn", "ambrosia", "kalaga"],
}
THEME_RX = {t: re.compile(r"\b(" + "|".join(re.escape(w) for w in ws) + r")\b", re.I) for t, ws in THEMES.items()}

STOP = set("""gta 6 vi gta6 grand theft auto the a an and or of in on to is it this that you your my i me we
with for at from how what when why who can be are was so just not no yes all new best like get got
shorts short video videos game gameplay ps5 xbox 2026 vs out up its it's has have will one first
der die das und ist ein eine in im mit von zu auf du ich wir nicht so wie was wenn man es mal
funny lol omg wtf insane crazy real only now did does do don't dont never ever even more most""".split())


def get(endpoint, params):
    q = dict(params, key=KEY)
    url = API + endpoint + "?" + urllib.parse.urlencode(q)
    with urllib.request.urlopen(url, timeout=30) as r:
        return json.load(r)


def clean(text, n=70):
    text = re.sub(r"https?://\S+|www\.\S+|\S+\.(com|de|net|gg|ly|io)\S*", "", text or "")
    text = re.sub(r"[\x00-\x1f\x7f<>\[\]`*_|#\\@]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return (text[: n - 1] + "…") if len(text) > n else text


def seconds(iso):
    m = re.fullmatch(r"P(?:\d+D)?T?(?:(\d+)H)?(?:(\d+)M)?(?:(\d+)S)?", iso or "")
    if not m:
        return 99999
    h, mi, s = (int(x or 0) for x in m.groups())
    return h * 3600 + mi * 60 + s


def fmt(n):
    return f"{int(round(n)):,}".replace(",", ".")


def collect(now):
    since = (now - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M:%SZ")
    ids, errors = [], []
    for q in QUERIES:
        try:
            d = get("search", {"part": "id", "q": q, "type": "video", "videoDuration": "short",
                               "order": "viewCount", "publishedAfter": since, "maxResults": 50})
            for it in d.get("items", []):
                vid = (it.get("id") or {}).get("videoId")
                if vid and vid not in ids:
                    ids.append(vid)
        except Exception as e:  # nie den Schluessel ausgeben
            errors.append(f"Suche '{q}': {type(e).__name__} {getattr(e, 'code', '')}".strip())
    videos = []
    for i in range(0, len(ids), 50):
        try:
            d = get("videos", {"part": "snippet,statistics,contentDetails", "id": ",".join(ids[i:i + 50])})
        except Exception as e:
            errors.append(f"Details: {type(e).__name__} {getattr(e, 'code', '')}".strip())
            continue
        for v in d.get("items", []):
            sn, st = v.get("snippet", {}), v.get("statistics", {})
            raw = (sn.get("title") or "") + " " + " ".join(sn.get("tags") or [])
            if not GTA.search(raw):
                continue
            if seconds((v.get("contentDetails") or {}).get("duration")) > MAX_SEC:
                continue
            try:
                pub = datetime.fromisoformat(sn["publishedAt"].replace("Z", "+00:00"))
            except Exception:
                continue
            hours = max((now - pub).total_seconds() / 3600, 1.0)
            views = int(st.get("viewCount", 0) or 0)
            videos.append({
                "id": v.get("id"),
                "title": clean(sn.get("title")),
                "_raw": raw,  # nur intern, wird nicht gespeichert
                "_ch": sn.get("channelId", ""),  # nur intern, wird nicht gespeichert
                "views": views,
                "likes": int(st.get("likeCount", 0) or 0),
                "comments": int(st.get("commentCount", 0) or 0),
                "hours": round(hours, 1),
                "vph": round(views / hours, 1),
            })
    return videos, errors


def analyse(videos):
    themes = defaultdict(lambda: {"videos": [], "channels": set(), "vph": 0.0})
    for v in videos:
        v["themes"] = [t for t, rx in THEME_RX.items() if rx.search(v["_raw"])]
        for t in v["themes"]:
            th = themes[t]
            th["videos"].append(v)
            th["channels"].add(v["_ch"])
            th["vph"] += v["vph"]
    confirmed, weak = [], []
    for t, th in themes.items():
        top = max(th["videos"], key=lambda x: x["vph"])
        row = {"theme": t, "videos": len(th["videos"]), "channels": len(th["channels"]),
               "vph_sum": round(th["vph"]), "top": {"id": top["id"], "title": top["title"], "vph": top["vph"]}}
        (confirmed if row["videos"] >= 3 and row["channels"] >= 3 else weak).append(row)
    confirmed.sort(key=lambda r: r["vph_sum"], reverse=True)
    confirmed_names = {r["theme"] for r in confirmed}

    med = median([v["vph"] for v in videos]) if videos else 0
    rumors = [v for v in videos
              if v["vph"] >= max(3 * med, 500) and not (set(v["themes"]) & confirmed_names)]
    rumors.sort(key=lambda v: v["vph"], reverse=True)

    word_ch = defaultdict(set)
    known = {w.lower() for ws in THEMES.values() for w in ws}
    for v in videos:
        for w in set(re.findall(r"[a-zäöüß]{4,}", v["title"].lower())):
            if w not in STOP and w not in known:
                word_ch[w].add(v["_ch"])
    words = sorted(((w, len(c)) for w, c in word_ch.items() if len(c) >= 3), key=lambda x: -x[1])[:10]

    top10 = sorted(videos, key=lambda v: v["vph"], reverse=True)[:10]
    return confirmed, rumors[:5], words, top10, med


def public(v):
    return {k: v[k] for k in ("id", "title", "views", "likes", "comments", "hours", "vph", "themes") if k in v}


def write(now, videos, errors, confirmed, rumors, words, top10, med):
    berlin = now.astimezone(ZoneInfo("Europe/Berlin")).strftime("%d.%m.%Y, %H:%M")
    L = [f"# 🎮 Sidequest-Radar – Stand {berlin} Uhr", "",
         f"Gesichtet: **{len(videos)} GTA-6-Shorts** der letzten 24 Std. · Median {fmt(med)} Aufrufe/Std. · "
         "Quelle: YouTube. Gespeichert werden nur Zahlen, gekürzte Titel und Video-IDs.", ""]
    L += ["## ✅ Bestätigte Trends (mind. 3 Videos von 3 Kanälen)", ""]
    if confirmed:
        L += ["| Rang | Thema | Videos | Kanäle | Aufrufe/Std. gesamt | Spitzen-Video (Aufrufe/Std.) | Video-ID |",
              "|---|---|---|---|---|---|---|"]
        for i, r in enumerate(confirmed, 1):
            L.append(f"| {i} | {r['theme']} | {r['videos']} | {r['channels']} | {fmt(r['vph_sum'])} | "
                     f"{r['top']['title']} ({fmt(r['top']['vph'])}) | `{r['top']['id']}` |")
    else:
        L.append("_Kein Thema erfüllt gerade die 3-Kanäle-Regel._")
    L += ["", "## 🔎 Gerüchte – unbestätigt, auf eigene Gefahr", ""]
    if rumors:
        L += ["| Titel (gekürzt) | Aufrufe/Std. | Alter (Std.) | Video-ID |", "|---|---|---|---|"]
        for v in rumors:
            L.append(f"| {v['title']} | {fmt(v['vph'])} | {v['hours']} | `{v['id']}` |")
    else:
        L.append("_Keine auffälligen Einzelgänger._")
    L += ["", "## 🆕 Auffällige Wörter (mind. 3 Kanäle, noch keinem Thema zugeordnet)", ""]
    L.append(" · ".join(f"{w} ({n})" for w, n in words) if words else "_keine_")
    L += ["", "## 🏆 Top 10 nach Aufrufen pro Stunde", "",
          "| # | Titel (gekürzt) | Aufrufe | Alter (Std.) | Aufrufe/Std. | Video-ID |", "|---|---|---|---|---|---|"]
    for i, v in enumerate(top10, 1):
        L.append(f"| {i} | {v['title']} | {fmt(v['views'])} | {v['hours']} | {fmt(v['vph'])} | `{v['id']}` |")
    if errors:
        L += ["", "## ⚠️ Störungen", ""] + [f"- {e}" for e in errors]
    L.append("")
    with open("radar.md", "w", encoding="utf-8") as f:
        f.write("\n".join(L))
    with open("radar.json", "w", encoding="utf-8") as f:
        json.dump({"stand_utc": now.strftime("%Y-%m-%dT%H:%M:%SZ"), "gesichtet": len(videos),
                   "median_vph": med, "trends": confirmed, "geruechte": [public(v) for v in rumors],
                   "woerter": words, "top10": [public(v) for v in top10], "stoerungen": errors},
                  f, ensure_ascii=False, indent=1)


def log(now, n, best, status):
    path = "verlauf.csv"
    rows = []
    if os.path.exists(path):
        with open(path, encoding="utf-8") as f:
            rows = f.read().splitlines()[1:]
    rows.append(f"{now.strftime('%Y-%m-%dT%H:%MZ')};{n};{best};{status}")
    with open(path, "w", encoding="utf-8") as f:
        f.write("zeit_utc;videos;top_thema;status\n" + "\n".join(rows[-600:]) + "\n")


def main():
    now = datetime.now(timezone.utc)
    if not KEY:
        log(now, 0, "-", "FEHLER kein Schluessel")
        print("Kein YT_API_KEY gesetzt.")
        return 1
    videos, errors = collect(now)
    if not videos:
        log(now, 0, "-", "PAUSE " + ("; ".join(errors) or "keine Treffer"))
        print("Keine Videos:", errors)
        return 0  # alte radar.md bleibt stehen
    confirmed, rumors, words, top10, med = analyse(videos)
    write(now, videos, errors, confirmed, rumors, words, top10, med)
    log(now, len(videos), confirmed[0]["theme"] if confirmed else "-", "OK" if not errors else "TEILWEISE")
    print(f"OK: {len(videos)} Videos, {len(confirmed)} Trends, {len(rumors)} Gerüchte")
    return 0


if __name__ == "__main__":
    sys.exit(main())
