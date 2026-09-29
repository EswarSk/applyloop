.PHONY: setup api web check replay seed-graph test-graph
setup:
	python3 -m venv .venv
	.venv/bin/python -m pip install -r server/requirements.lock.txt
	cd web && npm ci
	@test -f .env || cp .env.example .env
api:
	.venv/bin/python -m uvicorn server.main:app --host 127.0.0.1 --port 8000 --reload --timeout-graceful-shutdown 2
web:
	@grep '^NEXT_PUBLIC_' .env > web/.env.local 2>/dev/null || true
	cd web && node node_modules/next/dist/bin/next dev
check:
	.venv/bin/python -m unittest discover -s server -p 'test_*.py'
	cd web && npm run typecheck
replay:
	.venv/bin/python -m scripts.inject_demo_transcript
seed-graph:
	.venv/bin/python -m scripts.seed_neo4j
test-graph:
	NEO4J_TEST_URI=$${NEO4J_TEST_URI:-$$(grep ^NEO4J_URI= .env | cut -d= -f2-)} .venv/bin/python -m unittest server.test_graph_store -v
