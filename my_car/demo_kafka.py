#!/usr/bin/env python3
"""
demo_kafka.py
-------------
Freenove 4WD Smart Car ⑤ IBM Kafka 連携デモ

demo_autonomous.py の自律走行に Kafka 送受信を加えたデモ。

───────────────────────────────────────────────────────
データフロー（送信）
  CameraClient → Detector → VisionEventProducer → freenove.vision-events
  MotorClient ← SteeringController  → MotorEventProducer → freenove.motor-events
  定期タイマー → TelemetryProducer  → freenove.telemetry

データフロー（受信）
  freenove.commands → CommandConsumer → MotorClient（安全性チェック済み）
───────────────────────────────────────────────────────

使い方:
    # .env にIBM Event Streams の接続情報を設定してから実行
    cp my_car/.env.example my_car/.env
    # エディタで KAFKA_BOOTSTRAP_SERVERS / KAFKA_PASSWORD を入力

    python my_car/demo_kafka.py --host 192.168.1.100

    # 表示なし（Raspberry Pi 上のヘッドレス実行）
    python my_car/demo_kafka.py --host 192.168.1.100 --no-display

    # YOLOv4-tiny で検出
    python my_car/demo_kafka.py --host 192.168.1.100 --backend yolo

    # Kafka 送信だけ確認（モーター動かさない）
    python my_car/demo_kafka.py --host 192.168.1.100 --dry-run

キー操作（表示モード）:
    [p] 一時停止 / 再開
    [s] スナップショット保存
    [q / ESC] 終了
"""

from __future__ import annotations

import argparse
import os
import sys
import time
import threading

import cv2
import numpy as np

# my_car/ 直下を import パスに追加（demo ファイルは my_car/ 内にある）
sys.path.insert(0, os.path.dirname(__file__))

from camera_client import CameraClient
from detector import make_detector, draw_detections, Detection
from motor_client import MotorClient, SPEED_FULL
from steering import SteeringController, SteeringConfig

from producers.motor_event_producer import MotorEventProducer
from producers.vision_event_producer import VisionEventProducer
from producers.telemetry_producer import TelemetryProducer
from consumers.command_consumer import CommandConsumer


# ------------------------------------------------------------------ #
# FPS カウンタ                                                         #
# ------------------------------------------------------------------ #

class FpsCounter:
    def __init__(self, window: float = 2.0):
        self._window = window
        self._times: list[float] = []

    def tick(self) -> float:
        now = time.monotonic()
        self._times.append(now)
        self._times = [t for t in self._times if t >= now - self._window]
        return len(self._times) / self._window if len(self._times) > 1 else 0.0


# ------------------------------------------------------------------ #
# HUD 描画                                                             #
# ------------------------------------------------------------------ #

def _draw_hud(
    frame: np.ndarray,
    detections: list[Detection],
    duty: tuple[int, int, int, int],
    fps: float,
    paused: bool,
    target_label: str,
    kafka_ok: bool,
) -> np.ndarray:
    out = draw_detections(frame, detections, fps=fps)
    h, w = out.shape[:2]

    fl, _, fr, _ = duty
    if fl == 0 and fr == 0:
        action_text, color = "STOP", (0, 0, 255)
    elif fl > 0 and fr > 0:
        action_text, color = f"FWD  L={fl:+5d} R={fr:+5d}", (0, 255, 0)
    elif fl < 0 and fr < 0:
        action_text, color = f"BWD  L={fl:+5d} R={fr:+5d}", (0, 165, 255)
    else:
        action_text, color = f"TURN L={fl:+5d} R={fr:+5d}", (255, 255, 0)

    # Kafka 接続インジケータ
    kafka_color = (0, 220, 0) if kafka_ok else (0, 0, 255)
    kafka_text = "Kafka OK" if kafka_ok else "Kafka --"
    cv2.putText(out, kafka_text, (w - 100, 22),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, kafka_color, 1, cv2.LINE_AA)

    cv2.putText(out, action_text, (8, h - 10),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1, cv2.LINE_AA)
    cv2.putText(out, f"Target: {target_label}", (8, 40),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1, cv2.LINE_AA)
    if paused:
        cv2.putText(out, "PAUSED", (w // 2 - 50, h // 2),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 0, 255), 2, cv2.LINE_AA)
    return out


# ------------------------------------------------------------------ #
# テレメトリ定期送信スレッド                                          #
# ------------------------------------------------------------------ #

def _read_cpu_temp() -> float | None:
    """Raspberry Pi の CPU 温度を読む。非対応環境は None を返す。"""
    path = "/sys/class/thermal/thermal_zone0/temp"
    try:
        with open(path) as f:
            return int(f.read().strip()) / 1000.0
    except OSError:
        return None


class TelemetryThread(threading.Thread):
    """一定間隔でテレメトリを送信するバックグラウンドスレッド。"""

    def __init__(self, producer: TelemetryProducer, interval: float = 5.0):
        super().__init__(name="telemetry-thread", daemon=True)
        self._producer = producer
        self._interval = interval
        self._stop_event = threading.Event()

    def run(self) -> None:
        while not self._stop_event.wait(self._interval):
            self._producer.publish(cpu_temp_c=_read_cpu_temp())

    def stop(self) -> None:
        self._stop_event.set()


# ------------------------------------------------------------------ #
# コマンド受信ハンドラ                                                 #
# ------------------------------------------------------------------ #

def _make_command_handler(motor: MotorClient, speed_limit: int):
    """CommandConsumer に渡すコールバックを生成する。"""

    def handle(cmd: dict) -> None:
        command = cmd.get("command", "")
        params = cmd.get("parameters", {})
        direction = params.get("direction", "stop")
        speed = min(int(params.get("speed", 1200)), speed_limit)

        if command == "stop" or direction == "stop":
            motor.stop()
            print("[Command] STOP")
        elif command == "move":
            if direction == "forward":
                motor.forward(speed)
                print(f"[Command] forward speed={speed}")
            elif direction == "backward":
                motor.backward(speed)
                print(f"[Command] backward speed={speed}")
            elif direction == "left":
                motor.turn_left(speed)
                print(f"[Command] turn_left speed={speed}")
            elif direction == "right":
                motor.turn_right(speed)
                print(f"[Command] turn_right speed={speed}")
            else:
                print(f"[Command] unknown direction: {direction!r}")
        else:
            print(f"[Command] unknown command: {command!r}")

    return handle


# ------------------------------------------------------------------ #
# メイン処理                                                           #
# ------------------------------------------------------------------ #

def run_demo(
    host: str,
    backend: str,
    target: str,
    conf: float,
    speed: int,
    area_min: float,
    area_max: float,
    steer_gain: float,
    duration: float | None,
    no_display: bool,
    dry_run: bool,
    vision_interval: int,
) -> None:
    print(f"\n{'='*60}")
    print(f" Freenove Smart Car — Kafka 連携デモ")
    print(f" ホスト: {host}  backend: {backend}  target: {target}")
    print(f" Kafka dry-run: {dry_run}  vision 送信間隔: {vision_interval} フレームに 1 回")
    print(f"{'='*60}\n")

    # ---- Kafka Producer / Consumer 初期化 --------------------------
    motor_events = MotorEventProducer(mode="autonomous")
    vision_events = VisionEventProducer()
    telemetry_prod = TelemetryProducer()
    kafka_ok = True  # 接続失敗フラグ（将来の拡張用）

    # テレメトリ定期送信スレッド
    telemetry_thread = TelemetryThread(telemetry_prod, interval=5.0)

    # ---- 検出器 ----------------------------------------------------
    if backend == "haar":
        haar_target = "body" if target == "person" else target
        detector = make_detector("haar", target=haar_target)
        detect_label = haar_target
    else:
        detector = make_detector("yolo", conf_threshold=conf, filter_classes=[target])
        detect_label = target

    # ---- ステアリング ----------------------------------------------
    cfg = SteeringConfig(
        target_label=detect_label,
        base_speed=speed,
        steer_gain=steer_gain,
        area_target_min=area_min,
        area_target_max=area_max,
    )
    ctrl = SteeringController(cfg)

    fps_counter = FpsCounter()
    frame_count = 0
    start_time = time.monotonic()
    paused = False
    snapshot_count = 0
    last_duty: tuple[int, int, int, int] = (0, 0, 0, 0)
    last_target_time = time.monotonic()

    try:
        with MotorClient(host) as motor, CameraClient(host) as cam:
            motor.stop()

            # コマンド Consumer 起動（バックグラウンド）
            cmd_consumer = CommandConsumer(
                on_command=_make_command_handler(motor, speed_limit=speed),
            )
            if not dry_run:
                cmd_consumer.start()
                telemetry_thread.start()

            print("[Kafka デモ] 開始（Ctrl+C または q で停止）\n")

            for frame in cam.frames():
                frame_count += 1
                elapsed = time.monotonic() - start_time
                h_f, w_f = frame.shape[:2]

                # ---- 物体検出 --------------------------------------
                t0 = time.monotonic()
                detections = detector.detect(frame)
                inference_ms = (time.monotonic() - t0) * 1000
                fps = fps_counter.tick()

                # ---- ステアリング計算 ------------------------------
                if not paused:
                    duty = ctrl.compute(detections, w_f, h_f)
                    if any(d != 0 for d in duty):
                        last_target_time = time.monotonic()

                    idle = time.monotonic() - last_target_time
                    if idle > cfg.idle_timeout:
                        duty = (0, 0, 0, 0)

                    if duty != last_duty:
                        motor.set_motor(*duty)
                        # ---- Motor Kafka 送信 ----------------------
                        if not dry_run:
                            fl, bl, fr, br = duty
                            cmd_str = (
                                "stop"
                                if duty == (0, 0, 0, 0)
                                else (
                                    "forward" if fl > 0 and fr > 0
                                    else "backward" if fl < 0 and fr < 0
                                    else "turn"
                                )
                            )
                            motor_events.publish(
                                command=cmd_str,
                                fl=fl, bl=bl, fr=fr, br=br,
                            )
                        last_duty = duty
                else:
                    duty = (0, 0, 0, 0)
                    if duty != last_duty:
                        motor.stop()
                        if not dry_run:
                            motor_events.publish(
                                command="stop:paused",
                                fl=0, bl=0, fr=0, br=0,
                            )
                        last_duty = duty

                # ---- Vision Kafka 送信 -----------------------------
                # 検出あり or 定期送信の場合のみ送る
                if not dry_run and (detections or frame_count % vision_interval == 0):
                    vision_events.publish(
                        frame_id=frame_count,
                        detections=detections,
                        inference_ms=inference_ms,
                    )

                # ---- ログ ------------------------------------------
                if frame_count % 30 == 0 or detections:
                    desc = ctrl.describe(duty)
                    labels = (
                        ", ".join(f"{d.label}({d.conf:.0%})" for d in detections)
                        or "—"
                    )
                    print(
                        f"  [{elapsed:6.1f}s] Frame {frame_count:5d}"
                        f"  FPS {fps:4.1f}"
                        f"  {desc}"
                        f"  det=[{labels}]"
                        f"  infer={inference_ms:.1f}ms"
                    )

                # ---- 表示 ------------------------------------------
                if not no_display:
                    annotated = _draw_hud(
                        frame, detections, duty, fps, paused, detect_label, kafka_ok
                    )
                    cv2.imshow("Freenove Kafka Demo", annotated)
                    key = cv2.waitKey(1) & 0xFF
                    if key in (ord("q"), 27):
                        print("[Kafka デモ] 'q' で終了")
                        break
                    elif key == ord("p"):
                        paused = not paused
                        print(f"[Kafka デモ] {'一時停止' if paused else '再開'}")
                    elif key == ord("s"):
                        path = f"kafka_snap_{snapshot_count:04d}.jpg"
                        cv2.imwrite(path, annotated)
                        snapshot_count += 1
                        print(f"[Kafka デモ] スナップショット: {path}")

                # ---- 時間制限 --------------------------------------
                if duration and elapsed >= duration:
                    print(f"[Kafka デモ] {duration}秒経過、終了します")
                    break

    except StopIteration as e:
        print(f"[Kafka デモ] ストリーム終了: {e}")
    finally:
        cv2.destroyAllWindows()
        telemetry_thread.stop()
        cmd_consumer.stop() if not dry_run else None
        motor_events.close()
        vision_events.close()
        telemetry_prod.close()

    total = time.monotonic() - start_time
    avg_fps = frame_count / total if total > 0 else 0
    print(f"\n[Kafka デモ] 完了")
    print(f"  フレーム数: {frame_count}  平均 FPS: {avg_fps:.1f}  走行時間: {total:.1f}s")


# ------------------------------------------------------------------ #
# CLI                                                                 #
# ------------------------------------------------------------------ #

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Freenove 4WD Smart Car — IBM Kafka 連携デモ",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--host", "-H", required=True,
                        help="Raspberry Pi の IP アドレス")
    parser.add_argument("--backend", "-b", choices=["haar", "yolo"], default="haar",
                        help="検出バックエンド（デフォルト: haar）")
    parser.add_argument("--target", "-t", default="person",
                        help="追跡対象クラス名（デフォルト: person）")
    parser.add_argument("--conf", "-c", type=float, default=0.4,
                        help="[yolo] 信頼度しきい値（デフォルト: 0.4）")
    parser.add_argument("--speed", "-s", type=int, default=1200,
                        help="ベース速度 duty 0-4095（デフォルト: 1200）")
    parser.add_argument("--area-min", type=float, default=0.06,
                        help="前進する最小面積比（デフォルト: 0.06）")
    parser.add_argument("--area-max", type=float, default=0.20,
                        help="後退する最大面積比（デフォルト: 0.20）")
    parser.add_argument("--steer-gain", type=float, default=1.2,
                        help="横方向 P ゲイン（デフォルト: 1.2）")
    parser.add_argument("--duration", "-d", type=float, default=None,
                        help="自動終了までの秒数")
    parser.add_argument("--no-display", action="store_true",
                        help="OpenCV ウィンドウを表示しない")
    parser.add_argument("--dry-run", action="store_true",
                        help="Kafka 送受信を行わず動作のみ確認する")
    parser.add_argument("--vision-interval", type=int, default=5,
                        help="定期 vision-events 送信間隔（フレーム数、デフォルト: 5）")
    args = parser.parse_args()

    try:
        run_demo(
            host=args.host,
            backend=args.backend,
            target=args.target,
            conf=args.conf,
            speed=args.speed,
            area_min=args.area_min,
            area_max=args.area_max,
            steer_gain=args.steer_gain,
            duration=args.duration,
            no_display=args.no_display,
            dry_run=args.dry_run,
            vision_interval=args.vision_interval,
        )
    except KeyboardInterrupt:
        print("\n[Kafka デモ] Ctrl+C で中断しました")
        cv2.destroyAllWindows()
        sys.exit(0)
    except KeyError as e:
        print(f"\n[エラー] 環境変数が未設定です: {e}")
        print("  my_car/.env.example を参考に my_car/.env を作成してください。")
        sys.exit(1)
    except ConnectionRefusedError:
        print(f"\n[エラー] {args.host} に接続できませんでした")
        print("  ポート 5000（モーター）と 8000（カメラ）が開いているか確認してください。")
        sys.exit(1)


if __name__ == "__main__":
    main()
