import os
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / '.env')
DEMO_MODE = os.getenv('DEMO_MODE', 'true').lower() == 'true'
BAND_MODE = os.getenv('BAND_MODE', 'demo' if DEMO_MODE else 'live').lower()
if BAND_MODE not in {'demo', 'live'}:
    raise ValueError('BAND_MODE must be demo or live')
WEB_ORIGIN = os.getenv('WEB_ORIGIN', 'http://localhost:3000')
INTERNAL_API_TOKEN = os.getenv('INTERNAL_API_TOKEN', 'local-demo-change-me')
