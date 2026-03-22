# SplitWise — Group Expense Sharing System



A full-stack web application for automated group expense splitting, multi-currency support, AI-powered receipt scanning, UPI payments, and debt settlement.

---

## Quick Start (Local)

```bash
# 1. Clone the repository
git clone https://github.com/YOUR_USERNAME/splitwise.git
cd splitwise

# 2. Create a virtual environment and install dependencies
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt

# 3. Create your .env file
cp .env.example .env
# Open .env and set SECRET_KEY to any long random string

# 4. Start the app
python app.py
```

Open **http://localhost:5000** and register your account to get started.

---

## Deploy to Render (Recommended — Free)

1. Push this repo to GitHub
2. Go to [render.com](https://render.com) → New → Web Service → connect your repo
3. Render auto-detects `render.yaml` — just add your environment variables:

| Key | Value |
|-----|-------|
| `SECRET_KEY` | Any long random string (30+ chars) |
| `FLASK_ENV` | `production` |
| `HTTPS` | `true` |
| `ANTHROPIC_API_KEY` | Your key from console.anthropic.com *(optional — for AI receipt scanning)* |

4. Click **Create Web Service** — live in ~3 minutes.

### Updating after changes
```bash
git add .
git commit -m "describe your change"
git push
```
Render redeploys automatically on every push.

---

## Deploy to VPS / Cloud Server

```bash
# On your Ubuntu 22.04+ server as root:
sudo bash deploy.sh yourdomain.com
```

This will install Python 3 + Nginx, set up Gunicorn with 4 workers, configure a systemd service, and open firewall ports.

### Add HTTPS (free via Let's Encrypt)
```bash
sudo apt install certbot python3-certbot-nginx
sudo certbot --nginx -d yourdomain.com
# Then in /var/www/splitwise/.env set HTTPS=true
sudo systemctl restart splitwise
```

---

## Project Structure

```
splitwise/
├── app.py                  # Flask application — all routes & logic
├── wsgi.py                 # Gunicorn entry point
├── render.yaml             # Render.com deployment config
├── requirements.txt        # Python dependencies
├── deploy.sh               # VPS deployment script
├── nginx.conf              # Nginx reverse proxy config
├── splitwise.service       # Systemd unit file
├── .env.example            # Environment variable template
├── .gitignore
├── instance/               # SQLite database (auto-created, gitignored)
│   └── splitwise.db
├── uploads/                # Receipt images (auto-created, gitignored)
└── templates/
    ├── index.html          # Landing + auth page
    └── dashboard.html      # Full SPA dashboard
```

---

## Features

| Feature | Details |
|---------|---------|
| **Auth** | Register/login with bcrypt-hashed passwords, session management |
| **Groups** | Create, edit, delete groups — add members by email or phone |
| **Member Management** | Remove members, leave groups, creator badge |
| **Expenses** | Add, edit, delete — with payer, category, date, notes |
| **Split Types** | Equal, Percentage, Shares/Ratio, Exact amounts, By Item |
| **Multi-Currency** | 20 currencies with live INR conversion hint |
| **AI OCR** | Scan receipt → auto-fills amount, title & currency via Claude Vision |
| **Balances** | Real-time net balance per user per group |
| **Settle Up** | Minimum-transaction algorithm to clear all debts |
| **Payments** | Record payments, confirm receipt, auto-settle splits |
| **Simulate** | Mock payment gateway with TXN ID + UTR generation |
| **UPI / QR** | Scannable QR codes — works with GPay, PhonePe, Paytm |
| **Deep Links** | Open payment apps with pre-filled amount |
| **My Wallet** | Save & manage UPI IDs, bank accounts, cards |
| **Profile** | Edit name, phone, default currency, change password |
| **Reports** | Category spending, totals, net positions |
| **Security** | Rate-limited login, security headers, input validation |

---

## API Reference

### Auth
| Method | Endpoint | Body |
|--------|----------|------|
| POST | `/api/register` | `{name, email, password, phone?, currency?}` |
| POST | `/api/login` | `{email, password}` |
| POST | `/api/logout` | — |
| GET  | `/api/me` | — |
| PUT  | `/api/me` | `{name, phone?, currency?}` |
| PUT  | `/api/me/password` | `{old_password, new_password}` |

### Groups
| Method | Endpoint | Body |
|--------|----------|------|
| GET    | `/api/groups` | — |
| POST   | `/api/groups` | `{name, description?, member_emails?[]}` |
| GET    | `/api/groups/<id>` | — |
| PUT    | `/api/groups/<id>` | `{name, description?}` |
| DELETE | `/api/groups/<id>` | — *(creator only)* |

### Members
| Method | Endpoint | Body |
|--------|----------|------|
| POST   | `/api/groups/<id>/members` | `{identifier}` (email or phone) |
| DELETE | `/api/groups/<id>/members/<user_id>` | — *(creator only)* |
| POST   | `/api/groups/<id>/leave` | — |

### Expenses
| Method | Endpoint | Body |
|--------|----------|------|
| POST   | `/api/groups/<id>/expenses` | `{title, amount, currency?, payer_id?, category?, split_type?, custom_splits?, percentage_splits?, share_splits?, item_splits?, date?, notes?}` |
| PUT    | `/api/expenses/<id>` | `{title?, amount?, currency?, category?, notes?, date?}` |
| DELETE | `/api/expenses/<id>` | — *(payer only)* |
| POST   | `/api/expenses/<id>/settle` | `{user_id?}` |

### Split Types
| `split_type` | Extra field required |
|---|---|
| `equal` | *(none — splits evenly)* |
| `percentage` | `percentage_splits: {user_id: percent}` — must total 100 |
| `shares` | `share_splits: {user_id: share_count}` — e.g. 2:1:1 ratio |
| `custom` | `custom_splits: {user_id: amount}` |
| `items` | `item_splits: {user_id: amount}` |

### Payments
| Method | Endpoint | Body |
|--------|----------|------|
| GET  | `/api/groups/<id>/payments` | — |
| POST | `/api/groups/<id>/payments` | `{to_user_id, amount, currency?, method_id?, note?, reference?}` |
| POST | `/api/payments/<id>/confirm` | — |
| POST | `/api/payments/<id>/reject` | — |
| POST | `/api/payments/<id>/simulate` | — |
| GET  | `/api/payments/pending` | — |

### Payment Methods
| Method | Endpoint | Body |
|--------|----------|------|
| GET    | `/api/payment-methods` | — |
| POST   | `/api/payment-methods` | `{type, label, details, is_default?}` |
| DELETE | `/api/payment-methods/<id>` | — |
| POST   | `/api/payment-methods/<id>/default` | — |

### UPI / QR
| Method | Endpoint | Body |
|--------|----------|------|
| POST | `/api/upi-qr` | `{upi_id, name, amount, currency?, note?}` |
| GET  | `/api/users/<id>/upi` | — |

### Utilities
| Method | Endpoint | Notes |
|--------|----------|-------|
| POST | `/api/ocr` | Multipart file upload — returns `{amount, title, currency, text, source}` |
| GET  | `/api/stats` | Returns totals, balances, category breakdown |
| GET  | `/health` | Server health check |

---

## Database Schema

```
user            id, name, email, phone, password, currency, created_at
grp             id, name, description, created_by, created_at
group_member    id, group_id, user_id, joined_at
expense         id, group_id, payer_id, title, amount, currency,
                category, notes, split_type, date, created_at
split_detail    id, expense_id, user_id, share, is_paid, paid_at
payment_method  id, user_id, type, label, details, is_default, created_at
payment         id, group_id, from_user_id, to_user_id, amount, currency,
                method_type, method_label, note, status, reference,
                created_at, confirmed_at
```

---

## Environment Variables

| Variable | Required | Description |
|----------|----------|-------------|
| `SECRET_KEY` | **Yes** | Long random string — app won't start without it |
| `FLASK_ENV` | No | `development` or `production` (default: development) |
| `DATABASE` | No | Absolute path to SQLite file (default: `instance/splitwise.db`) |
| `UPLOAD_FOLDER` | No | Receipt image storage path (default: `uploads/`) |
| `HTTPS` | No | Set `true` behind HTTPS to enable secure cookies |
| `PORT` | No | Server port (default: `5000`) |
| `ANTHROPIC_API_KEY` | No | Enables AI-powered receipt scanning via Claude Vision |

---

## Supported Currencies

INR, USD, EUR, GBP, JPY, CNY, AUD, CAD, CHF, SGD, AED, MYR, THB, IDR, BRL, MXN, ZAR, KRW, TRY, SAR

---

## Security

- Passwords hashed with **bcrypt** (Werkzeug)
- Login brute-force protection — **10 attempts per IP per 15 minutes**
- Session cookies: `HttpOnly`, `SameSite=Lax`, `Secure` (when HTTPS=true)
- Security headers on every response: `X-Frame-Options`, `X-Content-Type-Options`, `X-XSS-Protection`, `Referrer-Policy`
- All API routes require authentication except `/api/login`, `/api/register`, `/health`
- Input length validation on all user-supplied fields
- SQL injection protected via parameterised queries throughout

---

## Server Management (VPS)

```bash
sudo systemctl status splitwise      # Check status
sudo systemctl restart splitwise     # Restart after changes
sudo journalctl -u splitwise -f      # Live logs
sudo tail -f /var/www/splitwise/instance/access.log   # Nginx access log
sudo tail -f /var/www/splitwise/instance/error.log    # Nginx error log
```

---

## Enable OCR Receipt Scanning

The app uses **Claude Vision API** (if `ANTHROPIC_API_KEY` is set) for best results.
It falls back to Tesseract OCR automatically.

```bash
# Install Tesseract (fallback)
sudo apt install tesseract-ocr       # Ubuntu/Debian
brew install tesseract               # macOS

pip install pytesseract Pillow
sudo systemctl restart splitwise
```

---

*Built with Flask · SQLite · Vanilla JS · Deployed on Render*
