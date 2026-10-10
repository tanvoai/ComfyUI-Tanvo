"""Everything in the Tanvo nodes that does not need ComfyUI: the catalogue, the key, the client and waiting."""
from __future__ import annotations

import json
import os
import time
import urllib.request
import uuid
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

import tanvo

BASE_URL = (os.environ.get("TANVO_BASE_URL") or "https://tanvo.ai").rstrip("/")
SETTING_ID = "Tanvo.APIKey"
HERE = Path(__file__).parent

# Inputs a model needs that these nodes cannot provide yet (kling-avatar wants an audio track).
UNSUPPORTED_INPUTS = {"audio"}


# ─── Catalogue ─────────────────────────────────────────────────────────────

def _slim(models: List[Dict[str, Any]], apps: List[Dict[str, Any]]) -> Dict[str, Any]:
    """Only what the node schemas need, so the bundled file stays small."""
    return {
        "models": [
            {k: m.get(k) for k in ("id", "kind", "name", "free_tier", "inputs", "options", "defaults", "credits_at_defaults")}
            for m in models
        ],
        "apps": [
            {
                "slug": a["slug"], "kind": a.get("kind", "image"), "name": a.get("name", a["slug"]),
                "inputs": [{k: i.get(k) for k in ("id", "label", "accept", "min", "max")} for i in a.get("inputs") or []],
                "looks": [{"id": l["id"], "name": l.get("name", l["id"])} for l in a.get("looks") or []],
            }
            for a in apps
        ],
    }


def fetch_catalog(base_url: str = BASE_URL, timeout: float = 3.0) -> Dict[str, Any]:
    def get(path: str) -> Dict[str, Any]:
        req = urllib.request.Request(f"{base_url}/api/v1{path}", headers={"Accept": "application/json", "User-Agent": "comfyui-tanvo"})
        with urllib.request.urlopen(req, timeout=timeout) as res:
            return json.loads(res.read())
    return _slim(get("/models")["models"], get("/apps")["apps"])


_catalog: Optional[Dict[str, Any]] = None


def catalog() -> Dict[str, Any]:
    """The live catalogue when tanvo.ai answers within a few seconds, otherwise the copy shipped with the nodes.
    Read once per ComfyUI start, so new models and apps show up after a restart."""
    global _catalog
    if _catalog is None:
        try:
            if os.environ.get("TANVO_OFFLINE"):
                raise OSError("offline")
            _catalog = fetch_catalog()
        except Exception:
            _catalog = json.loads((HERE / "catalog.json").read_text())
    return _catalog


def models(kind: str) -> List[Dict[str, Any]]:
    ms = [m for m in catalog()["models"] if m["kind"] == kind and not UNSUPPORTED_INPUTS & set(m.get("inputs") or [])]
    return sorted(ms, key=lambda m: not m.get("free_tier"))  # the free model first, so a new node works without a key


def media(spec_input: Dict[str, Any]) -> str:
    """"image", "video" or "audio": what an app input takes."""
    return (spec_input.get("accept") or "image/*").split("/")[0]


def apps(kind: str) -> List[Dict[str, Any]]:
    """Apps of a kind, leaving out those that take an audio file (the API takes app audio separately; not in these nodes yet)."""
    return [a for a in catalog()["apps"] if a["kind"] == kind and a["looks"] and not any(media(i) == "audio" for i in a["inputs"])]


# ─── Key and client ────────────────────────────────────────────────────────

def api_key(user_dir: Optional[str]) -> Optional[str]:
    """TANVO_API_KEY, else the key saved in ComfyUI's settings (Settings → Tanvo)."""
    key = os.environ.get("TANVO_API_KEY", "").strip()
    if key or not user_dir:
        return key or None
    try:
        settings = json.loads((Path(user_dir) / "default" / "comfy.settings.json").read_text())
        return (settings.get(SETTING_ID) or "").strip() or None
    except (OSError, ValueError):
        return None


def anon_id(user_dir: Optional[str]) -> str:
    """A stable id for the free tier, kept in ComfyUI's user folder so the allowance is the same across restarts."""
    if not user_dir:
        return f"comfyui-{uuid.uuid4()}"
    path = Path(user_dir) / "tanvo" / "anon_id"
    try:
        value = path.read_text().strip()
        if value:
            return value
    except OSError:
        pass
    value = f"comfyui-{uuid.uuid4()}"
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(value)
    except OSError:
        pass
    return value


def client(user_dir: Optional[str]) -> tanvo.Client:
    key = api_key(user_dir)
    return tanvo.Client(key, anon_id=None if key else anon_id(user_dir), base_url=BASE_URL, timeout=60)


# ─── Waiting ───────────────────────────────────────────────────────────────

class RunFailed(RuntimeError):
    pass


def wait(
    c: tanvo.Client, g: tanvo.Generation, *, timeout: float, every: float = 4.0,
    interrupted: Callable[[], None] = lambda: None, progress: Callable[[float], None] = lambda f: None,
    sleep: Callable[[float], None] = time.sleep,
) -> tanvo.Generation:
    """Polls a run until it finishes. `interrupted` raises when the user cancels in ComfyUI; the run itself keeps
    going on Tanvo and lands in the account's history."""
    started = time.monotonic()
    while not g.done:
        elapsed = time.monotonic() - started
        if elapsed > timeout:
            raise RunFailed(f"Tanvo run {g.id} is still {g.status.lower()} after {int(timeout)}s. It keeps going: find it at {BASE_URL}/history.")
        progress(min(0.95, elapsed / timeout))
        waited = 0.0
        while waited < every:
            interrupted()
            sleep(0.25)
            waited += 0.25
        try:
            g = c.get(g.id)
        except tanvo.ServiceError:
            pass  # one failed read is not a failed run
    if not g.succeeded:
        raise RunFailed(f"Tanvo run {g.id} {g.status.lower()}: {g.error or 'no reason given'}. Failed runs are refunded.")
    if not g.outputs:
        raise RunFailed(f"Tanvo run {g.id} finished without files.")
    progress(1.0)
    return g


def download(url: str, timeout: float = 180) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "comfyui-tanvo"})
    with urllib.request.urlopen(req, timeout=timeout) as res:
        return res.read()
