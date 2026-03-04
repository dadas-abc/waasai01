from fastapi import FastAPI, HTTPException, Header, Request, Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from typing import List, Optional, Dict
import os, base64, uuid, re
from datetime import datetime, timezone, timedelta
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.gzip import GZipMiddleware
from starlette.middleware.base import BaseHTTPMiddleware
from sqlalchemy import create_engine, Column, Integer, String, Float, Text, DateTime
from sqlalchemy.orm import declarative_base, sessionmaker

# ------------------- minimal settings -------------------
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "admin-demo-token")
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./simple_app.db")
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "false").lower() == "true"
AES_KEY_ENV = os.getenv("AES_KEY")
AES_KEY = base64.urlsafe_b64decode(AES_KEY_ENV.encode()) if AES_KEY_ENV else os.urandom(32)

# ------------------- db setup -------------------
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# ------------------- models -------------------
class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    id_uuid = Column(String(64), unique=True, index=True)
    password_hash = Column(String(255))
    sec_q1 = Column(String(255))
    sec_a1_enc = Column(String(255))
    sec_q2 = Column(String(255))
    sec_a2_enc = Column(String(255))
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

class Project(Base):
    __tablename__ = "projects"
    id = Column(Integer, primary_key=True)
    name = Column(String(128))
    intro = Column(Text)
    base_price = Column(Float)
    sla_hours = Column(Integer)
    required_fields = Column(Text)  # json list
    optional_fields = Column(Text)  # json list
    field_hints = Column(Text)      # json dict
    status = Column(String(32), default="online")

class Order(Base):
    __tablename__ = "orders"
    id = Column(Integer, primary_key=True)
    order_no = Column(String(64), unique=True)
    user_id = Column(String(64), index=True)
    project_id = Column(Integer)
    amount = Column(Float)
    status = Column(String(32), default="待支付")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    pay_deadline = Column(DateTime, nullable=True)
    pay_proof_url_opt = Column(Text, nullable=True)
    internal_note_opt = Column(Text, nullable=True)
    codepool_id_opt = Column(Integer, nullable=True)
    pay_channel_opt = Column(String(32), nullable=True)  # wechat/alipay

class Codepool(Base):
    __tablename__ = "codepool"
    id = Column(Integer, primary_key=True)
    project_id = Column(Integer, nullable=True)  # 为空表示通用码
    image_url = Column(Text)  # /web/uploads/xxx.png
    enabled = Column(Integer, default=1)
    channel = Column(String(32), default="unknown")  # wechat/alipay/unknown
    display_name = Column(String(128), nullable=True)

class ChatMessage(Base):
    __tablename__ = "chat_messages"
    id = Column(Integer, primary_key=True)
    user_id = Column(String(64), index=True)
    sender = Column(String(16))  # user/agent
    kind = Column(String(16))    # text/image/file/video
    content = Column(Text)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

class OrderEvent(Base):
    __tablename__ = "order_events"
    id = Column(Integer, primary_key=True)
    order_id = Column(Integer, index=True)
    name = Column(String(64))
    detail = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

class PayCodeStats(Base):
    __tablename__ = "pay_code_stats"
    id = Column(Integer, primary_key=True)
    codepool_id = Column(Integer, index=True)
    stat_date = Column(String(16), index=True)
    order_count = Column(Integer, default=0)
    total_amount = Column(Float, default=0.0)

class BannedUser(Base):
    __tablename__ = "banned_users"
    id = Column(Integer, primary_key=True)
    user_id = Column(String(64), unique=True, index=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

class AutoReplyRule(Base):
    __tablename__ = "auto_reply_rules"
    id = Column(Integer, primary_key=True)
    keyword = Column(String(255), unique=True, index=True)
    reply_text = Column(Text)
    enabled = Column(Integer, default=1)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

# ------------------- helpers -------------------
def create_tables():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        if db.query(Project).count() == 0:
            import json
            db.add(Project(
                name="示例项目A",
                intro="填写姓名与手机号，交付时效24小时",
                base_price=99.0,
                sla_hours=24,
                required_fields=json.dumps(["name","phone"]),
                optional_fields=json.dumps(["image_url"]),
                field_hints=json.dumps({"name":"真实姓名","phone":"11位手机号","image_url":"上传图片"}),
                status="online"
            ))
            db.add(Project(
                name="示例项目B",
                intro="填写微信号与身份证号，交付时效48小时",
                base_price=199.0,
                sla_hours=48,
                required_fields=json.dumps(["wechat_id","id_card"]),
                optional_fields=json.dumps(["image_url"]),
                field_hints=json.dumps({"wechat_id":"以字母开头的6-20位","id_card":"15或18位"}),
                status="online"
            ))
            db.commit()
    finally:
        db.close()
    # ensure uploads dir
    up = os.path.join(os.path.dirname(__file__), "web", "uploads")
    os.makedirs(up, exist_ok=True)
    try:
        conn = engine.raw_connection()
        cur = conn.cursor()
        cur.execute("PRAGMA table_info(orders)")
        cols = [r[1] for r in cur.fetchall()]
        if "codepool_id_opt" not in cols:
            cur.execute("ALTER TABLE orders ADD COLUMN codepool_id_opt INTEGER")
        if "pay_channel_opt" not in cols:
            cur.execute("ALTER TABLE orders ADD COLUMN pay_channel_opt VARCHAR(32)")
        cur.execute("PRAGMA table_info(codepool)")
        ccols = [r[1] for r in cur.fetchall()]
        if "channel" not in ccols:
            cur.execute("ALTER TABLE codepool ADD COLUMN channel VARCHAR(32) DEFAULT 'unknown'")
        if "display_name" not in ccols:
            cur.execute("ALTER TABLE codepool ADD COLUMN display_name VARCHAR(128)")
        conn.commit()
        cur.close()
        conn.close()
    except Exception:
        pass

def add_event(db, order_id: int, name: str, detail: str = ""):
    e = OrderEvent(order_id=order_id, name=name, detail=detail or "")
    db.add(e); db.commit()

def today_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")

CST = timezone(timedelta(hours=8))
def today_cst_str() -> str:
    return datetime.now(timezone.utc).astimezone(CST).strftime("%Y-%m-%d")
def to_cst_iso(dt: datetime) -> str:
    if dt is None: return ""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    else:
        dt = dt.astimezone(timezone.utc)
    return dt.astimezone(CST).isoformat()

def pick_codepool_for_order(db, o: Order, channel: Optional[str] = None) -> Optional[Codepool]:
    q = db.query(Codepool).filter(Codepool.enabled == 1, (Codepool.project_id == o.project_id) | (Codepool.project_id == None))
    if channel:
        q = q.filter(Codepool.channel == channel)
    cps = q.all()
    if not cps:
        return None
    stats = {cp.id: {"amount": 0.0, "count": 0} for cp in cps}
    rows = db.query(PayCodeStats).filter(PayCodeStats.stat_date == today_cst_str(), PayCodeStats.codepool_id.in_(list(stats.keys()))).all()
    for r in rows:
        stats[r.codepool_id] = {"amount": r.total_amount or 0.0, "count": r.order_count or 0}
    cps_sorted = sorted(cps, key=lambda cp: (stats.get(cp.id, {}).get("amount", 0.0), cp.id))
    return cps_sorted[0] if cps_sorted else cps[0]

def hash_pw(pw: str) -> str:
    import hashlib, os
    salt = os.urandom(8).hex()
    return salt + ":" + hashlib.sha256((salt + pw).encode()).hexdigest()

def verify_pw(pw: str, hpw: str) -> bool:
    import hashlib
    salt, h = hpw.split(":")
    return hashlib.sha256((salt + pw).encode()).hexdigest() == h

def create_token(sub: str) -> str:
    import jwt
    payload = {"sub": sub, "jti": uuid.uuid4().hex}
    return jwt.encode(payload, AES_KEY, algorithm="HS256")

def decode_token(tk: str) -> Dict:
    import jwt
    return jwt.decode(tk, AES_KEY, algorithms=["HS256"])

def validate_field(fid: str, v: str) -> bool:
    if fid == "phone": return re.fullmatch(r"^1[3-9]\d{9}$", v or "") is not None
    if fid == "id_card": return re.fullmatch(r"^(?:\d{15}|\d{17}[\dXx])$", v or "") is not None
    if fid == "wechat_id": return re.fullmatch(r"^[a-zA-Z][-_a-zA-Z0-9]{5,19}$", v or "") is not None
    if fid == "card_no": return re.fullmatch(r"^\d{12,19}$", v or "") is not None
    if fid == "douyin_id": return re.fullmatch(r"^[a-zA-Z0-9_.-]{3,24}$", v or "") is not None
    if fid == "passport_no": return re.fullmatch(r"^[A-Za-z][A-Za-z0-9]{7,17}$", v or "") is not None
    if fid == "image_url": return (v or "").startswith("/web/uploads/") or re.fullmatch(r"^https?://", v or "") is not None
    if fid == "qq_id": return re.fullmatch(r"^\d{5,11}$", v or "") is not None
    if fid == "xhs_id": return re.fullmatch(r"^[a-zA-Z0-9_]{3,24}$", v or "") is not None
    if fid == "weibo_id": return re.fullmatch(r"^[a-zA-Z0-9_]{2,30}$", v or "") is not None
    if fid == "corp_credit_code": return re.fullmatch(r"^[0-9A-Z]{18}$", (v or "").upper()) is not None
    if fid == "corp_name": return re.fullmatch(r"^[A-Za-z0-9\u4e00-\u9fa5（）()&·\-\.\s]{2,80}$", v or "") is not None
    if fid == "name": return re.fullmatch(r"^[A-Za-z\u4e00-\u9fa5][A-Za-z\u4e00-\u9fa5\s]{1,49}$", v or "") is not None
    return True

# ------------------- app -------------------
app = FastAPI(title="Simple APP")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://127.0.0.1:8000"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(GZipMiddleware, minimum_size=500)
class CacheStaticMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        try:
            path = request.url.path.lower()
            if path.startswith("/web/uploads/") or (path.startswith("/web/") and any(path.endswith(ext) for ext in (".png",".jpg",".jpeg",".gif",".webp",".bmp",".svg",".mp4",".webm",".mov",".mkv",".avi"))):
                response.headers["Cache-Control"] = "public, max-age=86400, immutable"
        except Exception:
            pass
        return response
app.add_middleware(CacheStaticMiddleware)
STATIC_DIR = os.path.join(os.path.dirname(__file__), "web")
app.mount("/web", StaticFiles(directory=STATIC_DIR, html=True), name="web")

@app.on_event("startup")
def on_startup():
    create_tables()

@app.get("/")
def root():
    from fastapi.responses import RedirectResponse
    return RedirectResponse("/web/index.html")

@app.get("/health")
def health():
    return {"status": "ok"}

# ------------------- schemas -------------------
class RegisterPayload(BaseModel):
    password: str
    sec_q1: str
    sec_a1: str
    sec_q2: str
    sec_a2: str

class LoginPayload(BaseModel):
    id: str
    password: str

class OrderCreatePayload(BaseModel):
    project_id: int
    fields: Dict[str, str] = {}

# ------------------- endpoints -------------------
@app.post("/api/auth/register")
def register(payload: RegisterPayload, response: Response):
    db = SessionLocal()
    try:
        user_id = str(uuid.uuid4())
        u = User(
            id_uuid=user_id,
            password_hash=hash_pw(payload.password),
            sec_q1=payload.sec_q1,
            sec_a1_enc=payload.sec_a1,
            sec_q2=payload.sec_q2,
            sec_a2_enc=payload.sec_a2,
        )
        db.add(u); db.commit()
        tk = create_token(user_id)
        response.set_cookie("app_token", tk, httponly=True, samesite="lax", path="/", secure=COOKIE_SECURE)
        response.set_cookie("csrf_token", uuid.uuid4().hex, httponly=False, samesite="lax", path="/", secure=COOKIE_SECURE)
        return {"id": user_id, "token": tk}
    finally:
        db.close()

@app.post("/api/auth/login")
def login(payload: LoginPayload, response: Response):
    db = SessionLocal()
    try:
        u = db.query(User).filter(User.id_uuid == payload.id).first()
        if not u or not verify_pw(payload.password, u.password_hash):
            raise HTTPException(status_code=401, detail="invalid credentials")
        tk = create_token(u.id_uuid)
        response.set_cookie("app_token", tk, httponly=True, samesite="lax", path="/", secure=COOKIE_SECURE)
        response.set_cookie("csrf_token", uuid.uuid4().hex, httponly=False, samesite="lax", path="/", secure=COOKIE_SECURE)
        return {"token": tk, "id": u.id_uuid}
    finally:
        db.close()

def current_user(authorization: str = Header(default="")) -> str:
    token = ""
    if authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1]
    if not token:
        raise HTTPException(status_code=401, detail="missing token")
    try:
        data = decode_token(token)
        return data.get("sub") or ""
    except Exception:
        raise HTTPException(status_code=401, detail="invalid token")

def is_banned(db, uid: str) -> bool:
    try:
        return db.query(BannedUser).filter(BannedUser.user_id == uid).first() is not None
    except Exception:
        return False

@app.get("/api/users/me")
def me(authorization: str = Header(default="")):
    uid = current_user(authorization)
    return {"id": uid, "balance": 0.0}

class UploadBase64Payload(BaseModel):
    filename: str
    content_b64: str
    mime_type: Optional[str] = "application/octet-stream"

@app.post("/api/uploads/base64")
def upload_base64(payload: UploadBase64Payload):
    # save under /web/uploads
    updir = os.path.join(os.path.dirname(__file__), "web", "uploads")
    os.makedirs(updir, exist_ok=True)
    ext = ""
    if "." in payload.filename:
        ext = "." + payload.filename.split(".")[-1].lower()
    name = uuid.uuid4().hex + ext
    full = os.path.join(updir, name)
    raw = base64.b64decode(payload.content_b64.encode())
    with open(full, "wb") as f:
        f.write(raw)
    url = f"/web/uploads/{name}"
    ct = (payload.mime_type or "").lower()
    kind = "file"
    if ct.startswith("image/"):
        kind = "image"
    elif ct.startswith("video/"):
        kind = "video"
    return {"url": url, "content_type": ct, "kind": kind}

from fastapi import Request

@app.post("/api/uploads/raw")
async def upload_raw(request: Request, filename: str = "upload.bin", mime_type: Optional[str] = "application/octet-stream"):
    updir = os.path.join(os.path.dirname(__file__), "web", "uploads")
    os.makedirs(updir, exist_ok=True)
    ext = ""
    if "." in filename:
        ext = "." + filename.split(".")[-1].lower()
    name = uuid.uuid4().hex + ext
    full = os.path.join(updir, name)
    data = await request.body()
    with open(full, "wb") as f:
        f.write(data)
    url = f"/web/uploads/{name}"
    ct = (mime_type or "").lower()
    kind = "file"
    if ct.startswith("image/"):
        kind = "image"
    elif ct.startswith("video/"):
        kind = "video"
    return {"url": url, "content_type": ct, "kind": kind}
@app.post("/api/uploads")
async def upload_unified(request: Request):
    ctype = request.headers.get("content-type", "") or ""
    updir = os.path.join(os.path.dirname(__file__), "web", "uploads")
    os.makedirs(updir, exist_ok=True)
    if "application/json" in ctype:
        data = await request.json()
        filename = (data.get("filename") or "upload.bin")
        mime_type = (data.get("mime_type") or "application/octet-stream")
        ext = ""
        if "." in filename:
            ext = "." + filename.split(".")[-1].lower()
        name = uuid.uuid4().hex + ext
        full = os.path.join(updir, name)
        raw = base64.b64decode((data.get("content_b64") or "").encode())
        with open(full, "wb") as f:
            f.write(raw)
        url = f"/web/uploads/{name}"
        ct = (mime_type or "").lower()
        kind = "file"
        if ct.startswith("image/"):
            kind = "image"
        elif ct.startswith("video/"):
            kind = "video"
        return {"url": url, "content_type": ct, "kind": kind}
    body = await request.body()
    params = request.query_params
    filename = params.get("filename") or "upload.bin"
    mime_type = params.get("mime_type") or "application/octet-stream"
    ext = ""
    if "." in filename:
        ext = "." + filename.split(".")[-1].lower()
    name = uuid.uuid4().hex + ext
    full = os.path.join(updir, name)
    with open(full, "wb") as f:
        f.write(body or b"")
    url = f"/web/uploads/{name}"
    ct = (mime_type or "").lower()
    kind = "file"
    if ct.startswith("image/"):
        kind = "image"
    elif ct.startswith("video/"):
        kind = "video"
    return {"url": url, "content_type": ct, "kind": kind}
@app.get("/api/pay/codepool_for_order")
def codepool_for_order(oid: int, channel: Optional[str] = None):
    db = SessionLocal()
    try:
        o = db.query(Order).filter(Order.id == oid).first()
        if not o:
            raise HTTPException(status_code=404, detail="order not found")
        ch = None
        if channel:
            ch = (channel or "").lower()
            if ch not in ("wechat", "alipay"):
                ch = None
        cp = pick_codepool_for_order(db, o, ch)
        if cp and (not o.codepool_id_opt or o.pay_channel_opt != ch):
            o.codepool_id_opt = cp.id
            if ch:
                o.pay_channel_opt = ch
            db.commit()
            add_event(db, o.id, "去支付", (ch or ""))
        return {"qr_url": (cp.image_url if cp else None), "codepool_id": (cp.id if cp else None), "channel": (ch or (cp.channel if cp else None))}
    finally:
        db.close()

# ------------------- admin endpoints -------------------
def check_admin(authorization: str):
    if authorization == ADMIN_TOKEN:
        return True
    if authorization.lower().startswith("bearer "):
        # accept any bearer in this simplified version
        return True
    raise HTTPException(status_code=401, detail="unauthorized")

class BanUserPayload(BaseModel):
    user_id: str

@app.post("/api/admin/users/ban")
def admin_ban_user(payload: BanUserPayload, authorization: str = Header(default="")):
    check_admin(authorization)
    db = SessionLocal()
    try:
        uid = (payload.user_id or "").strip()
        if not uid: raise HTTPException(status_code=400, detail="missing user_id")
        if db.query(BannedUser).filter(BannedUser.user_id == uid).first():
            return {"ok": True, "status": "already"}
        db.add(BannedUser(user_id=uid)); db.commit()
        return {"ok": True, "status": "banned"}
    finally:
        db.close()

@app.post("/api/admin/users/unban")
def admin_unban_user(payload: BanUserPayload, authorization: str = Header(default="")):
    check_admin(authorization)
    db = SessionLocal()
    try:
        uid = (payload.user_id or "").strip()
        if not uid: raise HTTPException(status_code=400, detail="missing user_id")
        r = db.query(BannedUser).filter(BannedUser.user_id == uid).first()
        if r:
            db.delete(r); db.commit()
            return {"ok": True, "status": "unbanned"}
        return {"ok": True, "status": "not_found"}
    finally:
        db.close()

@app.get("/api/admin/users/banned")
def admin_banned_users(authorization: str = Header(default="")):
    check_admin(authorization)
    db = SessionLocal()
    try:
        rows = db.query(BannedUser).order_by(BannedUser.id.desc()).all()
        return [{"user_id": r.user_id, "ts": to_cst_iso(r.created_at)} for r in rows]
    finally:
        db.close()
class CodepoolAddPayload(BaseModel):
    project_id: Optional[int] = None
    image_url: str
    enabled: int = 1
    channel: Optional[str] = "unknown"
    display_name: Optional[str] = None

@app.post("/api/admin/codepool/add")
def admin_codepool_add(payload: CodepoolAddPayload, authorization: str = Header(default="")):
    check_admin(authorization)
    db = SessionLocal()
    try:
        ch = (payload.channel or "unknown").lower()
        if ch not in ("wechat", "alipay", "unknown"):
            ch = "unknown"
        cp = Codepool(project_id=payload.project_id, image_url=payload.image_url, enabled=payload.enabled, channel=ch, display_name=payload.display_name)
        db.add(cp); db.commit(); db.refresh(cp)
        return {"ok": True, "id": cp.id}
    finally:
        db.close()

@app.get("/api/admin/codepool/list")
def admin_codepool_list(authorization: str = Header(default="")):
    check_admin(authorization)
    db = SessionLocal()
    try:
        rows = db.query(Codepool).order_by(Codepool.id.desc()).all()
        return [{"id": r.id, "project_id": r.project_id, "image_url": r.image_url, "enabled": r.enabled, "channel": r.channel, "display_name": r.display_name} for r in rows]
    finally:
        db.close()

@app.post("/api/admin/codepool/delete")
def admin_codepool_delete(id: int, authorization: str = Header(default="")):
    check_admin(authorization)
    db = SessionLocal()
    try:
        cp = db.query(Codepool).filter(Codepool.id == id).first()
        if not cp: raise HTTPException(status_code=404, detail="not found")
        db.delete(cp); db.commit()
        return {"ok": True}
    finally:
        db.close()

class AdminChatSendPayload(BaseModel):
    user_id: str
    text: Optional[str] = None
    kind: Optional[str] = "text"
    content: Optional[str] = None

@app.post("/api/admin/chat/send")
def admin_chat_send(payload: AdminChatSendPayload, authorization: str = Header(default="")):
    check_admin(authorization)
    db = SessionLocal()
    try:
        k = (payload.kind or "text").strip().lower()
        if k not in ("text", "image", "video", "file"):
            k = "text"
        if k == "text":
            txt = (payload.text or "").strip()
            if not txt:
                raise HTTPException(status_code=400, detail="empty text")
            cnt = txt
        else:
            cnt = (payload.content or "").strip()
            if not cnt:
                raise HTTPException(status_code=400, detail="empty content")
        msg = ChatMessage(user_id=payload.user_id, sender="agent", kind=k, content=cnt)
        db.add(msg); db.commit()
        return {"ok": True}
    finally:
        db.close()

class AdminBroadcastPayload(BaseModel):
    kind: Optional[str] = "text"
    text: Optional[str] = None
    content: Optional[str] = None
    all: Optional[bool] = False
    user_ids: Optional[List[str]] = []

@app.post("/api/admin/chat/broadcast")
def admin_chat_broadcast(payload: AdminBroadcastPayload, authorization: str = Header(default="")):
    check_admin(authorization)
    db = SessionLocal()
    try:
        k = (payload.kind or "text").strip().lower()
        if k not in ("text", "image", "video", "file"):
            k = "text"
        if k == "text":
            cnt = (payload.text or "").strip()
        else:
            cnt = (payload.content or "").strip()
        if not cnt:
            raise HTTPException(status_code=400, detail="empty content")
        targets: List[str] = []
        if payload.all:
            rows = db.query(User).all()
            targets = [u.id_uuid for u in rows]
        else:
            targets = [x.strip() for x in (payload.user_ids or []) if x and x.strip()]
        if not targets:
            raise HTTPException(status_code=400, detail="no targets")
        for uid in targets:
            db.add(ChatMessage(user_id=uid, sender="agent", kind=k, content=cnt))
        db.commit()
        return {"ok": True, "count": len(targets)}
    finally:
        db.close()

class AutoReplyAddPayload(BaseModel):
    keyword: str
    reply_text: str
    enabled: Optional[int] = 1

@app.get("/api/admin/autoreply/list")
def admin_autoreply_list(authorization: str = Header(default="")):
    check_admin(authorization)
    db = SessionLocal()
    try:
        rows = db.query(AutoReplyRule).order_by(AutoReplyRule.id.desc()).all()
        return [{"id": r.id, "keyword": r.keyword, "reply_text": r.reply_text, "enabled": int(r.enabled or 0)} for r in rows]
    finally:
        db.close()

@app.post("/api/admin/autoreply/add")
def admin_autoreply_add(payload: AutoReplyAddPayload, authorization: str = Header(default="")):
    check_admin(authorization)
    db = SessionLocal()
    try:
        kw = (payload.keyword or "").strip()
        rp = (payload.reply_text or "").strip()
        if not kw or not rp:
            raise HTTPException(status_code=400, detail="invalid payload")
        # upsert by keyword
        r = db.query(AutoReplyRule).filter(AutoReplyRule.keyword == kw).first()
        if r:
            r.reply_text = rp
            r.enabled = int(payload.enabled or 1)
            db.commit()
            return {"ok": True, "id": r.id}
        r = AutoReplyRule(keyword=kw, reply_text=rp, enabled=int(payload.enabled or 1))
        db.add(r); db.commit(); db.refresh(r)
        return {"ok": True, "id": r.id}
    finally:
        db.close()

@app.post("/api/admin/autoreply/delete")
def admin_autoreply_delete(id: int, authorization: str = Header(default="")):
    check_admin(authorization)
    db = SessionLocal()
    try:
        r = db.query(AutoReplyRule).filter(AutoReplyRule.id == id).first()
        if not r:
            raise HTTPException(status_code=404, detail="not found")
        db.delete(r); db.commit()
        return {"ok": True}
    finally:
        db.close()

@app.get("/api/admin/chat/messages")
def admin_chat_messages(user_id: str, authorization: str = Header(default="")):
    check_admin(authorization)
    db = SessionLocal()
    try:
        rows = db.query(ChatMessage).filter(ChatMessage.user_id == user_id).order_by(ChatMessage.id.asc()).all()
        return [{"sender": r.sender, "kind": r.kind, "content": r.content, "ts": to_cst_iso(r.created_at)} for r in rows]
    finally:
        db.close()

@app.get("/api/admin/chat/users")
def admin_chat_users(authorization: str = Header(default=""), limit: int = 50):
    check_admin(authorization)
    db = SessionLocal()
    try:
        rows = db.query(ChatMessage).order_by(ChatMessage.id.desc()).limit(1000).all()
        latest: dict[str, ChatMessage] = {}
        for r in rows:
            if r.user_id not in latest:
                latest[r.user_id] = r
        arr = []
        for u, m in latest.items():
            o = db.query(Order).filter(Order.user_id == u).order_by(Order.id.desc()).first()
            item = {
                "user_id": u,
                "last_text": m.content,
                "last_ts": to_cst_iso(m.created_at),
                "order_id": (o.id if o else None),
                "order_no": (o.order_no if o else None),
                "order_status": (o.status if o else None),
                "project_id": (o.project_id if o else None),
            }
            arr.append(item)
        arr.sort(key=lambda x: x["last_ts"], reverse=True)
        if arr:
            return arr[:limit]
        # fallback: show users even if没有消息
        users = db.query(User).order_by(User.id.desc()).limit(limit).all()
        fb = []
        for u in users:
            o = db.query(Order).filter(Order.user_id == u.id_uuid).order_by(Order.id.desc()).first()
            fb.append({
                "user_id": u.id_uuid,
                "last_text": "",
                "last_ts": (to_cst_iso(u.created_at) if hasattr(u, "created_at") and u.created_at else ""),
                "order_id": (o.id if o else None),
                "order_no": (o.order_no if o else None),
                "order_status": (o.status if o else None),
                "project_id": (o.project_id if o else None),
            })
        return fb
    finally:
        db.close()

@app.get("/api/admin/users/list")
def admin_users_list(authorization: str = Header(default=""), limit: int = 100):
    check_admin(authorization)
    db = SessionLocal()
    try:
        rows = db.query(User).order_by(User.id.desc()).limit(limit).all()
        return [{"id": r.id_uuid} for r in rows]
    finally:
        db.close()

class UnreadCountsPayload(BaseModel):
    last_seen: Dict[str, str]

@app.post("/api/admin/chat/unread_counts")
def admin_unread_counts(payload: UnreadCountsPayload, authorization: str = Header(default="")):
    check_admin(authorization)
    db = SessionLocal()
    try:
        out = []
        for uid, ts in (payload.last_seen or {}).items():
            try:
                dt = datetime.fromisoformat(ts)
            except Exception:
                dt = datetime.fromtimestamp(0, tz=timezone.utc)
            cnt = db.query(ChatMessage).filter(ChatMessage.user_id == uid, ChatMessage.sender == "user", ChatMessage.created_at > dt).count()
            out.append({"user_id": uid, "count": int(cnt)})
        return out
    finally:
        db.close()
@app.get("/api/projects")
def list_projects() -> List[Dict]:
    db = SessionLocal()
    try:
        rows = db.query(Project).filter(Project.status == "online").all()
        import json
        return [{
            "id": r.id, "name": r.name, "intro": r.intro, "price": r.base_price, "sla_hours": r.sla_hours,
            "required_fields": json.loads(r.required_fields or "[]"), "optional_fields": json.loads(r.optional_fields or "[]"),
            "field_hints": json.loads(r.field_hints or "{}"),
        } for r in rows]
    finally:
        db.close()

@app.get("/api/projects/{pid}")
def get_project(pid: int):
    db = SessionLocal()
    try:
        r = db.query(Project).filter(Project.id == pid, Project.status == "online").first()
        if not r: raise HTTPException(status_code=404, detail="not found")
        import json
        return {
            "id": r.id, "name": r.name, "intro": r.intro, "price": r.base_price, "sla_hours": r.sla_hours,
            "required_fields": json.loads(r.required_fields or "[]"), "optional_fields": json.loads(r.optional_fields or "[]"),
            "field_hints": json.loads(r.field_hints or "{}"),
        }
    finally:
        db.close()

@app.post("/api/orders/create")
def create_order(payload: OrderCreatePayload, authorization: str = Header(default="")):
    uid = current_user(authorization)
    db = SessionLocal()
    try:
        if is_banned(db, uid):
            raise HTTPException(status_code=403, detail="user banned")
        p = db.query(Project).filter(Project.id == payload.project_id, Project.status == "online").first()
        if not p: raise HTTPException(status_code=404, detail="project not found")
        import json
        req = json.loads(p.required_fields or "[]")
        opt = json.loads(p.optional_fields or "[]")
        fields = payload.fields or {}
        for f in req:
            v = (fields.get(f) or "").strip()
            if not v: raise HTTPException(status_code=400, detail=f"missing {f}")
            if not validate_field(f, v): raise HTTPException(status_code=400, detail=f"invalid {f}")
        for f in opt:
            v = (fields.get(f) or "").strip()
            if v and not validate_field(f, v): raise HTTPException(status_code=400, detail=f"invalid {f}")
        ono = "ORD" + datetime.now(timezone.utc).strftime("%Y%m%d") + uuid.uuid4().hex[:6].upper()
        o = Order(order_no=ono, user_id=uid, project_id=p.id, amount=p.base_price, status="待支付",
                  pay_deadline=datetime.now(timezone.utc))
        db.add(o); db.commit(); db.refresh(o)
        return {"order_id": o.id, "order_no": o.order_no}
    finally:
        db.close()

@app.get("/api/orders/{oid}")
def get_order(oid: int, authorization: str = Header(default="")):
    uid = current_user(authorization)
    db = SessionLocal()
    try:
        o = db.query(Order).filter(Order.id == oid, Order.user_id == uid).first()
        if not o: raise HTTPException(status_code=404, detail="not found")
        return {
            "order_id": o.id, "order_no": o.order_no, "status": o.status, "amount": o.amount,
            "pay_deadline": (to_cst_iso(o.pay_deadline) if o.pay_deadline else None)
        }
    finally:
        db.close()

@app.post("/api/chat/send")
def chat_send(kind: str = "text", content: str = "", authorization: str = Header(default="")):
    uid = current_user(authorization)
    db = SessionLocal()
    try:
        if is_banned(db, uid):
            raise HTTPException(status_code=403, detail="user banned")
        msg = ChatMessage(user_id=uid, sender="user", kind=kind, content=content)
        db.add(msg); db.commit()
        # precise keyword auto-reply: only when user sends text and exact match
        try:
            if (kind or "").strip().lower() == "text":
                txt = (content or "")
                r = db.query(AutoReplyRule).filter(AutoReplyRule.enabled == 1, AutoReplyRule.keyword == txt).first()
                if r and (r.reply_text or "").strip():
                    db.add(ChatMessage(user_id=uid, sender="agent", kind="text", content=r.reply_text.strip()))
                    db.commit()
        except Exception:
            pass
        return {"ok": True}
    finally:
        db.close()

@app.get("/api/chat/messages")
def chat_messages(authorization: str = Header(default="")):
    uid = current_user(authorization)
    db = SessionLocal()
    try:
        rows = db.query(ChatMessage).filter(ChatMessage.user_id == uid).order_by(ChatMessage.id.asc()).all()
        return [{"sender": r.sender, "kind": r.kind, "content": r.content, "ts": to_cst_iso(r.created_at)} for r in rows]
    finally:
        db.close()

class MarkPaidPayload(BaseModel):
    order_id: int
    proof_url: str

@app.post("/api/admin/orders/mark_paid")
def mark_paid(payload: MarkPaidPayload, authorization: str = Header(default="")):
    if authorization != ADMIN_TOKEN and not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="unauthorized")
    db = SessionLocal()
    try:
        o = db.query(Order).filter(Order.id == payload.order_id).first()
        if not o: raise HTTPException(status_code=404, detail="not found")
        if o.status != "待支付": raise HTTPException(status_code=400, detail="invalid status")
        if not (payload.proof_url or "").strip(): raise HTTPException(status_code=400, detail="proof image required")
        o.status = "已支付"; o.pay_proof_url_opt = payload.proof_url
        db.commit()
        add_event(db, o.id, "财务确认", payload.proof_url)
        add_event(db, o.id, "已支付", "")
        if o.codepool_id_opt:
            d = today_cst_str()
            s = db.query(PayCodeStats).filter(PayCodeStats.codepool_id == o.codepool_id_opt, PayCodeStats.stat_date == d).first()
            if not s:
                s = PayCodeStats(codepool_id=o.codepool_id_opt, stat_date=d, order_count=0, total_amount=0.0)
                db.add(s)
            s.order_count = int((s.order_count or 0)) + 1
            s.total_amount = float((s.total_amount or 0.0)) + float(o.amount or 0.0)
            db.commit()
        return {"ok": True}
    finally:
        db.close()

class UserConfirmPaidPayload(BaseModel):
    order_id: int

@app.post("/api/orders/confirm_paid")
def user_confirm_paid(payload: UserConfirmPaidPayload, authorization: str = Header(default="")):
    uid = current_user(authorization)
    db = SessionLocal()
    try:
        o = db.query(Order).filter(Order.id == payload.order_id, Order.user_id == uid).first()
        if not o:
            raise HTTPException(status_code=404, detail="not found")
        if o.status != "待支付":
            raise HTTPException(status_code=400, detail="invalid status")
        o.status = "已支付"
        db.commit()
        add_event(db, o.id, "用户确认", "")
        return {"ok": True}
    finally:
        db.close()

class AdminMarkDeliveredPayload(BaseModel):
    order_id: int
    note: Optional[str] = None

@app.post("/api/admin/orders/mark_delivered")
def admin_mark_delivered(payload: AdminMarkDeliveredPayload, authorization: str = Header(default="")):
    check_admin(authorization)
    db = SessionLocal()
    try:
        o = db.query(Order).filter(Order.id == payload.order_id).first()
        if not o: raise HTTPException(status_code=404, detail="not found")
        if o.status not in ("已支付", "已交付"):
            raise HTTPException(status_code=400, detail="invalid status")
        o.status = "已交付"
        if payload.note:
            o.internal_note_opt = (o.internal_note_opt or "") + ("\n交付备注：" + payload.note.strip())
        db.commit()
        add_event(db, o.id, "已交付", payload.note or "")
        return {"ok": True}
    finally:
        db.close()
class AdminOrdersListPayload(BaseModel):
    status: Optional[str] = None
    limit: int = 100

@app.post("/api/admin/orders/list")
def admin_orders_list(payload: AdminOrdersListPayload = AdminOrdersListPayload(), authorization: str = Header(default="")):
    check_admin(authorization)
    db = SessionLocal()
    try:
        q = db.query(Order)
        if payload.status:
            q = q.filter(Order.status == payload.status)
        rows = q.order_by(Order.id.desc()).limit(payload.limit).all()
        return [{"order_id": r.id, "order_no": r.order_no, "user_id": r.user_id, "project_id": r.project_id, "amount": r.amount, "status": r.status, "created_at": to_cst_iso(r.created_at)} for r in rows]
    finally:
        db.close()

@app.get("/api/orders/timeline")
def order_timeline(oid: int, authorization: str = Header(default="")):
    db = SessionLocal()
    try:
        evs = db.query(OrderEvent).filter(OrderEvent.order_id == oid).order_by(OrderEvent.id.asc()).all()
        return [{"name": e.name, "detail": e.detail, "ts": to_cst_iso(e.created_at)} for e in evs]
    finally:
        db.close()

class AdminProjectsUpsertPayload(BaseModel):
    id: Optional[int] = None
    name: str
    intro: str
    base_price: float
    sla_hours: int
    required_fields: List[str] = []
    optional_fields: List[str] = []
    field_hints: Dict[str, str] = {}
    status: str = "online"

@app.get("/api/admin/projects/list")
def admin_projects_list(authorization: str = Header(default="")):
    check_admin(authorization)
    db = SessionLocal()
    try:
        rows = db.query(Project).order_by(Project.id.desc()).all()
        import json
        return [{"id": r.id, "name": r.name, "intro": r.intro, "base_price": r.base_price, "sla_hours": r.sla_hours, "required_fields": json.loads(r.required_fields or "[]"), "optional_fields": json.loads(r.optional_fields or "[]"), "field_hints": json.loads(r.field_hints or "{}"), "status": r.status} for r in rows]
    finally:
        db.close()

@app.post("/api/admin/projects/upsert")
def admin_projects_upsert(payload: AdminProjectsUpsertPayload, authorization: str = Header(default="")):
    check_admin(authorization)
    db = SessionLocal()
    try:
        import json
        if payload.id:
            p = db.query(Project).filter(Project.id == payload.id).first()
            if not p: raise HTTPException(status_code=404, detail="not found")
            p.name = payload.name; p.intro = payload.intro; p.base_price = payload.base_price; p.sla_hours = payload.sla_hours
            p.required_fields = json.dumps(payload.required_fields or [])
            p.optional_fields = json.dumps(payload.optional_fields or [])
            p.field_hints = json.dumps(payload.field_hints or {})
            p.status = payload.status or "online"
            db.commit()
            return {"ok": True, "id": p.id}
        else:
            p = Project(name=payload.name, intro=payload.intro, base_price=payload.base_price, sla_hours=payload.sla_hours, status=payload.status or "online")
            import json as _json
            p.required_fields = _json.dumps(payload.required_fields or [])
            p.optional_fields = _json.dumps(payload.optional_fields or [])
            p.field_hints = _json.dumps(payload.field_hints or {})
            db.add(p); db.commit(); db.refresh(p)
            return {"ok": True, "id": p.id}
    finally:
        db.close()

@app.post("/api/admin/projects/delete")
def admin_projects_delete(id: int, authorization: str = Header(default="")):
    check_admin(authorization)
    db = SessionLocal()
    try:
        p = db.query(Project).filter(Project.id == id).first()
        if not p: raise HTTPException(status_code=404, detail="not found")
        db.delete(p); db.commit()
        return {"ok": True}
    finally:
        db.close()

@app.get("/api/admin/codepool/stats")
def admin_codepool_stats(date: Optional[str] = None, authorization: str = Header(default="")):
    check_admin(authorization)
    db = SessionLocal()
    try:
        d = date or today_str()
        rows = db.query(PayCodeStats).filter(PayCodeStats.stat_date == d).order_by(PayCodeStats.id.asc()).all()
        out = []
        for r in rows:
            cp = db.query(Codepool).filter(Codepool.id == r.codepool_id).first()
            out.append({"codepool_id": r.codepool_id, "display_name": (cp.display_name if cp else None), "image_url": cp.image_url if cp else None, "project_id": cp.project_id if cp else None, "stat_date": r.stat_date, "order_count": r.order_count, "total_amount": r.total_amount})
        return out
    finally:
        db.close()

@app.get("/api/admin/codepool/stats_orders")
def admin_codepool_stats_orders(date: Optional[str] = None, authorization: str = Header(default="")):
    check_admin(authorization)
    db = SessionLocal()
    try:
        d = date or today_str()
        cst = timezone(timedelta(hours=8))
        try:
            start_cst = datetime.fromisoformat(d + "T00:00:00+08:00")
        except Exception:
            now_cst = datetime.now(cst)
            start_cst = now_cst.replace(hour=0, minute=0, second=0, microsecond=0)
        start = start_cst.astimezone(timezone.utc)
        end = start + timedelta(days=1)
        evs = db.query(OrderEvent).filter(OrderEvent.name == "财务确认", OrderEvent.created_at >= start, OrderEvent.created_at < end).order_by(OrderEvent.id.asc()).all()
        out = []
        for e in evs:
            o = db.query(Order).filter(Order.id == e.order_id).first()
            if not o or not o.codepool_id_opt or o.status != "已支付":
                continue
            cp = db.query(Codepool).filter(Codepool.id == o.codepool_id_opt).first()
            dt_utc = e.created_at
            if dt_utc.tzinfo is None:
                dt_utc = dt_utc.replace(tzinfo=timezone.utc)
            else:
                dt_utc = dt_utc.astimezone(timezone.utc)
            paid_cst = dt_utc.astimezone(cst).strftime("%Y-%m-%d %H:%M:%S")
            out.append({"codepool_id": o.codepool_id_opt, "display_name": (cp.display_name if cp else None), "order_id": o.id, "order_no": o.order_no, "user_id": o.user_id, "amount": o.amount, "paid_at": paid_cst})
        return out
    finally:
        db.close()
