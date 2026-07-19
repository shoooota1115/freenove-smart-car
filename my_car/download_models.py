#!/usr/bin/env python3
"""
download_models.py
------------------
③ 物体検出で使う YOLOv4-tiny モデルをダウンロードするスクリプト。

形式: darknet 形式 (.cfg + .weights) — cv2.dnn.readNetFromDarknet() で直接ロード可能。
      ONNX 変換は不要。

ファイル:
  models/yolov4-tiny.cfg      ~8 KB  (AlexeyAB/darknet GitHub raw)
  models/yolov4-tiny.weights  ~23 MB (AlexeyAB/darknet GitHub releases)

使い方:
    python download_models.py          # ダウンロード実行
    python download_models.py --list   # ダウンロード済み確認のみ
"""

import argparse
import os
import sys
import urllib.request
from pathlib import Path

MODELS_DIR = Path(__file__).parent / "models"

MODELS = [
    (
        "yolov4-tiny.cfg",
        "https://raw.githubusercontent.com/AlexeyAB/darknet/master/cfg/yolov4-tiny.cfg",
    ),
    (
        "yolov4-tiny.weights",
        "https://github.com/AlexeyAB/darknet/releases/download/darknet_yolo_v4_pre/yolov4-tiny.weights",
    ),
]


def _progress(block_count: int, block_size: int, total_size: int) -> None:
    downloaded = block_count * block_size
    if total_size > 0:
        pct = min(downloaded / total_size * 100, 100)
        bar = "#" * int(pct / 2)
        sys.stdout.write(f"\r  [{bar:<50}] {pct:5.1f}%  {downloaded//1024//1024} MB / {total_size//1024//1024} MB")
        sys.stdout.flush()
        if downloaded >= total_size:
            print()
    else:
        sys.stdout.write(f"\r  {downloaded // 1024} KB ...")
        sys.stdout.flush()


def download_file(url: str, dest: Path) -> bool:
    try:
        print(f"  URL: {url}")
        urllib.request.urlretrieve(url, dest, reporthook=_progress)
        return True
    except Exception as e:
        print(f"\n  [ERROR] {e}")
        if dest.exists():
            dest.unlink()
        return False


def run(list_only: bool = False) -> None:
    MODELS_DIR.mkdir(exist_ok=True)

    if list_only:
        print(f"Models directory: {MODELS_DIR}\n")
        for fname, _ in MODELS:
            path = MODELS_DIR / fname
            if path.exists():
                print(f"  ✓ {fname}  ({path.stat().st_size // 1024} KB)")
            else:
                print(f"  ✗ {fname}  (missing)")
        return

    print(f"\nModels will be saved to: {MODELS_DIR}\n")
    ok_all = True
    for fname, url in MODELS:
        dest = MODELS_DIR / fname
        if dest.exists():
            print(f"[SKIP] {fname} already exists ({dest.stat().st_size // 1024} KB)")
            continue
        print(f"Downloading {fname} ...")
        if download_file(url, dest):
            print(f"  [OK] {fname}  ({dest.stat().st_size // 1024} KB)")
        else:
            ok_all = False

    print()
    if ok_all:
        print("モデルの準備が完了しました。")
        print("実行: python demo_detection.py --host <IP> --backend yolo")
    else:
        print("ダウンロードに失敗したファイルがあります。")
        print("Haar Cascade モード (--backend haar) なら追加モデル不要で動作します。")


def main() -> None:
    parser = argparse.ArgumentParser(description="YOLOv4-tiny モデルをダウンロード")
    parser.add_argument("--list", action="store_true", help="ダウンロード状況の確認のみ")
    args = parser.parse_args()
    run(list_only=args.list)


if __name__ == "__main__":
    main()
