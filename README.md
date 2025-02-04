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
- **Frontend repository**: [https://github.com/Clemeit/ddo-audit](https://github.com/Clemeit/ddo-audit)

### Setup
- Add certbot
- Set certbot to auto renew
- Open etc/letsencrypt/renewal/me.com.conf and under [renewalparams] add: renew_hook = docker exec -it html-nginx-1 nginx reload
  - This will automatically reload nginx whenever new certs are generated