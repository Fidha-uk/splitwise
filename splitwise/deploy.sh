#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────
# SplitWise — VPS Deployment Script
#
# Tested on: Ubuntu 22.04 / 24.04
# Run as root (or with sudo) on a fresh server:
#   sudo bash deploy.sh
#
# What this does:
#   1. Installs Python 3, pip, nginx
#   2. Creates /var/www/splitwise with a virtualenv
#   3. Copies app files, installs deps
#   4. Creates a systemd service (gunicorn)
#   5. Configures nginx as a reverse proxy
#   6. Starts everything and enables on boot
# ─────────────────────────────────────────────────────────────
set -e

APP_DIR="/var/www/splitwise"
APP_USER="www-data"
DOMAIN="${1:-localhost}"    # pass your domain as first arg, e.g.: bash deploy.sh mysite.com
PORT=5000

GREEN='\033[0;32m'; CYAN='\033[0;36m'; NC='\033[0m'
info()    { echo -e "${CYAN}▶${NC} $1"; }
success() { echo -e "${GREEN}✓${NC} $1"; }

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  SplitWise VPS Deployment"
echo "  Domain: $DOMAIN"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# ── 1. System packages ────────────────────────────────────────
info "Updating system and installing dependencies..."
apt-get update -qq
apt-get install -y -qq python3 python3-pip python3-venv nginx curl
success "System packages installed"

# ── 2. App directory ──────────────────────────────────────────
info "Setting up $APP_DIR..."
mkdir -p "$APP_DIR/instance" "$APP_DIR/uploads"
cp -r ./* "$APP_DIR/"
chown -R "$APP_USER:$APP_USER" "$APP_DIR"
success "Files copied"

# ── 3. Virtual environment ────────────────────────────────────
info "Creating Python virtualenv..."
cd "$APP_DIR"
python3 -m venv venv
venv/bin/pip install --upgrade pip -q
venv/bin/pip install -r requirements.txt -q
success "Python environment ready"

# ── 4. .env file ──────────────────────────────────────────────
if [ ! -f "$APP_DIR/.env" ]; then
    SK=$(python3 -c "import secrets; print(secrets.token_hex(32))")
    cat > "$APP_DIR/.env" << EOF
FLASK_ENV=production
SECRET_KEY=$SK
DATABASE=$APP_DIR/instance/splitwise.db
UPLOAD_FOLDER=$APP_DIR/uploads
HTTPS=false
PORT=$PORT
EOF
    chown "$APP_USER:$APP_USER" "$APP_DIR/.env"
    chmod 600 "$APP_DIR/.env"
    success ".env created"
fi

# ── 5. Systemd service ────────────────────────────────────────
info "Creating systemd service..."
cat > /etc/systemd/system/splitwise.service << EOF
[Unit]
Description=SplitWise Gunicorn Service
After=network.target

[Service]
User=$APP_USER
Group=$APP_USER
WorkingDirectory=$APP_DIR
EnvironmentFile=$APP_DIR/.env
ExecStart=$APP_DIR/venv/bin/gunicorn wsgi:application \\
    --bind 127.0.0.1:$PORT \\
    --workers 4 \\
    --timeout 120 \\
    --access-logfile $APP_DIR/instance/access.log \\
    --error-logfile  $APP_DIR/instance/error.log
Restart=always

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable splitwise
systemctl restart splitwise
success "Systemd service started"

# ── 6. Nginx config ───────────────────────────────────────────
info "Configuring nginx..."
cat > /etc/nginx/sites-available/splitwise << EOF
server {
    listen 80;
    server_name $DOMAIN www.$DOMAIN;

    client_max_body_size 15M;

    location / {
        proxy_pass         http://127.0.0.1:$PORT;
        proxy_set_header   Host              \$host;
        proxy_set_header   X-Real-IP         \$remote_addr;
        proxy_set_header   X-Forwarded-For   \$proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto \$scheme;
        proxy_read_timeout 120;
    }

    location /uploads/ {
        alias $APP_DIR/uploads/;
        expires 7d;
    }
}
EOF

ln -sf /etc/nginx/sites-available/splitwise /etc/nginx/sites-enabled/splitwise
rm -f /etc/nginx/sites-enabled/default
nginx -t
systemctl restart nginx
success "Nginx configured"

# ── 7. Firewall ───────────────────────────────────────────────
if command -v ufw &>/dev/null; then
    ufw allow 'Nginx Full' >/dev/null 2>&1 || true
    ufw allow ssh          >/dev/null 2>&1 || true
    info "Firewall: HTTP/HTTPS and SSH allowed"
fi

echo ""
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "  ✅  Deployment complete!"
echo ""
echo "  App URL:   http://$DOMAIN"
echo "  Logs:      $APP_DIR/instance/error.log"
echo "  Service:   systemctl status splitwise"
echo ""
echo "  Next steps:"
echo "  1. Point your DNS A record → this server's IP"
echo "  2. Add HTTPS with:  sudo apt install certbot python3-certbot-nginx"
echo "                      sudo certbot --nginx -d $DOMAIN"
echo "  3. After HTTPS:     set HTTPS=true in $APP_DIR/.env"
echo "                      systemctl restart splitwise"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
