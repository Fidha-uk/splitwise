"""
SplitWise — Automated Group Expense Sharing System
Flask + SQLite  |  Production-ready
"""
from flask import Flask, render_template, request, jsonify, session, redirect
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
from datetime import datetime
from functools import wraps
import sqlite3, os, uuid, re, time, hashlib, random

# Auto-detect Tesseract on Windows — set path before anything else
try:
    import pytesseract as _pt
    import subprocess as _sp
    _tess_paths = [
        r"C:\Program Files\Tesseract-OCR\tesseract.exe",
        r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe",
        r"C:\Users\lenovo\AppData\Local\Programs\Tesseract-OCR\tesseract.exe",
        r"C:\Users\lenovo\AppData\Local\Tesseract-OCR\tesseract.exe",
    ]
    for _p in _tess_paths:
        if os.path.exists(_p):
            _pt.pytesseract.tesseract_cmd = _p
            os.environ['TESSDATA_PREFIX'] = os.path.dirname(_p)
            break
    else:
        # Try to find via where command on Windows
        try:
            _res = _sp.run(['where', 'tesseract'], capture_output=True, text=True)
            if _res.returncode == 0 and _res.stdout.strip():
                _pt.pytesseract.tesseract_cmd = _res.stdout.strip().splitlines()[0]
        except Exception:
            pass
except ImportError:
    pass

# Load .env file if present
try:
    from pathlib import Path
    _env = Path(__file__).parent / '.env'
    if _env.exists():
        for _line in _env.read_text().splitlines():
            _line = _line.strip()
            if _line and not _line.startswith('#') and '=' in _line:
                _k, _v = _line.split('=', 1)
                os.environ.setdefault(_k.strip(), _v.strip().strip('"').strip("'"))
except Exception:
    pass

# ── CONFIG ────────────────────────────────────────────────────
class Config:
    BASE_DIR           = os.path.abspath(os.path.dirname(__file__))
    SECRET_KEY         = os.environ.get('SECRET_KEY','change-me-in-production')
    DATABASE           = os.environ.get('DATABASE', os.path.join(BASE_DIR,'instance','splitwise.db'))
    UPLOAD_FOLDER      = os.environ.get('UPLOAD_FOLDER', os.path.join(BASE_DIR,'uploads'))
    MAX_CONTENT_LENGTH = 10*1024*1024
    DEBUG              = os.environ.get('FLASK_ENV','development') == 'development'
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = 'Lax'
    SESSION_COOKIE_SECURE   = os.environ.get('HTTPS','false').lower()=='true'

# ── APP ───────────────────────────────────────────────────────
app = Flask(__name__)
app.config.from_object(Config)
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)
os.makedirs(os.path.dirname(app.config['DATABASE']), exist_ok=True)

SCHEMA = """
PRAGMA foreign_keys=ON;
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS user(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL, email TEXT UNIQUE NOT NULL, phone TEXT UNIQUE,
    password TEXT NOT NULL, currency TEXT DEFAULT 'INR',
    is_banned INTEGER DEFAULT 0,
    created_at TEXT DEFAULT(datetime('now')));
CREATE TABLE IF NOT EXISTS grp(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL, description TEXT DEFAULT '', created_by INTEGER NOT NULL,
    created_at TEXT DEFAULT(datetime('now')),
    FOREIGN KEY(created_by) REFERENCES user(id));
CREATE TABLE IF NOT EXISTS group_member(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
    joined_at TEXT DEFAULT(datetime('now')),
    FOREIGN KEY(group_id) REFERENCES grp(id) ON DELETE CASCADE,
    FOREIGN KEY(user_id)  REFERENCES user(id));
CREATE TABLE IF NOT EXISTS expense(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id INTEGER NOT NULL, payer_id INTEGER NOT NULL,
    title TEXT NOT NULL, amount REAL NOT NULL, currency TEXT DEFAULT 'INR',
    category TEXT DEFAULT 'General', notes TEXT DEFAULT '',
    ocr_raw_text TEXT DEFAULT '', image_filename TEXT DEFAULT '',
    split_type TEXT DEFAULT 'equal', date TEXT DEFAULT(datetime('now')),
    created_at TEXT DEFAULT(datetime('now')),
    FOREIGN KEY(group_id) REFERENCES grp(id) ON DELETE CASCADE,
    FOREIGN KEY(payer_id) REFERENCES user(id));
CREATE TABLE IF NOT EXISTS split_detail(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    expense_id INTEGER NOT NULL, user_id INTEGER NOT NULL,
    share REAL NOT NULL, is_paid INTEGER DEFAULT 0, paid_at TEXT,
    FOREIGN KEY(expense_id) REFERENCES expense(id) ON DELETE CASCADE,
    FOREIGN KEY(user_id)    REFERENCES user(id));
CREATE TABLE IF NOT EXISTS payment_method(
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER NOT NULL,
    type TEXT NOT NULL, label TEXT NOT NULL, details TEXT NOT NULL,
    is_default INTEGER DEFAULT 0, created_at TEXT DEFAULT(datetime('now')),
    FOREIGN KEY(user_id) REFERENCES user(id) ON DELETE CASCADE);
CREATE TABLE IF NOT EXISTS payment(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    group_id INTEGER NOT NULL, from_user_id INTEGER NOT NULL, to_user_id INTEGER NOT NULL,
    amount REAL NOT NULL, currency TEXT DEFAULT 'INR',
    method_id INTEGER, method_type TEXT DEFAULT 'cash', method_label TEXT DEFAULT '',
    note TEXT DEFAULT '', status TEXT DEFAULT 'pending', reference TEXT DEFAULT '',
    created_at TEXT DEFAULT(datetime('now')), confirmed_at TEXT,
    FOREIGN KEY(group_id)     REFERENCES grp(id) ON DELETE CASCADE,
    FOREIGN KEY(from_user_id) REFERENCES user(id),
    FOREIGN KEY(to_user_id)   REFERENCES user(id),
    FOREIGN KEY(method_id)    REFERENCES payment_method(id) ON DELETE SET NULL);
CREATE INDEX IF NOT EXISTS idx_gm_g  ON group_member(group_id);
CREATE INDEX IF NOT EXISTS idx_gm_u  ON group_member(user_id);
CREATE INDEX IF NOT EXISTS idx_e_g   ON expense(group_id);
CREATE INDEX IF NOT EXISTS idx_p_g   ON payment(group_id);
CREATE INDEX IF NOT EXISTS idx_p_to  ON payment(to_user_id);
CREATE TABLE IF NOT EXISTS password_reset(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER NOT NULL,
    otp TEXT NOT NULL,
    expires_at REAL NOT NULL,
    used INTEGER DEFAULT 0,
    FOREIGN KEY(user_id) REFERENCES user(id) ON DELETE CASCADE);
"""

def get_db():
    conn=sqlite3.connect(app.config['DATABASE'])
    conn.row_factory=sqlite3.Row
    return conn

def init_db():
    with get_db() as db: db.executescript(SCHEMA)

def r2d(r): return dict(r) if r else None
def rs(rows): return [dict(r) for r in rows]
def now(): return datetime.utcnow().isoformat()

def normalize_phone(p):
    if not p: return None
    c=re.sub(r'[\s\-().]+','',p.strip())
    if re.fullmatch(r'[6-9]\d{9}',c): c='+91'+c
    return c or None

def balances(db, gid):
    b={m['user_id']:0.0 for m in db.execute("SELECT user_id FROM group_member WHERE group_id=?",(gid,)).fetchall()}
    for e in db.execute("SELECT id,payer_id,amount FROM expense WHERE group_id=?",(gid,)).fetchall():
        if e['payer_id'] in b: b[e['payer_id']]+=e['amount']
        for s in db.execute("SELECT user_id,share,is_paid FROM split_detail WHERE expense_id=?",(e['id'],)).fetchall():
            if not s['is_paid'] and s['user_id'] in b: b[s['user_id']]-=s['share']
    return b

def settle(b):
    cr=sorted([(u,v) for u,v in b.items() if v>0.01],key=lambda x:-x[1])
    de=sorted([(u,-v) for u,v in b.items() if v<-0.01],key=lambda x:-x[1])
    txns,i,j=[],0,0
    while i<len(cr) and j<len(de):
        cu,ca=cr[i]; du,da=de[j]; amt=min(ca,da)
        txns.append({'from':du,'to':cu,'amount':round(amt,2)})
        cr[i]=(cu,ca-amt); de[j]=(du,da-amt)
        if cr[i][1]<0.01: i+=1
        if de[j][1]<0.01: j+=1
    return txns

def auto_settle(db, gid, puid, amount):
    rem=amount
    for s in db.execute("""SELECT sd.id,sd.share FROM split_detail sd
        JOIN expense e ON e.id=sd.expense_id
        WHERE e.group_id=? AND sd.user_id=? AND sd.is_paid=0 ORDER BY e.date""",(gid,puid)).fetchall():
        if rem<=0: break
        if s['share']<=rem+0.01:
            db.execute("UPDATE split_detail SET is_paid=1,paid_at=? WHERE id=?",(now(),s['id']))
            rem-=s['share']
    return round(max(rem,0),2)

ALLOWED={'png','jpg','jpeg','gif','webp'}
def ok_file(fn): return '.'+fn.rsplit('.',1)[-1].lower() in ['.'+x for x in ALLOWED]

def ocr_extract(path):
    """OCR using easyocr — free, no Tesseract needed, works on Windows."""
    try:
        import easyocr
        import json as _json

        # Init reader (downloads model first time ~100MB, then cached)
        reader = easyocr.Reader(['en'], gpu=False, verbose=False)
        results = reader.readtext(path, detail=0, paragraph=False)
        text = '\n'.join(results)
        lines = [l.strip() for l in text.splitlines() if l.strip()]

        if not lines:
            return {
                'source': 'error', 'bill_type': 'other', 'store_name': '', 'title': '',
                'currency': 'INR', 'date': None, 'items': [], 'subtotal': None, 'taxes': [],
                'discount': 0, 'service_charge': 0, 'tip': 0, 'amount': None,
                'payment_method': 'unknown', 'text': '',
                'error': 'No text found in image', 'setup_hint': 'Try a clearer image'
            }

        store_name = lines[0] if lines else ''

        # Bill type detection
        text_lower = text.lower()
        bill_type = 'other'
        if any(w in text_lower for w in ['restaurant','cafe','dine','food','swiggy','zomato','biryani','pizza','burger','meals','hotel']):
            bill_type = 'restaurant'
        elif any(w in text_lower for w in ['grocery','supermarket','dmart','bigbasket','vegetables','fruits']):
            bill_type = 'grocery'
        elif any(w in text_lower for w in ['pharmacy','medical','medicine','chemist','drug','tablet']):
            bill_type = 'pharmacy'
        elif any(w in text_lower for w in ['petrol','diesel','fuel','bpcl','hpcl','indian oil']):
            bill_type = 'fuel'
        elif any(w in text_lower for w in ['electricity','water bill','utility','bescom','tneb']):
            bill_type = 'utility'
        elif any(w in text_lower for w in ['uber','ola','rapido','bus','train','flight','metro','cab']):
            bill_type = 'transport'
        elif any(w in text_lower for w in ['mall','shop','fashion','garment','shoes','footwear']):
            bill_type = 'shopping'

        # Date extraction
        date_val = None
        for pat in [r'(\d{4}[-/]\d{2}[-/]\d{2})', r'(\d{2}[-/]\d{2}[-/]\d{4})',
                    r'(\d{1,2}\s+(?:jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*\s+\d{4})']:
            m = re.search(pat, text, re.I)
            if m:
                date_val = m.group(1)
                break

        # Extract items
        items = []
        skip_words = {'total','subtotal','grand','amount','balance','tax','gst','cgst','sgst',
                      'vat','discount','service','charge','tip','cash','change','due','net',
                      'bill','invoice','receipt','thank','visit','date','time','table','order',
                      'no','number','phone','address','gstin','pan','upi','card','paid','payment'}
        item_pat = re.compile(
            r'^(.+?)\s+(?:x\s*\d+\s+)?(?:Rs\.?\s*|₹\s*|INR\s*)?(\d+(?:,\d+)*(?:\.\d{1,2})?)\s*$', re.I)
        for line in lines:
            m = item_pat.match(line)
            if m:
                name = m.group(1).strip()
                if name.lower() in skip_words or len(name) < 2:
                    continue
                try:
                    price = float(m.group(2).replace(',',''))
                    if 0.5 < price < 100000:
                        items.append({'name': name, 'qty': 1, 'unit_price': price, 'total': price})
                except: pass

        # Extract taxes
        taxes = []
        tax_pat = re.compile(
            r'((?:cgst|sgst|igst|gst|vat|service\s*tax)[^\n]*?(?:\d+(?:\.\d+)?\s*%)?)'
            r'[\s:₹Rs.]*([0-9,]+(?:\.[0-9]{1,2})?)', re.I)
        for m in tax_pat.finditer(text):
            label = m.group(1).strip()[:30]
            try:
                amt = float(m.group(2).replace(',',''))
                if 0 < amt < 10000:
                    rate_m = re.search(r'(\d+(?:\.\d+)?)\s*%', label)
                    rate = float(rate_m.group(1)) if rate_m else None
                    taxes.append({'label': label, 'rate': rate, 'amount': amt})
            except: pass

        # Discount
        discount = 0.0
        disc_m = re.search(r'(?:discount|offer|savings?)[\s:₹Rs.]*([0-9,]+(?:\.[0-9]{1,2})?)', text, re.I)
        if disc_m:
            try: discount = float(disc_m.group(1).replace(',',''))
            except: pass

        # Total — priority order
        total = None
        for pat in [
            r'(?:grand\s*total|net\s*(?:amount|payable|total)|amount\s*(?:due|payable)|total\s*amount|total\s*payable)[\s:₹Rs.]*([0-9,]+(?:\.[0-9]{1,2})?)',
            r'(?:^|\n)\s*total[\s:₹Rs.]*([0-9,]+(?:\.[0-9]{1,2})?)\s*(?:\n|$)',
        ]:
            m = re.search(pat, text, re.I | re.M)
            if m:
                try: total = float(m.group(1).replace(',','')); break
                except: pass

        if not total:
            rs_vals = re.findall(r'(?:₹|Rs\.?|INR)\s*([0-9,]+(?:\.[0-9]{1,2})?)', text, re.I)
            candidates = []
            for x in rs_vals:
                try:
                    v = float(x.replace(',',''))
                    if 1 < v < 1000000: candidates.append(v)
                except: pass
            if candidates: total = max(candidates)

        if not total:
            nums = re.findall(r'\b([0-9]{2,6}(?:\.[0-9]{1,2})?)\b', text)
            candidates = []
            for x in nums:
                try:
                    v = float(x.replace(',',''))
                    if 10 < v < 500000: candidates.append(v)
                except: pass
            if candidates: total = candidates[-1]

        # Subtotal
        tax_total = sum(t['amount'] for t in taxes)
        subtotal = None
        sub_m = re.search(r'(?:sub\s*total|subtotal)[\s:₹Rs.]*([0-9,]+(?:\.[0-9]{1,2})?)', text, re.I)
        if sub_m:
            try: subtotal = float(sub_m.group(1).replace(',',''))
            except: pass
        if not subtotal and total and tax_total:
            subtotal = round(total - tax_total + discount, 2)

        title = store_name or bill_type.title() + ' bill'

        return {
            'source': 'easyocr',
            'bill_type': bill_type, 'store_name': store_name, 'title': title,
            'currency': 'INR', 'date': date_val, 'items': items,
            'subtotal': subtotal, 'taxes': taxes, 'discount': discount,
            'service_charge': 0, 'tip': 0, 'amount': total,
            'payment_method': 'unknown', 'text': text[:300]
        }

    except ImportError:
        # Fallback to pytesseract if easyocr not installed
        try:
            import pytesseract
            from PIL import Image, ImageEnhance, ImageFilter
            pytesseract.pytesseract.tesseract_cmd = r"C:\Program Files\Tesseract-OCR\tesseract.exe"
            img = Image.open(path).convert('L')
            img = ImageEnhance.Contrast(img).enhance(2.5)
            img = ImageEnhance.Sharpness(img).enhance(2.0)
            text = pytesseract.image_to_string(img, config='--psm 6')
            nums = re.findall(r'\b([0-9]{2,6}(?:\.[0-9]{1,2})?)\b', text)
            candidates = [float(x.replace(',','')) for x in nums if 10 < float(x.replace(',','')) < 500000]
            total = candidates[-1] if candidates else None
            return {
                'source': 'tesseract', 'bill_type': 'other', 'store_name': '', 'title': '',
                'currency': 'INR', 'date': None, 'items': [], 'subtotal': None, 'taxes': [],
                'discount': 0, 'service_charge': 0, 'tip': 0, 'amount': total,
                'payment_method': 'unknown', 'text': text[:300]
            }
        except Exception as e:
            return {
                'source': 'error', 'bill_type': 'other', 'store_name': '', 'title': '',
                'currency': 'INR', 'date': None, 'items': [], 'subtotal': None, 'taxes': [],
                'discount': 0, 'service_charge': 0, 'tip': 0, 'amount': None,
                'payment_method': 'unknown', 'text': '',
                'error': str(e), 'setup_hint': 'Run: .venv\\Scripts\\pip install easyocr'
            }
    except Exception as e:
        return {
            'source': 'error', 'bill_type': 'other', 'store_name': '', 'title': '',
            'currency': 'INR', 'date': None, 'items': [], 'subtotal': None, 'taxes': [],
            'discount': 0, 'service_charge': 0, 'tip': 0, 'amount': None,
            'payment_method': 'unknown', 'text': '',
            'error': str(e), 'setup_hint': 'Run: .venv\\Scripts\\pip install easyocr'
        }


def login_required(f):
    @wraps(f)
    def dec(*a,**kw):
        if not session.get('user_id'): return jsonify({'error':'Unauthorized'}),401
        return f(*a,**kw)
    return dec

def uid(): return session.get('user_id')

# ── PAGES ─────────────────────────────────────────────────────
@app.route('/')
def index():
    return redirect('/dashboard') if session.get('user_id') else render_template('index.html')

@app.route('/dashboard')
def dashboard():
    return render_template('dashboard.html') if session.get('user_id') else redirect('/')

@app.after_request
def security_headers(resp):
    resp.headers['X-Content-Type-Options'] = 'nosniff'
    resp.headers['X-Frame-Options'] = 'DENY'
    resp.headers['X-XSS-Protection'] = '1; mode=block'
    resp.headers['Referrer-Policy'] = 'strict-origin-when-cross-origin'
    resp.headers['Permissions-Policy'] = 'geolocation=(), microphone=()'
    return resp

@app.route('/health')
def health(): return jsonify({'status':'ok','time':now()})

# ── AUTH ──────────────────────────────────────────────────────
@app.route('/api/register',methods=['POST'])
def register():
    d=request.json or {}
    name=(d.get('name') or '').strip(); email=(d.get('email') or '').strip().lower()
    pw=d.get('password',''); phone=normalize_phone(d.get('phone',''))
    if not name or not email or not pw: return jsonify({'error':'Name, email and password required'}),400
    if len(name)>100 or len(email)>200: return jsonify({'error':'Input too long'}),400
    if len(pw)>128: return jsonify({'error':'Password too long'}),400
    if len(pw)<6: return jsonify({'error':'Password min 6 chars'}),400
    with get_db() as db:
        if db.execute("SELECT id FROM user WHERE email=?",(email,)).fetchone():
            return jsonify({'error':'Email already registered'}),400
        if phone and db.execute("SELECT id FROM user WHERE phone=?",(phone,)).fetchone():
            return jsonify({'error':'Phone already registered'}),400
        db.execute("INSERT INTO user(name,email,phone,password,currency) VALUES(?,?,?,?,?)",
            (name,email,phone,generate_password_hash(pw),d.get('currency','INR')))
        new_uid=db.execute("SELECT last_insert_rowid()").fetchone()[0]; db.commit()
    session['user_id']=new_uid
    return jsonify({'id':new_uid,'name':name,'email':email,'phone':phone})

_login_attempts = {}
@app.route('/api/login',methods=['POST'])
def login():
    ip = request.headers.get('X-Forwarded-For', request.remote_addr or 'unknown').split(',')[0].strip()
    now_ts = time.time()
    attempts = _login_attempts.get(ip, [])
    attempts = [t for t in attempts if now_ts - t < 900]  # 15 min window
    if len(attempts) >= 10:
        return jsonify({'error':'Too many login attempts. Try again in 15 minutes.'}), 429
    d=request.json or {}
    with get_db() as db:
        if d.get('phone'):
            pn = normalize_phone(d.get('phone',''))
            u = r2d(db.execute("SELECT * FROM user WHERE phone=?",(pn,)).fetchone())
        else:
            u = r2d(db.execute("SELECT * FROM user WHERE email=?",(d.get('email','').lower(),)).fetchone())
    if not u or not check_password_hash(u['password'],d.get('password','')):
        attempts.append(now_ts)
        _login_attempts[ip] = attempts
        return jsonify({'error':'Invalid email/phone or password'}),401
    if u.get('is_banned'):
        return jsonify({'error':'Your account has been suspended. Please contact support.'}),403
    _login_attempts.pop(ip, None)
    session['user_id']=u['id']
    session.permanent = True
    return jsonify({'id':u['id'],'name':u['name'],'email':u['email'],'currency':u['currency']})

@app.route('/api/forgot-password', methods=['POST'])
def forgot_password():
    d = request.json or {}
    identifier = (d.get('identifier') or '').strip()
    if not identifier: return jsonify({'error': 'Email or phone required'}), 400
    with get_db() as db:
        if '@' in identifier:
            u = r2d(db.execute("SELECT id,name,email FROM user WHERE email=?", (identifier.lower(),)).fetchone())
        else:
            pn = normalize_phone(identifier)
            u = r2d(db.execute("SELECT id,name,email FROM user WHERE phone=?", (pn,)).fetchone())
        if not u: return jsonify({'error': 'No account found with that email/phone'}), 404
        otp = str(random.randint(100000, 999999))
        expires = time.time() + 600  # 10 min
        db.execute("UPDATE password_reset SET used=1 WHERE user_id=?", (u['id'],))
        db.execute("INSERT INTO password_reset(user_id,otp,expires_at) VALUES(?,?,?)", (u['id'], otp, expires))
    # In production: send OTP via email/SMS. For demo, return it directly.
    return jsonify({'ok': True, 'otp': otp, 'name': u['name'], 'demo': True})

@app.route('/api/reset-password', methods=['POST'])
def reset_password():
    d = request.json or {}
    identifier = (d.get('identifier') or '').strip()
    otp = (d.get('otp') or '').strip()
    new_pw = d.get('password', '')
    if not identifier or not otp or not new_pw: return jsonify({'error': 'All fields required'}), 400
    if len(new_pw) < 6: return jsonify({'error': 'Password min 6 characters'}), 400
    with get_db() as db:
        if '@' in identifier:
            u = r2d(db.execute("SELECT id FROM user WHERE email=?", (identifier.lower(),)).fetchone())
        else:
            pn = normalize_phone(identifier)
            u = r2d(db.execute("SELECT id FROM user WHERE phone=?", (pn,)).fetchone())
        if not u: return jsonify({'error': 'Account not found'}), 404
        row = r2d(db.execute(
            "SELECT * FROM password_reset WHERE user_id=? AND otp=? AND used=0 ORDER BY id DESC LIMIT 1",
            (u['id'], otp)).fetchone())
        if not row: return jsonify({'error': 'Invalid OTP'}), 400
        if time.time() > row['expires_at']: return jsonify({'error': 'OTP expired. Please request a new one.'}), 400
        db.execute("UPDATE user SET password=? WHERE id=?", (generate_password_hash(new_pw), u['id']))
        db.execute("UPDATE password_reset SET used=1 WHERE id=?", (row['id'],))
    return jsonify({'ok': True})

@app.route('/api/logout',methods=['POST'])
def logout(): session.clear(); return jsonify({'ok':True})

@app.route('/api/me')
@login_required
def me():
    with get_db() as db:
        u=r2d(db.execute("SELECT id,name,email,phone,currency FROM user WHERE id=?",(uid(),)).fetchone())
    return jsonify(u)

# ── GROUPS ────────────────────────────────────────────────────
@app.route('/api/groups',methods=['GET'])
@login_required
def get_groups():
    with get_db() as db:
        gids=[r['group_id'] for r in db.execute("SELECT group_id FROM group_member WHERE user_id=?",(uid(),)).fetchall()]
        out=[]
        for gid in gids:
            g=r2d(db.execute("SELECT * FROM grp WHERE id=?",(gid,)).fetchone())
            if g is None:
                # Orphaned membership — group was deleted, clean it up
                db.execute("DELETE FROM group_member WHERE group_id=? AND user_id=?",(gid,uid()))
                continue
            mc=db.execute("SELECT COUNT(*) c FROM group_member WHERE group_id=?",(gid,)).fetchone()['c']
            ec=db.execute("SELECT COUNT(*) c FROM expense WHERE group_id=?",(gid,)).fetchone()['c']
            tot=db.execute("SELECT SUM(amount) s FROM expense WHERE group_id=?",(gid,)).fetchone()['s'] or 0
            b=balances(db,gid).get(uid(),0)
            out.append({**g,'member_count':mc,'expense_count':ec,'total_spent':round(tot,2),'my_balance':round(b,2)})
    return jsonify(out)

@app.route('/api/groups',methods=['POST'])
@login_required
def create_group():
    d=request.json or {}; name=(d.get('name') or '').strip()
    if not name: return jsonify({'error':'Name required'}),400
    with get_db() as db:
        db.execute("INSERT INTO grp(name,description,created_by) VALUES(?,?,?)",(name,d.get('description','').strip(),uid()))
        gid=db.execute("SELECT last_insert_rowid()").fetchone()[0]
        db.execute("INSERT INTO group_member(group_id,user_id) VALUES(?,?)",(gid,uid()))
        for e in d.get('member_emails',[]):
            u2=db.execute("SELECT id FROM user WHERE email=?",(e.strip().lower(),)).fetchone()
            if u2 and u2['id']!=uid(): db.execute("INSERT INTO group_member(group_id,user_id) VALUES(?,?)",(gid,u2['id']))
        db.commit()
    return jsonify({'id':gid,'name':name})

@app.route('/api/groups/<int:gid>',methods=['GET'])
@login_required
def get_group(gid):
    with get_db() as db:
        if not db.execute("SELECT id FROM group_member WHERE group_id=? AND user_id=?",(gid,uid())).fetchone():
            return jsonify({'error':'Not a member'}),403
        g=r2d(db.execute("SELECT * FROM grp WHERE id=?",(gid,)).fetchone())
        members=rs(db.execute("SELECT u.id,u.name,u.email,u.phone FROM user u JOIN group_member gm ON gm.user_id=u.id WHERE gm.group_id=?",(gid,)).fetchall())
        exps=[]
        for e in db.execute("SELECT e.*,u.name payer_name FROM expense e JOIN user u ON u.id=e.payer_id WHERE e.group_id=? ORDER BY e.date DESC",(gid,)).fetchall():
            ed=dict(e); ed['splits']=rs(db.execute("SELECT user_id,share,is_paid FROM split_detail WHERE expense_id=?",(e['id'],)).fetchall()); exps.append(ed)
        b=balances(db,gid); s=settle(b); nm={m['id']:m['name'] for m in members}
        for t in s: t['from_name']=nm.get(t['from'],'?'); t['to_name']=nm.get(t['to'],'?')
    return jsonify({**g,'members':members,'expenses':exps,'balances':{str(k):round(v,2) for k,v in b.items()},'settlements':s})

@app.route('/api/groups/<int:gid>/members',methods=['POST'])
@login_required
def add_member(gid):
    with get_db() as db:
        g=db.execute("SELECT created_by FROM grp WHERE id=?",(gid,)).fetchone()
        if not g or g['created_by']!=uid(): return jsonify({'error':'Only creator can add members'}),403
        ident=(request.json or {}).get('identifier','').strip()
        if not ident: return jsonify({'error':'Email or phone required'}),400
        is_ph=bool(re.search(r'\d{7,}',ident.replace('+','').replace(' ','')))
        if is_ph:
            pn=normalize_phone(ident); u2=r2d(db.execute("SELECT * FROM user WHERE phone=?",(pn,)).fetchone())
            if not u2: return jsonify({'error':f'No account with phone {pn}'}),404
        else:
            u2=r2d(db.execute("SELECT * FROM user WHERE email=?",(ident.lower(),)).fetchone())
            if not u2: return jsonify({'error':f'No account with email {ident}'}),404
        if u2['id']==uid(): return jsonify({'error':'Already in group'}),400
        if db.execute("SELECT id FROM group_member WHERE group_id=? AND user_id=?",(gid,u2['id'])).fetchone():
            return jsonify({'error':f'{u2["name"]} already a member'}),400
        db.execute("INSERT INTO group_member(group_id,user_id) VALUES(?,?)",(gid,u2['id'])); db.commit()
    return jsonify({'id':u2['id'],'name':u2['name'],'email':u2['email'],'phone':u2.get('phone')})

# ── GROUP MANAGEMENT ──────────────────────────────────────────
@app.route('/api/groups/<int:gid>',methods=['PUT'])
@login_required
def edit_group(gid):
    with get_db() as db:
        g=db.execute("SELECT created_by FROM grp WHERE id=?",(gid,)).fetchone()
        if not g: return jsonify({'error':'Not found'}),404
        if g['created_by']!=uid(): return jsonify({'error':'Only creator can edit group'}),403
        d=request.json or {}
        name=(d.get('name') or '').strip()
        if not name: return jsonify({'error':'Name required'}),400
        db.execute("UPDATE grp SET name=?,description=? WHERE id=?",(name,d.get('description','').strip(),gid))
        db.commit()
    return jsonify({'ok':True,'name':name})

@app.route('/api/groups/<int:gid>',methods=['DELETE'])
@login_required
def delete_group(gid):
    with get_db() as db:
        g=db.execute("SELECT created_by FROM grp WHERE id=?",(gid,)).fetchone()
        if not g: return jsonify({'error':'Not found'}),404
        if g['created_by']!=uid(): return jsonify({'error':'Only creator can delete group'}),403
        db.execute("DELETE FROM grp WHERE id=?",(gid,)); db.commit()
    return jsonify({'ok':True})

@app.route('/api/groups/<int:gid>/members/<int:mid>',methods=['DELETE'])
@login_required
def remove_member(gid,mid):
    with get_db() as db:
        g=db.execute("SELECT created_by FROM grp WHERE id=?",(gid,)).fetchone()
        if not g: return jsonify({'error':'Not found'}),404
        if g['created_by']!=uid() and mid!=uid(): return jsonify({'error':'Only creator can remove members'}),403
        if mid==g['created_by']: return jsonify({'error':'Cannot remove group creator'}),400
        db.execute("DELETE FROM group_member WHERE group_id=? AND user_id=?",(gid,mid)); db.commit()
    return jsonify({'ok':True})

@app.route('/api/groups/<int:gid>/leave',methods=['POST'])
@login_required
def leave_group(gid):
    with get_db() as db:
        g=db.execute("SELECT created_by FROM grp WHERE id=?",(gid,)).fetchone()
        if not g: return jsonify({'error':'Not found'}),404
        if g['created_by']==uid(): return jsonify({'error':'Creator cannot leave — delete the group instead'}),400
        db.execute("DELETE FROM group_member WHERE group_id=? AND user_id=?",(gid,uid())); db.commit()
    return jsonify({'ok':True})

# ── USER PROFILE ───────────────────────────────────────────────
@app.route('/api/me',methods=['PUT'])
@login_required
def update_profile():
    d=request.json or {}
    name=(d.get('name') or '').strip()
    phone=normalize_phone(d.get('phone',''))
    currency=d.get('currency','INR')
    if not name: return jsonify({'error':'Name required'}),400
    with get_db() as db:
        if phone:
            clash=db.execute("SELECT id FROM user WHERE phone=? AND id!=?",(phone,uid())).fetchone()
            if clash: return jsonify({'error':'Phone already used by another account'}),400
        db.execute("UPDATE user SET name=?,phone=?,currency=? WHERE id=?",(name,phone,currency,uid()))
        db.commit()
    return jsonify({'ok':True,'name':name})

@app.route('/api/me/password',methods=['PUT'])
@login_required
def change_password():
    d=request.json or {}
    old_pw=d.get('old_password',''); new_pw=d.get('new_password','')
    if len(new_pw)<6: return jsonify({'error':'New password must be at least 6 characters'}),400
    with get_db() as db:
        u=r2d(db.execute("SELECT * FROM user WHERE id=?",(uid(),)).fetchone())
        if not check_password_hash(u['password'],old_pw): return jsonify({'error':'Current password is incorrect'}),401
        db.execute("UPDATE user SET password=? WHERE id=?",(generate_password_hash(new_pw),uid()))
        db.commit()
    return jsonify({'ok':True})

# ── EXPENSES ──────────────────────────────────────────────────
@app.route('/api/groups/<int:gid>/expenses',methods=['POST'])
@login_required
def add_expense(gid):
    d=request.json or {}
    with get_db() as db:
        if not db.execute("SELECT id FROM group_member WHERE group_id=? AND user_id=?",(gid,uid())).fetchone():
            return jsonify({'error':'Not a member'}),403
        amt=float(d.get('amount',0)); title=(d.get('title') or '').strip()
        if not title or amt<=0: return jsonify({'error':'Title and positive amount required'}),400
        if amt>10_000_000: return jsonify({'error':'Amount too large'}),400
        if len(title)>200: return jsonify({'error':'Title too long'}),400
        payer=int(d.get('payer_id',uid())); dval=d.get('date') or now()
        split_type=d.get('split_type','equal')
        db.execute("INSERT INTO expense(group_id,payer_id,title,amount,currency,category,notes,split_type,date) VALUES(?,?,?,?,?,?,?,?,?)",
            (gid,payer,title,amt,d.get('currency','INR'),d.get('category','General'),d.get('notes',''),split_type,dval))
        eid=db.execute("SELECT last_insert_rowid()").fetchone()[0]
        mids=[r['user_id'] for r in db.execute("SELECT user_id FROM group_member WHERE group_id=?",(gid,)).fetchall()]

        if split_type=='equal':
            sh=round(amt/len(mids),2)
            for m in mids: db.execute("INSERT INTO split_detail(expense_id,user_id,share) VALUES(?,?,?)",(eid,m,sh))

        elif split_type=='custom':
            cs=d.get('custom_splits',{})
            for m in mids: db.execute("INSERT INTO split_detail(expense_id,user_id,share) VALUES(?,?,?)",(eid,m,float(cs.get(str(m),0))))

        elif split_type=='percentage':
            ps=d.get('percentage_splits',{})
            for m in mids:
                pct=float(ps.get(str(m),0))
                db.execute("INSERT INTO split_detail(expense_id,user_id,share) VALUES(?,?,?)",(eid,m,round(amt*pct/100,2)))

        elif split_type=='shares':
            ss=d.get('share_splits',{})
            total_shares=sum(float(ss.get(str(m),1)) for m in mids)
            if total_shares<=0: total_shares=len(mids)
            for m in mids:
                sh=float(ss.get(str(m),1))
                db.execute("INSERT INTO split_detail(expense_id,user_id,share) VALUES(?,?,?)",(eid,m,round(amt*sh/total_shares,2)))

        elif split_type=='items':
            # item_splits: {user_id: amount_for_their_items}
            cs=d.get('item_splits',{})
            for m in mids: db.execute("INSERT INTO split_detail(expense_id,user_id,share) VALUES(?,?,?)",(eid,m,float(cs.get(str(m),0))))

        else:
            sh=round(amt/len(mids),2)
            for m in mids: db.execute("INSERT INTO split_detail(expense_id,user_id,share) VALUES(?,?,?)",(eid,m,sh))

        db.commit()
    return jsonify({'id':eid,'title':title,'amount':amt})

@app.route('/api/expenses/<int:eid>',methods=['DELETE'])
@login_required
def delete_expense(eid):
    with get_db() as db:
        e=db.execute("SELECT payer_id FROM expense WHERE id=?",(eid,)).fetchone()
        if not e: return jsonify({'error':'Not found'}),404
        if e['payer_id']!=uid(): return jsonify({'error':'Only payer can delete'}),403
        db.execute("DELETE FROM expense WHERE id=?",(eid,)); db.commit()
    return jsonify({'ok':True})

@app.route('/api/expenses/<int:eid>',methods=['PUT'])
@login_required
def edit_expense(eid):
    d=request.json or {}
    with get_db() as db:
        e=r2d(db.execute("SELECT * FROM expense WHERE id=?",(eid,)).fetchone())
        if not e: return jsonify({'error':'Not found'}),404
        if e['payer_id']!=uid(): return jsonify({'error':'Only payer can edit'}),403
        title=(d.get('title') or '').strip()
        amt=float(d.get('amount',e['amount']))
        if not title or amt<=0: return jsonify({'error':'Title and positive amount required'}),400
        db.execute("UPDATE expense SET title=?,amount=?,currency=?,category=?,notes=?,date=? WHERE id=?",
            (title,amt,d.get('currency',e['currency']),d.get('category',e['category']),
             d.get('notes',e['notes']),d.get('date',e['date']),eid))
        # Recalculate equal splits if amount changed and split_type is equal
        if e['split_type']=='equal' and amt!=e['amount']:
            mids=[r['user_id'] for r in db.execute("SELECT user_id FROM split_detail WHERE expense_id=?",(eid,)).fetchall()]
            if mids:
                sh=round(amt/len(mids),2)
                for m in mids: db.execute("UPDATE split_detail SET share=? WHERE expense_id=? AND user_id=?",(sh,eid,m))
        db.commit()
    return jsonify({'ok':True,'title':title,'amount':amt})

@app.route('/api/expenses/<int:eid>/settle',methods=['POST'])
@login_required
def settle_split(eid):
    target=(request.json or {}).get('user_id',uid())
    with get_db() as db:
        row=db.execute("SELECT id FROM split_detail WHERE expense_id=? AND user_id=?",(eid,target)).fetchone()
        if not row: return jsonify({'error':'Not found'}),404
        db.execute("UPDATE split_detail SET is_paid=1,paid_at=? WHERE id=?",(now(),row['id'])); db.commit()
    return jsonify({'ok':True})

# ── PAYMENT METHODS ───────────────────────────────────────────
@app.route('/api/payment-methods',methods=['GET'])
@login_required
def get_payment_methods():
    with get_db() as db:
        return jsonify(rs(db.execute("SELECT * FROM payment_method WHERE user_id=? ORDER BY is_default DESC,id DESC",(uid(),)).fetchall()))

@app.route('/api/payment-methods',methods=['POST'])
@login_required
def add_payment_method():
    d=request.json or {}; ptype=d.get('type','').strip(); label=d.get('label','').strip(); details=d.get('details','').strip()
    if ptype not in ('upi','bank','card','cash','paypal','other'): return jsonify({'error':'Invalid type'}),400
    if not label or not details: return jsonify({'error':'Label and details required'}),400
    with get_db() as db:
        if d.get('is_default'): db.execute("UPDATE payment_method SET is_default=0 WHERE user_id=?",(uid(),))
        db.execute("INSERT INTO payment_method(user_id,type,label,details,is_default) VALUES(?,?,?,?,?)",(uid(),ptype,label,details,1 if d.get('is_default') else 0))
        mid=db.execute("SELECT last_insert_rowid()").fetchone()[0]; db.commit()
    return jsonify({'id':mid,'type':ptype,'label':label,'details':details})

@app.route('/api/payment-methods/<int:mid>',methods=['DELETE'])
@login_required
def delete_payment_method(mid):
    with get_db() as db:
        row=db.execute("SELECT user_id FROM payment_method WHERE id=?",(mid,)).fetchone()
        if not row: return jsonify({'error':'Not found'}),404
        if row['user_id']!=uid(): return jsonify({'error':'Forbidden'}),403
        db.execute("DELETE FROM payment_method WHERE id=?",(mid,)); db.commit()
    return jsonify({'ok':True})

@app.route('/api/payment-methods/<int:mid>/default',methods=['POST'])
@login_required
def set_default_method(mid):
    with get_db() as db:
        row=db.execute("SELECT user_id FROM payment_method WHERE id=?",(mid,)).fetchone()
        if not row or row['user_id']!=uid(): return jsonify({'error':'Forbidden'}),403
        db.execute("UPDATE payment_method SET is_default=0 WHERE user_id=?",(uid(),))
        db.execute("UPDATE payment_method SET is_default=1 WHERE id=?",(mid,)); db.commit()
    return jsonify({'ok':True})

# ── PAYMENTS ──────────────────────────────────────────────────
@app.route('/api/groups/<int:gid>/payments',methods=['GET'])
@login_required
def get_payments(gid):
    with get_db() as db:
        if not db.execute("SELECT id FROM group_member WHERE group_id=? AND user_id=?",(gid,uid())).fetchone():
            return jsonify({'error':'Not a member'}),403
        return jsonify(rs(db.execute("SELECT p.*,fu.name from_name,fu.email from_email,tu.name to_name,tu.email to_email FROM payment p JOIN user fu ON fu.id=p.from_user_id JOIN user tu ON tu.id=p.to_user_id WHERE p.group_id=? ORDER BY p.created_at DESC",(gid,)).fetchall()))

@app.route('/api/groups/<int:gid>/payments',methods=['POST'])
@login_required
def record_payment(gid):
    d=request.json or {}
    with get_db() as db:
        if not db.execute("SELECT id FROM group_member WHERE group_id=? AND user_id=?",(gid,uid())).fetchone():
            return jsonify({'error':'Not a member'}),403
        to_uid=int(d.get('to_user_id',0)); amt=float(d.get('amount',0))
        if not to_uid or amt<=0: return jsonify({'error':'Recipient and amount required'}),400
        mid=d.get('method_id'); mt='cash'; ml='Cash'
        if mid:
            mr=db.execute("SELECT * FROM payment_method WHERE id=? AND user_id=?",(mid,uid())).fetchone()
            if mr: mt=mr['type']; ml=mr['label']
        db.execute("INSERT INTO payment(group_id,from_user_id,to_user_id,amount,currency,method_id,method_type,method_label,note,reference,status) VALUES(?,?,?,?,?,?,?,?,?,?,'pending')",
            (gid,uid(),to_uid,amt,d.get('currency','INR'),mid,mt,ml,d.get('note','').strip(),d.get('reference','').strip()))
        pid=db.execute("SELECT last_insert_rowid()").fetchone()[0]; db.commit()
    return jsonify({'id':pid,'status':'pending','amount':amt})

@app.route('/api/payments/<int:pid>/confirm',methods=['POST'])
@login_required
def confirm_payment(pid):
    with get_db() as db:
        p=r2d(db.execute("SELECT * FROM payment WHERE id=?",(pid,)).fetchone())
        if not p: return jsonify({'error':'Not found'}),404
        if p['to_user_id']!=uid(): return jsonify({'error':'Only receiver can confirm'}),403
        if p['status']!='pending': return jsonify({'error':f'Already {p["status"]}'}),400
        db.execute("UPDATE payment SET status='confirmed',confirmed_at=? WHERE id=?",(now(),pid))
        left=auto_settle(db,p['group_id'],p['from_user_id'],p['amount']); db.commit()
    return jsonify({'ok':True,'remaining_unallocated':left})

@app.route('/api/payments/<int:pid>/reject',methods=['POST'])
@login_required
def reject_payment(pid):
    with get_db() as db:
        p=db.execute("SELECT to_user_id,status FROM payment WHERE id=?",(pid,)).fetchone()
        if not p: return jsonify({'error':'Not found'}),404
        if p['to_user_id']!=uid(): return jsonify({'error':'Only receiver can reject'}),403
        if p['status']!='pending': return jsonify({'error':f'Already {p["status"]}'}),400
        db.execute("UPDATE payment SET status='rejected' WHERE id=?",(pid,)); db.commit()
    return jsonify({'ok':True})

@app.route('/api/payments/pending',methods=['GET'])
@login_required
def pending_payments():
    with get_db() as db:
        return jsonify(rs(db.execute("SELECT p.*,fu.name from_name,g.name group_name FROM payment p JOIN user fu ON fu.id=p.from_user_id JOIN grp g ON g.id=p.group_id WHERE p.to_user_id=? AND p.status='pending' ORDER BY p.created_at DESC",(uid(),)).fetchall()))

@app.route('/api/payments/<int:pid>/simulate',methods=['POST'])
@login_required
def simulate_transaction(pid):
    with get_db() as db:
        p=r2d(db.execute("SELECT * FROM payment WHERE id=?",(pid,)).fetchone())
        if not p: return jsonify({'error':'Not found'}),404
        if p['from_user_id']!=uid(): return jsonify({'error':'Only payer can simulate'}),403
        if p['status']!='pending': return jsonify({'error':f'Already {p["status"]}'}),400
    time.sleep(random.uniform(0.3,0.8))
    if random.random()<0.08:
        return jsonify({'success':False,'status':'failed','error_code':random.choice(['INSUFFICIENT_FUNDS','NETWORK_ERROR','TIMEOUT','BANK_DECLINED']),'message':'Transaction declined. Please retry.'}),402
    txn='TXN'+hashlib.md5(f"{uid()}{pid}{time.time()}".encode()).hexdigest()[:16].upper()
    utr=''.join([str(random.randint(0,9)) for _ in range(12)])
    with get_db() as db:
        db.execute("UPDATE payment SET status='confirmed',confirmed_at=?,reference=? WHERE id=?",(now(),txn,pid))
        left=auto_settle(db,p['group_id'],p['from_user_id'],p['amount']); db.commit()
    return jsonify({'success':True,'status':'confirmed','txn_id':txn,'utr':utr,'amount':p['amount'],'currency':p['currency'],'timestamp':now(),'message':'Payment successful ✓'})

@app.route('/api/upi-qr',methods=['POST'])
@login_required
def upi_qr_data():
    d=request.json or {}; upi_id=d.get('upi_id','').strip(); name=d.get('name','').strip()
    amount=float(d.get('amount',0)); note=d.get('note','SplitWise settlement').strip(); cur=d.get('currency','INR')
    if not upi_id: return jsonify({'error':'UPI ID required'}),400
    n=name.replace(' ','%20'); nt=note.replace(' ','%20')
    return jsonify({'upi_string':f"upi://pay?pa={upi_id}&pn={n}&am={amount:.2f}&cu={cur}&tn={nt}",
        'gpay_link':f"tez://upi/pay?pa={upi_id}&pn={name}&am={amount:.2f}&cu={cur}&tn={note}",
        'phonepe_link':f"phonepe://pay?pa={upi_id}&pn={name}&am={amount:.2f}&cu={cur}&tn={note}",
        'paytm_link':f"paytmmp://upi/pay?pa={upi_id}&pn={name}&am={amount:.2f}&cu={cur}&tn={note}",
        'upi_id':upi_id,'name':name,'amount':amount,'currency':cur})

@app.route('/api/users/<int:target_uid>/upi',methods=['GET'])
@login_required
def get_user_upi(target_uid):
    with get_db() as db:
        u2=r2d(db.execute("SELECT id,name FROM user WHERE id=?",(target_uid,)).fetchone())
        if not u2: return jsonify({'error':'Not found'}),404
        return jsonify({'user':u2,'upi_methods':rs(db.execute("SELECT * FROM payment_method WHERE user_id=? AND type='upi' ORDER BY is_default DESC",(target_uid,)).fetchall())})

@app.route('/api/ocr',methods=['POST'])
@login_required
def ocr_scan():
    if 'file' not in request.files: return jsonify({'error':'No file'}),400
    f=request.files['file']
    if not f.filename or not ok_file(f.filename): return jsonify({'error':'Invalid file type'}),400
    fn=str(uuid.uuid4())+'_'+secure_filename(f.filename)
    path=os.path.join(app.config['UPLOAD_FOLDER'],fn); f.save(path)
    res=ocr_extract(path); res['filename']=fn
    return jsonify(res)

@app.route('/api/stats')
@login_required
def stats():
    with get_db() as db:
        gids=[r['group_id'] for r in db.execute("SELECT group_id FROM group_member WHERE user_id=?",(uid(),)).fetchall()]
        tp=sum((db.execute("SELECT SUM(amount) s FROM expense WHERE group_id=? AND payer_id=?",(g,uid())).fetchone()['s'] or 0) for g in gids)
        ms=sum((db.execute("SELECT SUM(sd.share) s FROM split_detail sd JOIN expense e ON e.id=sd.expense_id WHERE sd.user_id=? AND e.group_id=?",(uid(),g)).fetchone()['s'] or 0) for g in gids)
        cats={row['category']:round(row['s'],2) for row in db.execute("SELECT category,SUM(amount) s FROM expense WHERE payer_id=? GROUP BY category",(uid(),)).fetchall()}
        om=ie=0
        for g in gids:
            b=balances(db,g).get(uid(),0)
            if b>0: om+=b
            else: ie+=abs(b)
    return jsonify({'total_paid':round(tp,2),'my_share':round(ms,2),'owed_to_me':round(om,2),'i_owe':round(ie,2),'categories':cats,'group_count':len(gids)})



# ══════════════════════════════════════════════════════════════
# ── ADMIN PANEL ───────────────────────────────────────────────
# ══════════════════════════════════════════════════════════════
ADMIN_PASSWORD = os.environ.get('ADMIN_PASSWORD', 'admin123')  # Change in production

def admin_required(f):
    @wraps(f)
    def dec(*a, **kw):
        if not session.get('is_admin'):
            return jsonify({'error': 'Admin access required'}), 403
        return f(*a, **kw)
    return dec

@app.route('/admin')
def admin_page():
    if not session.get('is_admin'):
        return redirect('/admin/login')
    return render_template('admin.html')

@app.route('/admin/login')
def admin_login_page():
    if session.get('is_admin'):
        return redirect('/admin')
    return render_template('admin_login.html')

@app.route('/api/admin/login', methods=['POST'])
def admin_login():
    d = request.json or {}
    if d.get('password') == ADMIN_PASSWORD:
        session['is_admin'] = True
        session.permanent = True
        return jsonify({'ok': True})
    return jsonify({'error': 'Invalid admin password'}), 401

@app.route('/api/admin/logout', methods=['POST'])
def admin_logout():
    session.pop('is_admin', None)
    return jsonify({'ok': True})

@app.route('/api/admin/stats')
@admin_required
def admin_stats():
    with get_db() as db:
        users      = db.execute("SELECT COUNT(*) c FROM user").fetchone()['c']
        groups     = db.execute("SELECT COUNT(*) c FROM grp").fetchone()['c']
        expenses   = db.execute("SELECT COUNT(*) c FROM expense").fetchone()['c']
        payments   = db.execute("SELECT COUNT(*) c FROM payment").fetchone()['c']
        total_vol  = db.execute("SELECT COALESCE(SUM(amount),0) s FROM expense").fetchone()['s']
        new_today  = db.execute("SELECT COUNT(*) c FROM user WHERE DATE(created_at)=DATE('now')").fetchone()['c']
        new_week   = db.execute("SELECT COUNT(*) c FROM user WHERE created_at >= datetime('now','-7 days')").fetchone()['c']
        active_grps= db.execute("SELECT COUNT(DISTINCT group_id) c FROM expense WHERE created_at >= datetime('now','-7 days')").fetchone()['c']
    return jsonify({
        'users': users, 'groups': groups, 'expenses': expenses,
        'payments': payments, 'total_volume': round(total_vol, 2),
        'new_today': new_today, 'new_week': new_week, 'active_groups': active_grps
    })

@app.route('/api/admin/users')
@admin_required
def admin_list_users():
    page = int(request.args.get('page', 1))
    per  = int(request.args.get('per', 20))
    q    = (request.args.get('q') or '').strip()
    off  = (page - 1) * per
    with get_db() as db:
        if q:
            rows = rs(db.execute(
                "SELECT u.id,u.name,u.email,u.phone,u.currency,u.created_at,u.is_banned,"
                "(SELECT COUNT(*) FROM group_member WHERE user_id=u.id) grp_count,"
                "(SELECT COUNT(*) FROM expense WHERE payer_id=u.id) exp_count,"
                "(SELECT COALESCE(SUM(amount),0) FROM expense WHERE payer_id=u.id) total_spent "
                "FROM user u WHERE u.name LIKE ? OR u.email LIKE ? OR u.phone LIKE ? "
                "ORDER BY u.created_at DESC LIMIT ? OFFSET ?",
                (f'%{q}%', f'%{q}%', f'%{q}%', per, off)).fetchall())
            total = db.execute("SELECT COUNT(*) c FROM user WHERE name LIKE ? OR email LIKE ? OR phone LIKE ?",
                               (f'%{q}%',f'%{q}%',f'%{q}%')).fetchone()['c']
        else:
            rows = rs(db.execute(
                "SELECT u.id,u.name,u.email,u.phone,u.currency,u.created_at,u.is_banned,"
                "(SELECT COUNT(*) FROM group_member WHERE user_id=u.id) grp_count,"
                "(SELECT COUNT(*) FROM expense WHERE payer_id=u.id) exp_count,"
                "(SELECT COALESCE(SUM(amount),0) FROM expense WHERE payer_id=u.id) total_spent "
                "FROM user u ORDER BY u.created_at DESC LIMIT ? OFFSET ?",
                (per, off)).fetchall())
            total = db.execute("SELECT COUNT(*) c FROM user").fetchone()['c']
    return jsonify({'users': rows, 'total': total, 'page': page, 'per': per})

@app.route('/api/admin/users/<int:target_id>')
@admin_required
def admin_get_user(target_id):
    with get_db() as db:
        u = r2d(db.execute("SELECT id,name,email,phone,currency,created_at,is_banned FROM user WHERE id=?", (target_id,)).fetchone())
        if not u: return jsonify({'error': 'Not found'}), 404
        groups = rs(db.execute(
            "SELECT g.id,g.name,g.created_at,gm.joined_at FROM grp g JOIN group_member gm ON gm.group_id=g.id WHERE gm.user_id=?", (target_id,)).fetchall())
        expenses = rs(db.execute(
            "SELECT e.id,e.title,e.amount,e.currency,e.category,e.date,g.name grp_name FROM expense e JOIN grp g ON g.id=e.group_id WHERE e.payer_id=? ORDER BY e.date DESC LIMIT 20", (target_id,)).fetchall())
        resets = rs(db.execute(
            "SELECT id,otp,expires_at,used FROM password_reset WHERE user_id=? ORDER BY id DESC LIMIT 5", (target_id,)).fetchall())
    return jsonify({'user': u, 'groups': groups, 'expenses': expenses, 'resets': resets})

@app.route('/api/admin/users/<int:target_id>', methods=['PUT'])
@admin_required
def admin_edit_user(target_id):
    d = request.json or {}
    with get_db() as db:
        u = r2d(db.execute("SELECT id FROM user WHERE id=?", (target_id,)).fetchone())
        if not u: return jsonify({'error': 'Not found'}), 404
        if 'name' in d:
            db.execute("UPDATE user SET name=? WHERE id=?", (d['name'].strip(), target_id))
        if 'email' in d:
            db.execute("UPDATE user SET email=? WHERE id=?", (d['email'].strip().lower(), target_id))
        if 'phone' in d:
            db.execute("UPDATE user SET phone=? WHERE id=?", (normalize_phone(d['phone']) or None, target_id))
        if 'currency' in d:
            db.execute("UPDATE user SET currency=? WHERE id=?", (d['currency'], target_id))
        if 'password' in d and d['password']:
            if len(d['password']) < 6: return jsonify({'error': 'Password min 6 chars'}), 400
            db.execute("UPDATE user SET password=? WHERE id=?", (generate_password_hash(d['password']), target_id))
    return jsonify({'ok': True})

@app.route('/api/admin/users/<int:target_id>/ban', methods=['POST'])
@admin_required
def admin_ban_user(target_id):
    with get_db() as db:
        db.execute("UPDATE user SET is_banned=1 WHERE id=?", (target_id,))
        session_keys_to_clear = []  # In production you'd invalidate their session
    return jsonify({'ok': True})

@app.route('/api/admin/users/<int:target_id>/unban', methods=['POST'])
@admin_required
def admin_unban_user(target_id):
    with get_db() as db:
        db.execute("UPDATE user SET is_banned=0 WHERE id=?", (target_id,))
    return jsonify({'ok': True})

@app.route('/api/admin/users/<int:target_id>', methods=['DELETE'])
@admin_required
def admin_delete_user(target_id):
    with get_db() as db:
        db.execute("DELETE FROM user WHERE id=?", (target_id,))
    return jsonify({'ok': True})

@app.route('/api/admin/users', methods=['POST'])
@admin_required
def admin_create_user():
    d = request.json or {}
    name = (d.get('name') or '').strip()
    email = (d.get('email') or '').strip().lower()
    pw = d.get('password', '')
    phone = normalize_phone(d.get('phone', ''))
    currency = d.get('currency', 'INR')
    if not name or not email or not pw:
        return jsonify({'error': 'Name, email and password required'}), 400
    if len(pw) < 6:
        return jsonify({'error': 'Password min 6 chars'}), 400
    with get_db() as db:
        try:
            db.execute("INSERT INTO user(name,email,phone,password,currency) VALUES(?,?,?,?,?)",
                       (name, email, phone, generate_password_hash(pw), currency))
            new_id = db.execute("SELECT last_insert_rowid() id").fetchone()['id']
        except Exception as e:
            if 'UNIQUE' in str(e):
                return jsonify({'error': 'Email or phone already registered'}), 409
            raise
    return jsonify({'ok': True, 'id': new_id})

@app.route('/api/admin/groups')
@admin_required
def admin_list_groups():
    page = int(request.args.get('page', 1))
    per  = int(request.args.get('per', 20))
    q    = (request.args.get('q') or '').strip()
    off  = (page - 1) * per
    with get_db() as db:
        if q:
            rows = rs(db.execute(
                "SELECT g.id,g.name,g.description,g.created_at,u.name creator,"
                "(SELECT COUNT(*) FROM group_member WHERE group_id=g.id) member_count,"
                "(SELECT COUNT(*) FROM expense WHERE group_id=g.id) exp_count,"
                "(SELECT COALESCE(SUM(amount),0) FROM expense WHERE group_id=g.id) total "
                "FROM grp g JOIN user u ON u.id=g.created_by WHERE g.name LIKE ? OR u.name LIKE ? "
                "ORDER BY g.created_at DESC LIMIT ? OFFSET ?",
                (f'%{q}%', f'%{q}%', per, off)).fetchall())
            total = db.execute("SELECT COUNT(*) c FROM grp g JOIN user u ON u.id=g.created_by WHERE g.name LIKE ? OR u.name LIKE ?", (f'%{q}%',f'%{q}%')).fetchone()['c']
        else:
            rows = rs(db.execute(
                "SELECT g.id,g.name,g.description,g.created_at,u.name creator,"
                "(SELECT COUNT(*) FROM group_member WHERE group_id=g.id) member_count,"
                "(SELECT COUNT(*) FROM expense WHERE group_id=g.id) exp_count,"
                "(SELECT COALESCE(SUM(amount),0) FROM expense WHERE group_id=g.id) total "
                "FROM grp g JOIN user u ON u.id=g.created_by ORDER BY g.created_at DESC LIMIT ? OFFSET ?",
                (per, off)).fetchall())
            total = db.execute("SELECT COUNT(*) c FROM grp").fetchone()['c']
    return jsonify({'groups': rows, 'total': total, 'page': page, 'per': per})

@app.route('/api/admin/groups/<int:gid>', methods=['DELETE'])
@admin_required
def admin_delete_group(gid):
    with get_db() as db:
        db.execute("DELETE FROM grp WHERE id=?", (gid,))
    return jsonify({'ok': True})

@app.route('/api/admin/activity')
@admin_required
def admin_activity():
    with get_db() as db:
        recent_users = rs(db.execute(
            "SELECT id,name,email,created_at FROM user ORDER BY created_at DESC LIMIT 8").fetchall())
        recent_expenses = rs(db.execute(
            "SELECT e.id,e.title,e.amount,e.currency,e.category,e.created_at,u.name payer,g.name grp "
            "FROM expense e JOIN user u ON u.id=e.payer_id JOIN grp g ON g.id=e.group_id "
            "ORDER BY e.created_at DESC LIMIT 10").fetchall())
        signups_7d = rs(db.execute(
            "SELECT DATE(created_at) day, COUNT(*) cnt FROM user "
            "WHERE created_at >= datetime('now','-6 days') GROUP BY DATE(created_at) ORDER BY day").fetchall())
    return jsonify({'recent_users': recent_users, 'recent_expenses': recent_expenses, 'signups_7d': signups_7d})

# ── INIT & RUN ────────────────────────────────────────────────
init_db()

if __name__=='__main__':
    port=int(os.environ.get('PORT',5000))
    print(f"\n✅  SplitWise → http://0.0.0.0:{port}\n")
    app.run(host='0.0.0.0',port=port,debug=app.config['DEBUG'])