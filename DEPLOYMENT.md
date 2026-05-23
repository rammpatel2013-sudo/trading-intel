# DEPLOYMENT.md — Local → GitHub → Digital Ocean

End-to-end deployment guide. Read this once; refer back during Phase 0 (GitHub setup) and Phase 7 (DO provisioning).

---

## Local development workflow

### Prerequisites
- Windows 10/11 with WSL2 + Ubuntu 22.04 (recommended) **or** native Windows with Docker Desktop
- Python 3.11 or 3.12 (`python --version`)
- Docker Desktop running
- Git (`git --version`)
- A Convex pro account
- An Anthropic API key (create at console.anthropic.com)
- A Voyage AI API key (create at voyageai.com)
- A FRED API key (free at fredaccount.stlouisfed.org)
- A Discord webhook URL (server settings → integrations → webhooks)

### One-time setup

```bash
git clone git@github.com:YOUR_USER/trading-intel.git
cd trading-intel

cp .env.template .env
# Edit .env with real values — see comments in template

# Bring up Postgres + pgvector
docker compose up -d postgres

# Install Python deps (editable mode)
python -m venv .venv
source .venv/bin/activate    # or .venv\Scripts\activate on Windows
pip install -e ".[dev]"

# Run migrations
alembic upgrade head

# Smoke test
pytest -q
```

### Daily dev loop

```bash
# Terminal 1: dashboard
streamlit run trading_intel/dashboard/Home.py

# Terminal 2: scheduler (optional — only if testing jobs)
python -m trading_intel.scheduler.runner

# Terminal 3: code + test
pytest -q --watch    # or just rerun pytest manually
```

---

## Git + GitHub workflow

### Branch strategy
- `main` — production-ready, always green CI, auto-deploys to DO
- `feature/*` — new work; PR into main
- `fix/*` — bug fixes; PR into main
- No long-lived develop branch; we're not big enough.

### Commit style
- Imperative mood, present tense: `add convex client` not `added convex client`
- Prefix with scope when helpful: `clients: add ConvexClient`, `greeks: fix flip point edge case`, `memory: log Phase 1 start`
- One logical change per commit. Squash-merge PRs.

### Pull requests
- Every change goes through a PR, even solo work — forces a moment of review
- PR must pass CI (`pytest`, `ruff`, `black --check`)
- Title format: `[scope] short summary`
- Description: what changed + why + how to test

### GitHub repo setup (Phase 0)

```bash
# In the trading-intel/ folder
git init
git add .
git commit -m "initial scaffolding"

# Create empty private repo on github.com first, then:
git remote add origin git@github.com:YOUR_USER/trading-intel.git
git branch -M main
git push -u origin main
```

Branch protection rules (Settings → Branches → Add rule for `main`):
- ✅ Require status checks to pass before merging
- ✅ Require branches to be up to date
- ✅ Require pull request reviews (optional, but good habit even solo)

### Required GitHub secrets
Go to repo Settings → Secrets and variables → Actions → New repository secret:
- `DO_SSH_HOST` — droplet IP or hostname
- `DO_SSH_USER` — usually `deploy` (a non-root user you'll create)
- `DO_SSH_KEY` — private SSH key for the deploy user
- `DO_DEPLOY_PATH` — `/srv/trading-intel`

These are used by `.github/workflows/deploy.yml`.

---

## Digital Ocean setup (Phase 7)

### Step 1: Create droplet

DO Console → Create → Droplets:
- Image: **Ubuntu 24.04 LTS**
- Size: **Basic Regular**, 2 GB RAM / 1 vCPU / 50 GB SSD (**$12/mo**)
- Datacenter: closest to you (likely NYC1 or SFO3)
- Authentication: **SSH key** (upload your laptop's public key)
- Hostname: `trading-intel-prod`

Total: $12/mo for the droplet.

### Step 2: Initial droplet hardening

SSH in as root, then:

```bash
# Update
apt update && apt upgrade -y

# Create non-root user
adduser deploy
usermod -aG sudo deploy

# Copy your SSH key for the new user
rsync --archive --chown=deploy:deploy ~/.ssh /home/deploy

# Lock down SSH
sed -i 's/PermitRootLogin yes/PermitRootLogin no/' /etc/ssh/sshd_config
sed -i 's/#PasswordAuthentication yes/PasswordAuthentication no/' /etc/ssh/sshd_config
systemctl restart sshd

# Firewall
ufw allow OpenSSH
ufw allow 80
ufw allow 443
ufw --force enable

# Install Docker + Compose
apt install -y docker.io docker-compose-plugin
usermod -aG docker deploy

# Install nginx + certbot
apt install -y nginx certbot python3-certbot-nginx
```

Logout, log back in as `deploy`.

### Step 3: Create managed Postgres database

DO Console → Databases → Create Database Cluster:
- Engine: **PostgreSQL 16**
- Plan: **Basic**, 1 GB RAM / 1 vCPU / 10 GB ($12/mo) — daily backups included
- Datacenter: same as droplet
- Database name: `trading_intel`
- User: `intel`

Once created:
- Enable **trusted sources** → add your droplet's IP
- Enable the **pgvector** extension from the DO UI (Settings → Extensions → pgvector)
- Copy the connection string — that's your `DATABASE_URL` in `.env` on the droplet

Total now: $24/mo.

### Step 4: Provision the app

On the droplet, as `deploy`:

```bash
# Clone repo
sudo mkdir -p /srv/trading-intel
sudo chown deploy:deploy /srv/trading-intel
cd /srv
git clone git@github.com:YOUR_USER/trading-intel.git

# Add deploy SSH key to GitHub (or use a deploy token)
# Settings → Deploy keys → Add deploy key (read-only)

cd trading-intel

# Create production .env
cp .env.template .env
# Edit .env — production values:
# - CONVEX_EMAIL/PASSWORD
# - ANTHROPIC_API_KEY
# - VOYAGE_API_KEY
# - FRED_API_KEY
# - DISCORD_WEBHOOK_URL
# - DATABASE_URL=postgres://intel:PASSWORD@MANAGED_DB_HOST:25060/trading_intel?sslmode=require

# Build app container
docker compose -f docker-compose.prod.yml build

# Run migrations against managed DB
docker compose -f docker-compose.prod.yml run --rm app alembic upgrade head

# Start
docker compose -f docker-compose.prod.yml up -d
```

### Step 5: nginx + SSL

Point your domain at the droplet's IP (DNS A record).

```bash
sudo nano /etc/nginx/sites-available/trading-intel
```

Paste:
```nginx
server {
    listen 80;
    server_name your-domain.com;

    location / {
        proxy_pass http://localhost:8501;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        # Streamlit needs long timeouts for streaming
        proxy_read_timeout 86400;
    }
}
```

```bash
sudo ln -s /etc/nginx/sites-available/trading-intel /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx

# Get SSL cert
sudo certbot --nginx -d your-domain.com
# Choose "redirect HTTP to HTTPS"

# Auto-renewal is set up automatically; verify:
sudo certbot renew --dry-run
```

Dashboard now at `https://your-domain.com`.

### Step 6: Auth in front of dashboard (important)

Streamlit has no built-in auth. Add HTTP basic auth via nginx:

```bash
sudo apt install -y apache2-utils
sudo htpasswd -c /etc/nginx/.htpasswd mithil
# Enter password
```

Edit `/etc/nginx/sites-available/trading-intel`, add inside `location /`:
```nginx
        auth_basic "trading-intel";
        auth_basic_user_file /etc/nginx/.htpasswd;
```

Reload nginx.

### Step 7: systemd unit for scheduler

```bash
sudo nano /etc/systemd/system/trading-intel-scheduler.service
```

```ini
[Unit]
Description=trading-intel scheduler
After=docker.service
Requires=docker.service

[Service]
Type=simple
User=deploy
WorkingDirectory=/srv/trading-intel
ExecStart=/usr/bin/docker compose -f docker-compose.prod.yml run --rm scheduler
Restart=on-failure
RestartSec=30s

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable trading-intel-scheduler
sudo systemctl start trading-intel-scheduler
sudo systemctl status trading-intel-scheduler
```

Logs:
```bash
journalctl -u trading-intel-scheduler -f
```

### Step 8: Backups

Managed Postgres includes daily backups (7-day retention) — nothing to do.

For PDFs and snapshots:
```bash
# Daily rsync to DO Spaces (s3-compatible)
# Or simpler: a cron job that tar-gzips data/ to a backup location
```

Optional: DO Spaces $5/mo — set up with `s3cmd` and a daily cron.

---

## Deployment automation (GitHub Actions)

`.github/workflows/deploy.yml` (already in the repo) runs on every push to `main`:

1. Checks out the code
2. SSHs into the droplet
3. `cd /srv/trading-intel && git pull`
4. `docker compose -f docker-compose.prod.yml build`
5. `docker compose -f docker-compose.prod.yml run --rm app alembic upgrade head`
6. `docker compose -f docker-compose.prod.yml up -d`
7. `sudo systemctl restart trading-intel-scheduler`
8. Posts a Discord message: "Deploy of <commit hash> complete"

If any step fails, the deploy is rolled back and a Discord alert fires.

---

## Monitoring & observability

Phase 7 baseline:
- **Health endpoint:** `https://your-domain.com/_stcore/health` (Streamlit built-in)
- **System Health dashboard page:** shows last-job times, Convex rate-limit status, DB connection lag, token-usage trends
- **Discord alert on deploy success/failure**
- **Discord alert on any job failure** (added to scheduler wrapper)

Phase 8+ (optional):
- Grafana Cloud free tier with Prometheus exporter — $0
- DO monitoring agent for droplet CPU/memory/disk

---

## Rollback procedure

If a deploy breaks production:

```bash
ssh deploy@droplet
cd /srv/trading-intel
git log --oneline -5             # find the last known-good commit
git checkout <good-commit-sha>
docker compose -f docker-compose.prod.yml build
docker compose -f docker-compose.prod.yml run --rm app alembic upgrade head
docker compose -f docker-compose.prod.yml up -d
```

For schema rollbacks: `alembic downgrade -1` before checking out an older commit.

---

## Cost summary (Phase 7 steady state)

| Item | Monthly |
|---|---:|
| DO droplet (2GB) | $12 |
| DO managed Postgres (1GB) | $12 |
| Domain (annual / 12) | ~$1 |
| Anthropic API (estimated) | $5–20 |
| Voyage embeddings | <$1 |
| **Subtotal (excluding Convex)** | **~$31–46** |
| ConvexValue pro tier | Existing subscription |
| **Total** | **Convex + ~$35** |

Optional add-ons:
- DO Spaces (backups) — $5/mo
- DO monitoring — free
- Grafana Cloud — free tier

---

## Common operational tasks

```bash
# Check scheduler status
sudo systemctl status trading-intel-scheduler

# Tail scheduler logs
journalctl -u trading-intel-scheduler -f

# Tail app logs
docker compose -f docker-compose.prod.yml logs -f app

# Force-restart everything
sudo systemctl restart trading-intel-scheduler
docker compose -f docker-compose.prod.yml restart

# Connect to managed Postgres
psql "$(grep DATABASE_URL .env | cut -d= -f2-)"

# Backup DB manually
pg_dump "$DATABASE_URL" > backups/manual-$(date +%F).sql

# Run a one-off script
docker compose -f docker-compose.prod.yml run --rm app python -m scripts.recalibrate_thrasher
```

---

*Last updated: May 19, 2026. Update whenever deployment topology changes.*
