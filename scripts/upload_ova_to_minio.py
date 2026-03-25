#!/usr/bin/env python3
"""
upload_ova_to_minio.py — 将 OVA 文件上传到 MinIO 对象存储

用法:
    python upload_ova_to_minio.py [--file OVA_PATH] [--bucket BUCKET] [--object-name NAME]

环境变量:
    MINIO_ENDPOINT   MinIO 地址 (默认: 172.19.80.100:9090)
    MINIO_ACCESS_KEY 访问密钥 (默认: admin)
    MINIO_SECRET_KEY 秘密密钥 (默认: minio_test_password_2025)
"""

import argparse
import os
import sys
from pathlib import Path

from minio import Minio
from minio.error import S3Error


def main():
    parser = argparse.ArgumentParser(description="Upload OVA to MinIO")
    parser.add_argument(
        "--file",
        default="images/Win11VM-marvin.ova",
        help="OVA file path (default: images/Win11VM-marvin.ova)",
    )
    parser.add_argument(
        "--bucket",
        default=os.getenv("MINIO_BUCKET", "aidd-files"),
        help="MinIO bucket name",
    )
    parser.add_argument(
        "--object-name",
        default=None,
        help="Object name in MinIO (default: marvin-vbox/<filename>)",
    )
    args = parser.parse_args()

    ova_path = Path(args.file)
    if not ova_path.exists():
        print(f"[ERROR] 文件不存在: {ova_path}")
        sys.exit(1)

    file_size = ova_path.stat().st_size
    file_size_gb = file_size / (1024 ** 3)
    print(f"文件: {ova_path} ({file_size_gb:.2f} GB)")

    # MinIO 配置
    endpoint = os.getenv("MINIO_ENDPOINT", "172.19.80.100:9090")
    access_key = os.getenv("MINIO_ACCESS_KEY", "admin")
    secret_key = os.getenv("MINIO_SECRET_KEY", "minio_test_password_2025")
    secure = os.getenv("MINIO_SECURE", "false").lower() == "true"

    object_name = args.object_name or f"marvin-vbox/{ova_path.name}"

    print(f"MinIO Endpoint: {endpoint}")
    print(f"Bucket: {args.bucket}")
    print(f"Object: {object_name}")
    print()

    # 创建客户端
    client = Minio(endpoint, access_key=access_key, secret_key=secret_key, secure=secure)

    # 确保 bucket 存在
    if not client.bucket_exists(args.bucket):
        print(f"[INFO] Bucket '{args.bucket}' 不存在，正在创建...")
        client.make_bucket(args.bucket)

    # 上传（大文件自动使用分片上传）
    print(f"[INFO] 开始上传 ({file_size_gb:.2f} GB)...")
    print("       大文件上传可能需要较长时间，请耐心等待...")

    result = client.fput_object(
        bucket_name=args.bucket,
        object_name=object_name,
        file_path=str(ova_path),
        content_type="application/x-virtualbox-ova",
        # 分片大小 64MB（适合大文件）
        part_size=64 * 1024 * 1024,
    )

    print()
    print(f"[OK] 上传完成!")
    print(f"     Object:  {result.object_name}")
    print(f"     ETag:    {result.etag}")
    print(f"     Version: {result.version_id or 'N/A'}")
    print()
    print(f"下载命令:")
    print(f"  mc cp myminio/{args.bucket}/{object_name} ./Win11VM-marvin.ova")


if __name__ == "__main__":
    main()
