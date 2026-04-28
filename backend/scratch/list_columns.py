import sqlalchemy
from sqlalchemy.orm import sessionmaker

DATABASE_URL = "postgresql://postgres:root@localhost:5432/ae_orchestrator"
engine = sqlalchemy.create_engine(DATABASE_URL)
Session = sessionmaker(bind=engine)
session = Session()

try:
    res = session.execute(
        sqlalchemy.text("SELECT column_name FROM information_schema.columns WHERE table_name = 'execution_logs'")
    ).fetchall()
    
    for row in res:
        print(row[0])
finally:
    session.close()
