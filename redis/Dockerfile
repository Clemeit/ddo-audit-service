FROM redis/redis-stack-server:latest

# Start Redis with AOF configuration, disable RDB to ensure AOF priority
CMD ["redis-stack-server", \
    "--appendonly", "yes", \
    "--appendfsync", "everysec", \
    "--dir", "/data", \
    "--aof-use-rdb-preamble", "no", \
    "--auto-aof-rewrite-percentage", "100", \
    "--auto-aof-rewrite-min-size", "64mb", \
    "--appendfilename", "appendonly.aof", \
    "--aof-load-truncated", "yes", \
    "--save", ""]