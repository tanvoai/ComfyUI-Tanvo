"""Refreshes catalog.json, the copy of Tanvo's models and apps the nodes fall back to offline:
python scripts/update_catalog.py"""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tanvo_core import fetch_catalog  # noqa: E402

data = fetch_catalog(timeout=30)
out = Path(__file__).resolve().parent.parent / "catalog.json"
out.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n")
print(f"{len(data['models'])} models, {len(data['apps'])} apps -> {out.name}")
