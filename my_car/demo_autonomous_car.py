#!/usr/bin/env python3
"""
demo_autonomous_car.py
-----------------------
Freenove 4WD Smart Car ⑦ 自律障害物回避デモ — 車載側（PC から接続）

このスクリプトは PC 上で動かす「IoT デバイス側」コンポーネント。
Raspberry Pi 上の Code/Server/main.py に TCP 接続し、センサー読み取りと
モーター制御を行う。

データフロー:
    [超音波センサー] → TelemetryProducer → freenove.telemetry (Kafka)
    freenove.commands (Kafka) → CommandConsumer → MotorClient → [モーター]

動作:
    1. 超音波センサーを 0.5 秒ごとに Kafka へ送信（高頻度で衝突を早期検知）
    2. サーバーからコマンドを受信してモーターを制御:
        ▶  direction=forward → motor.forward(duty)  低速前進継続
        ◀  direction=left   → motor.turn_left(duty)
                               → duration_ms 後に motor.stop()
                               → 停止して次のコマンドを待つ

速度変換:
    コマンドの speed（0–60 正規化）→ PWM duty: duty = speed * 4095 // 60
    speed=20 → duty ≈ 1365（低速、衝突余裕を確保）

使い方:
    # .env に IBM Event Streams の接続情報を設定してから実行
    python my_car/demo_autonomous_car.py --host 192.168.1.24

    # テレメトリ送信間隔を変更（デフォルト: 0.5 秒）
    python my_car/demo_autonomous_car.py --host 192.168.1.24 --interval 0.3

    # Kafka・モーター接続なしで動作確認（ダミー値 + ログ出力のみ）
    python my_car/demo_autonomous_car.py --host 192.168.1.24 --dry-run
"""

from __future__ import annotations

import argparse
import math
import os
import signal
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(__file__))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"))

# Code/Server のハードウェアモジュールを直接インポートするためパスを追加
from pathlib import Path
_SERVER_DIR = Path(__file__).resolve().parent.parent / "Code" / "Server"
sys.path.insert(0, str(_SERVER_DIR))

# ADC インスタンス（初回呼び出し時に遅延初期化）
_adc = None


def _get_adc():
    """ADC インスタンスをシングルトンで返す（ラズパイ専用）。"""
    global _adc
    if _adc is None:
        _prev_dir = os.getcwd()
        try:
            os.chdir(str(_SERVER_DIR))
            from adc import ADC
            _adc = ADC()
        finally:
            os.chdir(_prev_dir)
    return _adc


def _read_battery_voltage() -> float | None:
    """電池電圧 (V) を ADC から読み取る。"""
    try:
        adc = _get_adc()
        multiplier = 3 if adc.pcb_version == 1 else 2
        return round(adc.read_adc(2) * multiplier, 2)
    except Exception as e:
        print(f"[Sensor] battery read failed: {type(e).__name__}: {e}")
        return None


def _dummy_ultrasonic(seq: int) -> float:
    """dry-run 用ダミー超音波値（50 〜 10 cm を往復）。"""
    return round(30.0 + 20.0 * math.sin(seq * 0.5), 1)


def _dummy_battery(seq: int) -> float:
    """dry-run 用ダミー電池電圧（徐々に低下）。"""
    return round(max(7.0, 8.4 - seq * 0.01), 2)


# ------------------------------------------------------------------ #
# 速度変換                                                             #
# ------------------------------------------------------------------ #

def _speed_to_duty(speed: int) -> int:
    """正規化速度（0–60）を PWM duty 値（0–4095）に変換する。

    speed=20 → duty ≈ 1365
    speed=60 → duty = 4095 (SPEED_FULL)
    """
    return max(0, min(4095, speed * 4095 // 60))


# ------------------------------------------------------------------ #
# メイン処理                                                           #
# ------------------------------------------------------------------ #

def run(host: str, interval: float, dry_run: bool) -> None:
    print(f"\n{'='*65}")
    print(f" Freenove Smart Car — 自律障害物回避デモ (車載側)")
    print(f" ホスト: {host}  送信間隔: {interval}s  dry-run: {dry_run}")
    print(f"{'='*65}\n")

    # --- 各クライアント・プロデューサー・コンシューマーの初期化 ---
    if dry_run:
        telemetry_producer = None
        command_consumer = None
        led = None
        motor = None
        print("[dry-run] Kafka・モーター接続をスキップします。ダミー値を出力します。\n")
    else:
        from producers.telemetry_producer import TelemetryProducer
        from consumers.command_consumer import CommandConsumer
        from led_client import LEDClient
        from motor_client import MotorClient

        telemetry_producer = TelemetryProducer()

        motor = MotorClient(host)
        motor.connect()

        led = LEDClient(host)
        led.connect()
        led.set_all(0, 128, 0)  # 起動時: 緑点灯（正常）

        # ----------------------------------------------------------
        # コマンドコールバック
        # ----------------------------------------------------------
        # バックグラウンドスレッドで呼ばれるため、motor 操作は
        # スレッドセーフに行う。同時に複数コマンドが来ないよう
        # ロックで保護する。
        _motor_lock = threading.Lock()

        def _handle_command(cmd: dict) -> None:
            params    = cmd.get("parameters", {})
            direction = params.get("direction", "stop")
            speed     = params.get("speed", 20)
            dur_ms    = params.get("duration_ms", 0)
            duty      = _speed_to_duty(speed)

            print(
                f"\n  ⚙  コマンド受信: direction={direction!r}"
                f"  speed={speed}(duty={duty})  duration_ms={dur_ms}"
            )

            with _motor_lock:
                if direction == "forward":
                    motor.forward(duty)
                elif direction == "backward":
                    motor.backward(duty)
                elif direction == "left":
                    motor.turn_left(duty)
                    if dur_ms > 0:
                        time.sleep(dur_ms / 1000.0)
                        motor.stop()
                        print(f"  ⏹  左回転完了 → 停止（{dur_ms}ms）")
                elif direction == "right":
                    motor.turn_right(duty)
                    if dur_ms > 0:
                        time.sleep(dur_ms / 1000.0)
                        motor.stop()
                        print(f"  ⏹  右回転完了 → 停止（{dur_ms}ms）")
                elif direction == "stop":
                    motor.stop()

        command_consumer = CommandConsumer(on_command=_handle_command)
        command_consumer.start()

    # --- シグナルハンドラ ---
    def _shutdown(sig, frame):
        print("\n\n終了処理中...")
        if command_consumer:
            command_consumer.stop()
        if motor:
            motor.close()
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
    print(f"{'─'*65}")
    print(f"{'SEQ':>5}  {'超音波 (cm)':>12}  {'電池電圧 (V)':>13}  {'送信'}  {'時刻'}")
    print(f"{'─'*65}")

    seq = 0
    while True:
        seq += 1

        if dry_run:
            ultrasonic = _dummy_ultrasonic(seq)
            battery    = _dummy_battery(seq)
            status     = "skip"
        else:
            # 超音波は LEDClient の既存ソケット経由でサーバーから取得
            ultrasonic = led.read_ultrasonic_cm()
            battery    = _read_battery_voltage()
            status     = "sent"

        ts        = time.strftime("%H:%M:%S")
        ultra_str = f"{ultrasonic:6.1f}" if ultrasonic is not None else "   N/A"
        batt_str  = f"{battery:7.2f}"    if battery   is not None else "   N/A"
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
        description="Freenove 4WD Smart Car — 自律障害物回避デモ (車載側)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--host", "-H", required=True,
                        help="Raspberry Pi の IP アドレス")
    parser.add_argument(
        "--interval", "-i", type=float, default=0.5,
        help="テレメトリ送信間隔（秒、デフォルト: 0.5）",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Kafka・モーター接続なしでダミー値を出力するだけ",
    )
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
