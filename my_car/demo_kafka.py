#!/usr/bin/env python3
"""
demo_kafka.py
-------------
Freenove 4WD Smart Car ⑤ IBM Kafka 連携デモ（モーター制御のみ）

起動時に固定の走行シーケンスを実行し、各操作を IBM Kafka へ送信する。

データフロー:
    MotorClient (TCP:5000) ← 走行コマンド
    MotorEventProducer     → freenove.motor-events (IBM Kafka)

走行シーケンス（--sequence オプションで変更可）:
    前進 1.0 秒 → 後退 0.5 秒 → 左回転 1.0 秒 → 右回転 1.0 秒 → 停止

使い方:
    # .env に IBM Event Streams の接続情報を設定してから実行
    cp my_car/.env.example my_car/.env

    python my_car/demo_kafka.py --host 192.168.1.24

    # Kafka 送信なしでモーター動作だけ確認（接続情報不要）
    python my_car/demo_kafka.py --host 192.168.1.24 --dry-run

    # 速度を変更（デフォルト: 1200）
    python my_car/demo_kafka.py --host 192.168.1.24 --speed 800
"""

from __future__ import annotations

import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))

from motor_client import MotorClient
from producers.motor_event_producer import MotorEventProducer


# ------------------------------------------------------------------ #
# 走行シーケンス定義                                                   #
# ------------------------------------------------------------------ #

# (Kafka コマンド名, MotorClient メソッド名, 継続秒数)
_STARTUP_SEQUENCE: list[tuple[str, str, float]] = [
    ("forward",    "forward",    1.0),
    ("backward",   "backward",   0.5),
    ("turn_left",  "turn_left",  1.0),
    ("turn_right", "turn_right", 1.0),
]


def _duty_for(command: str, speed: int) -> tuple[int, int, int, int]:
    """コマンド名から (fl, bl, fr, br) の duty 値を返す。"""
    if command == "forward":
        return (speed, speed, speed, speed)
    elif command == "backward":
        return (-speed, -speed, -speed, -speed)
    elif command == "turn_left":
        return (-speed, -speed, speed, speed)
    elif command == "turn_right":
        return (speed, speed, -speed, -speed)
    else:
        return (0, 0, 0, 0)


# ------------------------------------------------------------------ #
# メイン処理                                                           #
# ------------------------------------------------------------------ #

def run(host: str, speed: int, dry_run: bool) -> None:
    print(f"\n{'='*55}")
    print(f" Freenove Smart Car — Kafka モーターデモ")
    print(f" ホスト: {host}  速度: {speed}  dry-run: {dry_run}")
    print(f"{'='*55}\n")

    motor_events = MotorEventProducer(mode="manual")

    with MotorClient(host) as motor:
        motor.stop()
        print("[走行シーケンス] 開始")

        for command, method_name, duration in _STARTUP_SEQUENCE:
            print(f"  → {command:12s} {duration}s ...", end="", flush=True)

            # モーター動作
            getattr(motor, method_name)(speed)

            # Kafka 送信
            if not dry_run:
                fl, bl, fr, br = _duty_for(command, speed)
                motor_events.publish(
                    command=command,
                    fl=fl, bl=bl, fr=fr, br=br,
                )

            time.sleep(duration)
            print(" done")

        # 停止
        motor.stop()
        if not dry_run:
            motor_events.publish(command="stop", fl=0, bl=0, fr=0, br=0)
        print("[走行シーケンス] 完了\n")

    motor_events.close()
    print("Kafka へのフラッシュが完了しました。")


# ------------------------------------------------------------------ #
# CLI                                                                 #
# ------------------------------------------------------------------ #

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Freenove 4WD Smart Car — Kafka モーターデモ",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--host", "-H", required=True,
                        help="Raspberry Pi の IP アドレス")
    parser.add_argument("--speed", "-s", type=int, default=1200,
                        help="走行速度 duty 0-4095（デフォルト: 1200）")
    parser.add_argument("--dry-run", action="store_true",
                        help="Kafka 送信を行わずモーター動作だけ確認する")
    args = parser.parse_args()

    try:
        run(host=args.host, speed=args.speed, dry_run=args.dry_run)
    except KeyboardInterrupt:
        print("\n中断しました")
        sys.exit(0)
    except KeyError as e:
        print(f"\n[エラー] 環境変数が未設定です: {e}")
        print("  my_car/.env.example を参考に my_car/.env を作成してください。")
        sys.exit(1)
    except ConnectionRefusedError:
        print(f"\n[エラー] {args.host} に接続できませんでした")
        print("  Raspberry Pi 上で Code/Server/main.py が起動しているか確認してください。")
        sys.exit(1)


if __name__ == "__main__":
    main()
