from sqlalchemy import Column, Integer, String, Text, DateTime, Boolean, ForeignKey, Index
from sqlalchemy.orm import declarative_base, relationship
from datetime import datetime

Base = declarative_base()

class Portal(Base):
    __tablename__ = "portals"
    id           = Column(Integer, primary_key=True, autoincrement=True)
    name         = Column(String(100), nullable=False)
    base_url     = Column(String(255), nullable=False, unique=True)
    rss_url      = Column(String(255), nullable=True)
    crawl_policy = Column(String(50), default="allowed")
    active       = Column(Boolean, default=True)
    domain       = Column(String(100), nullable=True)
    selectors    = Column(Text, nullable=True)
    urls         = Column(Text, nullable=True)
    created_at   = Column(DateTime, default=datetime.utcnow)
    updated_at   = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    articles     = relationship("Article", back_populates="portal_ref", lazy="dynamic")

class Article(Base):
    __tablename__ = "articles"
    id            = Column(Integer, primary_key=True, autoincrement=True)
    portal_id     = Column(Integer, ForeignKey("portals.id"), nullable=True)
    title         = Column(Text, nullable=False)
    excerpt       = Column(Text, nullable=True)
    body          = Column(Text, nullable=True)
    published_at  = Column(String(100), nullable=True)
    source        = Column(String(100), nullable=True)
    url           = Column(Text, nullable=False)
    url_hash      = Column(String(64), nullable=False, unique=True)
    content_hash  = Column(String(64), nullable=True)
    simhash       = Column(String(20), nullable=True)
    canonical_url = Column(Text, nullable=True)
    category      = Column(String(100), nullable=True)
    image_url     = Column(Text, nullable=True)
    parsed_at     = Column(DateTime, default=datetime.utcnow)
    crawl_run_id  = Column(String(50), nullable=True)
    portal_ref    = relationship("Portal", back_populates="articles")
    interactions  = relationship("Interaction", back_populates="article", lazy="dynamic")
    __table_args__ = (
        Index("ix_articles_url_hash",     "url_hash"),
        Index("ix_articles_content_hash", "content_hash"),
        Index("ix_articles_source",       "source"),
        Index("ix_articles_parsed_at",    "parsed_at"),
    )

class User(Base):
    __tablename__ = "users"
    id           = Column(Integer, primary_key=True, autoincrement=True)
    pseudonym    = Column(String(64), unique=True, nullable=False)
    created_at   = Column(DateTime, default=datetime.utcnow)
    interactions = relationship("Interaction", back_populates="user", lazy="dynamic")

class Interaction(Base):
    __tablename__ = "interactions"
    id         = Column(Integer, primary_key=True, autoincrement=True)
    user_id    = Column(Integer, ForeignKey("users.id"), nullable=False)
    article_id = Column(Integer, ForeignKey("articles.id"), nullable=False)
    event_type = Column(String(30), default="click")
    created_at = Column(DateTime, default=datetime.utcnow)
    user       = relationship("User",    back_populates="interactions")
    article    = relationship("Article", back_populates="interactions")
    __table_args__ = (
        Index("ix_interactions_user_id",    "user_id"),
        Index("ix_interactions_article_id", "article_id"),
        Index("ix_interactions_created_at", "created_at"),
    )

class CrawlLog(Base):
    __tablename__   = "crawl_logs"
    id              = Column(Integer, primary_key=True, autoincrement=True)
    run_id          = Column(String(50), unique=True, nullable=False)
    portal_id       = Column(Integer, ForeignKey("portals.id"), nullable=True)
    portal_name     = Column(String(100), nullable=True)
    started_at      = Column(DateTime, default=datetime.utcnow)
    finished_at     = Column(DateTime, nullable=True)
    articles_found  = Column(Integer, default=0)
    articles_new    = Column(Integer, default=0)
    articles_dup    = Column(Integer, default=0)
    articles_error  = Column(Integer, default=0)
    status          = Column(String(20), default="running")
    error_msg       = Column(Text, nullable=True)

class CFRecommendation(Base):
    """Cache rekomendasi Top-N per user dari Collaborative Filtering."""
    __tablename__ = "cf_recommendations"

    id          = Column(Integer, primary_key=True, autoincrement=True)
    user_id     = Column(Integer, nullable=False, unique=True)
    article_ids = Column(Text, nullable=False)   # JSON array article_id
    scores      = Column(Text, nullable=False)   # JSON array float scores
    computed_at = Column(DateTime, default=datetime.utcnow)

    __table_args__ = (
        Index("ix_cf_user_id", "user_id"),
    )

class AdminUser(Base):
    """Tabel admin untuk login dashboard."""
    __tablename__ = "admin_users"

    id         = Column(Integer, primary_key=True, autoincrement=True)
    username   = Column(String(64), unique=True, nullable=False)
    password   = Column(String(255), nullable=False)  # bcrypt hash
    created_at = Column(DateTime, default=datetime.utcnow)

    def __repr__(self):
        return f"<AdminUser {self.username}>"
