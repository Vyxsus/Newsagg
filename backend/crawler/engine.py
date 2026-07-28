"""
Hybrid Crawler Engine
Strategi per portal (urutan prioritas):
  1. RSS + Feedparser   -> listing artikel dari feed
  2. Newspaper3k        -> ekstrak isi artikel otomatis
  3. BeautifulSoup      -> fallback parsing HTML manual

Keunggulan:
  - RSS tidak kena robots.txt (memang dibuat untuk bot)
  - Newspaper3k tidak perlu selector CSS manual
  - Coverage portal jauh lebih luas
"""

import requests, time, re, json, feedparser, unicodedata
from datetime import datetime
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser
from bs4 import BeautifulSoup
from typing import Optional, Callable

USER_AGENT = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
              "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")
HEADERS    = {"User-Agent": USER_AGENT}
TIMEOUT    = 15
RATE_DELAY = 1.2

KATEGORI_MAPPING = {
    "Nasional":      ["nasional","indonesia","nusantara","daerah","regional","peristiwa","news"],
    "Politik":       ["politik","pemerintah","pilkada","pemilu","dpr","mpr","dprd"],
    "Ekonomi":       ["ekonomi","keuangan","pasar","saham","rupiah","inflasi","finance","moneter"],
    "Bisnis":        ["bisnis","industri","korporasi","startup","umkm","investasi"],
    "Teknologi":     ["teknologi","tekno","gadget","digital","internet","ai","software","sains","inet"],
    "Olahraga":      ["olahraga","sport","bola","sepak bola","basket","badminton","atletik"],
    "Hiburan":       ["hiburan","entertainment","selebriti","film","musik","artis","seleb","hot"],
    "Internasional": ["internasional","dunia","global","luar negeri","world","mancanegara"],
    "Kesehatan":     ["kesehatan","health","medis","dokter","covid","virus","gizi"],
    "Pendidikan":    ["pendidikan","kampus","universitas","sekolah","beasiswa","edukasi"],
    "Otomotif":      ["otomotif","mobil","motor","kendaraan","oto"],
    "Gaya Hidup":    ["gaya hidup","lifestyle","kuliner","travel","wisata","fashion","food"],
    "Hukum":         ["hukum","kriminal","polisi","pengadilan","korupsi","kpk","kejaksaan"],
}

PORTAL_CONFIGS = [
    {"name":"Detik.com","domain":"detik.com","method":"hybrid",
     "rss_urls":["https://rss.detik.com/index.php/detikcom","https://rss.detik.com/index.php/detiknews","https://rss.detik.com/index.php/detikfinance"],
     "html_urls":["https://www.detik.com/terpopuler","https://news.detik.com/","https://finance.detik.com/"],
     "selectors":{"article":"article","title":"h2, h3","link":"a[href]","date":"div.media__date, time","category":"span.label"},
     "content_selectors":["div.detail__body-text","div.itp_bodycontent"]},

    {"name":"Kompas.com","domain":"kompas.com","method":"hybrid",
     "rss_urls":["https://rss.kompas.com/","https://rss.kompas.com/money","https://rss.kompas.com/tekno"],
     "html_urls":["https://www.kompas.com/","https://money.kompas.com/","https://tekno.kompas.com/"],
     "selectors":{"article":"div.article__list, div.latest--item","title":"h3, h2, a","link":"a[href]","date":"div.article__date, time","category":"span.article__label"},
     "content_selectors":["div.read__content","div.article__content"]},

    {"name":"Antara News","domain":"antaranews.com","method":"hybrid",
     "rss_urls":["https://www.antaranews.com/rss/terkini.xml","https://www.antaranews.com/rss/ekonomi.xml","https://www.antaranews.com/rss/tekno.xml"],
     "html_urls":["https://www.antaranews.com","https://www.antaranews.com/ekonomi"],
     "selectors":{"article":"div.simple-post, article","title":"h2, h3","link":"a[href]","date":"time","category":"span[class*='label']"},
     "content_selectors":["div.post-content"]},

    {"name":"Republika","domain":"republika.co.id","method":"hybrid",
     "rss_urls":["https://www.republika.co.id/rss","https://www.republika.co.id/rss/ekonomi"],
     "html_urls":["https://www.republika.co.id","https://www.republika.co.id/indeks/kategori/ekonomi"],
     "selectors":{"article":"div[class*='item'], article","title":"h2, h3","link":"a[href]","date":"time","category":"span[class*='tag']"},
     "content_selectors":["div.article-content"]},

    {"name":"Liputan6","domain":"liputan6.com","method":"hybrid",
     "rss_urls":["https://www.liputan6.com/rss","https://www.liputan6.com/rss/bisnis","https://www.liputan6.com/rss/tekno"],
     "html_urls":["https://www.liputan6.com","https://www.liputan6.com/news"],
     "selectors":{"article":"article","title":"h2, h3","link":"a[href]","date":"time","category":"span[class*='label']"},
     "content_selectors":["div.article-content-body","div[itemprop='articleBody']"]},

    {"name":"Okezone","domain":"okezone.com","method":"hybrid",
     "rss_urls":["https://sindikasi.okezone.com/index.php/rss/0/RSS2.0","https://sindikasi.okezone.com/index.php/rss/2/RSS2.0"],
     "html_urls":["https://www.okezone.com","https://economy.okezone.com","https://techno.okezone.com"],
     "selectors":{"article":"article, div[class*='list']","title":"h2, h3","link":"a[href]","date":"time, span[class*='date']","category":"span[class*='label']"},
     "content_selectors":["div#content-detail","div.detail-content"]},

    {"name":"Merdeka.com","domain":"merdeka.com","method":"hybrid",
     "rss_urls":["https://www.merdeka.com/feed/","https://www.merdeka.com/teknologi/feed/"],
     "html_urls":["https://www.merdeka.com","https://www.merdeka.com/ekonomi"],
     "selectors":{"article":"article","title":"h2, h3","link":"a[href]","date":"time, span[class*='date']","category":"span[class*='label']"},
     "content_selectors":["div.artikel","div.detail-content"]},

    {"name":"JPNN","domain":"jpnn.com","method":"hybrid",
     "rss_urls":["https://www.jpnn.com/rss"],
     "html_urls":["https://www.jpnn.com","https://www.jpnn.com/ekonomi"],
     "selectors":{"article":"div.item-berita, article","title":"h2, h3","link":"a[href]","date":"span.date, time","category":"span[class*='label']"},
     "content_selectors":["div.detail-text"]},

    {"name":"Sindonews","domain":"sindonews.com","method":"hybrid",
     "rss_urls":["https://feeds.sindonews.com/rss/nasional","https://feeds.sindonews.com/rss/ekbis","https://feeds.sindonews.com/rss/tekno"],
     "html_urls":["https://nasional.sindonews.com","https://ekbis.sindonews.com"],
     "selectors":{"article":"div.homelist-item, article","title":"h2, h3, h4","link":"a[href]","date":"span.date, time","category":"span[class*='label']"},
     "content_selectors":["div.detail-content"]},

    {"name":"Bisnis.com","domain":"bisnis.com","method":"hybrid",
     "rss_urls":["https://rss.bisnis.com/bisnis.com.rss"],
     "html_urls":["https://www.bisnis.com","https://teknologi.bisnis.com"],
     "selectors":{"article":"article, div[class*='article']","title":"h2, h3","link":"a[href]","date":"time, span[class*='date']","category":"span[class*='label']"},
     "content_selectors":["div.article__content"]},

    {"name":"Viva.co.id","domain":"viva.co.id","method":"hybrid",
     "rss_urls":["https://www.viva.co.id/rss/nasional","https://www.viva.co.id/rss/bisnis"],
     "html_urls":["https://www.viva.co.id","https://www.viva.co.id/bisnis"],
     "selectors":{"article":"article, div[class*='item']","title":"h2, h3","link":"a[href]","date":"time, span[class*='date']","category":"span[class*='label']"},
     "content_selectors":["div.main-article"]},

    {"name":"IDN Times","domain":"idntimes.com","method":"hybrid",
     "rss_urls":["https://www.idntimes.com/rss/feed.xml"],
     "html_urls":["https://www.idntimes.com","https://www.idntimes.com/business"],
     "selectors":{"article":"article, div[class*='article']","title":"h2, h3","link":"a[href]","date":"time, span[class*='date']","category":"span[class*='label']"},
     "content_selectors":["div.article-content"]},

    {"name":"Kumparan","domain":"kumparan.com","method":"rss",
     "rss_urls":["https://kumparan.com/@kumparannews/rss","https://kumparan.com/@kumparantech/rss","https://kumparan.com/@kumparanbisnis/rss"],
     "html_urls":[],"selectors":{},"content_selectors":["div[class*='usercontent']"]},

    {"name":"Medcom","domain":"medcom.id","method":"hybrid",
     "rss_urls":["https://www.medcom.id/rss"],
     "html_urls":["https://www.medcom.id","https://www.medcom.id/ekonomi"],
     "selectors":{"article":"div.col-article, article","title":"h2, h3","link":"a[href]","date":"time, span[class*='date']","category":"span[class*='label']"},
     "content_selectors":["div.article-content"]},

    {"name":"Jawapos","domain":"jawapos.com","method":"hybrid",
     "rss_urls":["https://www.jawapos.com/feed/"],
     "html_urls":["https://www.jawapos.com","https://www.jawapos.com/ekonomi-bisnis"],
     "selectors":{"article":"article, div[class*='article']","title":"h2, h3","link":"a[href]","date":"time, span[class*='date']","category":"span[class*='label']"},
     "content_selectors":["div.detail-content"]},
]

NOISE_SELECTORS = [
    "script","style","noscript","iframe","figure","figcaption",
    "div.ads","div[class*='ads']","div[class*='iklan']","div[class*='banner']",
    "div[class*='related']","div[class*='recommend']","div[class*='social']",
    "div[class*='share']","div[class*='tag']","div[class*='author']",
    "aside","nav","header","footer","ins","form","button",
]


def normalize_kategori(raw):
    if not raw: return "Umum"
    r = raw.lower().strip()
    for kat, kws in KATEGORI_MAPPING.items():
        if any(kw in r for kw in kws): return kat
    return raw.strip().title() or "Umum"


def clean_text(text):
    if not text: return ""
    text = unicodedata.normalize("NFKC", text)
    return re.sub(r"\s+", " ", text).strip()


def parse_rss_date(entry):
    for attr in ["published","updated","created"]:
        val = getattr(entry, attr, None)
        if val: return clean_text(val)
    return ""


class RateLimiter:
    def __init__(self): self._last = {}
    def wait(self, url):
        host = urlparse(url).netloc
        elapsed = time.time() - self._last.get(host, 0)
        if elapsed < RATE_DELAY: time.sleep(RATE_DELAY - elapsed)
        self._last[host] = time.time()


class RobotsChecker:
    def __init__(self): self._cache = {}
    def is_allowed(self, url, bypass=False):
        if bypass: return True
        domain = "{0.scheme}://{0.netloc}".format(urlparse(url))
        if domain not in self._cache:
            rp = RobotFileParser()
            try: rp.set_url(f"{domain}/robots.txt"); rp.read()
            except: rp = None
            self._cache[domain] = rp
        rp = self._cache.get(domain)
        return rp.can_fetch(USER_AGENT, url) if rp else True


class NewspaperExtractor:
    """Ekstrak isi artikel otomatis menggunakan newspaper3k."""
    def extract(self, url):
        try:
            from newspaper import Article as NpArticle
            art = NpArticle(url, language="id", fetch_images=False)
            art.download(); art.parse()
            title = clean_text(art.title or "")
            # Preserve paragraph structure: clean whitespace within each paragraph,
            # but keep double-newline separators that newspaper3k produces
            raw_body = art.text or ""
            paras = [re.sub(r"[ \t]+", " ", p).strip()
                     for p in re.split(r"\n{2,}", raw_body) if p.strip()]
            if not paras:
                paras = [re.sub(r"\s+", " ", raw_body).strip()]
            body = "\n\n".join(paras)
            if not body or len(body) < 100:
                return {"success":False,"body":"","ringkasan":"","title":title,"tanggal":""}
            flat = body.replace("\n\n", " ")
            sentences = [s.strip() for s in re.split(r"(?<=[.!?])\s+", flat) if len(s.strip())>30]
            ringkasan = " ".join(sentences[:2])
            if len(ringkasan) > 300: ringkasan = ringkasan[:297]+"..."
            pub_date = ""
            if art.publish_date:
                try: pub_date = art.publish_date.strftime("%d %B %Y")
                except: pass
            
            # Ambil gambar dari newspaper3k atau meta tag
            image_url = ""
            if art.top_image:
                image_url = art.top_image
            return {"success":True,"body":body,"ringkasan":ringkasan,
                    "title":title,"tanggal":pub_date,"image_url":image_url}
        except Exception:
            return {"success":False,"body":"","ringkasan":"","title":"","tanggal":""}


class BSExtractor:
    """Ekstrak isi artikel menggunakan BeautifulSoup sebagai fallback."""
    def extract(self, url, content_selectors, rate_limiter):
        rate_limiter.wait(url)
        try:
            r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "html.parser")
        except Exception:
            return {"success":False,"body":"","ringkasan":""}
        generic = ["div[itemprop='articleBody']","div.article-body","div.article-content",
                   "div.detail-content","div.post-content","div.entry-content","article"]
        content_el = None
        for sel in content_selectors + generic:
            try:
                el = soup.select_one(sel)
                if el and len(el.get_text(strip=True)) > 200:
                    content_el = el; break
            except Exception: pass
        if not content_el: return {"success":False,"body":"","ringkasan":""}
        for noise in NOISE_SELECTORS:
            try:
                for el in content_el.select(noise): el.decompose()
            except Exception: pass
        paragraphs = []
        for tag in content_el.find_all("p"):
            if tag.find_parent("p"): continue
            text = "".join(
                str(t).strip() if not hasattr(t,"get_text") else t.get_text(strip=True)
                for t in tag.children
                if not (hasattr(t,"name") and t.name=="p")
            ).strip()
            if not text: text = tag.get_text(strip=True)
            if len(text) < 40: continue
            if re.search(r"^\d{1,2}\s+(jam|menit|detik|hari)\s+yang\s+lalu", text, re.I): continue
            if re.search(r"^(baca juga|lihat juga|artikel terkait)", text, re.I): continue
            paragraphs.append(text)
        unique = []
        for p in paragraphs:
            if not any(u[:60]==p[:60] for u in unique): unique.append(p)
        if not unique: return {"success":False,"body":"","ringkasan":""}
        body      = "\n\n".join(unique)
        ringkasan = unique[0][:250] + ("..." if len(unique[0])>250 else "")
        return {"success":True,"body":body,"ringkasan":ringkasan}


class HybridCrawlerEngine:
    """
    Engine crawling hybrid:
    - Listing : RSS/Feedparser (utama) -> BeautifulSoup (fallback)
    - Isi     : Newspaper3k (utama)    -> BeautifulSoup (fallback)
    """
    def __init__(self):
        self._rate   = RateLimiter()
        self._robots = RobotsChecker()
        self._np     = NewspaperExtractor()
        self._bs     = BSExtractor()

    def _fetch_rss(self, rss_url):
        articles = []
        try:
            self._rate.wait(rss_url)
            feed = feedparser.parse(rss_url, agent=USER_AGENT)
            if feed.bozo and not feed.entries: return []
            for entry in feed.entries[:20]:
                title   = clean_text(getattr(entry,"title","") or "")
                url     = getattr(entry,"link","") or ""
                tanggal = parse_rss_date(entry)
                tags    = getattr(entry,"tags",[])
                raw_kat = tags[0].term if tags else ""
                summary_html = getattr(entry,"summary","") or ""
                ringkasan = ""
                if summary_html:
                    try:
                        soup = BeautifulSoup(summary_html, "html.parser")
                        ringkasan = clean_text(soup.get_text())[:250]
                    except Exception:
                        ringkasan = clean_text(summary_html)[:250]
                if title and url and url.startswith("http"):
                    articles.append({"judul":title,"url":url,"tanggal":tanggal,
                                     "ringkasan":ringkasan,"kategori":normalize_kategori(raw_kat),
                                     "_from_rss":True})
        except Exception as e:
            print(f"[RSS ERROR] {rss_url}: {e}")
        return articles

    def _fetch_html_listing(self, html_url, cfg):
        try:
            self._rate.wait(html_url)
            if not self._robots.is_allowed(html_url): return []
            r = requests.get(html_url, headers=HEADERS, timeout=TIMEOUT)
            r.raise_for_status()
            soup = BeautifulSoup(r.text, "html.parser")
        except Exception as e:
            print(f"[HTML ERROR] {html_url}: {e}"); return []
        sel      = cfg.get("selectors", {})
        articles = []
        items    = soup.select(sel.get("article","article"))
        if not items:
            items = soup.select("article, div[class*='article'], div[class*='news-item']")
        for item in items[:20]:
            t_el = item.select_one(sel.get("title","h2, h3"))
            l_el = item.select_one(sel.get("link","a[href]"))
            d_el = item.select_one(sel.get("date","time"))
            c_el = item.select_one(sel.get("category","span[class*='label']"))
            title = clean_text(t_el.get_text()) if t_el else ""
            url   = l_el.get("href","")         if l_el else ""
            if not url.startswith("http"): url = urljoin(html_url, url)
            raw_kat = c_el.get_text(strip=True) if c_el else ""
            if title and len(title)>8 and url.startswith("http"):
                articles.append({"judul":title,"url":url,"tanggal":d_el.get_text(strip=True) if d_el else "",
                                  "ringkasan":"","kategori":normalize_kategori(raw_kat),"_from_rss":False})
        return articles

    def _extract_image_url(self, url: str, soup=None) -> str:
        """
        Ekstrak URL gambar utama artikel.
        Prioritas: og:image > twitter:image > img pertama di konten.
        """
        try:
            if soup is None:
                self._rate.wait(url)
                r = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
                r.raise_for_status()
                soup = BeautifulSoup(r.text, "html.parser")
            # 1. Open Graph image (paling akurat)
            og = soup.find("meta", property="og:image")
            if og and og.get("content"):
                return og["content"].strip()
            # 2. Twitter card image
            tw = soup.find("meta", attrs={"name": "twitter:image"})
            if tw and tw.get("content"):
                return tw["content"].strip()
            # 3. Gambar pertama di konten artikel
            for sel in ["div.article-content img", "div.detail-content img",
                        "div.post-content img", "article img"]:
                img = soup.select_one(sel)
                if img and img.get("src"):
                    src = img["src"].strip()
                    if src.startswith("http") and not "logo" in src.lower():
                        return src
            return ""
        except Exception:
            return ""

    def _fetch_content(self, url, cfg):
        """Newspaper3k dulu, fallback BeautifulSoup."""
        result = self._np.extract(url)
        if result.get("success") and len(result.get("body",""))>200:
            return result
        bs = self._bs.extract(url, cfg.get("content_selectors",[]), self._rate)
        if bs.get("success"): return bs
        return {"body":"","ringkasan":"","tanggal":""}

    def crawl_portal(self, cfg, portal_id=None, kategori_filter="Semua", progress_cb=None):
        method    = cfg.get("method","hybrid")
        listing   = []
        seen_urls = set()

        # Step 1: Listing via RSS
        if method in ("rss","hybrid"):
            for rss_url in cfg.get("rss_urls",[]):
                try:
                    arts = self._fetch_rss(rss_url)
                    for a in arts:
                        if a["url"] not in seen_urls:
                            seen_urls.add(a["url"]); listing.append(a)
                    time.sleep(0.5)
                except Exception as e:
                    print(f"[RSS SKIP] {rss_url}: {e}")

        # Fallback ke HTML jika RSS kurang
        if method == "html" or (method == "hybrid" and len(listing) < 5):
            for html_url in cfg.get("html_urls",[]):
                try:
                    arts = self._fetch_html_listing(html_url, cfg)
                    for a in arts:
                        if a["url"] not in seen_urls:
                            seen_urls.add(a["url"]); listing.append(a)
                    time.sleep(0.5)
                except Exception as e:
                    print(f"[HTML SKIP] {html_url}: {e}")

        # Filter kategori
        if kategori_filter and kategori_filter != "Semua":
            listing = [a for a in listing
                       if kategori_filter.lower() in a.get("kategori","").lower()
                       or kategori_filter.lower() in a.get("judul","").lower()]

        # Step 2: Ekstrak isi tiap artikel
        enriched = []
        for i, art in enumerate(listing):
            if progress_cb: progress_cb(i+1, len(listing), art["judul"])
            content = self._fetch_content(art["url"], cfg)
            
            # Ekstrak gambar dari soup yang sudah diambil Newspaper3k
            # atau ambil ulang via BeautifulSoup
            image_url = content.get("image_url","")
            if not image_url:
                image_url = self._extract_image_url(art["url"])
                
            enriched.append({
                "judul":     art["judul"],
                "url":       art["url"],
                "sumber":    cfg["name"],
                "tanggal":   content.get("tanggal") or art.get("tanggal",""),
                "kategori":  art.get("kategori","Umum"),
                "ringkasan": content.get("ringkasan") or art.get("ringkasan",""),
                "isi":       content.get("body",""),
                "image_url": image_url,
            })

        print(f"[{cfg['name']}] {len(listing)} listing -> {len(enriched)} artikel")
        return enriched


def get_portal_configs():
    return PORTAL_CONFIGS


def get_portal_config_by_domain(domain):
    for cfg in PORTAL_CONFIGS:
        if cfg.get("domain","") in domain or domain in cfg.get("domain",""):
            return cfg
    return None