from __future__ import annotations

import csv
import json
import math
import re
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import requests

INPUT_FILE = Path("Jeppe_Bolig_Masterliste_BillundPrioritet.csv")
OUTPUT_CSV = Path("Jeppe_Bolig_Masterliste_Pendling.csv")
OUTPUT_HTML = Path("Bolig_kort_pendling.html")
WEB_DIR = Path("web")

BILLUND_LAT = 55.7308
BILLUND_LON = 9.1153
OSRM_BASE = "https://router.project-osrm.org/route/v1/driving"


def read_csv_with_fallback(path: Path) -> list[dict[str, Any]]:
    last_error: Exception | None = None
    for enc in ("utf-8-sig", "utf-8", "cp1252", "latin1"):
        try:
            with path.open("r", encoding=enc, newline="") as f:
                return list(csv.DictReader(f))
        except UnicodeDecodeError as exc:
            last_error = exc
    raise RuntimeError(f"Could not decode CSV: {last_error}")


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


def normalize_location(city: str, by: str) -> str:
    source = (city or by or "").strip()
    if not source:
        return "Ukendt"
    source = source.split("/")[0].split("(")[0].strip()
    source_low = source.lower()
    if "billund" in source_low:
        return "Billund"
    if "jelling" in source_low:
        return "Jelling"
    if "give" in source_low:
        return "Give"
    if "kolding" in source_low:
        return "Kolding"
    if "vejle" in source_low or "grejs" in source_low or "oedsted" in source_low:
        return "Vejle/Grejs/Oedsted"
    if "egtved" in source_low or "vester nebel" in source_low:
        return "Egtved/Vester Nebel"
    if "vejen" in source_low or "askov" in source_low:
        return "Vejen/Askov"
    if "lunderskov" in source_low:
        return "Lunderskov"
    if "vamdrup" in source_low or "oedis" in source_low:
        return "Vamdrup/Oedis"
    return source.title()


def fetch_commute(lat: float, lon: float) -> tuple[float | None, float | None, str]:
    route_url = f"{OSRM_BASE}/{lon},{lat};{BILLUND_LON},{BILLUND_LAT}?overview=false"
    try:
        resp = requests.get(route_url, timeout=20)
        if resp.status_code == 200:
            data = resp.json()
            routes = data.get("routes") or []
            if routes:
                distance_km = (routes[0].get("distance") or 0.0) / 1000.0
                duration_min = (routes[0].get("duration") or 0.0) / 60.0
                return round(duration_min, 1), round(distance_km, 1), "osrm"
    except requests.RequestException:
        pass

    air_km = haversine_km(lat, lon, BILLUND_LAT, BILLUND_LON)
    fallback_min = (air_km * 1.28 / 70.0) * 60.0
    return round(fallback_min, 1), round(air_km * 1.28, 1), "fallback"


def build(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for i, row in enumerate(rows, start=1):
        lat = to_float(row.get("lat"))
        lon = to_float(row.get("lon"))

        commute_min = None
        commute_km = None
        commute_source = "missing_coords"
        if lat is not None and lon is not None:
            commute_min, commute_km, commute_source = fetch_commute(lat, lon)

        location_group = normalize_location(str(row.get("city_from_page") or ""), str(row.get("By") or ""))
        row["location_group"] = location_group
        row["commute_to_billund_min"] = commute_min
        row["commute_distance_km"] = commute_km
        row["commute_source"] = commute_source

        out.append(row)
        print(f"[{i}/{len(rows)}] {row.get('By')} -> {commute_min} min ({commute_source})")

    out.sort(
        key=lambda r: (
            9999 if to_float(r.get("commute_to_billund_min")) is None else to_float(r.get("commute_to_billund_min")),
            9999 if to_int(r.get("Mdl. leje")) is None else to_int(r.get("Mdl. leje")),
        )
    )
    for idx, row in enumerate(out, start=1):
        row["pendling_rank"] = idx

    return out


def write_csv(rows: list[dict[str, Any]]) -> None:
    front = [
        "pendling_rank",
        "location_group",
        "commute_to_billund_min",
        "commute_distance_km",
        "commute_source",
    ]
    fields = front + [c for c in rows[0].keys() if c not in front]
    with OUTPUT_CSV.open("w", encoding="utf-8-sig", newline="") as f:
        w = csv.DictWriter(f, fieldnames=fields)
        w.writeheader()
        w.writerows(rows)


def fmt_money(value: Any) -> str:
    i = to_int(value)
    if i is None:
        return "-"
    return f"{i:,}".replace(",", ".") + " kr"


def create_map(rows: list[dict[str, Any]]) -> None:
    groups = Counter((r.get("location_group") or "Ukendt") for r in rows)
    ordered_groups = sorted(groups.keys())

    serializable = []
    for r in rows:
        serializable.append(
            {
                "pendling_rank": r.get("pendling_rank"),
                "billund_rank": r.get("billund_rank"),
                "city": r.get("By"),
                "group": r.get("location_group"),
                "address": r.get("address"),
                "postal_code": r.get("postal_code"),
                "city_from_page": r.get("city_from_page"),
                "lat": to_float(r.get("lat")),
                "lon": to_float(r.get("lon")),
                "rent": to_int(r.get("Mdl. leje")),
                "score": to_float(r.get("billund_priority_score_0_100")),
                "commute_min": to_float(r.get("commute_to_billund_min")),
                "commute_km": to_float(r.get("commute_distance_km")),
                "dog": str(r.get("dog_friendly") or "").lower() in ("true", "1", "yes"),
                "url": r.get("canonical_url") or r.get("URL") or "",
                "image": r.get("image_url") or "",
                "title": r.get("card_title") or f"{r.get('Type', 'Bolig')} i {r.get('By', '')}",
                "type": r.get("Type") or "",
                "area": to_float(r.get("area_from_page_m2") or r.get("m²") or r.get("m2")),
                "rooms": to_float(r.get("rooms_from_page") or r.get("Vær.")),
            }
        )

    groups_html = "".join(
        f"<label><input type='checkbox' class='groupCheck' value='{g}' checked> {g} ({groups[g]})</label>" for g in ordered_groups
    )

    html = f"""<!doctype html>
<html lang="da">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Pendling og kortoversigt</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
  <link href="https://fonts.googleapis.com/css2?family=Outfit:wght@300;500;700&family=Newsreader:opsz,wght@6..72,600&display=swap" rel="stylesheet">
  <link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css" crossorigin=""/>
  <style>
    :root {{ --bg:#f2f4ef; --ink:#1f251d; --soft:#e8ece3; --line:#cfd7c8; --accent:#0f766e; --accent2:#dc6b2f; }}
    * {{ box-sizing:border-box; }}
    body {{ margin:0; font-family:'Outfit',sans-serif; color:var(--ink); background:radial-gradient(circle at 10% 10%, #fff6dd 0, transparent 35%), radial-gradient(circle at 95% 5%, #def7ee 0, transparent 35%), var(--bg); }}
    .wrap {{ max-width:1360px; margin:0 auto; padding:16px; }}
    h1 {{ margin:0 0 6px; font-family:'Newsreader',serif; font-size:clamp(28px,4vw,44px); }}
    .lead {{ margin:0 0 12px; color:#4f5a4b; }}
    .controls {{ display:grid; grid-template-columns: repeat(4, minmax(150px, 180px)) minmax(300px, 1fr); gap:10px; margin-bottom:12px; align-items:start; }}
    .controls > div {{ background:#fff; border:1px solid var(--line); border-radius:12px; padding:8px; }}
    .control-location {{ grid-column: 5; }}
    label {{ font-size:12px; color:#5d6658; display:block; margin-bottom:6px; }}
    input, select {{ width:100%; border:1px solid #c5cfbc; border-radius:8px; padding:7px; font:inherit; }}
    .groups {{ display:flex; flex-wrap:wrap; gap:8px; max-height:98px; overflow:auto; padding-right:2px; }}
    .groups label {{ display:flex; align-items:center; gap:4px; background:#fff; border:1px solid var(--line); border-radius:999px; padding:5px 10px; margin:0; }}
    .layout {{ display:grid; grid-template-columns: 1.15fr .85fr; gap:12px; }}
    #map {{ height:70vh; border-radius:14px; border:1px solid var(--line); }}
    .panel {{ background:#fff; border:1px solid var(--line); border-radius:14px; padding:10px; overflow:auto; max-height:70vh; }}
    .group-block {{ margin-bottom:10px; }}
    .group-block h3 {{ margin:0 0 6px; font-size:15px; color:#2c3328; }}
    .item {{ display:grid; grid-template-columns:68px 1fr auto; gap:8px; align-items:center; border:1px solid #e1e7dc; border-radius:10px; padding:6px; margin-bottom:6px; background:#fcfdfa; }}
    .item img {{ width:68px; height:56px; object-fit:cover; border-radius:8px; background:#edf1e9; }}
    .meta b {{ font-size:13px; display:block; line-height:1.2; }}
    .meta small {{ color:#596255; font-size:12px; }}
    .tag {{ font-size:11px; border:1px solid #c6d3c4; border-radius:999px; padding:3px 8px; background:#f2f8f2; }}
    .dog {{ border-color:#85c29f; background:#eaf7ef; }}
    .item a {{ text-decoration:none; color:#0a5f59; font-weight:600; }}
    @media (max-width: 980px) {{ .layout {{ grid-template-columns:1fr; }} #map,.panel {{ max-height:none; height:50vh; }} .controls {{ grid-template-columns:1fr 1fr; }} .control-location {{ grid-column: 1 / -1; }} }}
  </style>
</head>
<body>
  <main class="wrap">
    <h1>Pendletid og lokationsoverblik</h1>
    <p class="lead">Interaktivt kort med billund-pendling, prisfilter, hund-filter og tydelig inddeling efter lokation (Billund/Jelling/Give/Kolding osv.).</p>

    <section class="controls">
      <div>
        <label>Maks pendletid (min)</label>
        <input id="maxCommute" type="number" value="45" min="0" />
      </div>
      <div>
        <label>Maks husleje</label>
        <input id="maxRent" type="number" value="12000" min="0" />
      </div>
      <div>
        <label>Sortering</label>
        <select id="sortBy">
          <option value="commute">Kortest pendletid</option>
          <option value="rent">Laveste pris</option>
          <option value="score">Hojeste score</option>
        </select>
      </div>
      <div>
        <label>Husdyr</label>
        <select id="dogOnly">
          <option value="all">Alle</option>
          <option value="dog">Kun hund tilladt</option>
        </select>
      </div>
      <div class="control-location">
        <label>Lokation</label>
        <div class="groups" id="groupChecks">{groups_html}</div>
      </div>
    </section>

    <section class="layout">
      <div id="map"></div>
      <aside class="panel" id="listPanel"></aside>
    </section>
  </main>

  <script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js" crossorigin=""></script>
  <script>
    const billund = {{ lat: {BILLUND_LAT}, lon: {BILLUND_LON} }};
    const listings = {json.dumps(serializable, ensure_ascii=False)};

    const map = L.map('map').setView([billund.lat, billund.lon], 10);
    L.tileLayer('https://{{s}}.tile.openstreetmap.org/{{z}}/{{x}}/{{y}}.png', {{ maxZoom: 18, attribution: '&copy; OpenStreetMap contributors' }}).addTo(map);

    const billundMarker = L.marker([billund.lat, billund.lon]).addTo(map);
    billundMarker.bindPopup('<b>Billund</b><br>Reference for pendling');

    const markerLayer = L.layerGroup().addTo(map);

    const maxCommute = document.getElementById('maxCommute');
    const maxRent = document.getElementById('maxRent');
    const sortBy = document.getElementById('sortBy');
    const dogOnly = document.getElementById('dogOnly');
    const groupChecks = document.getElementById('groupChecks');
    const listPanel = document.getElementById('listPanel');

    function selectedGroups() {{
      return Array.from(groupChecks.querySelectorAll('.groupCheck:checked')).map(x => x.value);
    }}

    function filterData() {{
      const mc = Number(maxCommute.value || 9999);
      const mr = Number(maxRent.value || 999999);
      const dg = dogOnly.value === 'dog';
      const groups = new Set(selectedGroups());

      let data = listings.filter(x =>
        x.lat !== null && x.lon !== null &&
        (x.commute_min ?? 9999) <= mc &&
        (x.rent ?? 999999) <= mr &&
        (!dg || x.dog) &&
        groups.has(x.group)
      );

      if (sortBy.value === 'rent') data.sort((a,b) => (a.rent ?? 999999) - (b.rent ?? 999999));
      else if (sortBy.value === 'score') data.sort((a,b) => (b.score ?? 0) - (a.score ?? 0));
      else data.sort((a,b) => (a.commute_min ?? 9999) - (b.commute_min ?? 9999));

      return data;
    }}

    function renderMap(data) {{
      markerLayer.clearLayers();
      data.forEach(x => {{
        const color = x.dog ? '#1f8f5f' : '#dc6b2f';
        const marker = L.circleMarker([x.lat, x.lon], {{ radius: 8, color, fillColor: color, fillOpacity: 0.85, weight: 1 }});
        marker.bindPopup(`
          <b>${{x.title}}</b><br>
          ${{x.address || ''}}, ${{x.postal_code || ''}} ${{x.city_from_page || x.city || ''}}<br>
          Pendling: <b>${{x.commute_min ?? '-'}} min</b> · Pris: <b>${{x.rent ?? '-'}} kr</b><br>
          Lokation: <b>${{x.group}}</b><br>
          <a href="${{x.url}}" target="_blank" rel="noopener noreferrer">Se annonce</a>
        `);
        marker.addTo(markerLayer);
      }});
    }}

    function fmtMoney(v) {{
      if (v === null || v === undefined) return '-';
      return new Intl.NumberFormat('da-DK').format(v) + ' kr';
    }}

    function renderList(data) {{
      const grouped = {{}};
      data.forEach(x => {{
        grouped[x.group] = grouped[x.group] || [];
        grouped[x.group].push(x);
      }});

      const groups = Object.keys(grouped).sort();
      listPanel.innerHTML = groups.map(g => {{
        const rows = grouped[g].map(x => `
          <div class="item">
            <img src="${{x.image || ''}}" alt="" loading="lazy" />
            <div class="meta">
              <b>${{x.city}} · ${{fmtMoney(x.rent)}}</b>
              <small>${{x.commute_min ?? '-'}} min til Billund · ${{x.type || ''}} · ${{x.area ?? '-'}} m2 · ${{x.rooms ?? '-'}} vaer.</small><br>
              <a href="${{x.url}}" target="_blank" rel="noopener noreferrer">Aaben annonce</a>
            </div>
            <span class="tag ${{x.dog ? 'dog' : ''}}">${{x.dog ? 'Hund OK' : 'Ingen hund'}}</span>
          </div>
        `).join('');
        return `<section class="group-block"><h3>${{g}} (${{grouped[g].length}})</h3>${{rows}}</section>`;
      }}).join('');
    }}

    function rerender() {{
      const data = filterData();
      renderMap(data);
      renderList(data);
    }}

    [maxCommute, maxRent, sortBy, dogOnly].forEach(el => el.addEventListener('input', rerender));
    groupChecks.addEventListener('change', rerender);
    rerender();
  </script>
</body>
</html>
"""
    OUTPUT_HTML.write_text(html, encoding="utf-8")


def copy_to_web_bundle() -> None:
    WEB_DIR.mkdir(parents=True, exist_ok=True)
    (WEB_DIR / "pendling.html").write_text(OUTPUT_HTML.read_text(encoding="utf-8"), encoding="utf-8")
    (WEB_DIR / OUTPUT_CSV.name).write_text(OUTPUT_CSV.read_text(encoding="utf-8"), encoding="utf-8")


def main() -> None:
    rows = read_csv_with_fallback(INPUT_FILE)
    built = build(rows)
    write_csv(built)
    create_map(built)
    copy_to_web_bundle()
    print("Created:")
    print(f"- {OUTPUT_CSV}")
    print(f"- {OUTPUT_HTML}")
    print(f"- {WEB_DIR / 'pendling.html'}")


if __name__ == "__main__":
    main()
