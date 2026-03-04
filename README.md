# SplitWise — Group Expense Sharing System

**Mini Project · Group 1 · M-DIT Kozhikode · Dept. of CSE · 2026**

A full-stack web application for automated group expense splitting, UPI payments, and debt settlement.

---

## Quick Start (Local)

```bash
# 1. Clone / unzip the project
cd splitwise

# 2. Run setup (creates venv, installs deps, generates .env)
bash install.sh

# 3. Start the app
source venv/bin/activate
python app.py
```

Open **http://localhost:5000** — click **"Load Demo Data"** on the login page to get started instantly.

**Demo credentials:** `amaya@demo.com` / `demo123`

---

## Deploy to VPS / Cloud Server

```bash
# On your server (Ubuntu 22.04+), as root:
sudo bash deploy.sh yourdomain.com
```

That single command will:
- Install Python 3, Nginx
- Create `/var/www/splitwise` with a virtualenv
- Set up a **Gunicorn** WSGI server (4 workers)
- Configure **Nginx** as a reverse proxy
- Create a **systemd** service (auto-starts on reboot)
- Open firewall ports 80/443

### After deploying — add HTTPS (free via Let's Encrypt)
```bash
sudo apt install certbot python3-certbot-nginx
sudo certbot --nginx -d yourdomain.com
# Then edit /var/www/splitwise/.env → set HTTPS=true
sudo systemctl restart splitwise
```

---

## Project Structure

```
splitwise/
├── app.py                  # Flask application — all routes & logic
├── wsgi.py                 # Gunicorn entry point
├── config.py               # Config classes (dev / prod)
├── requirements.txt        # Python dependencies
├── install.sh              # One-command local setup
├── deploy.sh               # VPS deployment automation
├── nginx.conf              # Nginx reverse proxy config
├── splitwise.service       # Systemd unit file
├── .env.example            # Environment variable template
├── .gitignore
├── instance/               # SQLite database (auto-created)
│   └── splitwise.db
├── uploads/                # OCR bill images (auto-created)
└── templates/
    ├── index.html          # Landing + auth page
    └── dashboard.html      # Full SPA dashboard
```

---

## Features

| Feature | Details |
|---------|---------|
| **Auth** | Register/login with bcrypt-hashed passwords, phone number support |
| **Groups** | Create groups, add members by email or phone |
| **Expenses** | Add with payer, category, date, notes |
| **OCR** | Scan receipt image → auto-extract amount (requires Tesseract) |
| **Split Types** | Equal split or custom per-person amounts |
| **Balances** | Real-time net balance per user per group |
| **Settle Up** | Minimum-transaction algorithm to clear debts |
| **Payments** | Record payments, confirm receipt, auto-settle splits |
| **Simulate** | Mock payment gateway with TXN ID + UTR generation |
| **UPI QR** | Generate scannable QR codes (works with all BHIM apps) |
| **Deep Links** | Open GPay / PhonePe / Paytm with pre-filled amount |
| **My Wallet** | Save & manage UPI IDs, bank accounts, cards |
| **Reports** | Category spending, totals, net positions |

---

## API Reference

### Auth
| Method | Endpoint | Body |
|--------|----------|------|
| POST | `/api/register` | `{name, email, password, phone?, currency?}` |
| POST | `/api/login` | `{email, password}` |
| POST | `/api/logout` | — |
| GET  | `/api/me` | — |

### Groups
| Method | Endpoint | Body |
|--------|----------|------|
| GET  | `/api/groups` | — |
| POST | `/api/groups` | `{name, description?, member_emails?[]}` |
| GET  | `/api/groups/<id>` | — |
| POST | `/api/groups/<id>/members` | `{identifier}` (email or phone) |

### Expenses
| Method | Endpoint | Body |
|--------|----------|------|
| POST   | `/api/groups/<id>/expenses` | `{title, amount, currency?, payer_id?, category?, split_type?, custom_splits?, date?, notes?}` |
| DELETE | `/api/expenses/<id>` | — |
| POST   | `/api/expenses/<id>/settle` | `{user_id?}` |

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
| Method | Endpoint |
|--------|----------|
| POST | `/api/ocr` (multipart file) |
| GET  | `/api/stats` |
| POST | `/api/seed` |
| GET  | `/health` |

---

## Database Schema

```
user            id, name, email, phone, password, currency
grp             id, name, description, created_by
group_member    group_id, user_id
expense         id, group_id, payer_id, title, amount, currency,
                category, notes, split_type, date
split_detail    expense_id, user_id, share, is_paid, paid_at
payment_method  user_id, type, label, details, is_default
payment         group_id, from_user_id, to_user_id, amount,
                method_type, method_label, status, reference
```

---

## Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `FLASK_ENV` | `development` | `development` or `production` |
| `SECRET_KEY` | *(required in prod)* | Random 32-char string |
| `DATABASE` | `instance/splitwise.db` | Absolute path to SQLite file |
| `UPLOAD_FOLDER` | `uploads/` | Bill image storage |
| `HTTPS` | `false` | Set `true` behind HTTPS for secure cookies |
| `PORT` | `5000` | Server port |

---

## Server Management

```bash
# Check service status
sudo systemctl status splitwise

# Restart after code changes
sudo systemctl restart splitwise

# View live logs
sudo journalctl -u splitwise -f

# View Nginx logs
sudo tail -f /var/www/splitwise/instance/access.log
sudo tail -f /var/www/splitwise/instance/error.log
```

---

## Enable OCR Bill Scanning

```bash
# Install Tesseract engine
sudo apt install tesseract-ocr       # Ubuntu/Debian
brew install tesseract               # macOS

# Install Python bindings
source venv/bin/activate
pip install pytesseract Pillow
sudo systemctl restart splitwise
```
