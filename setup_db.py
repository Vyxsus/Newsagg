import sys, os
sys.path.insert(0, os.path.dirname(__file__))
try:
    from dotenv import load_dotenv; load_dotenv()
except ImportError: pass

def main():
    print("="*55)
    print("  NewsAgg — Setup Database (Hybrid Crawler)")
    print("="*55)
    from backend.db.connection import check_connection, init_db, get_db
    print("\n[1] Cek koneksi PostgreSQL...")
    if not check_connection():
        print("\n[ERROR] Tidak dapat terhubung ke PostgreSQL!")
        print("  Pastikan DATABASE_URL sudah di set dengan benar.")
        sys.exit(1)
    print("  OK - Koneksi berhasil")
    print("[2] Membuat tabel...")
    init_db()
    print("  OK - Semua tabel dibuat")
    print("[3] Membuat admin user default...")
    import bcrypt
    from backend.db.models import AdminUser
    with get_db() as db:
        existing = db.query(AdminUser).filter_by(username="admin").first()
        if not existing:
            hashed = bcrypt.hashpw("admin123".encode(), bcrypt.gensalt()).decode()
            db.add(AdminUser(username="admin", password=hashed))
            print("  + Admin user dibuat: username=admin, password=admin123")
            print("  ! PENTING: Ganti password setelah login pertama!")
        else:
            print("  - Admin user sudah ada, skip.")
    print("\n[4] Info portal:")
    from backend.crawler.engine import get_portal_configs
    cfgs = get_portal_configs()
    for i, c in enumerate(cfgs, 1):
        rss  = len(c.get("rss_urls",[]))
        html = len(c.get("html_urls",[]))
        print(f"  {i:2d}. {c['name']:<20} method={c['method']:<7} rss={rss} html={html}")
    print(f"\n  Total: {len(cfgs)} portal siap dicrawl")
    print("\n[OK] Setup selesai!")
    print("     Jalankan server: python run.py")

if __name__ == "__main__":
    main()