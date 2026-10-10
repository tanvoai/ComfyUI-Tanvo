"""ComfyUI nodes for Tanvo: image, video and song generation, and Tanvo's ready-made photo apps."""
from __future__ import annotations

import contextlib
import dataclasses
import io as _io
import re
from typing import Any, Dict, Iterator, List, Optional

import av
import numpy as np
import torch
from PIL import Image
from typing_extensions import override

import comfy.model_management
import comfy.utils
import folder_paths
import tanvo
from comfy_api.latest import ComfyExtension, InputImpl, Types, io

from . import tanvo_core as core

CATEGORY = "Tanvo"
SEED_TOOLTIP = "Tanvo does not use the seed. Change it to run the node again with the same inputs; each run is charged."
KEY_HINT = "Without an API key the node runs on Tanvo's free tier (the free model, watermarked). Add a key under Settings → Tanvo."


# ─── Conversions ───────────────────────────────────────────────────────────

def image_to_png(image: torch.Tensor) -> bytes:
    """One [H, W, C] image in 0..1 to PNG bytes."""
    arr = (image.clamp(0, 1).cpu().numpy() * 255).round().astype(np.uint8)
    if arr.shape[-1] == 1:
        arr = arr[..., 0]
    elif arr.shape[-1] == 4:
        arr = arr[..., :3]
    buf = _io.BytesIO()
    Image.fromarray(arr).save(buf, format="PNG")
    return buf.getvalue()


def images_from(tensors: List[Optional[torch.Tensor]]) -> List[bytes]:
    """Every image in every connected batch, as PNG bytes, in order."""
    out: List[bytes] = []
    for t in tensors:
        if t is None:
            continue
        for i in range(t.shape[0]):
            out.append(image_to_png(t[i]))
    return out


def bytes_to_image(data: bytes) -> torch.Tensor:
    img = Image.open(_io.BytesIO(data)).convert("RGB")
    return torch.from_numpy(np.array(img).astype(np.float32) / 255.0).unsqueeze(0)


def to_batch(images: List[torch.Tensor]) -> torch.Tensor:
    """Stacks results into one batch, resizing to the first one's size when they differ."""
    first = images[0]
    h, w = first.shape[1], first.shape[2]
    same = [first]
    for img in images[1:]:
        if img.shape[1:3] != (h, w):
            img = comfy.utils.common_upscale(img.movedim(-1, 1), w, h, "lanczos", "center").movedim(1, -1)
        same.append(img)
    return torch.cat(same, dim=0)


def bytes_to_audio(data: bytes) -> Dict[str, Any]:
    """Any audio file to ComfyUI's AUDIO: {"waveform": [1, C, T] float32, "sample_rate": int}."""
    with av.open(_io.BytesIO(data)) as container:
        stream = container.streams.audio[0]
        rate = int(stream.codec_context.sample_rate)
        resampler = av.AudioResampler(format="fltp", layout="stereo" if (stream.channels or 1) > 1 else "mono", rate=rate)
        chunks = []
        for frame in container.decode(stream):
            for f in resampler.resample(frame):
                chunks.append(torch.from_numpy(f.to_ndarray()))
        for f in resampler.resample(None):
            chunks.append(torch.from_numpy(f.to_ndarray()))
    if not chunks:
        raise core.RunFailed("The song file has no audio.")
    return {"waveform": torch.cat(chunks, dim=1).unsqueeze(0).contiguous(), "sample_rate": rate}


# ─── Running ───────────────────────────────────────────────────────────────

def _client():
    return core.client(folder_paths.get_user_directory())


@contextlib.contextmanager
def _errors() -> Iterator[None]:
    """API errors with a ComfyUI hint instead of the SDK's (which talks about api_key=)."""
    try:
        yield
    except tanvo.TanvoError as e:
        message = str(e).replace("(then pass api_key= or set TANVO_API_KEY)", "(then paste it in ComfyUI under Settings → Tanvo, or set TANVO_API_KEY)")
        raise RuntimeError(f"Tanvo: {message}") from None


def _wait(c, g, timeout: float):
    bar = comfy.utils.ProgressBar(100)
    return core.wait(
        c, g, timeout=timeout, every=6 if g.kind == "video" else 3,
        interrupted=comfy.model_management.throw_exception_if_processing_interrupted,
        progress=lambda f: bar.update_absolute(int(f * 100)),
    )


def _image_outputs(g) -> torch.Tensor:
    return to_batch([bytes_to_image(core.download(o.url)) for o in g.outputs])


def _video_output(g) -> InputImpl.VideoFromFile:
    return InputImpl.VideoFromFile(_io.BytesIO(core.download(g.outputs[0].url, timeout=600)))


def _song_outputs(g):
    takes = [bytes_to_audio(core.download(o.url, timeout=300)) for o in g.outputs[:2]]
    if len(takes) == 1:
        takes.append(takes[0])
    lyrics = "\n\n".join(f"{o.title or 'Take'}\n{o.lyrics}" for o in g.outputs[:2] if o.lyrics)
    return takes, lyrics


def _settings(model: Dict[str, Any], keys: List[str]) -> Dict[str, Any]:
    return {k: model[k] for k in keys if k in model and model[k] not in (None, "")}


# ─── Schema helpers ────────────────────────────────────────────────────────

_SCHEMA_FIELDS = {f.name for f in dataclasses.fields(io.Schema)}


def _schema(**kw: Any) -> io.Schema:
    """io.Schema without the fields an older ComfyUI does not know yet (search_aliases came after 0.8)."""
    return io.Schema(**{k: v for k, v in kw.items() if k in _SCHEMA_FIELDS})


def _combo(name: str, options: List[Any], default: Any, tooltip: str) -> io.Combo.Input:
    return io.Combo.Input(name, options=options, default=default if default in options else options[0], tooltip=tooltip)


def _model_inputs(m: Dict[str, Any]) -> List[io.Input]:
    opts, defaults = m.get("options") or {}, m.get("defaults") or {}
    inputs: List[io.Input] = []
    for key, tip in (("tier", "Quality tier."), ("aspect", "Aspect ratio."), ("resolution", "Output resolution."),
                     ("duration", "Length in seconds."), ("format", "File format.")):
        values = opts.get(key)
        if values:
            inputs.append(_combo(key, list(values), defaults.get(key), tip))
    if opts.get("audio"):
        inputs.append(io.Boolean.Input("audio", default=bool(defaults.get("audio", True)), tooltip="Generate sound with the video."))
    return inputs


def _model_combo(kind: str) -> io.DynamicCombo.Input:
    options = [io.DynamicCombo.Option(m["id"], _model_inputs(m)) for m in core.models(kind)]
    first = core.models(kind)[0]
    return io.DynamicCombo.Input(
        "model", options=options,
        tooltip=f"The model. {first['id']} is free without a key; the others need a Tanvo API key and cost credits (see tanvo.ai/pricing).",
    )


def _seed() -> io.Int.Input:
    # Fixed by default so queueing again does not pay for the same run twice (older ComfyUI has no enum for it).
    fixed = io.ControlAfterGenerate.fixed if hasattr(io, "ControlAfterGenerate") else "fixed"
    return io.Int.Input("seed", default=0, min=0, max=2**31 - 1, control_after_generate=fixed, tooltip=SEED_TOOLTIP)


def _photo_sockets(spec: Dict[str, Any]) -> List[tuple]:
    """(socket name, label, required, "image" | "video") for each file an app takes. The frontend shows nested inputs
    by name, so generic ids like `in1` are named after their label instead ("Pet photo" -> pet_photo)."""
    sockets = []
    for i in spec["inputs"]:
        label = i.get("label") or i["id"]
        base = i["id"]
        if re.fullmatch(r"in\d+", base):
            base = re.sub(r"[^a-z0-9]+", "_", label.lower()).strip("_") or base
        for n in range(max(1, i.get("max") or 1)):
            sockets.append((base if n == 0 else f"{base}_{n + 1}", label, n < (i.get("min") or 0), core.media(i)))
    return sockets


def _app_options(kind: str) -> List[io.DynamicCombo.Option]:
    options = []
    for a in sorted(core.apps(kind), key=lambda a: a["slug"]):
        inputs: List[io.Input] = [io.Combo.Input("look", options=[l["id"] for l in a["looks"]], tooltip="The app's preset looks, as on " + f"tanvo.ai/{kind}/{a['slug']}.")]
        for name, label, required, what in _photo_sockets(a):
            socket = io.Video if what == "video" else io.Image
            inputs.append(socket.Input(name, display_name=label, optional=not required, tooltip=label + ("" if required else " (optional)")))
        options.append(io.DynamicCombo.Option(a["slug"], inputs))
    return options


def video_to_mp4(video) -> bytes:
    buf = _io.BytesIO()
    video.save_to(buf, format=Types.VideoContainer.MP4, codec=Types.VideoCodec.H264)
    return buf.getvalue()


def _app_files(c, app: Dict[str, Any], kind: str) -> List[Any]:
    """The photos and clips connected to an app node, in the order the app expects them."""
    spec = next((a for a in core.apps(kind) if a["slug"] == app["app"]), None)
    if spec is None:
        raise ValueError(f"Unknown Tanvo app {app['app']!r}.")
    files: List[Any] = []
    for name, _, _, what in _photo_sockets(spec):
        value = app.get(name)
        if value is None:
            continue
        files.append(c.upload(video_to_mp4(value), mime="video/mp4") if what == "video" else image_to_png(value[0]))
    return files


def _run_app(kind: str, app: Dict[str, Any], details: str, photos: Optional[List[Any]] = None):
    c = _client()
    g = c.generate_from_app(app["app"], look=app.get("look"), photos=photos if photos is not None else _app_files(c, app, kind),
                            details=details.strip() or None, wait=False)
    return _wait(c, g, timeout={"video": 900, "music": 420}.get(kind, 240))


# ─── Nodes ─────────────────────────────────────────────────────────────────

class TanvoImage(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return _schema(
            node_id="TanvoImage",
            display_name="Tanvo Image",
            category=f"{CATEGORY}/image",
            description="Text to image, or edit and combine images, on Nano Banana, Seedream, GPT Image, Qwen and Grok Imagine. " + KEY_HINT,
            search_aliases=["tanvo", "nano banana", "seedream", "gpt image", "text to image", "image edit"],
            inputs=[
                io.String.Input("prompt", multiline=True, default="", tooltip="What to make, or what to change in the images."),
                _model_combo("image"),
                io.Autogrow.Input("images", template=io.Autogrow.TemplateNames(io.Image.Input("image"), names=[f"image_{i}" for i in range(1, 9)], min=0),
                                  tooltip="Optional images to edit or combine (every image of a batch counts)."),
                _seed(),
            ],
            outputs=[io.Image.Output(display_name="images"), io.String.Output(display_name="url")],
        )

    @classmethod
    def execute(cls, prompt: str, model: Dict[str, Any], seed: int, images: Optional[Dict[str, Any]] = None) -> io.NodeOutput:
        with _errors():
            if not prompt.strip():
                raise ValueError("Write a prompt.")
            c = _client()
            g = c.generate_image(prompt, model=model["model"], images=images_from(list((images or {}).values())), wait=False,
                                 **_settings(model, ["aspect", "resolution", "format"]))
            g = _wait(c, g, timeout=240)
            return io.NodeOutput(_image_outputs(g), g.url)


class TanvoVideo(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return _schema(
            node_id="TanvoVideo",
            display_name="Tanvo Video",
            category=f"{CATEGORY}/video",
            description="Text to video, or animate a start frame (with an optional end frame), on Kling, Veo, Seedance, Wan, LTX and MiniMax, most with sound. " + KEY_HINT,
            search_aliases=["tanvo", "kling", "veo", "seedance", "wan", "text to video", "image to video"],
            inputs=[
                io.String.Input("prompt", multiline=True, default="", tooltip="The shot: subject, action, camera and sound."),
                _model_combo("video"),
                io.Image.Input("start_image", optional=True, tooltip="Animate this image (the first of a batch)."),
                io.Image.Input("end_image", optional=True, tooltip="End on this image."),
                _seed(),
            ],
            outputs=[io.Video.Output(display_name="video"), io.String.Output(display_name="url")],
        )

    @classmethod
    def execute(cls, prompt: str, model: Dict[str, Any], seed: int, start_image: Optional[torch.Tensor] = None,
                end_image: Optional[torch.Tensor] = None) -> io.NodeOutput:
        with _errors():
            if not prompt.strip():
                raise ValueError("Write a prompt.")
            c = _client()
            g = c.generate_video(
                prompt, model=model["model"], wait=False,
                image=image_to_png(start_image[0]) if start_image is not None else None,
                end_image=image_to_png(end_image[0]) if end_image is not None else None,
                **_settings(model, ["tier", "aspect", "resolution", "duration", "audio"]),
            )
            g = _wait(c, g, timeout=900)
            return io.NodeOutput(_video_output(g), g.url)


class TanvoSong(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return _schema(
            node_id="TanvoSong",
            display_name="Tanvo Song",
            category=f"{CATEGORY}/audio",
            description="A song in two takes on Suno V6: describe it, sing your own lyrics, or go instrumental. Needs a Tanvo API key.",
            search_aliases=["tanvo", "suno", "music", "song", "lyrics"],
            inputs=[
                io.Combo.Input("mode", options=["Describe", "Lyrics", "Instrumental"], default="Describe",
                               tooltip="Describe: the prompt describes the song. Lyrics: the prompt is the lyrics to sing. Instrumental: no vocals."),
                io.String.Input("prompt", multiline=True, default="", tooltip="The song, or its lyrics in Lyrics mode."),
                io.String.Input("style", default="", optional=True, tooltip="Genre, instruments and voice, e.g. acoustic pop, hand claps, warm female vocal."),
                io.String.Input("title", default="", optional=True),
                _seed(),
            ],
            outputs=[io.Audio.Output(display_name="take_1"), io.Audio.Output(display_name="take_2"), io.String.Output(display_name="lyrics")],
        )

    @classmethod
    def execute(cls, mode: str, prompt: str, seed: int, style: str = "", title: str = "") -> io.NodeOutput:
        with _errors():
            if not prompt.strip():
                raise ValueError("Write a prompt.")
            c = _client()
            g = c.generate_music(prompt, mode=mode, style=style.strip() or None, title=title.strip() or None, wait=False)
            g = _wait(c, g, timeout=420)
            (a, b), lyrics = _song_outputs(g)
            return io.NodeOutput(a, b, lyrics)


def _app_schema(kind: str, node_id: str, name: str, description: str, outputs: List[io.Output]) -> io.Schema:
    return _schema(
        node_id=node_id,
        display_name=name,
        category=f"{CATEGORY}/apps",
        description=description,
        search_aliases=["tanvo", "app", "preset", "template"],
        inputs=[
            io.DynamicCombo.Input("app", options=_app_options(kind), tooltip="A Tanvo app. Each has preset looks tuned for it; connect the photos it asks for."),
            io.String.Input("details", default="", optional=True, tooltip="Optional: who or what it is for, e.g. a name, a date, an inside joke."),
            _seed(),
        ],
        outputs=outputs,
    )


class TanvoPhotoApp(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return _app_schema("image", "TanvoPhotoApp", "Tanvo Photo App",
                           "Run one of Tanvo's photo apps (pet portraits, figurines, old photo restoration, tattoo designs, headshots…) on your photos. " + KEY_HINT,
                           [io.Image.Output(display_name="images"), io.String.Output(display_name="url")])

    @classmethod
    def execute(cls, app: Dict[str, Any], seed: int, details: str = "") -> io.NodeOutput:
        with _errors():
            g = _run_app("image", app, details)
            return io.NodeOutput(_image_outputs(g), g.url)


class TanvoVideoApp(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return _app_schema("video", "TanvoVideoApp", "Tanvo Video App",
                           "Run one of Tanvo's trend video apps (duets, hugs, dances, product spins…) on your photos. Needs a Tanvo API key.",
                           [io.Video.Output(display_name="video"), io.String.Output(display_name="url")])

    @classmethod
    def execute(cls, app: Dict[str, Any], seed: int, details: str = "") -> io.NodeOutput:
        with _errors():
            g = _run_app("video", app, details)
            return io.NodeOutput(_video_output(g), g.url)


class TanvoSongApp(io.ComfyNode):
    @classmethod
    def define_schema(cls) -> io.Schema:
        return _app_schema("music", "TanvoSongApp", "Tanvo Song App",
                           "Run one of Tanvo's song apps (birthday songs, love songs…). Put the name and a few details in `details`. Needs a Tanvo API key.",
                           [io.Audio.Output(display_name="take_1"), io.Audio.Output(display_name="take_2"), io.String.Output(display_name="lyrics")])

    @classmethod
    def execute(cls, app: Dict[str, Any], seed: int, details: str = "") -> io.NodeOutput:
        with _errors():
            g = _run_app("music", app, details, photos=[])
            (a, b), lyrics = _song_outputs(g)
            return io.NodeOutput(a, b, lyrics)


class TanvoExtension(ComfyExtension):
    @override
    async def get_node_list(self) -> list[type[io.ComfyNode]]:
        return [TanvoImage, TanvoVideo, TanvoSong, TanvoPhotoApp, TanvoVideoApp, TanvoSongApp]


async def comfy_entrypoint() -> TanvoExtension:
    return TanvoExtension()
