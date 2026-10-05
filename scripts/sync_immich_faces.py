#!/usr/bin/env python3
"""Cache photos of named people out of Immich, for the family photo games.

Runs on the HOST, not in the container, for two reasons:

  * FunForge's container sits on its own bridge network and simply cannot reach
    Immich — cross-bridge traffic is dropped by Docker's isolation rules, and the
    host's published port is not bound in a way a container can use.
  * Keeping the app itself offline from Immich means the games keep working while
    Immich is down, upgrading, or has had its port mangled. The app only ever
    reads files off its own disk.

Standard library only, so it needs no virtualenv and the container image does not
have to grow an HTTP client it would never use.

Usage:
    python3 scripts/sync_immich_faces.py            # sync
    python3 scripts/sync_immich_faces.py --list     # just show the named people

Nothing it downloads is approved for the game automatically — a grown-up ticks
photos off in FunForge's parent screen first. Face recognition returns every
photo of a child, bath time included.
"""

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FACES_DIR = ROOT / "data" / "faces"
MANIFEST = ROOT / "data" / "family_photos.json"


def load_env():
    """Read .env by hand — this runs on the host, outside the app's settings."""
    env = {}
    path = ROOT / ".env"
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            env[key.strip()] = value.strip()
    # A real environment variable wins over the file.
    for key in ("IMMICH_URL", "IMMICH_API_KEY", "IMMICH_PEOPLE",
                "IMMICH_PHOTOS_PER_PERSON"):
        if os.environ.get(key):
            env[key] = os.environ[key]
    return env


def api(env, path, params=None, body=None):
    url = env["IMMICH_URL"].rstrip("/") + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    headers = {"x-api-key": env["IMMICH_API_KEY"], "Accept": "application/json"}
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers,
                                 method="POST" if body is not None else "GET")
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def fetch_thumbnail(env, asset_id, dest):
    url = f"{env['IMMICH_URL'].rstrip('/')}/api/assets/{asset_id}/thumbnail"
    req = urllib.request.Request(url, headers={"x-api-key": env["IMMICH_API_KEY"]})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = resp.read()
    dest.write_bytes(data)
    return len(data)


def load_manifest():
    if MANIFEST.exists():
        try:
            return json.loads(MANIFEST.read_text())
        except (OSError, ValueError):
            pass
    return {"synced_at": None, "people": [], "photos": []}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--list", action="store_true",
                    help="list the people Immich has names for, then exit")
    args = ap.parse_args()

    env = load_env()
    if not env.get("IMMICH_API_KEY"):
        sys.exit("IMMICH_API_KEY is not set. Copy .env.example to .env and put a "
                 "key in it (Immich: Account Settings -> API Keys).")

    try:
        people = api(env, "/api/people", {"withHidden": "false"})
    except urllib.error.HTTPError as e:
        sys.exit(f"Immich rejected the request: HTTP {e.code} {e.reason}")
    except urllib.error.URLError as e:
        sys.exit(f"Could not reach Immich at {env.get('IMMICH_URL')}: {e.reason}")

    named = {p["name"]: p["id"] for p in people.get("people", []) if p.get("name")}

    if args.list:
        print(f"{len(named)} named people in Immich:")
        for name in sorted(named):
            print(f"  {name}")
        return

    wanted = [n.strip() for n in env.get("IMMICH_PEOPLE", "").split(",") if n.strip()]
    if not wanted:
        sys.exit("IMMICH_PEOPLE is empty — nothing to sync.")

    missing = [n for n in wanted if n not in named]
    if missing:
        print(f"! Not found in Immich (check spelling on the People page): {', '.join(missing)}")
        print(f"  Immich knows: {', '.join(sorted(named)) or '(nobody named yet)'}")

    per_person = int(env.get("IMMICH_PHOTOS_PER_PERSON", "40"))
    FACES_DIR.mkdir(parents=True, exist_ok=True)
    MANIFEST.parent.mkdir(parents=True, exist_ok=True)

    manifest = load_manifest()
    # Approval is a human decision; never reset it by re-syncing.
    approved = {p["id"]: p.get("approved", False) for p in manifest.get("photos", [])}

    # Keyed by asset id, not appended blindly: a photo with both boys in it comes
    # back from both people's searches, and two manifest entries for one picture
    # means the approval screen shows it twice (tick one, reject the other, and it
    # still appears) and a board can deal four identical cards.
    by_id, downloaded, reused = {}, 0, 0
    for name in wanted:
        if name not in named:
            continue
        # Immich 2.x has no GET /api/people/{id}/assets — it 404s. Person assets
        # come from the metadata search, which takes a POST body.
        result = api(env, "/api/search/metadata", body={
            "personIds": [named[name]],
            "size": per_person,
            "type": "IMAGE",
        })
        assets = (result.get("assets") or {}).get("items") or []
        print(f"  {name}: {len(assets)} photos")

        for asset in assets[:per_person]:
            asset_id = asset.get("id")
            if not asset_id or asset.get("type") not in (None, "IMAGE"):
                continue
            dest = FACES_DIR / f"{asset_id}.jpg"
            if dest.exists():
                reused += 1
            else:
                try:
                    fetch_thumbnail(env, asset_id, dest)
                    downloaded += 1
                except (urllib.error.HTTPError, urllib.error.URLError, OSError) as e:
                    print(f"  ! skipped {asset_id}: {e}")
                    continue
            existing = by_id.get(asset_id)
            if existing:
                # Both boys are in this one; say so rather than picking a winner.
                if name not in existing["person"].split(" & "):
                    existing["person"] += f" & {name}"
                continue
            by_id[asset_id] = {
                "id": asset_id,
                "person": name,
                "file": f"faces/{asset_id}.jpg",
                "approved": approved.get(asset_id, False),
            }

    photos = list(by_id.values())
    manifest = {
        "synced_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "people": wanted,
        "photos": photos,
    }
    MANIFEST.write_text(json.dumps(manifest, indent=2))

    both = sum(1 for p in photos if " & " in p["person"])
    pending = sum(1 for p in photos if not p["approved"])
    print(f"\n{len(photos)} distinct photos cached ({downloaded} new, {reused} already had)")
    if both:
        print(f"{both} of them have more than one of them in it")
    print(f"manifest: {MANIFEST}")
    if pending:
        print(f"\n{pending} still need approving before they can appear in a game.")
        print("Open FunForge -> Games -> the ✏️ button, or /games/photos/")


if __name__ == "__main__":
    main()
