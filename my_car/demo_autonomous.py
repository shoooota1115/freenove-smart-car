#!/usr/bin/env python3
"""
demo_autonomous.py
------------------
Freenove 4WD Smart Car ④ 自律走行デモ

カメラ映像から物体を検出し、検出結果に基づいてモーターを制御することで
指定ターゲット（デフォルト: person）を追跡する自律走行を行う。

処理フロー:
    CameraClient → Detector → SteeringController → MotorClient

使い方:
    # 人を追跡（Haar Cascade, モデル不要）
    python demo_autonomous.py --host 192.168.1.100

    # YOLOv4-tiny で人を追跡
    python demo_autonomous.py --host 192.168.1.100 --backend yolo

    # 猫を追跡（YOLO のみ）
    python demo_autonomous.py --host 192.168.1.100 --backend yolo --target cat

    # 表示なし（ヘッドレス）
    python demo_autonomous.py --host 192.168.1.100 --no-display

    # 速度を落とす
    python demo_autonomous.py --host 192.168.1.100 --speed 800

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

import cv2
import numpy as np

sys.path.insert(0, os.path.dirname(__file__))
from camera_client import CameraClient
from detector import make_detector, draw_detections, Detection
from motor_client import MotorClient
from steering import SteeringController, SteeringConfig


# ------------------------------------------------------------------
# FPS カウンタ
# ------------------------------------------------------------------

class FpsCounter:
    def __init__(self, window: float = 2.0):
        self._window = window
        self._times: list[float] = []

    def tick(self) -> float:
        now = time.monotonic()
        self._times.append(now)
        self._times = [t for t in self._times if t >= now - self._window]
        return len(self._times) / self._window if len(self._times) > 1 else 0.0


# ------------------------------------------------------------------
# HUD 描画
# ------------------------------------------------------------------

def _draw_hud(
    frame: np.ndarray,
    detections: list[Detection],
    duty: tuple[int, int, int, int],
    fps: float,
    paused: bool,
    target_label: str,
) -> np.ndarray:
    """検出枠 + 操舵情報オーバーレイを描画して返す。"""
    out = draw_detections(frame, detections, fps=fps)
    h, w = out.shape[:2]

    fl, bl, fr, br = duty
    if fl == 0 and fr == 0:
        action_text = "STOP"
        color = (0, 0, 255)
    elif fl > 0 and fr > 0:
        action_text = f"FWD  L={fl:+5d} R={fr:+5d}"
        color = (0, 255, 0)
    elif fl < 0 and fr < 0:
        action_text = f"BWD  L={fl:+5d} R={fr:+5d}"
        color = (0, 165, 255)
    else:
        action_text = f"TURN L={fl:+5d} R={fr:+5d}"
        color = (255, 255, 0)

    # 操舵バー（画面下部中央）
    bar_y = h - 30
    mid_x = w // 2
    norm = max(abs(fl), abs(fr))
    if norm > 0:
        bar_len = int((fl / 2000) * mid_x)
        if bar_len > 0:
            cv2.rectangle(out, (mid_x, bar_y), (mid_x + bar_len, bar_y + 10), (0, 200, 0), cv2.FILLED)
        elif bar_len < 0:
            cv2.rectangle(out, (mid_x + bar_len, bar_y), (mid_x, bar_y + 10), (0, 100, 255), cv2.FILLED)
    cv2.line(out, (mid_x, bar_y - 2), (mid_x, bar_y + 12), (200, 200, 200), 1)

    # テキスト情報
    cv2.putText(out, action_text, (8, h - 10),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1, cv2.LINE_AA)
    cv2.putText(out, f"Target: {target_label}", (8, 40),
                cv2.FONT_HERSHEY_SIMPLEX, 0.5, (200, 200, 200), 1, cv2.LINE_AA)
    if paused:
        cv2.putText(out, "PAUSED", (w // 2 - 50, h // 2),
                    cv2.FONT_HERSHEY_SIMPLEX, 1.2, (0, 0, 255), 2, cv2.LINE_AA)
    return out


# ------------------------------------------------------------------
# メイン処理
# ------------------------------------------------------------------

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
) -> None:
    print(f"\n{'='*55}")
    print(f" Freenove Smart Car — 自律走行デモ")
    print(f" ホスト: {host}  バックエンド: {backend}  ターゲット: {target}")
    print(f" 速度: {speed}  面積範囲: [{area_min:.0%}, {area_max:.0%}]")
    if duration:
        print(f" 実行時間: {duration} 秒")
    print(f"{'='*55}")
    if not no_display:
        print(" キー操作: [p] 一時停止  [s] スナップショット  [q/ESC] 終了\n")

    # --- 検出器 ---
    if backend == "haar":
        # Haar は person 追跡に "body" または "upperbody" を使用
        haar_target = "body" if target == "person" else target
        detector = make_detector("haar", target=haar_target)
        detect_label = haar_target
    else:
        detector = make_detector(
            "yolo",
            conf_threshold=conf,
            filter_classes=[target],
        )
        detect_label = target

    # --- コントローラー ---
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
            print("[自律走行] 開始（Ctrl+C または q で停止）\n")

            for frame in cam.frames():
                frame_count += 1
                elapsed = time.monotonic() - start_time
                h_f, w_f = frame.shape[:2]

                # --- 検出 ---
                detections = detector.detect(frame)
                fps = fps_counter.tick()

                # --- 操舵計算 ---
                if not paused:
                    duty = ctrl.compute(detections, w_f, h_f)
                    if any(d != 0 for d in duty):
                        last_target_time = time.monotonic()

                    # ターゲットが一定時間見えなければ停止
                    idle = time.monotonic() - last_target_time
                    if idle > cfg.idle_timeout:
                        duty = (0, 0, 0, 0)

                    # モーター送信（前回と変化があるときだけ）
                    if duty != last_duty:
                        motor.set_motor(*duty)
                        last_duty = duty
                else:
                    duty = (0, 0, 0, 0)
                    if duty != last_duty:
                        motor.stop()
                        last_duty = duty

                # --- ログ ---
                if frame_count % 10 == 0 or detections:
                    desc = ctrl.describe(duty)
                    labels = ", ".join(f"{d.label}({d.conf:.0%})" for d in detections) or "—"
                    print(f"  [{elapsed:6.1f}s] Frame {frame_count:5d}  FPS {fps:4.1f}"
                          f"  {desc}  det=[{labels}]")

                # --- 表示 ---
                if not no_display:
                    annotated = _draw_hud(frame, detections, duty, fps, paused, detect_label)
                    cv2.imshow("Freenove Autonomous", annotated)
                    key = cv2.waitKey(1) & 0xFF
                    if key in (ord("q"), 27):
                        print("[自律走行] 'q' キーで終了")
                        break
                    elif key == ord("p"):
                        paused = not paused
                        print(f"[自律走行] {'一時停止' if paused else '再開'}")
                    elif key == ord("s"):
                        path = f"auto_snap_{snapshot_count:04d}.jpg"
                        cv2.imwrite(path, annotated)
                        snapshot_count += 1
                        print(f"[自律走行] スナップショット保存: {path}")

                # --- 時間制限 ---
                if duration and elapsed >= duration:
                    print(f"[自律走行] {duration}秒経過、終了します")
                    break

    except StopIteration as e:
        print(f"[自律走行] ストリーム終了: {e}")
    finally:
        cv2.destroyAllWindows()
        # MotorClient.__exit__ が stop() を呼ぶので追加不要

    total_time = time.monotonic() - start_time
    avg_fps = frame_count / total_time if total_time > 0 else 0
    print(f"\n[自律走行] 完了")
    print(f"  受信フレーム数: {frame_count}  平均 FPS: {avg_fps:.1f}")
    print(f"  総走行時間: {total_time:.1f}秒")


# ------------------------------------------------------------------
# CLI
# ------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Freenove 4WD Smart Car 自律走行デモ",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--host", "-H", required=True,
                        help="Raspberry Pi の IP アドレス")
    parser.add_argument("--backend", "-b", choices=["haar", "yolo"], default="haar",
                        help="検出バックエンド: haar（デフォルト）または yolo")
    parser.add_argument("--target", "-t", default="person",
                        help="追跡対象クラス名 (haar: body/face/upperbody, yolo: COCO クラス名)")
    parser.add_argument("--conf", "-c", type=float, default=0.4,
                        help="[yolo] 信頼度しきい値 (デフォルト: 0.4)")
    parser.add_argument("--speed", "-s", type=int, default=1200,
                        help="ベース速度 duty 0-4095 (デフォルト: 1200)")
    parser.add_argument("--area-min", type=float, default=0.06,
                        help="前進する最小面積比 (デフォルト: 0.06 = 6%%)")
    parser.add_argument("--area-max", type=float, default=0.20,
                        help="後退する最大面積比 (デフォルト: 0.20 = 20%%)")
    parser.add_argument("--steer-gain", type=float, default=1.2,
                        help="横方向 P ゲイン (デフォルト: 1.2)")
    parser.add_argument("--duration", "-d", type=float, default=None,
                        help="自動終了までの秒数")
    parser.add_argument("--no-display", action="store_true",
                        help="OpenCV ウィンドウを表示しない（ヘッドレス）")
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
        )
    except KeyboardInterrupt:
        print("\n[自律走行] Ctrl+C で中断しました")
        cv2.destroyAllWindows()
        sys.exit(0)
    except FileNotFoundError as e:
        print(f"\n[エラー] {e}")
        sys.exit(1)
    except ConnectionRefusedError:
        print(f"\n[エラー] サーバーに接続できませんでした ({args.host})")
        print("  ポート 5000（モーター）と 8000（カメラ）が開いているか確認してください。")
        sys.exit(1)


if __name__ == "__main__":
    main()
