from app.settings import get_settings

s = get_settings()
print(f"API key loaded: {s.eodhd_api_key[:10]}...")  # only prints first 10 chars
print(f"Database URL host: {s.database_url.split('@')[1] if '@' in s.database_url else 'ERROR'}")
print(f"Log level: {s.log_level}")