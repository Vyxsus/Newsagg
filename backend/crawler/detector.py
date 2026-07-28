import requests, re
from bs4 import BeautifulSoup
from urllib.parse import urlparse, urljoin

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; NewsAggBot/1.0)"}
KNOWN_PORTALS = {
    "liputan6":"https://www.liputan6.com","okezone":"https://www.okezone.com",
    "merdeka":"https://www.merdeka.com","sindonews":"https://www.sindonews.com",
    "republika":"https://www.republika.co.id","tempo":"https://www.tempo.co",
    "antara":"https://www.antaranews.com","bisnis":"https://www.bisnis.com",
    "suara":"https://www.suara.com","kumparan":"https://kumparan.com",
    "idntimes":"https://www.idntimes.com","viva":"https://www.viva.co.id",
    "detik":"https://www.detik.com","kompas":"https://www.kompas.com",
    "cnn":"https://www.cnnindonesia.com","cnnindonesia":"https://www.cnnindonesia.com",
    "jpnn":"https://www.jpnn.com","jawapos":"https://www.jawapos.com",
    "solopos":"https://www.solopos.com","medcom":"https://www.medcom.id",
}

def resolve_url(query):
    q = query.strip().lower().rstrip("/")
    for kw, url in KNOWN_PORTALS.items():
        if kw in q: return url
    if "." in q:
        return "https://" + q if not q.startswith("http") else q
    for tld in [".com",".co.id",".id"]:
        candidate = f"https://www.{q}{tld}"
        try:
            r = requests.head(candidate, headers=HEADERS, timeout=8, allow_redirects=True)
            if r.status_code < 400: return candidate
        except: pass
    return None

def detect_selectors(soup):
    art_candidates = ["article","div.article","div[class*='article']","div[class*='news']",
                      "div[class*='post']","div[class*='item']","div[class*='card']","div[class*='list']"]
    art_sel, best = "article", 0
    for sel in art_candidates:
        try:
            c = len(soup.select(sel))
            if 3 <= c <= 50 and c > best: art_sel, best = sel, c
        except: pass
    articles = soup.select(art_sel)[:5]
    def best_child(candidates, fallback):
        b = (fallback, 0)
        for sel in candidates:
            c = sum(1 for a in articles if a.select_one(sel))
            if c > b[1]: b = (sel, c)
        return b[0]
    return {
        "article":  art_sel,
        "title":    best_child(["h1","h2","h3","h4","a[class*='title']","div[class*='title']"],"h2, h3"),
        "link":     "a[href]",
        "date":     best_child(["time","span[class*='date']","div[class*='date']","span[class*='ago']"],"time"),
        "summary":  "p",
        "category": best_child(["span[class*='label']","span[class*='cat']","span[class*='tag']","a[class*='cat']"],"span[class*='label']"),
    }

def detect_sub_urls(soup, base_url):
    domain = urlparse(base_url).netloc
    seen, result = {base_url}, [base_url]
    for nav in soup.select("nav a[href], header a[href], div[class*='nav'] a[href]"):
        href = nav.get("href","")
        if not href or href=="#" or href.startswith("javascript"): continue
        full = urljoin(base_url, href)
        p    = urlparse(full)
        if p.netloc==domain and len(p.path)>1 and full not in seen:
            seen.add(full); result.append(full)
        if len(result)>=3: break
    return result

def preview_articles(soup, selectors, base_url):
    results = []
    for item in soup.select(selectors["article"])[:8]:
        t_el = item.select_one(selectors["title"])
        l_el = item.select_one(selectors["link"])
        d_el = item.select_one(selectors["date"])
        title = t_el.get_text(strip=True) if t_el else ""
        url   = l_el.get("href","")       if l_el else ""
        if url and not url.startswith("http"): url = urljoin(base_url, url)
        if title and len(title)>10 and url:
            results.append({"judul":title[:100],"url":url,"tanggal":d_el.get_text(strip=True) if d_el else "-"})
        if len(results)>=5: break
    return results

def auto_detect(query):
    base_url = resolve_url(query)
    if not base_url:
        return {"success":False,"error":f"Tidak dapat menemukan URL untuk '{query}'."}
    try:
        resp = requests.get(base_url, headers=HEADERS, timeout=15)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
    except Exception as e:
        return {"success":False,"error":f"Tidak dapat mengakses {base_url}: {e}"}
    title_tag = soup.find("title")
    site_name = title_tag.get_text(strip=True).split("|")[0].split("-")[0].strip() if title_tag else query.title()
    domain    = urlparse(base_url).netloc.replace("www.","")
    selectors = detect_selectors(soup)
    urls      = detect_sub_urls(soup, base_url)
    preview   = preview_articles(soup, selectors, base_url)
    return {
        "success":True,"detected_url":base_url,"preview_count":len(preview),"preview":preview,
        "suggested_config":{
            "key":re.sub(r"[^a-z0-9]","",query.lower())[:20],"name":site_name,
            "domain":domain,"active":True,"urls":urls,"selectors":selectors,
        },
    }
