.PHONY: demo-video install dev check lint fmt typecheck test schemas web-install web-check e2e attacks evals reset api-check ui-check diagrams

install:
	uv sync
	cd web && npm install

dev:
	./scripts/dev.sh

lint:
	uv run ruff check .
	uv run ruff format --check .

fmt:
	uv run ruff check --fix .
	uv run ruff format .

typecheck:
	uv run mypy

test:
	uv run pytest -q --ignore=tests/evals   # evals use real models: make evals

check: lint typecheck test

schemas:
	uv run python scripts/export_schemas.py
	cd web && npm run gen:types

web-check:
	cd web && npm run typecheck && npx vite build

e2e:
	uv run python scripts/e2e_order_loop.py

attacks:
	uv run python scripts/attack_impostor.py
	uv run python scripts/attack_injected_doc.py

api-check:
	uv run python scripts/api_check.py

ui-check:  # needs `make dev` running
	uv run python scripts/ui_check.py

evals:
	uv run pytest tests/evals -q -s

reset:
	uv run python scripts/demo_reset.py

diagrams:  # docs/diagrams/*.mmd → PNG
	./scripts/render_diagrams.sh

demo-video:  # needs `make dev` running
	uv run python scripts/record_demo.py
	uv run python scripts/build_demo_video.py  # needs ffmpeg (brew install ffmpeg)
