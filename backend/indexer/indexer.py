import hashlib, re, unicodedata
from urllib.parse import urlparse, urlunparse, urlencode, parse_qsl
from datetime import datetime
from ..db.connection import get_db
from ..db.models import Article

STRIP_PARAMS = {"utm_source","utm_medium","utm_campaign","utm_term","utm_content",
                "ref","from","source","fbclid","gclid","ocid","_ga"}

def canonicalize_url(url):
    try:
        p = urlparse(url.strip())
        params = sorted([(k,v) for k,v in parse_qsl(p.query) if k.lower() not in STRIP_PARAMS])
        return urlunparse((p.scheme.lower(), p.netloc.lower().lstrip("www."),
                           p.path.rstrip("/") or "/", "", urlencode(params), ""))
    except:
        return url

def make_url_hash(url):
    return hashlib.sha256(canonicalize_url(url).encode()).hexdigest()

def make_content_hash(title, excerpt=""):
    return hashlib.sha256(f"{title.strip().lower()}|{excerpt.strip().lower()[:200]}".encode()).hexdigest()

def compute_simhash(text, bits=64):
    if not text or not text.strip(): return 0
    text = unicodedata.normalize("NFKC", text.lower())
    text = re.sub(r"[^\w\s]", " ", text)
    words = text.split()
    tokens = words + [f"{words[i]} {words[i+1]}" for i in range(len(words)-1)]
    v = [0]*bits
    for token in tokens[:200]:
        h = int(hashlib.md5(token.encode()).hexdigest(), 16)
        for i in range(bits):
            v[i] += 1 if (h>>i)&1 else -1
    fp = 0
    for i in range(bits):
        if v[i] > 0: fp |= (1<<i)
    return fp

def hamming_distance(h1, h2):
    x, d = h1^h2, 0
    while x: d += x&1; x >>= 1
    return d

class ArticleIndexer:
    SIMHASH_THRESHOLD = 5

    def index(self, raw, portal_id=None, run_id=None):
        if not raw.get("judul","").strip():      return {"status":"invalid","reason":"judul kosong"}
        if not raw.get("url","").strip():        return {"status":"invalid","reason":"url kosong"}
        if not raw["url"].strip().startswith("http"): return {"status":"invalid","reason":"url tidak valid"}

        title    = raw.get("judul","").strip()
        url      = raw.get("url","").strip()
        excerpt  = raw.get("ringkasan","").strip()
        body     = raw.get("isi","").strip()
        source   = raw.get("sumber","").strip()
        tanggal  = raw.get("tanggal","").strip()
        kategori = raw.get("kategori","Umum").strip()

        canonical  = canonicalize_url(url)
        url_hash   = make_url_hash(url)
        c_hash     = make_content_hash(title, excerpt)
        sh_int     = compute_simhash(f"{title} {excerpt}")
        sh_str     = format(sh_int, "016x")

        with get_db() as db:
            if db.query(Article).filter_by(url_hash=url_hash).first():
                return {"status":"duplicate", "url":url}
            if db.query(Article).filter_by(content_hash=c_hash).first():
                return {"status":"duplicate", "url":url}

            recent = db.query(Article).filter(
                Article.source==source, Article.simhash.isnot(None)
            ).order_by(Article.parsed_at.desc()).limit(50).all()

            for art in recent:
                try:
                    if hamming_distance(sh_int, int(art.simhash,16)) <= self.SIMHASH_THRESHOLD:
                        return {"status":"near_dup", "url":url}
                except: pass

            # --- Update: Tambahan ekstraksi image_url ke database ---
            image_url = raw.get("image_url","").strip() or None
            
            article = Article(
                portal_id=portal_id, title=title,
                excerpt=excerpt[:500] if excerpt else None,
                body=body or None, published_at=tanggal or None,
                source=source, url=url, url_hash=url_hash,
                content_hash=c_hash, simhash=sh_str,
                canonical_url=canonical, category=kategori,
                image_url=image_url,
                parsed_at=datetime.utcnow(), crawl_run_id=run_id,
            )
            # --------------------------------------------------------
            
            db.add(article); db.flush()
            aid = article.id

        return {"status":"new", "article_id":aid, "url":url, "title":title}

    def bulk_index(self, articles, portal_id=None, run_id=None):
        stats = {"new":0,"duplicate":0,"near_dup":0,"invalid":0,"error":0}
        for art in articles:
            try:
                r = self.index(art, portal_id=portal_id, run_id=run_id)
                stats[r["status"]] = stats.get(r["status"],0) + 1
            except Exception as e:
                stats["error"] += 1
        return {"stats": stats}