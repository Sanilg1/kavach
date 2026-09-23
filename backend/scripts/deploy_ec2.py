"""Deploy the Kavach backend to AWS: one EC2 instance running the Docker image, fronted
by CloudFront for HTTPS. No local Docker needed - the instance builds the image from a
zip of this folder that we upload to S3.

    python scripts/deploy_ec2.py                # blue/green: new instance, switch CloudFront, retire old
    python scripts/deploy_ec2.py --no-cdn       # skip CloudFront
    python scripts/deploy_ec2.py --status       # print instance / URL info

Reads backend/.env for the app configuration (AWS_REGION, KAVACH_S3_BUCKET, ...).
Needs: an AWS session with EC2/IAM/S3/SSM/CloudFront permissions.
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import os
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

BACKEND = Path(__file__).resolve().parent.parent
NAME = "kavach-backend"


def load_env() -> dict[str, str]:
    env: dict[str, str] = {}
    p = BACKEND / ".env"
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                env[k.strip()] = v.split(" #")[0].strip().strip("'\"")
    # never ship static credentials into the instance; it gets an IAM role instead
    for k in ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN", "AWS_PROFILE"):
        env.pop(k, None)
    env.setdefault("KAVACH_MODE", "aws")
    env.setdefault("KAVACH_SSM_PREFIX", "/kavach")
    env.setdefault("KAVACH_DATA_DIR", "/tmp/kavach")
    env.setdefault("KAVACH_WORK_DIR", "/tmp/kavach/work")
    return env


SECRET_KEYS = ("GROQ_API_KEY", "GROQ_API_KEY_2", "GROQ_API_KEY_3", "ANTHROPIC_API_KEY")


def push_secrets(env: dict[str, str], region: str) -> list[str]:
    """Move API keys into SSM Parameter Store (SecureString); return the names moved."""
    ssm = boto3.client("ssm", region_name=region)
    moved = []
    for k in SECRET_KEYS:
        v = env.pop(k, None)
        if v:
            ssm.put_parameter(Name=f"{env['KAVACH_SSM_PREFIX']}/{k}", Value=v, Type="SecureString", Overwrite=True)
            moved.append(k)
    return moved


def ensure_queue(region: str) -> str:
    sqs = boto3.client("sqs", region_name=region)
    url = sqs.create_queue(QueueName=f"{NAME.split('-')[0]}-jobs", Attributes={
        "VisibilityTimeout": "300", "MessageRetentionPeriod": "86400", "ReceiveMessageWaitTimeSeconds": "20",
    })["QueueUrl"]
    return url


def zip_backend() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for sub in ("app", "assets", "scripts"):
            for f in (BACKEND / sub).rglob("*"):
                if f.is_file() and "__pycache__" not in f.parts and f.suffix != ".pyc":
                    z.write(f, f.relative_to(BACKEND).as_posix())
        for f in ("requirements.txt", "Dockerfile"):
            z.write(BACKEND / f, f)
    return buf.getvalue()


def ensure_role(iam, bucket: str, region: str, prefix: str) -> str:
    role = f"{NAME}-role"
    trust = {"Version": "2012-10-17", "Statement": [{"Effect": "Allow", "Principal": {"Service": "ec2.amazonaws.com"}, "Action": "sts:AssumeRole"}]}
    try:
        iam.create_role(RoleName=role, AssumeRolePolicyDocument=json.dumps(trust), Description="Kavach backend instance role")
        print(f"iam role {role}: created")
    except ClientError as e:
        if e.response["Error"]["Code"] != "EntityAlreadyExists":
            raise
    policy = {
        "Version": "2012-10-17",
        "Statement": [
            {"Effect": "Allow", "Action": ["s3:PutObject", "s3:GetObject", "s3:HeadObject", "s3:ListBucket"],
             "Resource": [f"arn:aws:s3:::{bucket}", f"arn:aws:s3:::{bucket}/*"]},
            {"Effect": "Allow", "Action": ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:DeleteItem",
                                            "dynamodb:Query", "dynamodb:Scan", "dynamodb:BatchWriteItem"],
             "Resource": [f"arn:aws:dynamodb:{region}:*:table/{prefix}_*", f"arn:aws:dynamodb:{region}:*:table/{prefix}_*/index/*"]},
            {"Effect": "Allow", "Action": ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"], "Resource": "*"},
            {"Effect": "Allow", "Action": ["polly:SynthesizeSpeech", "polly:DescribeVoices"], "Resource": "*"},
            {"Effect": "Allow", "Action": ["sqs:SendMessage", "sqs:ReceiveMessage", "sqs:DeleteMessage",
                                            "sqs:ChangeMessageVisibility", "sqs:GetQueueAttributes"],
             "Resource": f"arn:aws:sqs:{region}:*:kavach-jobs"},
            {"Effect": "Allow", "Action": ["ssm:GetParametersByPath", "ssm:GetParameter"],
             "Resource": f"arn:aws:ssm:{region}:*:parameter/kavach*"},
            {"Effect": "Allow", "Action": ["kms:Decrypt"], "Resource": "*",
             "Condition": {"StringEquals": {"kms:ViaService": f"ssm.{region}.amazonaws.com"}}},
        ],
    }
    iam.put_role_policy(RoleName=role, PolicyName="kavach-backend", PolicyDocument=json.dumps(policy))
    iam.attach_role_policy(RoleName=role, PolicyArn="arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore")
    try:
        iam.create_instance_profile(InstanceProfileName=role)
        iam.add_role_to_instance_profile(InstanceProfileName=role, RoleName=role)
        time.sleep(10)  # instance profiles take a few seconds to propagate
    except ClientError as e:
        if e.response["Error"]["Code"] != "EntityAlreadyExists":
            raise
    return role


def ensure_sg(ec2) -> str:
    vpc = ec2.describe_vpcs(Filters=[{"Name": "is-default", "Values": ["true"]}])["Vpcs"][0]["VpcId"]
    sgs = ec2.describe_security_groups(Filters=[{"Name": "group-name", "Values": [f"{NAME}-sg"]}, {"Name": "vpc-id", "Values": [vpc]}])["SecurityGroups"]
    if sgs:
        return sgs[0]["GroupId"]
    sg = ec2.create_security_group(GroupName=f"{NAME}-sg", Description="Kavach backend (HTTP)", VpcId=vpc)["GroupId"]
    ec2.authorize_security_group_ingress(GroupId=sg, IpPermissions=[
        {"IpProtocol": "tcp", "FromPort": 80, "ToPort": 80, "IpRanges": [{"CidrIp": "0.0.0.0/0"}]},
        {"IpProtocol": "tcp", "FromPort": 8000, "ToPort": 8000, "IpRanges": [{"CidrIp": "0.0.0.0/0"}]},
    ])
    print(f"security group {sg}: created")
    return sg


def user_data(bucket: str, key: str, env: dict[str, str], region: str) -> str:
    env_lines = "\n".join(f"{k}={v}" for k, v in env.items())
    return f"""#!/bin/bash
set -euxo pipefail
exec > /var/log/kavach-init.log 2>&1
dnf install -y docker unzip
systemctl enable --now docker
mkdir -p /opt/kavach /var/kavach && cd /opt/kavach
aws s3 cp s3://{bucket}/{key} backend.zip --region {region}
rm -rf src && mkdir src && unzip -q backend.zip -d src
cat > /opt/kavach/.env <<'ENVEOF'
{env_lines}
ENVEOF
cd src && docker build -t kavach:latest .
docker rm -f kavach || true
docker run -d --name kavach --restart unless-stopped -p 80:8000 -p 8000:8000 \\
  --env-file /opt/kavach/.env -v /var/kavach:/tmp/kavach kavach:latest
echo KAVACH_READY
"""


def existing_instances(ec2):
    r = ec2.describe_instances(Filters=[{"Name": "tag:Name", "Values": [NAME]}, {"Name": "instance-state-name", "Values": ["pending", "running", "stopping", "stopped"]}])
    return [i for res in r["Reservations"] for i in res["Instances"]]


def ensure_cdn(cf, origin_dns: str) -> str:
    """CloudFront in front of the instance so the Amplify (https) site can call the API."""
    for d in cf.list_distributions().get("DistributionList", {}).get("Items", []) or []:
        if d.get("Comment") == NAME:
            origin = d["Origins"]["Items"][0]["DomainName"]
            if origin != origin_dns:
                cfg = cf.get_distribution_config(Id=d["Id"])
                cfg["DistributionConfig"]["Origins"]["Items"][0]["DomainName"] = origin_dns
                cf.update_distribution(Id=d["Id"], IfMatch=cfg["ETag"], DistributionConfig=cfg["DistributionConfig"])
                print("cloudfront: origin updated")
            return d["DomainName"]
    cfg = {
        "CallerReference": f"{NAME}-{int(time.time())}",
        "Comment": NAME,
        "Enabled": True,
        "Origins": {"Quantity": 1, "Items": [{
            "Id": "ec2", "DomainName": origin_dns,
            "CustomOriginConfig": {"HTTPPort": 80, "HTTPSPort": 443, "OriginProtocolPolicy": "http-only",
                                   "OriginReadTimeout": 60, "OriginKeepaliveTimeout": 5},
        }]},
        "DefaultCacheBehavior": {
            "TargetOriginId": "ec2", "ViewerProtocolPolicy": "redirect-to-https",
            "AllowedMethods": {"Quantity": 7, "Items": ["GET", "HEAD", "OPTIONS", "PUT", "POST", "PATCH", "DELETE"],
                               "CachedMethods": {"Quantity": 2, "Items": ["GET", "HEAD"]}},
            "Compress": True,
            "CachePolicyId": "4135ea2d-6df8-44a3-9df3-4b5a84be39ad",          # CachingDisabled
            "OriginRequestPolicyId": "216adef6-5c7f-47e4-b989-5492eafa07d3",  # AllViewer
        },
        "PriceClass": "PriceClass_200",
        "HttpVersion": "http2",
    }
    d = cf.create_distribution(DistributionConfig=cfg)["Distribution"]
    print(f"cloudfront {d['Id']}: created (deploying, a few minutes)")
    return d["DomainName"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--instance-type", default="t3.medium")
    ap.add_argument("--no-cdn", action="store_true")
    ap.add_argument("--status", action="store_true")
    args = ap.parse_args()

    env = load_env()
    region = env.get("AWS_REGION") or os.environ.get("AWS_REGION", "ap-south-1")
    bucket = env.get("KAVACH_S3_BUCKET") or os.environ.get("KAVACH_S3_BUCKET")
    prefix = env.get("KAVACH_DDB_PREFIX", "kavach")
    if not bucket:
        sys.exit("KAVACH_S3_BUCKET missing (backend/.env)")
    ec2 = boto3.client("ec2", region_name=region)
    iam = boto3.client("iam")
    s3 = boto3.client("s3", region_name=region)
    ssm = boto3.client("ssm", region_name=region)
    cf = boto3.client("cloudfront")

    if args.status:
        for i in existing_instances(ec2):
            print(i["InstanceId"], i["State"]["Name"], i.get("PublicDnsName"))
        return

    key = f"deploy/backend-{int(time.time())}.zip"
    data = zip_backend()
    s3.put_object(Bucket=bucket, Key=key, Body=data)
    print(f"uploaded backend ({len(data) // 1024} KB) -> s3://{bucket}/{key}")

    env["KAVACH_QUEUE_URL"] = ensure_queue(region)
    moved = push_secrets(env, region)
    print(f"job queue: {env['KAVACH_QUEUE_URL']}")
    if moved:
        print(f"secrets moved to SSM {env['KAVACH_SSM_PREFIX']}/: {', '.join(moved)}")
    role = ensure_role(iam, bucket, region, prefix)
    sg = ensure_sg(ec2)
    ami = ssm.get_parameter(Name="/aws/service/ami-amazon-linux-latest/al2023-ami-kernel-default-x86_64")["Parameter"]["Value"]

    old = [i["InstanceId"] for i in existing_instances(ec2)]

    inst = ec2.run_instances(
        ImageId=ami, InstanceType=args.instance_type, MinCount=1, MaxCount=1,
        SecurityGroupIds=[sg], IamInstanceProfile={"Name": role},
        UserData=user_data(bucket, key, env, region),
        BlockDeviceMappings=[{"DeviceName": "/dev/xvda", "Ebs": {"VolumeSize": 30, "VolumeType": "gp3"}}],
        TagSpecifications=[{"ResourceType": "instance", "Tags": [{"Key": "Name", "Value": NAME}, {"Key": "project", "Value": "kavach"}]}],
    )["Instances"][0]
    iid = inst["InstanceId"]
    print(f"instance {iid}: launching ({args.instance_type})")
    ec2.get_waiter("instance_running").wait(InstanceIds=[iid])
    dns = ec2.describe_instances(InstanceIds=[iid])["Reservations"][0]["Instances"][0]["PublicDnsName"]
    print(f"instance running: http://{dns}  (docker build takes ~3-5 min)")

    print("waiting for the new instance to become healthy", end="", flush=True)
    healthy = False
    for _ in range(80):
        try:
            with urllib.request.urlopen(f"http://{dns}/health", timeout=5) as r:
                print("\nhealth:", r.read().decode())
                healthy = True
                break
        except Exception:
            print(".", end="", flush=True)
            time.sleep(10)
    if not healthy:
        print("\nnew instance never became healthy - keeping the old one live. Terminating the new instance.")
        ec2.terminate_instances(InstanceIds=[iid])
        sys.exit(1)

    cdn = None
    if not args.no_cdn:
        cdn = ensure_cdn(cf, dns)
        dist = next(d for d in cf.list_distributions()["DistributionList"]["Items"] if d.get("Comment") == NAME)
        print("switching CloudFront to the new instance (waits for propagation)...")
        cf.get_waiter("distribution_deployed").wait(Id=dist["Id"], WaiterConfig={"Delay": 20, "MaxAttempts": 45})
    if old:
        # jobs running on the old instance are redelivered by SQS to the new one
        print(f"retiring previous instance(s): {', '.join(old)}")
        ec2.terminate_instances(InstanceIds=old)

    print("\nBackend URLs:")
    print(f"  http://{dns}")
    if cdn:
        print(f"  https://{cdn}   <- use this as VITE_API_URL (CloudFront takes a few minutes to deploy)")


if __name__ == "__main__":
    main()
