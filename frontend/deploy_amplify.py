"""Deploy the Kavach frontend to AWS Amplify Hosting with a manual (zip) deployment -
no GitHub connection needed.

    python deploy_amplify.py --api https://<cloudfront-or-backend-url>

Builds the Vite app with VITE_API_URL, zips dist/, uploads it to Amplify and waits for
the deployment. Prints the https://main.<app>.amplifyapp.com URL.
"""
from __future__ import annotations

import argparse
import io
import os
import subprocess
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

import boto3

HERE = Path(__file__).resolve().parent
APP_NAME = "kavach"


def build(api_url: str) -> Path:
    env = dict(os.environ, VITE_API_URL=api_url)
    npm = "npm.cmd" if sys.platform.startswith("win") else "npm"
    subprocess.run([npm, "run", "build"], cwd=HERE, env=env, check=True)
    return HERE / "dist"


def zip_dir(d: Path) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for f in d.rglob("*"):
            if f.is_file():
                z.write(f, f.relative_to(d).as_posix())
    return buf.getvalue()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--api", required=True, help="backend base URL (https for a production site)")
    ap.add_argument("--region", default=os.environ.get("AWS_REGION", "ap-south-1"))
    ap.add_argument("--branch", default="main")
    args = ap.parse_args()

    amp = boto3.client("amplify", region_name=args.region)
    app = next((a for a in amp.list_apps(maxResults=100)["apps"] if a["name"] == APP_NAME), None)
    if not app:
        app = amp.create_app(
            name=APP_NAME, platform="WEB", description="Kavach - PDF to whiteboard revision shorts",
            customRules=[{"source": "</^[^.]+$|\\.(?!(css|gif|ico|jpg|js|png|txt|svg|woff|woff2|ttf|map|json|webp)$)([^.]+$)/>",
                          "target": "/index.html", "status": "200"}],
        )["app"]
        print(f"amplify app {app['appId']}: created")
    app_id = app["appId"]
    try:
        amp.get_branch(appId=app_id, branchName=args.branch)
    except amp.exceptions.NotFoundException:
        amp.create_branch(appId=app_id, branchName=args.branch, stage="PRODUCTION", enableAutoBuild=False)
        print(f"branch {args.branch}: created")

    dist = build(args.api)
    data = zip_dir(dist)
    dep = amp.create_deployment(appId=app_id, branchName=args.branch)
    req = urllib.request.Request(dep["zipUploadUrl"], data=data, method="PUT", headers={"Content-Type": "application/zip"})
    with urllib.request.urlopen(req) as r:
        assert r.status in (200, 204), r.status
    amp.start_deployment(appId=app_id, branchName=args.branch, jobId=dep["jobId"])
    print(f"deployment {dep['jobId']}: uploaded {len(data) // 1024} KB, deploying", end="", flush=True)
    for _ in range(60):
        job = amp.get_job(appId=app_id, branchName=args.branch, jobId=dep["jobId"])["job"]["summary"]
        if job["status"] in ("SUCCEED", "FAILED", "CANCELLED"):
            print("\nstatus:", job["status"])
            break
        print(".", end="", flush=True)
        time.sleep(5)
    print(f"\nFrontend: https://{args.branch}.{app['defaultDomain']}")
    print(f"API:      {args.api}")


if __name__ == "__main__":
    main()
