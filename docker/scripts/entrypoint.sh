#!/bin/bash

set -e  # Exit immediately if a command exits with a non-zero status

# Install dependencies
pip install --no-cache-dir -r /app/requirements.txt

# Navigate to the source directory
cd /app/src

# Execute the main application
exec "$@"
