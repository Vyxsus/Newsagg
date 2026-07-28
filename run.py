"""Entry point NewsAgg — jalankan: python run.py"""
import sys, os
from backend.app import app, scheduler, cf_scheduler
from backend.scheduler.cf_scheduler import load_cf_config as cf_sched_load
sys.path.insert(0, os.path.dirname(__file__))

try:
    from dotenv import load_dotenv; load_dotenv()
except ImportError:
    pass

from backend.db.connection import check_connection, init_db
from backend.scheduler.scheduler import load_config as sched_load
from backend.app import app, scheduler

if __name__ == "__main__":
    print("=" * 52)
    print("  NewsAgg — Prototipe Agregator Berita")
    print("  Sprint 1: Crawling + Indexing + Log Klik")
    print("=" * 52)
    print("\n[1] Cek koneksi PostgreSQL...")
    if not check_connection():
        print("\n[ERROR] Tidak dapat terhubung ke PostgreSQL!")
        print("  set DATABASE_URL=postgresql://postgres:PASSWORD@localhost:5432/newsagg")
        sys.exit(1)
    print("  OK - Koneksi berhasil")
    print("[2] Inisialisasi tabel database...")
    init_db()
    print("  OK - Tabel siap")
    cfg = sched_load()
    if cfg.get("enabled"):
        print(f"[3] Memulai scheduler (setiap {cfg['interval_hours']} jam)...")
        scheduler.start()
        print("  OK - Scheduler aktif")
    else:
        print("[3] Scheduler nonaktif (aktifkan di tab Scheduler)")
    cf_cfg = cf_sched_load()
    if cf_cfg.get("enabled"):
        print(f"[4] CF Scheduler aktif — setiap {cf_cfg['interval_hours']} jam")
        cf_scheduler.start()
    else:
        print("[4] CF Scheduler nonaktif (atur di tab CF Model)")
    print("\n  Admin  ->  http://localhost:5000/")
    print("  User   ->  http://localhost:5000/user")
    print("\nTekan Ctrl+C untuk berhenti.\n")
    app.run(debug=False, port=5000, host="0.0.0.0")
