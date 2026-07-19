#!/usr/bin/env python3
"""
demo_motor.py
-------------
Freenove 4WD Smart Car ① モーター制御デモ

走行シーケンス:
  1. 前進 2 秒
  2. 停止 0.5 秒
  3. 後退 2 秒
  4. 停止 0.5 秒
  5. 左旋回 1.5 秒
  6. 停止 0.5 秒
  7. 右旋回 1.5 秒
  8. 停止

使い方:
    python demo_motor.py --host <RaspberryPi の IP>
    python demo_motor.py --host 192.168.1.100 --speed 1500
"""

import argparse
import time
import sys
import os

# my_car/ を直接実行した場合も import できるようにパスを通す
sys.path.insert(0, os.path.dirname(__file__))

from motor_client import MotorClient, SPEED_FULL


# ------------------------------------------------------------------
# デモシーケンス定義
# 各エントリ: (ラベル, メソッド名, 実行秒数)
# ------------------------------------------------------------------
DEMO_SEQUENCE = [
    ("前進",   "forward",    2.0),
    ("停止",   "stop",       0.5),
    ("後退",   "backward",   2.0),
    ("停止",   "stop",       0.5),
    ("左旋回", "turn_left",  1.5),
    ("停止",   "stop",       0.5),
    ("右旋回", "turn_right", 1.5),
    ("停止",   "stop",       0.0),  # duration=0 → コマンドだけ送って終了
]


def run_demo(host: str, speed: int) -> None:
    print(f"\n{'='*40}")
    print(f" Freenove Smart Car — モーター制御デモ")
    print(f" ホスト: {host}  速度: {speed}")
    print(f"{'='*40}\n")

    with MotorClient(host) as car:
        for label, method, duration in DEMO_SEQUENCE:
            print(f"[デモ] {label} ({'%.1f' % duration}秒)" if duration > 0 else f"[デモ] {label}")
            # speed が必要なメソッドかどうかで呼び分け
            if method == "stop":
                car.stop()
            else:
                getattr(car, method)(speed)
            if duration > 0:
                time.sleep(duration)

    print("\n[デモ] 完了")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Freenove 4WD Smart Car モーター制御デモ"
    )
    parser.add_argument(
        "--host", "-H",
        required=True,
        help="Raspberry Pi の IP アドレス (例: 192.168.1.100)",
    )
    parser.add_argument(
        "--speed", "-s",
        type=int,
        default=SPEED_FULL,
        help=f"モーター速度 duty 値 (1〜4095, デフォルト: {SPEED_FULL})",
    )
    args = parser.parse_args()

    if not (1 <= args.speed <= 4095):
        parser.error("speed は 1〜4095 の範囲で指定してください")

    try:
        run_demo(args.host, args.speed)
    except KeyboardInterrupt:
        print("\n[デモ] Ctrl+C で中断しました")
        sys.exit(0)
    except ConnectionRefusedError:
        print(f"\n[エラー] {args.host}:5000 に接続できませんでした。")
        print("  - Raspberry Pi のサーバー (Code/Server/main.py) が起動しているか確認してください。")
        sys.exit(1)
    except TimeoutError:
        print(f"\n[エラー] {args.host} への接続がタイムアウトしました。IP アドレスを確認してください。")
        sys.exit(1)


if __name__ == "__main__":
    main()
