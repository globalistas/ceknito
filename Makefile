CUR_DIR=$(shell pwd)
NPM_CMD?=install

.PHONY: docker-compose-build

docker-npm:
	docker run \
		-v $(CUR_DIR):/throat \
		-w /throat node:14-buster-slim \
		npm $(NPM_CMD)

docker-compose-build:
	docker-compose build
	@$(DONE)

.PHONY: up
up: docker-compose-build
	docker-compose up --remove-orphans -d
	@$(DONE)

.PHONY: down
down:
	docker-compose down --remove-orphans
	@$(DONE)

.PHONY: docker-shell
docker-shell: docker-compose-build
	docker-compose run --rm throat /bin/bash
	@$(DONE)
