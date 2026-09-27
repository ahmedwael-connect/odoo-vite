.PHONY: lint test install-desktop

lint:
	python3 -m ruff check odoo_vite/ tests/

test:
	python3 -m pytest tests/ -q

install-desktop:
	mkdir -p "$(HOME)/.local/share/applications"
	printf '[Desktop Entry]\nType=Application\nName=Odoo Vite\nExec=python3 $(CURDIR)/main.py\nIcon=computer\nCategories=Development;\n' > "$(HOME)/.local/share/applications/odoo-vite.desktop"
	@echo "Installed ~/.local/share/applications/odoo-vite.desktop"
