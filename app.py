"""
ARIA - Pre-Workshop Voice Interview
=====================================
USC College of Nursing AI Workshop | February 27, 2026

A voice-based pre-workshop survey using AI interviewing.
3 brief questions (~5 minutes) to gather participant perspectives before the workshop.

Features:
- Voice conversation with AI interviewer
- 5-minute target interviews with a hard stop at 8 minutes
- Automatic transcript capture with CSV export
- Graceful wrap-up when time runs low

Usage:
1. Set your OPENAI_API_KEY environment variable
2. Run: python app.py
3. Open http://127.0.0.1:7860 in your browser
4. Click "Start Interview" and allow microphone access
"""

import os
import json
import threading
import time
from datetime import datetime
from typing import Optional
import csv
import io
import base64

import nest_asyncio
import uvicorn
import requests
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse, Response
from fastapi.middleware.cors import CORSMiddleware

from dotenv import load_dotenv
load_dotenv()

nest_asyncio.apply()

app = FastAPI(title="USC AI Workshop - Pre-Workshop Interview")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"]
)

# =============================================================================
# CONFIGURATION
# =============================================================================

TARGET_INTERVIEW_DURATION_SECONDS = 300  # 5 minutes (target)
HARD_STOP_DURATION_SECONDS = 480         # 8 minutes (hard stop)
WRAP_UP_WARNING_SECONDS = 60             # Start wrap-up 60 seconds before target
TRANSCRIPT_SAVE_DIR = os.environ.get('TRANSCRIPT_DIR', './interview_transcripts')

os.makedirs(TRANSCRIPT_SAVE_DIR, exist_ok=True)

# Email notification settings (via Resend API)
NOTIFY_EMAIL = os.environ.get('NOTIFY_EMAIL', '')       # Your email (recipient)
RESEND_API_KEY = os.environ.get('RESEND_API_KEY', '')   # Resend.com API key

# Interview questions for USC Workshop (3 questions)
INTERVIEW_QUESTIONS = [
    {
        "id": 1,
        "question": "Can you tell me a bit about your experience with AI so far - whether in your teaching, research, clinical work, or personal life?",
        "completeness_signals": ["some description of experience level", "context about where/how they've used AI"],
        "suggested_followup": "What was that experience like for you?"
    },
    {
        "id": 2,
        "question": "Thinking about the workshop on AI for research and teaching - what are you most hoping to learn or take away?",
        "completeness_signals": ["specific learning goal or interest area", "connection to their work"],
        "suggested_followup": "Is there a specific challenge in your work where you think AI might help?"
    },
    {
        "id": 3,
        "question": "Is there anything else you'd like me to know as we prepare for the workshop? Any concerns about AI, specific topics you'd like covered, or questions you're hoping we'll address?",
        "completeness_signals": ["specific concern or topic raised", "or clear indication nothing to add"],
        "suggested_followup": None
    }
]

# =============================================================================
# INTERVIEW INSTRUCTIONS (System Prompt)
# =============================================================================

INTERVIEW_INSTRUCTIONS = """
You are ARIA, a friendly and professional AI interviewer conducting a brief pre-workshop survey for the February 27, 2026 workshop "AI for Research and Teaching" at the University of South Carolina, College of Nursing.

## CONTEXT

You are gathering brief perspectives from workshop participants before the session begins. This is a quick, informal survey - not a deep research interview. The goal is to help the workshop facilitator (Dr. Max Topaz from Columbia University) understand:
- Participants' current experience with AI
- What they hope to learn
- Any specific concerns or topics they want covered

## INTERVIEW STRUCTURE

You have exactly 3 questions to cover in about 5 minutes (with a hard stop at 8 minutes).

### Opening
Start with a warm, brief greeting:
"Hi! I'm ARIA, an AI research assistant. Thanks for taking a couple of minutes to chat with me before the workshop. I have just three quick questions for you. Let's get started!"

Then ask Question 1.

### Question 1: AI Experience
"Can you tell me a bit about your experience with AI so far - whether in your teaching, research, clinical work, or personal life?"

This is a warm-up question. Accept any level of experience - from "I've never used it" to "I use it daily." If they say very little, one gentle follow-up is fine: "What was that experience like?" or "What prompted you to try it?"

### Question 2: Learning Goals
"Thinking about the workshop on AI for research and teaching - what are you most hoping to learn or take away?"

Listen for specific interests. If they're vague, one follow-up: "Is there a specific challenge in your work where you think AI might help?"

### Question 3: Anything Else
"Is there anything else you'd like me to know as we prepare for the workshop? Any concerns about AI, specific topics you'd like covered, or questions you're hoping we'll address?"

Accept any response - they may have specific concerns or simply say no. No follow-up needed. After their response, thank them warmly and close.

## CRITICAL RULES

1. **Be conversational and warm** - this is a casual pre-workshop chat, not a formal interview
2. **Keep it brief** - aim for ~5 minutes total. Don't over-probe.
3. **One follow-up maximum per question** - if they give a short answer, one gentle nudge is fine, then move on
4. **Don't lecture** - you're listening, not teaching
5. **Be encouraging** - if they say they have no AI experience, reassure them that's totally fine and the workshop will cover all levels
6. **Keep your turns SHORT** - under 10 seconds of speaking for most turns
7. **Wait patiently** - give them time to think, don't rush

## STYLE

- Warm, friendly, casual but professional
- Brief acknowledgments: "That's great!" / "Interesting!" / "Got it!"
- One question at a time
- Use their name if they share it

## TIME MANAGEMENT

- You will receive a TIME_WARNING signal when ~60 seconds remain before the 5-minute mark
- When you get TIME_WARNING: If you haven't asked Question 3, ask it now briefly. Otherwise wrap up.
- You will receive a SOFT_TIME_UP signal at the 5-minute mark
- When you get SOFT_TIME_UP: Begin closing. Thank them and wish them a great workshop.
- You will receive a HARD_STOP signal at 8 minutes
- When you get HARD_STOP: Immediately thank them and end.

## CLOSING

After Question 3, close warmly:
"Thanks so much for sharing! Your input will help make the workshop even more relevant. Enjoy the session!"

## INTERNAL STATE TRACKING (do this silently)

Keep track of:
- Current question number (1, 2, or 3)
- Whether you've done a follow-up on the current question
- Move on after at most 1 follow-up per question
"""

# =============================================================================
# TRANSCRIPT STORAGE
# =============================================================================

interview_sessions = {}

class InterviewSession:
    def __init__(self, session_id: str):
        self.session_id = session_id
        self.start_time = datetime.now()
        self.entries = []
        self.current_question_id = 0

    def add_entry(self, speaker: str, text: str, entry_type: str = "response",
                  question_id: Optional[int] = None, is_clarifying: bool = False):
        entry = {
            "timestamp": datetime.now().isoformat(),
            "elapsed_seconds": round((datetime.now() - self.start_time).total_seconds(), 1),
            "speaker": speaker,
            "text": text,
            "entry_type": entry_type,
            "question_id": (question_id if question_id is not None else self.current_question_id),
            "is_followup": is_clarifying
        }
        self.entries.append(entry)
        return entry

    def get_filename_timestamp(self) -> str:
        return self.start_time.strftime("%Y%m%d_%H%M%S")

    def to_chronological_csv(self) -> str:
        output = io.StringIO()
        fieldnames = ["timestamp", "elapsed_seconds", "speaker", "question_id", "is_followup", "text"]
        writer = csv.DictWriter(output, fieldnames=fieldnames)
        writer.writeheader()
        for entry in self.entries:
            writer.writerow({
                "timestamp": entry.get("timestamp", ""),
                "elapsed_seconds": entry.get("elapsed_seconds", ""),
                "speaker": entry.get("speaker", ""),
                "question_id": entry.get("question_id", ""),
                "is_followup": entry.get("is_followup", False),
                "text": entry.get("text", "")
            })
        return output.getvalue()

    def save_to_disk(self):
        filename = f"usc_workshop_interview_{self.get_filename_timestamp()}.csv"
        filepath = os.path.join(TRANSCRIPT_SAVE_DIR, filename)
        with open(filepath, 'w', newline='', encoding='utf-8') as f:
            f.write(self.to_chronological_csv())
        return filepath


def send_notification_email(session: InterviewSession):
    """Send email notification with transcript via Resend API."""
    if not all([NOTIFY_EMAIL, RESEND_API_KEY]):
        print("Email not configured — skipping notification (need NOTIFY_EMAIL and RESEND_API_KEY)")
        return

    try:
        entry_count = len(session.entries)
        participant_entries = [e for e in session.entries if e.get('speaker') == 'participant']
        duration = session.entries[-1]['elapsed_seconds'] if session.entries else 0
        duration_min = int(duration // 60)
        duration_sec = int(duration % 60)

        # Build preview of participant responses
        preview_lines = []
        for e in participant_entries[:6]:
            text = e.get('text', '')
            if len(text) > 150:
                text = text[:150] + '...'
            preview_lines.append(f"Q{e.get('question_id', '?')}: {text}")
        preview_html = '<br>'.join(preview_lines) if preview_lines else '(no participant responses captured)'

        subject = f"ARIA Interview Completed — USC Workshop ({session.get_filename_timestamp()})"

        html_body = f"""
        <h2>New ARIA pre-workshop interview completed!</h2>
        <p><strong>Duration:</strong> {duration_min}:{duration_sec:02d}<br>
        <strong>Total exchanges:</strong> {entry_count}<br>
        <strong>Participant responses:</strong> {len(participant_entries)}</p>
        <h3>Response Preview</h3>
        <p style="background:#f5f5f5; padding:12px; border-radius:8px; font-size:14px;">
        {preview_html}
        </p>
        <p style="color:#888; font-size:12px;">Full transcript attached as CSV.<br>
        ARIA · USC College of Nursing AI Workshop · Feb 27, 2026</p>
        """

        # CSV as base64 attachment
        csv_content = session.to_chronological_csv()
        csv_b64 = base64.b64encode(csv_content.encode('utf-8')).decode('utf-8')
        filename = f"usc_workshop_interview_{session.get_filename_timestamp()}.csv"

        payload = {
            "from": "ARIA <onboarding@resend.dev>",
            "to": [NOTIFY_EMAIL],
            "subject": subject,
            "html": html_body,
            "attachments": [
                {
                    "filename": filename,
                    "content": csv_b64,
                }
            ]
        }

        r = requests.post(
            "https://api.resend.com/emails",
            headers={
                "Authorization": f"Bearer {RESEND_API_KEY}",
                "Content-Type": "application/json"
            },
            json=payload,
            timeout=10
        )

        if r.status_code in (200, 201):
            print(f"Email notification sent to {NOTIFY_EMAIL}")
        else:
            print(f"Resend API error ({r.status_code}): {r.text}")

    except Exception as e:
        print(f"Failed to send email notification: {e}")


# =============================================================================
# HTML FRONTEND
# =============================================================================

HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>ARIA - Pre-Workshop Interview | USC AI Workshop</title>
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=DM+Serif+Display&family=Source+Sans+3:wght@300;400;500;600;700&display=swap" rel="stylesheet">
    <style>
        :root {
            --garnet: #73000a;
            --garnet-deep: #5a0008;
            --garnet-light: #8e1a23;
            --cream: #faf6f1;
            --cream-dark: #efe8df;
            --ink: #1a1a1a;
            --ink-light: #4a4a4a;
            --ink-muted: #8a8a8a;
            --sage: #3d6b5e;
            --sage-light: #e8f0ec;
            --warm-white: #fffcf8;
            --border: rgba(115, 0, 10, 0.1);
            --shadow-sm: 0 1px 3px rgba(0,0,0,0.06), 0 1px 2px rgba(0,0,0,0.04);
            --shadow-md: 0 4px 20px rgba(0,0,0,0.08), 0 2px 8px rgba(0,0,0,0.04);
            --shadow-lg: 0 12px 40px rgba(0,0,0,0.12), 0 4px 16px rgba(0,0,0,0.06);
            --radius: 12px;
        }

        * { box-sizing: border-box; margin: 0; padding: 0; }

        body {
            font-family: 'Source Sans 3', -apple-system, BlinkMacSystemFont, sans-serif;
            background: var(--cream);
            min-height: 100vh;
            color: var(--ink);
            line-height: 1.6;
            -webkit-font-smoothing: antialiased;
        }

        /* --- Garnet top bar --- */
        .top-bar {
            background: var(--garnet);
            height: 4px;
            width: 100%;
            position: fixed;
            top: 0;
            left: 0;
            z-index: 100;
        }

        /* --- Page wrapper --- */
        .page {
            max-width: 720px;
            margin: 0 auto;
            padding: 3rem 1.5rem 4rem;
        }

        /* --- Header --- */
        .header-card {
            text-align: center;
            padding: 2.5rem 2rem 2rem;
            margin-bottom: 2rem;
            animation: fadeUp 0.6s ease-out;
        }

        .aria-badge {
            display: inline-flex;
            align-items: center;
            gap: 0.4rem;
            background: var(--garnet);
            color: white;
            font-size: 0.7rem;
            font-weight: 700;
            letter-spacing: 2.5px;
            text-transform: uppercase;
            padding: 0.35rem 1rem;
            border-radius: 100px;
            margin-bottom: 1.25rem;
        }

        .aria-badge .dot {
            width: 6px;
            height: 6px;
            background: rgba(255,255,255,0.5);
            border-radius: 50%;
        }

        h1 {
            font-family: 'DM Serif Display', Georgia, serif;
            color: var(--ink);
            font-size: 2rem;
            font-weight: 400;
            line-height: 1.2;
            margin-bottom: 0.75rem;
            letter-spacing: -0.02em;
        }

        .subtitle {
            color: var(--ink-light);
            font-size: 1rem;
            font-weight: 400;
        }

        .workshop-info {
            display: inline-flex;
            align-items: center;
            gap: 0.5rem;
            margin-top: 1rem;
            color: var(--garnet);
            font-size: 0.8rem;
            font-weight: 600;
            letter-spacing: 0.5px;
            text-transform: uppercase;
        }

        .workshop-info .sep {
            width: 3px;
            height: 3px;
            background: var(--garnet);
            border-radius: 50%;
            opacity: 0.5;
        }

        /* --- Cards --- */
        .card {
            background: var(--warm-white);
            border: 1px solid var(--border);
            border-radius: var(--radius);
            box-shadow: var(--shadow-sm);
            padding: 1.75rem;
            margin-bottom: 1.25rem;
            animation: fadeUp 0.6s ease-out both;
        }

        .card:nth-child(2) { animation-delay: 0.08s; }
        .card:nth-child(3) { animation-delay: 0.16s; }
        .card:nth-child(4) { animation-delay: 0.24s; }

        @keyframes fadeUp {
            from { opacity: 0; transform: translateY(12px); }
            to { opacity: 1; transform: translateY(0); }
        }

        /* --- Section labels --- */
        .section-label {
            font-family: 'DM Serif Display', Georgia, serif;
            color: var(--ink);
            font-size: 1.15rem;
            font-weight: 400;
            margin-bottom: 1.25rem;
            display: flex;
            align-items: center;
            gap: 0.6rem;
        }

        .section-label::before {
            content: '';
            display: block;
            width: 3px;
            height: 1.1em;
            background: var(--garnet);
            border-radius: 2px;
        }

        /* --- Instructions --- */
        .instructions {
            background: var(--cream);
            border-radius: 10px;
            padding: 1.25rem 1.5rem;
            margin-bottom: 1.75rem;
        }

        .instructions h3 {
            font-family: 'Source Sans 3', sans-serif;
            font-size: 0.75rem;
            font-weight: 700;
            letter-spacing: 1.5px;
            text-transform: uppercase;
            color: var(--garnet);
            margin-bottom: 0.6rem;
        }

        .instructions p {
            color: var(--ink-light);
            font-size: 0.92rem;
            line-height: 1.65;
        }

        /* --- Timer --- */
        .timer-bar {
            background: var(--cream-dark);
            border-radius: 100px;
            height: 6px;
            margin-bottom: 0.6rem;
            overflow: hidden;
        }

        .timer-progress {
            background: var(--garnet);
            height: 100%;
            width: 0%;
            border-radius: 100px;
            transition: width 1s linear;
        }

        .timer-progress.warning {
            background: linear-gradient(90deg, #c9302c, #e05040);
        }

        .timer-text {
            text-align: center;
            font-size: 0.85rem;
            font-weight: 600;
            color: var(--ink-muted);
            margin-bottom: 1.5rem;
            font-variant-numeric: tabular-nums;
            letter-spacing: 0.5px;
        }

        /* --- Status --- */
        .status {
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 0.5rem;
            margin-bottom: 0.75rem;
            font-size: 0.85rem;
            color: var(--ink-muted);
            font-weight: 500;
        }

        .status-dot {
            width: 8px;
            height: 8px;
            border-radius: 50%;
            background: #ccc;
            flex-shrink: 0;
        }

        .status-dot.ready { background: #e6a817; }
        .status-dot.active { background: var(--sage); animation: livePulse 2s ease-in-out infinite; }
        .status-dot.ended { background: #b0b0b0; }

        @keyframes livePulse {
            0%, 100% { opacity: 1; box-shadow: 0 0 0 0 rgba(61,107,94,0.4); }
            50% { opacity: 0.7; box-shadow: 0 0 0 6px rgba(61,107,94,0); }
        }

        /* --- Visualizer --- */
        .visualizer {
            display: flex;
            align-items: center;
            justify-content: center;
            gap: 3px;
            height: 48px;
            margin: 0.75rem 0 1.25rem;
        }

        .visualizer-bar {
            width: 3px;
            height: 14px;
            background: var(--garnet);
            border-radius: 100px;
            opacity: 0.25;
            transition: opacity 0.3s;
        }

        .visualizer.active .visualizer-bar {
            opacity: 1;
            animation: voiceBar 0.6s ease-in-out infinite;
        }

        .visualizer-bar:nth-child(1) { animation-delay: 0s; }
        .visualizer-bar:nth-child(2) { animation-delay: 0.12s; }
        .visualizer-bar:nth-child(3) { animation-delay: 0.24s; }
        .visualizer-bar:nth-child(4) { animation-delay: 0.36s; }
        .visualizer-bar:nth-child(5) { animation-delay: 0.48s; }

        @keyframes voiceBar {
            0%, 100% { height: 14px; }
            50% { height: 36px; }
        }

        /* --- Buttons --- */
        .controls {
            display: flex;
            justify-content: center;
            gap: 0.75rem;
            flex-wrap: wrap;
        }

        .btn {
            font-family: 'Source Sans 3', sans-serif;
            padding: 0.8rem 1.75rem;
            font-size: 0.9rem;
            font-weight: 600;
            border: none;
            border-radius: 100px;
            cursor: pointer;
            transition: all 0.25s cubic-bezier(0.4, 0, 0.2, 1);
            display: inline-flex;
            align-items: center;
            gap: 0.5rem;
            letter-spacing: 0.3px;
        }

        .btn-primary {
            background: var(--garnet);
            color: white;
            box-shadow: 0 2px 8px rgba(115, 0, 10, 0.25);
        }

        .btn-primary:hover {
            background: var(--garnet-deep);
            transform: translateY(-1px);
            box-shadow: 0 4px 16px rgba(115, 0, 10, 0.35);
        }

        .btn-primary:active { transform: translateY(0); }

        .btn-primary:disabled {
            opacity: 0.5;
            cursor: not-allowed;
            transform: none;
            box-shadow: none;
        }

        .btn-secondary {
            background: var(--cream);
            color: var(--ink-light);
            border: 1px solid var(--border);
        }

        .btn-secondary:hover {
            background: var(--cream-dark);
            color: var(--ink);
        }

        /* --- Current Question Display --- */
        .current-question-display {
            background: var(--cream);
            border: 1px solid rgba(115, 0, 10, 0.15);
            border-left: 3px solid var(--garnet);
            border-radius: 8px;
            padding: 1.25rem 1.5rem;
            margin-top: 1.75rem;
        }

        .current-question-label {
            font-size: 0.65rem;
            font-weight: 700;
            color: var(--garnet);
            text-transform: uppercase;
            letter-spacing: 2px;
            margin-bottom: 0.5rem;
        }

        .current-question-text {
            font-family: 'DM Serif Display', Georgia, serif;
            font-size: 1.05rem;
            color: var(--ink);
            line-height: 1.5;
            font-weight: 400;
        }

        /* --- Questions list --- */
        .questions-preview { margin-top: 0.5rem; }

        .question-item {
            padding: 1rem 1.25rem;
            background: var(--cream);
            border-radius: 10px;
            margin-bottom: 0.5rem;
            font-size: 0.88rem;
            line-height: 1.55;
            color: var(--ink-light);
            border: 1px solid transparent;
            transition: all 0.3s ease;
        }

        .question-item .q-label {
            font-weight: 700;
            font-size: 0.7rem;
            letter-spacing: 1px;
            text-transform: uppercase;
            color: var(--ink-muted);
            margin-bottom: 0.3rem;
        }

        .question-item.current {
            background: var(--warm-white);
            border-color: var(--garnet);
            box-shadow: var(--shadow-md);
        }

        .question-item.current .q-label {
            color: var(--garnet);
        }

        .question-item.completed {
            background: var(--sage-light);
            border-color: rgba(61,107,94,0.15);
        }

        .question-item.completed .q-label {
            color: var(--sage);
        }

        .question-item.completed::after {
            content: '';
        }

        /* --- Transcript --- */
        .transcript-container {
            max-height: 360px;
            overflow-y: auto;
            padding: 0.25rem;
        }

        .transcript-container::-webkit-scrollbar { width: 4px; }
        .transcript-container::-webkit-scrollbar-track { background: transparent; }
        .transcript-container::-webkit-scrollbar-thumb { background: var(--cream-dark); border-radius: 100px; }

        .transcript-entry {
            margin-bottom: 1rem;
            padding: 0.75rem 1rem;
            border-radius: 8px;
            background: var(--cream);
            animation: fadeUp 0.3s ease-out;
        }

        .transcript-entry:last-child { margin-bottom: 0; }

        .transcript-speaker {
            font-weight: 700;
            font-size: 0.7rem;
            letter-spacing: 1px;
            text-transform: uppercase;
            margin-bottom: 0.3rem;
        }

        .transcript-speaker.interviewer { color: var(--garnet); }
        .transcript-speaker.participant { color: var(--sage); }

        .transcript-text {
            color: var(--ink-light);
            font-size: 0.9rem;
            line-height: 1.6;
        }

        .transcript-meta {
            font-size: 0.7rem;
            color: var(--ink-muted);
            margin-top: 0.25rem;
        }

        .transcript-placeholder {
            color: var(--ink-muted);
            text-align: center;
            padding: 2.5rem 1rem;
            font-size: 0.88rem;
            font-style: italic;
        }

        .hidden { display: none; }

        /* --- Footer --- */
        .footer {
            text-align: center;
            padding: 2rem 0 1rem;
            font-size: 0.75rem;
            color: var(--ink-muted);
            letter-spacing: 0.3px;
        }

        .footer a {
            color: var(--garnet);
            text-decoration: none;
        }

        /* --- Mobile --- */
        @media (max-width: 600px) {
            .page { padding: 2rem 1rem 3rem; }
            h1 { font-size: 1.6rem; }
            .card { padding: 1.25rem; }
            .header-card { padding: 1.75rem 1.25rem 1.5rem; }
            .btn { padding: 0.7rem 1.25rem; font-size: 0.85rem; }
        }
    </style>
</head>
<body>
    <div class="top-bar"></div>

    <div class="page">
        <div class="header-card">
            <div class="aria-badge"><span class="dot"></span> ARIA</div>
            <h1>Pre-Workshop Interview</h1>
            <p class="subtitle">AI for Research &amp; Teaching</p>
            <div class="workshop-info">
                <span>Feb 27, 2026</span>
                <span class="sep"></span>
                <span>USC College of Nursing</span>
            </div>
        </div>

        <div class="card">
            <div class="instructions">
                <h3>How this works</h3>
                <p>Click "Start Interview" and allow microphone access. ARIA will ask you 3 brief questions
                   about your experience with AI and what you hope to learn at the workshop.
                   Speak naturally &mdash; this takes about 3&ndash;5 minutes.
                   Your responses help the facilitator tailor the workshop to your needs.</p>
            </div>

            <div class="timer-bar">
                <div class="timer-progress" id="timerProgress"></div>
            </div>
            <div class="timer-text" id="timerText">5:00 remaining</div>

            <div class="status">
                <div class="status-dot" id="statusDot"></div>
                <span id="statusText">Ready to start</span>
            </div>

            <div class="visualizer" id="visualizer">
                <div class="visualizer-bar"></div>
                <div class="visualizer-bar"></div>
                <div class="visualizer-bar"></div>
                <div class="visualizer-bar"></div>
                <div class="visualizer-bar"></div>
            </div>

            <div class="controls">
                <button class="btn btn-primary" id="startBtn">
                    Start Interview
                </button>
                <button class="btn btn-secondary hidden" id="stopBtn">
                    End Early
                </button>
                <button class="btn btn-secondary hidden" id="downloadBtn">
                    Download Transcript
                </button>
            </div>

            <div class="current-question-display hidden" id="currentQuestionDisplay">
                <div class="current-question-label">Current Question</div>
                <div class="current-question-text" id="currentQuestionText"></div>
            </div>
        </div>

        <div class="card">
            <div class="section-label">Interview Questions</div>
            <div class="questions-preview" id="questionsPreview">
                <div class="question-item" data-q="1">
                    <div class="q-label">1 &middot; Your AI Experience</div>
                    Can you tell me about your experience with AI so far &mdash; in teaching, research, clinical work, or personal life?
                </div>
                <div class="question-item" data-q="2">
                    <div class="q-label">2 &middot; Learning Goals</div>
                    What are you most hoping to learn or take away from the workshop?
                </div>
                <div class="question-item" data-q="3">
                    <div class="q-label">3 &middot; Anything Else</div>
                    Any concerns about AI, topics you'd like covered, or questions you're hoping we'll address?
                </div>
            </div>
        </div>

        <div class="card">
            <div class="section-label">Live Transcript</div>
            <div class="transcript-container" id="transcript">
                <p class="transcript-placeholder">Transcript will appear here during the interview&hellip;</p>
            </div>
        </div>

        <div class="footer">
            ARIA &middot; AI Research Interview Assistant &middot; Workshop by <a href="https://maxtopaz.com" target="_blank">Dr. Max Topaz</a>
        </div>
    </div>

    <script src="/app.js"></script>
</body>
</html>
"""

APP_JS = r"""// =====================================================================
// STATE
// =====================================================================
let pc = null;
let dc = null;
let audioEl = null;
let sessionId = null;
let startTime = null;
let timerInterval = null;
let softInterviewDuration = 300; // 5-minute target
let hardStopDuration = 480;      // 8-minute hard stop
let wrapUpWarningSeconds = 60;
let wrapUpSent = false;
let softTimeUpSent = false;
let hardStopSent = false;
let transcriptEntries = [];
let currentQuestionId = 1;

// Question texts for display
const questionTexts = {
    1: "Can you tell me about your experience with AI so far - in teaching, research, clinical work, or personal life?",
    2: "What are you most hoping to learn or take away from the workshop?",
    3: "Any concerns about AI, topics you'd like covered, or questions you're hoping we'll address?"
};

// =====================================================================
// UI HELPERS
// =====================================================================
function setStatus(status, text) {
    const dot = document.getElementById('statusDot');
    const statusText = document.getElementById('statusText');
    dot.className = 'status-dot ' + status;
    statusText.textContent = text;
}

function updateTimer() {
    if (!startTime) return;

    const elapsed = Math.floor((Date.now() - startTime) / 1000);
    const softRemaining = softInterviewDuration - elapsed;
    const hardRemaining = hardStopDuration - elapsed;

    const progressBar = document.getElementById('timerProgress');
    const timerTextEl = document.getElementById('timerText');

    if (elapsed <= softInterviewDuration) {
        const remaining = Math.max(0, softRemaining);
        const minutes = Math.floor(remaining / 60);
        const seconds = Math.floor(remaining % 60);
        timerTextEl.textContent = `${minutes}:${seconds.toString().padStart(2, '0')} remaining`;
        const progress = (elapsed / softInterviewDuration) * 100;
        progressBar.style.width = `${Math.min(100, progress)}%`;
    } else {
        const overtime = elapsed - softInterviewDuration;
        const otMin = Math.floor(overtime / 60);
        const otSec = Math.floor(overtime % 60);
        const hardLeft = Math.max(0, hardRemaining);
        const hardMin = Math.floor(hardLeft / 60);
        const hardSec = Math.floor(hardLeft % 60);
        timerTextEl.textContent = `Overtime ${otMin}:${otSec.toString().padStart(2, '0')} (wrapping up in ${hardMin}:${hardSec.toString().padStart(2, '0')})`;
        progressBar.style.width = '100%';
        progressBar.classList.add('warning');
    }

    if (softRemaining <= wrapUpWarningSeconds) {
        progressBar.classList.add('warning');
    }

    if (softRemaining <= wrapUpWarningSeconds && !wrapUpSent && dc && dc.readyState === 'open') {
        wrapUpSent = true;
        sendDataChannelMessage({
            type: 'conversation.item.create',
            item: {
                type: 'message',
                role: 'user',
                content: [{ type: 'input_text', text: '[TIME_WARNING: ~1 minute left before the 5-minute mark. If you haven\'t asked Question 3 yet, ask it now. Otherwise start wrapping up.]' }]
            }
        });
    }

    if (softRemaining <= 0 && !softTimeUpSent && dc && dc.readyState === 'open') {
        softTimeUpSent = true;
        sendDataChannelMessage({
            type: 'conversation.item.create',
            item: {
                type: 'message',
                role: 'user',
                content: [{ type: 'input_text', text: '[SOFT_TIME_UP: 5 minutes reached. Please wrap up warmly and thank them for their time.]' }]
            }
        });
    }

    if (hardRemaining <= 0 && !hardStopSent && dc && dc.readyState === 'open') {
        hardStopSent = true;
        sendDataChannelMessage({
            type: 'conversation.item.create',
            item: {
                type: 'message',
                role: 'user',
                content: [{ type: 'input_text', text: '[HARD_STOP: 8 minutes. Thank them briefly and end immediately.]' }]
            }
        });
        setTimeout(() => { if (pc) stopInterview(); }, 15000);
    }
}

function formatTimestamp(date) {
    return date.toLocaleTimeString('en-US', { hour12: false });
}

function addTranscriptEntry(speaker, text, meta = '') {
    const container = document.getElementById('transcript');
    if (transcriptEntries.length === 0) container.innerHTML = '';

    const now = new Date();
    const elapsed = startTime ? Math.round((now - startTime) / 1000) : 0;
    const minutes = Math.floor(elapsed / 60);
    const seconds = elapsed % 60;
    const timeStr = `${minutes}:${seconds.toString().padStart(2, '0')}`;

    const entry = document.createElement('div');
    entry.className = 'transcript-entry';
    entry.innerHTML = `
        <div class="transcript-speaker ${speaker.toLowerCase()}">${speaker} <span style="font-weight:normal; color:#999;">[${timeStr}]</span></div>
        <div class="transcript-text">${text}</div>
        ${meta ? `<div class="transcript-meta">${meta}</div>` : ''}
    `;
    container.appendChild(entry);
    container.scrollTop = container.scrollHeight;

    transcriptEntries.push({ speaker, text, timestamp: now.toISOString(), elapsed });
    saveTranscriptEntry(speaker, text);
}

async function saveTranscriptEntry(speaker, text) {
    if (!sessionId) return;
    try {
        await fetch(`/transcript/${sessionId}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                speaker: speaker.toLowerCase(),
                text: text,
                question_id: currentQuestionId
            })
        });
    } catch (err) {
        console.error('Failed to save transcript entry:', err);
    }
}

function sendDataChannelMessage(msg) {
    if (dc && dc.readyState === 'open') {
        dc.send(JSON.stringify(msg));
    }
}

// =====================================================================
// MAIN FUNCTIONS
// =====================================================================
async function startInterview() {
    const startBtn = document.getElementById('startBtn');
    const stopBtn = document.getElementById('stopBtn');
    const visualizer = document.getElementById('visualizer');

    startBtn.disabled = true;
    setStatus('ready', 'Connecting...');

    try {
        pc = new RTCPeerConnection();

        audioEl = document.createElement('audio');
        audioEl.autoplay = true;
        document.body.appendChild(audioEl);

        pc.ontrack = (e) => {
            audioEl.srcObject = e.streams[0];
            visualizer.classList.add('active');
        };

        const mic = await navigator.mediaDevices.getUserMedia({ audio: true });
        pc.addTrack(mic.getTracks()[0]);

        dc = pc.createDataChannel('oai-events');

        dc.onopen = () => {
            console.log('Data channel open');
            sendDataChannelMessage({ type: 'response.create' });
        };

        dc.onmessage = (e) => {
            try {
                const msg = JSON.parse(e.data);
                handleRealtimeEvent(msg);
            } catch (err) {
                console.error('Failed to parse message:', err);
            }
        };

        const offer = await pc.createOffer();
        await pc.setLocalDescription(offer);

        const resp = await fetch('/session', {
            method: 'POST',
            body: offer.sdp,
            headers: { 'Content-Type': 'application/sdp' }
        });

        if (!resp.ok) {
            const errText = await resp.text().catch(() => '');
            throw new Error(`Session handshake failed (HTTP ${resp.status}) ${errText ? '- ' + errText.slice(0, 200) : ''}`);
        }

        sessionId = resp.headers.get('X-Session-Id') || Date.now().toString();
        const answerSdp = await resp.text();
        const answer = { type: 'answer', sdp: answerSdp };
        await pc.setRemoteDescription(answer);

        startTime = Date.now();
        timerInterval = setInterval(updateTimer, 1000);
        updateTimer();

        setStatus('active', 'Interview in progress');
        startBtn.classList.add('hidden');
        stopBtn.classList.remove('hidden');

        showCurrentQuestionDisplay();
        highlightQuestion(1);

    } catch (err) {
        console.error('Start error:', err);
        setStatus('ended', 'Error: ' + err.message);
        startBtn.disabled = false;
    }
}

function handleRealtimeEvent(msg) {
    switch (msg.type) {
        case 'conversation.item.input_audio_transcription.completed':
            if (msg.transcript) {
                addTranscriptEntry('Participant', msg.transcript, `Q${currentQuestionId}`);
            }
            break;

        case 'response.output_audio_transcript.done':
        case 'response.audio_transcript.done':
            if (msg.transcript) {
                const t = msg.transcript.toLowerCase();

                // Detect which question ARIA is asking
                if (t.includes('experience with ai') || t.includes('experience with a.i') ||
                    (t.includes('experience') && (t.includes('teaching') || t.includes('research') || t.includes('clinical')))) {
                    currentQuestionId = 1;
                    highlightQuestion(1);
                } else if ((t.includes('hoping to learn') || t.includes('take away') || t.includes('learning goal')) ||
                           (t.includes('workshop') && t.includes('learn'))) {
                    currentQuestionId = 2;
                    highlightQuestion(2);
                } else if (t.includes('anything else') || t.includes('concerns about ai') ||
                           t.includes('topics you') || t.includes('questions you')) {
                    currentQuestionId = 3;
                    highlightQuestion(3);
                }

                addTranscriptEntry('Interviewer', msg.transcript, `Q${currentQuestionId}`);
            }
            break;

        case 'error':
            console.error('Realtime API error:', msg);
            addTranscriptEntry('System', `Error: ${msg.error?.message || 'Unknown error'}`, 'error');
            break;
    }
}

function highlightQuestion(qNum) {
    document.querySelectorAll('.question-item').forEach(el => {
        const q = parseInt(el.dataset.q);
        if (q < qNum) {
            el.classList.remove('current');
            el.classList.add('completed');
        } else if (q === qNum) {
            el.classList.add('current');
            el.classList.remove('completed');
        } else {
            el.classList.remove('current', 'completed');
        }
    });

    const questionText = questionTexts[qNum] || "Interview in progress...";
    document.getElementById('currentQuestionText').textContent = questionText;
}

function showCurrentQuestionDisplay() {
    const display = document.getElementById('currentQuestionDisplay');
    display.classList.remove('hidden');
    document.getElementById('currentQuestionText').textContent = questionTexts[1];
    highlightQuestion(1);
}

function hideCurrentQuestionDisplay() {
    document.getElementById('currentQuestionDisplay').classList.add('hidden');
}

function stopInterview() {
    if (timerInterval) { clearInterval(timerInterval); timerInterval = null; }
    if (dc) { dc.close(); dc = null; }
    if (pc) { pc.close(); pc = null; }
    if (audioEl) { audioEl.remove(); audioEl = null; }

    setStatus('ended', 'Interview complete');
    document.getElementById('visualizer').classList.remove('active');
    document.getElementById('stopBtn').classList.add('hidden');
    document.getElementById('downloadBtn').classList.remove('hidden');
    document.getElementById('startBtn').classList.remove('hidden');
    document.getElementById('startBtn').disabled = false;
    document.getElementById('startBtn').textContent = 'Start New Interview';

    hideCurrentQuestionDisplay();

    // Notify server interview is complete (triggers email notification)
    if (sessionId) {
        fetch(`/complete/${sessionId}`, { method: 'POST' }).catch(() => {});
    }
}

async function downloadCSV() {
    if (!sessionId) { alert('No interview session to download'); return; }
    try {
        const resp = await fetch(`/transcript/${sessionId}/csv`);
        if (!resp.ok) throw new Error('Download failed');
        const blob = await resp.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        const disposition = resp.headers.get('Content-Disposition');
        const filenameMatch = disposition && disposition.match(/filename=(.+)/);
        a.download = filenameMatch ? filenameMatch[1] : `interview_${sessionId}.csv`;
        a.click();
        URL.revokeObjectURL(url);
    } catch (err) {
        console.error('Download error:', err);
        alert('Failed to download transcript');
    }
}

document.addEventListener('DOMContentLoaded', () => {
    const startBtn = document.getElementById('startBtn');
    const stopBtn = document.getElementById('stopBtn');
    const downloadBtn = document.getElementById('downloadBtn');
    if (startBtn) startBtn.addEventListener('click', startInterview);
    if (stopBtn) stopBtn.addEventListener('click', stopInterview);
    if (downloadBtn) downloadBtn.addEventListener('click', downloadCSV);
    if (startBtn) startBtn.disabled = false;
});
"""


# =============================================================================
# ROUTES
# =============================================================================

SHUTDOWN_DATE = datetime(2026, 2, 25, 23, 59, 59)  # Feb 25, 2026 11:59 PM

CLOSED_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>ARIA - Interview Closed</title>
    <link href="https://fonts.googleapis.com/css2?family=DM+Serif+Display&family=Source+Sans+3:wght@400;600&display=swap" rel="stylesheet">
    <style>
        body { font-family: 'Source Sans 3', sans-serif; background: #faf6f1; min-height: 100vh; display: flex; align-items: center; justify-content: center; }
        .card { text-align: center; max-width: 480px; padding: 3rem 2rem; }
        .badge { display: inline-flex; align-items: center; gap: 0.4rem; background: #73000a; color: white; font-size: 0.7rem; font-weight: 700; letter-spacing: 2.5px; text-transform: uppercase; padding: 0.35rem 1rem; border-radius: 100px; margin-bottom: 1.5rem; }
        h1 { font-family: 'DM Serif Display', serif; font-size: 1.6rem; color: #1a1a1a; margin-bottom: 1rem; }
        p { color: #4a4a4a; line-height: 1.6; }
    </style>
</head>
<body>
    <div class="card">
        <div class="badge">ARIA</div>
        <h1>Pre-Workshop Interview Closed</h1>
        <p>Thank you for your interest! The pre-workshop interview period has ended. See you at the workshop on February 27!</p>
    </div>
</body>
</html>
"""


@app.get("/")
def index():
    if datetime.now() > SHUTDOWN_DATE:
        return HTMLResponse(CLOSED_HTML)
    return HTMLResponse(HTML)


@app.get("/app.js")
def app_js():
    return Response(content=APP_JS, media_type="application/javascript")


@app.post("/session")
async def create_session(request: Request):
    sdp_offer = (await request.body()).decode("utf-8", errors="ignore")
    if not sdp_offer.strip():
        return JSONResponse({"error": "Missing SDP offer"}, status_code=400)

    now = datetime.now()
    timestamp_str = now.strftime("%Y%m%d_%H%M%S")
    session_id = f"interview_{timestamp_str}"

    interview_sessions[session_id] = InterviewSession(session_id)

    api_key = (
        os.environ.get("OPENAI_API_KEY")
        or os.environ.get("OPENAI_APIKEY")
        or os.environ.get("OPENAI_KEY")
    )
    if not api_key:
        return JSONResponse(
            {"error": "OPENAI_API_KEY not set"},
            status_code=500,
        )

    session_config = {
        "type": "realtime",
        "model": "gpt-realtime",
        "instructions": INTERVIEW_INSTRUCTIONS,
        "output_modalities": ["audio"],
        "audio": {
            "input": {
                "transcription": {"model": "whisper-1", "language": "en"},
                "turn_detection": {
                    "type": "server_vad",
                    "threshold": 0.5,
                    "prefix_padding_ms": 500,
                    "silence_duration_ms": 2000,
                },
            },
            "output": {"voice": "sage"},
        },
    }

    try:
        if "\r\n" not in sdp_offer and "\n" in sdp_offer:
            sdp_offer = sdp_offer.replace("\n", "\r\n")

        files = {
            "sdp": (None, sdp_offer, "application/sdp"),
            "session": (None, json.dumps(session_config), "application/json"),
        }
        r = requests.post(
            "https://api.openai.com/v1/realtime/calls",
            headers={"Authorization": f"Bearer {api_key}"},
            files=files,
            timeout=30,
        )

        if r.status_code not in (200, 201):
            return JSONResponse(
                {"error": f"OpenAI API error ({r.status_code}): {r.text}"},
                status_code=502,
            )

        return Response(
            content=r.text,
            media_type="application/sdp",
            headers={"X-Session-Id": session_id},
        )

    except requests.RequestException as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.post("/transcript/{session_id}")
async def add_transcript_entry(session_id: str, entry: dict):
    if session_id not in interview_sessions:
        interview_sessions[session_id] = InterviewSession(session_id)

    session = interview_sessions[session_id]
    session.add_entry(
        speaker=entry.get("speaker", "unknown"),
        text=entry.get("text", ""),
        question_id=entry.get("question_id"),
        is_clarifying=entry.get("is_followup", False)
    )
    session.save_to_disk()
    return {"status": "ok"}


@app.get("/transcript/{session_id}")
def get_transcript(session_id: str):
    if session_id not in interview_sessions:
        return JSONResponse({"error": "Session not found"}, status_code=404)
    session = interview_sessions[session_id]
    return {"entries": session.entries}


@app.get("/transcript/{session_id}/csv")
def download_transcript_csv(session_id: str):
    if session_id not in interview_sessions:
        return JSONResponse({"error": "Session not found"}, status_code=404)
    session = interview_sessions[session_id]
    csv_content = session.to_chronological_csv()
    filename = f"usc_workshop_interview_{session.get_filename_timestamp()}.csv"
    return StreamingResponse(
        iter([csv_content]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


@app.post("/complete/{session_id}")
async def complete_interview(session_id: str):
    """Mark interview as complete, save final transcript, and send email notification."""
    if session_id not in interview_sessions:
        return JSONResponse({"error": "Session not found"}, status_code=404)

    session = interview_sessions[session_id]
    session.save_to_disk()

    # Send email in background thread to not block response
    threading.Thread(
        target=send_notification_email,
        args=(session,),
        daemon=True
    ).start()

    return {"status": "ok", "message": "Interview completed, notification sent"}


@app.get("/sessions")
def list_sessions():
    return {
        session_id: {
            "start_time": session.start_time.isoformat(),
            "entry_count": len(session.entries)
        }
        for session_id, session in interview_sessions.items()
    }


@app.get("/admin/transcripts")
def list_saved_transcripts():
    try:
        files = []
        for filename in os.listdir(TRANSCRIPT_SAVE_DIR):
            if filename.endswith('.csv'):
                filepath = os.path.join(TRANSCRIPT_SAVE_DIR, filename)
                stat = os.stat(filepath)
                files.append({
                    "filename": filename,
                    "size_bytes": stat.st_size,
                    "modified": datetime.fromtimestamp(stat.st_mtime).isoformat(),
                    "download_url": f"/admin/transcripts/{filename}"
                })
        files.sort(key=lambda x: x["modified"], reverse=True)
        return {"transcript_directory": TRANSCRIPT_SAVE_DIR, "total_files": len(files), "files": files}
    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


@app.get("/admin/transcripts/{filename}")
def download_saved_transcript(filename: str):
    if '..' in filename or '/' in filename or '\\' in filename:
        return JSONResponse({"error": "Invalid filename"}, status_code=400)
    filepath = os.path.join(TRANSCRIPT_SAVE_DIR, filename)
    if not os.path.exists(filepath):
        return JSONResponse({"error": "File not found"}, status_code=404)
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    return StreamingResponse(
        iter([content]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )


# =============================================================================
# RUN SERVER
# =============================================================================

def run():
    port = int(os.environ.get('PORT', 7860))
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")


if __name__ == "__main__":
    port = int(os.environ.get('PORT', 7860))
    print(f"""
====================================================================
  ARIA - Pre-Workshop Interview
  USC College of Nursing | AI for Research & Teaching
  February 27, 2026
--------------------------------------------------------------------
  Server starting at: http://0.0.0.0:{port}
  3 questions, ~5 minute interviews
  Make sure OPENAI_API_KEY is set
====================================================================
    """)

    thread = threading.Thread(target=run, daemon=True)
    thread.start()

    print(f"\nServer running! Open http://localhost:{port} in your browser.\n")
    print("Press Ctrl+C to stop.\n")

    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\nShutting down...")
