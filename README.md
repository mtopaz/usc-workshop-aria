# ARIA — Pre-Workshop Voice Interviewer
## USC College of Nursing AI Workshop | February 27, 2026

A voice-based AI interviewer that conducts brief (~5 min) pre-workshop surveys with participants using OpenAI's Realtime API and WebRTC.

### Live URLs

- **Interviewer app:** https://usc-workshop-aria-production.up.railway.app
- **View all transcripts:** https://usc-workshop-aria-production.up.railway.app/admin/transcripts
- **Workshop page (links to ARIA):** https://www.maxtopaz.com/workshop/usc-feb2026

### How It Works

1. Participant visits the URL and clicks "Start Interview"
2. Browser requests microphone access
3. ARIA (AI interviewer) asks 3 questions via voice:
   - **Q1:** Your experience with AI (teaching, research, clinical, personal)
   - **Q2:** What you hope to learn at the workshop
   - **Q3:** Anything else (concerns, topics, questions)
4. Transcript is saved server-side as CSV
5. Participant can also download their own transcript via the "Download Transcript" button

### Auto-Expiry

The app automatically shows a "closed" page after **February 25, 2026 at 11:59 PM**. No manual shutdown needed. To turn it off earlier, either:
- Remove the public domain in Railway Settings → Networking
- Or delete the service entirely

### Retrieving Transcripts

**Option 1 — Web browser:**
Visit https://usc-workshop-aria-production.up.railway.app/admin/transcripts
Click any file to download the CSV.

**Option 2 — Claude Code:**
Ask Claude to fetch from `/admin/transcripts` and download all CSVs.

**Important:** Railway's filesystem is ephemeral — files are lost on redeploy. Download transcripts before pushing any code changes.

### Architecture

```
Browser (WebRTC + mic)
    ↕
Railway (FastAPI server, app.py)
    ↕
OpenAI Realtime API (gpt-realtime model, Whisper transcription, "sage" voice)
```

Single-file Python app (`app.py`) with embedded HTML/CSS/JS. No database — transcripts saved as CSV files on disk.

### Tech Stack

- **Backend:** FastAPI + Uvicorn
- **Voice AI:** OpenAI Realtime API (WebRTC, server-side VAD, 2s silence threshold)
- **Transcription:** Whisper-1
- **Hosting:** Railway (auto-deploys from GitHub)
- **Repo:** github.com/mtopaz/usc-workshop-aria

### Railway Environment Variables

| Variable | Value | Required |
|---|---|---|
| `OPENAI_API_KEY` | OpenAI API key with Realtime API access | Yes |
| `NOTIFY_EMAIL` | Email for interview notifications | Optional |
| `RESEND_API_KEY` | Resend.com API key (for email delivery) | Optional |

Note: `PORT` is set automatically by Railway (currently 8080).

### Email Notifications (Optional)

Railway blocks SMTP ports, so email uses the Resend API (HTTP-based). To enable:
1. Sign up at resend.com (free tier: 100 emails/day)
2. Get your API key
3. Set `RESEND_API_KEY` and `NOTIFY_EMAIL` in Railway variables

Each completed interview sends an email with a response preview and CSV attachment.

### Local Development

```bash
# Create .env file
echo "OPENAI_API_KEY=sk-your-key" > .env

# Install dependencies
pip install -r requirements.txt

# Run
python app.py
# Open http://localhost:7860
```

### Files

```
app.py              # Complete application (FastAPI + HTML/CSS/JS)
requirements.txt    # Python dependencies
Procfile            # Railway start command
railway.json        # Railway build/deploy config
.gitignore          # Excludes .env, transcripts, cache
```

### Original ARIA Project

This is adapted from the full ARIA interviewer used for the Columbia University multimodal AI workshop (January 2026). The original had 5 deep research questions and 12-minute interviews. This version is simplified to 3 survey questions and ~5 minutes.

Original source: `../ARIA-AI-Responsive-Interview-Assistant-for-Qualitative-Health-Research-main/`
