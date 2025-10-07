# FROM redis/redis-stack-server:7.4.0-v7
FROM redis/redis-stack-server@sha256:9b41d18ae488349f7b5de87befbb222b5a6bb63715765dfa23ff8484ff103749

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