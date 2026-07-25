
import os
import json
import time
import uuid
import sqlite3
import asyncio
import threading
import concurrent.futures
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import requests
import telebot
from dotenv import load_dotenv


# --- Configuration ---
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "8715344690:AAHQUX6eSkmHzOE7gmpHSy0X0IJ157lIoAs")
ADMIN_IDS = [int(x) for x in os.getenv("ADMIN_IDS", "8739344756").split(",") if x]
ADMIN_CHAT_ID = os.getenv("ADMIN_CHAT_ID")
FORCE_JOIN_CHANNEL = os.getenv("FORCE_JOIN_CHANNEL", "@YourChannelName")

GAME_COST_POINTS = int(os.getenv("GAME_COST_POINTS", 1))
JOIN_REWARD_POINTS = int(os.getenv("JOIN_REWARD_POINTS", 1))
REFERRAL_REWARD_POINTS = int(os.getenv("REFERRAL_REWARD_POINTS", 2))

DATABASE_NAME = os.getenv("DATABASE_NAME", "shopsy_bot.db")

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN is missing in .env")


# --- Database Functions ---
def get_db():
    conn = sqlite3.connect(DATABASE_NAME)
    conn.row_factory = sqlite3.Row
    return conn

def init_db():
    conn = get_db()
    cursor = conn.cursor()
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        points INTEGER DEFAULT 0,
        games_played INTEGER DEFAULT 0,
        referral_count INTEGER DEFAULT 0,
        is_verified INTEGER DEFAULT 0,
        last_login TEXT,
        joined_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS sessions (
        user_id INTEGER PRIMARY KEY,
        phone TEXT,
        session_data TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        expires_at TIMESTAMP
    )
    """)
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS referrals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        referrer_id INTEGER,
        referred_id INTEGER,
        rewarded INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(referrer_id, referred_id)
    )
    """)
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        action TEXT,
        details TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS admins (
        user_id INTEGER PRIMARY KEY,
        added_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS settings (
        key TEXT PRIMARY KEY,
        value TEXT
    )
    """)
    
    cursor.execute("""
    CREATE TABLE IF NOT EXISTS logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        level TEXT,
        message TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """)
    
    conn.commit()
    conn.close()

def update_user_points(user_id, points_change):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET points = points + ? WHERE user_id = ?", (points_change, user_id))
    conn.commit()
    conn.close()

def get_user(user_id):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
    user = cursor.fetchone()
    conn.close()
    return user

def create_user(user_id, username):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("INSERT OR IGNORE INTO users (user_id, username) VALUES (?, ?)", (user_id, username))
    conn.commit()
    conn.close()

def set_verified(user_id):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("UPDATE users SET is_verified = 1 WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()

def log_action(user_id, action, details=""):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("INSERT INTO history (user_id, action, details) VALUES (?, ?, ?)", (user_id, action, details))
    conn.commit()
    conn.close()


# --- Session Functions ---
def save_user_session(user_id, phone, session_dict):
    conn = get_db()
    cursor = conn.cursor()
    session_json = json.dumps(session_dict)
    cursor.execute("""
    INSERT OR REPLACE INTO sessions (user_id, phone, session_data)
    VALUES (?, ?, ?)
    """, (user_id, phone, session_json))
    conn.commit()
    conn.close()

def get_user_session(user_id):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT session_data FROM sessions WHERE user_id = ?", (user_id,))
    row = cursor.fetchone()
    conn.close()
    if row:
        return json.loads(row["session_data"])
    return None

def delete_user_session(user_id):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM sessions WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()


# --- Keyboard Functions ---
def force_join_keyboard(channel_url):
    markup = telebot.types.InlineKeyboardMarkup()
    markup.add(telebot.types.InlineKeyboardButton("📢 Join Channel", url=channel_url))
    markup.add(telebot.types.InlineKeyboardButton("✅ Check Again", callback_data="check_join"))
    return markup

def user_dashboard_keyboard(is_logged_in=False, is_login_process=False):
    markup = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True)
    if is_login_process:
        markup.row("❌ Cancel Login")
        return markup
    
    if not is_logged_in:
        markup.row("🔐 Login", "🎁 Refer")
    else:
        markup.row("🎮 Start Game", "💰 My Points")
        markup.row("🎁 Refer", "👤 Profile")
        markup.row("🚪 Logout")
    return markup

def admin_dashboard_keyboard():
    markup = telebot.types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.row("👥 Total Users", "🟢 Active Users")
    markup.row("💰 Grant Points", "➖ Deduct Points")
    markup.row("📢 Broadcast", "🔍 User Details")
    markup.row("📜 User History", "📊 Statistics")
    return markup


# --- Animation Class ---
class GameAnimator:
    def __init__(self, bot, chat_id, message_id):
        self.bot = bot
        self.chat_id = chat_id
        self.message_id = message_id

    async def update(self, text):
        try:
            self.bot.edit_message_text(text, self.chat_id, self.message_id)
        except Exception:
            pass

    async def start_animation(self):
        steps = [
            "🎮 Starting Game...",
            "⚡ Connecting...",
            "🎯 Playing Game...",
            "🎁 Claiming Rewards...",
            "🔒 Logging Out...",
            "💰 Updating Points...",
        ]
        pass


# --- Utility Functions ---
def generate_referral_link(bot_username, user_id):
    return f"https://t.me/{bot_username}?start=ref_{user_id}"

def format_duration(seconds):
    mins, secs = divmod(int(seconds), 60)
    hours, mins = divmod(mins, 60)
    return f"{hours}h {mins}m {secs}s"


# --- Referral Functions ---
def add_referral(referrer_id, referred_id):
    if referrer_id == referred_id:
        return False
    
    conn = get_db()
    cursor = conn.cursor()
    try:
        cursor.execute("INSERT INTO referrals (referrer_id, referred_id) VALUES (?, ?)", (referrer_id, referred_id))
        conn.commit()
        return True
    except:
        return False
    finally:
        conn.close()

def process_referral_reward(referred_id):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT referrer_id FROM referrals WHERE referred_id = ? AND rewarded = 0", (referred_id,))
    row = cursor.fetchone()
    
    if row:
        referrer_id = row["referrer_id"]
        update_user_points(referrer_id, REFERRAL_REWARD_POINTS)
        cursor.execute("UPDATE users SET referral_count = referral_count + 1 WHERE user_id = ?", (referrer_id,))
        cursor.execute("UPDATE referrals SET rewarded = 1 WHERE referred_id = ?", (referred_id,))
        conn.commit()
        log_action(referrer_id, "REFERRAL_REWARD", f"Referred user {referred_id}")
        conn.close()
        return referrer_id
    
    conn.close()
    return None


# --- Admin Functions ---
def is_admin(user_id):
    return user_id in ADMIN_IDS

def get_stats():
    conn = get_db()
    cursor = conn.cursor()
    
    stats = {}
    cursor.execute("SELECT COUNT(*) FROM users")
    stats["total_users"] = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(*) FROM sessions")
    stats["active_sessions"] = cursor.fetchone()[0]
    
    cursor.execute("SELECT SUM(games_played) FROM users")
    stats["total_games"] = cursor.fetchone()[0] or 0
    
    cursor.execute("SELECT SUM(points) FROM users WHERE points > 0")
    stats["total_points_dist"] = cursor.fetchone()[0] or 0
    
    cursor.execute("SELECT COUNT(*) FROM referrals")
    stats["total_referrals"] = cursor.fetchone()[0] or 0
    
    conn.close()
    return stats

def broadcast_message(bot, message_text):
    conn = get_db()
    cursor = conn.cursor()
    cursor.execute("SELECT user_id FROM users")
    users = cursor.fetchall()
    conn.close()
    
    count = 0
    for user in users:
        try:
            bot.send_message(user["user_id"], message_text)
            count += 1
        except:
            continue
    return count


# --- ShopsyClient and related (from legacy_flip.py) ---
ROME_TEMPLATE = "https://{dc}.rome.api.flipkart.net"

APP_VERSION = "2291175"
DEVICE_MODEL = "Pixel 9a"
DEVICE_BRAND = "Google"
DEFAULT_PINCODE = "226001"
FAST_PLAY_SEC = 10
FAST_RUNNER_SEC = 15

GAMES = [
    {"id": "runner-3d", "name": "Super Runner", "play_time": 94, "gems": 200},
    {"id": "city-builder", "name": "City Builder", "play_time": 47, "gems": 100},
    {"id": "match-3", "name": "Fruit Crush", "play_time": 35, "gems": 100},
    {"id": "goods-triple", "name": "Grocery Match", "play_time": 40, "gems": 100},
    {"id": "ludo", "name": "Ludo", "play_time": 50, "gems": 100},
    {"id": "nazaria", "name": "Nazar Pop", "play_time": 45, "gems": 100},
]

ONBOARDING_GAMIFICATION_PAGE_URI = (
    "/shopsyrevamp-onboarding-gamification-store"
    "?loadoutName=shopsy_onboarding_preferred_category_selection_v2"
)

DEFAULT_GAMIFICATION_BU_MAP: dict[str, Any] = {
    "COINS-uUFKMoaMyl": {
        "storePath": [
            "tyy/4io", "ajy/buh", "tyy/4io", "0pm/fcn/821/a7x/2rv", "0pm/fcn/821/a7x/2si",
            "0pm/fcn/821/fof", "0pm/0o7", "tyy/4mr/vnf", "tyy/4mr/3nu", "tyy/4mr/tp2",
            "tyy/4mr/fu6", "tyy/4mr/nkm", "tyy/4mr/q2u", "6bo/tia/8pp/p0w", "6bo/ai3/3oe",
            "6bo/g0i", "6bo/tia",
        ],
        "amount": "10",
        "rewardType": "COINS",
        "category": "",
    },
    "COINS-fZgrmb1I70": {
        "storePath": [
            "clo/qvw/kpn/9pk", "clo/ash/ohw/36j", "clo/ash/ank/loi", "clo/odx/maj/jhy",
            "clo/ash/axc/vop", "clo/h4p/fpl/cl5", "clo/odx/od7/0xx", "clo/vua/k58/4hp",
            "clo/vua/mle/8ie", "clo/vua/mle/8ie", "clo/cfv/ht7/cjo", "clo/vua/e8g/hbd",
            "clo/vua/iku/w5t", "clo/vua/zvq/je3",
        ],
        "amount": "10",
        "rewardType": "COINS",
        "category": "",
    },
    "COINS-Eb4vihNsag": {
        "storePath": [
            "eat/ltb", "eat/cpy", "eat/0pt", "eat/xhv", "hlc/etg/sxm", "eat/xgg",
            "upp/5ix/ymq", "hlc/etg", "hlc/etg",
        ],
        "amount": "10",
        "rewardType": "COINS",
        "category": "",
    },
}

def _deep_find(obj: Any, key: str) -> Any | None:
    if isinstance(obj, dict):
        if key in obj:
            return obj[key]
        for value in obj.values():
            found = _deep_find(value, key)
            if found is not None:
                return found
    elif isinstance(obj, list):
        for item in obj:
            found = _deep_find(item, key)
            if found is not None:
                return found
    return None


class LiveLog:
    def __init__(self, debug: bool = False) -> None:
        self.debug = debug
        self.start = time.time()

    def _ts(self) -> str:
        return datetime.now().strftime("%H:%M:%S")

    def _elapsed(self) -> str:
        sec = int(time.time() - self.start)
        return f"{sec // 60:02d}:{sec % 60:02d}"

    def info(self, msg: str) -> None:
        print(f"[{self._ts()} | {self._elapsed()}] {msg}", flush=True)

    def ok(self, msg: str) -> None:
        print(f"[{self._ts()} | {self._elapsed()}] [+] {msg}", flush=True)

    def warn(self, msg: str) -> None:
        print(f"[{self._ts()} | {self._elapsed()}] [!] {msg}", flush=True)

    def dbg(self, msg: str, data: Any = None) -> None:
        if not self.debug:
            return
        print(f"[{self._ts()} | {self._elapsed()}] [DEBUG] {msg}", flush=True)
        if data is not None:
            text = json.dumps(data, ensure_ascii=False, indent=2) if isinstance(data, (dict, list)) else str(data)
            if len(text) > 1200:
                text = text[:1200] + "\n... (truncated)"
            print(text, flush=True)

    def countdown(self, label: str, seconds: int) -> None:
        if seconds <= 0:
            return
        time.sleep(seconds)
        print(f"[{self._ts()} | {self._elapsed()}] {label} ... done ({seconds}s)", flush=True)


def _normalize_phone(phone: str) -> str:
    return phone.strip().replace("+91", "").replace(" ", "")


@dataclass
class ShopsySession:
    phone: str = ""
    device_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    visit_id: str = field(default_factory=lambda: f"{uuid.uuid4().hex}-{int(time.time() * 1000)}")
    dc_id: str = "1"
    at: str = ""
    sn: str = ""
    vid: str = ""
    secure_token: str = ""
    secure_cookie: str = ""
    account_id: str = ""
    user_name: str = ""
    is_logged_in: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {k: getattr(self, k) for k in self.__dataclass_fields__}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ShopsySession":
        return cls(**{k: data[k] for k in cls.__dataclass_fields__ if k in data})


def _extract_dc_id(data: dict[str, Any], http_status: int) -> str | None:
    is_dc = http_status == 406 or data.get("STATUS_CODE") == 406
    if not is_dc:
        return None
    if data.get("ERROR_MESSAGE") != "DC Change" and data.get("ERROR_CODE") != 2000:
        return None
    dc_info = (data.get("META_INFO") or {}).get("dcInfo") or data.get("RESPONSE") or {}
    dc_id = dc_info.get("id")
    return str(dc_id) if dc_id else None


class ShopsyClient:
    def __init__(
        self,
        log: LiveLog,
        fast: bool = True,
        phone: str = "",
        user_id: int = None,
        *,
        fresh: bool = False,
    ) -> None:
        self.log = log
        self.fast = fast
        self.phone = _normalize_phone(phone)
        self.user_id = user_id
        self.session = requests.Session()
        if fresh:
            saved = None
        else:
            if self.user_id:
                session_data = get_user_session(self.user_id)
                if session_data:
                    saved = ShopsySession.from_dict(session_data)
                else:
                    saved = None
            else:
                saved = None # Should not happen if user_id is passed

        self.ctx = saved or ShopsySession(phone=self.phone)
        if self.phone and not self.ctx.phone:
            self.ctx.phone = self.phone
        self._user_cache: dict[str, Any] | None = None
        self._sync_urls()
        self._lock = threading.Lock()
        self.parallel_results: list[dict] = []

    def _persist(self) -> None:
        key = _normalize_phone(self.ctx.phone or self.phone)
        if not key and self.ctx.account_id:
            key = f"acc_{self.ctx.account_id[-8:]}"
        if key and self.user_id:
            self.ctx.phone = key
            self.phone = key
            save_user_session(self.user_id, key, self.ctx.to_dict())

    @property
    def x_user_agent(self) -> str:
        return (
            f"Mozilla/5.0 (Linux; Android 15; {DEVICE_MODEL} Build/BD4A.250505.003) "
            f"FKUA/Retail/{APP_VERSION}/Android/Mobile "
            f"({DEVICE_BRAND}/{DEVICE_MODEL}/{self.ctx.device_id})"
        )

    def _sync_urls(self) -> None:
        self.base_url = ROME_TEMPLATE.format(dc=self.ctx.dc_id)

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    @staticmethod
    def _path_from_url(url: str) -> str:
        for path in ("/4/page/fetch", "/1/action/view", "/1/shopsy/games"):
            if path in url:
                return path
        if url.startswith("http"):
            idx = url.find("/", 8)
            return url[idx:] if idx != -1 else url
        return url

    def _switch_dc(self, dc_id: str) -> None:
        if self.ctx.dc_id == dc_id:
            return
        old = self.ctx.dc_id
        self.ctx.dc_id = dc_id
        self._sync_urls()
        self._persist()
        dc_name = (self._last_dc_meta or {}).get("dc", "?")
        self.log.info(f"DC Change: {old} -> {dc_id} ({dc_name}) | host={self.base_url}")
        self._last_dc_meta = None

    _last_dc_meta: dict[str, Any] | None = None

    def _partner_headers(self, *, layout: bool = False) -> dict[str, str]:
        headers = {
            "User-Agent": "okhttp/4.9.2",
            "Content-Type": "application/json; charset=UTF-8",
            "Accept-Encoding": "gzip",
            "X-PARTNER-CONTEXT": "{\"source\":\"reseller\"}",
            "FK-TENANT-ID": "SHOPSY",
            "business": "reseller",
            "X-User-Agent": self.x_user_agent,
            "X-Visit-Id": self.ctx.visit_id,
            "X-NewRelic-ID": "VwEHU1dSCxABUVlaAAQHU1UA",
        }
        if layout:
            headers["X-Layout-Version"] = "{\"appVersion\":\"910000\",\"frameworkVersion\":\"1.0\"}"
        if self.ctx.at:
            headers["at"] = self.ctx.at
        if self.ctx.sn:
            headers["sn"] = self.ctx.sn
        if self.ctx.secure_token:
            headers["secureToken"] = self.ctx.secure_token
        if self.ctx.secure_cookie:
            headers["secureCookie"] = self.ctx.secure_cookie
        return headers

    def _game_headers(self) -> dict[str, str]:
        return {
            "User-Agent": "okhttp/4.9.2",
            "Content-Type": "application/json; charset=UTF-8",
            "Accept-Encoding": "gzip",
            "x-user-agent": self.x_user_agent,
            "sessionid": "session_id",
            "X-NewRelic-ID": "VwEHU1dSCxABUVlaAAQHU1UA",
        }

    def _apply_session(self, data: dict[str, Any]) -> None:
        session = data.get("SESSION") or {}
        if not session:
            return
        self.ctx.at = session.get("at") or self.ctx.at
        self.ctx.sn = session.get("sn") or self.ctx.sn
        self.ctx.vid = session.get("vid") or self.ctx.vid
        self.ctx.secure_token = session.get("secureToken") or self.ctx.secure_token
        self.ctx.account_id = session.get("accountId") or self.ctx.account_id
        self.ctx.is_logged_in = bool(session.get("isLoggedIn"))
        if session.get("firstName"):
            last = session.get("lastName") or ""
            self.ctx.user_name = f"{session['firstName']} {last}".strip()
        self._persist()

    def _capture_secure_cookie(self, response: requests.Response) -> None:
        secure_cookie = response.headers.get("securecookie") or response.headers.get("secureCookie")
        if secure_cookie:
            self.ctx.secure_cookie = secure_cookie

    def _post_json(
        self,
        url: str,
        payload: dict[str, Any],
        *,
        game: bool = False,
        layout: bool = False,
    ) -> dict[str, Any]:
        path = self._path_from_url(url)
        last_error: str | None = None
        for attempt in range(5):
            active_url = self._url(path)
            headers = self._game_headers() if game else self._partner_headers(layout=layout)
            self.log.dbg(f"POST {active_url} (dc={self.ctx.dc_id}, try={attempt + 1})", payload)
            response = self.session.post(active_url, json=payload, headers=headers, timeout=30)
            self._capture_secure_cookie(response)

            try:
                data = response.json()
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"Invalid JSON from {url}: {response.text[:300]}") from exc

            dc_id = _extract_dc_id(data, response.status_code)
            if dc_id:
                self._last_dc_meta = (data.get("META_INFO") or {}).get("dcInfo") or data.get("RESPONSE")
                self._switch_dc(dc_id)
                last_error = f"DC Change -> retry on dc {dc_id}"
                continue

            self.log.dbg(f"Response HTTP {response.status_code}", data)

            if not game:
                self._apply_session(data)

            if response.ok:
                return data
            
            if last_error:
                self.log.warn(f"Request failed after {attempt + 1} tries: {last_error}")
                time.sleep(1)
                continue

            if response.status_code == 401:
                raise RuntimeError(f"Auth fail: {data}")
            if response.status_code == 404:
                raise RuntimeError(f"Not found: {data}")
            if response.status_code == 400 and data.get("ERROR_CODE") == 1000:
                raise RuntimeError(f"Bad request: {data}")
            if response.status_code == 500:
                raise RuntimeError(f"Server error: {data}")
            
            raise RuntimeError(f"Request failed with status {response.status_code}: {data}")

    def bootstrap(self) -> None:
        self.log.info("Bootstrap ho raha hai...")
        payload = {
            "deviceInfo": {
                "deviceId": self.ctx.device_id,
                "deviceModel": DEVICE_MODEL,
                "deviceBrand": DEVICE_BRAND,
            },
            "location": {"pincode": DEFAULT_PINCODE},
        }
        data = self._post_json(self._url("/4/page/fetch"), payload, layout=True)
        self.log.dbg("Bootstrap response", data)
        self.log.ok("Bootstrap OK")

    def refresh_session(self) -> None:
        if not self.ctx.is_logged_in:
            return
        self.log.info("Session refresh ho raha hai...")
        payload = {
            "actionRequestContext": {
                "type": "LOGIN_SHOPSY2",
                "loginId": self.ctx.phone,
                "loginIdPrefix": "+91",
                "password": None,
                "otp": None,
                "otpRequestId": None,
                "remainingAttempts": 5,
                "phoneNumberFormat": "E164",
                "loginType": "MOBILE",
                "verificationType": "TOKEN",
                "sourceContext": "DEFAULT",
                "churned": False,
                "otpRegex": None,
                "data": None,
                "clientQueryParamMap": None,
            }
        }
        data = self._post_json(self._url("/1/action/view"), payload)
        response_ctx = data.get("RESPONSE", {}).get("actionResponseContext", {})
        if not response_ctx.get("authenticationSuccess"):
            raise RuntimeError(f"Session refresh fail: {data}")
        self.log.ok("Session refresh OK")

    def send_otp(self, phone: str) -> str:
        self.log.info("OTP request ho raha hai...")
        payload = {
            "actionRequestContext": {
                "type": "LOGIN_SHOPSY2",
                "loginId": phone,
                "loginIdPrefix": "+91",
                "password": None,
                "otp": None,
                "otpRequestId": None,
                "remainingAttempts": 5,
                "phoneNumberFormat": "E164",
                "loginType": "MOBILE",
                "verificationType": "OTP",
                "sourceContext": "DEFAULT",
                "churned": False,
                "otpRegex": None,
                "data": None,
                "clientQueryParamMap": None,
            }
        }
        data = self._post_json(self._url("/1/action/view"), payload)
        response_ctx = data.get("RESPONSE", {}).get("actionResponseContext", {})
        otp_request_id = response_ctx.get("otpRequestId")
        if not otp_request_id:
            raise RuntimeError(f"OTP request fail: {data}")
        self.log.ok("OTP request OK")
        return otp_request_id

    def verify_otp(self, phone: str, otp: str, otp_request_id: str) -> None:
        self.phone = phone
        self.ctx.phone = phone
        self.log.info("OTP verify ho raha hai...")
        payload = {
            "actionRequestContext": {
                "type": "LOGIN_SHOPSY2",
                "loginId": phone,
                "loginIdPrefix": "+91",
                "password": None,
                "otp": otp.strip(),
                "otpRequestId": otp_request_id,
                "remainingAttempts": 5,
                "phoneNumberFormat": "E164",
                "loginType": "MOBILE",
                "verificationType": "OTP",
                "sourceContext": "DEFAULT",
                "churned": False,
                "otpRegex": None,
                "data": None,
                "clientQueryParamMap": None,
            }
        }
        data = self._post_json(self._url("/1/action/view"), payload)
        response_ctx = data.get("RESPONSE", {}).get("actionResponseContext", {})
        if not response_ctx.get("authenticationSuccess"):
            raise RuntimeError(f"Login fail: {data}")
        self.log.ok(f"Login OK | account={self.ctx.account_id} | name={self.ctx.user_name or 'User'}")

    def games_api(self, route_uri: str, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        body = {"requestMethod": method, "routeUri": route_uri, "payload": payload}
        return self._post_json(self._url("/1/shopsy/games"), body, game=True)

    def get_user(self, *, refresh: bool = False) -> dict[str, Any]:
        if not self.ctx.account_id:
            raise RuntimeError("Account ID missing — pehle login karo")
        if self._user_cache and not refresh:
            return self._user_cache
        data = self.games_api(
            "user/get-user",
            "GET",
            {"userId": self.ctx.account_id, "userName": self.ctx.user_name or "User"},
        )
        if not data.get("success"):
            raise RuntimeError(f"get-user fail: {data}")
        self._user_cache = data["data"]
        return self._user_cache

    def _game_already_done(self, game_id: str, user: dict[str, Any]) -> bool:
        for g in user.get("gameStats", {}).get("games", []):
            if g.get("gameId") == game_id and g.get("rewards", {}).get("isMaxGameBonusEarned"):
                return True
        return False

    def claim_gullak(self, label: str = "Gullak") -> dict[str, Any] | None:
        self.log.info(f"{label} claim (SuperCoin)...")
        data = self.games_api(
            "gullak/claim-gullak",
            "POST",
            {"userId": self.ctx.account_id},
        )
        if not data.get("success"):
            err = data.get("data", {}).get("errorMessage") or data
            self.log.warn(f"{label} skip/fail: {err}")
            return None
        result = data["data"]
        self._user_cache = None
        self.log.ok(
            f"{label} | login={result.get('loginRewardCoinsClaimed', 0)} | "
            f"games={result.get('gameRewardCoinsClaimed', 0)} | "
            f"today={result.get('totalCoinsEarnedToday', 0)}"
        )
        return result

    def claim_welcome_bonus(self) -> None:
        user = self.get_user(refresh=True)
        login = user.get("login", {})
        earnings = user.get("earnings", {})
        self.log.info(
            f"Status | firstVisit={login.get('isFirstVisit')} | "
            f"ftue={login.get('ftueCompleted')} | coins today={earnings.get('coinsEarnedToday', 0)}"
        )
        self.claim_gullak("Welcome/Login bonus")

    def fetch_onboarding_gamification_map(self) -> dict[str, Any] | None:
        payload = {
            "pageUri": ONBOARDING_GAMIFICATION_PAGE_URI,
            "pageContext": {
                "pageHashKey": None,
                "slotContextMap": None,
                "paginationContextMap": None,
                "stateInfoMap": None,
                "slotIdInfoMap": None,
                "paginatedFetch": False,
                "pageNumber": 1,
                "fetchAllPages": False,
                "networkSpeed": 3000,
                "trackingContext": None,
                "fetchSeoData": False,
            },
            "partnerContext": None,
            "locationContext": {"pincode": DEFAULT_PINCODE},
            "requestContext": None,
        }
        data = self._post_json(self._url("/4/page/fetch"), payload, layout=True)
        bu_map = _deep_find(data, "gamificationBUInfoMap")
        if isinstance(bu_map, dict) and bu_map:
            return bu_map
        return None

    def claim_pending_gamification(self) -> dict[str, Any] | None:
        self.log.info("Pending onboarding bonus claim (30 SuperCoins)...")
        bu_map = self.fetch_onboarding_gamification_map() or DEFAULT_GAMIFICATION_BU_MAP
        expected = sum(
            int(entry.get("amount", 0))
            for entry in bu_map.values()
            if isinstance(entry, dict)
        )
        self.log.info(f"Gamification map: {len(bu_map)} categories | expected ~{expected} coins")

        payload = {
            "actionRequestContext": {
                "pageContext": {
                    "pageNumber": 1.0,
                    "gamificationBUInfoMap": bu_map,
                },
                "pageUri": ONBOARDING_GAMIFICATION_PAGE_URI,
                "type": "GAMIFICATION_ALLOCATE_ACTION",
            }
        }
        data = self._post_json(self._url("/1/action/view"), payload)
        response = data.get("RESPONSE") or {}
        if not response.get("actionSuccess"):
            err = response.get("actionResponseContext") or data.get("ERROR_MESSAGE") or data
            self.log.warn(f"Pending bonus skip/fail: {err}")
            return None

        ctx = response.get("actionResponseContext") or {}
        self.log.dbg("Pending bonus response", ctx)
        coins = (
            ctx.get("coinsAllocated")
            or ctx.get("totalCoinsAllocated")
            or ctx.get("totalCoins")
            or expected
        )
        self.log.ok(f"Pending bonus | +{coins} SuperCoins")
        return ctx

    def _play_seconds(self, game: dict[str, Any]) -> int:
        if not self.fast:
            return game["play_time"]
        if game["id"] == "runner-3d":
            return min(game["play_time"], FAST_RUNNER_SEC)
        return min(game["play_time"], FAST_PLAY_SEC)

    def _send_end_game(self, game_id: str, session_id: str, play_time: int, gems: int, request_num: int) -> dict[str, Any]:
        payload = {
            "userId": self.ctx.account_id,
            "gameId": game_id,
            "sessionId": session_id,
            "gemsEarned": gems,
            "playTimeInSec": play_time,
        }
        try:
            result = self.games_api("game/game-ended", "POST", payload)
            if result.get("success"):
                coins = result.get("data", {}).get("coinsEarnedForGame", 0)
                self.log.ok(f"[Parallel #{request_num}] +{coins} coins")
                return result
            else:
                self.log.warn(f"[Parallel #{request_num}] Failed: {result}")
                return result
        except Exception as e:
            self.log.warn(f"[Parallel #{request_num}] Error: {e}")
            return {"success": False, "error": str(e)}

    def play_game(self, game: dict[str, Any], user: dict[str, Any]) -> dict[str, Any] | None:
        game_id = game["id"]
        name = game["name"]
        if self._game_already_done(game_id, user):
            self.log.warn(f"{name}: already done today, skip")
            return None

        play_time = self._play_seconds(game)
        self.log.info(f"{name} start | wait={play_time}s")

        start = self.games_api(
            "game/game-started",
            "POST",
            {"userId": self.ctx.account_id, "gameId": game_id},
        )
        if not start.get("success"):
            raise RuntimeError(f"{name} start fail: {start}")

        session_id = start["data"]["sessionId"]
        earnable = start["data"].get("earnableCoins", 0)
        self.log.info(f"{name} session={session_id} | earnable={earnable}")
        self.log.countdown(name, play_time)

        self.log.info("Refreshing session before parallel requests...")
        self.refresh_session()

        self.parallel_results = []
        total_requests = 50

        self.log.info(f"🚀 Sending {total_requests} parallel end-game requests for {name}...")
        self.log.info("=" * 56)

        shared_ctx = self.ctx.to_dict()
        shared_ctx["user_id"] = self.user_id # Add user_id to shared context

        with concurrent.futures.ThreadPoolExecutor(max_workers=total_requests) as executor:
            futures = []
            for i in range(total_requests):
                future = executor.submit(
                    self._send_end_game_parallel,
                    shared_ctx,
                    game_id,
                    session_id,
                    play_time,
                    game["gems"],
                    i + 1
                )
                futures.append(future)

            for future in concurrent.futures.as_completed(futures):
                try:
                    result = future.result(timeout=10)
                    if result and result.get("success"):
                        self.parallel_results.append(result)
                except Exception as e:
                    self.log.dbg(f"Parallel request error: {e}")

        total_coins = 0
        success_count = len([r for r in self.parallel_results if r.get("success")])

        for result in self.parallel_results:
            if result and result.get("success"):
                data = result.get("data", {})
                coins = data.get("coinsEarnedForGame", 0)
                if coins > 0:
                    total_coins += coins

        self._user_cache = None

        self.log.info("=" * 56)
        if total_coins > 0:
            self.log.ok(f"🎉 {name} TOTAL +{total_coins} coins from {success_count} successful parallel requests")
            self.log.ok(f"📊 Average per request: {total_coins // success_count if success_count > 0 else 0} coins")
        else:
            self.log.warn(f"{name} no coins earned from parallel requests")

        return self.parallel_results[0].get("data") if self.parallel_results and self.parallel_results[0].get("success") else None

    @staticmethod
    def _send_end_game_parallel(ctx_dict: dict, game_id: str, session_id: str, play_time: int, gems: int, request_num: int) -> dict[str, Any]:
        try:
            parallel_client = ShopsyClient(
                log=LiveLog(debug=False),
                fast=True,
                phone=ctx_dict.get("phone", ""),
                user_id=ctx_dict.get("user_id"),
                fresh=True
            )

            parallel_client.ctx.phone = ctx_dict.get("phone", "")
            parallel_client.ctx.device_id = ctx_dict.get("device_id", uuid.uuid4().hex)
            parallel_client.ctx.visit_id = ctx_dict.get("visit_id", f"{uuid.uuid4().hex}-{int(time.time() * 1000)}")
            parallel_client.ctx.dc_id = ctx_dict.get("dc_id", "1")
            parallel_client.ctx.at = ctx_dict.get("at", "")
            parallel_client.ctx.sn = ctx_dict.get("sn", "")
            parallel_client.ctx.vid = ctx_dict.get("vid", "")
            parallel_client.ctx.secure_token = ctx_dict.get("secure_token", "")
            parallel_client.ctx.secure_cookie = ctx_dict.get("secure_cookie", "")
            parallel_client.ctx.account_id = ctx_dict.get("account_id", "")
            parallel_client.ctx.user_name = ctx_dict.get("user_name", "")
            parallel_client.ctx.is_logged_in = ctx_dict.get("is_logged_in", False)

            parallel_client._sync_urls()

            payload = {
                "userId": parallel_client.ctx.account_id,
                "gameId": game_id,
                "sessionId": session_id,
                "gemsEarned": gems,
                "playTimeInSec": play_time,
            }
            result = parallel_client.games_api("game/game-ended", "POST", payload)

            if result.get("success"):
                coins = result.get("data", {}).get("coinsEarnedForGame", 0)
                print(f"[Parallel #{request_num}] ✅ +{coins} coins", flush=True)
                return result
            else:
                print(f"[Parallel #{request_num}] ❌ Failed: {result.get('error', 'Unknown error')}", flush=True)
                return result
        except Exception as e:
            print(f"[Parallel #{request_num}] 💥 Error: {e}", flush=True)
            return {"success": False, "error": str(e)}

    def print_summary(self) -> None:
        user = self.get_user(refresh=True)
        earnings = user.get("earnings", {})
        print("\n" + "=" * 56)
        print(f"  SUPERCOIN SUMMARY  [{self.log._elapsed()}]")
        print("=" * 56)
        print(f"  User          : {user.get("basic", {}).get("userName", "N/A")}")
        print(f"  DC            : {self.ctx.dc_id}")
        print(f"  Total coins   : {earnings.get("coinsEarnedTotal", 0)}")
        print(f"  Today         : {earnings.get("coinsEarnedToday", 0)}")
        print(f"  Max bonus?    : {earnings.get("isMaxGameBonusEarnedToday", False)}")
        print("\n  Games:")
        for g in user.get("gameStats", {}).get("games", []):
            r = g.get("rewards", {})
            st = "DONE" if r.get("isMaxGameBonusEarned") else "pending"
            print(f"    {g.get("gameId", "?"):15} today={r.get("coinsEarnedToday", 0):>3} {st}")
        print("=" * 56)


# --- Game Logic ---
async def run_shopsy_game(user_id, animator):
    session_data = get_user_session(user_id)
    if not session_data:
        return False, "No active session. Please login again."

    await animator.update("🎮 Starting Game...")
    await asyncio.sleep(1)
    
    log = LiveLog(debug=False)
    client = ShopsyClient(log=log, phone=session_data.get("phone"), user_id=user_id)
    for k, v in session_data.items():
        setattr(client.ctx, k, v)
    
    try:
        await animator.update("⚡ Connecting...")
        user = client.get_user(refresh=True)
        
        await animator.update("🎯 Playing Game...")
        game = GAMES[0] 
        result = client.play_game(game, user)
        
        if result:
            await animator.update("🎁 Claiming Rewards...")
            client.claim_gullak()
            
            # Fetch final user stats for supercoin balance
            final_user = client.get_user(refresh=True)
            
            await animator.update("🔒 Logging Out...")
            delete_user_session(user_id)
            
            await animator.update("💰 Updating Points...")
            update_user_points(user_id, -GAME_COST_POINTS)
            
            conn = get_db()
            conn.execute("UPDATE users SET games_played = games_played + 1 WHERE user_id = ?", (user_id,))
            conn.commit()
            conn.close()
            
            log_action(user_id, "GAME_COMPLETED", f"Game: {game['name']}")
            
            await animator.update("✅ Completed Successfully")
            return True, final_user
        else:
            return False, "Game failed or already done today."
            
    except Exception as e:
        return False, str(e)


# --- Telegram Bot Handlers ---
bot = telebot.TeleBot(BOT_TOKEN)
user_login_state = {}

@bot.message_handler(commands=['start'])
def start(message):
    user_id = message.from_user.id
    username = message.from_user.username or message.from_user.first_name
    
    args = message.text.split()
    if len(args) > 1 and args[1].startswith('ref_'):
        referrer_id = int(args[1].replace('ref_', ''))
        add_referral(referrer_id, user_id)

    create_user(user_id, username)
    user = get_user(user_id)
    
    if not user['is_verified']:
        bot.send_message(
            message.chat.id,
            "📢 You must join our channel to use this bot.",
            reply_markup=force_join_keyboard(f"https://t.me/{FORCE_JOIN_CHANNEL.replace('@', '')}")
        )
    else:
        show_dashboard(message)

@bot.callback_query_handler(func=lambda call: call.data == "check_join")
def check_join(call):
    user_id = call.from_user.id
    try:
        status = bot.get_chat_member(FORCE_JOIN_CHANNEL, user_id).status
        if status in ['member', 'administrator', 'creator']:
            user = get_user(user_id)
            if not user['is_verified']:
                set_verified(user_id)
                update_user_points(user_id, JOIN_REWARD_POINTS)
                
                referrer_id = process_referral_reward(user_id)
                if referrer_id:
                    try:
                        bot.send_message(referrer_id, f"🎉 Referral Successful!\n➕ You earned {REFERRAL_REWARD_POINTS} Points.")
                    except: pass

                bot.answer_callback_query(call.id, "✅ Verified!")
                bot.edit_message_text(
                    f"✅ Channel Joined Successfully\n\n🎉 You received +{JOIN_REWARD_POINTS} Point.\n💰 Current Balance: {user['points'] + JOIN_REWARD_POINTS} Point",
                    call.message.chat.id,
                    call.message.message_id
                )
                show_dashboard(call.message)
            else:
                bot.answer_callback_query(call.id, "Already verified!")
        else:
            bot.answer_callback_query(call.id, "❌ You haven't joined yet!", show_alert=True)
    except Exception as e:
        bot.answer_callback_query(call.id, f"Error: {str(e)}")

def show_dashboard(message):
    user_id = message.from_user.id if hasattr(message.from_user, 'id') else message.chat.id
    user = get_user(user_id)
    session = get_user_session(user_id)
    is_logged_in = session is not None
    
    text = (
        f"🏠 Welcome, {user['username']}\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"💰 Current Points : {user['points']}\n"
        f"🎮 Games Played : {user['games_played']}\n"
        f"🎁 Referral Count : {user['referral_count']}\n"
        f"━━━━━━━━━━━━━━━━"
    )
    
    markup = user_dashboard_keyboard(is_logged_in)
    bot.send_message(message.chat.id, text, reply_markup=markup)
    
    if is_admin(user_id):
        bot.send_message(message.chat.id, "🛡️ Admin Access Detected. Use the buttons below for admin tasks, or the menu above to play.", reply_markup=admin_dashboard_keyboard())

@bot.message_handler(func=lambda m: m.text == "👤 Profile")
def profile(message):
    show_dashboard(message)

@bot.message_handler(func=lambda m: m.text == "💰 My Points")
def my_points(message):
    user = get_user(message.from_user.id)
    bot.send_message(message.chat.id, f"💰 Your Current Balance: {user['points']} Points")

@bot.message_handler(func=lambda m: m.text == "🎁 Refer")
def refer(message):
    user_id = message.from_user.id
    bot_info = bot.get_me()
    ref_link = generate_referral_link(bot_info.username, user_id)
    bot.send_message(message.chat.id, f"🎁 Share your referral link to earn points!\n\n🔗 {ref_link}\n\nReward: {REFERRAL_REWARD_POINTS} Points per verified referral.")

@bot.message_handler(func=lambda m: m.text == "🔐 Login")
def login_start(message):
    markup = user_dashboard_keyboard(is_login_process=True)
    msg = bot.send_message(message.chat.id, "📱 Please enter your Shopsy Mobile Number (10 digits):", reply_markup=markup)
    bot.register_next_step_handler(msg, process_phone)

def process_phone(message):
    if message.text == "❌ Cancel Login":
        bot.send_message(message.chat.id, "🚫 Login cancelled.")
        show_dashboard(message)
        return

    phone = message.text.strip()
    if len(phone) != 10 or not phone.isdigit():
        bot.send_message(message.chat.id, "❌ Invalid phone number. Please enter 10 digits.")
        # Re-register if invalid
        msg = bot.send_message(message.chat.id, "📱 Please enter your Shopsy Mobile Number (10 digits):")
        bot.register_next_step_handler(msg, process_phone)
        return

    log = LiveLog(debug=False)
    client = ShopsyClient(log=log, phone=phone, user_id=message.from_user.id)
    try:
        otp_req_id = client.send_otp(phone)
        user_login_state[message.from_user.id] = {
            'phone': phone,
            'otp_request_id': otp_req_id,
            'client': client
        }
        msg = bot.send_message(message.chat.id, f"📩 OTP sent to +91{phone}. Please enter the OTP:")
        bot.register_next_step_handler(msg, process_otp)
    except Exception as e:
        bot.send_message(message.chat.id, f"❌ Error: {str(e)}")
        show_dashboard(message)

def process_otp(message):
    user_id = message.from_user.id
    if message.text == "❌ Cancel Login":
        if user_id in user_login_state:
            del user_login_state[user_id]
        bot.send_message(message.chat.id, "🚫 Login cancelled.")
        show_dashboard(message)
        return

    if user_id not in user_login_state:
        bot.send_message(message.chat.id, "❌ Session expired. Please try login again.")
        show_dashboard(message)
        return

    state = user_login_state[user_id]
    otp = message.text.strip()
    client = state['client']
    
    try:
        client.verify_otp(state['phone'], otp, state['otp_request_id'])
        save_user_session(user_id, state['phone'], client.ctx.to_dict())
        del user_login_state[user_id]
        
        bot.send_message(message.chat.id, "✅ Login Successful! You can now start the game.")
        show_dashboard(message)
    except Exception as e:
        bot.send_message(message.chat.id, f"❌ OTP Verification Failed: {str(e)}")
        # Re-register if invalid
        msg = bot.send_message(message.chat.id, "📩 Please enter the correct OTP:")
        bot.register_next_step_handler(msg, process_otp)

@bot.message_handler(func=lambda m: m.text == "🎮 Start Game")
def start_game_handler(message):
    user_id = message.from_user.id
    user = get_user(user_id)
    
    if user['points'] < GAME_COST_POINTS:
        bot.send_message(message.chat.id, f"❌ Insufficient Points\nYou need at least {GAME_COST_POINTS} Point to play.")
        return

    session = get_user_session(user_id)
    if not session:
        bot.send_message(message.chat.id, "🔐 Please login first.")
        return

    msg = bot.send_message(message.chat.id, "🎮 Initializing Game...")
    
    async def run_game_async():
        animator = GameAnimator(bot, message.chat.id, msg.message_id)
        success, error = await run_shopsy_game(user_id, animator)
        
        if success:
            updated_user = get_user(user_id)
            # Try to get supercoin balance if success info is returned
            sc_info = ""
            if isinstance(error, dict): # We can pass back the user data in 'error' variable if success
                earnings = error.get('earnings', {})
                sc_info = (
                    f"━━━━━━━━━━━━━━━━\n"
                    f"🪙 Total SuperCoins: {earnings.get('coinsEarnedTotal', 0)}\n"
                    f"📅 Coins Today: {earnings.get('coinsEarnedToday', 0)}\n"
                    f"━━━━━━━━━━━━━━━━\n"
                )
            
            completion_text = (
                "✅ Game Completed Successfully!\n\n"
                f"{sc_info}"
                "🎁 Rewards Claimed Successfully\n"
                "🔒 Account Logged Out Successfully\n"
                f"➖ Points Deducted: {GAME_COST_POINTS}\n"
                f"💰 Bot Balance: {updated_user['points']}\n"
            )
            bot.send_message(message.chat.id, completion_text)
            show_dashboard(message)
        else:
            bot.edit_message_text(f"❌ Game Failed: {error}", message.chat.id, msg.message_id)

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
    loop.run_in_executor(None, lambda: asyncio.run(run_game_async()))

@bot.message_handler(func=lambda m: m.text == "🚪 Logout")
def logout_handler(message):
    delete_user_session(message.from_user.id)
    bot.send_message(message.chat.id, "🚪 Logged out successfully.")
    show_dashboard(message)

# Admin Handlers
@bot.message_handler(func=lambda m: m.text == "📊 Statistics" and is_admin(m.from_user.id))
def admin_stats(message):
    stats = get_stats()
    text = (
        f"📊 Bot Statistics\n"
        f"━━━━━━━━━━━━━━━━\n"
        f"👥 Total Users: {stats['total_users']}\n"
        f"🟢 Active Sessions: {stats['active_sessions']}\n"
        f"🎮 Games Played: {stats['total_games']}\n"
        f"💰 Points Distributed: {stats['total_points_dist']}\n"
        f"🎁 Total Referrals: {stats['total_referrals']}\n"
    )
    bot.send_message(message.chat.id, text)

@bot.message_handler(func=lambda m: m.text == "👥 Total Users" and is_admin(m.from_user.id))
def total_users_admin(message):
    stats = get_stats()
    bot.send_message(message.chat.id, f"👥 Total Users: {stats['total_users']}")

@bot.message_handler(func=lambda m: m.text == "🟢 Active Users" and is_admin(m.from_user.id))
def active_users_admin(message):
    stats = get_stats()
    bot.send_message(message.chat.id, f"🟢 Active Sessions: {stats['active_sessions']}")

@bot.message_handler(func=lambda m: m.text == "💰 Grant Points" and is_admin(m.from_user.id))
def grant_points_start(message):
    msg = bot.send_message(message.chat.id, "Enter User ID and points to grant (e.g., 12345 10):")
    bot.register_next_step_handler(msg, grant_points_process)

def grant_points_process(message):
    try:
        user_id, points = map(int, message.text.split())
        update_user_points(user_id, points)
        log_action(message.from_user.id, "ADMIN_GRANT_POINTS", f"Granted {points} to user {user_id}")
        bot.send_message(message.chat.id, f"✅ Granted {points} points to user {user_id}.")
    except Exception as e:
        bot.send_message(message.chat.id, f"❌ Error: {e}")

@bot.message_handler(func=lambda m: m.text == "➖ Deduct Points" and is_admin(m.from_user.id))
def deduct_points_start(message):
    msg = bot.send_message(message.chat.id, "Enter User ID and points to deduct (e.g., 12345 5):")
    bot.register_next_step_handler(msg, deduct_points_process)

def deduct_points_process(message):
    try:
        user_id, points = map(int, message.text.split())
        update_user_points(user_id, -points)
        log_action(message.from_user.id, "ADMIN_DEDUCT_POINTS", f"Deducted {points} from user {user_id}")
        bot.send_message(message.chat.id, f"✅ Deducted {points} points from user {user_id}.")
    except Exception as e:
        bot.send_message(message.chat.id, f"❌ Error: {e}")

@bot.message_handler(func=lambda m: m.text == "📢 Broadcast" and is_admin(m.from_user.id))
def broadcast_start(message):
    msg = bot.send_message(message.chat.id, "Enter the message to broadcast to all users:")
    bot.register_next_step_handler(msg, broadcast_process)

def broadcast_process(message):
    try:
        count = broadcast_message(bot, message.text)
        log_action(message.from_user.id, "ADMIN_BROADCAST", f"Broadcasted to {count} users")
        bot.send_message(message.chat.id, f"✅ Message broadcasted to {count} users.")
    except Exception as e:
        bot.send_message(message.chat.id, f"❌ Error: {e}")

@bot.message_handler(func=lambda m: m.text == "🔍 User Details" and is_admin(m.from_user.id))
def user_details_start(message):
    msg = bot.send_message(message.chat.id, "Enter User ID to get details:")
    bot.register_next_step_handler(msg, user_details_process)

def user_details_process(message):
    try:
        user_id = int(message.text.strip())
        user = get_user(user_id)
        if user:
            text = (
                f"👤 User Details for ID: {user_id}\n"
                f"Username: {user['username']}\n"
                f"Points: {user['points']}\n"
                f"Games Played: {user['games_played']}\n"
                f"Referral Count: {user['referral_count']}\n"
                f"Verified: {'Yes' if user['is_verified'] else 'No'}\n"
                f"Joined At: {user['joined_at']}"
            )
            bot.send_message(message.chat.id, text)
        else:
            bot.send_message(message.chat.id, "❌ User not found.")
    except Exception as e:
        bot.send_message(message.chat.id, f"❌ Error: {e}")

@bot.message_handler(func=lambda m: m.text == "📜 User History" and is_admin(m.from_user.id))
def user_history_start(message):
    msg = bot.send_message(message.chat.id, "Enter User ID to get history:")
    bot.register_next_step_handler(msg, user_history_process)

def user_history_process(message):
    try:
        user_id = int(message.text.strip())
        conn = get_db()
        cursor = conn.cursor()
        cursor.execute("SELECT action, details, created_at FROM history WHERE user_id = ? ORDER BY created_at DESC LIMIT 10", (user_id,))
        history = cursor.fetchall()
        conn.close()

        if history:
            text = f"📜 Last 10 actions for User ID: {user_id}\n━━━━━━━━━━━━━━━━\n"
            for item in history:
                text += f"- {item['action']} ({item['created_at']}): {item['details']}\n"
            bot.send_message(message.chat.id, text)
        else:
            bot.send_message(message.chat.id, "No history found for this user.")
    except Exception as e:
        bot.send_message(message.chat.id, f"❌ Error: {e}")


# --- Main Execution ---
if __name__ == "__main__":
    print("Initializing Database...")
    init_db()
    print("Bot is starting...")
    bot.infinity_polling()
