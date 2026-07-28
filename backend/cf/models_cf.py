"""Tambahkan tabel CFRecommendation ke models.py"""
# File ini berisi model tambahan — import di models.py sudah otomatis via Base
from sqlalchemy import Column, Integer, Text, DateTime, Index
from sqlalchemy.orm import declarative_base
from datetime import datetime

# Import Base yang sama dari models.py
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', '..'))

from backend.db.models import Base


class CFRecommendation(Base):
    """
    Cache rekomendasi Top-N per user dari Collaborative Filtering.
    Diperbarui setiap kali CF model di-rebuild.
    """
    __tablename__ = "cf_recommendations"

    id          = Column(Integer, primary_key=True, autoincrement=True)
    user_id     = Column(Integer, nullable=False, unique=True)
    article_ids = Column(Text, nullable=False)   # JSON array article_id
    scores      = Column(Text, nullable=False)   # JSON array float scores
    computed_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_cf_user_id", "user_id"),
    )

    def __repr__(self):
        return f"<CFRecommendation user={self.user_id}>"
