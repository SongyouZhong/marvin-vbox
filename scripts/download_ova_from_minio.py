#!/usr/bin/env python3
"""Download OVA from MinIO to local images/ directory."""

import os
import sys
import time
from pathlib import Path

from minio import Minio
from minio.error import S3Error


def main():
    endpoint = os.getenv("MINIO_ENDPOINT", "172.19.80.100:9090")
    access_key = os.getenv("MINIO_ACCESS_KEY", "admin")
    secret_key = os.getenv("MINIO_SECRET_KEY", "minio_test_password_2025")
    bucket = os.getenv("MINIO_BUCKET", "aidd-files")
    object_name = "marvin-vbox/Win11VM-marvin.ova"

    output_dir = Path(__file__).resolve().parent.parent / "images"
    output_dir.mkdir(exist_ok=True)
    output_file = output_dir / "Win11VM-marvin.ova"

    print(f"MinIO endpoint: {endpoint}")
    print(f"Bucket: {bucket}, Object: {object_name}")
    print(f"Output: {output_file}")

    client = Minio(endpoint, access_key=access_key, secret_key=secret_key, secure=False)

    # Get object info
    stat = client.stat_object(bucket, object_name)
    total_size = stat.size
    total_gb = total_size / (1024 ** 3)
    print(f"File size: {total_gb:.2f} GB")

    start_time = time.time()
    print("Downloading...")

    client.fget_object(bucket, object_name, str(output_file))

    elapsed = time.time() - start_time
    speed_mbps = (total_size / (1024 ** 2)) / elapsed if elapsed > 0 else 0
    print(f"Done! {elapsed:.1f}s, {speed_mbps:.1f} MB/s")
    print(f"Saved to: {output_file}")


if __name__ == "__main__":
    main()
