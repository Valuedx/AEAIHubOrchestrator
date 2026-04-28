from app.config import settings
from app.security.jwt_auth import create_access_token
from app.database import SessionLocal, set_tenant_context
from app.models.user import User

def generate_admin_token():
    # Ensure we are in the default tenant
    db = SessionLocal()
    try:
        set_tenant_context(db, "default")
        admin = db.query(User).filter(User.username == "admin").first()
        if not admin:
            print("Admin user not found. Start the backend first to seed it.")
            return
        
        token = create_access_token(
            tenant_id="default",
            subject=str(admin.id),
            extra_claims={
                "username": admin.username,
                "is_admin": admin.is_admin,
            }
        )
        print(f"TOKEN:{token}")
    finally:
        db.close()

if __name__ == "__main__":
    generate_admin_token()
