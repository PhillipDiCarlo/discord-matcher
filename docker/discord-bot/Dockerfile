# Use official Python image
FROM python:latest
LABEL authors="kajov"

# Set the working directory
WORKDIR /app/src

# Copy the bot source code
COPY ../../src /app/src

# Copy the requirements file and install dependencies
COPY ../../config/other_configs/requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

# Copy the entrypoint script and make it executable
COPY ../scripts/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

# Set the entrypoint
ENTRYPOINT ["/bin/bash", "-c", "chmod +x /entrypoint.sh && /entrypoint.sh"]
