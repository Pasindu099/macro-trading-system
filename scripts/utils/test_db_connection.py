from sqlalchemy import create_engine, text

DB_USER = "postgres"
DB_PASSWORD = "root1234"
DB_HOST = "localhost"
DB_PORT = "5432"
DB_NAME = "macroeconomic_db"

DATABASE_URL = f"postgresql+psycopg2://{DB_USER}:{DB_PASSWORD}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

try:
    engine = create_engine(DATABASE_URL)

    with engine.connect() as connection:
        result = connection.execute(text("SELECT current_database(), current_user;"))
        for row in result:
            print("Connected successfully!")
            print(f"Database: {row[0]}")
            print(f"User: {row[1]}")

except Exception as e:
    print("Connection failed.")
    print("Error:", e)