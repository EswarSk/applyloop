"""Seed (or reset) the ApplyLoop learner knowledge graph in Neo4j. Only :ApplyLoop nodes are touched.

    make seed-graph        # uses NEO4J_URI / NEO4J_USERNAME / NEO4J_PASSWORD from .env
"""
from server.neo4j_store import Neo4jStore
from server.settings import LEARNER_ID, NEO4J_DATABASE, NEO4J_PASSWORD, NEO4J_URI, NEO4J_USERNAME

if __name__ == '__main__':
    if not NEO4J_URI:
        raise SystemExit('Set NEO4J_URI (and NEO4J_PASSWORD) in .env first — e.g. a free Neo4j Aura instance.')
    store = Neo4jStore(NEO4J_URI, NEO4J_USERNAME, NEO4J_PASSWORD, NEO4J_DATABASE, LEARNER_ID)
    try:
        store.connect()
        store.reset()
        progress = store.progress()
        print(f"Seeded learner '{LEARNER_ID}': level {progress['level']['current']}, "
              f"resume at {progress['resume']['title']} step {progress['resume']['step']}, "
              f"{progress['counts']['fluent']} fluent / {len(progress['words'])} words.")
    finally:
        store.close()
