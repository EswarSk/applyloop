import os
from pathlib import Path
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / '.env')
DEMO_MODE = os.getenv('DEMO_MODE', 'true').lower() == 'true'
WEB_ORIGIN = os.getenv('WEB_ORIGIN', 'http://localhost:3000')
INTERNAL_API_TOKEN = os.getenv('INTERNAL_API_TOKEN', 'local-demo-change-me')
# Graph backend: set NEO4J_URI (e.g. neo4j+s://xxxx.databases.neo4j.io for Aura) to use Neo4j.
NEO4J_URI = os.getenv('NEO4J_URI', '').strip()
NEO4J_USERNAME = os.getenv('NEO4J_USERNAME', 'neo4j')
NEO4J_PASSWORD = os.getenv('NEO4J_PASSWORD', '')
NEO4J_DATABASE = os.getenv('NEO4J_DATABASE', '') or None
LEARNER_ID = os.getenv('LEARNER_ID', 'demo')
