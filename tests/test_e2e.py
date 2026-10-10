"""Runs every Tanvo node inside a real ComfyUI against a fake Tanvo API.

    COMFYUI_PATH=/path/to/ComfyUI /path/to/ComfyUI/python -m unittest tests/test_e2e.py

This folder must be linked (or copied) into $COMFYUI_PATH/custom_nodes."""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import unittest
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import fake_api  # noqa: E402

COMFY = os.environ.get("COMFYUI_PATH")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@unittest.skipUnless(COMFY, "set COMFYUI_PATH to run the end-to-end test")
class EndToEnd(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.api, cls.state = fake_api.serve()
        cls.tmp = Path(tempfile.mkdtemp(prefix="tanvo-e2e-"))
        for d in ("user", "output", "input"):
            (cls.tmp / d).mkdir()
        cls.port = free_port()
        env = dict(os.environ, TANVO_BASE_URL=f"http://127.0.0.1:{cls.api.server_address[1]}")
        env.pop("TANVO_API_KEY", None)
        cls.log = open(cls.tmp / "comfyui.log", "w")
        cls.proc = subprocess.Popen(
            [sys.executable, "main.py", "--cpu", "--port", str(cls.port), "--disable-auto-launch",
             "--user-directory", str(cls.tmp / "user"), "--output-directory", str(cls.tmp / "output"), "--input-directory", str(cls.tmp / "input")],
            cwd=COMFY, env=env, stdout=cls.log, stderr=subprocess.STDOUT,
        )
        deadline = time.time() + 180
        while time.time() < deadline:
            try:
                cls.info = cls.get("/object_info")
                break
            except OSError:
                time.sleep(1)
        else:
            raise RuntimeError("ComfyUI did not start; see " + str(cls.tmp / "comfyui.log"))

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        cls.proc.wait(30)
        cls.log.close()
        cls.api.shutdown()

    @classmethod
    def get(cls, path):
        with urllib.request.urlopen(f"http://127.0.0.1:{cls.port}{path}", timeout=30) as r:
            return json.loads(r.read())

    def run_prompt(self, prompt):
        req = urllib.request.Request(f"http://127.0.0.1:{self.port}/prompt", data=json.dumps({"prompt": prompt}).encode(),
                                     headers={"Content-Type": "application/json"}, method="POST")
        with urllib.request.urlopen(req, timeout=30) as r:
            pid = json.loads(r.read())["prompt_id"]
        deadline = time.time() + 120
        while time.time() < deadline:
            h = self.get(f"/history/{pid}")
            if pid in h and h[pid]["status"].get("completed") is not None and h[pid]["status"]["status_str"] in ("success", "error"):
                return h[pid]
            time.sleep(0.5)
        self.fail("prompt did not finish")

    def ok(self, prompt):
        h = self.run_prompt(prompt)
        errors = [m[1].get("exception_message") for m in h["status"]["messages"] if m[0] == "execution_error"]
        self.assertEqual(h["status"]["status_str"], "success", errors)
        return h["outputs"]

    def files(self, sub=""):
        return sorted(p.name for p in (self.tmp / "output" / sub).glob("*") if p.is_file())

    def save_video(self, video, prefix):
        """SaveVideo's inputs changed in later ComfyUI versions (codec moved under format)."""
        fmt = self.info["SaveVideo"]["input"]["required"]["format"]
        nested = fmt[0] == "COMFY_DYNAMICCOMBO_V3"
        return {"class_type": "SaveVideo", "inputs": {"video": video, "filename_prefix": prefix, "format": "auto",
                                                      ("format.codec" if nested else "codec"): "auto"}}

    def set_key(self, key):
        d = self.tmp / "user" / "default"
        d.mkdir(parents=True, exist_ok=True)
        (d / "comfy.settings.json").write_text(json.dumps({"Tanvo.APIKey": key}))

    # ─── The tests run in name order: the free tier first, then with a key ───

    def test_0_nodes_register_with_the_live_catalogue(self):
        for n in ("TanvoImage", "TanvoVideo", "TanvoSong", "TanvoPhotoApp", "TanvoVideoApp", "TanvoSongApp"):
            self.assertIn(n, self.info)
        keys = [o["key"] for o in self.info["TanvoImage"]["input"]["required"]["model"][1]["options"]]
        self.assertEqual(keys[0], "studio-image-v1")  # the free model first
        video = [o["key"] for o in self.info["TanvoVideo"]["input"]["required"]["model"][1]["options"]]
        self.assertNotIn("kling-avatar", video)  # needs an audio input the node does not have
        self.assertTrue(any(e.endswith("/tanvo.js") for e in self.get("/extensions")))  # the settings entry
        video_apps = [o["key"] for o in self.info["TanvoVideoApp"]["input"]["required"]["app"][1]["options"]]
        self.assertIn("video-to-cartoon-converter", video_apps)
        self.assertNotIn("ai-lip-sync", video_apps)  # takes an audio file

    def test_1_free_image_with_reference_images(self):
        self.state.submissions.clear()
        out = self.ok({
            "1": {"class_type": "EmptyImage", "inputs": {"width": 32, "height": 32, "batch_size": 2, "color": 0xFF0000}},
            "2": {"class_type": "EmptyImage", "inputs": {"width": 48, "height": 40, "batch_size": 1, "color": 0x00FF00}},
            "3": {"class_type": "TanvoImage", "inputs": {"prompt": "combine them", "model": "studio-image-v1", "model.aspect": "16:9",
                                                         "model.resolution": "1K", "model.format": "PNG", "seed": 0,
                                                         "images.image_1": ["1", 0], "images.image_2": ["2", 0]}},
            "4": {"class_type": "SaveImage", "inputs": {"images": ["3", 0], "filename_prefix": "img"}},
        })
        body = self.state.submissions[-1]
        self.assertEqual(body["model"], "studio-image-v1")
        self.assertEqual(body["options"]["aspect"], "16:9")
        self.assertEqual(len(body["imageUrls"]), 3)  # both images of the batch, then the other one
        self.assertEqual(len(out["4"]["images"]), 2)  # two results of different sizes, resized into one batch
        self.assertIn("x-anon-id", {k.lower() for k in self.state.headers[-1]})
        anon = (self.tmp / "user" / "tanvo" / "anon_id").read_text()
        self.assertTrue(anon.startswith("comfyui-"))

    def test_2_paid_model_without_a_key_says_where_to_put_one(self):
        h = self.run_prompt({
            "1": {"class_type": "TanvoImage", "inputs": {"prompt": "a cat", "model": "nano-banana-2", "model.aspect": "1:1",
                                                         "model.resolution": "2K", "model.format": "PNG", "seed": 0}},
            "2": {"class_type": "PreviewImage", "inputs": {"images": ["1", 0]}},
        })
        self.assertEqual(h["status"]["status_str"], "error")
        msg = [m[1]["exception_message"] for m in h["status"]["messages"] if m[0] == "execution_error"][0]
        self.assertTrue(msg.startswith("Tanvo: needs_api_key"), msg)
        self.assertIn("Settings → Tanvo", msg)

    def test_3_video_with_tier_start_frame_and_key_from_settings(self):
        self.set_key("sk_test_settings")
        out = self.ok({
            "1": {"class_type": "EmptyImage", "inputs": {"width": 64, "height": 64, "batch_size": 1, "color": 0x3366FF}},
            "2": {"class_type": "TanvoVideo", "inputs": {"prompt": "slow push-in", "model": "veo-3-1", "model.tier": "Quality", "model.aspect": "9:16",
                                                         "model.resolution": "1080p", "model.duration": 8, "seed": 0, "start_image": ["1", 0]}},
            "3": self.save_video(["2", 0], "vid"),
        })
        body = self.state.submissions[-1]
        self.assertEqual(body["model"], "veo-3-1")
        self.assertEqual(body["options"]["tier"], "Quality")
        self.assertEqual(body["options"]["duration"], 8)
        self.assertEqual(len(body["imageUrls"]), 1)
        self.assertEqual(self.state.headers[-1].get("Authorization"), "Bearer sk_test_settings")
        self.assertTrue(any(f.startswith("vid") for f in self.files()), out)

    def test_4_song_two_takes_and_lyrics(self):
        self.set_key("sk_test_settings")
        out = self.ok({
            "1": {"class_type": "TanvoSong", "inputs": {"mode": "Describe", "prompt": "a birthday song for Ana", "style": "acoustic pop", "title": "", "seed": 0}},
            "2": {"class_type": "SaveAudio", "inputs": {"audio": ["1", 0], "filename_prefix": "audio/a"}},
            "3": {"class_type": "SaveAudio", "inputs": {"audio": ["1", 1], "filename_prefix": "audio/b"}},
            "4": {"class_type": "PreviewAny", "inputs": {"source": ["1", 2]}},
        })
        body = self.state.submissions[-1]
        self.assertEqual(body["options"]["tier"], "Describe")
        self.assertEqual(body["options"]["style"], "acoustic pop")
        self.assertEqual(len(self.files("audio")), 2)
        self.assertIn("la la", json.dumps(out["4"]))

    def test_5_photo_app_uploads_the_photo_and_uses_the_look(self):
        self.set_key("sk_test_settings")
        self.ok({
            "1": {"class_type": "EmptyImage", "inputs": {"width": 40, "height": 40, "batch_size": 1, "color": 0xAA8844}},
            "2": {"class_type": "TanvoPhotoApp", "inputs": {"app": "ai-pet-portrait-generator", "app.look": "royal", "app.pet_photo": ["1", 0],
                                                            "details": "Rex, a very good boy", "seed": 0}},
            "3": {"class_type": "SaveImage", "inputs": {"images": ["2", 0], "filename_prefix": "app"}},
        })
        body = self.state.submissions[-1]
        self.assertTrue(body["prompt"].startswith("Look royal."))
        self.assertIn("Rex, a very good boy", body["prompt"])
        self.assertEqual(len(body["imageUrls"]), 1)

    def test_6_video_app_sends_both_people_in_order(self):
        self.set_key("sk_test_settings")
        app = next(a for a in fake_api.CATALOG["apps"] if a["slug"] == "hotel-lobby-ai-video")
        ids = [i["id"] for i in app["inputs"]]
        self.ok({
            "1": {"class_type": "EmptyImage", "inputs": {"width": 40, "height": 40, "batch_size": 1, "color": 0x111111}},
            "2": {"class_type": "EmptyImage", "inputs": {"width": 40, "height": 40, "batch_size": 1, "color": 0xEEEEEE}},
            "3": {"class_type": "TanvoVideoApp", "inputs": {"app": app["slug"], "app.look": "classic", f"app.{ids[0]}": ["1", 0], f"app.{ids[1]}": ["2", 0],
                                                            "details": "", "seed": 0}},
            "4": self.save_video(["3", 0], "duet"),
        })
        body = self.state.submissions[-1]
        self.assertEqual(body["kind"], "video")
        from PIL import Image
        import io
        colours = [Image.open(io.BytesIO(self.state.uploads[u.rsplit("/", 1)[1]])).getpixel((0, 0)) for u in body["imageUrls"]]
        self.assertEqual(colours, [(17, 17, 17), (238, 238, 238)])  # both people, in the order of the app's inputs

    def test_6b_video_app_uploads_a_clip_as_mp4(self):
        self.set_key("sk_test_settings")
        self.ok({
            "1": {"class_type": "EmptyImage", "inputs": {"width": 64, "height": 64, "batch_size": 8, "color": 0x884422}},
            "2": {"class_type": "CreateVideo", "inputs": {"images": ["1", 0], "fps": 8}},
            "3": {"class_type": "TanvoVideoApp", "inputs": {"app": "video-to-cartoon-converter", "app.look": "anime", "app.video_clip": ["2", 0],
                                                            "details": "", "seed": 0}},
            "4": self.save_video(["3", 0], "cartoon"),
        })
        body = self.state.submissions[-1]
        self.assertEqual(len(body["imageUrls"]), 1)
        uid = body["imageUrls"][0].rsplit("/", 1)[1]
        self.assertEqual(self.state.upload_mimes[uid], "video/mp4")
        self.assertEqual(self.state.uploads[uid][4:8], b"ftyp")  # an MP4 file

    def test_7_song_app_puts_details_in_the_prompt(self):
        self.set_key("sk_test_settings")
        self.ok({
            "1": {"class_type": "TanvoSongApp", "inputs": {"app": "ai-birthday-song-generator", "app.look": "idea-1", "details": "my sister Ana", "seed": 0}},
            "2": {"class_type": "SaveAudio", "inputs": {"audio": ["1", 0], "filename_prefix": "audio/song"}},
        })
        body = self.state.submissions[-1]
        self.assertEqual(body["kind"], "music")
        self.assertIn("my sister Ana", body["prompt"])
        self.assertEqual(body.get("imageUrls", []), [])


if __name__ == "__main__":
    unittest.main()
