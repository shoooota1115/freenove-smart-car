#!/usr/bin/env python3
"""
demo_autonomous_car.py
-----------------------
Freenove 4WD Smart Car ⑦ 自律障害物回避デモ — 車載側（PC から接続）

このスクリプトはラズパイ上（または PC から接続）で動かす「IoT デバイス側」。
Raspberry Pi 上の Code/Server/main.py に TCP 接続し、センサー読み取りと
モーター制御を行う。

サーバー（Code/Server/main.py）は max_clients=1 のため、接続は MotorClient
の1本のソケットのみを使う。超音波センサーの読み取りも同じソケット経由で行う
（LEDClient との接続競合を避けるため LEDClient は使わない）。

データフロー:
    [超音波センサー] → MotorClient(TCP) → TelemetryProducer → freenove.telemetry
    freenove.commands → CommandConsumer → MotorClient → [モーター]

動作:
    1. 超音波センサーを --interval 秒ごとに Kafka へ送信
    2. サーバーからコマンドを受信してモーターを制御:
        ▶  direction=forward → motor.forward(duty)  低速前進
        ◀  direction=left   → motor.turn_left(duty) → duration_ms 後に stop
    3. --duration 秒後にモーターを自動停止して終了（デフォルト: 30 秒）

速度変換:
    コマンドの speed（0–60 正規化）→ PWM duty: duty = speed * 4095 // 60
    speed=20 → duty ≈ 1365

使い方:
    python my_car/demo_autonomous_car.py --host 192.168.1.24

    # 自動停止時間を変更（デフォルト: 30 秒）
    python my_car/demo_autonomous_car.py --host 192.168.1.24 --duration 60

    # Kafka・モーター接続なしで動作確認
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

from pathlib import Path
_SERVER_DIR = Path(__file__).resolve().parent.parent / "Code" / "Server"
sys.path.insert(0, str(_SERVER_DIR))

_adc = None


def _get_adc():
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
    return round(max(7.0, 8.4 - seq * 0.01), 2)


def _speed_to_duty(speed: int) -> int:
    """正規化速度（0–60）を PWM duty 値（0–4095）に変換する。"""
    return max(0, min(4095, speed * 4095 // 60))


# ------------------------------------------------------------------ #
# メイン処理                                                           #
# ------------------------------------------------------------------ #

def run(host: str, interval: float, duration: float, dry_run: bool) -> None:
    print(f"\n{'='*65}")
    print(f" Freenove Smart Car — 自律障害物回避デモ (車載側)")
    print(f" ホスト: {host}  送信間隔: {interval}s  自動停止: {duration}s  dry-run: {dry_run}")
    print(f"{'='*65}\n")

    if dry_run:
        telemetry_producer = None
        command_consumer = None
        motor = None
        print("[dry-run] Kafka・モーター接続をスキップします。ダミー値を出力します。\n")
    else:
        from producers.telemetry_producer import TelemetryProducer
        from consumers.command_consumer import CommandConsumer
        from motor_client import MotorClient

        telemetry_producer = TelemetryProducer()

        # MotorClient 1本だけ接続（超音波読み取りも兼用）
        motor = MotorClient(host)
        motor.connect()

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

    # --- グレースフルシャットダウン ---
    def _shutdown(reason: str) -> None:
        print(f"\n\n[{reason}] 終了処理中...")
        if not dry_run:
            if command_consumer:
                command_consumer.stop()
            if motor:
                motor.close()   # close() 内で stop() を呼ぶ
            if telemetry_producer:
                telemetry_producer.close()
        print("終了しました。")
        sys.exit(0)

    def _sig_handler(sig, frame):
        _shutdown("Ctrl+C")

    signal.signal(signal.SIGINT, _sig_handler)
    signal.signal(signal.SIGTERM, _sig_handler)

    # --- 自動停止フラグ（メインループを止めるため Event を使う）---
    _stop_event = threading.Event()

    # --- テレメトリ送信ループ ---
    print(f"テレメトリ送信ループ開始（{duration:.0f}秒後に自動停止 / Ctrl+C で即停止）")
    print(f"{'─'*65}")
    print(f"{'SEQ':>5}  {'超音波 (cm)':>12}  {'電池電圧 (V)':>13}  {'送信'}  {'時刻'}")
    print(f"{'─'*65}")

    seq = 0
    start = time.monotonic()
    while not _stop_event.is_set():
        seq += 1
        elapsed = time.monotonic() - start

        # 自動停止チェック
        if elapsed >= duration:
            print(f"\n⏱  {duration:.0f}秒経過 — 自動停止します")
            break

        if dry_run:
            ultrasonic = _dummy_ultrasonic(seq)
            battery    = _dummy_battery(seq)
            status     = "skip"
        else:
            # 超音波は MotorClient の同一ソケット経由で読み取る
            ultrasonic = motor.read_ultrasonic_cm()
            battery    = _read_battery_voltage()
            status     = "sent"

        ts        = time.strftime("%H:%M:%S")
        ultra_str = f"{ultrasonic:6.1f}" if ultrasonic is not None else "   N/A"
        batt_str  = f"{battery:7.2f}"    if battery   is not None else "   N/A"
        elapsed   = time.monotonic() - start
        print(f"{seq:>5}  {ultra_str:>12}  {batt_str:>13}  {status:>4}  {ts}  [{elapsed:.0f}s/{duration:.0f}s]")

        if not dry_run and telemetry_producer:
            telemetry_producer.publish(
                ultrasonic_cm=ultrasonic,
                battery_voltage=battery,
            )

        # read_ultrasonic_cm が 0.55s ブロックするため、残り待機時間だけ sleep する
        wait = max(0.0, interval - 0.55) if not dry_run else interval
        time.sleep(wait)

    # ループ終了後にシャットダウン
    _shutdown("自動停止")


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
        "--duration", "-d", type=float, default=30.0,
        help="自動停止までの秒数（デフォルト: 30 秒）",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Kafka・モーター接続なしでダミー値を出力するだけ",
    )
    args = parser.parse_args()

    try:
        run(
            host=args.host,
            interval=args.interval,
            duration=args.duration,
            dry_run=args.dry_run,
        )
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
