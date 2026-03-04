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
    try:
        import pytesseract
        from PIL import Image
        text=pytesseract.image_to_string(Image.open(path))
        m=re.findall(r'(?:total|amount|rs\.?|₹|inr)[\s:]*([0-9,]+(?:\.[0-9]{1,2})?)',text,re.I)
        if not m: m=re.findall(r'\b([0-9]{2,6}(?:\.[0-9]{1,2})?)\b',text)
        amt=None
        if m:
            try: amt=float(m[-1].replace(',',''))
            except: pass
        return {'text':text,'amount':amt}
    except Exception as e:
        return {'text':'','amount':None,'error':str(e)}

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

@app.route('/health')
def health(): return jsonify({'status':'ok','time':now()})

# ── AUTH ──────────────────────────────────────────────────────
@app.route('/api/register',methods=['POST'])
def register():
    d=request.json or {}
    name=(d.get('name') or '').strip(); email=(d.get('email') or '').strip().lower()
    pw=d.get('password',''); phone=normalize_phone(d.get('phone',''))
    if not name or not email or not pw: return jsonify({'error':'Name, email and password required'}),400
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

@app.route('/api/login',methods=['POST'])
def login():
    d=request.json or {}
    with get_db() as db:
        u=r2d(db.execute("SELECT * FROM user WHERE email=?",(d.get('email','').lower(),)).fetchone())
    if not u or not check_password_hash(u['password'],d.get('password','')): return jsonify({'error':'Invalid credentials'}),401
    session['user_id']=u['id']
    return jsonify({'id':u['id'],'name':u['name'],'email':u['email'],'currency':u['currency']})

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
        payer=int(d.get('payer_id',uid())); dval=d.get('date') or now()
        db.execute("INSERT INTO expense(group_id,payer_id,title,amount,currency,category,notes,split_type,date) VALUES(?,?,?,?,?,?,?,?,?)",
            (gid,payer,title,amt,d.get('currency','INR'),d.get('category','General'),d.get('notes',''),d.get('split_type','equal'),dval))
        eid=db.execute("SELECT last_insert_rowid()").fetchone()[0]
        mids=[r['user_id'] for r in db.execute("SELECT user_id FROM group_member WHERE group_id=?",(gid,)).fetchall()]
        if d.get('split_type')=='custom':
            cs=d.get('custom_splits',{})
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

@app.route('/api/seed',methods=['POST'])
def seed():
    with get_db() as db:
        if db.execute("SELECT COUNT(*) c FROM user").fetchone()['c']>0: return jsonify({'msg':'Already seeded'})
        pw=generate_password_hash('demo123'); uids=[]
        for name,email,phone in [('Amaya K','amaya@demo.com','+919876543210'),('Fathima Fidha','fathima@demo.com','+919876543211'),('Nandana Kishor','nandanak@demo.com','+919876543212'),('Nandana KM','nandanakm@demo.com','+919876543213')]:
            db.execute("INSERT INTO user(name,email,phone,password,currency) VALUES(?,?,?,?,'INR')",(name,email,phone,pw))
            uids.append(db.execute("SELECT last_insert_rowid()").fetchone()[0])
        db.execute("INSERT INTO grp(name,description,created_by) VALUES('College Trip 🎒','Munnar trip expenses',?)",(uids[0],))
        gid=db.execute("SELECT last_insert_rowid()").fetchone()[0]
        for u2 in uids: db.execute("INSERT INTO group_member(group_id,user_id) VALUES(?,?)",(gid,u2))
        for title,amt,cat,payer in [('Hotel Stay',2400,'Travel',uids[0]),('Dinner Day 1',960,'Food',uids[1]),('Bus Tickets',480,'Travel',uids[2]),('Lunch Day 2',640,'Food',uids[3]),('Sightseeing',360,'Activities',uids[0])]:
            db.execute("INSERT INTO expense(group_id,payer_id,title,amount,category,currency) VALUES(?,?,?,?,?,'INR')",(gid,payer,title,amt,cat))
            eid=db.execute("SELECT last_insert_rowid()").fetchone()[0]
            for u2 in uids: db.execute("INSERT INTO split_detail(expense_id,user_id,share) VALUES(?,?,?)",(eid,u2,round(amt/4,2)))
        for u2,pt,lbl,det,dflt in [(uids[0],'upi','GPay','amaya@okicici',1),(uids[0],'bank','SBI Savings','ACC:1234567890',0),(uids[1],'upi','PhonePe','fathima@ybl',1),(uids[2],'upi','GPay','nandana@okaxis',1),(uids[3],'cash','Cash','In person',1)]:
            db.execute("INSERT INTO payment_method(user_id,type,label,details,is_default) VALUES(?,?,?,?,?)",(u2,pt,lbl,det,dflt))
        db.commit()
    return jsonify({'msg':'Seeded','demo_email':'amaya@demo.com','demo_password':'demo123'})

# ── INIT & RUN ────────────────────────────────────────────────
init_db()

if __name__=='__main__':
    port=int(os.environ.get('PORT',5000))
    print(f"\n✅  SplitWise → http://0.0.0.0:{port}\n")
    app.run(host='0.0.0.0',port=port,debug=app.config['DEBUG'])
