"""Thin wrapper around boto3 for MinIO / S3 operations."""

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import ClientError

from mkb.config import settings


def get_s3_client():
    return boto3.client(
        "s3",
        endpoint_url=settings.s3_endpoint,
        aws_access_key_id=settings.s3_access_key,
        aws_secret_access_key=settings.s3_secret_key,
        config=BotoConfig(signature_version="s3v4"),
        region_name="us-east-1",
    )


def ensure_bucket_exists(bucket: str) -> None:
    """Create bucket if it does not exist."""
    client = get_s3_client()
    try:
        client.head_bucket(Bucket=bucket)
    except ClientError as exc:
        code = (exc.response or {}).get("Error", {}).get("Code")
        if code in {"404", "NoSuchBucket", "NotFound"}:
            client.create_bucket(Bucket=bucket)
            return
        raise


def upload_bytes(data: bytes, bucket: str, key: str) -> None:
    client = get_s3_client()
    try:
        client.put_object(Bucket=bucket, Key=key, Body=data)
    except ClientError as exc:
        # MinIO/S3 may return NoSuchBucket when environment bootstrap was skipped.
        code = (exc.response or {}).get("Error", {}).get("Code")
        if code == "NoSuchBucket":
            ensure_bucket_exists(bucket)
            client.put_object(Bucket=bucket, Key=key, Body=data)
            return
        raise


def download_bytes(bucket: str, key: str) -> bytes:
    client = get_s3_client()
    response = client.get_object(Bucket=bucket, Key=key)
    return response["Body"].read()


def object_exists(bucket: str, key: str) -> bool:
    client = get_s3_client()
    try:
        client.head_object(Bucket=bucket, Key=key)
        return True
    except client.exceptions.ClientError:
        return False


def delete_object(bucket: str, key: str) -> None:
    client = get_s3_client()
    client.delete_object(Bucket=bucket, Key=key)


def delete_prefix(bucket: str, prefix: str) -> int:
    """Delete all objects under a prefix. Returns number of deleted objects."""
    client = get_s3_client()
    paginator = client.get_paginator("list_objects_v2")
    deleted = 0

    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        contents = page.get("Contents", [])
        if not contents:
            continue

        objects = [{"Key": item["Key"]} for item in contents]
        client.delete_objects(Bucket=bucket, Delete={"Objects": objects})
        deleted += len(objects)

    return deleted
