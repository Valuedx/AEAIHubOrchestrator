from sqlalchemy import create_engine, text

def update_db():
    db_url = 'postgresql://postgres:root@localhost:5432/ae_orchestrator_ai'
    engine = create_engine(db_url)
    with engine.begin() as conn:
        res = conn.execute(text("UPDATE copilot_sessions SET model = 'gemini-2.5-flash' WHERE model = 'gemini-3.1-pro-preview-customtools'"))
        print(f"Updated {res.rowcount} copilot_sessions rows.")

if __name__ == "__main__":
    update_db()
