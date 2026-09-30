# YouTube desk

An approval desk for one kind of video: face, full-frame screen recording, bottom captions with one colored word, a thumbnail made from your photo.

The app runs on Railway. Supabase stores the project record and the footage. After you approve the price, ffmpeg cuts the full video. Silence has to last about two seconds before it is removed, and a little air is left at each join. The word “cut” still drops a retake. The mic locks to the clap, captions sit on the bottom, and cards and screen recordings replace the face. A built-in hit plays on each number card, a click on each screen label, and a riser into the first card. Face cuts have no whoosh. Attach your own files on those three slots to replace the built-in sounds. Two music files, if you attach them, duck under the voice. A Deepgram key times those captions to the words. Without it, captions follow the script. Supabase stores the file. It does not render it.

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
| `SUPABASE_DB_URL` | Session-pooler URL for the `youtube_desk` login |
| `ANTHROPIC_API_KEY` or `OPENAI_API_KEY` | Optional model drafts |
| `DEEPGRAM_API_KEY` | Optional word captions |

There is no desk password. The browser does not send a token.

`studio/schema.sql` is the YouTube schema on the database you already pay for. It creates `youtube.projects` and `youtube.files`. Footage is a Postgres large object owned by `youtube_desk`. That login cannot read `public.leads` or call the lead export functions. The service role and the anon key stay off Railway, because those keys can read the lead tables. Scratch files for ffmpeg live in `/tmp/desk` and are written back through the same login.

This does not add a Supabase project. A new project would be another $10 a month. The paused project stays paused.

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

You do not need a generative video account. Screen recordings are files you attach. The thumbnail is rendered from a still you upload. Approving the price with a camera file renders the full cut.
