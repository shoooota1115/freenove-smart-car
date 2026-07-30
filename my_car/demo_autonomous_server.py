#!/usr/bin/env python3
"""
demo_autonomous_server.py
--------------------------
Freenove 4WD Smart Car ⑦ 自律障害物回避デモ — サーバー側

このスクリプトは PC（または別サーバー）上で動かす「自律制御エンジン」。

データフロー:
    freenove.telemetry (Kafka) → 障害物判定 → freenove.commands (Kafka)

動作:
    1. freenove.telemetry を購読し、超音波センサー値をリアルタイム監視
    2. 障害物判定に基づいてモーターコマンドを送信:
        ✅ 超音波 ≥ --threshold cm → direction=forward, speed=20（低速前進）
        ⚠️  超音波 < --threshold cm → direction=left, duration_ms=650（約90度左回転）
    3. forward は同じ状態が続く場合は再送しない（重複抑制）
    4. left は障害物検知のたびに毎回送信（ワンショット回避）

衝突防止パラメータ:
    - テレメトリ間隔: 0.5 秒（車載側 demo_autonomous_car.py のデフォルト）
    - 前進速度: speed=20（PWM duty ≈ 1365、低速で衝突余裕を確保）
    - 障害物検知閾値: 30 cm（デフォルト）
    - 左回転時間: 650 ms（4WD スキッドステアで約 90 度）

車載側で demo_autonomous_car.py を動かしてテレメトリを送信すること（別ターミナル）。

使い方:
    # .env に IBM Event Streams の接続情報を設定してから実行
    python my_car/demo_autonomous_server.py

    # 障害物検知閾値をカスタマイズ
    python my_car/demo_autonomous_server.py --threshold 40

    # 左回転時間をカスタマイズ（実機調整用）
    python my_car/demo_autonomous_server.py --turn-duration 700

    # Kafka 送信なしで判定結果だけ確認
    python my_car/demo_autonomous_server.py --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

from kafka.client import create_consumer
from kafka.schemas import TOPIC_TELEMETRY
from confluent_kafka import KafkaError


# ANSI カラー
_RED    = "\033[31m"
_YELLOW = "\033[33m"
_GREEN  = "\033[32m"
_CYAN   = "\033[36m"
_RESET  = "\033[0m"
_BOLD   = "\033[1m"

# 前進速度（正規化スケール 0–60）
_FORWARD_SPEED = 20

# 左回転時間 ms（約 90 度）
_DEFAULT_TURN_DURATION_MS = 650


# ------------------------------------------------------------------ #
# 障害物判定ロジック                                                   #
# ------------------------------------------------------------------ #

def _detect(
    ultrasonic_cm: float | None,
    threshold: float,
) -> str:
    """センサー値から direction を判定する。

    Returns
    -------
    "left" | "forward"
        障害物あり → "left"、なし / 計測不能 → "forward"
    """
    if ultrasonic_cm is not None and ultrasonic_cm < threshold:
        return "left"
    return "forward"


# ------------------------------------------------------------------ #
# メイン処理                                                           #
# ------------------------------------------------------------------ #

def run(
    threshold: float,
    turn_duration_ms: int,
    dry_run: bool,
) -> None:
    print(f"\n{'='*65}")
    print(f" Freenove Smart Car — 自律障害物回避デモ (制御サーバー側)")
    print(f" 障害物閾値: {threshold} cm  /  左回転時間: {turn_duration_ms} ms")
    print(f" 前進速度: speed={_FORWARD_SPEED}  /  dry-run: {dry_run}")
    print(f"{'='*65}\n")

    # --- Kafka Consumer / CommandProducer の初期化 ---
    consumer = create_consumer(
        group_id="freenove-auto-server",
        topics=[TOPIC_TELEMETRY],
    )
    print(f"[server] {TOPIC_TELEMETRY} を購読中... (Ctrl+C で終了)\n")

    if dry_run:
        command_producer = None
        print("[dry-run] コマンドは Kafka へ送信されず、ターミナルに表示されます。\n")
    else:
        from producers.command_producer import CommandProducer
        command_producer = CommandProducer()

    # ヘッダー表示
    print(f"{'─'*70}")
    print(
        f"{'時刻':>8}  {'seq':>5}  {'超音波(cm)':>11}  "
        f"{'判定':>8}  {'アクション'}"
    )
    print(f"{'─'*70}")

    # 前回送信方向（重複抑制用）
    _last_direction: str | None = None

    try:
        while True:
            msg = consumer.poll(timeout=1.0)
            if msg is None:
                continue
            if msg.error():
                if msg.error().code() == KafkaError._PARTITION_EOF:
                    continue
                print(f"[server] Kafka error: {msg.error()}", file=sys.stderr)
                continue

            # --- JSON パース ---
            try:
                event: dict = json.loads(msg.value().decode("utf-8"))
            except Exception as e:
                print(f"[server] Parse error: {e}", file=sys.stderr)
                continue

            sensors      = event.get("sensors", {})
            ultrasonic   = sensors.get("ultrasonic_cm")
            seq          = event.get("sequence", "—")
            ts           = event.get("timestamp", "")
            ts_short     = ts[11:19] if len(ts) >= 19 else ts  # "HH:MM:SS"

            # --- 障害物判定 ---
            direction = _detect(ultrasonic, threshold)

            # --- 送信判定 ---
            # left  : 障害物検知のたびに毎回送信（ワンショット回避）
            # forward: 前回も forward なら再送しない（重複抑制）
            should_send = (direction == "left") or (direction != _last_direction)

            ultra_str = f"{ultrasonic:6.1f}" if ultrasonic is not None else "   N/A"

            if direction == "left":
                color = _YELLOW
                action = f"⚠️  OBSTACLE → LEFT {turn_duration_ms}ms"
            else:
                color = _GREEN
                if should_send:
                    action = f"▶  FORWARD (speed={_FORWARD_SPEED})"
                else:
                    action = f"   前進継続"

            print(
                f"{color}"
                f"{ts_short:>8}  {str(seq):>5}  {ultra_str:>11}  "
                f"{direction:>8}  {action}"
                f"{_RESET}"
            )

            # --- コマンド送信 ---
            if should_send:
                if dry_run:
                    if direction == "left":
                        print(
                            f"{_BOLD}  [dry-run] PUBLISH: direction=left"
                            f"  duration_ms={turn_duration_ms}{_RESET}"
                        )
                    else:
                        print(
                            f"{_BOLD}  [dry-run] PUBLISH: direction=forward"
                            f"  speed={_FORWARD_SPEED}{_RESET}"
                        )
                else:
                    if direction == "left":
                        command_producer.publish(
                            direction="left",
                            speed=_FORWARD_SPEED,
                            duration_ms=turn_duration_ms,
                        )
                    else:
                        command_producer.publish(
                            direction="forward",
                            speed=_FORWARD_SPEED,
                            duration_ms=0,
                        )
                _last_direction = direction

    except KeyboardInterrupt:
        print(f"\n\n[server] 終了")
    finally:
        consumer.close()
        if command_producer:
            command_producer.close()


# ------------------------------------------------------------------ #
# CLI                                                                 #
# ------------------------------------------------------------------ #

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Freenove 4WD Smart Car — 自律障害物回避デモ (制御サーバー側)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--threshold", type=float, default=40.0,
        help="障害物検知閾値 cm（デフォルト: 40.0）",
    )
    parser.add_argument(
        "--turn-duration", type=int, default=_DEFAULT_TURN_DURATION_MS,
        dest="turn_duration_ms",
        help=f"左回転持続時間 ms（デフォルト: {_DEFAULT_TURN_DURATION_MS}、約90度）",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="コマンドを Kafka へ送信せず、ターミナルに表示するだけ",
    )
    args = parser.parse_args()

    try:
        run(
            threshold=args.threshold,
            turn_duration_ms=args.turn_duration_ms,
            dry_run=args.dry_run,
        )
    except KeyError as e:
        print(f"\n[エラー] 環境変数が未設定です: {e}")
        print("  my_car/.env.example を参考に my_car/.env を作成してください。")
        sys.exit(1)


if __name__ == "__main__":
    main()
