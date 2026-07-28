import os
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from contextlib import contextmanager
from .models import Base

_engine = None
_SessionLocal = None

def get_database_url():
    url = os.environ.get("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/newsagg")
    # Railway/Heroku kadang mengeluarkan "postgres://" — SQLAlchemy 2.x butuh "postgresql://"
    if url.startswith("postgres://"):
        url = url.replace("postgres://", "postgresql://", 1)
    return url

def get_engine():
    global _engine
    if _engine is None:
        _engine = create_engine(get_database_url(), pool_size=5, max_overflow=10, pool_pre_ping=True, echo=False)
    return _engine

def get_session_factory():
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=get_engine(), autocommit=False, autoflush=False)
    return _SessionLocal

@contextmanager
def get_db():
    db = get_session_factory()()
    try:
        yield db
        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

def init_db():
    Base.metadata.create_all(bind=get_engine())
    print("[DB] Tabel siap.")

def check_connection():
    try:
        with get_engine().connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception as e:
        print(f"[DB] Koneksi gagal: {e}")
        return False
