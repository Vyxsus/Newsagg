"""
Pengujian Runtime Algoritma Utama — NewsAgg
=============================================
Mengukur waktu eksekusi aktual dari:
  1. Deduplikasi url_hash / content_hash (SHA-256 + lookup DB)
  2. Deduplikasi SimHash (near-duplicate, Hamming distance)
  3. BangunModel (pembentukan model Collaborative Filtering)
  4. Rekomendasikan (generate Top-N rekomendasi per user)
  5. SimulasiInteraksi (simulasi data klik)

Hasil dicetak dalam bentuk tabel siap-pakai untuk Bab IV skripsi,
dan juga diekspor sebagai runtime_results.csv & runtime_results.json.

Jalankan: python test_runtime.py
"""

import sys, os, time, json, csv, hashlib, random, math
from datetime import datetime, timedelta
from collections import defaultdict

sys.path.insert(0, os.path.dirname(__file__))
try:
    from dotenv import load_dotenv; load_dotenv()
except ImportError:
    pass


# ════════════════════════════════════════════════════════════════
# UTIL
# ════════════════════════════════════════════════════════════════
class Timer:
    """Context manager sederhana untuk mengukur durasi eksekusi (ms)."""
    def __enter__(self):
        self.t0 = time.perf_counter()
        return self
    def __exit__(self, *args):
        self.elapsed_ms = (time.perf_counter() - self.t0) * 1000


def fmt_ms(ms):
    if ms < 1000:
        return f"{ms:.2f} ms"
    return f"{ms/1000:.2f} s"


def simhash_64(text: str) -> int:
    """SimHash sederhana 64-bit berbasis token hashing (replikasi indexer.py)."""
    tokens = text.lower().split()
    v = [0] * 64
    for tok in tokens:
        h = int(hashlib.md5(tok.encode()).hexdigest(), 16)
        for i in range(64):
            bit = (h >> i) & 1
            v[i] += 1 if bit else -1
    fingerprint = 0
    for i in range(64):
        if v[i] > 0:
            fingerprint |= (1 << i)
    return fingerprint


def hamming_distance(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def cosine_sim(va: dict, vb: dict) -> float:
    common = set(va) & set(vb)
    if not common:
        return 0.0
    dot = sum(va[k] * vb[k] for k in common)
    norm_a = math.sqrt(sum(v * v for v in va.values()))
    norm_b = math.sqrt(sum(v * v for v in vb.values()))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


# ════════════════════════════════════════════════════════════════
# AMBIL DATA DARI DATABASE
# ════════════════════════════════════════════════════════════════
def load_data_from_db():
    """
    Ambil artikel & interaksi dari PostgreSQL.
    Mengembalikan plain tuple agar tidak terjadi DetachedInstanceError.
    """
    from backend.db.connection import check_connection, get_db
    from backend.db.models import Article, Interaction, User

    if not check_connection():
        print("[ERROR] Tidak dapat terhubung ke database.")
        print("        Pengujian akan menggunakan DATA SINTETIS sebagai gantinya.\n")
        return None, None

    with get_db() as db:
        articles = db.query(
            Article.id, Article.title, Article.excerpt, Article.category
        ).all()
        interactions = db.query(
            Interaction.user_id, Interaction.article_id,
            Interaction.event_type, Interaction.created_at,
        ).all()

    return articles, interactions


def generate_synthetic_data(n_articles=6000, n_users=600, n_interactions=14000):
    """Fallback: bangkitkan data sintetis jika DB tidak tersedia, agar script tetap bisa diuji."""
    kategori_list = ["Nasional", "Ekonomi", "Teknologi", "Olahraga", "Hiburan"]
    articles = [
        (i, f"Judul artikel contoh nomor {i} tentang berita terkini hari ini",
         "Ini adalah ringkasan singkat dari artikel berita tersebut.",
         random.choice(kategori_list))
        for i in range(1, n_articles + 1)
    ]
    interactions = []
    now = datetime.utcnow()
    for _ in range(n_interactions):
        u = random.randint(1, n_users)
        a = random.randint(1, n_articles)
        ev = random.choices(["click", "read"], weights=[0.7, 0.3])[0]
        t = now - timedelta(days=random.uniform(0, 30))
        interactions.append((u, a, ev, t))
    return articles, interactions


# ════════════════════════════════════════════════════════════════
# PENGUJIAN 1: DEDUPLIKASI url_hash / content_hash
# ════════════════════════════════════════════════════════════════
def test_hash_dedup(articles):
    """
    Mengukur waktu pembuatan url_hash + content_hash dan
    pengecekan keberadaannya pada koleksi (simulasi lookup DB via set/dict).
    """
    seen_hashes = set()
    durations = []

    for aid, title, excerpt, category in articles:
        fake_url = f"https://example.com/artikel/{aid}"
        with Timer() as t:
            url_hash = hashlib.sha256(fake_url.encode()).hexdigest()
            content_hash = hashlib.sha256(
                f"{title}{excerpt or ''}".encode()
            ).hexdigest()
            _ = url_hash in seen_hashes  # simulasi lookup
            seen_hashes.add(url_hash)
        durations.append(t.elapsed_ms)

    avg_ms = sum(durations) / len(durations) if durations else 0
    total_ms = sum(durations)
    return {
        "nama": "Dedup url_hash / content_hash",
        "n": len(articles),
        "avg_per_item_ms": avg_ms,
        "total_ms": total_ms,
    }


# ════════════════════════════════════════════════════════════════
# PENGUJIAN 2: DEDUPLIKASI SimHash (near-duplicate)
# ════════════════════════════════════════════════════════════════
def test_simhash_dedup(articles, sample_size=500):
    """
    Mengukur waktu deteksi near-duplicate menggunakan SimHash.
    Karena kompleksitas O(n^2), pengujian dibatasi pada subset (sample_size)
    untuk menjaga waktu eksekusi pengujian itu sendiri tetap wajar,
    lalu hasilnya diekstrapolasi secara linear ke ukuran penuh.
    """
    subset = articles[:sample_size]
    fingerprints = []

    # Tahap 1: generate fingerprint
    with Timer() as t_gen:
        for aid, title, excerpt, category in subset:
            text = f"{title} {excerpt or ''}"
            fp = simhash_64(text)
            fingerprints.append((aid, fp))
    gen_ms = t_gen.elapsed_ms

    # Tahap 2: bandingkan setiap fingerprint baru dengan semua yang sudah ada (O(n^2))
    near_dup_count = 0
    with Timer() as t_cmp:
        existing = []
        for aid, fp in fingerprints:
            for _, fp_old in existing:
                if hamming_distance(fp, fp_old) <= 5:
                    near_dup_count += 1
                    break
            existing.append((aid, fp))
    cmp_ms = t_cmp.elapsed_ms

    total_sample_ms = gen_ms + cmp_ms
    avg_per_item_sample = total_sample_ms / len(subset) if subset else 0

    # Ekstrapolasi O(n^2) ke ukuran penuh koleksi
    n_full = len(articles)
    n_sample = len(subset)
    scale_factor = (n_full / n_sample) ** 2 if n_sample else 1
    extrapolated_total_ms = total_sample_ms * scale_factor

    return {
        "nama": "Dedup SimHash (near-duplicate)",
        "n_sampled": n_sample,
        "n_full_estimate": n_full,
        "avg_per_item_ms_sampled": avg_per_item_sample,
        "total_ms_sampled": total_sample_ms,
        "extrapolated_total_ms_full": extrapolated_total_ms,
        "near_dup_found_in_sample": near_dup_count,
    }


# ════════════════════════════════════════════════════════════════
# PENGUJIAN 3: BangunModel (Collaborative Filtering build)
# ════════════════════════════════════════════════════════════════
LAMBDA_DECAY = 0.1
CLICK_WEIGHT, READ_WEIGHT, SHARE_WEIGHT = 1.0, 2.0, 3.0
MIN_SIM = 0.01


def event_weight(ev):
    return {"click": CLICK_WEIGHT, "read": READ_WEIGHT, "share": SHARE_WEIGHT}.get(ev, 1.0)


def time_decay(created_at, lam=LAMBDA_DECAY):
    days = max(0, (datetime.utcnow() - created_at).total_seconds() / 86400)
    return math.exp(-lam * days)


def test_cf_build(interactions):
    """Replikasi prosedur BangunModel — mengukur waktu pembentukan matriks dan similarity."""
    with Timer() as t_matrix:
        user_item = defaultdict(lambda: defaultdict(float))
        item_users = defaultdict(lambda: defaultdict(float))
        for user_id, article_id, event_type, created_at in interactions:
            w = time_decay(created_at) * event_weight(event_type)
            user_item[user_id][article_id] += w
            item_users[article_id][user_id] += w
    matrix_ms = t_matrix.elapsed_ms

    with Timer() as t_sim:
        item_ids = list(item_users.keys())
        n_pairs = 0
        for i, item_a in enumerate(item_ids):
            va = item_users[item_a]
            for item_b in item_ids[i + 1:]:
                sim = cosine_sim(va, item_users[item_b])
                if sim > MIN_SIM:
                    n_pairs += 1
    sim_ms = t_sim.elapsed_ms

    return {
        "nama": "BangunModel (CF build)",
        "n_interaksi": len(interactions),
        "n_user": len(user_item),
        "n_item": len(item_users),
        "n_sim_pairs": n_pairs,
        "matrix_build_ms": matrix_ms,
        "similarity_compute_ms": sim_ms,
        "total_ms": matrix_ms + sim_ms,
    }, user_item, item_users


# ════════════════════════════════════════════════════════════════
# PENGUJIAN 4: Rekomendasikan (Top-N per user)
# ════════════════════════════════════════════════════════════════
def test_recommend(user_item, item_users, top_n=10, max_users_to_test=50):
    """Mengukur waktu generate Top-N rekomendasi untuk sejumlah user (rata-rata per user)."""
    # Bangun item_sim sekali (precomputed, seperti pada sistem nyata)
    item_ids = list(item_users.keys())
    item_sim = defaultdict(dict)
    for i, item_a in enumerate(item_ids):
        va = item_users[item_a]
        for item_b in item_ids[i + 1:]:
            sim = cosine_sim(va, item_users[item_b])
            if sim > MIN_SIM:
                item_sim[item_a][item_b] = sim
                item_sim[item_b][item_a] = sim

    user_ids = list(user_item.keys())[:max_users_to_test]
    durations = []

    for uid in user_ids:
        with Timer() as t:
            seen = set(user_item[uid].keys())
            scores = defaultdict(float)
            for seen_item, uw in user_item[uid].items():
                for cand, sim in item_sim.get(seen_item, {}).items():
                    if cand not in seen:
                        scores[cand] += sim * uw
            _ = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:top_n]
        durations.append(t.elapsed_ms)

    avg_ms = sum(durations) / len(durations) if durations else 0
    return {
        "nama": "Rekomendasikan (per-user, Top-10)",
        "n_user_tested": len(user_ids),
        "avg_per_user_ms": avg_ms,
        "total_ms": sum(durations),
    }


# ════════════════════════════════════════════════════════════════
# PENGUJIAN 5: SimulasiInteraksi
# ════════════════════════════════════════════════════════════════
def test_simulate_interactions(articles, n_users=608):
    """Replikasi ringkas algoritma simulate_clicks.py untuk mengukur runtime pembuatan data.

    Parameter disinkronkan dengan simulate_clicks.py:
      - 40% user aktif (15-40 klik), 60% pasif (5-15 klik)
      - 70% dari preferensi kategori, 20% dari artikel populer, ~10% acak
      - 35% klik lanjut jadi "read", 5% dari "read" lanjut jadi "share"
    """
    kategori_set = list({a[3] for a in articles}) or ["Umum"]
    arts_by_kat = defaultdict(list)
    for a in articles:
        arts_by_kat[a[3]].append(a)

    popular_ids = random.sample(
        [a[0] for a in articles], max(1, int(len(articles) * 0.2))
    )

    with Timer() as t:
        total_interactions = 0
        for i in range(n_users):
            aktif = random.random() < 0.40   # 40% aktif — sesuai simulate_clicks.py
            n_klik = random.randint(15, 40) if aktif else random.randint(5, 15)
            prefs = random.sample(kategori_set, min(len(kategori_set), random.randint(1, 3)))

            n_pref = int(n_klik * 0.70)      # 70% dari preferensi kategori
            n_pop  = int(n_klik * 0.20)      # 20% dari artikel populer
            n_acak = n_klik - n_pref - n_pop # ~10% acak

            pool_pref = []
            for k in prefs:
                pool_pref.extend(arts_by_kat[k])
            chosen = (
                random.sample(pool_pref, min(n_pref, len(pool_pref))) if pool_pref else []
            )
            chosen += random.sample(popular_ids, min(n_pop, len(popular_ids)))
            chosen_ids = set(c if isinstance(c, int) else c[0] for c in chosen)

            for _ in range(n_acak):
                chosen_ids.add(random.choice(articles)[0])

            for _ in chosen_ids:
                total_interactions += 1          # event "click"
                if random.random() < 0.35:
                    total_interactions += 1      # event "read"
                    if random.random() < 0.05:
                        total_interactions += 1  # event "share"

    return {
        "nama": "SimulasiInteraksi",
        "n_user": n_users,
        "n_interactions_generated": total_interactions,
        "total_ms": t.elapsed_ms,
    }

# ════════════════════════════════════════════════════════════════
# PENGUJIAN 7: Crawling Throughput
# ════════════════════════════════════════════════════════════════
def test_crawling_throughput(portal_name="Detik.com"):
    """
    Mengukur throughput crawling (artikel/jam) untuk satu portal.
    Memerlukan koneksi internet dan portal aktif.
    """
    try:
        from backend.crawler.engine import HybridCrawlerEngine, get_portal_configs
    except ImportError:
        return {
            "nama": "Crawling Throughput",
            "status": "SKIP - engine tidak ditemukan",
        }

    cfgs = get_portal_configs()
    target = next((c for c in cfgs if c["name"] == portal_name), None)
    if not target:
        return {
            "nama": "Crawling Throughput",
            "status": f"SKIP - portal '{portal_name}' tidak ditemukan",
        }

    engine = HybridCrawlerEngine()
    print(f"    Crawling portal '{portal_name}'...")
    try:
        with Timer() as t:
            articles = engine.crawl_portal(target, kategori_filter="Semua")
    except Exception as e:
        return {
            "nama": "Crawling Throughput",
            "status": f"ERROR - {str(e)}",
        }

    elapsed_ms = t.elapsed_ms
    n = len(articles)
    throughput_per_sec = n / (elapsed_ms / 1000) if elapsed_ms > 0 else 0

    return {
        "nama": "Crawling Throughput",
        "portal": portal_name,
        "n_articles": n,
        "total_ms": elapsed_ms,
        "throughput_articles_per_sec": throughput_per_sec,
        "throughput_articles_per_hour": throughput_per_sec * 3600,
    }


# ════════════════════════════════════════════════════════════════
# PENGUJIAN 8: API Response Time & Konkurensi
# ════════════════════════════════════════════════════════════════
def test_api_response_time(endpoint="/api/articles", n_requests=10, concurrent=5):
    """
    Mengukur waktu respons endpoint API dengan beberapa request paralel.
    Memerlukan server Flask berjalan di localhost:5000.
    """
    try:
        import requests
        from concurrent.futures import ThreadPoolExecutor, as_completed
    except ImportError:
        return {
            "nama": "API Response Time",
            "status": "SKIP - requests atau ThreadPoolExecutor tidak tersedia",
        }

    url = f"http://localhost:5000{endpoint}"
    try:
        # Cek koneksi dulu
        requests.get(url, timeout=2)
    except:
        return {
            "nama": "API Response Time",
            "status": "SKIP - server tidak merespons (pastikan Flask running di localhost:5000)",
        }

    def single_request():
        start = time.perf_counter()
        resp = requests.get(url, timeout=5)
        elapsed_ms = (time.perf_counter() - start) * 1000
        return elapsed_ms, resp.status_code

    durations = []
    status_codes = []
    with ThreadPoolExecutor(max_workers=concurrent) as executor:
        futures = [executor.submit(single_request) for _ in range(n_requests)]
        for f in as_completed(futures):
            dur, code = f.result()
            durations.append(dur)
            status_codes.append(code)

    avg_ms = sum(durations) / len(durations) if durations else 0
    min_ms = min(durations) if durations else 0
    max_ms = max(durations) if durations else 0

    return {
        "nama": "API Response Time",
        "endpoint": endpoint,
        "n_requests": n_requests,
        "concurrent": concurrent,
        "avg_ms": avg_ms,
        "min_ms": min_ms,
        "max_ms": max_ms,
        "status_ok": all(c == 200 for c in status_codes),
    }


# ════════════════════════════════════════════════════════════════
# PENGUJIAN 9: Latensi End-to-End (dengan data aktual)
# ════════════════════════════════════════════════════════════════
def test_end_to_end_latency(results_hash_dedup, results_simhash, crawl_result=None):
    """
    Estimasi latensi end-to-end: crawl + index + simpan.
    Jika crawl_result tersedia (dari test_crawling_throughput),
    gunakan data aktual. Jika tidak, gunakan estimasi default.
    """
    # Waktu indexing per artikel (hash + simhash)
    avg_hash_ms = results_hash_dedup.get("avg_per_item_ms", 0)
    avg_simhash_ms = results_simhash.get("avg_per_item_ms_sampled", 0)
    index_time_per_article_ms = avg_hash_ms + avg_simhash_ms

    # Cek apakah ada hasil crawling aktual
    if crawl_result and "n_articles" in crawl_result and crawl_result["n_articles"] > 0:
        # Gunakan data aktual dari crawling
        crawl_total_ms = crawl_result.get("total_ms", 0)
        crawl_n_articles = crawl_result.get("n_articles", 1)
        crawl_time_per_article_ms = crawl_total_ms / crawl_n_articles
        sumber = f"aktual dari {crawl_result.get('portal', 'portal')} ({crawl_n_articles} artikel)"
    else:
        # Fallback ke estimasi default
        crawl_time_per_200_articles_ms = 12000  # 12 detik
        crawl_time_per_article_ms = crawl_time_per_200_articles_ms / 200
        sumber = "estimasi (12 detik / 200 artikel)"

    total_per_article_ms = crawl_time_per_article_ms + index_time_per_article_ms

    return {
        "nama": "Latensi End-to-End",
        "sumber_crawl": sumber,
        "crawl_time_per_article_ms": crawl_time_per_article_ms,
        "index_time_per_article_ms": index_time_per_article_ms,
        "estimated_total_per_article_ms": total_per_article_ms,
    }

# ════════════════════════════════════════════════════════════════
# MAIN
# ════════════════════════════════════════════════════════════════
def main():
    print("=" * 64)
    print("  NewsAgg — Pengujian Runtime Algoritma Utama")
    print("=" * 64)

    print("\n[1] Memuat data dari database...")
    articles, interactions = load_data_from_db()

    using_synthetic = False
    if articles is None:
        using_synthetic = True
        articles, interactions = generate_synthetic_data()

    print(f"    Sumber data : {'SINTETIS (fallback)' if using_synthetic else 'PostgreSQL (data nyata)'}")
    print(f"    Artikel     : {len(articles):,}")
    print(f"    Interaksi   : {len(interactions):,}")

    results = []

    print("\n[2] Menguji Dedup url_hash / content_hash...")
    r1 = test_hash_dedup(articles)
    results.append(r1)
    print(f"    Rata-rata per artikel : {fmt_ms(r1['avg_per_item_ms'])}")
    print(f"    Total ({r1['n']} artikel)  : {fmt_ms(r1['total_ms'])}")

    print("\n[3] Menguji Dedup SimHash (near-duplicate)...")
    print("    (diuji pada sampel, lalu diekstrapolasi O(n^2) ke ukuran penuh)")
    r2 = test_simhash_dedup(articles, sample_size=min(500, len(articles)))
    results.append(r2)
    print(f"    Sampel uji            : {r2['n_sampled']} artikel")
    print(f"    Waktu sampel          : {fmt_ms(r2['total_ms_sampled'])}")
    print(f"    Estimasi skala penuh  : {fmt_ms(r2['extrapolated_total_ms_full'])} ({r2['n_full_estimate']} artikel)")

    print("\n[4] Menguji BangunModel (CF build)...")
    r3, user_item, item_users = test_cf_build(interactions)
    results.append(r3)
    print(f"    Bangun matriks user-item : {fmt_ms(r3['matrix_build_ms'])}")
    print(f"    Hitung similarity        : {fmt_ms(r3['similarity_compute_ms'])}")
    print(f"    Total                    : {fmt_ms(r3['total_ms'])}")
    print(f"    ({r3['n_user']} user, {r3['n_item']} item, {r3['n_sim_pairs']} pasangan similar)")

    print("\n[5] Menguji Rekomendasikan (Top-10 per user)...")
    r4 = test_recommend(user_item, item_users, top_n=10, max_users_to_test=50)
    results.append(r4)
    print(f"    Rata-rata per user : {fmt_ms(r4['avg_per_user_ms'])}")
    print(f"    ({r4['n_user_tested']} user diuji)")

    print("\n[6] Menguji SimulasiInteraksi...")
    r5 = test_simulate_interactions(articles, n_users=608)
    results.append(r5)
    print(f"    Total waktu : {fmt_ms(r5['total_ms'])}")
    print(f"    ({r5['n_user']} user, {r5['n_interactions_generated']} interaksi dihasilkan)")

        # ── Pengujian tambahan ──
    print("\n[7] Menguji Crawling Throughput (hanya jika engine tersedia)...")
    r7 = test_crawling_throughput("Detik.com")
    results.append(r7)
    if r7.get("status", "").startswith("SKIP"):
        print(f"    {r7['status']}")
    else:
        print(f"    Portal      : {r7['portal']}")
        print(f"    Artikel     : {r7['n_articles']}")
        print(f"    Waktu       : {fmt_ms(r7['total_ms'])}")
        print(f"    Throughput  : {r7['throughput_articles_per_hour']:.0f} artikel/jam")

    print("\n[8] Menguji API Response Time (server harus berjalan)...")
    r8 = test_api_response_time("/api/articles", n_requests=10, concurrent=5)
    results.append(r8)
    if r8.get("status", "").startswith("SKIP"):
        print(f"    {r8['status']}")
    else:
        print(f"    Endpoint    : {r8['endpoint']}")
        print(f"    Rata-rata   : {fmt_ms(r8['avg_ms'])}")
        print(f"    Min-Max     : {fmt_ms(r8['min_ms'])} – {fmt_ms(r8['max_ms'])}")

    print("\n[9] Estimasi Latensi End-to-End...")
    r9 = test_end_to_end_latency(r1, r2, crawl_result=r7)  # ← kirim r7 (hasil crawling)
    results.append(r9)
    print(f"    Sumber crawling : {r9['sumber_crawl']}")
    print(f"    Crawl per artikel : {fmt_ms(r9['crawl_time_per_article_ms'])}")
    print(f"    Index per artikel : {fmt_ms(r9['index_time_per_article_ms'])}")
    print(f"    Total per artikel : {fmt_ms(r9['estimated_total_per_article_ms'])}")

    # ── Ringkasan tabel untuk Bab IV ──────────────────────────
    print("\n" + "=" * 64)
    print("  RINGKASAN — siap disalin ke tabel Bab IV skripsi")
    print("=" * 64)
    print(f"{'Algoritma':<38} {'Runtime':>15}")
    print("-" * 64)
    print(f"{'Dedup url_hash/content_hash (per artikel)':<38} {fmt_ms(r1['avg_per_item_ms']):>15}")
    print(f"{'Dedup SimHash (estimasi skala penuh)':<38} {fmt_ms(r2['extrapolated_total_ms_full']):>15}")
    print(f"{'BangunModel CF (total build)':<38} {fmt_ms(r3['total_ms']):>15}")
    print(f"{'Rekomendasikan (per user)':<38} {fmt_ms(r4['avg_per_user_ms']):>15}")
    print(f"{'SimulasiInteraksi (608 user)':<38} {fmt_ms(r5['total_ms']):>15}")
    print("=" * 64)

    # ── Export hasil ───────────────────────────────────────────
    out_dir = os.path.dirname(__file__) or "."
    json_path = os.path.join(out_dir, "runtime_results.json")
    csv_path = os.path.join(out_dir, "runtime_results.csv")

    with open(json_path, "w", encoding="utf-8") as f:
        json.dump({
            "tested_at": datetime.utcnow().isoformat(),
            "using_synthetic_data": using_synthetic,
            "n_articles": len(articles),
            "n_interactions": len(interactions),
            "results": results,
        }, f, indent=2, ensure_ascii=False)

    with open(csv_path, "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["algoritma", "metrik", "nilai"])
        for r in results:
            for k, v in r.items():
                if k != "nama":
                    w.writerow([r["nama"], k, v])

    print(f"\n[OK] Hasil lengkap diekspor ke:")
    print(f"     {json_path}")
    print(f"     {csv_path}")
    if using_synthetic:
        print("\n[!] PERHATIAN: pengujian ini menggunakan DATA SINTETIS karena")
        print("    database tidak dapat diakses. Untuk hasil yang valid dipakai")
        print("    di skripsi, jalankan script ini di komputer yang terhubung")
        print("    ke PostgreSQL berisi data NewsAgg yang sesungguhnya.")


if __name__ == "__main__":
    main()
