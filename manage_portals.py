"""
Script manajemen portal — hapus portal bermasalah, ganti dengan yang baru.
Jalankan: python manage_portals.py
"""
import sys, os, json
sys.path.insert(0, os.path.dirname(__file__))
try:
    from dotenv import load_dotenv; load_dotenv()
except ImportError:
    pass

# Portal yang diblokir robots.txt atau tidak bisa di-crawl → hapus
REMOVE = [
    "https://www.tribunnews.com",   # Diblokir sebelumnya
    "https://www.cnnindonesia.com", # Diblokir robots.txt
    "https://www.tempo.co",         # Diblokir robots.txt
    "https://www.suara.com",        # Diblokir robots.txt
]

# Portal pengganti — sudah dicek ramah crawler
ADD = [
    {
        "name": "Sindonews",
        "base_url": "https://nasional.sindonews.com",
        "domain": "sindonews.com",
        "urls": [
            "https://nasional.sindonews.com",
            "https://ekbis.sindonews.com",
            "https://tekno.sindonews.com"
        ],
        "selectors": {
            "article": "div.homelist-item, article, div[class*='list-item']",
            "title":   "h2, h3, h4",
            "link":    "a[href]",
            "date":    "span.date, time, span[class*='date']",
            "summary": "p",
            "category":"span[class*='label'], span[class*='tag']"
        }
    },
    {
        "name": "JPNN",
        "base_url": "https://www.jpnn.com",
        "domain": "jpnn.com",
        "urls": [
            "https://www.jpnn.com",
            "https://www.jpnn.com/ekonomi",
            "https://www.jpnn.com/teknologi"
        ],
        "selectors": {
            "article": "div.item-berita, article, div[class*='article']",
            "title":   "h2, h3",
            "link":    "a[href]",
            "date":    "span.date, time, span[class*='date']",
            "summary": "p",
            "category":"span[class*='label'], span[class*='cat']"
        }
    },
    {
        "name": "Medcom",
        "base_url": "https://www.medcom.id",
        "domain": "medcom.id",
        "urls": [
            "https://www.medcom.id",
            "https://www.medcom.id/ekonomi",
            "https://www.medcom.id/teknologi"
        ],
        "selectors": {
            "article": "div.col-article, article, div[class*='article']",
            "title":   "h2, h3",
            "link":    "a[href]",
            "date":    "time, span[class*='date'], div[class*='date']",
            "summary": "p",
            "category":"span[class*='label'], a[class*='tag']"
        }
    },
]


def main():
    print("=" * 55)
    print("  NewsAgg — Update Portal")
    print("=" * 55)

    from backend.db.connection import check_connection, get_db
    from backend.db.models import Portal

    if not check_connection():
        print("[ERROR] Tidak dapat terhubung ke PostgreSQL!")
        sys.exit(1)

    with get_db() as db:
        # ── Hapus portal bermasalah ────────────────────────────
        print("\n[1] Menghapus portal yang diblokir robots.txt / tidak bisa crawl...")
        for base_url in REMOVE:
            p = db.query(Portal).filter_by(base_url=base_url).first()
            if p:
                db.delete(p)
                print(f"  - Dihapus: {p.name}")
            else:
                print(f"  - Tidak ditemukan: {base_url} (mungkin sudah dihapus)")

        # ── Tambah portal pengganti ────────────────────────────
        print("\n[2] Menambah portal pengganti...")
        added = 0
        for p_data in ADD:
            existing = db.query(Portal).filter_by(base_url=p_data["base_url"]).first()
            if existing:
                print(f"  - {p_data['name']} sudah ada, skip.")
                continue
            db.add(Portal(
                name         = p_data["name"],
                base_url     = p_data["base_url"],
                domain       = p_data["domain"],
                crawl_policy = "allowed",
                active       = True,
                urls         = json.dumps(p_data["urls"]),
                selectors    = json.dumps(p_data["selectors"]),
            ))
            added += 1
            print(f"  + Ditambahkan: {p_data['name']}")

        # ── Tampilkan daftar akhir ─────────────────────────────
        print("\n[3] Portal aktif sekarang:")
        all_portals = db.query(Portal).filter_by(active=True).order_by(Portal.id).all()
        for i, p in enumerate(all_portals, 1):
            print(f"  {i:2d}. {p.name} ({p.domain})")

    print(f"\n[OK] Selesai! {added} portal baru ditambahkan.")
    print("     Restart server: python run.py\n")


if __name__ == "__main__":
    main()
