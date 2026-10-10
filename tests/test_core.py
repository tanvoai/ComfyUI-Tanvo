"""Tests for the parts that need no ComfyUI: python -m unittest tests/test_core.py (with `tanvo` installed)."""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import tanvo  # noqa: E402

import tanvo_core as core  # noqa: E402


def gen(status="PROCESSING", outputs=(), error=None, kind="image"):
    return tanvo.Generation(id="g1", kind=kind, model="m", prompt="p", options={}, cost=0, status=status, error=error, created_at=0,
                            outputs=[tanvo.Output(url=u, mime="image/png") for u in outputs])


class Key(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        os.environ.pop("TANVO_API_KEY", None)

    def write_settings(self, value):
        p = Path(self.dir) / "default"
        p.mkdir(parents=True, exist_ok=True)
        (p / "comfy.settings.json").write_text(json.dumps(value))

    def test_env_wins_over_settings(self):
        self.write_settings({core.SETTING_ID: "sk_settings"})
        with mock.patch.dict(os.environ, {"TANVO_API_KEY": " sk_env "}):
            self.assertEqual(core.api_key(self.dir), "sk_env")
        self.assertEqual(core.api_key(self.dir), "sk_settings")

    def test_missing_blank_or_broken_settings_mean_no_key(self):
        self.assertIsNone(core.api_key(self.dir))
        self.write_settings({core.SETTING_ID: "  "})
        self.assertIsNone(core.api_key(self.dir))
        (Path(self.dir) / "default" / "comfy.settings.json").write_text("{not json")
        self.assertIsNone(core.api_key(self.dir))
        self.assertIsNone(core.api_key(None))

    def test_anon_id_is_kept_across_calls(self):
        a = core.anon_id(self.dir)
        self.assertTrue(a.startswith("comfyui-"))
        self.assertEqual(core.anon_id(self.dir), a)

    def test_client_uses_the_key_or_a_free_wallet(self):
        self.assertIsNone(core.client(self.dir).api_key)
        self.assertEqual(core.client(self.dir).anon_id, core.anon_id(self.dir))
        self.write_settings({core.SETTING_ID: "sk_x"})
        c = core.client(self.dir)
        self.assertEqual((c.api_key, c.anon_id), ("sk_x", None))


class Catalogue(unittest.TestCase):
    def setUp(self):
        core._catalog = None

    def tearDown(self):
        core._catalog = None

    def test_falls_back_to_the_bundled_copy_offline(self):
        with mock.patch.object(core, "fetch_catalog", side_effect=OSError("no network")):
            cat = core.catalog()
        self.assertGreater(len(cat["models"]), 10)
        self.assertGreater(len(cat["apps"]), 10)

    def test_free_model_first_and_audio_models_left_out(self):
        fake = {"models": [
            {"id": "paid", "kind": "video", "free_tier": False, "inputs": ["text"]},
            {"id": "avatar", "kind": "video", "free_tier": False, "inputs": ["text", "image", "audio"]},
            {"id": "free", "kind": "video", "free_tier": True, "inputs": ["text"]},
        ], "apps": [{"slug": "a", "kind": "image", "inputs": [], "looks": []},
                    {"slug": "b", "kind": "image", "inputs": [{"id": "in1", "accept": "image/*"}], "looks": [{"id": "x"}]},
                    {"slug": "c", "kind": "image", "inputs": [{"id": "in1", "accept": "audio/*"}], "looks": [{"id": "x"}]}]}
        with mock.patch.object(core, "fetch_catalog", return_value=fake):
            self.assertEqual([m["id"] for m in core.models("video")], ["free", "paid"])
            self.assertEqual([a["slug"] for a in core.apps("image")], ["b"])  # no looks, or an audio input: left out

    def test_bundled_copy_is_slim(self):
        cat = json.loads((Path(core.__file__).parent / "catalog.json").read_text())
        look = cat["apps"][0]["looks"][0]
        self.assertEqual(set(look), {"id", "name"})  # no prompts in the package


class Wait(unittest.TestCase):
    def test_polls_until_done(self):
        c = mock.Mock()
        c.get.side_effect = [gen(), gen("SUCCEEDED", ["https://x/a.png"])]
        seen = []
        g = core.wait(c, gen(), timeout=60, every=0.5, sleep=lambda s: None, progress=seen.append)
        self.assertEqual(g.url, "https://x/a.png")
        self.assertEqual(seen[-1], 1.0)

    def test_a_failed_read_is_not_a_failed_run(self):
        c = mock.Mock()
        c.get.side_effect = [tanvo.ServiceError("network", "down", 0), gen("SUCCEEDED", ["https://x/a.png"])]
        self.assertTrue(core.wait(c, gen(), timeout=60, every=0.25, sleep=lambda s: None).succeeded)

    def test_failure_and_empty_results_raise(self):
        c = mock.Mock()
        c.get.return_value = gen("FAILED", error="moderation")
        with self.assertRaisesRegex(core.RunFailed, "failed: moderation. Failed runs are refunded"):
            core.wait(c, gen(), timeout=60, every=0.25, sleep=lambda s: None)
        c.get.return_value = gen("SUCCEEDED")
        with self.assertRaisesRegex(core.RunFailed, "without files"):
            core.wait(c, gen(), timeout=60, every=0.25, sleep=lambda s: None)

    def test_interrupt_stops_waiting(self):
        c = mock.Mock()
        c.get.return_value = gen()

        def interrupted():
            raise KeyboardInterrupt

        with self.assertRaises(KeyboardInterrupt):
            core.wait(c, gen(), timeout=60, interrupted=interrupted, sleep=lambda s: None)
        c.get.assert_not_called()

    def test_timeout_points_to_history(self):
        c = mock.Mock()
        c.get.return_value = gen()
        clock = iter(range(0, 1000, 10))
        with mock.patch.object(core.time, "monotonic", lambda: next(clock)):
            with self.assertRaisesRegex(core.RunFailed, "still processing after 30s.*/history"):
                core.wait(c, gen(), timeout=30, every=0.25, sleep=lambda s: None)


if __name__ == "__main__":
    unittest.main()
