# Deploy (dev)

Bring up Redis + the six services:

```bash
docker compose -f deploy/docker-compose.yml up --build
```

Health-check every service:

```bash
for p in 8001 8002 8003 8004 8005 8006; do curl -s localhost:$p/health; echo; done
```

Tear down:

```bash
docker compose -f deploy/docker-compose.yml down
```
