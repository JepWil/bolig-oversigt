from __future__ import annotations

import csv
import datetime as dt
import json
import re
import time
from pathlib import Path
from typing import Any

import requests
from bs4 import BeautifulSoup

INPUT_FILE = Path("Jeppe_Bolig_Masterliste_Fuld(Masterliste).csv")
OUTPUT_FILE = Path("Jeppe_Bolig_Masterliste_Beriget.csv")
QA_FILE = Path("Jeppe_Bolig_Masterliste_QA.csv")

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/124.0.0.0 Safari/537.36"
)

NEW_COLUMNS = [
    "listing_id",
    "canonical_url",
    "address",
    "postal_code",
    "city_from_page",
    "lat",
    "lon",
    "date_posted",
    "valid_from",
    "monthly_rent_from_page_dkk",
    "area_from_page_m2",
    "rooms_from_page",
    "deposit_dkk",
    "monthly_aconto_dkk",
    "move_in_price_dkk",
    "available_from_text",
    "energy_label",
    "furnished",
    "floor",
    "pets_allowed",
    "pets_policy_text",
    "landlord_name",
    "landlord_validated",
    "scrape_status",
    "scraped_at_utc",
]

LABEL_ALIASES = {
    "Depositum": ["Depositum"],
    "Indflytningspris": ["Indflytningspris"],
    "Aconto": ["Aconto", "Maanedlig aconto", "Mnedlig aconto"],
    "Ledig fra": ["Ledig fra"],
    "Energimaerke": ["Energimaerke", "Energimarke", "Energi"],
    "Moebleret": ["Moebleret", "Mobleret"],
    "Etage": ["Etage"],
    "Husdyr tilladt": ["Husdyr tilladt", "Husdyr"],
    "Sagsnr": ["Sagsnr.", "Sagsnr"],
}


def normalize_text(value: str) -> str:
    value = value.replace("\xa0", " ").strip()
    replacements = {
        "å": "aa",
        "Å": "Aa",
        "æ": "ae",
        "Æ": "Ae",
        "ø": "oe",
        "Ø": "Oe",
        "é": "e",
        "è": "e",
        "ê": "e",
        "ë": "e",
        "ö": "o",
        "ü": "u",
        "ä": "a",
        "§": "a",
        "�": "",
    }
    for src, dst in replacements.items():
        value = value.replace(src, dst)
    return value


def parse_money_to_int(value: str | None) -> int | None:
    if not value:
        return None
    cleaned = re.sub(r"[^0-9]", "", value)
    if not cleaned:
        return None
    return int(cleaned)


def get_listing_id(url: str) -> str | None:
    match = re.search(r"-id-(\d+)", url)
    return match.group(1) if match else None


def extract_label_value(html: str, aliases: list[str]) -> str | None:
    for alias in aliases:
        # Try exact label first.
        pattern = re.compile(
            rf"<span[^>]*>\s*{re.escape(alias)}\s*</span>\s*</div>\s*<div[^>]*>\s*<span[^>]*>\s*([^<]+?)\s*</span>",
            re.IGNORECASE | re.DOTALL,
        )
        match = pattern.search(html)
        if match:
            return " ".join(match.group(1).split())

    # Fallback: loose matching by alias token.
    for alias in aliases:
        token = normalize_text(alias).lower().split()[0]
        pattern = re.compile(
            rf"<span[^>]*>\s*[^<]*{re.escape(token)}[^<]*</span>\s*</div>\s*<div[^>]*>\s*<span[^>]*>\s*([^<]+?)\s*</span>",
            re.IGNORECASE | re.DOTALL,
        )
        match = pattern.search(normalize_text(html))
        if match:
            return " ".join(match.group(1).split())

    return None


def extract_landlord_name(html: str) -> str | None:
    match = re.search(
        r'<span[^>]*>([^<]{2,60})</span>\s*<span[^>]*type="button"[^>]*role="switch"',
        html,
        re.IGNORECASE | re.DOTALL,
    )
    if not match:
        return None
    candidate = " ".join(match.group(1).split())
    banned = {"Sog sagsnr.", "Sagsnr.", "Sagsnr"}
    return None if candidate in banned else candidate


def extract_json_ld_objects(soup: BeautifulSoup) -> list[dict[str, Any]]:
    objs: list[dict[str, Any]] = []
    for script in soup.find_all("script", {"type": "application/ld+json"}):
        raw = script.get_text(strip=True)
        if not raw:
            continue
        try:
            parsed = json.loads(raw)
            if isinstance(parsed, dict):
                objs.append(parsed)
            elif isinstance(parsed, list):
                objs.extend([item for item in parsed if isinstance(item, dict)])
        except json.JSONDecodeError:
            continue
    return objs


def enrich_from_url(url: str, timeout: int = 35) -> dict[str, Any]:
    listing_id = get_listing_id(url)
    if not listing_id:
        return {"scrape_status": "missing_listing_id"}

    candidate_urls = [url, f"https://www.boligportal.dk/rækkehuse/id-{listing_id}", f"https://www.boligportal.dk/huse/id-{listing_id}"]

    errors: list[str] = []
    response = None
    for candidate in candidate_urls:
        try:
            response = requests.get(
                candidate,
                headers={"User-Agent": USER_AGENT, "Accept-Language": "da-DK,da;q=0.9"},
                timeout=timeout,
                allow_redirects=True,
            )
            if response.status_code == 200 and "boligportal" in response.url.lower():
                break
            errors.append(f"{candidate} -> {response.status_code}")
        except requests.RequestException as exc:
            errors.append(f"{candidate} -> {exc.__class__.__name__}")

    if response is None or response.status_code != 200:
        return {
            "listing_id": listing_id,
            "scrape_status": "http_error",
            "scrape_error": " | ".join(errors)[:400],
        }

    html = response.text
    soup = BeautifulSoup(html, "html.parser")
    jsonld = extract_json_ld_objects(soup)

    listing_obj = next((o for o in jsonld if o.get("@type") == "RealEstateListing"), {})
    offer_obj = next((o for o in jsonld if o.get("@type") == "Offer"), {})
    item_offered = offer_obj.get("itemOffered", {}) if isinstance(offer_obj, dict) else {}
    address_obj = item_offered.get("address", {}) if isinstance(item_offered, dict) else {}
    geo_obj = item_offered.get("geo", {}) if isinstance(item_offered, dict) else {}

    mapped = {
        "listing_id": listing_id,
        "canonical_url": response.url,
        "address": address_obj.get("streetAddress"),
        "postal_code": address_obj.get("postalCode"),
        "city_from_page": address_obj.get("addressLocality"),
        "lat": geo_obj.get("latitude"),
        "lon": geo_obj.get("longitude"),
        "date_posted": listing_obj.get("datePosted"),
        "valid_from": offer_obj.get("validFrom"),
        "monthly_rent_from_page_dkk": offer_obj.get("price"),
        "area_from_page_m2": (item_offered.get("floorSize") or {}).get("value") if isinstance(item_offered, dict) else None,
        "rooms_from_page": item_offered.get("numberOfRooms") if isinstance(item_offered, dict) else None,
        "deposit_dkk": parse_money_to_int(extract_label_value(html, LABEL_ALIASES["Depositum"])),
        "monthly_aconto_dkk": parse_money_to_int(extract_label_value(html, LABEL_ALIASES["Aconto"])),
        "move_in_price_dkk": parse_money_to_int(extract_label_value(html, LABEL_ALIASES["Indflytningspris"])),
        "available_from_text": extract_label_value(html, LABEL_ALIASES["Ledig fra"]),
        "energy_label": extract_label_value(html, LABEL_ALIASES["Energimaerke"]),
        "furnished": extract_label_value(html, LABEL_ALIASES["Moebleret"]),
        "floor": extract_label_value(html, LABEL_ALIASES["Etage"]),
        "pets_allowed": item_offered.get("petsAllowed"),
        "pets_policy_text": extract_label_value(html, LABEL_ALIASES["Husdyr tilladt"]),
        "landlord_name": extract_landlord_name(html),
        "landlord_validated": "Valideret af BoligPortal" in html,
        "scrape_status": "ok",
        "scraped_at_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
    }

    # If listing id on page differs, keep page value for audit.
    page_listing = extract_label_value(html, LABEL_ALIASES["Sagsnr"])
    if page_listing and str(page_listing).strip() != str(listing_id):
        mapped["scrape_status"] = "listing_id_mismatch"

    return mapped


def main() -> None:
    rows: list[dict[str, Any]] = []
    qa_rows: list[dict[str, Any]] = []

    input_fields: list[str] = []
    last_error: Exception | None = None
    for enc in ("utf-8-sig", "utf-8", "cp1252", "latin1"):
        try:
            with INPUT_FILE.open("r", encoding=enc, newline="") as f:
                reader = csv.DictReader(f)
                input_fields = reader.fieldnames or []
                rows = list(reader)
            print(f"Loaded input using encoding: {enc}")
            break
        except UnicodeDecodeError as exc:
            last_error = exc
            continue

    if not rows:
        raise RuntimeError(f"Could not decode input CSV with known encodings: {last_error}")

    output_fields = list(input_fields)
    for col in NEW_COLUMNS:
        if col not in output_fields:
            output_fields.append(col)

    for i, row in enumerate(rows, start=1):
        url = (row.get("URL") or "").strip()
        if not url:
            result = {"scrape_status": "missing_url", "scraped_at_utc": dt.datetime.now(dt.timezone.utc).isoformat()}
        else:
            result = enrich_from_url(url)
            time.sleep(0.6)

        for col in NEW_COLUMNS:
            row[col] = result.get(col)

        # Fill missing monthly rent from scraped value when possible.
        mdleje_key = "Mdl. leje"
        current_rent = (row.get(mdleje_key) or "").strip() if mdleje_key in row else ""
        scraped_rent = result.get("monthly_rent_from_page_dkk")
        if not current_rent and scraped_rent not in (None, ""):
            row[mdleje_key] = str(int(scraped_rent))

        qa_rows.append(
            {
                "Rang": row.get("Rang"),
                "By": row.get("By"),
                "URL": url,
                "listing_id": row.get("listing_id"),
                "status": row.get("scrape_status"),
                "rent_csv": row.get("Mdl. leje"),
                "rent_scraped": row.get("monthly_rent_from_page_dkk"),
                "rent_mismatch": "yes"
                if str(row.get("Mdl. leje") or "").strip()
                and str(row.get("monthly_rent_from_page_dkk") or "").strip()
                and str(row.get("Mdl. leje")).strip() != str(row.get("monthly_rent_from_page_dkk")).strip()
                else "",
                "area_csv": row.get("m²") or row.get("m2") or row.get("m�"),
                "area_scraped": row.get("area_from_page_m2"),
                "area_mismatch": "yes"
                if str(row.get("m²") or row.get("m2") or row.get("m�") or "").strip()
                and str(row.get("area_from_page_m2") or "").strip()
                and str(row.get("m²") or row.get("m2") or row.get("m�")).strip() != str(row.get("area_from_page_m2")).strip()
                else "",
                "deposit_dkk": row.get("deposit_dkk"),
                "move_in_price_dkk": row.get("move_in_price_dkk"),
                "available_from_text": row.get("available_from_text"),
                "energy_label": row.get("energy_label"),
                "note": "manual_check" if row.get("scrape_status") != "ok" else "",
            }
        )

        print(f"[{i}/{len(rows)}] Rang {row.get('Rang')} -> {row.get('scrape_status')}")

    with OUTPUT_FILE.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=output_fields)
        writer.writeheader()
        writer.writerows(rows)

    with QA_FILE.open("w", encoding="utf-8-sig", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(qa_rows[0].keys()))
        writer.writeheader()
        writer.writerows(qa_rows)

    filled_rent = sum(1 for r in rows if (r.get("Mdl. leje") or "").strip())
    print("---")
    print(f"Rows processed: {len(rows)}")
    print(f"Rows with monthly rent after enrichment: {filled_rent}")
    print(f"Output file: {OUTPUT_FILE}")
    print(f"QA file: {QA_FILE}")


if __name__ == "__main__":
    main()
