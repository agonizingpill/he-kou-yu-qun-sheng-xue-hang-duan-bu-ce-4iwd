import os
from sqlalchemy import create_engine,inspect,text
from sqlalchemy.orm import DeclarativeBase, sessionmaker
engine=create_engine(os.environ['DATABASE_URL'],pool_pre_ping=True)
Session=sessionmaker(engine,expire_on_commit=False)
class Base(DeclarativeBase):pass
def session():
    with Session() as db:
        try:yield db
        except Exception:db.rollback();raise
def ensure_columns():
    """为既有演示库补上复核结论与覆盖依据列（项目无迁移链，首次create_all不会改旧表）。"""
    added={'school_reviews':{'verdict':"VARCHAR(20) DEFAULT 'schools'",'coverage':'JSON'}}
    insp=inspect(engine);tables=set(insp.get_table_names())
    with engine.begin() as conn:
        for table,columns in added.items():
            if table not in tables:continue
            existing={c['name'] for c in insp.get_columns(table)}
            for name,ddl in columns.items():
                if name not in existing:conn.execute(text(f'ALTER TABLE {table} ADD COLUMN {name} {ddl}'))
