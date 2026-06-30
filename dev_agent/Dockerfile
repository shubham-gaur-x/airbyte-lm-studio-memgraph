FROM python:3.11-slim

# System deps: git (for worktrees), curl, ca-certificates, Node.js LTS, GitHub CLI
RUN apt-get update && apt-get install -y --no-install-recommends \
    git curl ca-certificates gnupg \
    && curl -fsSL https://deb.nodesource.com/setup_lts.x | bash - \
    && apt-get install -y --no-install-recommends nodejs \
    && npm install -g @anthropic-ai/claude-code \
    && apt-get clean && rm -rf /var/lib/apt/lists/*

# GitHub CLI — pin version for reproducibility
ARG GH_VERSION=2.62.0
RUN curl -fsSL https://github.com/cli/cli/releases/download/v${GH_VERSION}/gh_${GH_VERSION}_linux_arm64.tar.gz \
    | tar -xz -C /usr/local/bin --strip-components=2 gh_${GH_VERSION}_linux_arm64/bin/gh 2>/dev/null \
    || curl -fsSL https://github.com/cli/cli/releases/download/v${GH_VERSION}/gh_${GH_VERSION}_linux_amd64.tar.gz \
    | tar -xz -C /usr/local/bin --strip-components=2 gh_${GH_VERSION}_linux_amd64/bin/gh

WORKDIR /app

# Install transform_service deps first (jira_client, utils are reused)
COPY transform_service/requirements.txt /tmp/ts_requirements.txt
RUN pip install --no-cache-dir -r /tmp/ts_requirements.txt

# Install dev_agent deps
COPY dev_agent/requirements.txt /tmp/da_requirements.txt
RUN pip install --no-cache-dir -r /tmp/da_requirements.txt

# Copy source
COPY transform_service/ /app/transform_service/
COPY dev_agent/ /app/dev_agent/

CMD ["uvicorn", "dev_agent.orchestrator:app", "--host", "0.0.0.0", "--port", "8000"]
