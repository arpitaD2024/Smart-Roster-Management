import logging
import os
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from dotenv import load_dotenv

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
load_dotenv(PACKAGE_ROOT / ".env")

logger = logging.getLogger(__name__)
app_environment = os.getenv("APP_ENV", "development").strip().lower()

class Base(DeclarativeBase):
    pass

url = os.getenv("DATABASE_URL", "").strip()
if not url:
    if app_environment == "production":
        raise RuntimeError("DATABASE_URL must be set in production; SQLite is local-development only.")
    data_dir = PACKAGE_ROOT / "data"
    data_dir.mkdir(exist_ok=True)
    url = f"sqlite:///{(data_dir / 'roster.db').as_posix()}"
elif url.startswith("postgres://"):
    url = url.replace("postgres://", "postgresql+psycopg://", 1)
elif url.startswith("postgresql://"):
    url = url.replace("postgresql://", "postgresql+psycopg://", 1)

if app_environment == "production" and url.startswith("sqlite"):
    raise RuntimeError("SQLite is not supported in production; configure DATABASE_URL for PostgreSQL.")

engine_options = {"pool_pre_ping": True}
if url.startswith("sqlite"):
    engine_options["connect_args"] = {"check_same_thread": False}
else:
    engine_options.update(
        pool_size=5,
        max_overflow=2,
        pool_timeout=30,
        pool_recycle=1800,
        pool_use_lifo=True,
    )

try:
    engine = create_engine(url, **engine_options)
except SQLAlchemyError:
    logger.exception("Could not configure the database engine.")
    raise

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

def get_db():
    db = SessionLocal()
    try:
        yield db
    except SQLAlchemyError:
        db.rollback()
        logger.exception("Database operation failed.")
        raise
    finally:
        db.close()
