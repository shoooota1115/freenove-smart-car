#!/usr/bin/env python3
"""
demo_alert_car.py
-----------------
Freenove 4WD Smart Car ⑥ リアルタイム異常検知デモ — ラズパイ側

このスクリプトはラズパイ上で動かす「IoT デバイス側」コンポーネント。

データフロー:
    [Freenove センサー] → TelemetryProducer → freenove.telemetry (Kafka)
    freenove.alerts (Kafka) → AlertConsumer → LEDClient → [Freenove LED]

動作:
    1. 超音波センサー + 電池電圧を --interval 秒ごとに Kafka へ送信
    2. アラートを受信したら LED で視覚的に通知:
        🟡 obstacle   (障害物接近) → 黄色点滅
        🔴 low_battery (電池低下)   → 全 LED 赤点灯
        🟢 clear      (正常復帰)    → 全 LED 緑点灯

PC 側で demo_alert_detector.py を動かしてアラート判定を行う（別ターミナル）。

使い方:
    # .env に IBM Event Streams の接続情報を設定してから実行
    cp my_car/.env.example my_car/.env

    python my_car/demo_alert_car.py --host 192.168.1.24

    # 送信間隔を変更（デフォルト: 2 秒）
    python my_car/demo_alert_car.py --host 192.168.1.24 --interval 1

    # Kafka 接続なしで動作確認（ダミーセンサー値 + LED 出力なし）
    python my_car/demo_alert_car.py --host 192.168.1.24 --dry-run
"""

from __future__ import annotations

import argparse
import math
import os
import signal
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))


# ------------------------------------------------------------------ #
# センサー読み取り（ラズパイ実機用）                                    #
# ------------------------------------------------------------------ #

def _read_ultrasonic_cm(host: str) -> float | None:
    """超音波センサーから距離 (cm) を読み取る。

    Freenove サーバーへ CMD_SONIC コマンドを送って応答を受信する。
    接続エラー時は None を返す。
    """
    import socket
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(2.0)
            s.connect((host, 5000))
            s.sendall(b"CMD_SONIC#\n")
            data = s.recv(64).decode("utf-8", errors="ignore").strip()
            # 応答フォーマット: "CMD_SONIC#<value>" or "<value>"
            parts = data.split("#")
            val = float(parts[-1])
            return round(val, 1)
    except Exception:
        return None


def _read_battery_voltage(host: str) -> float | None:
    """電池電圧 (V) を読み取る。

    Freenove サーバーへ CMD_POWER コマンドを送って応答を受信する。
    接続エラー時は None を返す。
    """
    import socket
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(2.0)
            s.connect((host, 5000))
            s.sendall(b"CMD_POWER#\n")
            data = s.recv(64).decode("utf-8", errors="ignore").strip()
            parts = data.split("#")
            val = float(parts[-1])
            return round(val, 2)
    except Exception:
        return None


def _dummy_ultrasonic(seq: int) -> float:
    """dry-run 用ダミー超音波値（30 〜 5 cm を往復）。"""
    return round(17.5 + 12.5 * math.sin(seq * 0.4), 1)


def _dummy_battery(seq: int) -> float:
    """dry-run 用ダミー電池電圧（徐々に低下し 6.8V まで下がる）。"""
    return round(max(6.8, 8.0 - seq * 0.02), 2)


# ------------------------------------------------------------------ #
# メイン処理                                                           #
# ------------------------------------------------------------------ #

def run(host: str, interval: float, dry_run: bool) -> None:
    print(f"\n{'='*60}")
    print(f" Freenove Smart Car — リアルタイム異常検知デモ (デバイス側)")
    print(f" ホスト: {host}  送信間隔: {interval}s  dry-run: {dry_run}")
    print(f"{'='*60}\n")

    # --- Kafka / LED の初期化 ---
    if dry_run:
        telemetry_producer = None
        alert_consumer = None
        led = None
        print("[dry-run] Kafka・LED 接続をスキップします。ダミー値を出力します。\n")
    else:
        from producers.telemetry_producer import TelemetryProducer
        from consumers.alert_consumer import AlertConsumer
        from led_client import LEDClient

        telemetry_producer = TelemetryProducer()
        led = LEDClient(host)
        led.connect()
        led.set_all(0, 128, 0)  # 起動時: 緑点灯（正常）

        def _handle_alert(alert: dict) -> None:
            alert_type = alert.get("alert_type")
            sensor_val = alert.get("sensor_value")
            msg = alert.get("message", "")
            print(f"\n  🔔 アラート受信: {alert_type}  sensor={sensor_val}  {msg}")
            if alert_type == "obstacle":
                led.blink(255, 200, 0, count=3, interval=0.25)   # 黄色点滅
            elif alert_type == "low_battery":
                led.set_all(255, 0, 0)                            # 赤点灯
            elif alert_type == "clear":
                led.set_all(0, 128, 0)                            # 緑点灯

        alert_consumer = AlertConsumer(on_alert=_handle_alert)
        alert_consumer.start()

    # --- シグナルハンドラ ---
    def _shutdown(sig, frame):
        print("\n\n終了処理中...")
        if alert_consumer:
            alert_consumer.stop()
        if telemetry_producer:
            telemetry_producer.close()
        if led:
            led.close()
        print("終了しました。")
        sys.exit(0)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    # --- テレメトリ送信ループ ---
    print(f"テレメトリ送信ループ開始（Ctrl+C で停止）")
    print(f"{'─'*60}")
    print(f"{'SEQ':>5}  {'超音波 (cm)':>12}  {'電池電圧 (V)':>13}  {'送信'}  {'時刻'}")
    print(f"{'─'*60}")

    seq = 0
    while True:
        seq += 1

        if dry_run:
            ultrasonic = _dummy_ultrasonic(seq)
            battery    = _dummy_battery(seq)
        else:
            ultrasonic = _read_ultrasonic_cm(host)
            battery    = _read_battery_voltage(host)

        status = "skip" if dry_run else "sent"
        ts = time.strftime("%H:%M:%S")
        ultra_str = f"{ultrasonic:6.1f}" if ultrasonic is not None else "   N/A"
        batt_str  = f"{battery:7.2f}" if battery is not None else "   N/A"
        print(f"{seq:>5}  {ultra_str:>12}  {batt_str:>13}  {status:>4}  {ts}")

        if not dry_run and telemetry_producer:
            telemetry_producer.publish(
                ultrasonic_cm=ultrasonic,
                battery_voltage=battery,
            )

        time.sleep(interval)


# ------------------------------------------------------------------ #
# CLI                                                                 #
# ------------------------------------------------------------------ #

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Freenove 4WD Smart Car — リアルタイム異常検知デモ (デバイス側)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--host", "-H", required=True,
                        help="Raspberry Pi の IP アドレス")
    parser.add_argument("--interval", "-i", type=float, default=2.0,
                        help="テレメトリ送信間隔（秒、デフォルト: 2.0）")
    parser.add_argument("--dry-run", action="store_true",
                        help="Kafka 接続なしでダミー値を出力するだけ")
    args = parser.parse_args()

    try:
        run(host=args.host, interval=args.interval, dry_run=args.dry_run)
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
