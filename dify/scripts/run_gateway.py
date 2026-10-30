import os
import sys
from pathlib import Path

ROOT = Path(__file__).parents[1]
sys.path.insert(0, str(ROOT))

if __name__ == "__main__":
    from dotenv import load_dotenv
    import uvicorn
    from app.server import create_app
    load_dotenv(ROOT / ".runtime/app.env", override=False)
    uvicorn.run(create_app(), host=os.getenv("APP_HOST", "127.0.0.1"),
                port=int(os.getenv("APP_PORT", "18381")), workers=1, access_log=False)
