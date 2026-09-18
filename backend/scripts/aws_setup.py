"""One-shot AWS provisioning for Kavach: S3 bucket + three DynamoDB tables.
Also prints the IAM policy the backend needs and verifies Bedrock/Polly access.

    set AWS_REGION=us-east-1
    set KAVACH_S3_BUCKET=my-kavach-bucket
    python scripts/aws_setup.py
"""
from __future__ import annotations

import json
import os
import sys

import boto3
from botocore.exceptions import ClientError

REGION = os.environ.get("AWS_REGION", os.environ.get("AWS_DEFAULT_REGION", "us-east-1"))
BUCKET = os.environ.get("KAVACH_S3_BUCKET", "")
PREFIX = os.environ.get("KAVACH_DDB_PREFIX", "kavach")
MODEL = os.environ.get("KAVACH_BEDROCK_MODEL", "us.anthropic.claude-opus-4-6-v1")
BEDROCK_REGION = os.environ.get("KAVACH_BEDROCK_REGION", REGION)


def ensure_bucket(s3):
    if not BUCKET:
        sys.exit("Set KAVACH_S3_BUCKET to a globally unique bucket name")
    try:
        s3.head_bucket(Bucket=BUCKET)
        print(f"bucket {BUCKET}: exists")
        return
    except ClientError:
        pass
    kwargs = {"Bucket": BUCKET}
    if REGION != "us-east-1":
        kwargs["CreateBucketConfiguration"] = {"LocationConstraint": REGION}
    s3.create_bucket(**kwargs)
    s3.put_bucket_cors(
        Bucket=BUCKET,
        CORSConfiguration={"CORSRules": [{"AllowedMethods": ["GET", "HEAD"], "AllowedOrigins": ["*"], "AllowedHeaders": ["*"], "MaxAgeSeconds": 3600}]},
    )
    print(f"bucket {BUCKET}: created (presigned GET + CORS for video playback)")


def ensure_table(ddb, name, key_schema, attr_defs, gsi=None):
    try:
        ddb.describe_table(TableName=name)
        print(f"table {name}: exists")
        return
    except ClientError as e:
        if e.response["Error"]["Code"] != "ResourceNotFoundException":
            raise
    kwargs = dict(TableName=name, KeySchema=key_schema, AttributeDefinitions=attr_defs, BillingMode="PAY_PER_REQUEST")
    if gsi:
        kwargs["GlobalSecondaryIndexes"] = gsi
    ddb.create_table(**kwargs)
    ddb.get_waiter("table_exists").wait(TableName=name)
    print(f"table {name}: created")


def main():
    s3 = boto3.client("s3", region_name=REGION)
    ddb = boto3.client("dynamodb", region_name=REGION)
    ensure_bucket(s3)
    ensure_table(ddb, f"{PREFIX}_documents", [{"AttributeName": "document_id", "KeyType": "HASH"}],
                 [{"AttributeName": "document_id", "AttributeType": "S"}])
    ensure_table(ddb, f"{PREFIX}_topics",
                 [{"AttributeName": "document_id", "KeyType": "HASH"}, {"AttributeName": "topic_id", "KeyType": "RANGE"}],
                 [{"AttributeName": "document_id", "AttributeType": "S"}, {"AttributeName": "topic_id", "AttributeType": "S"}])
    ensure_table(ddb, f"{PREFIX}_reels", [{"AttributeName": "reel_id", "KeyType": "HASH"}],
                 [{"AttributeName": "reel_id", "AttributeType": "S"}, {"AttributeName": "document_id", "AttributeType": "S"}],
                 gsi=[{"IndexName": "by_document", "KeySchema": [{"AttributeName": "document_id", "KeyType": "HASH"}],
                       "Projection": {"ProjectionType": "ALL"}}])

    # smoke-test Polly + Bedrock access
    try:
        boto3.client("polly", region_name=REGION).describe_voices(LanguageCode="en-US", Engine="neural")
        print("polly: ok")
    except ClientError as e:
        print(f"polly: FAILED ({e})")
    try:
        if os.environ.get("KAVACH_BRAIN", "converse") == "bedrock":
            from anthropic import AnthropicBedrockMantle

            m = AnthropicBedrockMantle(aws_region=BEDROCK_REGION).messages.create(
                model=MODEL, max_tokens=20, messages=[{"role": "user", "content": "Say ok"}])
            print(f"bedrock mantle ({MODEL}): ok -> {m.content[0].text!r}")
        else:
            r = boto3.client("bedrock-runtime", region_name=BEDROCK_REGION).converse(
                modelId=MODEL, messages=[{"role": "user", "content": [{"text": "Say ok"}]}], inferenceConfig={"maxTokens": 20})
            print(f"bedrock converse ({MODEL}): ok -> {r['output']['message']['content'][0]['text']!r}")
    except Exception as e:  # noqa: BLE001
        print(f"bedrock ({MODEL}): FAILED ({str(e)[:300]})\n  -> Bedrock console > Model access: submit the Anthropic "
              "use-case form / request the model, or set KAVACH_BEDROCK_MODEL to an id your account can use")

    policy = {
        "Version": "2012-10-17",
        "Statement": [
            {"Effect": "Allow", "Action": ["s3:PutObject", "s3:GetObject", "s3:HeadObject"], "Resource": f"arn:aws:s3:::{BUCKET}/*"},
            {"Effect": "Allow", "Action": ["s3:ListBucket"], "Resource": f"arn:aws:s3:::{BUCKET}"},
            {"Effect": "Allow", "Action": ["dynamodb:GetItem", "dynamodb:PutItem", "dynamodb:UpdateItem", "dynamodb:DeleteItem",
                                            "dynamodb:Query", "dynamodb:Scan", "dynamodb:BatchWriteItem"],
             "Resource": [f"arn:aws:dynamodb:{REGION}:*:table/{PREFIX}_*", f"arn:aws:dynamodb:{REGION}:*:table/{PREFIX}_*/index/*"]},
            {"Effect": "Allow", "Action": ["bedrock:InvokeModel", "bedrock:InvokeModelWithResponseStream"], "Resource": "*"},
            {"Effect": "Allow", "Action": ["polly:SynthesizeSpeech", "polly:DescribeVoices"], "Resource": "*"},
        ],
    }
    print("\nIAM policy for the backend role/user:\n" + json.dumps(policy, indent=2))
    print("\nBackend env:\n  KAVACH_MODE=aws\n  AWS_REGION=%s\n  KAVACH_S3_BUCKET=%s\n  KAVACH_DDB_PREFIX=%s\n  KAVACH_BEDROCK_MODEL=%s" % (REGION, BUCKET, PREFIX, MODEL))


if __name__ == "__main__":
    main()
