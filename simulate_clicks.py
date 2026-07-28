"""
Simulasi Data Klik — 200 User
Menghasilkan data interaksi realistis untuk training Collaborative Filtering.

Pola simulasi:
  - Setiap user punya 1-3 kategori favorit (preferensi berbeda)
  - User aktif klik 15-40 artikel, user pasif 5-15 artikel
  - Artikel populer diklik lebih banyak (power-law distribution)
  - Timestamp tersebar dalam 30 hari terakhir (untuk time-decay)
  - 35% klik berlanjut jadi "read" (indikasi baca tuntas)
  - 5% dari "read" berlanjut jadi "share" (berbagi artikel)

Jalankan: python simulate_clicks.py
"""
import sys, os, random, hashlib
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(__file__))
try:
    from dotenv import load_dotenv; load_dotenv()
except ImportError:
    pass

# ── Konfigurasi simulasi ──────────────────────────────────────
N_USERS          = 200
DAYS_BACK        = 30    # Sebar interaksi dalam 30 hari terakhir
ACTIVE_USER_PCT  = 0.4   # 40% user aktif (15-40 klik)
PASSIVE_USER_PCT = 0.6   # 60% user pasif (5-15 klik)
READ_PROBABILITY = 0.35  # 35% klik berlanjut jadi "read"

KATEGORI_LIST = [
    "Nasional","Politik","Ekonomi","Bisnis","Teknologi",
    "Olahraga","Hiburan","Internasional","Kesehatan",
    "Pendidikan","Otomotif","Gaya Hidup","Hukum",
]

# Bobot popularitas kategori (sesuai tren berita Indonesia)
KATEGORI_WEIGHTS = {
    "Nasional": 0.20, "Politik": 0.12, "Ekonomi": 0.10, "Bisnis": 0.08,
    "Teknologi": 0.09, "Olahraga": 0.10, "Hiburan": 0.08,
    "Internasional": 0.07, "Kesehatan": 0.06, "Pendidikan": 0.04,
    "Otomotif": 0.03, "Gaya Hidup": 0.02, "Hukum": 0.01,
}


def generate_pseudonym(i: int) -> str:
    """Buat pseudonym anonim yang unik per user."""
    raw = f"simuser_{i}_{random.randint(10000,99999)}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def pick_user_preferences() -> list:
    """Pilih 1-3 kategori favorit untuk satu user."""
    n_prefs = random.choices([1, 2, 3], weights=[0.3, 0.5, 0.2])[0]
    return random.choices(KATEGORI_LIST,
                          weights=[KATEGORI_WEIGHTS.get(k, 0.05) for k in KATEGORI_LIST],
                          k=n_prefs)


def random_timestamp(days_back: int) -> datetime:
    """Buat timestamp acak dalam N hari terakhir."""
    seconds_back = random.randint(0, days_back * 86400)
    return datetime.utcnow() - timedelta(seconds=seconds_back)


def main():
    print("=" * 55)
    print("  NewsAgg — Simulasi Data Klik 200 User")
    print("=" * 55)

    from backend.db.connection import check_connection, get_db
    from backend.db.models import Article, User, Interaction

    print("\n[1] Cek koneksi...")
    if not check_connection():
        print("[ERROR] Tidak dapat terhubung ke PostgreSQL!")
        sys.exit(1)
    print("  OK")

    with get_db() as db:
        # Ambil artikel yang memiliki isi (body) agar konsisten dengan halaman user
        articles = db.query(Article).filter(
            Article.body.isnot(None), Article.body != ""
        ).all()
        if not articles:
            print("[ERROR] Tidak ada artikel berisi di database!")
            print("  Jalankan crawling dulu sebelum simulasi.")
            sys.exit(1)
        print(f"[2] Artikel berisi tersedia: {len(articles)}")

        # Kelompokkan artikel per kategori untuk memudahkan pemilihan
        arts_by_kat = {}
        for a in articles:
            kat = a.category or "Umum"
            arts_by_kat.setdefault(kat, []).append(a)

        print(f"     Kategori tersedia: {list(arts_by_kat.keys())}")

        # Artikel populer — 20% artikel teratas mendapat 60% klik (power-law)
        all_ids     = [a.id for a in articles]
        n_popular   = max(1, int(len(all_ids) * 0.20))
        popular_ids = random.sample(all_ids, n_popular)

        # Cek berapa user simulasi sudah ada
        # Sim-user pakai pseudonym SHA-256 hex 16 char; real-user pakai prefix "newsagg_"
        existing = db.query(User).filter(
            ~User.pseudonym.like('newsagg_%')
        ).count()
        if existing > 0:
            print(f"\n[!] Sudah ada {existing} user simulasi.")
            ans = input("     Tambah lagi atau skip? (tambah/skip): ").strip().lower()
            if ans == "skip":
                print("  Skip. Selesai.")
                return

        print(f"\n[3] Membuat {N_USERS} user simulasi...")
        total_interactions = 0
        users_created      = 0

        for i in range(N_USERS):
            pseudo = generate_pseudonym(i)

            # Skip jika sudah ada
            if db.query(User).filter_by(pseudonym=pseudo).first():
                continue

            user = User(pseudonym=pseudo, created_at=random_timestamp(DAYS_BACK))
            db.add(user)
            db.flush()  # Dapat user.id
            users_created += 1

            # Tentukan profil user
            is_active   = random.random() < ACTIVE_USER_PCT
            n_clicks    = random.randint(15, 40) if is_active else random.randint(5, 15)
            preferences = pick_user_preferences()

            # Pilih artikel yang akan diklik user ini
            clicked_ids = set()
            articles_to_click = []

            # 70% dari preferensi kategori user
            n_pref_clicks = int(n_clicks * 0.70)
            for _ in range(n_pref_clicks):
                # Pilih kategori sesuai preferensi
                kat = random.choice(preferences)
                pool = arts_by_kat.get(kat, articles)
                if not pool: pool = articles
                art = random.choice(pool)
                if art.id not in clicked_ids:
                    clicked_ids.add(art.id)
                    articles_to_click.append(art)

            # 20% dari artikel populer
            n_pop_clicks = int(n_clicks * 0.20)
            for _ in range(n_pop_clicks):
                aid = random.choice(popular_ids)
                if aid not in clicked_ids:
                    clicked_ids.add(aid)
                    art = next((a for a in articles if a.id == aid), None)
                    if art: articles_to_click.append(art)

            # 10% acak (eksplorasi)
            n_random = max(0, n_clicks - len(articles_to_click))
            for _ in range(n_random):
                art = random.choice(articles)
                if art.id not in clicked_ids:
                    clicked_ids.add(art.id)
                    articles_to_click.append(art)

            # Buat interaksi dengan timestamp tersebar
            for art in articles_to_click:
                ts = random_timestamp(DAYS_BACK)

                # Event: click
                db.add(Interaction(
                    user_id    = user.id,
                    article_id = art.id,
                    event_type = "click",
                    created_at = ts,
                ))
                total_interactions += 1

                # 35% kemungkinan lanjut "read"
                if random.random() < READ_PROBABILITY:
                    read_ts = ts + timedelta(seconds=random.randint(30, 300))
                    db.add(Interaction(
                        user_id    = user.id,
                        article_id = art.id,
                        event_type = "read",
                        created_at = read_ts,
                    ))
                    total_interactions += 1

                    # 5% kemungkinan lanjut "share" setelah selesai membaca
                    if random.random() < 0.05:
                        db.add(Interaction(
                            user_id    = user.id,
                            article_id = art.id,
                            event_type = "share",
                            created_at = read_ts + timedelta(seconds=random.randint(5, 60)),
                        ))
                        total_interactions += 1

            if (i + 1) % 50 == 0:
                print(f"  ... {i+1}/{N_USERS} user dibuat")

        print(f"\n  {users_created} user baru dibuat")
        print(f"  {total_interactions} interaksi dibuat")

    print("\n[OK] Simulasi selesai!")
    print(f"     Jalankan: python run.py\n")


if __name__ == "__main__":
    main()
