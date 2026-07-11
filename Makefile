.PHONY: up down start

up:
	docker compose up -d mongodb

down:
	docker compose down

start:
	uv run uvicorn emercard.main:app --host 127.0.0.1 --port 8000
