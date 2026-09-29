.PHONY: setup api web check replay
setup:
	python3 -m venv .venv
	.venv/bin/python -m pip install -r server/requirements.lock.txt
	cd web && npm ci
	@test -f .env || cp .env.example .env
api:
	.venv/bin/python -m uvicorn server.main:app --host 127.0.0.1 --port 8000 --reload
web:
	cd web && node --env-file=../.env node_modules/next/dist/bin/next dev
check:
	.venv/bin/python -m unittest discover -s server -p 'test_*.py'
	cd web && npm run typecheck
replay:
	.venv/bin/python -m scripts.inject_demo_transcript
