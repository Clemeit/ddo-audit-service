FROM redis/redis-stack-server:latest

# Copy custom Redis configuration
COPY redis.conf /usr/local/etc/redis/redis.conf

# Start Redis with custom configuration
CMD ["redis-stack-server", "/usr/local/etc/redis/redis.conf"]