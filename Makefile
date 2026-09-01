.PHONY: install demo test lint

install:
	python3 -m venv .venv && .venv/bin/pip install -r requirements.txt

demo:
	.venv/bin/uvicorn main:app --reload --port 8000

test:
	.venv/bin/pytest

lint:
	.venv/bin/ruff check . && .venv/bin/mypy --strict --follow-imports=silent adapters/base.py
