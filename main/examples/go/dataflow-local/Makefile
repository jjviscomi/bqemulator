.PHONY: test test-unit test-e2e build

test:
	go test ./... -count=1 -timeout=120s

test-unit:
	go test ./... -count=1 -run='^TestTransform|^TestRead'

test-e2e:
	go test ./... -count=1 -run='^TestSinkEndToEnd' -timeout=120s

build:
	go build ./...
