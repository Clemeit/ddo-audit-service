FROM redis/redis-stack-server:7.4.0-v7

# Start Redis with AOF configuration and correct data directory
CMD ["redis-stack-server", \
    "--appendonly", "yes", \
    "--appendfsync", "everysec", \
    "--dir", "/data", \
    "--aof-use-rdb-preamble", "yes", \
    "--auto-aof-rewrite-percentage", "100", \
    "--auto-aof-rewrite-min-size", "64mb", \
    "--appendfilename", "appendonly.aof", \
    "--aof-load-truncated", "yes"]