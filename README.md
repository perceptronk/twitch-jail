# Twitch Jail Overlay

Single-file browser overlay. Open `index.html` in OBS as a browser source — no server required.

## What it does

- Connects directly to Twitch IRC from the browser.
- Timeout events display the user's avatar behind bars for the timeout duration.
- Ban events move the user to the **Death Row** section.
- Mods or broadcaster can run `!execute <username> <method>` in chat to trigger an execution animation.
- Supported execution methods: `catapult`, `plank`.

## Setup

1. Add `index.html` as a **Browser Source** in OBS.
2. Use the **Connect** controls at the bottom of the overlay to enter your channel name and connect.
3. The overlay connects to Twitch chat anonymously — no credentials needed.

## Controls

The control panel at the bottom is for local testing and is invisible against a transparent background in OBS.

| Control | What it does |
|---|---|
| Channel + Connect | Joins that Twitch IRC channel |
| Timeout | Adds a user to Timeout Cell for 5 minutes |
| Ban | Adds a user to Death Row |
| Untimeout / Unban | Removes a user from either section |
| Execute | Triggers the execution animation (auto-bans first if needed) |

## Notes

- `!execute` only works for broadcaster and moderator badges.
- Command format is strict: exactly 3 tokens — `!execute <username> <method>`.
- Avatars are fetched from ivr.fi; identicon placeholders are shown while loading.
