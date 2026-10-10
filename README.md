# Tanvo for ComfyUI

[![license](https://img.shields.io/badge/license-MIT-blue)](LICENSE)

ComfyUI nodes for [Tanvo](https://tanvo.ai/?utm_source=github&utm_medium=referral), an AI image, video and music studio. Generate images on Nano Banana, Seedream, GPT Image, Qwen and Grok Imagine, video on Kling, Veo, Seedance, Wan, LTX and MiniMax (most with sound), and songs on Suno V6. Or run one of Tanvo's 80 ready-made apps (pet portraits, figurines, old photo restoration, tattoo designs, trend videos, birthday songs) on your own photos, with the same tuned prompts and settings as the website.

Everything runs on Tanvo's servers. No GPU, no model downloads. The free model works without an account.

[Install](#install) · [API key](#api-key) · [Nodes](#nodes) · [Example workflows](#example-workflows) · [Pricing](#pricing) · [Troubleshooting](#troubleshooting)

## Install

**ComfyUI Manager:** search for **Tanvo** and install, then restart ComfyUI.

**Comfy CLI:**

```bash
comfy node install tanvo
```

**By hand:**

```bash
cd ComfyUI/custom_nodes
git clone https://github.com/tanvoai/ComfyUI-Tanvo
pip install -r ComfyUI-Tanvo/requirements.txt
```

Needs ComfyUI 0.8 or newer. The only dependency is the [`tanvo`](https://pypi.org/project/tanvo/) Python client (no other packages).

## API key

Without a key the nodes use Tanvo's free tier: the free image model (`studio-image-v1`), watermarked, with a small daily allowance. For every other model:

1. Sign in at [tanvo.ai](https://tanvo.ai/?utm_source=github&utm_medium=referral) (new accounts start with free credits).
2. Create a key under [Settings → API keys](https://tanvo.ai/settings/apikeys?utm_source=github&utm_medium=referral). It starts with `sk_`.
3. In ComfyUI open **Settings → Tanvo** and paste it. The next run uses it; no restart needed.

The key is kept in ComfyUI's own settings file, never in your workflows, so you can share workflow files and images safely. You can set `TANVO_API_KEY` in the environment instead; it takes precedence.

## Nodes

All nodes are under **Tanvo** in the node library (double-click the canvas and type "tanvo").

| Node | What it does | Outputs |
|---|---|---|
| **Tanvo Image** | Text to image, or edit and combine images. Connect up to 8 image inputs (every image of a batch counts). | `images`, `url` |
| **Tanvo Video** | Text to video, or animate a `start_image`, optionally ending on an `end_image`. Most models add sound. | `video`, `url` |
| **Tanvo Song** | A song in two takes: describe it, sing your own lyrics, or go instrumental. | `take_1`, `take_2`, `lyrics` |
| **Tanvo Photo App** | One of Tanvo's photo apps with its preset looks. Pick the app and the photo inputs it needs appear. | `images`, `url` |
| **Tanvo Video App** | Trend video apps (duets, hugs, dances, product spins, video to cartoon). Some take a video clip. | `video`, `url` |
| **Tanvo Song App** | Birthday songs, love songs. Put the name and a few details in `details`. | `take_1`, `take_2`, `lyrics` |

Picking a model shows only that model's settings (aspect, resolution, duration, sound, Veo's quality tier), so you cannot pick a combination it does not support. The `url` output is the hosted file, handy for passing on to other services.

**Seed.** Tanvo does not use it. It is there so you can re-run a node on purpose: ComfyUI skips nodes whose inputs have not changed, so queueing the same workflow twice does not pay twice. Change the seed (or set it to increment) to get a new result.

**Cancelling** stops ComfyUI from waiting. The run itself carries on at Tanvo and lands in your [history](https://tanvo.ai/history?utm_source=github&utm_medium=referral); failed runs are refunded automatically.

New models and apps appear after restarting ComfyUI: the nodes read Tanvo's catalogue at startup and fall back to the copy they ship with when offline.

## Example workflows

In the templates browser they are listed under the folder Tanvo is installed in (`tanvo` or `ComfyUI-Tanvo`). The JSON files are also in [`example_workflows`](example_workflows).

- **Text to image (free)**: Tanvo Image → Save Image. Works without a key.
- **Pet portrait app**: Load Image → Tanvo Photo App (Pet Portrait Generator, Royal look) → Save Image.

## Pricing

Runs cost credits at the same prices as the website; the [pricing page](https://tanvo.ai/pricing?utm_source=github&utm_medium=referral) lists every model. Failed runs are refunded. The free model costs nothing.

## Troubleshooting

| Message | Fix |
|---|---|
| `needs_api_key`, `allowance` or `anon_ip_daily` | Add a key under Settings → Tanvo. |
| `credits` | Top up at [tanvo.ai/pricing](https://tanvo.ai/pricing?utm_source=github&utm_medium=referral). |
| `invalid: … options.resolution` | That model does not take the setting; pick another value. |
| `busy` | Your plan's parallel runs are in use; wait for one to finish. |
| Nodes missing after install | Check the ComfyUI console for errors mentioning Tanvo, and that ComfyUI is 0.8 or newer. |

## Development

```bash
python -m unittest tests/test_core.py                     # no ComfyUI needed
COMFYUI_PATH=~/ComfyUI ~/ComfyUI/.venv/bin/python -m unittest tests/test_e2e.py
```

The end-to-end test runs every node in a real ComfyUI against a local stand-in for the Tanvo API (link this folder into `custom_nodes` first). `python scripts/update_catalog.py` refreshes the bundled catalogue.

## Also from Tanvo

- [n8n-nodes-tanvo](https://github.com/tanvoai/n8n-nodes-tanvo): Tanvo in n8n workflows
- [tanvo-mcp](https://github.com/tanvoai/tanvo-mcp): Tanvo inside Claude, Cursor and other MCP clients
- [tanvo-python](https://github.com/tanvoai/tanvo-python), [tanvo-js](https://github.com/tanvoai/tanvo-js), [tanvo-go](https://github.com/tanvoai/tanvo-go): API clients

## License

[MIT](LICENSE)
