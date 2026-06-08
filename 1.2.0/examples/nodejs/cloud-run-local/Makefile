.PHONY: test up down seed smoke install

test: install up seed smoke down

install:
	npm install

up:
	docker compose up -d --build

down:
	docker compose down -v

seed:
	BQEMU_REST_URL=http://localhost:9050 node seed.js

smoke:
	@./smoke-test.sh
