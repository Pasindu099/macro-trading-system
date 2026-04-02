from sqlalchemy import create_engine


DB_USER = "postgres"
DB_PASSWORD = "root1234"
DB_HOST = "localhost"
DB_PORT = "5432"
DB_NAME = "macroeconomic_db"


def get_engine():
    database_url = (
        f"postgresql+psycopg2://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"
    )
    return create_engine(database_url)