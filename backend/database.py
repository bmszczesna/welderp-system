from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

DATABASE_URL = "postgresql://welderp_user:welderp_password@db:5432/welderp_db"

engine = create_engine(DATABASE_URL)

SessionLocal = sessionmaker(bind=engine)


Base = declarative_base()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()