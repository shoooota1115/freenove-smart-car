#!/usr/bin/env python3
"""
consumer_motor.py
-----------------
freenove.motor-events を購読してターミナルに表示する確認用スクリプト。

使い方:
    python my_car/consumer_motor.py

    # 別ターミナルで demo_kafka.py を実行すると、ここにリアルタイム表示される
    python my_car/demo_kafka.py --host 192.168.1.24
"""

from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))

from kafka.client import create_consumer
from kafka.schemas import TOPIC_MOTOR_EVENTS

COLORS = {
    "forward":    "\033[32m",   # 緑
    "backward":   "\033[33m",   # 黄
    "turn_left":  "\033[36m",   # シアン
    "turn_right": "\033[35m",   # マゼンタ
    "stop":       "\033[31m",   # 赤
}
RESET = "\033[0m"


def main() -> None:
    print(f"[consumer_motor] {TOPIC_MOTOR_EVENTS} を購読中... (Ctrl+C で終了)\n")

    consumer = create_consumer(
        group_id="motor-monitor",
        topics=[TOPIC_MOTOR_EVENTS],
    )

    try:
        while True:
            msg = consumer.poll(timeout=1.0)
            if msg is None:
                continue
            if msg.error():
                print(f"[Error] {msg.error()}", file=sys.stderr)
                continue

            try:
                event: dict = json.loads(msg.value().decode("utf-8"))
            except Exception as e:
                print(f"[Parse error] {e}", file=sys.stderr)
                continue

            command  = event.get("motion", {}).get("command", "—")
            fl       = event.get("motion", {}).get("fl_pwm", 0)
            fr       = event.get("motion", {}).get("fr_pwm", 0)
            ts       = event.get("timestamp", "")
            seq      = event.get("sequence", "—")
            color    = COLORS.get(command.split(":")[0], "")

            print(
                f"{color}[{ts[11:23]}] "
                f"seq={seq:>5}  command={command:<14} "
                f"fl={fl:+5d}  fr={fr:+5d}"
                f"{RESET}"
            )

    except KeyboardInterrupt:
        print("\n[consumer_motor] 終了")
    finally:
        consumer.close()


if __name__ == "__main__":
    main()
