#!/bin/bash

set -euo pipefail

PLUGIN_NAME="Ruff"
echo
echo "Running Plugin $PLUGIN_NAME..."
# Ruff is already in the dev dependency group. Exclude large generated artifacts.
uv run ruff check --fix --force-exclude --exclude "data,.venv"
