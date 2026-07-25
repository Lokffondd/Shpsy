#!/usr/bin/env python3
"""
Shopsy SuperCoin Auto Bot with Telegram Interface
Combined Single File Edition
"""

import logging
import asyncio
import traceback
import sys
import json
import time
import uuid
import argparse
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import requests
from telegram import Update, ReplyKeyboardMarkup, ReplyKeyboardRemove
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
    ConversationHandler,
)

# --- CONFIGURATION ---
TELEGRAM_BOT_TOKEN = "8715344690:AAHQUX6eSkmHzOE7gmpHSy0X0IJ157lIoAs"
ADMIN_IDS = [8183677305, 8739344756]

# --- SHOPSY API LOGIC ---
ROME_TEMPLATE = "https://{dc}.rome.api.flipkart.net"
def get_session_file(user_id: str | int = "default") -> Path:
    return Path(__file__).parent / f".shopsy_session_{user_id}.json"

APP_VERSION = "2291175"
DEVICE_MODEL = "Pixel 9a"
DEVICE_BRAND = "Google"
DEFAULT_PINCODE = "226001"
FAST_PLAY_SEC = 18
# New headers for better compatibility
X_APP_VERSION = "2291175"
X_APP_BUILD = "2291175"

GAMES = [
    {"id": "runner-3d", "name": "Super Runner", "play_time": 94, "gems": 200},
    {"id": "city-builder", "name": "City Builder", "play_time": 47, "gems": 100},
    {"id": "match-3", "name": "Fruit Crush", "play_time": 35, "gems": 100},
    {"id": "goods-triple", "name": "Grocery Match", "play_time": 40, "gems": 100},
    {"id": "ludo", "name": "Ludo", "play_time": 50, "gems": 100},
    {"id": "nazaria", "name": "Nazar Pop", "play_time": 45, "gems": 100},
]

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
        for remaining in range(seconds, 0, -1):
            print(f"\r[{self._ts()} | {self._elapsed()}] {label} ... {remaining}s ", end="", flush=True)
            time.sleep(1)
        print(f"\r[{self._ts()} | {self._elapsed()}] {label} ... done!          ", flush=True)

@dataclass
class ShopsySession:
    user_id: str | int = "default"
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

    def save(self) -> None:
        get_session_file(self.user_id).write_text(
            json.dumps(
                {
                    "device_id": self.device_id,
                    "visit_id": self.visit_id,
                    "dc_id": self.dc_id,
                    "at": self.at,
                    "sn": self.sn,
                    "vid": self.vid,
                    "secure_token": self.secure_token,
                    "secure_cookie": self.secure_cookie,
                    "account_id": self.account_id,
                    "user_name": self.user_name,
                    "is_logged_in": self.is_logged_in,
                },
                indent=2,
            ),
            encoding="utf-8",
        )

    @classmethod
    def load(cls, user_id: str | int = "default") -> 'ShopsySession' or None:
        session_file = get_session_file(user_id)
        if not session_file.exists():
            return None
        try:
            data = json.loads(session_file.read_text(encoding="utf-8"))
            return cls(user_id=user_id, **{k: data[k] for k in cls.__dataclass_fields__ if k in data and k != "user_id"})
        except (json.JSONDecodeError, KeyError, TypeError):
            return None

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
    def __init__(self, log: LiveLog, fast: bool = True, user_id: str | int = "default") -> None:
        self.log = log
        self.fast = fast
        self.user_id = user_id
        self.session = requests.Session()
        saved = ShopsySession.load(user_id)
        self.ctx = saved or ShopsySession(user_id=user_id)
        self._user_cache: dict[str, Any] | None = None
        self._sync_urls()

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
        self.ctx.save()
        dc_name = (self._last_dc_meta or {}).get("dc", "?")
        self.log.info(f"DC Change: {old} -> {dc_id} ({dc_name}) | host={self.base_url}")
        self._last_dc_meta = None

    _last_dc_meta: dict[str, Any] | None = None

    def _partner_headers(self, *, layout: bool = False) -> dict[str, str]:
        headers = {
            "User-Agent": "okhttp/4.9.2",
            "Content-Type": "application/json; charset=UTF-8",
            "Accept-Encoding": "gzip",
            "X-PARTNER-CONTEXT": '{"source":"reseller"}',
            "FK-TENANT-ID": "SHOPSY",
            "business": "reseller",
            "X-User-Agent": self.x_user_agent,
            "X-Visit-Id": self.ctx.visit_id,
            "X-NewRelic-ID": "VwEHU1dSCxABUVlaAAQHU1UA",
            "X-App-Version": X_APP_VERSION,
            "X-App-Build": X_APP_BUILD,
        }
        if layout:
            headers["X-Layout-Version"] = '{"appVersion":"' + X_APP_VERSION + '","frameworkVersion":"1.0"}'
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
        self.ctx.save()

    def _capture_secure_cookie(self, response: requests.Response) -> None:
        secure_cookie = response.headers.get("securecookie") or response.headers.get("secureCookie")
        if secure_cookie:
            self.ctx.secure_cookie = secure_cookie

    def _post_json(self, url: str, payload: dict[str, Any], *, game: bool = False, layout: bool = False) -> dict[str, Any]:
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
            if response.status_code >= 400 or (data.get("STATUS_CODE") or 200) >= 400:
                err = data.get("ERROR_MESSAGE") or data
                last_error = f"HTTP {response.status_code}: {err}"
                raise RuntimeError(f"HTTP {response.status_code} from {active_url}: {data}")
            return data
        raise RuntimeError(f"DC retry limit hit ({last_error})")

    def bootstrap(self) -> None:
        self.log.info(f"Session bootstrap (DC {self.ctx.dc_id})...")
        payload = {
            "pageUri": "/shopsy2-login-page-store",
            "pageContext": {
                "pageHashKey": None, "slotContextMap": None, "paginationContextMap": None,
                "stateInfoMap": None, "slotIdInfoMap": None, "paginatedFetch": False,
                "pageNumber": 1, "fetchAllPages": False, "networkSpeed": 3000,
                "trackingContext": None, "fetchSeoData": False,
            },
            "partnerContext": None,
            "locationContext": {"pincode": DEFAULT_PINCODE},
            "requestContext": None,
        }
        data = self._post_json(self._url("/4/page/fetch"), payload, layout=True)
        self.ctx.save()
        has_cookie = "yes" if self.ctx.secure_cookie else "no"
        self.log.ok(f"Bootstrap OK | dc={self.ctx.dc_id} | vid={self.ctx.vid or 'N/A'} | cookie={has_cookie}")

    def send_otp(self, phone: str) -> str:
        phone = phone.strip().replace("+91", "").replace(" ", "")
        self.log.info(f"OTP bheja ja raha hai: +91{phone}")
        payload = {
            "actionRequestContext": {
                "type": "LOGIN_IDENTITY_VERIFY_SHOPSY2",
                "loginId": phone, "loginIdPrefix": "+91", "phoneNumberFormat": "E164",
                "addAppHash": True, "loginType": "MOBILE", "verificationType": "OTP",
                "sourceContext": "DEFAULT", "clientQueryParamMap": None,
            }
        }
        data = self._post_json(self._url("/1/action/view"), payload)
        response_ctx = data.get("RESPONSE", {}).get("actionResponseContext", {})
        if not data.get("RESPONSE", {}).get("actionSuccess"):
            raise RuntimeError(f"OTP send fail: {data}")
        request_id = response_ctx.get("requestId")
        if not request_id:
            attempts = response_ctx.get("remainingAttempts")
            ctx_type = response_ctx.get("type", "")
            if attempts == 0 or ctx_type == "LOGIN_VERIFY":
                raise RuntimeError("OTP nahi bheja — galat number ya 24h limit. Sahi 10-digit number try karo.")
            raise RuntimeError(f"OTP requestId nahi mila: {response_ctx}")
        self.log.ok(f"OTP bhej diya | requestId={request_id[:12]}...")
        return request_id

    def verify_otp(self, phone: str, otp: str, otp_request_id: str) -> None:
        phone = phone.strip().replace("+91", "").replace(" ", "")
        self.log.info("OTP verify ho raha hai...")
        payload = {
            "actionRequestContext": {
                "type": "LOGIN_SHOPSY2", "loginId": phone, "loginIdPrefix": "+91",
                "password": None, "otp": otp.strip(), "otpRequestId": otp_request_id,
                "remainingAttempts": 5, "phoneNumberFormat": "E164", "loginType": "MOBILE",
                "verificationType": "OTP", "sourceContext": "DEFAULT", "churned": False,
                "otpRegex": None, "data": None, "clientQueryParamMap": None,
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
        data = self.games_api("user/get-user", "GET", {})
        self._user_cache = data.get("RESPONSE") or {}
        return self._user_cache

    def claim_welcome_bonus(self) -> None:
        self.log.info("Welcome bonus check...")
        try:
            res = self.games_api("user/claim-welcome-bonus", "POST", {})
            coins = res.get("RESPONSE", {}).get("totalCoinsEarned", 0)
            if coins > 0:
                self.log.ok(f"Welcome bonus claimed: +{coins}")
            else:
                self.log.info("Welcome bonus already claimed or not available")
        except RuntimeError as exc:
            self.log.warn(f"Welcome bonus skip: {exc}")

    def _game_already_done(self, game_id: str, user_data: dict[str, Any]) -> bool:
        games = user_data.get("gameStats", {}).get("games", [])
        for g in games:
            if g.get("gameId") == game_id:
                return bool(g.get("rewards", {}).get("isMaxGameBonusEarned"))
        return False

    def play_game(self, game: dict[str, Any], user_data: dict[str, Any]) -> dict[str, Any]:
        game_id = game["id"]
        if self._game_already_done(game_id, user_data):
            self.log.info(f"{game['name']} already completed today")
            return {}
        self.log.info(f"Playing {game['name']}...")
        play_time = FAST_PLAY_SEC if self.fast else game["play_time"]
        self.log.countdown(f"Waiting for {game['name']} rewards", play_time)
        payload = {"gameId": game_id, "playTime": play_time, "gems": game["gems"]}
        res = self.games_api("user/update-game-score", "POST", payload)
        earned = res.get("RESPONSE", {}).get("totalCoinsEarned", 0)
        self.log.ok(f"{game['name']} done! Earned: +{earned}")
        return res

    def claim_gullak(self, label: str = "Gullak") -> None:
        self.log.info(f"Claiming {label}...")
        try:
            res = self.games_api("user/claim-gullak-rewards", "POST", {})
            coins = res.get("RESPONSE", {}).get("totalCoinsEarned", 0)
            self.log.ok(f"{label} claimed: +{coins}")
        except RuntimeError as exc:
            self.log.warn(f"{label} claim fail: {exc}")

    def print_summary(self) -> None:
        user = self.get_user(refresh=True)
        earnings = user.get("earnings", {})
        print("\n" + "=" * 56)
        print("  SHOPSY REWARDS SUMMARY")
        print("-" * 56)
        print(f"  User          : {user.get('basic', {}).get('userName', 'N/A')}")
        print(f"  DC            : {self.ctx.dc_id}")
        print(f"  Total coins   : {earnings.get('coinsEarnedTotal', 0)}")
        print(f"  Today         : {earnings.get('coinsEarnedToday', 0)}")
        print(f"  Max bonus?    : {earnings.get('isMaxGameBonusEarnedToday', False)}")
        print("\n  Games:")
        for g in user.get("gameStats", {}).get("games", []):
            r = g.get("rewards", {})
            st = "DONE" if r.get("isMaxGameBonusEarned") else "pending"
            print(f"    {g.get('gameId', '?'):15} today={r.get('coinsEarnedToday', 0):>3} {st}")
        print("=" * 56)

# --- TELEGRAM BOT INTERFACE ---

# Enable logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Conversation states
PHONE, OTP = range(2)

class TelegramLiveLog(LiveLog):
    def __init__(self, update: Update, context: ContextTypes.DEFAULT_TYPE, debug: bool = False):
        super().__init__(debug=debug)
        self.update = update
        self.context = context
        self.user_id = update.effective_user.id
        self.last_message = None

    async def _send_or_edit(self, msg: str):
        try:
            await self.context.bot.send_message(chat_id=self.user_id, text=msg, parse_mode='Markdown')
        except Exception as e:
            logger.error(f"Error sending message: {e}")

    def info(self, msg: str):
        super().info(msg)
        asyncio.run_coroutine_threadsafe(self._send_or_edit(f"ℹ️ {msg}"), self.context.application.loop)

    def ok(self, msg: str):
        super().ok(msg)
        asyncio.run_coroutine_threadsafe(self._send_or_edit(f"✅ {msg}"), self.context.application.loop)

    def warn(self, msg: str):
        super().warn(msg)
        asyncio.run_coroutine_threadsafe(self._send_or_edit(f"⚠️ {msg}"), self.context.application.loop)

    def dbg(self, msg: str, data=None):
        super().dbg(msg, data)
        if self.debug:
            asyncio.run_coroutine_threadsafe(self._send_or_edit(f"🔍 [DEBUG] {msg}"), self.context.application.loop)

    def countdown(self, label: str, seconds: int):
        super().countdown(label, seconds)
        asyncio.run_coroutine_threadsafe(
            self.context.bot.send_message(chat_id=self.user_id, text=f"⏳ {label} ({seconds}s)..."),
            self.context.application.loop
        )

def admin_only(func):
    async def wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        user_id = update.effective_user.id
        if user_id not in ADMIN_IDS:
            await update.message.reply_text("❌ You are not authorized to use this bot.")
            return
        return await func(update, context, *args, **kwargs)
    return wrapper

def get_main_keyboard():
    keyboard = [
        ["🚀 Start Rewards", "📊 Account Status"],
        ["🪙 SuperCoin Summary", "🔄 Login Again"],
        ["🗑 Logout", "⚙️ Settings"],
        ["ℹ️ Help"]
    ]
    return ReplyKeyboardMarkup(keyboard, resize_keyboard=True)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in ADMIN_IDS:
        await update.message.reply_text("❌ You are not authorized to use this bot.")
        return
    
    # UI from screenshot
    user_name = update.effective_user.first_name or "User"
    welcome_msg = (
        f"🏠 Welcome, 🕷 {user_name} ⚡\n"
        "━━━━━━━━━━━━━━━━━━━━\n\n"
        "💰 Current Points : 1\n"
        "🎮 Games Played : 0\n"
        "🎁 Referral Count : 0\n"
        "━━━━━━━━━━━━━━━━━━━━"
    )
    
    # Try to get real stats if logged in
    log = TelegramLiveLog(update, context)
    client = ShopsyClient(log=log, user_id=user_id)
    if client.ctx.is_logged_in:
        try:
            user = await asyncio.to_thread(client.get_user, refresh=True)
            points = user.get("totalCoins", 0)
            games_played = len(user.get("gameStats", {}).get("games", []))
            welcome_msg = (
                f"🏠 Welcome, 🕷 {user_name} ⚡\n"
                "━━━━━━━━━━━━━━━━━━━━\n\n"
                f"💰 Current Points : {points}\n"
                f"🎮 Games Played : {games_played}\n"
                "🎁 Referral Count : 0\n"
                "━━━━━━━━━━━━━━━━━━━━"
            )
        except:
            pass

    await update.message.reply_text(
        welcome_msg,
        reply_markup=get_main_keyboard()
    )

@admin_only
async def login_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "📱 Please enter your Shopsy Mobile Number (10 digits):", 
        reply_markup=ReplyKeyboardRemove()
    )
    return PHONE

async def handle_phone(update: Update, context: ContextTypes.DEFAULT_TYPE):
    phone = update.message.text.strip().replace("+91", "").replace(" ", "")
    if len(phone) != 10 or not phone.isdigit():
        await update.message.reply_text("❌ Invalid number. Please enter a 10-digit mobile number:")
        return PHONE
    context.user_data['phone'] = phone
    log = TelegramLiveLog(update, context)
    client = ShopsyClient(log=log, user_id=update.effective_user.id)
    try:
        # Improved bootstrap with retry
        for i in range(2):
            try:
                await asyncio.to_thread(client.bootstrap)
                break
            except Exception as be:
                if i == 1: raise be
                await asyncio.sleep(1)
        
        request_id = await asyncio.to_thread(client.send_otp, phone)
        context.user_data['otp_request_id'] = request_id
        await update.message.reply_text("📩 OTP sent! Please enter the 6-digit OTP:")
        return OTP
    except Exception as e:
        # Detailed error for user
        err_msg = str(e)
        if "500" in err_msg:
            await update.message.reply_text(
                "❌ Error: Server error (500). This usually means Shopsy is blocking the request or the app version is outdated. Please try again later or check the logs.",
                reply_markup=get_main_keyboard()
            )
        else:
            await update.message.reply_text(f"❌ Error: {e}", reply_markup=get_main_keyboard())
        await send_error_to_admin(context, e)
        return ConversationHandler.END

async def handle_otp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    otp = update.message.text.strip()
    if len(otp) != 6 or not otp.isdigit():
        await update.message.reply_text("❌ Invalid OTP. Please enter a 6-digit OTP:")
        return OTP
    phone = context.user_data.get('phone')
    request_id = context.user_data.get('otp_request_id')
    log = TelegramLiveLog(update, context)
    client = ShopsyClient(log=log, user_id=update.effective_user.id)
    try:
        await asyncio.to_thread(client.verify_otp, phone, otp, request_id)
        await update.message.reply_text(f"✅ Login Successful!\nWelcome, {client.ctx.user_name or 'User'}.", reply_markup=get_main_keyboard())
        return ConversationHandler.END
    except Exception as e:
        await update.message.reply_text(f"❌ Login failed: {e}")
        await send_error_to_admin(context, e)
        return ConversationHandler.END

@admin_only
async def logout(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    session_file = ShopsySession.load(user_id)
    if session_file:
        import os
        try:
            os.remove(get_session_file(user_id))
            await update.message.reply_text("🗑 Logout successful. Session cleared.", reply_markup=get_main_keyboard())
        except Exception as e:
            await update.message.reply_text(f"❌ Error during logout: {e}")
    else:
        await update.message.reply_text("ℹ️ No active session found.")

@admin_only
async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    log = TelegramLiveLog(update, context)
    client = ShopsyClient(log=log, user_id=user_id)
    if not client.ctx.is_logged_in:
        await update.message.reply_text("❌ Not logged in. Please login first.")
        return
    try:
        user = await asyncio.to_thread(client.get_user, refresh=True)
        name = client.ctx.user_name or "User"
        coins = user.get("totalCoins", 0)
        await update.message.reply_text(f"📊 *Account Status*\n\n👤 Name: {name}\n🪙 Total SuperCoins: {coins}", parse_mode='Markdown')
    except Exception as e:
        await update.message.reply_text(f"❌ Error fetching status: {e}")
        await send_error_to_admin(context, e)

@admin_only
async def start_rewards(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    log = TelegramLiveLog(update, context)
    client = ShopsyClient(log=log, user_id=user_id)
    if not client.ctx.is_logged_in:
        await update.message.reply_text("❌ Not logged in. Please login first.")
        return
    progress_msg = await update.message.reply_text("🚀 Starting rewards claiming process...")
    try:
        await progress_msg.edit_text("🎁 Claiming Welcome Bonus...")
        await asyncio.to_thread(client.claim_welcome_bonus)
        user = await asyncio.to_thread(client.get_user, refresh=True)
        pending_games = [g for g in GAMES if not client._game_already_done(g["id"], user)]
        await progress_msg.edit_text(f"🎮 Games pending: {len(pending_games)}/{len(GAMES)}")
        total_games = len(GAMES)
        for i, game in enumerate(GAMES, 1):
            if client._game_already_done(game["id"], user):
                continue
            percent = int((i / total_games) * 100)
            bar = "█" * (i * 10 // total_games) + "░" * (10 - (i * 10 // total_games))
            status_text = f"🎮 Playing *{game['name']}*... ({i}/{total_games})\n{bar} {percent}%\n"
            await progress_msg.edit_text(status_text, parse_mode='Markdown')
            try:
                res = await asyncio.to_thread(client.play_game, game, user)
                earned = res.get("RESPONSE", {}).get("totalCoinsEarned", 0)
                await progress_msg.edit_text(f"{status_text}\n🪙 Coins Earned: +{earned}", parse_mode='Markdown')
            except Exception as ge:
                await update.message.reply_text(f"⚠️ {game['name']} error: {ge}")
            await asyncio.sleep(0.5)
        await progress_msg.edit_text("🐷 Claiming Gullak...")
        await asyncio.to_thread(client.claim_gullak, "Final SuperCoin")
        user_final = await asyncio.to_thread(client.get_user, refresh=True)
        summary = (
            "🎉 *Rewards Completed Successfully!*\n\n"
            f"🪙 Total SuperCoins: {user_final.get('totalCoins', 0)}\n\n"
            "Check your SuperCoins in your Flipkart Account.\nThank you for using the bot."
        )
        await progress_msg.edit_text(summary, parse_mode='Markdown')
    except Exception as e:
        await update.message.reply_text(f"❌ Error during rewards: {e}")
        await send_error_to_admin(context, e)

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    help_text = (
        "📖 *Help & Commands*\n\n/start - Start the bot\n/login - Login with mobile & OTP\n"
        "/rewards - Start claiming rewards\n/status - Check account status\n/logout - Clear saved session\n\n"
        "Use the keyboard buttons for easy access!"
    )
    await update.message.reply_text(help_text, parse_mode='Markdown')

async def send_error_to_admin(context: ContextTypes.DEFAULT_TYPE, error: Exception):
    tb = traceback.format_exc()
    error_msg = f"❌ *Error Occurred*\n\n```python\n{tb[-3000:]}\n```"
    for admin_id in ADMIN_IDS:
        try:
            await context.bot.send_message(chat_id=admin_id, text=error_msg, parse_mode='Markdown')
        except:
            pass

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = update.message.text
    if text == "🚀 Start Rewards": await start_rewards(update, context)
    elif text == "📊 Account Status": await status(update, context)
    elif text == "🪙 SuperCoin Summary": await status(update, context)
    elif text == "🔄 Login Again": return await login_command(update, context)
    elif text == "🗑 Logout": await logout(update, context)
    elif text == "ℹ️ Help": await help_command(update, context)
    elif text == "⚙️ Settings": await update.message.reply_text("⚙️ Settings coming soon!")
    else: await update.message.reply_text("❓ Unknown command. Use the menu buttons.")

if __name__ == '__main__':
    if TELEGRAM_BOT_TOKEN == "YOUR_TELEGRAM_BOT_TOKEN":
        print("❌ Please set your TELEGRAM_BOT_TOKEN in the script.")
        sys.exit(1)
    app = ApplicationBuilder().token(TELEGRAM_BOT_TOKEN).build()
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('login', login_command), MessageHandler(filters.Regex('^🔄 Login Again$'), login_command)],
        states={PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_phone)], OTP: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_otp)]},
        fallbacks=[CommandHandler('start', start)],
    )
    app.add_handler(CommandHandler('start', start))
    app.add_handler(CommandHandler('rewards', start_rewards))
    app.add_handler(CommandHandler('status', status))
    app.add_handler(CommandHandler('logout', logout))
    app.add_handler(CommandHandler('help', help_command))
    app.add_handler(conv_handler)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
    print("🤖 Bot is starting...")
    app.run_polling()
