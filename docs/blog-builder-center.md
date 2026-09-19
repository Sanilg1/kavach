# Compress the delivery, not the knowledge: building Kavach on AWS in a weekend

*First Commit hackathon (WeMakeDevs × AWS), 17–20 September 2026. #BharatBuilds*

Every exam season I do the same thing: open a 60-page PDF of lecture notes the night before
and try to read it faster. Summaries don't help — they throw away exactly the detail the
exam asks for. So for First Commit we built **Kavach**: upload a study PDF, get a learning
path, and watch each concept as a 30–60 second narrated whiteboard short, grounded in the
PDF's own pages, with a quick-check question after each one.

Live: https://main.d1ex6vljuszbzw.amplifyapp.com · Code: https://github.com/Sanilg1/kavach

![Architecture](https://github.com/Sanilg1/kavach/raw/main/docs/architecture.png)

## The idea in one rule

The AI is not allowed to summarise. It has to **plan a lesson**: which concepts matter, what
depends on what, in which order to teach them, and — for each short — a list of scenes with
narration and *what to draw*. That plan is JSON. Code turns the JSON into video.

This split turned out to be the most important design decision of the weekend:

- **Claude on Amazon Bedrock** produces the topic map and the teaching plan (scenes →
  primitives like `BOX`, `ARROW`, `FLOW`, `TABLE`, `DIAGRAM`, `EQUATION`, `HIGHLIGHT`, with
  positions on a 0–100 board and animations like `DRAW`, `WRITE`, `ARROW_FLOW`).
- **A deterministic renderer** (Pillow + FFmpeg) draws the frames with hand-drawn wobbly
  strokes and a handwriting font. A 40-second short renders in about 6 seconds on a
  t3.medium, and the same plan always produces the same video. Nothing in the visuals can
  be hallucinated, because nothing in the visuals is generated.
- **Amazon Polly** narrates each scene. We ask Polly for word-level *speech marks* too, and
  use them to burn synced captions into the video with the spoken word highlighted — because
  students watch shorts on mute.

Everything the student sees carries its **PDF source pages**, and anything the model adds
from general knowledge is listed separately as "AI-added context". That distinction is
cheap to build and was the feature people trusted most.

## The AWS stack

| Service | Job |
|---|---|
| Amplify Hosting | the React shorts feed (mobile-first, 9:16 player) |
| CloudFront | HTTPS in front of the API so the Amplify site can call it |
| EC2 (Docker) | FastAPI + background workers; IAM instance role, no static keys |
| S3 | PDFs, plans, audio, videos — served to the browser with presigned URLs |
| DynamoDB | documents / topics / reels |
| Polly | narration + speech marks |
| Bedrock | Claude Opus 4.6 through the Converse API, with the PDF and page images attached so it can see diagrams |

Deployment is two scripts: one zips the backend to S3 and launches an instance whose
user-data builds the container; the other does a manual zip deployment to Amplify. No local
Docker, no GitHub OAuth.

## What we learned (the honest part)

**1. Bedrock model access moved from a console page to an API.** The "Model access" page is
retired. Anthropic models still need a one-time use-case form, and it now lives behind
`bedrock:PutUseCaseForModelAccess` with an undocumented JSON body — `intendedUsers` is
`"0"` or `"1"`, not a label. We reverse-engineered it from `ValidationException`s.

**2. New AWS accounts get near-zero Bedrock quotas.** After the form was accepted, every
model — Claude, Nova, Llama, Mistral — answered "Too many tokens per day". The applied
quotas were `0` against defaults in the millions, and they differ per region (Mumbai showed
0 for Claude 4.6 while Oregon had 10k RPM). So Kavach has a separate `KAVACH_BEDROCK_REGION`,
and we filed quota cases. Our takeaway: **file quota requests on day one** of any hackathon.

**3. Design for the quota wall.** The API now tries Bedrock first and, if it's throttled,
falls back to an offline planner for a cooldown period. Every document records which brain
produced it and the UI says so. The moment AWS lifts the cap, the live site switches to
Claude with no redeploy.

**4. App Runner throttles CPU between requests**, which stalls background video rendering.
A plain EC2 container was the simplest reliable host.

**5. Presigned URLs change on every poll.** The React player has to pin the video `src`
or playback restarts every 3 seconds.

**6. A Linux-only bug we only found in the cloud.** `subprocess.communicate()` flushes the
stdin pipe you already closed, so every FFmpeg render "failed" on the server after
succeeding — while working perfectly on Windows. We found it by running our smoke test
inside the container over SSM Session Manager. Now stderr goes to a file and we `wait()`.

## What's next

Real Bedrock lessons for every topic once the quota lands; OCR for scanned PDFs; a queue +
autoscaling render workers; spaced-repetition scheduling of the quick checks.

*Built by Sanil Grover, Thapar Institute of Engineering and Technology.*
