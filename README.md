# Kavach — MVP

> **Compress the delivery, not the knowledge.**

**Live demo:** https://main.d1ex6vljuszbzw.amplifyapp.com · API: https://di1ubb11ugmj1.cloudfront.net/health
(Amplify Hosting → CloudFront → EC2/Docker → S3 · DynamoDB · Polly · Bedrock, region ap-south-1)

Kavach turns an educational PDF (≤ 60 pages) into a learning path of 30–60 second
**whiteboard revision shorts** with AI narration, PDF source references, quick-check
questions, follow-up Q&A and feedback-driven regeneration.

```
PDF ─▶ S3 ─▶ text extraction + suitability check ─▶ Bedrock (Brain AI) ─▶ topic map / learning order
     ─▶ student picks topics ─▶ Bedrock teaching plan JSON ─▶ Polly narration + whiteboard renderer
     ─▶ FFmpeg MP4 ─▶ S3 ─▶ React feed (video · sources · quick check · ask · feedback)
```

Everything runs **end to end on a laptop with no AWS account** (mock Brain, local files,
Windows speech or silent narration) and switches to **S3 + DynamoDB + Bedrock + Polly** with
environment variables.

---

## Repository layout

```
backend/                 FastAPI service + pipeline (Python 3.11+)
  app/main.py            REST API (spec §26) + Lambda handler (Mangum)
  app/pipeline.py        analyze → topic map → plan → narrate → render → MP4
  app/pdf/extract.py     pypdfium2 text extraction, headings, page thumbnails, OCR hook, suitability check
  app/brain/prompts.py   Brain AI prompts = the renderer contract in prose
  app/brain/client.py    Claude in Amazon Bedrock (Anthropic SDK Mantle client) · boto3 Converse fallback · mock
  app/brain/planner.py   validation/normalisation of topic maps and teaching plans
  app/brain/mock.py      offline brain (uses the PDF text) + hand-written TCP handshake demo plan
  app/render/renderer.py deterministic whiteboard renderer (12 primitives, 8 animations)
  app/tts/synth.py       Amazon Polly · Windows SAPI · silent mock
  app/video/ffmpeg.py    bundled FFmpeg (imageio-ffmpeg) or system ffmpeg
  app/storage.py         S3 or local filesystem (spec §24 key layout)
  app/db.py              DynamoDB or local JSON (spec §25 tables)
  app/models.py          Teaching Plan JSON contract (spec §10) + API models
  scripts/make_demo_pdf.py   generates the Computer Networks demo PDF
  scripts/smoke_test.py      runs the whole pipeline in-process (local or AWS)
  scripts/aws_setup.py       creates S3 bucket + DynamoDB tables, checks Bedrock/Polly, prints IAM policy
  Dockerfile             container for App Runner / ECS / Lambda
frontend/                Vite + React + TypeScript (Amplify-hostable)
  src/components/        Upload · Analysis · TopicMap · ReelFeed/ReelCard · QuickCheck
```

---

## 1. Run locally (no AWS)

**Prereqs:** Python 3.11+, Node 18+. FFmpeg is bundled via pip; no system install needed.

```powershell
# backend
cd backend
python -m pip install -r requirements.txt
copy .env.example .env            # defaults are already local mode
.\run_local.ps1                   # or: python -m uvicorn app.main:app --port 8000
# (macOS/Linux: ./run_local.sh)

# frontend (second terminal)
cd frontend
npm install
npm run dev                       # http://localhost:5173  (API: http://localhost:8000)
```

Open http://localhost:5173, upload `backend/assets/demo_computer_networks.pdf`
(created on first run) and click through Upload → Analyze → Topics → Shorts.

In local mode:
- **Brain** = `mock` — builds the topic map from the PDF's headings and generic lessons from
  its text; the *TCP Three-Way Handshake* topic gets a hand-written, high-quality plan so the
  renderer can be demoed properly.
- **Narration** = `auto` — Windows built-in speech if available, otherwise timed silence.
- Files go to `backend/data/storage/`, metadata to `backend/data/kavach_db.json`.

Quick check without the UI:

```powershell
cd backend
python scripts/smoke_test.py        # analyses the demo PDF and renders the handshake shorts
```

---

## 2. Run with real AWS services

```powershell
cd backend
$env:AWS_REGION="us-east-1"
$env:KAVACH_S3_BUCKET="<globally-unique-bucket>"
python scripts/aws_setup.py         # creates bucket + tables, tests Bedrock and Polly, prints IAM policy
```

Then set in `backend/.env`:

```
KAVACH_MODE=aws
AWS_REGION=us-east-1
KAVACH_S3_BUCKET=<bucket>
KAVACH_BEDROCK_MODEL=global.anthropic.claude-opus-4-6-v1   # Bedrock inference profile id
KAVACH_POLLY_VOICE=Matthew
```

and start the backend as above. Credentials come from the usual AWS chain
(`aws configure`, env vars, or an IAM role). Validate with
`python scripts/smoke_test.py` (prints presigned S3 URLs for the MP4s).

Notes
- **Model access**: Anthropic models need a one-time use-case form. The console page was
  retired; submit it through the API (`bedrock:PutUseCaseForModelAccess`, JSON with
  `companyName, companyWebsite, industryOption, otherIndustryOption, intendedUsers ("0"/"1"),
  useCases`) or from the Model catalog playground. Until then Bedrock returns "Model use
  case details have not been submitted". Newer tiers (Opus 4.7+, Sonnet 5, Fable) may show
  "not available for this account" — that needs an AWS request.
- **New-account quotas**: fresh AWS accounts get near-zero Bedrock quotas ("Too many tokens
  per day") even though defaults are millions. Request increases for the per-minute quotas in
  Service Quotas and open a *Service limit increase* support case for the per-day token quota.
  Quotas also differ per region (`KAVACH_BEDROCK_REGION`); `us-west-2` had the best defaults.
- Stopgap while quotas are pending: `KAVACH_BRAIN=anthropic` + `ANTHROPIC_API_KEY` runs the
  same prompts on the first-party Claude API (`claude-opus-5`).
- The Brain talks to Bedrock through the **Converse API** (boto3, streaming, adaptive
  thinking) using inference-profile ids like `global.anthropic.claude-opus-4-6-v1`
  (best quality) or `global.anthropic.claude-sonnet-4-6` (faster). Accounts with Claude
  in Amazon Bedrock (Mantle) access can set `KAVACH_BRAIN=bedrock` and an
  `anthropic.claude-*` id to use the Anthropic SDK client instead.
- `aws login` sessions need `pip install "botocore[crt]"` (already in requirements).
- Pieces can be mixed: e.g. `KAVACH_MODE=local` + `KAVACH_BRAIN=bedrock` + `KAVACH_TTS=polly`
  keeps files on disk but uses real AI.
- `KAVACH_BRAIN_EFFORT` (`low|medium|high`) trades quality for latency per topic.

---

## 3. Deploy on AWS (one command each)

**Backend → EC2 (Docker) + CloudFront (HTTPS).** No local Docker needed: the script zips
`backend/`, uploads it to S3, creates an instance role (S3/DynamoDB/Bedrock/Polly), a
security group and a t3.medium Amazon Linux 2023 instance whose user-data builds and runs
the container; then puts CloudFront in front so the HTTPS frontend can call it.

```powershell
cd backend
python scripts/deploy_ec2.py          # prints http://<ec2> and https://<cloudfront>
python scripts/deploy_ec2.py --status
```

Re-running replaces the instance with the current code (state lives in S3/DynamoDB, so
nothing is lost). Logs: `/var/log/kavach-init.log` and `docker logs kavach` via SSM
Session Manager.

**Frontend → Amplify Hosting (manual zip deployment, no GitHub connection needed).**

```powershell
cd frontend
python deploy_amplify.py --api https://<cloudfront-domain>
```

Builds with `VITE_API_URL`, uploads `dist/` and prints `https://main.<app>.amplifyapp.com`.

**Alternatives:** the Dockerfile also runs on App Runner / ECS Fargate (App Runner throttles
CPU between requests, which stalls background rendering - prefer ECS or EC2), and
`app.main.handler` (Mangum) lets the API run as a Lambda container image behind API Gateway
with generation moved to a second Lambda / Step Functions.

---

## API (spec §26)

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/documents` | upload PDF (multipart `file`) → `document_id`, analysis starts |
| GET | `/documents/{id}` | status (`UPLOADED…COMPLETED/FAILED`), suitability, estimates |
| GET | `/documents/{id}/topics` | topic map + recommended learning order |
| POST | `/documents/{id}/topics` | `{selected_topic_ids, order, added_topics}` |
| POST | `/documents/{id}/generate` | `{ai_enhanced}` → shorts generated in background |
| GET | `/documents/{id}/reels` | reel feed with video URLs, sources, quick checks |
| GET | `/reels/{reel_id}` | one reel |
| POST | `/reels/{reel_id}/regenerate` | `{feedback: didnt_understand\|too_fast\|too_difficult\|explain_differently}` |
| POST | `/reels/{reel_id}/ask` | `{question, ai_enhanced}` → grounded answer with page refs |
| POST | `/reels/{reel_id}/feedback` | record 👍 etc. |
| GET | `/reels/{reel_id}/plan` | the Teaching Plan JSON behind a reel |

---

## Teaching Plan → renderer contract

The Brain returns structured JSON (`app/models.py: TeachingPlan`). Each part is a list of
scenes; each scene has narration plus elements in a 0–100 coordinate space:

```json
{"narration": "The client starts by sending a SYN segment...",
 "clear": false,
 "elements": [
   {"id": "client", "type": "BOX", "label": "Client", "x": 18, "y": 30, "w": 18, "h": 11, "color": "blue", "animation": "DRAW"},
   {"id": "syn", "type": "ARROW", "from": "client", "to": "server", "label": "1. SYN (seq = x)", "animation": "ARROW_FLOW"}
 ]}
```

Primitives: `TEXT BOX CIRCLE LINE ARROW DIAGRAM EQUATION TABLE HIGHLIGHT IMAGE FLOW TIMELINE`
Animations: `DRAW WRITE FADE_IN FADE_OUT MOVE HIGHLIGHT ARROW_FLOW SEQUENTIAL_REVEAL`

The renderer is deterministic: narration is synthesised per scene, scene length comes from
the audio, element animations are scheduled across it, frames are drawn with Pillow
(hand-drawn wobble strokes, handwriting font) and piped to FFmpeg with the narration track.
Shorts are portrait 9:16 (720x1280) by default; set `KAVACH_VIDEO_WIDTH/HEIGHT` for other sizes.

---

## Beyond the spec

- **Synced captions**: Polly word-level speech marks drive a caption band with the spoken
  word highlighted (shorts are often watched muted); offline backends estimate timings.
- **Vision-grounded Brain**: the PDF itself is attached to the topic-map call and page
  images of the source pages to each teaching-plan call, so diagrams, tables and equations
  that text extraction loses still make it into the lesson (`KAVACH_BRAIN_VISION`).
- **Parallel planning**: several topics are planned on Bedrock concurrently while earlier
  ones render (`KAVACH_PLAN_CONCURRENCY`).
- **Combined lesson** (`POST /documents/{id}/combine`): all shorts stitched into one MP4.
- **Revision notes** (`GET /documents/{id}/notes.md`): scripts, sources, AI-added context
  and quick checks as Markdown.
- Progress bar burned into every short; `backend/.env` is loaded automatically.

## What we learned (First Commit, 17–20 Sep 2026)

**Product**
- *Compress the delivery, not the knowledge* only works if the AI is forced to plan, not
  summarise. Asking for a **teaching plan JSON** (concepts → prerequisites → learning order
  → scenes → primitives → quick check) produced far better lessons than "make a video about
  this PDF". The renderer contract in `app/brain/prompts.py` is the most important file.
- Students watch shorts muted: **synced captions** (Polly word-level speech marks) turned
  out to matter as much as the drawings. Polly strips punctuation from marks, so we map
  byte offsets back to the original text to keep sentence boundaries.
- Keeping AI-added context visibly separate from the PDF's own content is cheap to build
  and the feature students trusted most.

**AWS**
- **Amazon Bedrock model access moved from a console page to an API.** The one-time
  Anthropic use-case form is now `bedrock:PutUseCaseForModelAccess` with an undocumented
  JSON body (`intendedUsers` is `"0"`/`"1"`, not a string label). We reverse-engineered it
  from `ValidationException`s.
- **New AWS accounts get near-zero Bedrock quotas** ("Too many tokens per day") on *every*
  vendor's models, even though `get_aws_default_service_quota` reports millions. Quotas are
  also **per region** — Mumbai showed 0 for Claude 4.6 while us-west-2 had 10k RPM — so the
  Bedrock region is a separate setting from the data region. Per-minute increases are
  self-service; per-day ones need a support case.
- **Deterministic video beats generative video** for education: Pillow + FFmpeg render a
  40 s short in ~6 s on a t3.medium, every run is reproducible, and there is nothing to
  hallucinate in the visuals.
- **App Runner throttles CPU between requests**, which stalls background rendering; a plain
  EC2 container with an instance role (no static keys) was the simplest reliable host, with
  CloudFront in front so the HTTPS Amplify site can call it.
- Presigned S3 URLs change on every poll; the React player has to pin the video `src` or
  playback restarts every 3 seconds.
- A Linux-only `subprocess.communicate()` bug (it flushes a stdin pipe you already closed)
  made every render "fail" on the server while succeeding on Windows — caught only by
  running the smoke test inside the container over SSM.

## Known limitations (honest status at submission)

- **Bedrock is wired, tested and deployed but throttled.** Claude Opus 4.6 answers small
  requests on this account; real teaching-plan requests hit the new-account daily token
  cap on all models. Four quota-increase cases are open with AWS
  (178973245700023, 178973245400759, 178973245300071, 178973245300119). The live demo
  therefore runs the **offline mock brain** (`KAVACH_BRAIN=mock`), which builds the topic
  map from the PDF's headings and uses a hand-written plan for the TCP handshake topic;
  other topics get generic lessons. Switching to Claude is one line in `backend/.env` +
  `python scripts/deploy_ec2.py`.
- Only text-based PDFs are handled well; OCR is an optional hook (pytesseract).
- Single instance, in-process job queue: fine for a demo, not for many concurrent users
  (next step: SQS + a render worker autoscaling group, or Step Functions).
- English only; one Polly voice.

## MVP success criteria (spec §28)

Upload · S3 · Bedrock analysis · topic map · learning order · add/remove topics ·
teaching plan · shorts · Polly narration · whiteboard render · MP4 · S3 · watch in frontend ·
source pages · quick check — all implemented, plus P1/P2: multiple shorts per topic,
follow-up Q&A, feedback regeneration, AI-enhanced mode with visible "AI-added context",
uncertainty warnings, suitability check and an OCR hook (`pip install pytesseract` +
Tesseract binary to enable).

Not in the MVP: accounts, multiple PDFs per session, combined lesson, mobile app.
