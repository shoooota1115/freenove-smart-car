#!/usr/bin/env python3
"""
demo_camera.py
--------------
Freenove 4WD Smart Car ② カメラ取得デモ

機能:
  - TCP:8000 からビデオストリームを受信
  - OpenCV ウィンドウにリアルタイム表示
  - 's' キーでスナップショット保存
  - 'q' / ESC キーで終了
  - --save-dir 指定で全フレームを連番 JPEG として保存
  - --duration 指定で指定秒数後に自動終了

使い方:
    python demo_camera.py --host 192.168.1.100
    python demo_camera.py --host 192.168.1.100 --duration 10
    python demo_camera.py --host 192.168.1.100 --save-dir ./frames --no-display
"""

import argparse
import os
import sys
import time

import cv2

sys.path.insert(0, os.path.dirname(__file__))
from camera_client import CameraClient


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
        cutoff = now - self._window
        self._times = [t for t in self._times if t >= cutoff]
        return len(self._times) / self._window if len(self._times) > 1 else 0.0


# ------------------------------------------------------------------
# メイン処理
# ------------------------------------------------------------------

def run_demo(
    host: str,
    duration: float | None,
    save_dir: str | None,
    no_display: bool,
) -> None:
    print(f"\n{'='*45}")
    print(f" Freenove Smart Car — カメラ取得デモ")
    print(f" ホスト: {host}")
    if duration:
        print(f" 録画時間: {duration} 秒")
    if save_dir:
        print(f" 保存先: {save_dir}")
    print(f" 表示: {'なし' if no_display else 'OpenCV ウィンドウ'}")
    print(f"{'='*45}")
    print(" キー操作:  [s] スナップショット  [q / ESC] 終了\n")

    if save_dir:
        os.makedirs(save_dir, exist_ok=True)

    fps_counter = FpsCounter()
    frame_count = 0
    snapshot_count = 0
    start_time = time.monotonic()

    try:
        with CameraClient(host) as cam:
            for frame in cam.frames():
                frame_count += 1
                fps = fps_counter.tick()
                elapsed = time.monotonic() - start_time

                # オーバーレイ描画
                display = frame.copy()
                cv2.putText(
                    display,
                    f"FPS: {fps:.1f}  Frame: {frame_count}  Time: {elapsed:.1f}s",
                    (8, 22),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.55,
                    (0, 255, 0),
                    1,
                    cv2.LINE_AA,
                )

                # ウィンドウ表示
                if not no_display:
                    cv2.imshow("Freenove Camera", display)
                    key = cv2.waitKey(1) & 0xFF
                    if key in (ord("q"), 27):  # 'q' or ESC
                        print("[デモ] 'q' キーで終了")
                        break
                    if key == ord("s"):
                        snap_path = f"snapshot_{snapshot_count:04d}.jpg"
                        cv2.imwrite(snap_path, frame)
                        snapshot_count += 1
                        print(f"[デモ] スナップショット保存: {snap_path}")

                # フレーム連番保存
                if save_dir:
                    path = os.path.join(save_dir, f"frame_{frame_count:06d}.jpg")
                    cv2.imwrite(path, frame)

                # 時間制限チェック
                if duration and elapsed >= duration:
                    print(f"[デモ] {duration}秒経過、終了します")
                    break

                # 統計を定期出力（表示なしモード向け）
                if no_display and frame_count % 30 == 0:
                    print(f"  フレーム: {frame_count}  FPS: {fps:.1f}  経過: {elapsed:.1f}s")

    except StopIteration as e:
        print(f"[デモ] ストリーム終了: {e}")
    finally:
        cv2.destroyAllWindows()

    total_time = time.monotonic() - start_time
    avg_fps = frame_count / total_time if total_time > 0 else 0
    print(f"\n[デモ] 完了 — 受信フレーム数: {frame_count}  平均 FPS: {avg_fps:.1f}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Freenove 4WD Smart Car カメラ取得デモ"
    )
    parser.add_argument(
        "--host", "-H",
        required=True,
        help="Raspberry Pi の IP アドレス (例: 192.168.1.100)",
    )
    parser.add_argument(
        "--duration", "-d",
        type=float,
        default=None,
        help="自動終了までの秒数（省略で無制限）",
    )
    parser.add_argument(
        "--save-dir", "-o",
        default=None,
        help="フレームを連番 JPEG で保存するディレクトリ",
    )
    parser.add_argument(
        "--no-display",
        action="store_true",
        help="OpenCV ウィンドウを表示しない（ヘッドレス実行）",
    )
    args = parser.parse_args()

    try:
        run_demo(args.host, args.duration, args.save_dir, args.no_display)
    except KeyboardInterrupt:
        print("\n[デモ] Ctrl+C で中断しました")
        cv2.destroyAllWindows()
        sys.exit(0)
    except ConnectionRefusedError:
        print(f"\n[エラー] {args.host}:8000 に接続できませんでした。")
        print("  - Raspberry Pi のサーバー (Code/Server/main.py) が起動しているか確認してください。")
        sys.exit(1)
    except TimeoutError:
        print(f"\n[エラー] {args.host} への接続がタイムアウトしました。IP アドレスを確認してください。")
        sys.exit(1)


if __name__ == "__main__":
    main()
