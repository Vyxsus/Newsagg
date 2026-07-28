"""Scheduler crawling otomatis — pakai HybridCrawlerEngine."""
import threading, json, os, time, uuid
from datetime import datetime, timedelta

SCHEDULER_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "scheduler.json")
DEFAULT_CONFIG = {"enabled":False,"interval_hours":6,"kategori":"Semua","sources":[],
                  "last_run":None,"next_run":None,"run_count":0,"last_result":None}

def load_config():
    if not os.path.exists(SCHEDULER_PATH): return DEFAULT_CONFIG.copy()
    try:
        with open(SCHEDULER_PATH, encoding="utf-8") as f:
            cfg = json.load(f)
            for k,v in DEFAULT_CONFIG.items(): cfg.setdefault(k,v)
            return cfg
    except: return DEFAULT_CONFIG.copy()

def save_config(cfg):
    os.makedirs(os.path.dirname(SCHEDULER_PATH), exist_ok=True)
    with open(SCHEDULER_PATH,"w",encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)

def _next_run_str(hours):
    return (datetime.now()+timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")

class NewsScheduler:
    def __init__(self, crawl_status, status_lock):
        self._status   = crawl_status
        self._lock     = status_lock
        self._stop     = threading.Event()
        self._thread   = None
        self._run_lock = threading.Lock()

    def start(self):
        with self._run_lock:
            if self._thread and self._thread.is_alive(): return
            self._stop.clear()
            self._thread = threading.Thread(target=self._loop, daemon=True)
            self._thread.start()

    def stop(self): self._stop.set()

    def update_config(self, **kwargs):
        cfg = load_config(); cfg.update(kwargs)
        if kwargs.get("enabled", cfg["enabled"]):
            cfg["next_run"] = _next_run_str(cfg["interval_hours"])
        else:
            cfg["next_run"] = None
        save_config(cfg)
        self.stop(); time.sleep(0.3)
        if cfg["enabled"]: self.start()
        return cfg

    def get_status(self):
        cfg = load_config()
        countdown = None
        if cfg["enabled"] and cfg["next_run"]:
            try:
                delta = datetime.strptime(cfg["next_run"],"%Y-%m-%d %H:%M:%S") - datetime.now()
                secs  = max(0,int(delta.total_seconds()))
                h,r   = divmod(secs,3600); m,s = divmod(r,60)
                countdown = f"{h:02d}:{m:02d}:{s:02d}"
            except: pass
        return {**cfg,"countdown":countdown,"is_running_now":self._status.get("running",False)}

    def _loop(self):
        while not self._stop.is_set():
            cfg = load_config()
            if not cfg["enabled"]: self._stop.wait(30); continue
            should_run = False
            if cfg["next_run"]:
                try: should_run = datetime.now() >= datetime.strptime(cfg["next_run"],"%Y-%m-%d %H:%M:%S")
                except: should_run = True
            else:
                cfg["next_run"] = _next_run_str(cfg["interval_hours"]); save_config(cfg)
            if should_run: self._do_crawl(cfg)
            else: self._stop.wait(30)

    def _do_crawl(self, cfg):
        from ..crawler.engine import HybridCrawlerEngine, get_portal_configs
        from ..indexer.indexer import ArticleIndexer
        from ..db.connection import get_db
        from ..db.models import Portal, CrawlLog

        if self._status.get("running"):
            cfg["next_run"] = (datetime.now()+timedelta(minutes=5)).strftime("%Y-%m-%d %H:%M:%S")
            save_config(cfg); self._stop.wait(30); return

        # Ambil portal configs
        all_cfgs = get_portal_configs()
        with get_db() as db:
            inactive = {p.domain for p in db.query(Portal).filter_by(active=False).all()}
        selected = cfg.get("sources", [])
        portal_cfgs = [
            c for c in all_cfgs
            if c["domain"] not in inactive
            and (not selected or c["name"] in selected)
        ]
        if not portal_cfgs:
            cfg["next_run"] = _next_run_str(cfg["interval_hours"]); save_config(cfg); return

        now_str = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cfg.update({"last_run":now_str,"next_run":_next_run_str(cfg["interval_hours"]),
                    "run_count":cfg.get("run_count",0)+1,"last_result":"Sedang berjalan..."})
        save_config(cfg)

        with self._lock:
            self._status.update({
                "running":True,"progress":[],"total":len(portal_cfgs),
                "done":0,"current_article":"","current_source":"",
                "article_progress":[0,0],"triggered_by":"scheduler",
            })

        eng     = HybridCrawlerEngine()
        idxr    = ArticleIndexer()
        total_new = 0
        try:
            for pcfg in portal_cfgs:
                if self._stop.is_set(): break
                self._status["current_source"] = pcfg["name"]
                self._status["progress"].append({"source":pcfg["name"],"name":pcfg["name"],"status":"crawling","count":0})
                run_id = str(uuid.uuid4())[:8]

                def on_progress(idx,total,title):
                    self._status["article_progress"] = [idx,total]
                    self._status["current_article"]  = title[:60]

                articles  = eng.crawl_portal(pcfg, kategori_filter=cfg.get("kategori","Semua"), progress_cb=on_progress)
                result    = idxr.bulk_index(articles, run_id=run_id)
                new_count = result["stats"].get("new",0)
                total_new += new_count

                for p in self._status["progress"]:
                    if p["source"]==pcfg["name"]: p["status"]="done"; p["count"]=new_count
                self._status["done"] += 1

                with get_db() as db:
                    db.add(CrawlLog(run_id=run_id,portal_name=pcfg["name"],
                                    finished_at=datetime.utcnow(),articles_found=len(articles),
                                    articles_new=new_count,
                                    articles_dup=result["stats"].get("duplicate",0)+result["stats"].get("near_dup",0),
                                    articles_error=result["stats"].get("error",0),status="done"))
        finally:
            self._status.update({"running":False,"current_article":"","article_progress":[0,0]})

        cfg = load_config()
        cfg["last_result"] = f"+{total_new} artikel baru ({datetime.now().strftime('%H:%M')})"
        save_config(cfg)
