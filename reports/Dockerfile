FROM python:3.12.8-slim

# Set the working directory
WORKDIR /app

# Install cron
RUN apt-get update && apt-get install -y cron

# Install any needed packages specified in requirements.txt
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy the cron job file into the container
COPY cronjob /etc/cron.d/cronjob

# Give execution rights on the cron job
RUN chmod 0644 /etc/cron.d/cronjob

# Apply the cron job
RUN crontab /etc/cron.d/cronjob

# Create the log file to be able to run tail
RUN touch /var/log/cron.log && chmod 0666 /var/log/cron.log

# Copy the script to the container
COPY demographics /app/demographics
COPY population /app/population

# Run the command on container startup
CMD cron && tail -f /var/log/cron.log