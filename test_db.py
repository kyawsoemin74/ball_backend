import os
from sqlalchemy import create_engine, text

def get_db_url():
    if os.path.exists('.env'):
        with open('.env', 'r') as f:
            for l in f:
                if l.startswith('DATABASE_URL='):
                    return l.strip().split('=', 1)[1]
    return os.environ.get('DATABASE_URL')

url = get_db_url()
print("URL:", url.split('@')[-1] if url else 'None')
engine = create_engine(url)
with engine.connect() as conn:
    res = conn.execute(text("SELECT table_name FROM information_schema.tables WHERE table_schema='public'"))
    print("Tables:", [r[0] for r in res])
