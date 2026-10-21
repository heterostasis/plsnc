"""Copies every track from your SOURCE playlists into your DEST playlist,
skipping anything that's already in DEST.

Settings come from environment variables (set them as GitHub Secrets):
  SPOTIFY_CLIENT_ID, SPOTIFY_CLIENT_SECRET, SPOTIFY_REFRESH_TOKEN
  SOURCE_PLAYLISTS  playlist links or IDs, separated by commas or new lines
  DEST_PLAYLIST     the playlist link or ID everything gets added to
  DRY_RUN           "true" = only print what would be added
"""
import os
import sys
import time

import requests

API = "https://api.spotify.com/v1"


def playlist_id(text):
    """Accepts a share link, a spotify: URI, or a bare playlist ID."""
    text = text.strip()
    if "/playlist/" in text:
        text = text.split("/playlist/")[1]
    elif text.startswith("spotify:playlist:"):
        text = text.split(":")[-1]
    return text.split("?")[0].split("/")[0]


def get_access_token():
    r = requests.post(
        "https://accounts.spotify.com/api/token",
        data={
            "grant_type": "refresh_token",
            "refresh_token": os.environ["SPOTIFY_REFRESH_TOKEN"],
        },
        auth=(os.environ["SPOTIFY_CLIENT_ID"], os.environ["SPOTIFY_CLIENT_SECRET"]),
        timeout=30,
    )
    if not r.ok:
        sys.exit(f"Couldn't log in to Spotify ({r.status_code}): {r.text}")
    return r.json()["access_token"]


def api(method, url, token, **kwargs):
    """Calls Spotify, waiting and retrying briefly if we're rate limited."""
    r = None
    for _ in range(5):
        r = requests.request(
            method,
            url,
            headers={"Authorization": f"Bearer {token}"},
            timeout=30,
            **kwargs,
        )
        if r.status_code != 429:
            break
        wait = int(r.headers.get("Retry-After", "5"))
        if wait > 60:
            print(f"Spotify rate-limited us for {wait}s. Skipping; next run will retry.")
            sys.exit(0)
        time.sleep(wait + 1)
    return r


def get_track_uris(pid, token):
    """Returns the URIs of all normal tracks in a playlist (skips local files and podcasts)."""
    uris = []
    url = f"{API}/playlists/{pid}/items"
    params = {"limit": 50}
    while url:
        r = api("GET", url, token, params=params)
        if r.status_code in (403, 404):
            raise RuntimeError(
                f"Can't read playlist {pid} (HTTP {r.status_code}). "
                "It has to be a playlist you own or collaborate on."
            )
        r.raise_for_status()
        data = r.json()
        for entry in data.get("items", []):
            # Spotify renamed this field from "track" to "item" in Feb 2026; handle both.
            obj = entry.get("item") or entry.get("track")
            if not obj or obj.get("is_local") or obj.get("type") != "track":
                continue
            uris.append(obj["uri"])
        url = data.get("next")
        params = None  # the "next" link already includes the paging info
    return uris


def main():
    token = get_access_token()
    dest = playlist_id(os.environ["DEST_PLAYLIST"])
    sources = [
        playlist_id(s)
        for s in os.environ["SOURCE_PLAYLISTS"].replace("\n", ",").split(",")
        if s.strip()
    ]
    dry_run = os.environ.get("DRY_RUN", "").strip().lower() in ("1", "true", "yes")

    have = set(get_track_uris(dest, token))
    to_add = []
    for src in sources:
        try:
            uris = get_track_uris(src, token)
        except RuntimeError as e:
            print(f"SKIPPING: {e}")
            continue
        for uri in uris:
            if uri not in have:
                have.add(uri)
                to_add.append(uri)

    if not to_add:
        print("Nothing new to add.")
        return

    print(f"Found {len(to_add)} new track(s).")
    if dry_run:
        print("DRY RUN: not adding anything. Tracks that would be added:")
        for uri in to_add:
            print("  ", uri)
        return

    for i in range(0, len(to_add), 100):  # Spotify allows 100 per request
        chunk = to_add[i : i + 100]
        r = api("POST", f"{API}/playlists/{dest}/items", token, json={"uris": chunk})
        if not r.ok:
            sys.exit(f"Failed to add tracks ({r.status_code}): {r.text}")
        print(f"Added {len(chunk)} track(s).")


if __name__ == "__main__":
    main()
