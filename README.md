# YouTube desk

A local approval desk for one kind of video: face, full-frame screen recording, bottom captions with one colored word, a thumbnail made from your photo.

```bash
pip install -r requirements.txt
uvicorn studio.main:app --reload
```

Open http://127.0.0.1:8000

Copy `.env.example` to `.env` when you have keys. Load them yourself before starting the server (`set -a && source .env && set +a`). The desk runs with no keys: suggestions come from your brief, the thumbnail renders locally, and the price shows a $0 transcription line until Deepgram is connected.

## Accounts

| Account | When you need it |
|---|---|
| [Anthropic](https://console.anthropic.com/) or [OpenAI](https://platform.openai.com/) | Model-written hooks, offers, titles, and scripts. Without a key, the desk fills those from your brief with a fixed set of patterns. |
| [Deepgram](https://console.deepgram.com/) | Word-timed captions after you upload footage. |
| [Epidemic Sound](https://www.epidemicsound.com/) | Two music beds (hook, then the rest). A normal creator login is enough: download the tracks and attach them on the plan step. The partner API is only for letting the app search the catalog itself, and the free API tier cannot ship public videos. |
| [Cloudflare R2](https://www.cloudflare.com/developer-platform/r2/) | Later, when footage should live off this machine. The desk stores files in `data/projects` until then. |
| [Auphonic](https://auphonic.com/) | Optional voice leveling. The cut can start from the DJI Mic file without it. |
| [vidIQ](https://vidiq.com/) | Optional. Use it to check a title against videos that already outperformed their channel before you accept a topic. |

You do not need a generative video account. Screen recordings are files you attach. The thumbnail is rendered here from a still you upload.
