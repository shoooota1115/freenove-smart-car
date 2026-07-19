#!/usr/bin/env python3
"""
demo_detection.py
-----------------
Freenove 4WD Smart Car ③ OpenCV 物体検出デモ

カメラストリームを受信しながらリアルタイムで物体検出を行い、
OpenCV ウィンドウに検出枠を重ね描きして表示する。
クラウド/LLM 不要、完全ローカル動作。

バックエンド:
  haar (デフォルト) : OpenCV 内蔵 Haar Cascade。追加モデル不要。
  yolo             : YOLOv4-tiny + cv2.dnn。80クラス COCO 汎用検出。
                     事前に: python download_models.py

使い方:
    # Haar Cascade で顔検出（モデルダウンロード不要）
    python demo_detection.py --host 192.168.1.100

    # Haar Cascade でフルボディ検出
    python demo_detection.py --host 192.168.1.100 --backend haar --target body

    # YOLOv4-tiny で人・車のみ検出
    python demo_detection.py --host 192.168.1.100 --backend yolo --filter person car

    # YOLOv4-tiny で全クラス検出、10秒後に自動終了
    python demo_detection.py --host 192.168.1.100 --backend yolo --duration 10

    # ヘッドレス + 検出ログを CSV に保存
    python demo_detection.py --host 192.168.1.100 --no-display --log detections.csv
"""

import argparse
import csv
import os
import sys
import time

import cv2

sys.path.insert(0, os.path.dirname(__file__))
from camera_client import CameraClient
from detector import make_detector, draw_detections


# ------------------------------------------------------------------
# FPS カウンタ（camera_client と重複しないよう独立実装）
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
# メイン処理
# ------------------------------------------------------------------

def run_demo(
    host: str,
    backend: str,
    target: str,
    filter_classes: list[str] | None,
    conf: float,
    duration: float | None,
    no_display: bool,
    log_path: str | None,
) -> None:
    print(f"\n{'='*50}")
    print(f" Freenove Smart Car — OpenCV 物体検出デモ")
    print(f" ホスト: {host}  バックエンド: {backend}")
    if backend == "haar":
        print(f" ターゲット: {target}")
    elif filter_classes:
        print(f" 検出クラス: {filter_classes}")
    print(f" 信頼度しきい値: {conf}")
    if duration:
        print(f" 実行時間: {duration} 秒")
    print(f"{'='*50}")
    print(" キー操作:  [s] スナップショット  [q / ESC] 終了\n")

    # 検出器の初期化
    if backend == "haar":
        detector = make_detector("haar", target=target)
    else:
        detector = make_detector(
            "yolo",
            conf_threshold=conf,
            filter_classes=filter_classes or None,
        )

    fps_counter = FpsCounter()
    frame_count = 0
    detect_count = 0
    snapshot_count = 0
    start_time = time.monotonic()

    # CSV ログの準備
    csv_writer = None
    csv_file = None
    if log_path:
        csv_file = open(log_path, "w", newline="", encoding="utf-8")
        csv_writer = csv.writer(csv_file)
        csv_writer.writerow(["frame", "elapsed_s", "label", "conf", "x", "y", "w", "h"])
        print(f"[デモ] 検出ログ → {log_path}")

    try:
        with CameraClient(host) as cam:
            for frame in cam.frames():
                frame_count += 1
                elapsed = time.monotonic() - start_time

                # --- 物体検出 ---
                detections = detector.detect(frame)
                detect_count += len(detections)
                fps = fps_counter.tick()

                # CSV 書き込み
                if csv_writer and detections:
                    for d in detections:
                        csv_writer.writerow([
                            frame_count, f"{elapsed:.3f}",
                            d.label, f"{d.conf:.3f}",
                            *d.box,
                        ])

                # 標準出力（ヘッドレスモード or 検出あり時）
                if detections and (no_display or frame_count % 10 == 0):
                    labels = ", ".join(f"{d.label}({d.conf:.0%})" for d in detections)
                    print(f"  [{elapsed:6.1f}s] Frame {frame_count:5d}  FPS {fps:5.1f}  → {labels}")

                # --- 描画・表示 ---
                if not no_display:
                    annotated = draw_detections(frame, detections, fps=fps)
                    cv2.imshow("Freenove Detection", annotated)
                    key = cv2.waitKey(1) & 0xFF
                    if key in (ord("q"), 27):
                        print("[デモ] 'q' キーで終了")
                        break
                    if key == ord("s"):
                        path = f"detection_snap_{snapshot_count:04d}.jpg"
                        cv2.imwrite(path, annotated)
                        snapshot_count += 1
                        print(f"[デモ] スナップショット保存: {path}")

                # 時間制限チェック
                if duration and elapsed >= duration:
                    print(f"[デモ] {duration}秒経過、終了します")
                    break

    except StopIteration as e:
        print(f"[デモ] ストリーム終了: {e}")
    finally:
        cv2.destroyAllWindows()
        if csv_file:
            csv_file.close()

    total_time = time.monotonic() - start_time
    avg_fps = frame_count / total_time if total_time > 0 else 0
    print(f"\n[デモ] 完了")
    print(f"  受信フレーム数: {frame_count}  平均 FPS: {avg_fps:.1f}")
    print(f"  総検出数: {detect_count}")
    if log_path:
        print(f"  ログ保存: {log_path}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Freenove 4WD Smart Car OpenCV 物体検出デモ",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--host", "-H", required=True,
                        help="Raspberry Pi の IP アドレス")
    parser.add_argument("--backend", "-b", choices=["haar", "yolo"], default="haar",
                        help="検出バックエンド: haar（デフォルト）または yolo")
    parser.add_argument("--target", "-t", default="face",
                        help="[haar] 検出対象: face / eye / body / upperbody (デフォルト: face)")
    parser.add_argument("--filter", "-f", nargs="+", dest="filter_classes",
                        metavar="CLASS",
                        help="[yolo] 検出するクラス名 (例: person car cat)")
    parser.add_argument("--conf", "-c", type=float, default=0.4,
                        help="[yolo] 信頼度しきい値 0.0-1.0 (デフォルト: 0.4)")
    parser.add_argument("--duration", "-d", type=float, default=None,
                        help="自動終了までの秒数")
    parser.add_argument("--no-display", action="store_true",
                        help="OpenCV ウィンドウを表示しない")
    parser.add_argument("--log", "-l", default=None,
                        help="検出結果を CSV ファイルに保存")
    args = parser.parse_args()

    try:
        run_demo(
            host=args.host,
            backend=args.backend,
            target=args.target,
            filter_classes=args.filter_classes,
            conf=args.conf,
            duration=args.duration,
            no_display=args.no_display,
            log_path=args.log,
        )
    except KeyboardInterrupt:
        print("\n[デモ] Ctrl+C で中断しました")
        cv2.destroyAllWindows()
        sys.exit(0)
    except FileNotFoundError as e:
        print(f"\n[エラー] {e}")
        sys.exit(1)
    except ConnectionRefusedError:
        print(f"\n[エラー] カメラストリームに接続できませんでした ({args.host}:8000)")
        print("  サーバーが起動しているか確認してください。")
        sys.exit(1)


if __name__ == "__main__":
    main()
