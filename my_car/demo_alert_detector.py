#!/usr/bin/env python3
"""
demo_alert_detector.py
-----------------------
Freenove 4WD Smart Car ⑥ リアルタイム異常検知デモ — PC/サーバー側

このスクリプトは PC（または別サーバー）上で動かす「エッジクラウド側」コンポーネント。

データフロー:
    freenove.telemetry (Kafka) → 異常判定 → freenove.alerts (Kafka)

動作:
    1. freenove.telemetry を購読し、センサー値をリアルタイム監視
    2. 閾値超えを検知したら freenove.alerts へアラートを発行:
        ⚠️  超音波 < --obstacle-threshold cm → alert_type="obstacle"
        🔋 電池電圧  < --battery-threshold V  → alert_type="low_battery"
        ✅ 両方正常（かつ前回がアラート）      → alert_type="clear"
    3. 重複アラートは AlertProducer が自動的に抑制する

ラズパイ側で demo_alert_car.py を動かしてテレメトリを送信すること（別ターミナル）。

使い方:
    # .env に IBM Event Streams の接続情報を設定してから実行
    python my_car/demo_alert_detector.py

    # 閾値をカスタマイズ
    python my_car/demo_alert_detector.py --obstacle-threshold 30 --battery-threshold 7.2

    # Kafka 送信なしでアラート判定だけ確認（アラートをターミナルに表示）
    python my_car/demo_alert_detector.py --dry-run
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


# ------------------------------------------------------------------ #
# 異常判定ロジック                                                      #
# ------------------------------------------------------------------ #

def _detect_alert_type(
    ultrasonic_cm: float | None,
    battery_voltage: float | None,
    obstacle_threshold: float,
    battery_threshold: float,
) -> tuple[str | None, float | None, float | None]:
    """センサー値から alert_type を判定する。

    Returns
    -------
    (alert_type, sensor_value, threshold)
        判定なし(正常継続中)は (None, None, None)
        "obstacle", "low_battery", "clear" のいずれか
    """
    if ultrasonic_cm is not None and ultrasonic_cm < obstacle_threshold:
        return "obstacle", ultrasonic_cm, obstacle_threshold

    if battery_voltage is not None and battery_voltage < battery_threshold:
        return "low_battery", battery_voltage, battery_threshold

    # 両方正常
    return "clear", None, None


def _make_message(alert_type: str, sensor_value: float | None, threshold: float | None) -> str:
    if alert_type == "obstacle":
        return f"Obstacle detected at {sensor_value:.1f} cm (threshold: {threshold:.1f} cm)"
    elif alert_type == "low_battery":
        return f"Low battery: {sensor_value:.2f} V (threshold: {threshold:.2f} V)"
    elif alert_type == "clear":
        return "All sensors normal — alert cleared"
    return ""


# ------------------------------------------------------------------ #
# メイン処理                                                           #
# ------------------------------------------------------------------ #

def run(
    obstacle_threshold: float,
    battery_threshold: float,
    dry_run: bool,
) -> None:
    print(f"\n{'='*65}")
    print(f" Freenove Smart Car — リアルタイム異常検知デモ (検知サーバー側)")
    print(f" 障害物閾値: {obstacle_threshold} cm  /  電池閾値: {battery_threshold} V")
    print(f" dry-run: {dry_run}")
    print(f"{'='*65}\n")

    # --- Kafka Consumer / AlertProducer の初期化 ---
    consumer = create_consumer(
        group_id="freenove-alert-detector",
        topics=[TOPIC_TELEMETRY],
    )
    print(f"[detector] {TOPIC_TELEMETRY} を購読中... (Ctrl+C で終了)\n")

    if dry_run:
        alert_producer = None
        print("[dry-run] アラートは Kafka へ送信されず、ターミナルに表示されます。\n")
    else:
        from producers.alert_producer import AlertProducer
        alert_producer = AlertProducer()

    # ヘッダー表示
    print(f"{'─'*65}")
    print(
        f"{'時刻':>8}  {'seq':>5}  {'超音波(cm)':>11}  "
        f"{'電池(V)':>8}  {'判定':>12}  {'アクション'}"
    )
    print(f"{'─'*65}")

    # --- アラート判定の状態管理 ---
    _last_alert_type: str | None = None  # "obstacle" | "low_battery" | "clear" | None

    try:
        while True:
            msg = consumer.poll(timeout=1.0)
            if msg is None:
                continue
            if msg.error():
                if msg.error().code() == KafkaError._PARTITION_EOF:
                    continue
                print(f"[detector] Kafka error: {msg.error()}", file=sys.stderr)
                continue

            # --- JSON パース ---
            try:
                event: dict = json.loads(msg.value().decode("utf-8"))
            except Exception as e:
                print(f"[detector] Parse error: {e}", file=sys.stderr)
                continue

            sensors      = event.get("sensors", {})
            ultrasonic   = sensors.get("ultrasonic_cm")
            battery      = sensors.get("battery_voltage")
            seq          = event.get("sequence", "—")
            ts           = event.get("timestamp", "")[:19]  # "2026-01-01T12:00:00"
            ts_short     = ts[11:19] if len(ts) >= 19 else ts  # "HH:MM:SS"

            # --- 異常判定 ---
            alert_type, sensor_value, threshold = _detect_alert_type(
                ultrasonic_cm=ultrasonic,
                battery_voltage=battery,
                obstacle_threshold=obstacle_threshold,
                battery_threshold=battery_threshold,
            )

            # clear は前回がアラート状態の時だけ発行
            should_publish = False
            action_str = "—"
            color = _RESET

            if alert_type in ("obstacle", "low_battery"):
                color = _RED if alert_type == "low_battery" else _YELLOW
                if alert_type != _last_alert_type:
                    should_publish = True
                    action_str = f"⚠️  ALERT→{alert_type}"
                else:
                    action_str = f"   継続中({alert_type})"
                _last_alert_type = alert_type
            elif alert_type == "clear":
                color = _GREEN
                if _last_alert_type not in (None, "clear"):
                    # 異常→正常 への復帰
                    should_publish = True
                    action_str = f"✅ CLEAR (was {_last_alert_type})"
                else:
                    action_str = "   正常"
                _last_alert_type = "clear"

            # --- テーブル表示 ---
            ultra_str = f"{ultrasonic:6.1f}" if ultrasonic is not None else "   N/A"
            batt_str  = f"{battery:5.2f}"   if battery   is not None else "  N/A"
            print(
                f"{color}"
                f"{ts_short:>8}  {str(seq):>5}  {ultra_str:>11}  "
                f"{batt_str:>8}  {alert_type or '—':>12}  {action_str}"
                f"{_RESET}"
            )

            # --- アラート発行 ---
            if should_publish:
                msg_text = _make_message(
                    alert_type=alert_type,
                    sensor_value=sensor_value,
                    threshold=threshold,
                )
                if dry_run:
                    print(
                        f"{_BOLD}  [dry-run] PUBLISH alert:"
                        f" type={alert_type!r}  val={sensor_value}  msg={msg_text!r}"
                        f"{_RESET}"
                    )
                else:
                    published = alert_producer.publish(
                        alert_type=alert_type,
                        sensor_value=sensor_value if sensor_value is not None else 0.0,
                        threshold=threshold if threshold is not None else 0.0,
                        message=msg_text,
                    )
                    if published:
                        print(
                            f"{_BOLD}  → freenove.alerts へ送信:"
                            f" type={alert_type!r}  val={sensor_value}{_RESET}"
                        )

    except KeyboardInterrupt:
        print(f"\n\n[detector] 終了")
    finally:
        consumer.close()
        if alert_producer:
            alert_producer.close()


# ------------------------------------------------------------------ #
# CLI                                                                 #
# ------------------------------------------------------------------ #

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Freenove 4WD Smart Car — リアルタイム異常検知デモ (検知サーバー側)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--obstacle-threshold", type=float, default=20.0,
        help="障害物検知閾値 cm（デフォルト: 20.0）",
    )
    parser.add_argument(
        "--battery-threshold", type=float, default=7.0,
        help="電池低下閾値 V（デフォルト: 7.0）",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="アラートを Kafka へ送信せず、ターミナルに表示するだけ",
    )
    args = parser.parse_args()

    try:
        run(
            obstacle_threshold=args.obstacle_threshold,
            battery_threshold=args.battery_threshold,
            dry_run=args.dry_run,
        )
    except KeyError as e:
        print(f"\n[エラー] 環境変数が未設定です: {e}")
        print("  my_car/.env.example を参考に my_car/.env を作成してください。")
        sys.exit(1)


if __name__ == "__main__":
    main()
