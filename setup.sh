#!/bin/bash
# setup.sh — Run this once on your Mac to initialise the repo
# Usage: cd /Users/shubham.gaur/Desktop/airbyte-lm-studio-memgraph && bash setup.sh

set -e

echo "🚀 Setting up airbyte-lm-studio-memgraph..."

# Create directory structure
mkdir -p transform_service
mkdir -p scripts
mkdir -p docs
mkdir -p sample_data
mkdir -p .claude/commands
mkdir -p .forge
mkdir -p prompts

echo "✅ Directories created"

# Git init
git init
git branch -M main

echo "✅ Git initialised"

# Create .env.example
cat > .env.example << 'EOF'
# LM Studio (local — must be running on Mac with gemma3:12b loaded)
LM_STUDIO_BASE_URL=http://host.docker.internal:1234/v1
LM_STUDIO_MODEL=gemma3-12b

# Local Postgres (Docker)
POSTGRES_HOST=postgres
POSTGRES_PORT=5432
POSTGRES_DB=meeting_memory
POSTGRES_USER=meeting_user
POSTGRES_PASSWORD=changeme

# Local Memgraph (Docker — leave blank if no auth)
MEMGRAPH_HOST=memgraph
MEMGRAPH_PORT=7687
MEMGRAPH_USER=
MEMGRAPH_PASSWORD=

# Jira
JIRA_ENABLED=true
JIRA_DOMAIN=shubhamgaur1.atlassian.net
JIRA_EMAIL=shubham.gaur@onixnet.com
JIRA_API_TOKEN=
JIRA_PROJECT_KEY=SCRUM
JIRA_BOARD_ID=1
JIRA_ISSUE_TYPE=Task

# Airbyte
AIRBYTE_WEBHOOK_SECRET=

# Service
PORT=8000
LOG_LEVEL=INFO
EOF

cp .env.example .env
echo "✅ .env.example and .env created (fill in secrets)"

# Create .gitignore
cat > .gitignore << 'EOF'
.env
__pycache__/
*.pyc
*.pyo
*.egg-info/
.venv/
venv/
.DS_Store
.forge/redacted/
*.log
EOF

echo "✅ .gitignore created"

# Initial commit
git add .
git commit -m "chore: initial scaffold — airbyte-lm-studio-memgraph v4"

echo ""
echo "✅ Done! Next steps:"
echo ""
echo "  1. Fill in secrets in .env"
echo "  2. Open Claude Code in this directory:"
echo "     cd /Users/shubham.gaur/Desktop/airbyte-lm-studio-memgraph"
echo "     claude"
echo ""
echo "  3. In Claude Code, run Phase 0 from prompts/PROMPTS.md"
echo "     (it will install Superpowers + forge-skills automatically)"
echo ""
echo "  4. Create GitHub repo and push:"
echo "     gh repo create airbyte-lm-studio-memgraph --public"
echo "     git remote add origin https://github.com/shubham-gaur-x/airbyte-lm-studio-memgraph.git"
echo "     git push -u origin main"
echo ""
echo "  5. Load gemma3:12b in LM Studio before running Phase 6"
