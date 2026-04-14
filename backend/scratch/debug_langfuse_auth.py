import os
import sys

# Add the current directory to sys.path to import app modules
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.observability import get_langfuse
from app.config import settings

def main():
    print(f"Testing Langfuse Connection...")
    print(f"Host: {settings.langfuse_host}")
    print(f"Public Key: {settings.langfuse_public_key}")
    
    lf = get_langfuse()
    if lf:
        print("Langfuse client created.")
        try:
            # check if auth works
            res = lf.auth_check()
            if res:
                print("Auth Check: OK")
            else:
                print("Auth Check: FAILED")
        except Exception as e:
            print(f"Auth Check Error: {e}")
            
        # Try sending a simple trace and flushing
        print("Sending test trace...")
        trace = lf.trace(name="test-auth-trace")
        trace.span(name="test-span")
        lf.flush()
        print("Trace sent and flushed.")
    else:
        print("Failed to get Langfuse client.")

if __name__ == "__main__":
    main()
