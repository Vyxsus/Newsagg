"""
Collaborative Filtering — Sprint 2
Algoritma: Item-based CF dengan implicit feedback (klik) + Time-decay

Alur sesuai proposal:
  1. Baca interaction log dari DB
  2. Terapkan time-decay: w(t) = exp(-λ * Δt_hari)
  3. Bangun user-item matrix berbobot
  4. Hitung item-item similarity (cosine similarity)
  5. Generate Top-N rekomendasi per user
  6. Simpan ke tabel recommendations (cache)

Metrik evaluasi (offline):
  - Precision@K
  - Recall@K
  - MAP (Mean Average Precision)
"""

import math
import json
import os
from datetime import datetime, timezone
from collections import defaultdict
from typing import Optional

# Konfigurasi path parameter
CF_PARAMS_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "cf_params.json")

DEFAULT_PARAMS = {
    "lambda_decay": 0.1,
    "click_weight": 1.0,
    "read_weight": 2.0,
    "share_weight": 3.0,
    "min_sim": 0.01,
    "top_n_default": 10,
}

def load_cf_params():
    if not os.path.exists(CF_PARAMS_PATH):
        return DEFAULT_PARAMS.copy()
    try:
        with open(CF_PARAMS_PATH, encoding="utf-8") as f:
            p = json.load(f)
            for k, v in DEFAULT_PARAMS.items():
                p.setdefault(k, v)
            return p
    except Exception:
        return DEFAULT_PARAMS.copy()

def save_cf_params(params):
    os.makedirs(os.path.dirname(CF_PARAMS_PATH), exist_ok=True)
    with open(CF_PARAMS_PATH, "w", encoding="utf-8") as f:
        json.dump(params, f, ensure_ascii=False, indent=2)

# Tetap sediakan TOP_N_DEFAULT untuk kompatibilitas
TOP_N_DEFAULT = DEFAULT_PARAMS["top_n_default"]


def time_decay_weight(created_at, lam=None):
    if lam is None:
        lam = load_cf_params()["lambda_decay"]

    # Normalisasi timezone — hilangkan tzinfo agar konsisten dengan datetime.utcnow()
    if created_at.tzinfo is not None:
        created_at = created_at.replace(tzinfo=None)

    days = max(0, (datetime.utcnow() - created_at).total_seconds() / 86400)
    return math.exp(-lam * days)


def event_weight(event_type, params=None):
    if params is None:
        params = load_cf_params()
    return {
        "click": params["click_weight"],
        "read": params["read_weight"],
        "share": params["share_weight"],
    }.get(event_type, 1.0)


def cosine_similarity(vec_a: dict, vec_b: dict) -> float:
    """Hitung cosine similarity antara dua vector sparse."""
    if not vec_a or not vec_b:
        return 0.0
    common = set(vec_a.keys()) & set(vec_b.keys())
    if not common:
        return 0.0
    dot    = sum(vec_a[k] * vec_b[k] for k in common)
    norm_a = math.sqrt(sum(v*v for v in vec_a.values()))
    norm_b = math.sqrt(sum(v*v for v in vec_b.values()))
    if norm_a == 0 or norm_b == 0:
        return 0.0
    return dot / (norm_a * norm_b)


class CollaborativeFilter:
    """
    Item-based Collaborative Filtering dengan time-decay.

    Struktur data utama:
      user_item[user_id][article_id] = bobot gabungan (decay * event_weight)
      item_users[article_id][user_id] = bobot
    """

    def __init__(self):
        self.user_item   = {}
        self.item_users  = {}
        self.item_sim    = {}
        self.last_built  = None
        self.last_params = DEFAULT_PARAMS.copy()

    # ── Build dari database ──────────────────────────────────────
    def build_from_db(self):
        from ..db.connection import get_db
        from ..db.models import Interaction, Article, User

        params = load_cf_params()
        print(f"[CF] Membangun model dengan parameter: {params}")

        with get_db() as db:
            rows = db.query(
                Interaction.user_id,
                Interaction.article_id,
                Interaction.event_type,
                Interaction.created_at,
            ).all()
            n_articles = db.query(Article).count()
            n_users    = db.query(User).count()

        print(f"[CF] {len(rows)} interaksi, {n_users} user, {n_articles} artikel")

        if not rows:
            # Kosongkan model
            self.user_item = {}
            self.item_users = {}
            self.item_sim = {}
            self.last_built = datetime.now(timezone.utc)
            self.last_params = params
            return {"status": "empty", "message": "Belum ada interaksi"}

        ui = defaultdict(lambda: defaultdict(float))
        iu = defaultdict(lambda: defaultdict(float))

        for user_id, article_id, event_type, created_at in rows:
            w = time_decay_weight(created_at, lam=params["lambda_decay"]) * event_weight(event_type, params)
            ui[user_id][article_id] += w
            iu[article_id][user_id] += w

        # Simpan hasil
        self.user_item = ui
        self.item_users = iu
        self.last_params = params

        # Hitung item-item similarity (cosine)
        print("[CF] Menghitung item similarity...")
        item_ids = list(self.item_users.keys())
        item_sim = {}

        for item_a in item_ids:
            sims = {}
            va   = self.item_users[item_a]
            for item_b in item_ids:
                if item_a == item_b:
                    continue
                s = cosine_similarity(va, self.item_users[item_b])
                if s > params["min_sim"]:
                    sims[item_b] = s
            if sims:
                item_sim[item_a] = sims

        self.item_sim = item_sim
        self.last_built = datetime.now(timezone.utc)
        n_pairs = sum(len(v) for v in self.item_sim.values())
        print(f"[CF] Model selesai — {len(item_ids)} item, {n_pairs} pasangan similar")

        return {
            "status":         "ok",
            "n_users":        len(self.user_item),
            "n_items":        len(self.item_users),
            "n_interactions": len(rows),
            "n_sim_pairs":    n_pairs,
            "built_at":       str(self.last_built),
        }

    # ── Rekomendasi per user ─────────────────────────────────────
    def recommend(self, user_id: int, top_n: int = TOP_N_DEFAULT,
                  exclude_seen: bool = True) -> list[dict]:
        """
        Hasilkan Top-N rekomendasi untuk satu user.

        Skor artikel kandidat = Σ (sim(item_i, item_j) * bobot_user_i)
        di mana item_j adalah artikel yang belum dilihat user,
        item_i adalah artikel yang sudah dilihat user.
        """
        if user_id not in self.user_item:
            return []

        seen    = set(self.user_item[user_id].keys())
        scores  = defaultdict(float)

        for seen_item, user_weight in self.user_item[user_id].items():
            if seen_item not in self.item_sim:
                continue
            for candidate_item, sim in self.item_sim[seen_item].items():
                if exclude_seen and candidate_item in seen:
                    continue
                scores[candidate_item] += sim * user_weight

        if not scores:
            return []

        # Urutkan berdasarkan skor tertinggi
        ranked = sorted(scores.items(), key=lambda x: x[1], reverse=True)[:top_n]
        return [{"article_id": aid, "score": round(score, 4)} for aid, score in ranked]

    # ── Rekomendasi berbasis item (item-to-item) ─────────────────
    def similar_items(self, article_id: int, top_n: int = 5) -> list[dict]:
        """Artikel-artikel yang mirip dengan artikel tertentu."""
        if article_id not in self.item_sim:
            return []
        sims = sorted(self.item_sim[article_id].items(),
                      key=lambda x: x[1], reverse=True)[:top_n]
        return [{"article_id": aid, "similarity": round(sim, 4)} for aid, sim in sims]

    # ── Simpan & load model ──────────────────────────────────────
    def save_to_db(self, top_n: int = TOP_N_DEFAULT) -> int:
        """
        Simpan rekomendasi Top-N semua user ke tabel CF_recommendations.
        Ini cache untuk akses cepat di frontend.
        """
        from ..db.connection import get_db
        from ..db.models import CFRecommendation

        # Hitung semua rekomendasi SEBELUM buka session DB
        all_recs = {}
        for user_id in self.user_item:
            recs = self.recommend(user_id, top_n=top_n)
            if recs:
                all_recs[user_id] = recs

        saved = 0
        with get_db() as db:
            # Hapus rekomendasi lama
            db.query(CFRecommendation).delete()

            for user_id, recs in all_recs.items():
                rec = CFRecommendation(
                    user_id      = user_id,
                    article_ids  = json.dumps([r["article_id"] for r in recs]),
                    scores       = json.dumps([r["score"]      for r in recs]),
                    computed_at  = datetime.now(timezone.utc),
                )
                db.add(rec)
                saved += 1

        print(f"[CF] {saved} rekomendasi user disimpan ke DB")
        return saved

    # ── Evaluasi offline ─────────────────────────────────────────
    def evaluate(self, test_ratio: float = 0.2, top_k: int = 10) -> dict:
        """
        Evaluasi offline dengan hold-out:
        - Sembunyikan 20% interaksi terbaru per user sebagai test set
        - Ukur Precision@K, Recall@K, MAP@K

        Hanya bisa dijalankan jika model sudah di-build dari DB.
        """
        from ..db.connection import get_db
        from ..db.models import Interaction

        with get_db() as db:
            rows = db.query(
                Interaction.user_id,
                Interaction.article_id,
                Interaction.created_at,
            ).order_by(Interaction.created_at).all()

        # Kelompokkan per user, urutkan by waktu
        user_ixs = defaultdict(list)
        for user_id, article_id, created_at in rows:
            user_ixs[user_id].append((article_id, created_at))

        precisions, recalls, aps = [], [], []

        for user_id, items_ts in user_ixs.items():
            if len(items_ts) < 5:
                continue  # Skip user dengan terlalu sedikit data

            items_ts.sort(key=lambda x: x[1])
            split        = max(1, int(len(items_ts) * (1 - test_ratio)))
            train_items  = set(aid for aid, _ in items_ts[:split])
            test_items   = set(aid for aid, _ in items_ts[split:])

            if not test_items:
                continue

            # Simpan state asli user, lalu ubah sementara ke train saja
            old_user_items = dict(self.user_item.get(user_id, {}))
            # Buat ulang user_item hanya dari train_items
            train_dict = {aid: w for aid, w in old_user_items.items() if aid in train_items}
            self.user_item[user_id] = defaultdict(float, train_dict)

            recs = self.recommend(user_id, top_n=top_k)
            rec_ids = [r["article_id"] for r in recs]

            # Kembalikan state asli (jika user tidak ada, hapus key)
            if old_user_items:
                self.user_item[user_id] = defaultdict(float, old_user_items)
            else:
                self.user_item.pop(user_id, None)

            if not rec_ids:
                continue

            # Precision@K
            hits = [1 if rid in test_items else 0 for rid in rec_ids]
            precision = sum(hits) / len(rec_ids)
            precisions.append(precision)

            # Recall@K
            recall = sum(hits) / len(test_items) if test_items else 0
            recalls.append(recall)

            # Average Precision
            ap, n_hits = 0, 0
            for rank, h in enumerate(hits, 1):
                if h:
                    n_hits += 1
                    ap     += n_hits / rank
            ap /= min(top_k, len(test_items)) if test_items else 1
            aps.append(ap)

        n_eval = len(precisions)
        if n_eval == 0:
            return {"status": "insufficient_data", "n_users_evaluated": 0}

        return {
            "status":          "ok",
            "n_users_evaluated": n_eval,
            "top_k":           top_k,
            f"precision@{top_k}": round(sum(precisions)/n_eval, 4),
            f"recall@{top_k}":    round(sum(recalls)   /n_eval, 4),
            f"map@{top_k}":       round(sum(aps)       /n_eval, 4),
        }


# ── Singleton instance ─────────────────────────────────────────
_cf_model: Optional[CollaborativeFilter] = None

def get_cf_model() -> CollaborativeFilter:
    global _cf_model
    if _cf_model is None:
        _cf_model = CollaborativeFilter()
    return _cf_model