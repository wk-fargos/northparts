#!/usr/bin/env python3
"""
NorthParts — Allegro Auto Parts Parser
=======================================
Scrapes auto parts from Allegro.pl, translates descriptions to English,
applies markup, and exports to JSON for the NorthParts website.

Modes:
  1. Allegro REST API (recommended, requires API keys)
  2. Web scraping fallback (no keys needed, demo mode)

Setup:
  pip install requests beautifulsoup4 deep-translator Pillow tqdm

Usage:
  python allegro_parser.py --mode api --query "części samochodowe BMW" --pages 3
  python allegro_parser.py --mode scrape --query "hamulce BMW" --pages 2
  python allegro_parser.py --mode demo   (uses built-in test data)
"""

import os
import re
import json
import time
import random
import argparse
import logging
import hashlib
from datetime import datetime
from pathlib import Path
from urllib.parse import quote, urlencode

import requests
from bs4 import BeautifulSoup

try:
    from deep_translator import GoogleTranslator
    TRANSLATOR_AVAILABLE = True
except ImportError:
    TRANSLATOR_AVAILABLE = False
    print("[WARN] deep-translator not installed. Run: pip install deep-translator")

try:
    from tqdm import tqdm
    TQDM_AVAILABLE = True
except ImportError:
    TQDM_AVAILABLE = False

# ──────────────────────────────────────────────
# CONFIG
# ──────────────────────────────────────────────

OUTPUT_DIR = Path("northparts_data")
IMAGES_DIR = OUTPUT_DIR / "images"
OUTPUT_JSON = OUTPUT_DIR / "products.json"

DEFAULT_MARKUP = 30          # percent
DEFAULT_CURRENCY = "CAD"
PLN_TO_CAD = 0.34            # approximate rate (update as needed)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "pl-PL,pl;q=0.9,en;q=0.8",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S"
)
log = logging.getLogger("allegro_parser")


# ──────────────────────────────────────────────
# TRANSLATOR
# ──────────────────────────────────────────────

class Translator:
    """Translates Polish text to English using Google Translate (free tier)."""

    def __init__(self):
        self._cache = {}

    def translate(self, text: str) -> str:
        if not text or not text.strip():
            return text
        if not TRANSLATOR_AVAILABLE:
            return text  # return original if lib not installed

        key = hashlib.md5(text.encode()).hexdigest()
        if key in self._cache:
            return self._cache[key]

        try:
            result = GoogleTranslator(source="pl", target="en").translate(text)
            self._cache[key] = result
            time.sleep(0.3)  # be polite to the API
            return result
        except Exception as e:
            log.warning(f"Translation failed: {e}")
            return text

    def translate_product(self, product: dict) -> dict:
        """Translate title and description fields of a product dict."""
        log.info(f"  Translating: {product.get('title_pl','')[:50]}...")
        product["title"] = self.translate(product.get("title_pl", ""))
        product["description"] = self.translate(product.get("description_pl", ""))
        return product


translator = Translator()


# ──────────────────────────────────────────────
# PRICE CALCULATOR
# ──────────────────────────────────────────────

def pln_to_cad(pln: float) -> float:
    return round(pln * PLN_TO_CAD, 2)

def apply_markup(price_cad: float, markup_pct: float) -> float:
    return round(price_cad * (1 + markup_pct / 100), 2)

def build_prices(pln_price: float, markup: float) -> dict:
    base_cad = pln_to_cad(pln_price)
    final_cad = apply_markup(base_cad, markup)
    return {
        "price_pln": round(pln_price, 2),
        "price_cad_base": base_cad,
        "price_cad_final": final_cad,
        "markup_pct": markup,
        "currency": DEFAULT_CURRENCY,
    }


# ──────────────────────────────────────────────
# IMAGE DOWNLOADER
# ──────────────────────────────────────────────

def download_image(url: str, product_id: str) -> str | None:
    """Download product image, return local relative path."""
    if not url:
        return None
    try:
        IMAGES_DIR.mkdir(parents=True, exist_ok=True)
        ext = url.split("?")[0].rsplit(".", 1)[-1] or "jpg"
        filename = f"{product_id}.{ext}"
        filepath = IMAGES_DIR / filename

        if filepath.exists():
            return f"images/{filename}"

        resp = requests.get(url, headers=HEADERS, timeout=10)
        resp.raise_for_status()
        filepath.write_bytes(resp.content)
        log.info(f"  Downloaded image → {filename}")
        return f"images/{filename}"
    except Exception as e:
        log.warning(f"  Image download failed ({url[:60]}...): {e}")
        return None


# ──────────────────────────────────────────────
# MODE 1 — ALLEGRO REST API
# ──────────────────────────────────────────────

class AllegroAPIParser:
    """
    Official Allegro REST API parser.
    Requires: Client ID + Client Secret from developer.allegro.pl
    Docs: https://developer.allegro.pl/documentation
    """

    AUTH_URL = "https://allegro.pl/auth/oauth/token"
    API_BASE = "https://api.allegro.pl"

    def __init__(self, client_id: str, client_secret: str):
        self.client_id = client_id
        self.client_secret = client_secret
        self.token = None

    def authenticate(self):
        """Get OAuth2 access token (client credentials flow)."""
        log.info("Authenticating with Allegro API...")
        resp = requests.post(
            self.AUTH_URL,
            auth=(self.client_id, self.client_secret),
            data={"grant_type": "client_credentials"},
            timeout=15,
        )
        resp.raise_for_status()
        self.token = resp.json()["access_token"]
        log.info("✓ Authenticated successfully")

    def _headers(self):
        return {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.allegro.public.v1+json",
        }

    def search_offers(self, query: str, category_id: str = None,
                      limit: int = 60, offset: int = 0) -> dict:
        """Search listings by keyword."""
        params = {
            "phrase": query,
            "limit": limit,
            "offset": offset,
            "sort": "-withDeliveryPrice",  # most popular first
        }
        if category_id:
            params["category.id"] = category_id

        resp = requests.get(
            f"{self.API_BASE}/offers/listing",
            headers=self._headers(),
            params=params,
            timeout=20,
        )
        resp.raise_for_status()
        return resp.json()

    def get_offer_details(self, offer_id: str) -> dict:
        """Fetch full details for a single offer."""
        resp = requests.get(
            f"{self.API_BASE}/sale/product-offers/{offer_id}",
            headers=self._headers(),
            timeout=15,
        )
        if resp.status_code == 404:
            return {}
        resp.raise_for_status()
        return resp.json()

    def parse_offer(self, offer: dict, markup: float) -> dict:
        """Convert API response to NorthParts product format."""
        offer_id = offer.get("id", "")
        title_pl = offer.get("name", "")
        price_pln = float(offer.get("sellingMode", {}).get("price", {}).get("amount", 0))
        images = offer.get("images", [])
        image_url = images[0].get("url", "") if images else ""

        # Download image
        image_local = download_image(image_url, offer_id)

        # Translate
        product = {
            "id": offer_id,
            "title_pl": title_pl,
            "description_pl": offer.get("description", {}).get("sections", [{}])[0]
                              .get("items", [{}])[0].get("value", "") if offer.get("description") else "",
            "image_url": image_url,
            "image_local": image_local,
            "allegro_url": f"https://allegro.pl/oferta/{offer_id}",
            "category": offer.get("category", {}).get("name", ""),
            "source": "allegro_api",
        }
        product = translator.translate_product(product)
        product.update(build_prices(price_pln, markup))
        return product

    def run(self, query: str, markup: float, pages: int = 3,
            download_images: bool = True) -> list[dict]:
        self.authenticate()
        products = []
        limit = 60

        for page in range(pages):
            offset = page * limit
            log.info(f"Fetching page {page+1}/{pages} (offset={offset})...")
            data = self.search_offers(query, limit=limit, offset=offset)
            items = data.get("items", {}).get("regular", [])
            if not items:
                log.info("No more results.")
                break

            it = tqdm(items, desc=f"Page {page+1}") if TQDM_AVAILABLE else items
            for offer in it:
                try:
                    product = self.parse_offer(offer, markup)
                    products.append(product)
                    time.sleep(0.2)
                except Exception as e:
                    log.warning(f"Failed to parse offer {offer.get('id')}: {e}")

        log.info(f"✓ API: collected {len(products)} products")
        return products


# ──────────────────────────────────────────────
# MODE 2 — WEB SCRAPER (fallback)
# ──────────────────────────────────────────────

class AllegroScraper:
    """
    Web scraper fallback for Allegro.
    Use when API credentials are not available.
    Note: May break if Allegro changes their HTML structure.
    """

    BASE_URL = "https://allegro.pl"
    SEARCH_URL = "https://allegro.pl/listing"

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update(HEADERS)

    def _get(self, url: str, params: dict = None) -> BeautifulSoup | None:
        try:
            time.sleep(random.uniform(1.5, 3.5))  # polite delay
            resp = self.session.get(url, params=params, timeout=20)
            resp.raise_for_status()
            return BeautifulSoup(resp.text, "html.parser")
        except requests.RequestException as e:
            log.error(f"Request failed: {url} → {e}")
            return None

    def search_page(self, query: str, page: int = 1) -> list[dict]:
        """Scrape one search results page, return list of raw offer dicts."""
        log.info(f"Scraping page {page}: '{query}'...")
        params = {"string": query, "p": page}
        soup = self._get(self.SEARCH_URL, params=params)
        if not soup:
            return []

        offers = []
        # Allegro uses data-role="article" or similar on listing items
        # Selectors may need updating if Allegro redesigns
        articles = soup.select("article[data-role='offer']") or \
                   soup.select("[data-testid='listing-grid'] article") or \
                   soup.select("article")

        log.info(f"  Found {len(articles)} articles on page {page}")

        for art in articles:
            try:
                offer = self._parse_article(art)
                if offer:
                    offers.append(offer)
            except Exception as e:
                log.debug(f"  Article parse error: {e}")

        return offers

    def _parse_article(self, art) -> dict | None:
        """Extract data from a single search result article."""
        # Title
        title_tag = art.select_one("h2 a, [data-testid='listing-grid-title'] a, a[title]")
        title = title_tag.get_text(strip=True) if title_tag else ""
        if not title:
            return None

        # URL
        link = title_tag.get("href", "") if title_tag else ""
        if link and not link.startswith("http"):
            link = self.BASE_URL + link

        # Offer ID from URL
        offer_id = ""
        m = re.search(r"-(\d{9,})$", link)
        if m:
            offer_id = m.group(1)
        else:
            offer_id = hashlib.md5(title.encode()).hexdigest()[:12]

        # Price
        price_pln = 0.0
        price_tag = art.select_one("[data-testid='price-normal'] span, .price, [class*='price']")
        if price_tag:
            price_text = price_tag.get_text(strip=True).replace("\xa0", "").replace(",", ".").replace("zł", "").strip()
            m = re.search(r"[\d.]+", price_text.replace(" ", ""))
            if m:
                price_pln = float(m.group())

        # Image
        img_tag = art.select_one("img[src], img[data-src]")
        image_url = ""
        if img_tag:
            image_url = img_tag.get("src") or img_tag.get("data-src", "")
            if image_url.startswith("//"):
                image_url = "https:" + image_url

        return {
            "id": offer_id,
            "title_pl": title,
            "description_pl": "",  # not available on listing page
            "price_pln": price_pln,
            "image_url": image_url,
            "allegro_url": link,
            "source": "scrape",
        }

    def get_offer_description(self, url: str) -> str:
        """Visit offer page to get full description (optional, slower)."""
        soup = self._get(url)
        if not soup:
            return ""
        desc_tag = soup.select_one("[data-box-name='Description'] section, .offer-description")
        if desc_tag:
            return desc_tag.get_text(separator=" ", strip=True)[:1000]
        return ""

    def run(self, query: str, markup: float, pages: int = 3,
            fetch_descriptions: bool = False,
            download_images: bool = True) -> list[dict]:
        all_offers = []

        for page in range(1, pages + 1):
            offers = self.search_page(query, page)
            if not offers:
                break
            all_offers.extend(offers)

        log.info(f"Total scraped: {len(all_offers)} offers. Processing...")
        products = []

        it = tqdm(all_offers) if TQDM_AVAILABLE else all_offers
        for offer in it:
            try:
                # Optionally fetch full description
                if fetch_descriptions and offer.get("allegro_url"):
                    offer["description_pl"] = self.get_offer_description(offer["allegro_url"])

                # Download image
                image_local = None
                if download_images and offer.get("image_url"):
                    image_local = download_image(offer["image_url"], offer["id"])

                # Build product
                product = {**offer, "image_local": image_local}
                product = translator.translate_product(product)
                product.update(build_prices(offer.get("price_pln", 0), markup))
                products.append(product)

            except Exception as e:
                log.warning(f"Processing failed for offer {offer.get('id')}: {e}")

        log.info(f"✓ Scraper: collected {len(products)} products")
        return products


# ──────────────────────────────────────────────
# MODE 3 — DEMO (built-in test data)
# ──────────────────────────────────────────────

DEMO_DATA_PL = [
    {"id": "demo-001", "title_pl": "Klocki hamulcowe przednie BMW 3 E90 E92 ceramiczne", "description_pl": "Wysokiej jakości ceramiczne klocki hamulcowe do BMW serii 3. Niski pył, cicha praca, doskonały hamowanie.", "price_pln": 115.00, "image_url": "", "allegro_url": "https://allegro.pl/demo", "category": "Brakes", "make": "BMW", "oem": "34116761281"},
    {"id": "demo-002", "title_pl": "Zestaw rozrządu Toyota Corolla 1.6 VVT-i kompletny", "description_pl": "Kompletny zestaw rozrządu z napinaczem i rolką prowadzącą. Jakość OEM.", "price_pln": 214.00, "image_url": "", "allegro_url": "https://allegro.pl/demo", "category": "Engine", "make": "Toyota", "oem": "13568-0D010"},
    {"id": "demo-003", "title_pl": "Filtr oleju + filtr powietrza VW Golf Mk6 2.0 TDI", "description_pl": "Komplet filtrów do VW Golf Mk6. Chroni silnik przed zanieczyszczeniami.", "price_pln": 74.00, "image_url": "", "allegro_url": "https://allegro.pl/demo", "category": "Filters", "make": "Volkswagen", "oem": "1K0-129-620"},
    {"id": "demo-004", "title_pl": "Amortyzatory przednie Ford Focus Mk3 para", "description_pl": "Amortyzatory gazowe do Forda Focusa. Poprawa prowadzenia, montaż OEM. Sprzedawane jako para.", "price_pln": 265.00, "image_url": "", "allegro_url": "https://allegro.pl/demo", "category": "Suspension", "make": "Ford", "oem": "BM51-18K001-AB"},
    {"id": "demo-005", "title_pl": "Cewka zapłonowa Audi A4 B8 2.0 TFSI oryginalna", "description_pl": "Bezpośredni zamiennik cewki zapłonowej OEM. Eliminuje problem z zapłonem.", "price_pln": 132.00, "image_url": "", "allegro_url": "https://allegro.pl/demo", "category": "Electrical", "make": "Audi", "oem": "06H905115"},
    {"id": "demo-006", "title_pl": "Chłodnica Honda Civic 1.8i FD nowa aluminiowa", "description_pl": "Aluminiowa chłodnica z plastikowymi zbiornikami. Bezpośredni zamiennik OEM.", "price_pln": 340.00, "image_url": "", "allegro_url": "https://allegro.pl/demo", "category": "Cooling", "make": "Honda", "oem": "19010-RNA-A51"},
    {"id": "demo-007", "title_pl": "Tarcze hamulcowe tylne Toyota RAV4 Mk3 wentylowane para", "description_pl": "Wentylowane tarcze tylne, wymiary OEM. Para. Kompatybilne ze standardowymi zaciskami.", "price_pln": 172.00, "image_url": "", "allegro_url": "https://allegro.pl/demo", "category": "Brakes", "make": "Toyota", "oem": "42431-42100"},
    {"id": "demo-008", "title_pl": "Uszczelka pokrywy zaworów BMW N52 komplet", "description_pl": "Kompletny zestaw uszczelek pokrywy zaworów. Zatrzymuje wycieki oleju z głowicy.", "price_pln": 98.00, "image_url": "", "allegro_url": "https://allegro.pl/demo", "category": "Engine", "make": "BMW", "oem": "11127552281"},
]

class DemoParser:
    def run(self, markup: float, **kwargs) -> list[dict]:
        log.info("Running in DEMO mode with built-in test data...")
        products = []
        for item in DEMO_DATA_PL:
            p = {**item, "image_local": None, "source": "demo"}
            p = translator.translate_product(p)
            p.update(build_prices(item["price_pln"], markup))
            products.append(p)
            time.sleep(0.1)
        log.info(f"✓ Demo: {len(products)} products ready")
        return products


# ──────────────────────────────────────────────
# EXPORT
# ──────────────────────────────────────────────

def export_json(products: list[dict], markup: float):
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    output = {
        "meta": {
            "generated_at": datetime.now().isoformat(),
            "total_products": len(products),
            "markup_pct": markup,
            "currency": DEFAULT_CURRENCY,
            "pln_to_cad_rate": PLN_TO_CAD,
        },
        "products": products,
    }
    OUTPUT_JSON.write_text(json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8")
    log.info(f"✓ Exported {len(products)} products → {OUTPUT_JSON}")
    return OUTPUT_JSON


def export_js_snippet(products: list[dict]):
    """Export as JS variable for direct drop-in to the website HTML."""
    js_products = []
    for i, p in enumerate(products):
        js_products.append({
            "id": i + 1,
            "category": p.get("category", "Parts"),
            "make": p.get("make", "Universal"),
            "title": p.get("title", p.get("title_pl", "")),
            "desc": p.get("description", p.get("description_pl", ""))[:200],
            "compat": p.get("compat", ""),
            "basePrice": p.get("price_cad_base", 0),
            "badge": p.get("badge", None),
            "icon": p.get("icon", "🔧"),
            "oemNo": p.get("oem", p.get("id", "")),
            "imageLocal": p.get("image_local", None),
            "allegro_url": p.get("allegro_url", ""),
        })

    js_file = OUTPUT_DIR / "products_snippet.js"
    js_file.write_text(
        "// NorthParts — Auto-generated product data\n"
        "// Copy this into your autoparts-canada.html PRODUCTS array\n\n"
        f"const PRODUCTS = {json.dumps(js_products, ensure_ascii=False, indent=2)};\n",
        encoding="utf-8"
    )
    log.info(f"✓ JS snippet exported → {js_file}")


def print_summary(products: list[dict], markup: float):
    print("\n" + "="*55)
    print("  NORTHPARTS PARSER — SUMMARY")
    print("="*55)
    print(f"  Products parsed:    {len(products)}")
    print(f"  Markup applied:     {markup}%")
    print(f"  PLN → CAD rate:     {PLN_TO_CAD}")
    print(f"  Output dir:         {OUTPUT_DIR.resolve()}")
    print()
    if products:
        print("  Sample products:")
        for p in products[:3]:
            title = p.get("title") or p.get("title_pl","")[:50]
            base = p.get("price_cad_base", 0)
            final = p.get("price_cad_final", 0)
            print(f"    • {title[:48]}")
            print(f"      CA${base:.2f} base → CA${final:.2f} with {markup}% markup")
    print("="*55 + "\n")


# ──────────────────────────────────────────────
# MAIN
# ──────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="NorthParts — Allegro Auto Parts Parser",
        formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--mode", choices=["api", "scrape", "demo"], default="demo",
                        help="Parser mode:\n  api    = Allegro REST API (needs credentials)\n  scrape = Web scraper\n  demo   = Built-in test data")
    parser.add_argument("--query", default="części samochodowe", help="Search query (in Polish)")
    parser.add_argument("--pages", type=int, default=2, help="Number of pages to fetch")
    parser.add_argument("--markup", type=float, default=DEFAULT_MARKUP, help="Price markup %% (default: 30)")
    parser.add_argument("--no-images", action="store_true", help="Skip image downloading")
    parser.add_argument("--fetch-desc", action="store_true", help="[scrape] Fetch full descriptions (slower)")
    parser.add_argument("--client-id", default=os.getenv("ALLEGRO_CLIENT_ID", ""), help="Allegro API Client ID")
    parser.add_argument("--client-secret", default=os.getenv("ALLEGRO_CLIENT_SECRET", ""), help="Allegro API Client Secret")
    parser.add_argument("--pln-rate", type=float, default=0.34, help="PLN to CAD rate")

    args = parser.parse_args()

    global PLN_TO_CAD
    PLN_TO_CAD = args.pln_rate

    log.info(f"Mode: {args.mode.upper()} | Query: '{args.query}' | Pages: {args.pages} | Markup: {args.markup}%")

    products = []

    if args.mode == "demo":
        products = DemoParser().run(markup=args.markup)

    elif args.mode == "scrape":
        scraper = AllegroScraper()
        products = scraper.run(
            query=args.query,
            markup=args.markup,
            pages=args.pages,
            fetch_descriptions=args.fetch_desc,
            download_images=not args.no_images,
        )

    elif args.mode == "api":
        if not args.client_id or not args.client_secret:
            log.error("API mode requires --client-id and --client-secret")
            log.error("Get credentials at: https://developer.allegro.pl/")
            log.error("Or set env vars: ALLEGRO_CLIENT_ID, ALLEGRO_CLIENT_SECRET")
            return
        api = AllegroAPIParser(args.client_id, args.client_secret)
        products = api.run(
            query=args.query,
            markup=args.markup,
            pages=args.pages,
            download_images=not args.no_images,
        )

    if products:
        export_json(products, args.markup)
        export_js_snippet(products)
        print_summary(products, args.markup)
    else:
        log.warning("No products collected.")


if __name__ == "__main__":
    main()
