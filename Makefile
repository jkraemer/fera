IMAGE := fera
DEV_CONTAINER := fera-dev
PROD_CONTAINER := fera-prod
DEPLOY_HOST ?= fera.example.com

HIMALAYA_USER := himalaya
HIMALAYA_HOME := /home/himalaya
HIMALAYA_WRAPPER := /usr/local/bin/himalaya-wrapper

.PHONY: build build-himalaya-wrapper dev shell down test gateway run stop status logs deploy deploy-himalaya rollback

build:
	podman build -f Containerfile -t $(IMAGE) .

build-himalaya-wrapper:
	cd tools/himalaya-wrapper && cargo build --release

# --- Development ---

dev: build
	podman run -it --rm \
		--name $(DEV_CONTAINER) \
		-v .:/app:Z \
		-v $(DEV_CONTAINER)-venv:/app/.venv:Z \
		-v $(DEV_CONTAINER)-claude:/home/fera/.claude:Z \
		-p 8389:8389 \
		-p 8390:8390 \
		-e FERA_HOME=/home/fera \
		-e UV_PROJECT_ENVIRONMENT=/app/.venv \
		-e CLAUDE_CODE_OAUTH_TOKEN \
		-e ANTHROPIC_API_KEY \
		-e TELEGRAM_BOT_TOKEN \
		-e TELEGRAM_ALLOWED_USERS \
		--user fera \
		$(IMAGE) \
		bash -c 'cd /app && uv sync && exec bash'

shell:
	podman exec -it $(DEV_CONTAINER) bash

down:
	podman stop $(DEV_CONTAINER)

test:
	podman exec $(DEV_CONTAINER) bash -c 'cd /app && uv run pytest'

gateway:
	podman exec -d $(DEV_CONTAINER) bash -c 'cd /app && uv run fera-gateway'

# --- Production-like (systemd) ---

run: build
	podman run -d \
		--name $(PROD_CONTAINER) \
		--systemd=always \
		-v $(PROD_CONTAINER)-home:/home/fera:Z \
		-e CLAUDE_CODE_OAUTH_TOKEN \
		-e ANTHROPIC_API_KEY \
		-e TELEGRAM_BOT_TOKEN \
		-e TELEGRAM_ALLOWED_USERS \
		-p 8389:8389 \
		-p 8080:8080 \
		$(IMAGE)

stop:
	podman stop $(PROD_CONTAINER) && podman rm $(PROD_CONTAINER)

status:
	podman exec $(PROD_CONTAINER) systemctl status fera-memory fera-gateway fera-webui --no-pager

logs:
	podman exec $(PROD_CONTAINER) journalctl -u fera-gateway -u fera-memory -u fera-webui --no-pager -n 50

# --- Deployment ---

deploy:
	git archive HEAD | ssh root@$(DEPLOY_HOST) \
		'rm -rf /opt/fera-new && \
		mkdir /opt/fera-new && \
		tar -xC /opt/fera-new && \
		cd /opt/fera-new/webui && npm ci --silent && npm run build && \
		cp /opt/fera-new/deploy/*.service /etc/systemd/system/ && \
		systemctl daemon-reload && \
		rm -rf /opt/fera.bak && mv /opt/fera /opt/fera.bak && mv /opt/fera-new /opt/fera && \
		cd /opt/fera && UV_PROJECT_ENVIRONMENT=/opt/fera-venv UV_PYTHON_INSTALL_DIR=/opt/uv-python uv sync --no-dev && \
		systemctl restart fera-memory fera-gateway fera-webui'

deploy-himalaya: build-himalaya-wrapper
	scp tools/himalaya-wrapper/target/release/himalaya-wrapper root@$(DEPLOY_HOST):/tmp/himalaya-wrapper
	ssh root@$(DEPLOY_HOST) \
		'id $(HIMALAYA_USER) &>/dev/null || useradd --system --home-dir $(HIMALAYA_HOME) --create-home --shell /usr/sbin/nologin --comment "Himalaya email service account" $(HIMALAYA_USER) && \
		mkdir -p $(HIMALAYA_HOME)/.config/himalaya && \
		chown -R $(HIMALAYA_USER):$(HIMALAYA_USER) $(HIMALAYA_HOME) && \
		chmod 700 $(HIMALAYA_HOME) $(HIMALAYA_HOME)/.config $(HIMALAYA_HOME)/.config/himalaya && \
		install -o $(HIMALAYA_USER) -g fera -m 4750 /tmp/himalaya-wrapper $(HIMALAYA_WRAPPER) && \
		rm -f /tmp/himalaya-wrapper'

rollback:
	ssh root@$(DEPLOY_HOST) \
		'test -d /opt/fera.bak || (echo "No backup found at /opt/fera.bak" && exit 1) && \
		mv /opt/fera /opt/fera-broken && mv /opt/fera.bak /opt/fera && \
		cp /opt/fera/deploy/*.service /etc/systemd/system/ && \
		systemctl daemon-reload && \
		cd /opt/fera && UV_PROJECT_ENVIRONMENT=/opt/fera-venv UV_PYTHON_INSTALL_DIR=/opt/uv-python uv sync --no-dev && \
		systemctl restart fera-memory fera-gateway fera-webui'
