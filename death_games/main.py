from __future__ import annotations

import asyncio
import os
import random
import re
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import aiohttp
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

CHANNEL_RE = re.compile(r"^[A-Za-z0-9_]{3,25}$")
USERNAME_RE = re.compile(r"^[A-Za-z0-9_]{3,25}$")
RESERVED_ROUTES = {"ws", "static", "health"}
SUPPORTED_METHODS = {"catapult", "plank"}


@dataclass
class TimeoutEntry:
    username: str
    user_id: str
    avatar_url: str
    until: float


@dataclass
class BanEntry:
    username: str
    user_id: str
    avatar_url: str
    since: float


class AvatarResolver:
    def __init__(self) -> None:
        self._cache: dict[str, str] = {}
        self._session: aiohttp.ClientSession | None = None
        self._client_id = os.getenv("TWITCH_CLIENT_ID")
        self._access_token = os.getenv("TWITCH_APP_ACCESS_TOKEN")

    async def startup(self) -> None:
        self._session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=5))

    async def shutdown(self) -> None:
        if self._session:
            await self._session.close()

    async def get_avatar(self, username: str, user_id: str | None = None) -> str:
        key = (user_id or username).lower()
        if key in self._cache:
            return self._cache[key]

        avatar_url = await self._fetch_twitch_avatar(username=username, user_id=user_id)
        if not avatar_url:
            avatar_url = await self._fetch_public_avatar(username)
        if not avatar_url:
            avatar_url = f"https://api.dicebear.com/9.x/identicon/svg?seed={username.lower()}"

        self._cache[key] = avatar_url
        return avatar_url

    async def _fetch_twitch_avatar(self, username: str, user_id: str | None) -> str | None:
        if not self._client_id or not self._access_token or not self._session:
            return None

        if user_id and user_id.isdigit():
            url = f"https://api.twitch.tv/helix/users?id={user_id}"
        else:
            url = f"https://api.twitch.tv/helix/users?login={username}"

        headers = {
            "Client-ID": self._client_id,
            "Authorization": f"Bearer {self._access_token}",
        }

        try:
            async with self._session.get(url, headers=headers) as response:
                if response.status != 200:
                    return None
                payload = await response.json()
        except (aiohttp.ClientError, asyncio.TimeoutError):
            return None

        data = payload.get("data", [])
        if not data:
            return None
        return data[0].get("profile_image_url")

    async def _fetch_public_avatar(self, username: str) -> str | None:
        if not self._session:
            return None

        url = f"https://decapi.me/twitch/avatar/{username}"
        try:
            async with self._session.get(url) as response:
                if response.status != 200:
                    return None
                body = (await response.text()).strip()
        except (aiohttp.ClientError, asyncio.TimeoutError):
            return None

        if body.startswith("http://") or body.startswith("https://"):
            return body
        return None


class ChannelState:
    def __init__(self, channel: str) -> None:
        self.channel = channel
        self.timeouts: dict[str, TimeoutEntry] = {}
        self.death_row: dict[str, BanEntry] = {}
        self.clients: set[WebSocket] = set()
        self._cleanup_task: asyncio.Task[None] | None = None

    def start_cleanup(self) -> None:
        if not self._cleanup_task:
            self._cleanup_task = asyncio.create_task(self._cleanup_loop())

    async def stop_cleanup(self) -> None:
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
            self._cleanup_task = None

    async def _cleanup_loop(self) -> None:
        while True:
            await asyncio.sleep(1)
            now = time.time()
            expired = [name for name, entry in self.timeouts.items() if entry.until <= now]
            for username in expired:
                self.timeouts.pop(username, None)
                await self.broadcast({"type": "timeout_remove", "username": username})

    async def add_timeout(self, username: str, user_id: str, avatar_url: str, duration_seconds: int) -> None:
        if username in self.death_row:
            return
        until = time.time() + max(duration_seconds, 1)
        entry = TimeoutEntry(
            username=username,
            user_id=user_id,
            avatar_url=avatar_url,
            until=until,
        )
        self.timeouts[username] = entry
        await self.broadcast({"type": "timeout_add", "entry": asdict(entry)})

    async def add_ban(self, username: str, user_id: str, avatar_url: str) -> None:
        if username in self.timeouts:
            self.timeouts.pop(username)
            await self.broadcast({"type": "timeout_remove", "username": username})
        entry = BanEntry(
            username=username,
            user_id=user_id,
            avatar_url=avatar_url,
            since=time.time(),
        )
        self.death_row[username] = entry
        await self.broadcast({"type": "ban_add", "entry": asdict(entry)})

    async def remove_timeout(self, username: str) -> bool:
        if username not in self.timeouts:
            return False
        self.timeouts.pop(username)
        await self.broadcast({"type": "timeout_remove", "username": username})
        return True

    async def remove_ban(self, username: str) -> bool:
        if username not in self.death_row:
            return False
        self.death_row.pop(username)
        await self.broadcast({"type": "ban_remove", "username": username})
        return True

    async def execute(self, target: str, method: str, actor: str) -> bool:
        if method not in SUPPORTED_METHODS:
            return False

        target_key = target.lower()
        selected: BanEntry | None = self.death_row.get(target_key)
        if not selected:
            for entry in self.death_row.values():
                if entry.user_id == target:
                    selected = entry
                    break

        if not selected:
            return False

        self.death_row.pop(selected.username, None)
        await self.broadcast({"type": "ban_remove", "username": selected.username})
        await self.broadcast(
            {
                "type": "execute",
                "method": method,
                "actor": actor,
                "entry": asdict(selected),
                "at": time.time(),
            }
        )
        return True

    async def snapshot(self) -> dict[str, Any]:
        now = time.time()
        active_timeouts = [asdict(entry) for entry in self.timeouts.values() if entry.until > now]
        death_row = [asdict(entry) for entry in self.death_row.values()]
        return {
            "type": "snapshot",
            "timeouts": active_timeouts,
            "death_row": death_row,
            "methods": sorted(SUPPORTED_METHODS),
        }

    async def add_client(self, websocket: WebSocket) -> None:
        await websocket.accept()
        self.clients.add(websocket)
        await websocket.send_json(await self.snapshot())

    def remove_client(self, websocket: WebSocket) -> None:
        self.clients.discard(websocket)

    async def broadcast(self, payload: dict[str, Any]) -> None:
        dead_clients: list[WebSocket] = []
        for client in self.clients:
            try:
                await client.send_json(payload)
            except Exception:
                dead_clients.append(client)

        for client in dead_clients:
            self.remove_client(client)


class TwitchSession:
    def __init__(self, channel: str, state: ChannelState, avatar_resolver: AvatarResolver) -> None:
        self.channel = channel
        self.state = state
        self.avatar_resolver = avatar_resolver
        self._task: asyncio.Task[None] | None = None

    def start(self) -> None:
        if not self._task:
            self._task = asyncio.create_task(self._runner())

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    async def _runner(self) -> None:
        while True:
            try:
                await self._connect_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                await asyncio.sleep(3)

    async def _connect_once(self) -> None:
        reader, writer = await asyncio.open_connection(
            "irc.chat.twitch.tv",
            6697,
            ssl=True,
        )

        nick = os.getenv("TWITCH_BOT_USERNAME") or f"justinfan{random.randint(10000, 99999)}"
        password = os.getenv("TWITCH_BOT_OAUTH") or "SCHMOOPIIE"

        for command in [
            "CAP REQ :twitch.tv/tags twitch.tv/commands twitch.tv/membership",
            f"PASS {password}",
            f"NICK {nick}",
            f"JOIN #{self.channel}",
        ]:
            writer.write((command + "\r\n").encode("utf-8"))
        await writer.drain()

        while True:
            raw_line = await reader.readline()
            if not raw_line:
                break
            line = raw_line.decode("utf-8", errors="ignore").strip()

            if line.startswith("PING"):
                writer.write(line.replace("PING", "PONG", 1).encode("utf-8") + b"\r\n")
                await writer.drain()
                continue

            parsed = parse_irc_line(line)
            if not parsed:
                continue

            tags, command, payload = parsed
            if command == "CLEARCHAT":
                await self._handle_clearchat(tags, payload)
            elif command == "PRIVMSG":
                await self._handle_privmsg(tags, payload)

        writer.close()
        await writer.wait_closed()

    async def _handle_clearchat(self, tags: dict[str, str], payload: dict[str, str]) -> None:
        target = payload.get("trailing", "").strip().lower()
        if not target:
            return

        user_id = tags.get("target-user-id") or target
        avatar = await self.avatar_resolver.get_avatar(target, user_id)

        duration = tags.get("ban-duration")
        if duration and duration.isdigit():
            await self.state.add_timeout(target, user_id, avatar, int(duration))
            return

        await self.state.add_ban(target, user_id, avatar)

    async def _handle_privmsg(self, tags: dict[str, str], payload: dict[str, str]) -> None:
        message = payload.get("trailing", "").strip()
        if not message.startswith("!execute"):
            return

        if not is_moderator(tags):
            return

        parts = message.split()
        if len(parts) != 3:
            return

        target = parts[1].lstrip("@").lower()
        method = parts[2].lower()
        if method not in SUPPORTED_METHODS:
            return

        actor = tags.get("display-name") or tags.get("login") or "mod"
        await self.state.execute(target=target, method=method, actor=actor)


class Runtime:
    def __init__(self) -> None:
        self.avatar_resolver = AvatarResolver()
        self.states: dict[str, ChannelState] = {}
        self.sessions: dict[str, TwitchSession] = {}

    async def startup(self) -> None:
        await self.avatar_resolver.startup()

    async def shutdown(self) -> None:
        await self.avatar_resolver.shutdown()
        for session in list(self.sessions.values()):
            await session.stop()
        for state in list(self.states.values()):
            await state.stop_cleanup()

    def ensure_channel(self, channel: str) -> ChannelState:
        if channel in self.states:
            return self.states[channel]

        state = ChannelState(channel)
        state.start_cleanup()
        session = TwitchSession(channel, state, self.avatar_resolver)
        session.start()

        self.states[channel] = state
        self.sessions[channel] = session
        return state


def normalize_channel(channel: str) -> str | None:
    normalized = channel.lower().strip()
    if normalized in RESERVED_ROUTES or not CHANNEL_RE.match(normalized):
        return None
    return normalized


def normalize_username(username: str) -> str | None:
    normalized = username.lower().lstrip("@").strip()
    if not USERNAME_RE.match(normalized):
        return None
    return normalized


def parse_irc_line(line: str) -> tuple[dict[str, str], str, dict[str, str]] | None:
    tags: dict[str, str] = {}
    rest = line

    if rest.startswith("@"):
        tags_part, rest = rest.split(" ", 1)
        for item in tags_part[1:].split(";"):
            if "=" in item:
                key, value = item.split("=", 1)
                tags[key] = value

    prefix = ""
    if rest.startswith(":"):
        prefix, rest = rest[1:].split(" ", 1)

    if " :" in rest:
        before, trailing = rest.split(" :", 1)
    else:
        before, trailing = rest, ""

    parts = before.split()
    if not parts:
        return None

    command = parts[0]
    params = parts[1:]

    payload = {
        "prefix": prefix,
        "params": params,
        "trailing": trailing,
    }
    return tags, command, payload


def is_moderator(tags: dict[str, str]) -> bool:
    badges = tags.get("badges", "")
    return (
        "broadcaster/1" in badges
        or "moderator/1" in badges
        or "staff/1" in badges
        or "admin/1" in badges
    )


runtime = Runtime()
BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "static"

app = FastAPI(title="Twitch Jail Overlay")
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.on_event("startup")
async def on_startup() -> None:
    await runtime.startup()


@app.on_event("shutdown")
async def on_shutdown() -> None:
    await runtime.shutdown()


@app.get("/health")
async def health() -> JSONResponse:
    return JSONResponse({"ok": True, "channels": len(runtime.states)})


@app.get("/")
async def index() -> JSONResponse:
    return JSONResponse(
        {
            "message": "Open /<twitch_channel> to use overlay",
            "example": "/b8stal",
            "simulate_timeout": "/b8stal/timeout/someuser",
            "simulate_ban": "/b8stal/ban/someuser",
            "simulate_execute": "/b8stal/execute/someuser/catapult",
        }
    )


@app.post("/{channel}/timeout/{username}")
@app.get("/{channel}/timeout/{username}")
async def simulate_timeout(channel: str, username: str) -> JSONResponse:
    normalized_channel = normalize_channel(channel)
    normalized_username = normalize_username(username)
    if not normalized_channel or not normalized_username:
        return JSONResponse({"ok": False, "error": "invalid channel or username"}, status_code=400)

    state = runtime.ensure_channel(normalized_channel)
    user_id = f"{normalized_username}"
    avatar = await runtime.avatar_resolver.get_avatar(normalized_username, user_id)
    duration_seconds = 300
    await state.add_timeout(normalized_username, user_id, avatar, duration_seconds)

    return JSONResponse(
        {
            "ok": True,
            "simulated": "timeout",
            "channel": normalized_channel,
            "username": normalized_username,
            "duration_seconds": duration_seconds,
            "note": "Overlay simulation only. No real Twitch moderation action was performed.",
        }
    )


@app.post("/{channel}/ban/{username}")
@app.get("/{channel}/ban/{username}")
async def simulate_ban(channel: str, username: str) -> JSONResponse:
    normalized_channel = normalize_channel(channel)
    normalized_username = normalize_username(username)
    if not normalized_channel or not normalized_username:
        return JSONResponse({"ok": False, "error": "invalid channel or username"}, status_code=400)

    state = runtime.ensure_channel(normalized_channel)
    user_id = f"{normalized_username}"
    avatar = await runtime.avatar_resolver.get_avatar(normalized_username, user_id)
    await state.add_ban(normalized_username, user_id, avatar)

    return JSONResponse(
        {
            "ok": True,
            "simulated": "ban",
            "channel": normalized_channel,
            "username": normalized_username,
            "note": "Overlay simulation only. No real Twitch moderation action was performed.",
        }
    )


@app.post("/{channel}/untimeout/{username}")
@app.get("/{channel}/untimeout/{username}")
async def simulate_untimeout(channel: str, username: str) -> JSONResponse:
    normalized_channel = normalize_channel(channel)
    normalized_username = normalize_username(username)
    if not normalized_channel or not normalized_username:
        return JSONResponse({"ok": False, "error": "invalid channel or username"}, status_code=400)

    state = runtime.ensure_channel(normalized_channel)
    removed = await state.remove_timeout(normalized_username)

    return JSONResponse(
        {
            "ok": removed,
            "simulated": "untimeout",
            "channel": normalized_channel,
            "username": normalized_username,
            "note": "Overlay simulation only. No real Twitch moderation action was performed.",
        }
    )


@app.post("/{channel}/unban/{username}")
@app.get("/{channel}/unban/{username}")
async def simulate_unban(channel: str, username: str) -> JSONResponse:
    normalized_channel = normalize_channel(channel)
    normalized_username = normalize_username(username)
    if not normalized_channel or not normalized_username:
        return JSONResponse({"ok": False, "error": "invalid channel or username"}, status_code=400)

    state = runtime.ensure_channel(normalized_channel)
    removed = await state.remove_ban(normalized_username)

    return JSONResponse(
        {
            "ok": removed,
            "simulated": "unban",
            "channel": normalized_channel,
            "username": normalized_username,
            "note": "Overlay simulation only. No real Twitch moderation action was performed.",
        }
    )


@app.post("/{channel}/execute/{username}/{method}")
@app.get("/{channel}/execute/{username}/{method}")
async def simulate_execute(channel: str, username: str, method: str) -> JSONResponse:
    normalized_channel = normalize_channel(channel)
    normalized_username = normalize_username(username)
    normalized_method = method.lower().strip()
    if not normalized_channel or not normalized_username:
        return JSONResponse({"ok": False, "error": "invalid channel or username"}, status_code=400)
    if normalized_method not in SUPPORTED_METHODS:
        return JSONResponse(
            {
                "ok": False,
                "error": "invalid method",
                "supported_methods": sorted(SUPPORTED_METHODS),
            },
            status_code=400,
        )

    state = runtime.ensure_channel(normalized_channel)
    user_id = f"{normalized_username}"
    if normalized_username not in state.death_row:
        avatar = await runtime.avatar_resolver.get_avatar(normalized_username, user_id)
        state.death_row[normalized_username] = BanEntry(
            username=normalized_username,
            user_id=user_id,
            avatar_url=avatar,
            since=time.time(),
        )

    executed = await state.execute(
        target=normalized_username,
        method=normalized_method,
        actor="simulation",
    )

    if not executed:
        return JSONResponse(
            {
                "ok": False,
                "error": "unable to execute target",
                "note": "Overlay simulation only. No real Twitch moderation action was performed.",
            },
            status_code=404,
        )

    return JSONResponse(
        {
            "ok": True,
            "simulated": "execute",
            "channel": normalized_channel,
            "username": normalized_username,
            "method": normalized_method,
            "note": "Overlay simulation only. No real Twitch moderation action was performed.",
        }
    )


@app.get("/{channel}")
async def overlay(channel: str) -> FileResponse:
    normalized = normalize_channel(channel)
    if not normalized:
        return FileResponse(STATIC_DIR / "invalid.html", status_code=404)

    runtime.ensure_channel(normalized)
    return FileResponse(
        STATIC_DIR / "overlay.html",
        headers={
            "Cache-Control": "no-store, max-age=0",
            "Pragma": "no-cache",
            "Expires": "0",
        },
    )


@app.websocket("/ws/{channel}")
async def websocket_channel(websocket: WebSocket, channel: str) -> None:
    normalized = normalize_channel(channel)
    if not normalized:
        await websocket.close(code=1008)
        return

    state = runtime.ensure_channel(normalized)
    await state.add_client(websocket)

    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        state.remove_client(websocket)
