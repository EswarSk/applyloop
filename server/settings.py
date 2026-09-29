import os
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / '.env')
DEMO_MODE = os.getenv('DEMO_MODE', 'true').lower() == 'true'
WEB_ORIGIN = os.getenv('WEB_ORIGIN', 'http://localhost:3000')
INTERNAL_API_TOKEN = os.getenv('INTERNAL_API_TOKEN', 'local-demo-change-me')
