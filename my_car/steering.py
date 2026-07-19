"""
steering.py
-----------
検出結果（list[Detection]）を受け取り、追跡対象の画面内位置に基づいて
モーターの duty 値を計算するコントローラー。

制御ロジック:
  1. ターゲットの bounding box 中心 X を計算
  2. フレーム幅に対する相対位置（-1.0 〜 +1.0）を求める
  3. P ゲインで左右差 (steer) を算出
  4. ターゲットの面積比で前後速度 (throttle) を決定
     → ターゲットが近すぎれば後退 / 遠すぎれば前進

返り値は (fl, bl, fr, br) の duty タプル。符号と意味は MotorClient に準拠:
  正値 = 前進方向, 負値 = 後退方向

使い方:
    from steering import SteeringController
    ctrl = SteeringController()
    fl, bl, fr, br = ctrl.compute(detections, frame_width=640, frame_height=480)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

from detector import Detection


@dataclass
class SteeringConfig:
    """SteeringController の設定パラメータ。"""

    # --- ターゲット ---
    target_label: str = "person"   # 追跡対象のクラス名（複数指定は filter_classes で）
    # ターゲット検出がない場合の待機時間(秒)後に停止
    idle_timeout: float = 2.0

    # --- 速度 ---
    base_speed: int = 1200          # 直進時のベース duty (0–4095)
    steer_gain: float = 1.2         # 横偏差への P ゲイン (duty/normalized_error)

    # --- 面積制御（近すぎ/遠すぎ判定） ---
    # ターゲット bbox の面積 ÷ 全フレーム面積
    area_target_min: float = 0.06   # これより小さければ前進
    area_target_max: float = 0.20   # これより大きければ後退

    # --- デッドゾーン（横方向、normalized 値）---
    deadzone: float = 0.08          # |error| < deadzone なら直進扱い

    # --- duty クリップ ---
    max_duty: int = 2000


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _best_detection(
    detections: Sequence[Detection], label: str
) -> Detection | None:
    """指定ラベルのうち最も信頼度が高い検出を返す。"""
    candidates = [d for d in detections if d.label == label]
    if not candidates:
        return None
    return max(candidates, key=lambda d: d.conf)


class SteeringController:
    """
    検出結果 → モーター duty を計算するシンプルな比例制御コントローラー。

    Parameters
    ----------
    cfg : SteeringConfig
        制御パラメータ。省略時はデフォルト値を使用。
    """

    def __init__(self, cfg: SteeringConfig | None = None) -> None:
        self.cfg = cfg or SteeringConfig()

    def compute(
        self,
        detections: Sequence[Detection],
        frame_width: int,
        frame_height: int,
    ) -> tuple[int, int, int, int]:
        """
        検出リストからモーター duty を計算する。

        Returns
        -------
        (fl, bl, fr, br) : tuple[int, int, int, int]
            各ホイールの duty 値。0 のとき停止。
        """
        cfg = self.cfg
        target = _best_detection(detections, cfg.target_label)

        if target is None:
            return (0, 0, 0, 0)

        x, y, w, h = target.box
        frame_area = frame_width * frame_height

        # --- 横方向誤差（-1.0: 最左端, 0: 中央, +1.0: 最右端） ---
        cx = x + w / 2
        error = (cx - frame_width / 2) / (frame_width / 2)   # -1.0 〜 +1.0

        if abs(error) < cfg.deadzone:
            error = 0.0

        # --- 前後速度（面積フィードバック） ---
        area_ratio = (w * h) / frame_area
        if area_ratio < cfg.area_target_min:
            throttle = cfg.base_speed          # 前進
        elif area_ratio > cfg.area_target_max:
            throttle = -cfg.base_speed         # 後退
        else:
            throttle = 0                       # 距離 OK → 旋回のみ

        # --- 旋回成分 ---
        steer = int(error * cfg.base_speed * cfg.steer_gain)

        # --- 4輪 duty 計算 ---
        # 右回りは左輪を速く、左回りは右輪を速くする
        fl = int(_clamp(throttle - steer, -cfg.max_duty, cfg.max_duty))
        bl = fl
        fr = int(_clamp(throttle + steer, -cfg.max_duty, cfg.max_duty))
        br = fr

        return (fl, bl, fr, br)

    def describe(self, duty: tuple[int, int, int, int]) -> str:
        """duty タプルを人間が読みやすい文字列に変換する（デバッグ用）。"""
        fl, bl, fr, br = duty
        if fl == 0 and fr == 0:
            return "STOP"
        if fl > 0 and fr > 0:
            direction = "FWD"
        elif fl < 0 and fr < 0:
            direction = "BWD"
        else:
            direction = "TURN"
        return f"{direction} fl={fl:+5d} fr={fr:+5d}"
