from __future__ import annotations

import csv
import datetime as dt
import json
import math
import re
import shutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from typing import Any

import requests
from bs4 import BeautifulSoup

INPUT_FILE = Path("Jeppe_Bolig_Masterliste_Beriget.csv")
OUTPUT_CSV = Path("Jeppe_Bolig_Masterliste_BillundPrioritet.csv")
OUTPUT_HTML = Path("Bolig_oversigt_modern.html")
OUTPUT_LIGHT_HTML = Path("Bolig_oversigt_light.html")
WEB_DIR = Path("web")
SEEN_STATE_FILE = Path(".listing_seen_state.json")
NEW_BADGE_DAYS = 3

BILLUND_LAT = 55.7308
BILLUND_LON = 9.1153

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept-Language": "da-DK,da;q=0.9,en;q=0.8",
}


def read_csv_with_fallback(path: Path) -> list[dict[str, Any]]:
    last_error: Exception | None = None
    for enc in ("utf-8-sig", "utf-8", "cp1252", "latin1"):
        try:
            with path.open("r", encoding=enc, newline="") as f:
                return list(csv.DictReader(f))
        except UnicodeDecodeError as exc:
            last_error = exc
    raise RuntimeError(f"Could not read CSV: {last_error}")


def to_float(value: Any) -> float | None:
    if value in (None, ""):
        return None
    txt = str(value).strip().replace(",", ".")
    try:
        return float(txt)
    except ValueError:
        return None


def to_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    txt = re.sub(r"[^0-9-]", "", str(value))
    if txt in ("", "-"):
        return None
    return int(txt)


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = (
        math.sin(dlat / 2) ** 2
        + math.cos(math.radians(lat1))
        * math.cos(math.radians(lat2))
        * math.sin(dlon / 2) ** 2
    )
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))
    return r * c


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def safe_text(value: Any) -> str:
    if value in (None, ""):
        return ""
    return str(value).replace("<", "&lt;").replace(">", "&gt;")


def format_currency(value: Any) -> str:
    num = to_int(value)
    if num is None:
        return "-"
    return f"{num:,}".replace(",", ".") + " kr"


def format_float(value: Any, suffix: str = "") -> str:
    if value in (None, ""):
        return "-"
    try:
        num = float(value)
        return f"{num:.1f}{suffix}"
    except (TypeError, ValueError):
        return "-"


def listing_key(row: dict[str, Any]) -> str:
    url = (row.get("canonical_url") or row.get("URL") or "").strip()
    if url:
        return f"url:{url.lower()}"
    addr = " ".join(
        str(row.get(k) or "").strip().lower()
        for k in ("address", "postal_code", "city_from_page", "By")
    )
    rent = str(to_int(row.get("Mdl. leje") or row.get("monthly_rent_from_page_dkk")) or "")
    return f"fallback:{addr}|{rent}"


def load_seen_state(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {str(k): str(v) for k, v in raw.items()}


def save_seen_state(state: dict[str, str], path: Path) -> None:
    path.write_text(json.dumps(state, ensure_ascii=True, indent=2, sort_keys=True), encoding="utf-8")


def apply_new_listing_flags(rows: list[dict[str, Any]], days_as_new: int = NEW_BADGE_DAYS) -> None:
    seen_state = load_seen_state(SEEN_STATE_FILE)
    today = dt.date.today()
    first_baseline_run = len(seen_state) == 0

    for row in rows:
        key = listing_key(row)
        first_seen_txt = seen_state.get(key)
        if first_seen_txt is None:
            seen_state[key] = today.isoformat()
            row["is_new"] = not first_baseline_run
            continue

        try:
            first_seen = dt.date.fromisoformat(first_seen_txt)
        except ValueError:
            first_seen = today
            seen_state[key] = today.isoformat()
        row["is_new"] = (today - first_seen).days < days_as_new

    save_seen_state(seen_state, SEEN_STATE_FILE)


def slippy_tile_coords(lat: float, lon: float, zoom: int) -> tuple[float, float, int]:
    lat_rad = math.radians(lat)
    tiles = 2**zoom
    x = (lon + 180.0) / 360.0 * tiles
    y = (1.0 - math.log(math.tan(lat_rad) + (1 / math.cos(lat_rad))) / math.pi) / 2.0 * tiles
    return x, y, tiles


def static_map_preview_markup(lat: float | None, lon: float | None, city_name: str) -> str:
    if lat is None or lon is None:
        return ""

    zoom = 13
    tile_size = 256
    thumb_width = 126
    thumb_height = 78
    x_float, y_float, tile_count = slippy_tile_coords(lat, lon, zoom)
    base_x = math.floor(x_float) - 1
    base_y = math.floor(y_float) - 1
    point_x = (x_float - base_x) * tile_size
    point_y = (y_float - base_y) * tile_size
    offset_x = (thumb_width / 2) - point_x
    offset_y = (thumb_height / 2) - point_y
    city_label = safe_text(city_name or "Ukendt by")[:24]

    tiles_html = []
    for row in range(3):
        for col in range(3):
            tile_x = int((base_x + col) % tile_count)
            tile_y = int(min(max(base_y + row, 0), tile_count - 1))
            tiles_html.append(
                f'<img src="https://tile.openstreetmap.org/{zoom}/{tile_x}/{tile_y}.png" '
                f'alt="" loading="lazy" referrerpolicy="no-referrer" '
                f'style="left:{col * tile_size}px;top:{row * tile_size}px;" />'
            )

    return (
        '<div class="mapThumb" aria-hidden="true">'
        f'<div class="mapThumbTiles" style="transform:translate({offset_x:.1f}px,{offset_y:.1f}px)">{"".join(tiles_html)}</div>'
        '<div class="mapThumbShade"></div>'
        '<div class="mapThumbPin"></div>'
        f'<div class="mapThumbLabel">{city_label}</div>'
        '</div>'
    )


def fetch_card_media(url: str) -> dict[str, str | None]:
    if not url:
        return {"card_title": None, "card_description": None, "image_url": None}
    try:
        resp = requests.get(url, headers=HEADERS, timeout=25)
        if resp.status_code != 200:
            return {"card_title": None, "card_description": None, "image_url": None}
        soup = BeautifulSoup(resp.text, "html.parser")

        def meta(prop: str) -> str | None:
            tag = soup.find("meta", attrs={"property": prop}) or soup.find("meta", attrs={"name": prop})
            if tag and tag.get("content"):
                return " ".join(str(tag["content"]).split())
            return None

        title = meta("og:title")
        desc = meta("og:description")
        image = meta("og:image")
        if not title and soup.title:
            title = " ".join(soup.title.get_text(" ", strip=True).split())
        return {"card_title": title, "card_description": desc, "image_url": image}
    except requests.RequestException:
        return {"card_title": None, "card_description": None, "image_url": None}


def build_scores(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    for row in rows:
        lat = to_float(row.get("lat"))
        lon = to_float(row.get("lon"))
        row["distance_to_billund_km"] = round(haversine_km(lat, lon, BILLUND_LAT, BILLUND_LON), 1) if lat is not None and lon is not None else None

        rent = to_int(row.get("Mdl. leje") or row.get("monthly_rent_from_page_dkk"))
        area = to_float(row.get("area_from_page_m2") or row.get("m²") or row.get("m2"))
        row["rent_per_m2_dkk"] = round(rent / area, 1) if (rent and area and area > 0) else None

        row["samlet_vurdering_num"] = to_float(row.get("Samlet vurdering")) or 0.0
        pets = (str(row.get("pets_policy_text") or "") + " " + str(row.get("pets_allowed") or "")).lower()
        row["dog_friendly"] = bool("ja" in pets or "true" in pets or "tilladt" in pets)

    distances = [r["distance_to_billund_km"] for r in rows if r.get("distance_to_billund_km") is not None]
    max_distance = max(distances) if distances else 1.0

    ppm2s = [r["rent_per_m2_dkk"] for r in rows if r.get("rent_per_m2_dkk") is not None]
    min_ppm2 = min(ppm2s) if ppm2s else 1.0
    max_ppm2 = max(ppm2s) if ppm2s else 1.0

    for row in rows:
        d = row.get("distance_to_billund_km")
        distance_score = 0.0 if d is None else 100.0 * (1.0 - (d / max_distance if max_distance else 0.0))
        row["distance_score_0_100"] = round(clamp(distance_score, 0.0, 100.0), 1)

        ppm2 = row.get("rent_per_m2_dkk")
        if ppm2 is None or max_ppm2 == min_ppm2:
            price_eff = 50.0
        else:
            price_eff = 100.0 * ((max_ppm2 - ppm2) / (max_ppm2 - min_ppm2))
        row["price_efficiency_score_0_100"] = round(clamp(price_eff, 0.0, 100.0), 1)

        combined = 0.65 * row["distance_score_0_100"] + 0.25 * row["samlet_vurdering_num"] + 0.10 * row["price_efficiency_score_0_100"]
        row["billund_priority_score_0_100"] = round(clamp(combined, 0.0, 100.0), 1)

    rows.sort(key=lambda r: (9999 if r.get("distance_to_billund_km") is None else r.get("distance_to_billund_km"), -(r.get("billund_priority_score_0_100") or 0.0), to_int(r.get("Mdl. leje")) or 999999))
    for i, row in enumerate(rows, start=1):
        row["billund_rank"] = i
    return rows


def enrich_media(rows: list[dict[str, Any]]) -> None:
    futures = {}
    with ThreadPoolExecutor(max_workers=6) as pool:
        for row in rows:
            url = (row.get("canonical_url") or row.get("URL") or "").strip()
            futures[pool.submit(fetch_card_media, url)] = row
        for future in as_completed(futures):
            futures[future].update(future.result())


def write_csv(rows: list[dict[str, Any]], path: Path) -> None:
    front = ["billund_rank", "distance_to_billund_km", "distance_score_0_100", "billund_priority_score_0_100", "price_efficiency_score_0_100", "dog_friendly", "rent_per_m2_dkk"]
    fields = front + [c for c in rows[0].keys() if c not in front]
    with path.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def card_markup(row: dict[str, Any]) -> str:
    image = row.get("image_url") or "https://images.unsplash.com/photo-1480074568708-e7b720bb3f09?auto=format&fit=crop&w=1200&q=80"
    lat = to_float(row.get("lat"))
    lon = to_float(row.get("lon"))
    map_thumb_html = static_map_preview_markup(lat, lon, row.get("city_from_page") or row.get("By") or "")
    new_badge = '<div class="newBadge">Ny tilfojelse!</div>' if row.get("is_new") else ""
    card_key = safe_text(row.get("listing_id") or listing_key(row))

    title = row.get("card_title") or f"{row.get('Type', 'Bolig')} i {row.get('By', '')}"
    desc = row.get("card_description") or row.get("Kommentar") or ""
    return f"""
    <article class=\"card{' dog-friendly' if row.get('dog_friendly') else ''}\" data-key=\"{card_key}\" data-rent=\"{to_int(row.get('Mdl. leje')) or 0}\" data-rooms=\"{to_int(row.get('rooms_from_page')) or to_int(row.get('Vær.')) or 0}\" data-distance=\"{to_float(row.get('distance_to_billund_km')) or 9999}\" data-score=\"{to_float(row.get('billund_priority_score_0_100')) or 0}\" data-dog=\"{'yes' if row.get('dog_friendly') else 'no'}\">
        <div class=\"hero\" style=\"background-image:url('{safe_text(image)}')\"><div class=\"overlay\"></div>{new_badge}<div class=\"rank\">#{row.get('billund_rank')}</div><div class=\"distance\">{format_float(row.get('distance_to_billund_km'), ' km')} til Billund</div>{map_thumb_html}</div>
        <div class=\"content\">
            <h3>{safe_text(title)}</h3>
            <p class=\"meta\">{safe_text(row.get('address'))}, {safe_text(row.get('postal_code'))} {safe_text(row.get('city_from_page') or row.get('By'))}</p>
            <p class=\"desc\">{safe_text(desc)}</p>
            <div class=\"chips\"><span>{safe_text(row.get('Type'))}</span><span>{safe_text(row.get('area_from_page_m2') or row.get('m²'))} m²</span><span>{safe_text(row.get('rooms_from_page') or row.get('Vær.'))} vær.</span><span>{'Hund OK' if row.get('dog_friendly') else 'Ingen hund'}</span></div>
            <div class=\"stats\"><div><small>Mdl. leje</small><strong>{format_currency(row.get('Mdl. leje'))}</strong></div><div><small>Indflytning</small><strong>{format_currency(row.get('move_in_price_dkk'))}</strong></div><div><small>Kr/m²</small><strong>{format_float(row.get('rent_per_m2_dkk'))}</strong></div></div>
            <div class=\"score\"><div><small>Distance-score</small><strong>{format_float(row.get('distance_score_0_100'))}</strong></div><div><small>Billund-prio</small><strong>{format_float(row.get('billund_priority_score_0_100'))}</strong></div><div><small>Samlet</small><strong>{safe_text(row.get('Samlet vurdering'))}</strong></div></div>
            <div class=\"actions\"><a href=\"{safe_text(row.get('canonical_url') or row.get('URL') or '#')}\" target=\"_blank\" rel=\"noopener noreferrer\">Se annonce</a><button class=\"favBtn\" type=\"button\" data-key=\"{card_key}\">Gem</button></div>
        </div>
    </article>
    """


def create_modern_html(rows: list[dict[str, Any]], path: Path) -> None:
    cards = "\n".join(card_markup(r) for r in rows)
    now = dt.datetime.now().strftime("%d-%m-%Y %H:%M")

    html = f"""<!doctype html>
<html lang=\"da\">
<head>
  <meta charset=\"utf-8\" />
  <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
  <title>Boligoversigt</title>
  <link rel=\"preconnect\" href=\"https://fonts.googleapis.com\">
  <link rel=\"preconnect\" href=\"https://fonts.gstatic.com\" crossorigin>
  <link href=\"https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,600;9..144,800&family=Space+Grotesk:wght@400;500;700&display=swap\" rel=\"stylesheet\">
  <style>
    :root {{ --bg:#f7f4ef; --ink:#15211f; --accent:#0d7a5f; --soft:#d7efe8; --card:#fffdf8; --muted:#5d6b69; --line:#deebe5; --radius:18px; }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; font-family:'Space Grotesk',sans-serif; color:var(--ink); background:radial-gradient(circle at 8% 8%,#ffe5c9 0,transparent 35%), radial-gradient(circle at 92% 15%,#d5f3e5 0,transparent 32%), linear-gradient(160deg,#f8f5ef 0%,#eef7f3 100%); }}
    .wrap {{ max-width:1220px; margin:0 auto; padding:20px; }}
    h1 {{ margin:0 0 6px; font-family:'Fraunces',serif; font-size:clamp(28px,4vw,44px); }}
    .sub {{ margin:0 0 14px; color:var(--muted); }}
    .tabs {{ display:flex; gap:8px; flex-wrap:wrap; margin-bottom:10px; }}
    .tabs button {{ border:1px solid #cfd9d2; background:#fff; border-radius:999px; padding:8px 12px; font:inherit; cursor:pointer; }}
    .tabs button.active {{ background:#1f5e50; color:#fff; border-color:#1f5e50; }}
    .shortlistMeta {{ display:inline-flex; align-items:center; font-size:12px; color:#36564d; padding:0 2px; }}
    .filterToggle {{ border:1px solid #b9cbc3; background:#f9fffc; border-radius:999px; padding:8px 12px; font:inherit; cursor:pointer; color:#1e4c42; }}
    .toolbar {{ display:flex; gap:10px; flex-wrap:wrap; align-items:center; background:rgba(255,255,255,.8); border:1px solid var(--line); border-radius:12px; padding:10px; margin-bottom:12px; }}
    .toolbar.collapsed {{ display:none; }}
    .toolbar label {{ font-size:13px; color:#4f5f56; }}
    .toolbar input,.toolbar select {{ border:1px solid #c8d3cb; border-radius:8px; padding:7px; font:inherit; }}
    .panel {{ display:none; }}
    .panel.active {{ display:block; }}
    .grid {{ display:grid; grid-template-columns:repeat(auto-fill,minmax(290px,1fr)); gap:14px; }}
    .card {{ background:var(--card); border:1px solid var(--line); border-radius:var(--radius); overflow:hidden; box-shadow:0 10px 26px rgba(15,47,41,.12); }}
    .card.dog-friendly {{ border-color:#8fcea8; }}
    .hero {{ height:170px; background-size:cover; background-position:center; position:relative; display:flex; justify-content:space-between; align-items:flex-start; padding:10px; }}
    .overlay {{ position:absolute; inset:0; background:linear-gradient(180deg,rgba(0,0,0,.05),rgba(0,0,0,.46)); }}
    .rank,.distance {{ position:relative; z-index:1; align-self:flex-start; display:inline-flex; align-items:center; background:rgba(21,33,31,.55); color:#fff; border-radius:999px; padding:5px 9px; font-size:12px; font-weight:700; line-height:1.15; white-space:nowrap; }}
    .newBadge {{ position:absolute; left:10px; bottom:10px; z-index:2; background:#ffcc34; color:#352700; font-weight:800; font-size:11px; letter-spacing:.02em; border-radius:999px; padding:4px 9px; box-shadow:0 3px 10px rgba(0,0,0,.22); }}
    .mapThumb {{ position:absolute; right:10px; bottom:10px; z-index:1; width:126px; height:78px; overflow:hidden; border-radius:16px; border:2px solid rgba(255,255,255,.92); box-shadow:0 6px 14px rgba(0,0,0,.24); background:#e7ece8; }}
    .mapThumbTiles {{ position:absolute; inset:0 auto auto 0; width:768px; height:768px; }}
    .mapThumbTiles img {{ position:absolute; width:256px; height:256px; display:block; image-rendering:auto; }}
    .mapThumbShade {{ position:absolute; inset:0; background:linear-gradient(180deg,rgba(7,16,14,.06),rgba(7,16,14,.20)); pointer-events:none; }}
    .mapThumbPin {{ position:absolute; left:50%; top:50%; width:18px; height:18px; transform:translate(-50%,-82%) rotate(-45deg); background:#d64545; border:2px solid #fff; border-radius:50% 50% 50% 0; box-shadow:0 3px 8px rgba(0,0,0,.26); }}
    .mapThumbPin::after {{ content:''; position:absolute; width:6px; height:6px; left:4px; top:4px; border-radius:50%; background:#fff; }}
    .mapThumbLabel {{ position:absolute; left:8px; bottom:7px; max-width:calc(100% - 16px); padding:3px 8px; border-radius:999px; background:rgba(21,33,31,.74); color:#fff; font-size:11px; font-weight:700; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; }}
    .content {{ padding:12px; }}
    .content h3 {{ margin:0 0 6px; font-size:18px; }}
    .meta,.desc {{ margin:0 0 8px; color:#4f5f56; font-size:13px; }}
    .chips {{ display:flex; flex-wrap:wrap; gap:6px; margin-bottom:8px; }}
    .chips span {{ background:var(--soft); border-radius:999px; padding:3px 8px; font-size:11px; }}
    .stats,.score {{ display:grid; grid-template-columns:repeat(3,1fr); gap:6px; margin-bottom:8px; }}
    .stats div,.score div {{ border:1px solid var(--line); border-radius:10px; padding:7px 8px; background:#fbfefb; }}
    .stats small,.score small {{ display:block; margin:0 0 3px; color:#5c6b63; line-height:1.25; }}
    .stats strong,.score strong {{ display:block; line-height:1.3; letter-spacing:.01em; }}
    .card.shortlisted {{ border-color:#e3be46; box-shadow:0 10px 26px rgba(123,91,22,.24); }}
    .actions {{ display:flex; gap:8px; align-items:center; flex-wrap:wrap; }}
    .actions a {{ display:inline-block; text-decoration:none; background:linear-gradient(135deg,var(--accent),#0f9271); color:#fff; border-radius:10px; padding:7px 10px; font-weight:700; font-size:13px; }}
    .favBtn {{ border:1px solid #cfb262; background:#fff7dc; color:#5b4610; border-radius:10px; padding:7px 10px; font:inherit; font-size:12px; font-weight:700; cursor:pointer; }}
    .favBtn.active {{ background:#f3c94a; border-color:#d3a92f; color:#2b2207; }}
    body.light-mode .desc, body.light-mode .stats, body.light-mode .score {{ display:none; }}
    body.light-mode .hero {{ height:190px; }}
    body.light-mode .content h3 {{ font-size:17px; }}
    #mapFrame {{ width:100%; height:74vh; border:1px solid var(--line); border-radius:14px; background:#fff; }}
    .mapHelp {{ font-size:13px; color:#4f5f56; margin:0 0 8px; }}
    @media (max-width:760px) {{ .stats,.score {{ grid-template-columns:1fr; }} .hero {{ height:160px; }} }}
  </style>
</head>
<body>
  <main class=\"wrap\">
    <h1>Boligoversigt med Billund i fokus</h1>
    <p class=\"sub\">Samlet visning med modern + light mode og indbygget kort. Opdateret: {now}</p>

    <section class=\"tabs\">
      <button class=\"tabBtn active\" data-view=\"cards\">Kortliste</button>
      <button class=\"tabBtn\" data-view=\"map\">Interaktivt Kort</button>
      <button id=\"modeToggle\">Skift til Light</button>
            <button id="filterToggle" class="filterToggle">Vis filtre og sortering</button>
            <button id="shortlistToggle">Vis kun valgte</button>
            <button id="shortlistShare">Del valgte</button>
            <span id="shortlistCount" class="shortlistMeta">0 valgte</span>
    </section>

        <section class=\"toolbar collapsed\" id=\"filtersBar\">
      <label>Maks husleje <input id=\"rentMax\" type=\"number\" value=\"12000\" /></label>
      <label>Min værelser <select id=\"roomsMin\"><option value=\"0\">Alle</option><option value=\"2\">2+</option><option value=\"3\" selected>3+</option><option value=\"4\">4+</option></select></label>
      <label>Husdyr <select id=\"dogFilter\"><option value=\"all\">Alle</option><option value=\"dog\">Kun hund tilladt</option></select></label>
      <label>Sortering <select id=\"sortBy\"><option value=\"billund\">Billund-rang</option><option value=\"distance\">Kortest afstand</option><option value=\"rentAsc\">Laveste pris</option><option value=\"rentDesc\">Højeste pris</option><option value=\"scoreDesc\">Højeste score</option></select></label>
      <label><input id=\"highlightDog\" type=\"checkbox\" checked /> Fremhæv hund-venlige</label>
    </section>

    <section id=\"cardsPanel\" class=\"panel active\"><div class=\"grid\" id=\"grid\">{cards}</div></section>
    <section id=\"mapPanel\" class=\"panel\">
    <p class=\"mapHelp\">Kortet hentes automatisk fra pendlingssiden (lokalt eller hostet). Hvis det er tomt, kør appen igen for at opdatere pendlingskortet.</p>
      <iframe id=\"mapFrame\" src=\"Bolig_kort_pendling.html\" title=\"Pendling map\"></iframe>
    </section>
  </main>

<script>
const grid = document.getElementById('grid');
const rentMax = document.getElementById('rentMax');
const roomsMin = document.getElementById('roomsMin');
const dogFilter = document.getElementById('dogFilter');
const sortBy = document.getElementById('sortBy');
const highlightDog = document.getElementById('highlightDog');
const modeToggle = document.getElementById('modeToggle');
const filterToggle = document.getElementById('filterToggle');
const tabButtons = Array.from(document.querySelectorAll('.tabBtn'));
const cardsPanel = document.getElementById('cardsPanel');
const mapPanel = document.getElementById('mapPanel');
const filtersBar = document.getElementById('filtersBar');
const mapFrame = document.getElementById('mapFrame');
const shortlistToggle = document.getElementById('shortlistToggle');
const shortlistShare = document.getElementById('shortlistShare');
const shortlistCount = document.getElementById('shortlistCount');

const shortlistStorageKey = 'bolig-shortlist-v1';
let showOnlyShortlist = false;
let shortlist = new Set();

try {{
    const raw = localStorage.getItem(shortlistStorageKey);
    if (raw) shortlist = new Set(JSON.parse(raw));
}} catch (_) {{
    shortlist = new Set();
}}

const sharedShortlist = new URLSearchParams(window.location.search).get('shortlist');
if (sharedShortlist) {{
    sharedShortlist.split(',').forEach((id) => {{
        const clean = decodeURIComponent(id || '').trim();
        if (clean) shortlist.add(clean);
    }});
}}

function persistShortlist() {{
    localStorage.setItem(shortlistStorageKey, JSON.stringify(Array.from(shortlist)));
}}

function refreshShortlistUI() {{
    Array.from(grid.querySelectorAll('.card')).forEach((card) => {{
        const key = card.dataset.key;
        const isOn = shortlist.has(key);
        card.classList.toggle('shortlisted', isOn);
        const btn = card.querySelector('.favBtn');
        if (btn) {{
            btn.classList.toggle('active', isOn);
            btn.textContent = isOn ? 'Valgt' : 'Gem';
        }}
    }});
    shortlistCount.textContent = `${{shortlist.size}} valgte`;
    shortlistToggle.textContent = showOnlyShortlist ? 'Vis alle' : 'Vis kun valgte';
}}

const hosted = window.location.protocol === 'http:' || window.location.protocol === 'https:';
mapFrame.src = hosted ? 'pendling.html' : 'Bolig_kort_pendling.html';

function applySort() {{
  const mode = sortBy.value;
  const cards = Array.from(grid.children);
  cards.sort((a,b) => {{
    if (mode === 'distance') return Number(a.dataset.distance) - Number(b.dataset.distance);
    if (mode === 'rentAsc') return Number(a.dataset.rent) - Number(b.dataset.rent);
    if (mode === 'rentDesc') return Number(b.dataset.rent) - Number(a.dataset.rent);
    if (mode === 'scoreDesc') return Number(b.dataset.score) - Number(a.dataset.score);
    return Number(a.querySelector('.rank').textContent.replace('#','')) - Number(b.querySelector('.rank').textContent.replace('#',''));
  }});
  cards.forEach((c) => grid.appendChild(c));
}}

function applyFilters() {{
  const max = Number(rentMax.value || 999999);
  const minRooms = Number(roomsMin.value || 0);
  const onlyDog = dogFilter.value === 'dog';
  Array.from(grid.children).forEach((card) => {{
    const rent = Number(card.dataset.rent || 0);
    const rooms = Number(card.dataset.rooms || 0);
    const hasDog = card.dataset.dog === 'yes';
        const isShortlisted = shortlist.has(card.dataset.key);
        const visible = rent <= max && rooms >= minRooms && (!onlyDog || hasDog) && (!showOnlyShortlist || isShortlisted);
    card.style.display = visible ? 'block' : 'none';
    card.classList.toggle('dog-friendly', hasDog && highlightDog.checked);
  }});
  applySort();
}}

function activateView(view) {{
  const isCards = view === 'cards';
  cardsPanel.classList.toggle('active', isCards);
  mapPanel.classList.toggle('active', !isCards);
    if (!isCards) {{
        filtersBar.style.display = 'none';
        filterToggle.style.display = 'none';
        modeToggle.style.display = 'none';
    }} else {{
        modeToggle.style.display = 'inline-block';
        filterToggle.style.display = 'inline-block';
        filtersBar.style.display = filtersBar.classList.contains('collapsed') ? 'none' : 'flex';
    }}
  tabButtons.forEach((b) => b.classList.toggle('active', b.dataset.view === view));
}}

tabButtons.forEach((btn) => btn.addEventListener('click', () => activateView(btn.dataset.view)));
[rentMax, roomsMin, dogFilter, highlightDog].forEach((el) => el.addEventListener('input', applyFilters));
sortBy.addEventListener('change', applySort);
grid.addEventListener('click', (evt) => {{
    const btn = evt.target.closest('.favBtn');
    if (!btn) return;
    const key = btn.dataset.key;
    if (!key) return;
    if (shortlist.has(key)) shortlist.delete(key); else shortlist.add(key);
    persistShortlist();
    refreshShortlistUI();
    applyFilters();
}});
shortlistToggle.addEventListener('click', () => {{
    showOnlyShortlist = !showOnlyShortlist;
    refreshShortlistUI();
    applyFilters();
}});
shortlistShare.addEventListener('click', async () => {{
    const selected = encodeURIComponent(Array.from(shortlist).join(','));
    const url = `${{window.location.origin}}${{window.location.pathname}}?shortlist=${{selected}}`;
    try {{
        await navigator.clipboard.writeText(url);
        shortlistShare.textContent = 'Link kopieret';
        setTimeout(() => shortlistShare.textContent = 'Del valgte', 1400);
    }} catch (_) {{
        window.prompt('Kopiér linket:', url);
    }}
}});
filterToggle.addEventListener('click', () => {{
    filtersBar.classList.toggle('collapsed');
    const collapsed = filtersBar.classList.contains('collapsed');
    filtersBar.style.display = collapsed ? 'none' : 'flex';
    filterToggle.textContent = collapsed ? 'Vis filtre og sortering' : 'Skjul filtre og sortering';
}});
modeToggle.addEventListener('click', () => {{
  document.body.classList.toggle('light-mode');
  modeToggle.textContent = document.body.classList.contains('light-mode') ? 'Skift til Modern' : 'Skift til Light';
}});
refreshShortlistUI();
applyFilters();
</script>
</body>
</html>
"""
    path.write_text(html, encoding="utf-8")


def create_light_html(rows: list[dict[str, Any]], path: Path) -> None:
    cards = []
    for row in rows:
        image = row.get("image_url") or "https://images.unsplash.com/photo-1494526585095-c41746248156?auto=format&fit=crop&w=1200&q=80"
        cards.append(
            f"""
            <a class=\"tile{' dog' if row.get('dog_friendly') else ''}\" data-rent=\"{to_int(row.get('Mdl. leje')) or 0}\" data-dog=\"{'yes' if row.get('dog_friendly') else 'no'}\" data-distance=\"{to_float(row.get('distance_to_billund_km')) or 9999}\" href=\"{safe_text(row.get('canonical_url') or row.get('URL') or '#')}\" target=\"_blank\" rel=\"noopener noreferrer\">
              <img src=\"{safe_text(image)}\" alt=\"\" loading=\"lazy\" /><div class=\"veil\"></div>
                            {'<div class="new">Ny tilfojelse!</div>' if row.get('is_new') else ''}
              <div class=\"txt\"><small>#{row.get('billund_rank')} · {format_float(row.get('distance_to_billund_km'), ' km')}</small><h3>{safe_text(row.get('By'))} · {format_currency(row.get('Mdl. leje'))}</h3><p>{'Hund OK' if row.get('dog_friendly') else 'Ingen hund'}</p></div>
            </a>
            """
        )

    html = f"""<!doctype html><html lang=\"da\"><head><meta charset=\"utf-8\"/><meta name=\"viewport\" content=\"width=device-width, initial-scale=1\"/><title>Light</title>
    <style>body{{font-family:Arial,sans-serif;margin:0;padding:14px;background:#f3f7f8}}.grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:10px}}.tile{{display:block;position:relative;min-height:220px;color:#fff;text-decoration:none;border-radius:12px;overflow:hidden}}img{{position:absolute;inset:0;width:100%;height:100%;object-fit:cover}}.veil{{position:absolute;inset:0;background:linear-gradient(180deg,rgba(0,0,0,.05),rgba(0,0,0,.75))}}.txt{{position:absolute;left:10px;right:10px;bottom:10px;z-index:1}}.new{{position:absolute;left:10px;top:10px;z-index:1;background:#ffcc34;color:#352700;border-radius:999px;padding:4px 8px;font-weight:800;font-size:11px}}.dog{{outline:3px solid #78c998}}</style></head><body><div class=\"grid\">{''.join(cards)}</div></body></html>"""
    path.write_text(html, encoding="utf-8")


def create_web_bundle() -> None:
    WEB_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copy2(OUTPUT_HTML, WEB_DIR / "index.html")
    shutil.copy2(OUTPUT_LIGHT_HTML, WEB_DIR / "family.html")
    shutil.copy2(OUTPUT_CSV, WEB_DIR / OUTPUT_CSV.name)


def main() -> None:
    rows = read_csv_with_fallback(INPUT_FILE)
    rows = build_scores(rows)
    apply_new_listing_flags(rows)
    enrich_media(rows)
    write_csv(rows, OUTPUT_CSV)
    create_modern_html(rows, OUTPUT_HTML)
    create_light_html(rows, OUTPUT_LIGHT_HTML)
    create_web_bundle()
    print("Created:")
    print(f"- {OUTPUT_CSV}")
    print(f"- {OUTPUT_HTML}")
    print(f"- {OUTPUT_LIGHT_HTML}")


if __name__ == "__main__":
    main()
