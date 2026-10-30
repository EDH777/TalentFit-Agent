from sqlalchemy import text, inspect
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.job_categories import JOB_CATEGORY_CHECK
from app.db.session import Base, SessionLocal, engine
from app.db.candidate_positions import migrate_candidate_positions
from app.models.entities import User
from app.services.auth import hash_password
from app.services.staffing.common import ensure_state


def init_db() -> None:
    migrate_candidate_positions(engine)
    Base.metadata.create_all(bind=engine)
    with engine.begin() as conn:
        chunk_columns = {column["name"] for column in inspect(conn).get_columns("resume_chunks")}
        for name, sql_type in [("embedding_json", "TEXT"), ("embedding_model", "VARCHAR(120)"), ("embedding_dimensions", "INTEGER")]:
            if name not in chunk_columns:
                conn.execute(text(f"ALTER TABLE resume_chunks ADD COLUMN {name} {sql_type}"))
        for name, sql_type in [("enabled", "BOOLEAN NOT NULL DEFAULT 1"), ("position", "INTEGER NOT NULL DEFAULT 0"),
                               ("hit_count", "INTEGER NOT NULL DEFAULT 0"), ("updated_at", "DATETIME")]:
            if name not in chunk_columns:
                conn.execute(text(f"ALTER TABLE resume_chunks ADD COLUMN {name} {sql_type}"))
        conn.execute(text("UPDATE resume_chunks SET updated_at=created_at WHERE updated_at IS NULL"))
        candidate_columns = {column["name"] for column in inspect(conn).get_columns("candidates")}
        if 'job_category' not in candidate_columns:
            conn.execute(text(f'ALTER TABLE candidates ADD COLUMN job_category VARCHAR(40) CHECK ({JOB_CATEGORY_CHECK})'))
        if "source_type" in candidate_columns:
            conn.execute(text("ALTER TABLE candidates DROP COLUMN source_type"))
        for name, sql_type in [("enabled", "BOOLEAN NOT NULL DEFAULT 1"), ("parent_chunk_size", "INTEGER NOT NULL DEFAULT 4000"),
                               ("child_chunk_size", "INTEGER NOT NULL DEFAULT 2048"), ("child_chunk_overlap", "INTEGER NOT NULL DEFAULT 60"),
                               ("processed_text", "TEXT NOT NULL DEFAULT ''"), ("processing_version", "VARCHAR(40) NOT NULL DEFAULT ''"),
                               ("parent_delimiter", "VARCHAR(256) NOT NULL DEFAULT '<<<RESUME_PARENT>>>'"),
                               ("child_delimiter", "VARCHAR(256) NOT NULL DEFAULT '<<<RESUME_SECTION>>>'"),
                               ("replace_consecutive_spaces", "BOOLEAN NOT NULL DEFAULT 0"), ("delete_urls_email", "BOOLEAN NOT NULL DEFAULT 0")]:
            if name not in candidate_columns:
                conn.execute(text(f"ALTER TABLE candidates ADD COLUMN {name} {sql_type}"))
        conn.execute(text("""UPDATE candidates SET
            parent_chunk_size=min(parent_chunk_size,4000),
            child_chunk_size=min(child_chunk_size,2048,parent_chunk_size,4000),
            child_chunk_overlap=min(child_chunk_overlap,min(child_chunk_size,2048,parent_chunk_size,4000)-1)
            WHERE parent_chunk_size>4000 OR child_chunk_size>2048 OR child_chunk_size>parent_chunk_size"""))
        columns = {column["name"] for column in inspect(conn).get_columns("chat_messages")}
        if "sources_json" not in columns:
            conn.execute(text("ALTER TABLE chat_messages ADD COLUMN sources_json TEXT NOT NULL DEFAULT '[]'"))
        if "trace_json" not in columns:
            conn.execute(text("ALTER TABLE chat_messages ADD COLUMN trace_json TEXT NOT NULL DEFAULT '[]'"))
        conn.execute(text("""CREATE VIRTUAL TABLE IF NOT EXISTS resume_chunks_fts USING fts5(
            chunk_id UNINDEXED, candidate_id UNINDEXED, content
        )"""))
    db: Session = SessionLocal()
    try:
        ensure_state(db)
        seeds = [
            (settings.admin_username, settings.admin_password, "admin"),
            (settings.hr_username, settings.hr_password, "hr"),
        ]
        for username, password, role in seeds:
            if not db.query(User).filter(User.username == username).first():
                if not password:
                    raise RuntimeError("请先运行 scripts/setup.ps1 配置初始化账号。")
                db.add(User(username=username, password_hash=hash_password(password), role=role))
        db.commit()
    finally:
        db.close()
