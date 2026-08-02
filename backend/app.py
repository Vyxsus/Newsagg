from flask import Flask, render_template, jsonify, request, send_file, Response
from flask import session, redirect, url_for
from functools import wraps
import bcrypt
import threading, os, uuid, json, csv, io
from datetime import datetime
from sqlalchemy import func

from .db.connection import init_db, check_connection, get_db
from .db.models import Portal, Article, CrawlLog, User, Interaction, CFRecommendation, AdminUser
from .cf.cf_engine import get_cf_model, load_cf_params, save_cf_params, DEFAULT_PARAMS
from .scheduler.cf_scheduler import CFScheduler, load_cf_config as cf_sched_load, save_cf_config as cf_sched_save
from .crawler.engine import HybridCrawlerEngine, get_portal_configs, KATEGORI_MAPPING
from .indexer.indexer import ArticleIndexer
from .scheduler.scheduler import NewsScheduler, load_config as sched_load, save_config as sched_save

app = Flask(__name__, template_folder="../frontend", static_folder="../frontend/static")
app.secret_key = os.environ.get("SECRET_KEY", "newsagg-secret-key-ganti-ini-2026")
app.config["SESSION_COOKIE_HTTPONLY"] = True
app.config["SESSION_COOKIE_SAMESITE"] = "Lax"


# ────────────────────────────────────────────────────────────────
# LOGIN REQUIRED DECORATOR
# ────────────────────────────────────────────────────────────────
ADMIN_LOGIN_URL = os.environ.get("ADMIN_LOGIN_URL", "/panel-admin")


def login_required(f):
    """Decorator — redirect ke halaman user (bukan ke login) jika belum autentikasi.
    URL login admin disimpan di ADMIN_LOGIN_URL agar tidak mudah ditebak pengunjung biasa.
    """
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get("admin_logged_in"):
            return redirect("/user")
        return f(*args, **kwargs)
    return decorated


# ────────────────────────────────────────────────────────────────
# AUTHENTICATION ENDPOINTS
# ────────────────────────────────────────────────────────────────
@app.route("/panel-admin", methods=["GET"])
def login_page():
    if session.get("admin_logged_in"):
        return redirect("/")
    return render_template("login.html")


@app.route("/panel-admin", methods=["POST"])
def login_post():
    data     = request.json or {}
    username = data.get("username", "").strip()
    password = data.get("password", "").strip()

    if not username or not password:
        return jsonify({"error": "Username dan password wajib diisi"}), 400

    with get_db() as db:
        user = db.query(AdminUser).filter_by(username=username).first()
        if not user:
            return jsonify({"error": "Username atau password salah"}), 401
        if not bcrypt.checkpw(password.encode(), user.password.encode()):
            return jsonify({"error": "Username atau password salah"}), 401

    session["admin_logged_in"] = True
    session["admin_username"]  = username
    return jsonify({"message": "Login berhasil"})


@app.route("/login", methods=["GET", "POST"])
def login_old():
    """URL lama — redirect ke halaman user agar tidak mengekspos keberadaan admin."""
    return redirect("/user")


@app.route("/logout", methods=["POST"])
def logout():
    session.clear()
    return jsonify({"message": "Logout berhasil"})


@app.errorhandler(404)
def not_found(e):
    """Semua URL tidak dikenal diarahkan ke halaman user, bukan error page."""
    return redirect("/user")


@app.route("/api/auth/change-password", methods=["POST"])
@login_required
def change_password():
    data         = request.json or {}
    old_password = data.get("old_password", "").strip()
    new_password = data.get("new_password", "").strip()

    if not old_password or not new_password:
        return jsonify({"error": "Semua field wajib diisi"}), 400
    if len(new_password) < 6:
        return jsonify({"error": "Password baru minimal 6 karakter"}), 400

    username = session.get("admin_username")
    with get_db() as db:
        user = db.query(AdminUser).filter_by(username=username).first()
        if not user:
            return jsonify({"error": "User tidak ditemukan"}), 404
        if not bcrypt.checkpw(old_password.encode(), user.password.encode()):
            return jsonify({"error": "Password lama salah"}), 401
        user.password = bcrypt.hashpw(new_password.encode(), bcrypt.gensalt()).decode()

    return jsonify({"message": "Password berhasil diubah"})


@app.route("/api/auth/me", methods=["GET"])
def auth_me():
    if session.get("admin_logged_in"):
        return jsonify({
            "logged_in": True,
            "username": session.get("admin_username")
        })
    return jsonify({"logged_in": False})


# ────────────────────────────────────────────────────────────────
# ERROR HANDLERS
# ────────────────────────────────────────────────────────────────
@app.errorhandler(Exception)
def handle_exception(e):
    import traceback
    tb = traceback.format_exc()
    print(f"[ERROR] {e}\n{tb}")
    return jsonify({"error": str(e), "type": type(e).__name__}), 500


@app.errorhandler(404)
def not_found(e):
    return jsonify({"error": "Endpoint tidak ditemukan"}), 404


# ────────────────────────────────────────────────────────────────
# GLOBAL CRAWL STATE
# ────────────────────────────────────────────────────────────────
crawl_status = {
    "running": False, "progress": [], "total": 0, "done": 0,
    "current_article": "", "current_source": "", "article_progress": [0, 0],
    "triggered_by": "manual",
}
status_lock = threading.Lock()
engine = HybridCrawlerEngine()
indexer = ArticleIndexer()
scheduler = NewsScheduler(crawl_status, status_lock)
cf_status      = {"running": False}
cf_status_lock = threading.Lock()
cf_scheduler   = CFScheduler(cf_status, cf_status_lock)

# ────────────────────────────────────────────────────────────────
# ADMIN PAGE (PROTECTED)
# ────────────────────────────────────────────────────────────────
@app.route("/")
@login_required
def index():
    return render_template("index.html")


@app.route("/user")
def user_page():
    return render_template("user.html")


# ────────────────────────────────────────────────────────────────
# PUBLIC API UNTUK FOOTER
# ────────────────────────────────────────────────────────────────
@app.route("/api/portals", methods=["GET"])
def public_portals():
    """Daftar nama portal saja — untuk footer halaman user."""
    cfgs = get_portal_configs()
    return jsonify([{"name": c["name"], "domain": c["domain"]} for c in cfgs])


# ────────────────────────────────────────────────────────────────
# PORTAL ENDPOINTS (PUBLIC, TAPI DATA PORTAL DARI CONFIG)
# ────────────────────────────────────────────────────────────────
@app.route("/admin/portals", methods=["GET"])
def list_portals():
    cfgs = get_portal_configs()
    inactive_domains = set()
    try:
        with get_db() as db:
            inactive_domains = {p.domain for p in db.query(Portal).filter_by(active=False).all()}
    except Exception:
        pass

    result = []
    for i, cfg in enumerate(cfgs):
        domain = cfg["domain"]
        result.append({
            "id": i + 1,
            "name": cfg["name"],
            "domain": domain,
            "base_url": cfg["html_urls"][0] if cfg.get("html_urls") else cfg["rss_urls"][0],
            "method": cfg["method"],
            "rss_count": len(cfg.get("rss_urls", [])),
            "html_count": len(cfg.get("html_urls", [])),
            "active": domain not in inactive_domains,
            "crawl_policy": "allowed",
        })
    return jsonify(result)


@app.route("/admin/portals/<domain>/toggle", methods=["POST"])
def toggle_portal(domain):
    data = request.json or {}
    active = data.get("active", True)
    with get_db() as db:
        p = db.query(Portal).filter_by(domain=domain).first()
        if not p:
            cfg = next((c for c in get_portal_configs() if c["domain"] == domain), None)
            if not cfg:
                return jsonify({"error": "Portal tidak ditemukan"}), 404
            p = Portal(
                name=cfg["name"],
                base_url=cfg["html_urls"][0] if cfg.get("html_urls") else cfg["rss_urls"][0],
                domain=domain,
                crawl_policy="allowed",
                active=active,
                urls=json.dumps(cfg.get("html_urls", [])),
                selectors=json.dumps(cfg.get("selectors", {}))
            )
            db.add(p)
        else:
            p.active = active
    return jsonify({"message": "Diperbarui", "domain": domain, "active": active})


# ────────────────────────────────────────────────────────────────
# ARTICLES API
# ────────────────────────────────────────────────────────────────
@app.route("/api/articles", methods=["GET"])
def get_articles():
    page = int(request.args.get("page", 1))
    per_page = min(int(request.args.get("per_page", 25)), 100)
    source = request.args.get("source", "")
    kategori = request.args.get("kategori", "")
    q = request.args.get("q", "")
    with get_db() as db:
        query = db.query(Article).order_by(Article.parsed_at.desc())
        if source:
            query = query.filter(Article.source == source)
        if kategori and kategori != "Semua":
            query = query.filter(Article.category == kategori)
        if q:
            query = query.filter(Article.title.ilike(f"%{q}%"))
        user_only = request.args.get("user_only", "")
        if user_only == "1":
            query = query.filter(Article.body.isnot(None), Article.body != "")
        total = query.count()
        articles = query.offset((page - 1) * per_page).limit(per_page).all()
        return jsonify({
            "total": total,
            "page": page,
            "pages": (total + per_page - 1) // per_page,
            "data": [{
                "id": a.id,
                "title": a.title,
                "excerpt": a.excerpt,
                "body": a.body,
                "source": a.source,
                "url": a.url,
                "category": a.category,
                "published_at": a.published_at,
                "parsed_at": str(a.parsed_at),
                "url_hash": a.url_hash,
                "content_hash": a.content_hash,
                "image_url": a.image_url or ""
            } for a in articles]
        })


@app.route("/api/articles/<int:aid>", methods=["GET"])
def get_article(aid):
    with get_db() as db:
        a = db.query(Article).get(aid)
        if not a:
            return jsonify({"error": "Tidak ditemukan"}), 404
        return jsonify({
            "id": a.id,
            "title": a.title,
            "excerpt": a.excerpt,
            "body": a.body,
            "source": a.source,
            "url": a.url,
            "category": a.category,
            "published_at": a.published_at,
            "parsed_at": str(a.parsed_at),
            "url_hash": a.url_hash,
            "content_hash": a.content_hash,
            "simhash": a.simhash,
            "canonical_url": a.canonical_url,
            "image_url": a.image_url or ""
        })


@app.route("/api/stats", methods=["GET"])
def get_stats():
    with get_db() as db:
        total_articles = db.query(Article).count()
        total_portals = len(get_portal_configs())
        total_users = db.query(User).count()
        total_clicks = db.query(Interaction).count()
        per_source = db.query(Article.source, func.count(Article.id)).group_by(Article.source).all()
        per_kat = db.query(Article.category, func.count(Article.id)).group_by(Article.category).all()
        logs = db.query(CrawlLog).order_by(CrawlLog.started_at.desc()).limit(10).all()
        return jsonify({
            "total_articles": total_articles,
            "total_portals": total_portals,
            "total_users": total_users,
            "total_clicks": total_clicks,
            "per_source": [{"source": s, "count": c} for s, c in per_source],
            "per_kategori": [{"kategori": k or "Umum", "count": c} for k, c in per_kat],
            "crawl_logs": [{
                "run_id": l.run_id,
                "portal_name": l.portal_name,
                "started_at": str(l.started_at),
                "articles_new": l.articles_new,
                "articles_dup": l.articles_dup,
                "articles_error": l.articles_error,
                "status": l.status
            } for l in logs],
        })


# ────────────────────────────────────────────────────────────────
# CRAWLING
# ────────────────────────────────────────────────────────────────
@app.route("/api/crawl", methods=["POST"])
def start_crawl():
    global crawl_status
    if crawl_status["running"]:
        return jsonify({"error": "Crawling sedang berjalan"}), 400

    data = request.json or {}
    selected = data.get("portal_names", [])
    kategori = data.get("kategori", "Semua")
    all_cfgs = get_portal_configs()

    with get_db() as db:
        inactive = {p.domain for p in db.query(Portal).filter_by(active=False).all()}

    portal_cfgs = [
        c for c in all_cfgs
        if c["domain"] not in inactive and (not selected or c["name"] in selected)
    ]

    if not portal_cfgs:
        return jsonify({"error": "Tidak ada portal aktif"}), 400

    with status_lock:
        crawl_status.update({
            "running": True,
            "progress": [],
            "total": len(portal_cfgs),
            "done": 0,
            "current_article": "",
            "current_source": "",
            "article_progress": [0, 0],
            "triggered_by": "manual",
        })

    def run():
        global crawl_status
        for cfg in portal_cfgs:
            crawl_status["current_source"] = cfg["name"]
            crawl_status["progress"].append({
                "source": cfg["name"],
                "name": cfg["name"],
                "status": "crawling",
                "count": 0
            })
            run_id = str(uuid.uuid4())[:8]

            def on_progress(idx, total, title):
                crawl_status["article_progress"] = [idx, total]
                crawl_status["current_article"] = title[:60]

            articles = engine.crawl_portal(cfg, kategori_filter=kategori, progress_cb=on_progress)
            result = indexer.bulk_index(articles, run_id=run_id)
            new_count = result["stats"].get("new", 0)

            for p in crawl_status["progress"]:
                if p["source"] == cfg["name"]:
                    p["status"] = "done"
                    p["count"] = new_count
            crawl_status["done"] += 1

            with get_db() as db:
                db.add(CrawlLog(
                    run_id=run_id,
                    portal_name=cfg["name"],
                    finished_at=datetime.utcnow(),
                    articles_found=len(articles),
                    articles_new=new_count,
                    articles_dup=result["stats"].get("duplicate", 0) + result["stats"].get("near_dup", 0),
                    articles_error=result["stats"].get("error", 0),
                    status="done",
                ))

        crawl_status.update({"running": False, "current_article": "", "article_progress": [0, 0]})
        # Auto-rebuild CF model jika diaktifkan
        cf_cfg = cf_sched_load()
        if cf_cfg.get("auto_rebuild_after_crawl", True):
            cf_scheduler.trigger_rebuild_now(reason="auto-after-crawl")

    threading.Thread(target=run).start()
    return jsonify({"message": "Crawling dimulai", "portals": len(portal_cfgs)})


@app.route("/api/crawl/status", methods=["GET"])
def crawl_status_ep():
    return jsonify(crawl_status)


# ────────────────────────────────────────────────────────────────
# SCHEDULER
# ────────────────────────────────────────────────────────────────
@app.route("/api/scheduler", methods=["GET"])
def get_scheduler():
    return jsonify(scheduler.get_status())


@app.route("/api/scheduler", methods=["POST"])
def update_scheduler():
    data = request.json or {}
    if "interval_hours" in data:
        try:
            iv = int(data["interval_hours"])
            if not (1 <= iv <= 168):
                return jsonify({"error": "Interval 1-168 jam"}), 400
            data["interval_hours"] = iv
        except:
            return jsonify({"error": "Interval tidak valid"}), 400
    return jsonify({"message": "Tersimpan", "config": scheduler.update_config(**data)})


@app.route("/api/scheduler/run-now", methods=["POST"])
def run_now():
    if crawl_status["running"]:
        return jsonify({"error": "Crawling sedang berjalan"}), 400
    cfg = sched_load()
    cfg["next_run"] = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    sched_save(cfg)
    if not scheduler._thread or not scheduler._thread.is_alive():
        scheduler.start()
    return jsonify({"message": "Crawling dijadwalkan segera"})


# ────────────────────────────────────────────────────────────────
# INTERACTIONS
# ────────────────────────────────────────────────────────────────
@app.route("/api/interactions", methods=["POST"])
def log_interaction():
    data = request.json or {}
    pseudonym = data.get("pseudonym", "").strip()
    article_id = data.get("article_id")
    event_type = data.get("event_type", "click")
    if not pseudonym or not article_id:
        return jsonify({"error": "pseudonym dan article_id wajib"}), 400
    if event_type not in ("click", "read", "share", "rec_click"):
        event_type = "click"
    is_new_user = False
    with get_db() as db:
        user = db.query(User).filter_by(pseudonym=pseudonym).first()
        if not user:
            user = User(pseudonym=pseudonym)
            db.add(user)
            db.flush()
            is_new_user = True
        if not db.query(Article).get(article_id):
            return jsonify({"error": "Artikel tidak ditemukan"}), 404
        db.add(Interaction(user_id=user.id, article_id=article_id, event_type=event_type))

    # Jika user baru, rebuild CF di background agar rekomendasi segera tersedia
    if is_new_user and not cf_status.get("running"):
        cf_scheduler.trigger_rebuild_now(reason="new-user")

    return jsonify({"message": "Interaksi dicatat", "new_user": is_new_user}), 201


@app.route("/api/interactions", methods=["GET"])
def get_interactions():
    page = int(request.args.get("page", 1))
    per_page = min(int(request.args.get("per_page", 50)), 200)
    pseudonym = request.args.get("pseudonym", "")
    event_type = request.args.get("event_type", "")
    with get_db() as db:
        q = db.query(Interaction, User, Article)\
               .join(User, Interaction.user_id == User.id)\
               .join(Article, Interaction.article_id == Article.id)\
               .order_by(Interaction.created_at.desc())
        if pseudonym:
            q = q.filter(User.pseudonym.ilike(f"%{pseudonym}%"))
        if event_type:
            q = q.filter(Interaction.event_type == event_type)
        total = q.count()
        rows = q.offset((page - 1) * per_page).limit(per_page).all()
        return jsonify({
            "total": total,
            "page": page,
            "pages": (total + per_page - 1) // per_page,
            "data": [{
                "id": i.id,
                "pseudonym": u.pseudonym,
                "user_id": u.id,
                "article_id": a.id,
                "article_title": a.title,
                "article_source": a.source,
                "article_category": a.category or "Umum",
                "event_type": i.event_type,
                "created_at": str(i.created_at)
            } for i, u, a in rows]
        })


@app.route("/api/interactions/summary", methods=["GET"])
def get_interactions_summary():
    with get_db() as db:
        total_i = db.query(Interaction).count()
        total_u = db.query(User).count()
        top_arts = db.query(
            Article.id, Article.title, Article.source, Article.category,
            func.count(Interaction.id).label("click_count")
        ).join(Interaction, Article.id == Interaction.article_id)\
         .group_by(Article.id, Article.title, Article.source, Article.category)\
         .order_by(func.count(Interaction.id).desc()).limit(10).all()
        per_kat = db.query(
            Article.category, func.count(Interaction.id).label("count")
        ).join(Interaction, Article.id == Interaction.article_id)\
         .group_by(Article.category).order_by(func.count(Interaction.id).desc()).all()
        per_evt = db.query(
            Interaction.event_type, func.count(Interaction.id).label("count")
        ).group_by(Interaction.event_type).all()
        top_u = db.query(
            User.pseudonym, func.count(Interaction.id).label("count")
        ).join(Interaction, User.id == Interaction.user_id)\
         .group_by(User.pseudonym)\
         .order_by(func.count(Interaction.id).desc()).limit(10).all()
        return jsonify({
            "total_interactions": total_i,
            "total_users": total_u,
            "top_articles": [{
                "id": r.id,
                "title": r.title,
                "source": r.source,
                "category": r.category,
                "click_count": r.click_count
            } for r in top_arts],
            "per_kategori": [{"kategori": r.category or "Umum", "count": r.count} for r in per_kat],
            "per_event": [{"event": r.event_type, "count": r.count} for r in per_evt],
            "top_users": [{"pseudonym": r.pseudonym, "count": r.count} for r in top_u],
        })


@app.route("/api/interactions/export", methods=["GET"])
def export_interactions():
    with get_db() as db:
        rows = db.query(Interaction, User, Article)\
                 .join(User, Interaction.user_id == User.id)\
                 .join(Article, Interaction.article_id == Article.id)\
                 .order_by(Interaction.created_at.desc()).all()
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["interaction_id", "user_id", "pseudonym", "article_id", "article_title",
                "article_source", "article_category", "event_type", "created_at"])
    for i, u, a in rows:
        w.writerow([i.id, u.id, u.pseudonym, a.id, a.title, a.source,
                    a.category or "Umum", i.event_type, str(i.created_at)])
    out.seek(0)
    return Response(out.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment;filename=interactions_log.csv"})


# ────────────────────────────────────────────────────────────────
# COLLABORATIVE FILTERING
# ────────────────────────────────────────────────────────────────
@app.route("/api/cf/build", methods=["POST"])
def cf_build():
    cf = get_cf_model()
    result = cf.build_from_db()
    if result.get("status") == "ok":
        saved = cf.save_to_db(top_n=10)
        result["recommendations_saved"] = saved
    return jsonify(result)


@app.route("/api/cf/recommend/<pseudonym>", methods=["GET"])
def cf_recommend(pseudonym):
    top_n = int(request.args.get("top_n", 10))
    with get_db() as db:
        user = db.query(User).filter_by(pseudonym=pseudonym).first()
        if not user:
            return jsonify({"error": "User tidak ditemukan", "pseudonym": pseudonym}), 404

        cached = db.query(CFRecommendation).filter_by(user_id=user.id).first()
        if cached:
            import json as _json
            article_ids = _json.loads(cached.article_ids)[:top_n]
            scores = _json.loads(cached.scores)[:top_n]
            articles = db.query(Article).filter(Article.id.in_(article_ids)).all()
            arts_map = {a.id: a for a in articles}
            recs = []
            for aid, score in zip(article_ids, scores):
                a = arts_map.get(aid)
                if a:
                    recs.append({
                        "article_id": a.id,
                        "title": a.title,
                        "source": a.source,
                        "category": a.category or "Umum",
                        "url": a.url,
                        "excerpt": a.excerpt,
                        "published_at": a.published_at,
                        "score": score,
                    })
            return jsonify({
                "pseudonym": pseudonym,
                "user_id": user.id,
                "source": "cache",
                "computed_at": str(cached.computed_at),
                "recommendations": recs,
            })

    cf = get_cf_model()
    if not cf.user_item:
        result = cf.build_from_db()
        if result.get("status") != "ok":
            return jsonify({"error": "Model CF belum siap", "detail": result}), 503

    recs_raw = cf.recommend(user.id, top_n=top_n)
    if not recs_raw:
        return jsonify({"pseudonym": pseudonym, "recommendations": [], "message": "Belum cukup data interaksi"})

    with get_db() as db:
        article_ids = [r["article_id"] for r in recs_raw]
        articles = db.query(Article).filter(Article.id.in_(article_ids)).all()
        arts_map = {a.id: a for a in articles}
        recs = []
        for r in recs_raw:
            a = arts_map.get(r["article_id"])
            if a:
                recs.append({
                    "article_id": a.id,
                    "title": a.title,
                    "source": a.source,
                    "category": a.category or "Umum",
                    "url": a.url,
                    "excerpt": a.excerpt,
                    "published_at": a.published_at,
                    "score": r["score"],
                })

    return jsonify({
        "pseudonym": pseudonym,
        "user_id": user.id,
        "source": "realtime",
        "recommendations": recs,
    })


@app.route("/api/cf/similar/<int:article_id>", methods=["GET"])
def cf_similar(article_id):
    top_n = int(request.args.get("top_n", 5))
    cf = get_cf_model()
    if not cf.item_sim:
        result = cf.build_from_db()
        if result.get("status") != "ok":
            return jsonify({"error": "Model CF belum siap"}), 503
    sims_raw = cf.similar_items(article_id, top_n=top_n)
    if not sims_raw:
        return jsonify({"article_id": article_id, "similar": []})
    with get_db() as db:
        ids = [s["article_id"] for s in sims_raw]
        articles = db.query(Article).filter(Article.id.in_(ids)).all()
        arts_map = {a.id: a for a in articles}
    similar = []
    for s in sims_raw:
        a = arts_map.get(s["article_id"])
        if a:
            similar.append({
                "article_id": a.id,
                "title": a.title,
                "source": a.source,
                "category": a.category or "Umum",
                "url": a.url,
                "similarity": s["similarity"],
            })
    return jsonify({"article_id": article_id, "similar": similar})


@app.route("/api/cf/evaluate", methods=["GET"])
def cf_evaluate():
    top_k = int(request.args.get("top_k", 10))
    cf = get_cf_model()
    if not cf.user_item:
        result = cf.build_from_db()
        if result.get("status") != "ok":
            return jsonify({"error": "Model CF belum siap", "detail": result}), 503
    metrics = cf.evaluate(test_ratio=0.2, top_k=top_k)
    return jsonify(metrics)


@app.route("/api/cf/status", methods=["GET"])
def cf_status():
    cf = get_cf_model()
    with get_db() as db:
        n_recs = db.query(CFRecommendation).count()
        n_interactions = db.query(Interaction).count()
        n_users = db.query(User).count()
    return jsonify({
        "model_built": cf.last_built is not None,
        "last_built": str(cf.last_built) if cf.last_built else None,
        "n_users_indexed": len(cf.user_item),
        "n_items_indexed": len(cf.item_sim),
        "n_cached_recs": n_recs,
        "n_interactions": n_interactions,
        "n_users_db": n_users,
    })


@app.route("/api/cf/scheduler", methods=["GET"])
def get_cf_scheduler():
    return jsonify(cf_scheduler.get_status())


@app.route("/api/cf/scheduler", methods=["POST"])
def update_cf_scheduler():
    data = request.json or {}
    if "interval_hours" in data:
        try:
            iv = int(data["interval_hours"])
            if not (1 <= iv <= 168):
                return jsonify({"error": "Interval 1-168 jam"}), 400
            data["interval_hours"] = iv
        except Exception:
            return jsonify({"error": "Interval tidak valid"}), 400
    return jsonify({"message": "Tersimpan", "config": cf_scheduler.update_config(**data)})


@app.route("/api/cf/scheduler/run-now", methods=["POST"])
def cf_scheduler_run_now():
    if cf_status.get("running"):
        return jsonify({"error": "Build CF sedang berjalan"}), 400
    cf_scheduler.trigger_rebuild_now(reason="manual-trigger")
    return jsonify({"message": "Build CF dijadwalkan segera"})


@app.route("/api/cf/params", methods=["POST"])
def update_cf_params():
    data = request.json or {}
    current = load_cf_params()

    validations = {
        "lambda_decay":  (0.001, 2.0),
        "click_weight":  (0.0, 10.0),
        "read_weight":   (0.0, 10.0),
        "share_weight":  (0.0, 10.0),
        "min_sim":       (0.0, 1.0),
        "top_n_default": (1, 50),
    }
    for key, (lo, hi) in validations.items():
        if key in data:
            try:
                val = float(data[key])
                if not (lo <= val <= hi):
                    return jsonify({"error": f"{key} harus antara {lo} dan {hi}"}), 400
                current[key] = val
            except (TypeError, ValueError):
                return jsonify({"error": f"{key} tidak valid"}), 400

    save_cf_params(current)
    return jsonify({"message": "Parameter tersimpan. Build ulang model agar diterapkan.", "params": current})


@app.route("/api/cf/params/reset", methods=["POST"])
def reset_cf_params():
    save_cf_params(DEFAULT_PARAMS.copy())
    return jsonify({"message": "Parameter dikembalikan ke default", "params": DEFAULT_PARAMS})


@app.route("/api/interactions/user/<pseudonym>", methods=["GET"])
def get_user_interaction_detail(pseudonym):
    """
    Detail log interaksi satu user untuk visualisasi:
    - timeline (urut waktu, untuk line/scatter chart)
    - distribusi kategori (untuk pie/bar chart)
    - distribusi jam (untuk mengetahui jam aktif user)
    """
    with get_db() as db:
        user = db.query(User).filter_by(pseudonym=pseudonym).first()
        if not user:
            return jsonify({"error": "User tidak ditemukan"}), 404

        rows = db.query(Interaction, Article).join(
            Article, Interaction.article_id == Article.id
        ).filter(Interaction.user_id == user.id).order_by(Interaction.created_at).all()

        if not rows:
            return jsonify({
                "pseudonym": pseudonym, "total": 0,
                "timeline": [], "per_kategori": [], "per_jam": [],
            })

        timeline = []
        kat_count = {}
        jam_count = {h: 0 for h in range(24)}

        for ix, art in rows:
            timeline.append({
                "waktu": str(ix.created_at),
                "artikel_id": art.id,
                "judul": art.title,
                "sumber": art.source,
                "kategori": art.category or "Umum",
                "event_type": ix.event_type,
            })
            kat = art.category or "Umum"
            kat_count[kat] = kat_count.get(kat, 0) + 1
            jam_count[ix.created_at.hour] += 1

        per_kategori = [{"kategori": k, "count": v} for k, v in
                        sorted(kat_count.items(), key=lambda x: -x[1])]
        per_jam = [{"jam": h, "count": jam_count[h]} for h in range(24)]

        return jsonify({
            "pseudonym": pseudonym,
            "user_id": user.id,
            "total": len(rows),
            "timeline": timeline,
            "per_kategori": per_kategori,
            "per_jam": per_jam,
            "first_interaction": str(rows[0][0].created_at),
            "last_interaction": str(rows[-1][0].created_at),
        })


@app.route("/api/stats/clicks", methods=["GET"])
def get_click_stats():
    with get_db() as db:
        portal_clicks = db.query(
            Article.source,
            func.count(Interaction.id).label("click_count")
        ).join(Interaction, Article.id == Interaction.article_id)\
         .group_by(Article.source)\
         .order_by(func.count(Interaction.id).desc()).all()

        article_clicks = db.query(
            Article.id, Article.title, Article.source, Article.category, Article.image_url,
            func.count(Interaction.id).label("click_count")
        ).join(Interaction, Article.id == Interaction.article_id)\
         .group_by(Article.id, Article.title, Article.source, Article.category, Article.image_url)\
         .order_by(func.count(Interaction.id).desc()).limit(20).all()

        return jsonify({
            "portal_clicks": [
                {"portal": r.source or "-", "click_count": r.click_count}
                for r in portal_clicks
            ],
            "article_clicks": [
                {
                    "id": r.id,
                    "title": r.title,
                    "source": r.source or "-",
                    "category": r.category or "Umum",
                    "image_url": r.image_url or "",
                    "click_count": r.click_count
                }
                for r in article_clicks
            ],
        })


@app.route("/api/stats/recommendation-ctr", methods=["GET"])
def get_recommendation_ctr():
    """
    Hitung Click-Through Rate (CTR) panel rekomendasi CF.
    Dipakai untuk validasi nyata: seberapa sering user klik 'Untuk Kamu'.
    """
    with get_db() as db:
        total_organic  = db.query(Interaction).filter(Interaction.event_type == "click").count()
        total_rec      = db.query(Interaction).filter(Interaction.event_type == "rec_click").count()
        total_read     = db.query(Interaction).filter(Interaction.event_type == "read").count()
        total_share    = db.query(Interaction).filter(Interaction.event_type == "share").count()
        total_all      = total_organic + total_rec + total_read + total_share

        # User unik yang pernah klik rekomendasi
        users_clicked_rec = db.query(func.count(func.distinct(Interaction.user_id)))\
            .filter(Interaction.event_type == "rec_click").scalar() or 0

        # User unik yang punya setidaknya 1 interaksi (real users — exclude sim users)
        users_total = db.query(func.count(func.distinct(Interaction.user_id))).scalar() or 0

        # Top artikel yang diklik dari rekomendasi
        top_rec_articles = db.query(
            Article.id, Article.title, Article.source, Article.category,
            func.count(Interaction.id).label("rec_clicks")
        ).join(Interaction, Article.id == Interaction.article_id)\
         .filter(Interaction.event_type == "rec_click")\
         .group_by(Article.id, Article.title, Article.source, Article.category)\
         .order_by(func.count(Interaction.id).desc()).limit(10).all()

        ctr = round(total_rec / (total_organic + total_rec) * 100, 2) if (total_organic + total_rec) > 0 else 0

        return jsonify({
            "total_organic_clicks":  total_organic,
            "total_rec_clicks":      total_rec,
            "total_read":            total_read,
            "total_share":           total_share,
            "ctr_recommendation_pct": ctr,
            "users_clicked_rec":     users_clicked_rec,
            "users_total":           users_total,
            "adoption_rate_pct":     round(users_clicked_rec / users_total * 100, 2) if users_total > 0 else 0,
            "top_rec_articles": [
                {"id": r.id, "title": r.title, "source": r.source,
                 "category": r.category or "Umum", "rec_clicks": r.rec_clicks}
                for r in top_rec_articles
            ],
        })


# ────────────────────────────────────────────────────────────────
# MISC ENDPOINTS
# ────────────────────────────────────────────────────────────────
@app.route("/api/kategori", methods=["GET"])
def get_kategori():
    return jsonify(["Semua"] + list(KATEGORI_MAPPING.keys()))


@app.route("/api/download/csv", methods=["GET"])
def download_articles_csv():
    with get_db() as db:
        arts = db.query(Article).order_by(Article.parsed_at.desc()).all()
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["id", "title", "source", "category", "url", "published_at", "parsed_at", "url_hash", "excerpt"])
    for a in arts:
        w.writerow([a.id, a.title, a.source, a.category or "Umum", a.url,
                    a.published_at, str(a.parsed_at), a.url_hash, (a.excerpt or "")[:200]])
    out.seek(0)
    return Response(out.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment;filename=articles.csv"})