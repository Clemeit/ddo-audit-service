# DDO Audit Service

This repository serves as the backend service for DDO Audit.

## Tech Stack

- **Python**: Programming language used for the backend.
- **Sanic**: An asynchronous web framework for building fast and scalable web applications in Python.
- **Nginx**: A high-performance web server and reverse proxy server used to serve static files and handle incoming requests.
- **Redis**: An in-memory data structure store used as a caching layer for characters and LFMs to improve performance.
- **Postgres**: A powerful, open-source relational database system used for persistent storage of character data.
- **Docker**: Containerization platform used to ensure a consistent and reproducible environment.

## Links:

- **Main website**: [https://www.ddoaudit.com](https://www.ddoaudit.com)
- **Frontend repository**: [https://github.com/Clemeit/ddo-audit-ui](https://github.com/Clemeit/ddo-audit-ui)

### Setup

- Add certbot
- Run certbot certonly --dry-run --webroot -w /var/www/ddoaudit.com/acme-challenge --cert-name ddoaudit.com -d ddoaudit.com,www.ddoaudit.com,api.ddoaudit.com,pgadmin.ddoaudit.com,gear.ddoaudit.com,playeraudit.com,www.playeraudit.com
  - To add a name to the existing certificate, run the same command with `--expand` and the full list.
- Set certbot to auto renew
- Add a deploy hook so nginx picks up renewed certs: create /etc/letsencrypt/renewal-hooks/deploy/reload-nginx.sh containing `#!/bin/sh` and `docker exec ddoaudit-nginx-1 nginx -s reload`, then `chmod +x` it
  - certbot runs it after every successful renewal
- Install docker and docker-compose with apt. See [https://docs.docker.com/engine/install/ubuntu/] walkthrough.
