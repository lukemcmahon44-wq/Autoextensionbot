# Common operations. `make help` lists them.
.PHONY: help install test lint check paper smoke backtest live docker

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN{FS=":.*?## "}{printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

install:  ## Install pinned dependencies
	pip install -r requirements.txt

test:  ## Run the test suite
	pytest -q

lint:  ## Lint with ruff
	ruff check src tests

check: lint test  ## Lint + test (what CI runs)

paper:  ## Run a safe paper dry-run (no real-money orders)
	FORCE_PAPER=true python -m src.main

smoke:  ## Validate the API against your DEMO key (read-only)
	FORCE_PAPER=true python -m src.smoke

backtest:  ## Show the strategy's simulated risk profile
	python -m src.backtest --trials 400 --start-balance 1000

live:  ## Run LIVE (needs real credentials in .env; fails closed otherwise)
	python -m src.main

docker:  ## Build the container image
	docker build -t kalshi-scalper .
