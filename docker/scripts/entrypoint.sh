#!/bin/bash

set -e  # Exit immediately if a command exits with a non-zero status

# Print environment info
echo "Running in $ENVIRONMENT mode"

# If any setup is needed based on environment
if [ "$ENVIRONMENT" = "development" ]; then
    echo "Development environment detected. Installing dependencies..."
    pip install -r /app/requirements.txt
fi

# Execute the main application
exec "$@"
