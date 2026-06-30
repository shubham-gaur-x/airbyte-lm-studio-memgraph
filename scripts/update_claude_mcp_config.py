#!/usr/bin/env python3
"""Idempotently adds memgraph and jira MCP servers to Claude Desktop config.

Reads credentials from .env in the project root. Safe to re-run.
"""
import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent
CONFIG_PATH = Path.home() / "Library/Application Support/Claude/claude_desktop_config.json"

def load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    env_file = PROJECT_ROOT / ".env"
    if not env_file.exists():
        print(f"ERROR: .env not found at {env_file}", file=sys.stderr)
        sys.exit(1)
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip()
    return env

def main() -> None:
    env = load_env()

    jira_domain = env.get("JIRA_DOMAIN", "")
    jira_email = env.get("JIRA_EMAIL", "")
    jira_token = env.get("JIRA_API_TOKEN", "")

    # JIRA_DOMAIN is "shubhamgaur1.atlassian.net" → site name is "shubhamgaur1"
    site_name = jira_domain.replace(".atlassian.net", "")

    if not all([site_name, jira_email, jira_token]):
        print("ERROR: JIRA_DOMAIN, JIRA_EMAIL, JIRA_API_TOKEN must all be set in .env", file=sys.stderr)
        sys.exit(1)

    config: dict = {}
    if CONFIG_PATH.exists():
        config = json.loads(CONFIG_PATH.read_text())

    config.setdefault("mcpServers", {})
    config["mcpServers"]["memgraph"] = {
        "command": "uvx",
        "args": ["mcp-memgraph"],
        "env": {
            "MEMGRAPH_URL": "bolt://localhost:7687",
            "MEMGRAPH_USER": "",
            "MEMGRAPH_PASSWORD": "",
            "MCP_READ_ONLY": "false",
        },
    }
    config["mcpServers"]["jira"] = {
        "command": "mcp-atlassian-jira",
        "args": [],
        "env": {
            "ATLASSIAN_SITE_NAME": site_name,
            "ATLASSIAN_USER_EMAIL": jira_email,
            "ATLASSIAN_API_TOKEN": jira_token,
        },
    }

    CONFIG_PATH.write_text(json.dumps(config, indent=2))
    print(f"Updated {CONFIG_PATH}")
    print(f"  memgraph → uvx mcp-memgraph (bolt://localhost:7687)")
    print(f"  jira     → mcp-atlassian-jira ({site_name}.atlassian.net)")

if __name__ == "__main__":
    main()
