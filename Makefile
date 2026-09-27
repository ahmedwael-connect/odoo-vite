.PHONY: lint test

lint:
	python3 -m ruff check odoo_vite/ tests/

test:
	python3 -m pytest tests/ -q
