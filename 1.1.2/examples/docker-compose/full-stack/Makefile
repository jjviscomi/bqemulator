.PHONY: test up down seed smoke logs

test: up seed smoke down

up:
	docker compose up -d --build
	@echo "waiting for services to be healthy..."
	@for i in $$(seq 1 60); do \
	    if curl -sf http://localhost:8080/healthz >/dev/null && \
	       curl -sf http://localhost:9090/-/ready >/dev/null && \
	       curl -sf http://localhost:3000/api/health >/dev/null; then \
	        exit 0; \
	    fi; \
	    sleep 1; \
	done; \
	echo "services did not become healthy" >&2; \
	docker compose ps; \
	exit 1

down:
	docker compose down -v

seed:
	BQEMU_REST_URL=http://localhost:9050 python seed.py

smoke:
	@./smoke-test.sh

logs:
	docker compose logs
