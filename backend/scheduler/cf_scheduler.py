"""
Scheduler untuk Collaborative Filtering — mirip scheduler crawling.
Menjalankan build/rebuild model CF secara otomatis berdasarkan interval.
"""
import threading, json, os, time
from datetime import datetime, timedelta

CF_SCHEDULER_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "cf_scheduler.json")

DEFAULT_CF_CONFIG = {
    "enabled": False,
    "interval_hours": 6,
    "last_run": None,
    "next_run": None,
    "run_count": 0,
    "last_result": None,
    "auto_rebuild_after_crawl": True,   # rebuild otomatis setelah crawling selesai
}


def load_cf_config():
    if not os.path.exists(CF_SCHEDULER_PATH):
        return DEFAULT_CF_CONFIG.copy()
    try:
        with open(CF_SCHEDULER_PATH, encoding="utf-8") as f:
            cfg = json.load(f)
            for k, v in DEFAULT_CF_CONFIG.items():
                cfg.setdefault(k, v)
            return cfg
    except Exception:
        return DEFAULT_CF_CONFIG.copy()


def save_cf_config(cfg):
    os.makedirs(os.path.dirname(CF_SCHEDULER_PATH), exist_ok=True)
    with open(CF_SCHEDULER_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)


def _next_run_str(hours):
    return (datetime.now() + timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")


class CFScheduler:
    """
    Scheduler background thread untuk rebuild model CF secara periodik.
    Pola sama seperti NewsScheduler di scheduler.py.
    """
    def __init__(self, cf_status, status_lock):
        self._status   = cf_status     # dict shared status (mirip crawl_status)
        self._lock     = status_lock
        self._stop     = threading.Event()
        self._thread   = None
        self._run_lock = threading.Lock()

    def start(self):
        with self._run_lock:
            if self._thread and self._thread.is_alive():
                return
            self._stop.clear()
            self._thread = threading.Thread(target=self._loop, daemon=True)
            self._thread.start()

    def stop(self):
        self._stop.set()

    def update_config(self, **kwargs):
        cfg = load_cf_config()
        cfg.update(kwargs)
        if kwargs.get("enabled", cfg["enabled"]):
            cfg["next_run"] = _next_run_str(cfg["interval_hours"])
        else:
            cfg["next_run"] = None
        save_cf_config(cfg)
        self.stop()
        time.sleep(0.3)
        if cfg["enabled"]:
            self.start()
        return cfg

    def get_status(self):
        cfg = load_cf_config()
        countdown = None
        if cfg["enabled"] and cfg["next_run"]:
            try:
                delta = datetime.strptime(cfg["next_run"], "%Y-%m-%d %H:%M:%S") - datetime.now()
                secs = max(0, int(delta.total_seconds()))
                h, r = divmod(secs, 3600)
                m, s = divmod(r, 60)
                countdown = f"{h:02d}:{m:02d}:{s:02d}"
            except Exception:
                pass
        return {**cfg, "countdown": countdown, "is_running_now": self._status.get("running", False)}

    def trigger_rebuild_now(self, reason="manual"):
        """Dipanggil scheduler internal ATAU dipanggil dari luar (misal setelah crawling selesai)."""
        if self._status.get("running"):
            return  # sudah ada proses build berjalan, skip
        threading.Thread(target=self._do_build, args=(reason,), daemon=True).start()

    def _loop(self):
        while not self._stop.is_set():
            cfg = load_cf_config()
            if not cfg["enabled"]:
                self._stop.wait(30)
                continue
            should_run = False
            if cfg["next_run"]:
                try:
                    should_run = datetime.now() >= datetime.strptime(cfg["next_run"], "%Y-%m-%d %H:%M:%S")
                except Exception:
                    should_run = True
            else:
                cfg["next_run"] = _next_run_str(cfg["interval_hours"])
                save_cf_config(cfg)

            if should_run:
                self._do_build("scheduler")
                cfg = load_cf_config()
                cfg["next_run"] = _next_run_str(cfg["interval_hours"])
                save_cf_config(cfg)
            else:
                self._stop.wait(30)

    def _do_build(self, reason="manual"):
        from ..cf.cf_engine import get_cf_model

        with self._lock:
            self._status["running"] = True

        try:
            cf = get_cf_model()
            result = cf.build_from_db()
            saved = 0
            if result.get("status") == "ok":
                saved = cf.save_to_db(top_n=10)

            cfg = load_cf_config()
            now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            cfg["last_run"] = now_str
            cfg["run_count"] = cfg.get("run_count", 0) + 1
            if result.get("status") == "ok":
                cfg["last_result"] = f"OK — {result['n_users']} user, {result['n_items']} item, {saved} rekomendasi (trigger: {reason})"
            else:
                cfg["last_result"] = f"Gagal — {result.get('message', result.get('status'))} (trigger: {reason})"
            save_cf_config(cfg)
        except Exception as e:
            cfg = load_cf_config()
            cfg["last_result"] = f"Error — {e} (trigger: {reason})"
            save_cf_config(cfg)
        finally:
            with self._lock:
                self._status["running"] = False