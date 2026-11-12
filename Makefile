.PHONY: install backend frontend build test lint demo reset
install:
	cd backend && pip install -r requirements.txt
	cd frontend && npm install
backend:
	cd backend && uvicorn app.main:app --reload --port 8000
frontend:
	cd frontend && npm run dev
build:
	cd frontend && npm run build
test:
	cd backend && python -m pytest
lint:
	cd backend && ruff check .
demo:
	./scripts/demo.sh
reset:
	rm -f backend/governance.db
