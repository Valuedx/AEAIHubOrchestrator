import uuid
from app.database import SessionLocal
from app.models.copilot import CopilotSession

def fix_sessions():
    db = SessionLocal()
    try:
        # Update sessions currently using anthropic to use vertex
        updated = db.query(CopilotSession).filter(
            CopilotSession.provider == "anthropic",
            CopilotSession.status == "active"
        ).update({
            "provider": "vertex",
            "model": "gemini-3-flash-preview"
        })
        db.commit()
        print(f"Updated {updated} active sessions to use Vertex AI.")
    except Exception as e:
        db.rollback()
        print(f"Error: {e}")
    finally:
        db.close()

if __name__ == "__main__":
    fix_sessions()
