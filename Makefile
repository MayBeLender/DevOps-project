PYTHON ?= python
FILE ?= backup.dump

.DEFAULT_GOAL := check
.PHONY: install run check lint test sast migrate backup restore

install:
	$(PYTHON) -m pip install -r requirements.txt

run:
	$(PYTHON) -c "from pathlib import Path; Path('logs').mkdir(exist_ok=True)"
	$(PYTHON) main.py

check: lint test sast

lint:
	$(PYTHON) -m ruff check .
	$(PYTHON) -m ruff format --check .

test:
	$(PYTHON) -m pytest

sast:
	$(PYTHON) -c "from pathlib import Path; Path('reports').mkdir(exist_ok=True)"
	$(PYTHON) -m bandit -r main.py db_backup.py migrations -f json -o reports/bandit.json

migrate:
	$(PYTHON) -m alembic upgrade head

backup:
	$(PYTHON) db_backup.py backup "$(FILE)"

restore:
	$(PYTHON) db_backup.py restore "$(FILE)"
