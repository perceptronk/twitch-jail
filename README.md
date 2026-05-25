# Death Games Overlay

Browser stream overlay powered by a local Python server.

## What it does

1. Open `/<channel>` (example: `/b8stal`).
2. The server joins that Twitch chat and listens to moderation events.
3. Timeout events display the user avatar + user id behind bars for the timeout duration.
4. Ban events display the user in a dedicated `Death Row` section.
5. Mods or broadcaster can run `!execute <userId-or-username> <method>` in chat.
6. Supported execution methods right now:
	- `catapult`
	- `plank`

## Run

```bash
uv sync
uv run uvicorn death_games.main:app --host 127.0.0.1 --port 8000 --reload
```

Then open:

- `http://127.0.0.1:8000/b8stal` (replace with your channel)

## Twitch auth options

The app supports two modes:

- Anonymous read mode (default): no env vars required
- Bot-auth mode (recommended for reliability): set env vars below

### Optional bot credentials

```bash
set TWITCH_BOT_USERNAME=your_bot_name
set TWITCH_BOT_OAUTH=oauth:xxxxxxxxxxxxxxxxxxxx
```

### Optional real profile image lookup (Helix)

If these are not set, fallback avatars are generated.

```bash
set TWITCH_CLIENT_ID=your_client_id
set TWITCH_APP_ACCESS_TOKEN=your_app_access_token
```

## Notes

- `!execute` can only be triggered by broadcaster/mod badges.
- Command format is strict: exactly 3 tokens.
- `method` must be `catapult` or `plank`.
- Target accepts either username or user id from death row.

## Dummy simulation routes (safe)

These endpoints only update the local overlay state. They do not issue real timeout/ban actions on Twitch.

- `GET /<channel>/timeout/<username>`
- `GET /<channel>/ban/<username>`
- `GET /<channel>/execute/<username>/<method>`

Examples:

```bash
curl http://127.0.0.1:8000/b8stal/timeout/testuser
curl http://127.0.0.1:8000/b8stal/ban/testuser
curl http://127.0.0.1:8000/b8stal/execute/testuser/catapult
```

Behavior:

- Timeout simulation duration is always 300 seconds (5 minutes).
- Ban simulation adds the user to the Death Row overlay section.
- Execute simulation triggers the animation with `catapult` or `plank`.
