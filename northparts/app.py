"""
NorthParts — Flask + PostgreSQL
================================
Локальный запуск:
  pip install -r requirements.txt
  python app.py

На Render всё настраивается автоматически через переменные окружения.
"""

import os, json, hashlib, requests
from datetime import datetime
from functools import wraps
from flask import (Flask, render_template, request, jsonify,
                   session, redirect, url_for, send_from_directory)
import psycopg2
from psycopg2.extras import RealDictCursor, Json

app = Flask(__name__)
app.secret_key = os.environ.get("SECRET_KEY", "northparts-dev-key-change-in-prod")

# ──────────────────────────────────────────────
# DATABASE
# ──────────────────────────────────────────────

DATABASE_URL = os.environ.get("DATABASE_URL", "")
if DATABASE_URL.startswith("postgres://"):
    DATABASE_URL = DATABASE_URL.replace("postgres://", "postgresql://", 1)

def get_conn():
    if not DATABASE_URL:
        raise RuntimeError("DATABASE_URL not set.")
    return psycopg2.connect(DATABASE_URL, cursor_factory=RealDictCursor)

def query(sql, params=(), fetch="all"):
    conn = get_conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                if fetch == "all":
                    return [dict(r) for r in cur.fetchall()]
                elif fetch == "one":
                    row = cur.fetchone()
                    return dict(row) if row else None
                return None
    finally:
        conn.close()

def execute(sql, params=()):
    conn = get_conn()
    try:
        with conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
    finally:
        conn.close()

# ──────────────────────────────────────────────
# INIT DB
# ──────────────────────────────────────────────

def init_db():
    conn = get_conn()
    try:
        # Step 1: Create tables
        with conn:
            with conn.cursor() as cur:
                cur.execute("""CREATE TABLE IF NOT EXISTS settings (
                    key TEXT PRIMARY KEY, value TEXT NOT NULL)""")
                cur.execute("""CREATE TABLE IF NOT EXISTS products (
                    id          SERIAL PRIMARY KEY,
                    category    TEXT NOT NULL DEFAULT 'Parts',
                    make        TEXT NOT NULL DEFAULT 'Universal',
                    title       TEXT NOT NULL,
                    description TEXT NOT NULL DEFAULT '',
                    compat      TEXT NOT NULL DEFAULT '',
                    base_price  NUMERIC(10,2) NOT NULL DEFAULT 0,
                    badge       TEXT,
                    icon        TEXT NOT NULL DEFAULT '🔧',
                    oem_no      TEXT NOT NULL DEFAULT '',
                    active      BOOLEAN NOT NULL DEFAULT TRUE,
                    source      TEXT NOT NULL DEFAULT 'manual',
                    allegro_url TEXT NOT NULL DEFAULT '',
                    image_url   TEXT NOT NULL DEFAULT '',
                    image_local TEXT NOT NULL DEFAULT '',
                    created_at  TIMESTAMP NOT NULL DEFAULT NOW())""")
                cur.execute("""CREATE TABLE IF NOT EXISTS orders (
                    id          TEXT PRIMARY KEY,
                    seq         SERIAL,
                    date        TEXT NOT NULL,
                    first_name  TEXT NOT NULL,
                    last_name   TEXT NOT NULL,
                    email       TEXT NOT NULL,
                    phone       TEXT NOT NULL DEFAULT '',
                    address     TEXT NOT NULL DEFAULT '',
                    city        TEXT NOT NULL DEFAULT '',
                    province    TEXT NOT NULL DEFAULT '',
                    postal      TEXT NOT NULL DEFAULT '',
                    items       JSONB NOT NULL DEFAULT '[]',
                    total       NUMERIC(10,2) NOT NULL DEFAULT 0,
                    status      TEXT NOT NULL DEFAULT 'New',
                    notes       TEXT NOT NULL DEFAULT '',
                    created_at  TIMESTAMP NOT NULL DEFAULT NOW())""")
        # Step 1b: Migrate — add new columns if not exist (safe on repeat runs)
        with conn:
            with conn.cursor() as cur:
                cur.execute("ALTER TABLE products ADD COLUMN IF NOT EXISTS image_url TEXT NOT NULL DEFAULT ''")
                cur.execute("ALTER TABLE products ADD COLUMN IF NOT EXISTS image_local TEXT NOT NULL DEFAULT ''")
        # Step 2: Seed settings
        with conn:
            with conn.cursor() as cur:
                for k, v in {
                    "markup": "30", "pln_to_cad": "0.34",
                    "site_name": "NorthParts", "admin_user": "admin",
                    "admin_pass": hashlib.sha256(b"admin123").hexdigest(),
                }.items():
                    cur.execute("INSERT INTO settings(key,value) VALUES(%s,%s) ON CONFLICT(key) DO NOTHING", (k, v))
        # Step 3: Demo products
        with conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) AS c FROM products")
                if cur.fetchone()["c"] == 0:
                    for d in [
                        ("Brakes","BMW","Front Brake Pad Set - BMW 3 Series E90/E92","High-performance ceramic brake pads. Low dust, low noise.","BMW 3 Series 2005-2012",38.50,"Best Seller","🔧","34116761281"),
                        ("Engine","Toyota","Timing Belt Kit - Toyota Corolla 1.6 VVT-i","Complete timing belt kit with tensioner. OEM quality.","Toyota Corolla 2007-2014",72.00,None,"⚙️","13568-0D010"),
                        ("Filters","Volkswagen","Oil Filter + Air Filter - VW Golf Mk6 2.0 TDI","Premium filtration set. Protects engine.","VW Golf Mk6 2008-2013",24.90,"Sale","🔩","1K0-129-620"),
                        ("Suspension","Ford","Front Shock Absorber Set - Ford Focus Mk3","Gas-pressurized shock absorbers. Sold as pair.","Ford Focus 2011-2018",89.00,None,"🏎️","BM51-18K001-AB"),
                        ("Electrical","Audi","Ignition Coil Pack - Audi A4 B8 2.0 TFSI","Direct OEM replacement coil. Eliminates misfires.","Audi A4 B8 2008-2016",44.20,"New","⚡","06H905115"),
                        ("Cooling","Honda","Radiator - Honda Civic 1.8i 2006-2011","Aluminium core radiator. Direct bolt-on.","Honda Civic FD 2006-2011",115.00,None,"❄️","19010-RNA-A51"),
                    ]:
                        cur.execute("INSERT INTO products(category,make,title,description,compat,base_price,badge,icon,oem_no) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s)", d)
        # Step 4: Demo orders (items as plain JSON string cast to jsonb)
        with conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) AS c FROM orders")
                if cur.fetchone()["c"] == 0:
                    rows = [
                        ("ORD-0001","2024-03-01","John","Smith","john.smith@gmail.com","+1 416-555-0198","142 Maple Ave","Toronto","ON","M4B 1B3",'[{"title":"Brake Pads","qty":1,"price":50.05}]',50.05,"Shipped","Leave at door"),
                        ("ORD-0002","2024-03-02","Emily","Tremblay","e.tremblay@outlook.com","+1 514-555-0247","88 Rue St-Denis","Montreal","QC","H2X 3K8",'[{"title":"Timing Belt","qty":1,"price":93.60}]',93.60,"Processing",""),
                        ("ORD-0003","2024-03-03","Mike","Kowalski","mkowalski@yahoo.ca","+1 604-555-0312","55 Burrard St","Vancouver","BC","V6C 2R7",'[{"title":"Radiator","qty":1,"price":149.50}]',149.50,"New","Urgent"),
                    ]
                    for o in rows:
                        cur.execute("INSERT INTO orders(id,date,first_name,last_name,email,phone,address,city,province,postal,items,total,status,notes) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s,%s,%s)", o)
        print("✓ Database fully ready")
    finally:
        conn.close()

def get_setting(key, default=None):
    row = query("SELECT value FROM settings WHERE key=%s", (key,), fetch="one")
    return row["value"] if row else default

def get_settings():
    return {r["key"]: r["value"] for r in query("SELECT key,value FROM settings")}

def calc_final(base_price, markup):
    return round(float(base_price) * (1 + float(markup) / 100), 2)

def next_order_id():
    row = query("SELECT id FROM orders ORDER BY seq DESC LIMIT 1", fetch="one")
    if not row:
        return "ORD-0001"
    return f"ORD-{int(row['id'].split('-')[1]) + 1:04d}"

def sbadge(status):
    return {"New":"red","Processing":"yellow","Shipped":"blue","Delivered":"green","Cancelled":"gray"}.get(status,"gray")

app.jinja_env.globals["sbadge"] = sbadge

# ──────────────────────────────────────────────
# AUTH
# ──────────────────────────────────────────────

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("admin_logged_in"):
            # Return JSON for API routes, redirect for page routes
            if request.path.startswith("/api/") or request.is_json:
                return jsonify({"error": "Not logged in"}), 401
            return redirect(url_for("admin_login"))
        return f(*args, **kwargs)
    return decorated

# ──────────────────────────────────────────────
# STORE
# ──────────────────────────────────────────────

@app.route("/")
def index():
    settings = get_settings()
    markup = float(settings.get("markup", 30))
    products = query("SELECT * FROM products WHERE active=TRUE ORDER BY id")
    for p in products:
        p["finalPrice"] = calc_final(p["base_price"], markup)
        p["basePrice"]  = float(p["base_price"])
    return render_template("store.html", products=products, settings=settings)

# ──────────────────────────────────────────────
# ADMIN PAGES
# ──────────────────────────────────────────────

@app.route("/admin/login", methods=["GET","POST"])
def admin_login():
    error = None
    if request.method == "POST":
        u = request.form.get("username","")
        p = request.form.get("password","")
        if u == get_setting("admin_user","admin") and \
           hashlib.sha256(p.encode()).hexdigest() == get_setting("admin_pass"):
            session["admin_logged_in"] = True
            return redirect(url_for("admin_dashboard"))
        error = "Invalid username or password"
    return render_template("admin_login.html", error=error)

@app.route("/admin/logout")
def admin_logout():
    session.clear()
    return redirect(url_for("admin_login"))

@app.route("/admin")
@app.route("/admin/")
@login_required
def admin_dashboard():
    settings = get_settings()
    new_orders_count = query("SELECT COUNT(*) AS c FROM orders WHERE status='New'", fetch="one")["c"]
    stats = {
        "total_products": query("SELECT COUNT(*) AS c FROM products WHERE active=TRUE", fetch="one")["c"],
        "total_orders":   query("SELECT COUNT(*) AS c FROM orders", fetch="one")["c"],
        "new_orders":     new_orders_count,
        "markup":         settings.get("markup","30"),
        "revenue":        float(query("SELECT COALESCE(SUM(total),0) AS s FROM orders", fetch="one")["s"]),
    }
    recent = query("SELECT * FROM orders ORDER BY created_at DESC LIMIT 5")
    return render_template("admin_dashboard.html", stats=stats, recent_orders=recent,
                           settings=settings, new_orders_count=new_orders_count)

@app.route("/admin/products")
@login_required
def admin_products():
    settings = get_settings()
    markup = float(settings.get("markup", 30))
    products = query("SELECT * FROM products ORDER BY id")
    for p in products:
        p["finalPrice"] = calc_final(p["base_price"], markup)
        p["basePrice"]  = float(p["base_price"])
    new_count = query("SELECT COUNT(*) AS c FROM orders WHERE status='New'", fetch="one")["c"]
    return render_template("admin_products.html", products=products,
                           settings=settings, new_orders_count=new_count)

@app.route("/admin/orders")
@login_required
def admin_orders():
    settings = get_settings()
    sf = request.args.get("status","")
    new_count = query("SELECT COUNT(*) AS c FROM orders WHERE status='New'", fetch="one")["c"]
    orders = query("SELECT * FROM orders WHERE status=%s ORDER BY created_at DESC", (sf,)) if sf \
             else query("SELECT * FROM orders ORDER BY created_at DESC")
    for o in orders:
        if isinstance(o["items"], str):
            o["items"] = json.loads(o["items"])
    return render_template("admin_orders.html", orders=orders, status_filter=sf,
                           settings=settings, new_orders_count=new_count)

@app.route("/admin/settings")
@login_required
def admin_settings():
    settings = get_settings()
    new_count = query("SELECT COUNT(*) AS c FROM orders WHERE status='New'", fetch="one")["c"]
    return render_template("admin_settings.html", settings=settings, new_orders_count=new_count)

# ──────────────────────────────────────────────
# API — PRODUCTS
# ──────────────────────────────────────────────

@app.route("/api/products", methods=["GET"])
def api_products():
    markup = float(get_setting("markup", 30))
    products = query("SELECT * FROM products WHERE active=TRUE ORDER BY id")
    for p in products:
        p["finalPrice"] = calc_final(p["base_price"], markup)
        p["basePrice"] = float(p["base_price"])
        p["base_price"] = float(p["base_price"])
    return jsonify({"products": products, "markup": markup})

@app.route("/api/products", methods=["POST"])
@login_required
def api_add_product():
    d = request.json
    row = query("""INSERT INTO products(category,make,title,description,compat,base_price,badge,icon,oem_no,active)
                   VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) RETURNING *""",
                (d.get("category","Parts"), d.get("make","Universal"), d.get("title",""),
                 d.get("description",""), d.get("compat",""), float(d.get("basePrice",0)),
                 d.get("badge") or None, d.get("icon","🔧"), d.get("oemNo",""),
                 d.get("active",True)), fetch="one")
    return jsonify({"success": True, "product": row}), 201

@app.route("/api/products/<int:pid>", methods=["PUT"])
@login_required
def api_update_product(pid):
    d = request.json
    execute("""UPDATE products SET category=%s,make=%s,title=%s,description=%s,compat=%s,
               base_price=%s,badge=%s,icon=%s,oem_no=%s,active=%s WHERE id=%s""",
            (d.get("category"), d.get("make"), d.get("title"), d.get("description"),
             d.get("compat"), float(d.get("basePrice",0)), d.get("badge") or None,
             d.get("icon","🔧"), d.get("oemNo",""), d.get("active",True), pid))
    return jsonify({"success": True})

@app.route("/api/products/<int:pid>", methods=["DELETE"])
@login_required
def api_delete_product(pid):
    execute("DELETE FROM products WHERE id=%s", (pid,))
    return jsonify({"success": True})

# ──────────────────────────────────────────────
# API — ORDERS
# ──────────────────────────────────────────────

@app.route("/api/orders", methods=["POST"])
def api_create_order():
    d = request.json
    oid = next_order_id()
    execute("""INSERT INTO orders(id,date,first_name,last_name,email,phone,address,city,province,postal,items,total,notes)
               VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (oid, datetime.now().strftime("%Y-%m-%d"),
             d.get("firstName",""), d.get("lastName",""), d.get("email",""),
             d.get("phone",""), d.get("address",""), d.get("city",""),
             d.get("province",""), d.get("postal",""),
             Json(d.get("items",[])), float(d.get("total",0)), d.get("notes","")))
    return jsonify({"success": True, "order_id": oid}), 201

@app.route("/api/orders/<order_id>/status", methods=["PUT"])
@login_required
def api_update_order_status(order_id):
    execute("UPDATE orders SET status=%s WHERE id=%s", (request.json.get("status"), order_id))
    return jsonify({"success": True})

@app.route("/api/orders/<order_id>", methods=["GET"])
@login_required
def api_get_order(order_id):
    o = query("SELECT * FROM orders WHERE id=%s", (order_id,), fetch="one")
    if not o:
        return jsonify({"error":"Not found"}), 404
    if isinstance(o["items"], str):
        o["items"] = json.loads(o["items"])
    return jsonify(o)

# ──────────────────────────────────────────────
# API — SETTINGS
# ──────────────────────────────────────────────

@app.route("/api/settings", methods=["PUT"])
@login_required
def api_update_settings():
    for k in ["markup","pln_to_cad","site_name"]:
        if k in request.json:
            execute("UPDATE settings SET value=%s WHERE key=%s", (str(request.json[k]), k))
    return jsonify({"success": True})

# ──────────────────────────────────────────────
# API — PARSER
# ──────────────────────────────────────────────

@app.route("/api/parser/run", methods=["POST"])
@login_required
def api_run_parser():
    import subprocess, sys
    d = request.json or {}
    markup = d.get("markup", get_setting("markup", 30))
    cmd = [sys.executable, "../allegro_parser.py",
           "--mode", d.get("mode","demo"),
           "--query", d.get("query","części samochodowe"),
           "--pages", str(d.get("pages",1)),
           "--markup", str(markup), "--no-images"]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        from pathlib import Path
        pf = Path("../northparts_data/products.json")
        if pf.exists():
            parsed = json.loads(pf.read_text(encoding="utf-8"))
            added = 0
            for p in parsed.get("products",[]):
                execute("""INSERT INTO products(category,make,title,description,compat,base_price,icon,oem_no,source,allegro_url)
                           VALUES(%s,%s,%s,%s,%s,%s,'🔧',%s,'allegro',%s)""",
                        (p.get("category","Parts"), p.get("make","Universal"),
                         p.get("title") or p.get("title_pl",""),
                         p.get("description") or p.get("description_pl",""),
                         p.get("compat",""), float(p.get("price_cad_base",0)),
                         p.get("oem",p.get("id","")), p.get("allegro_url","")))
                added += 1
            return jsonify({"success":True,"imported":added,"log":result.stdout[-1000:]})
        return jsonify({"success":False,"error":"Parser output not found","log":result.stderr[-500:]})
    except Exception as e:
        return jsonify({"success":False,"error":str(e)})

# ──────────────────────────────────────────────
# ALLEGRO API INTEGRATION (Authorization Code Flow)
# ──────────────────────────────────────────────

ALLEGRO_CLIENT_ID     = os.environ.get("ALLEGRO_CLIENT_ID", "")
ALLEGRO_CLIENT_SECRET = os.environ.get("ALLEGRO_CLIENT_SECRET", "")
ALLEGRO_API           = "https://api.allegro.pl"
ALLEGRO_AUTH          = "https://allegro.pl"
ALLEGRO_REDIRECT      = "https://northparts.onrender.com/allegro/callback"

SEARCH_QUERIES_BMW_AUDI = []  # unused, kept for compatibility

def allegro_headers(token):
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.allegro.public.v1+json",
    }

def allegro_save_token(data):
    import time
    expires_at = time.time() + data.get("expires_in", 43200)
    for k, v in [("access_token", data.get("access_token","")),
                 ("refresh_token", data.get("refresh_token","")),
                 ("expires_at", str(expires_at))]:
        execute("INSERT INTO settings(key,value) VALUES(%s,%s) ON CONFLICT(key) DO UPDATE SET value=%s",
                (f"allegro_{k}", v, v))

def allegro_refresh():
    refresh_tok = get_setting("allegro_refresh_token")
    if not refresh_tok:
        return None
    resp = requests.post(f"{ALLEGRO_AUTH}/auth/oauth/token",
                         auth=(ALLEGRO_CLIENT_ID, ALLEGRO_CLIENT_SECRET),
                         data={"grant_type":"refresh_token","refresh_token":refresh_tok})
    if resp.status_code == 200:
        allegro_save_token(resp.json())
        return resp.json()["access_token"]
    return None

def allegro_valid_token():
    import time
    expires_at = float(get_setting("allegro_expires_at", 0) or 0)
    if expires_at > time.time() + 120:
        return get_setting("allegro_access_token")
    return allegro_refresh()

@app.route("/allegro/connect")
@login_required
def allegro_connect():
    """Redirect admin to Allegro authorization page."""
    import urllib.parse, secrets
    state = secrets.token_hex(16)
    execute("INSERT INTO settings(key,value) VALUES('allegro_oauth_state',%s) ON CONFLICT(key) DO UPDATE SET value=%s",
            (state, state))
    params = urllib.parse.urlencode({
        "response_type": "code",
        "client_id":     ALLEGRO_CLIENT_ID,
        "redirect_uri":  ALLEGRO_REDIRECT,
        "state":         state,
    })
    return redirect(f"{ALLEGRO_AUTH}/auth/oauth/authorize?{params}")

@app.route("/allegro/callback")
def allegro_callback():
    """Handle OAuth2 callback from Allegro."""
    code  = request.args.get("code")
    error = request.args.get("error")

    if error:
        return f"Allegro authorization error: {error}", 400
    if not code:
        return "Error: no code returned by Allegro", 400

    try:
        resp = requests.post(
            f"{ALLEGRO_AUTH}/auth/oauth/token",
            auth=(ALLEGRO_CLIENT_ID, ALLEGRO_CLIENT_SECRET),
            data={"grant_type": "authorization_code",
                  "code": code,
                  "redirect_uri": ALLEGRO_REDIRECT},
            timeout=15,
        )
        if resp.status_code != 200:
            return f"Allegro token error {resp.status_code}: {resp.text}", 400

        token_data = resp.json()
        if "access_token" not in token_data:
            return f"No access_token in response: {token_data}", 400

        allegro_save_token(token_data)
        return redirect("/admin/settings?allegro=connected")

    except Exception as e:
        import traceback
        return f"Exception in callback: {e}\n\n{traceback.format_exc()}", 500

@app.route("/allegro/status")
@login_required
def allegro_status():
    import time
    token = get_setting("allegro_access_token")
    expires_at = float(get_setting("allegro_expires_at", 0) or 0)
    connected = bool(token) and expires_at > time.time()
    return jsonify({"connected": connected, "expires_at": expires_at})

@app.route("/api/allegro/import", methods=["POST"])
@login_required
def api_allegro_import():
    """Scrape Allegro search results for BMW and Audi auto parts."""
    try:
        return _do_allegro_import()
    except Exception as e:
        import traceback
        return jsonify({"success": False, "error": str(e), "trace": traceback.format_exc()[-500:]}), 500

def _do_allegro_import():
    import re, urllib.parse, time

    d       = request.json or {}
    pln_cad = float(get_setting("pln_to_cad", 0.34))
    limit   = int(d.get("limit", 10))
    added   = 0
    skipped = 0
    errors  = []

    searches = [
        ("klocki hamulcowe BMW",   "Brakes",     "BMW"),
        ("tarcze hamulcowe Audi",  "Brakes",     "Audi"),
        ("filtr oleju BMW",        "Filters",    "BMW"),
        ("amortyzator BMW",        "Suspension", "BMW"),
        ("zawieszenie Audi",       "Suspension", "Audi"),
        ("rozrzad BMW",            "Engine",     "BMW"),
        ("filtr powietrza Audi",   "Filters",    "Audi"),
        ("cewka zaplonowa BMW",    "Electrical", "BMW"),
    ]

    req_headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36",
        "Accept-Language": "pl-PL,pl;q=0.9",
        "Accept": "text/html,application/xhtml+xml",
    }

    for phrase, category, make in searches:
        try:
            encoded = urllib.parse.quote(phrase)
            url = f"https://allegro.pl/listing?string={encoded}&order=d"
            resp = requests.get(url, headers=req_headers, timeout=25, allow_redirects=True)

            if resp.status_code != 200:
                errors.append(f"{phrase}: HTTP {resp.status_code}")
                continue

            html = resp.text
            offers_found = []

            # JSON-LD structured data
            for ld_match in re.finditer(r'<script type="application/ld\+json">(.*?)</script>', html, re.DOTALL):
                try:
                    ld = json.loads(ld_match.group(1))
                    if ld.get("@type") == "ItemList":
                        for elem in (ld.get("itemListElement") or [])[:limit]:
                            item = elem.get("item", {})
                            raw_url = item.get("url","") or item.get("@id","")
                            oid_m = re.search(r'-(\d{8,})$', raw_url)
                            if not oid_m:
                                continue
                            oid      = oid_m.group(1)
                            title_pl = item.get("name","")
                            price_pln = float((item.get("offers") or {}).get("price", 0) or 0)
                            img = item.get("image","")
                            image_url = img if isinstance(img, str) else (img[0] if isinstance(img, list) and img else "")
                            offers_found.append((oid, title_pl, price_pln, image_url))
                except Exception:
                    pass

            # Fallback: regex href
            if not offers_found:
                seen = set()
                for slug, oid in re.findall(r'href="https://allegro\.pl/oferta/([\w-]+-(?P<id>\d{8,}))"', html):
                    if oid in seen: continue
                    seen.add(oid)
                    offers_found.append((oid, phrase, 0.0, ""))
                    if len(offers_found) >= limit:
                        break

            for oid, title_pl, price_pln, image_url in offers_found[:limit]:
                if not oid or not title_pl:
                    continue
                if query("SELECT id FROM products WHERE oem_no=%s AND source='allegro'", (oid,), fetch="one"):
                    skipped += 1
                    continue

                title_en = title_pl
                try:
                    from deep_translator import GoogleTranslator
                    title_en = GoogleTranslator(source="pl", target="en").translate(title_pl[:300])
                except Exception:
                    pass

                base_cad = round(price_pln * pln_cad, 2)
                execute(
                    """INSERT INTO products
                       (category,make,title,description,compat,base_price,icon,oem_no,
                        active,source,allegro_url,image_url,image_local)
                       VALUES(%s,%s,%s,%s,%s,%s,'🔧',%s,TRUE,'allegro',%s,%s,'')""",
                    (category, make, title_en, "", "", base_cad, oid,
                     f"https://allegro.pl/oferta/{oid}", image_url)
                )
                added += 1

            time.sleep(0.5)

        except Exception as exc:
            errors.append(f"{phrase}: {exc}")

    return jsonify({"success": True, "added": added, "skipped": skipped, "errors": errors})

# ──────────────────────────────────────────────
# STARTUP
# ──────────────────────────────────────────────

@app.route("/init-db")
def route_init_db():
    """Safety route to initialize DB manually if auto-init failed."""
    try:
        init_db()
        return "✓ Database initialized successfully!", 200
    except Exception as e:
        return f"✗ Error: {e}", 500

# Run init_db every time gunicorn starts a worker
try:
    init_db()
except Exception as e:
    print(f"⚠ DB init skipped at module load: {e}")

if __name__ == "__main__":
    print("🚀 http://localhost:5000  |  Admin: /admin  (admin/admin123)")
    app.run(debug=True, port=5000)
