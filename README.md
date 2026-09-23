# YouTube desk

An approval desk for one kind of video: face, full-frame screen recording, bottom captions with one colored word, a thumbnail made from your photo.

The app runs on Railway. Supabase stores the project record and the footage. ffmpeg on Railway makes the thumbnail and the 8-second caption preview. Supabase replaces object storage. It does not render the video.

```bash
pip install -r requirements.txt
uvicorn studio.main:app --reload
```

Open http://127.0.0.1:8000 for a local check. Copy `.env.example` to `.env` and load it before starting (`set -a && source .env && set +a`). With no keys, suggestions come from the brief and files stay in `data/projects`.

## Cloud

Railway builds `Dockerfile` (Python, ffmpeg) and listens on `$PORT`. Health check is `GET /api/vendors`.

Set these on the Railway service:

| Variable | Purpose |
|---|---|
| `SUPABASE_URL` | Project URL |
| `SUPABASE_SERVICE_ROLE_KEY` | Server-only. Never put this in the browser. |
| `DESK_TOKEN` | Shared password. Without it, a public URL can accept uploads. |
| `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` | Optional model drafts |
| `DEEPGRAM_API_KEY` | Optional word captions |

Apply `studio/schema.sql` on a dedicated Supabase project. It creates `desk_projects` and a private `desk` bucket. Row level security is on and there are no anon policies; the service role bypasses them. Scratch files for ffmpeg live in `/tmp/desk` and are uploaded back to the bucket.

A new project on the SalesGlider org is $10 a month. The desk is not pointed at the existing campaign or maps databases.

## Accounts

| Account | When you need it |
|---|---|
| [Railway](https://railway.com/) | Hosts the desk and ffmpeg. |
| [Supabase](https://supabase.com/) | Project JSON and footage. Replaces S3. |
| [Anthropic](https://console.anthropic.com/) or [OpenAI](https://platform.openai.com/) | Model-written hooks, offers, titles, and scripts. Without a key, the desk fills those from the brief. |
| [Deepgram](https://console.deepgram.com/) | Word-timed captions after footage is uploaded. |
| [Epidemic Sound](https://www.epidemicsound.com/) | Two music beds. A creator login is enough: download the tracks and attach them. The free partner API cannot ship public videos. |
| [Auphonic](https://auphonic.com/) | Optional voice leveling. The cut can start from the DJI Mic file without it. |
| [vidIQ](https://vidiq.com/) | Optional title check before accepting a topic. |

You do not need a generative video account. Screen recordings are files you attach. The thumbnail is rendered from a still you upload. The full timeline is quoted, then saved as `edit-plan.json`. The only render today is the 8-second preview.
