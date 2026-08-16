.PHONY: dev backend frontend test lint build migrate
dev:
	@echo "Run 'make backend' and 'make frontend' in separate terminals"
backend:
	cd backend && uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
frontend:
	cd frontend && npm run dev
test:
	cd backend && pytest -q
lint:
	cd frontend && npm run lint
build:
	cd frontend && npm run build
migrate:
	cd backend && alembic upgrade head
