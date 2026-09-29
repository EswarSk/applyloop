.PHONY: setup api web check replay seed-graph test-graph plaud-install plaud plaud-retry
setup:
	python3 -m venv .venv
	.venv/bin/python -m pip install -r server/requirements.lock.txt
	cd web && npm ci
	@test -f .env || cp .env.example .env
api:
	.venv/bin/python -m uvicorn server.main:app --host 127.0.0.1 --port 8000 --reload --timeout-graceful-shutdown 2
web:
	cd web && node --env-file=../.env node_modules/next/dist/bin/next dev
check:
	.venv/bin/python -m unittest discover -s server -p 'test_*.py'
	cd web && npm run typecheck
replay:
	.venv/bin/python -m scripts.inject_demo_transcript
seed-graph:
	.venv/bin/python -m scripts.seed_neo4j
test-graph:
	@test -n "$$NEO4J_TEST_URI" || (echo 'Set NEO4J_TEST_URI to a dedicated test database'; exit 1)
	.venv/bin/python -m unittest server.test_graph_store server.test_integrations -v
plaud-install:
	npm install -g @plaud-ai/cli@0.3.14
plaud:
	.venv/bin/python -m server.plaud_bridge
plaud-retry:
	.venv/bin/python -m server.plaud_bridge --retry-failed
