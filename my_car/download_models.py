#!/usr/bin/env python3
"""
download_models.py
------------------
③ 物体検出で使う YOLOv4-tiny ONNX モデルをダウンロードするスクリプト。

ダウンロード元: GitHub (AlexeyAB/darknet) の公式ミラー / ONNX Model Zoo
サイズ: ~23 MB（YOLOv4-tiny.onnx）

使い方:
    python download_models.py
    python download_models.py --list       # ダウンロード済み確認のみ
"""

import argparse
import hashlib
import os
import sys
import urllib.request
from pathlib import Path

MODELS_DIR = Path(__file__).parent / "models"

# ダウンロードするモデル一覧
# (ファイル名, URL, SHA256 の先頭16文字)
MODELS = [
    (
        "yolov4-tiny.onnx",
        "https://github.com/Tianxiaomo/pytorch-YOLOv4/releases/download/weights/yolov4-tiny.onnx",
        None,  # チェックサム省略（URL変更時のフォールバック用）
    ),
]

# フォールバック URL（GitHub releases が落ちているとき）
FALLBACK_URLS: dict[str, list[str]] = {
    "yolov4-tiny.onnx": [
        "https://github.com/Tianxiaomo/pytorch-YOLOv4/releases/download/weights/yolov4-tiny.onnx",
    ],
}


def _progress(block_count: int, block_size: int, total_size: int) -> None:
    downloaded = block_count * block_size
    if total_size > 0:
        pct = min(downloaded / total_size * 100, 100)
        bar = "#" * int(pct / 2)
        sys.stdout.write(f"\r  [{bar:<50}] {pct:5.1f}%  {downloaded//1024//1024} MB")
        sys.stdout.flush()
        if downloaded >= total_size:
            print()
    else:
        sys.stdout.write(f"\r  {downloaded // 1024} KB ...")
        sys.stdout.flush()


def download_file(url: str, dest: Path) -> bool:
    """1 ファイルをダウンロードして dest に保存。成功したら True。"""
    try:
        print(f"  Downloading: {url}")
        urllib.request.urlretrieve(url, dest, reporthook=_progress)
        return True
    except Exception as e:
        print(f"  [ERROR] {e}")
        if dest.exists():
            dest.unlink()
        return False


def run(list_only: bool = False) -> None:
    MODELS_DIR.mkdir(exist_ok=True)

    if list_only:
        print(f"Models directory: {MODELS_DIR}\n")
        for fname, _, _ in MODELS:
            path = MODELS_DIR / fname
            status = f"✓ ({path.stat().st_size // 1024} KB)" if path.exists() else "✗ missing"
            print(f"  {fname}: {status}")
        return

    print(f"\nModels will be saved to: {MODELS_DIR}\n")
    ok_all = True
    for fname, primary_url, _ in MODELS:
        dest = MODELS_DIR / fname
        if dest.exists():
            print(f"  [SKIP] {fname} already exists ({dest.stat().st_size // 1024 // 1024} MB)")
            continue

        urls = [primary_url] + FALLBACK_URLS.get(fname, [])
        # 重複除去（順序維持）
        seen: set[str] = set()
        urls = [u for u in urls if not (u in seen or seen.add(u))]

        print(f"Downloading {fname} ...")
        success = False
        for url in urls:
            if download_file(url, dest):
                size_mb = dest.stat().st_size / 1024 / 1024
                print(f"  [OK] Saved {fname} ({size_mb:.1f} MB)")
                success = True
                break
        if not success:
            print(f"  [FAIL] {fname} のダウンロードに失敗しました。")
            print("  手動でダウンロードして my_car/models/ に配置してください。")
            ok_all = False

    print()
    if ok_all:
        print("すべてのモデルの準備が完了しました。")
        print("実行: python demo_detection.py --host <IP>")
    else:
        print("一部のモデルが取得できませんでした。")
        print("Haar Cascade モード (--backend haar) なら追加モデル不要で動作します。")


def main() -> None:
    parser = argparse.ArgumentParser(description="YOLOv4-tiny ONNX モデルをダウンロード")
    parser.add_argument("--list", action="store_true", help="ダウンロード状況の確認のみ")
    args = parser.parse_args()
    run(list_only=args.list)


if __name__ == "__main__":
    main()
