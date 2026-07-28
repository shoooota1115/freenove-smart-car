"""
kafka/schemas.py
----------------
Kafka メッセージのスキーマ定数とビルダー関数。

各 Producer からインポートして使う。
dict を直接組み立てると散らばるため、ここに集約する。
"""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone
from typing import Any


# ------------------------------------------------------------------
# トピック名
# ------------------------------------------------------------------

TOPIC_TELEMETRY = "freenove.telemetry"
TOPIC_MOTOR_EVENTS = "freenove.motor-events"
TOPIC_VISION_EVENTS = "freenove.vision-events"
TOPIC_COMMANDS = "freenove.commands"
TOPIC_ALERTS = "freenove.alerts"


# ------------------------------------------------------------------
# 共通ユーティリティ
# ------------------------------------------------------------------

def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _new_id() -> str:
    return str(uuid.uuid4())


# ------------------------------------------------------------------
# motor-events スキーマ
# ------------------------------------------------------------------

def build_motor_event(
    vehicle_id: str,
    sequence: int,
    command: str,
    fl: int,
    bl: int,
    fr: int,
    br: int,
    mode: str = "manual",
) -> dict[str, Any]:
    """freenove.motor-events へ送信するペイロードを組み立てる。

    Parameters
    ----------
    command : str
        "forward" / "backward" / "turn_left" / "turn_right" / "stop" など
    fl, bl, fr, br : int
        各ホイールの duty 値（-4095 ～ 4095）
    mode : str
        "manual" | "autonomous"
    """
    return {
        "schema_version": 1,
        "event_id": _new_id(),
        "vehicle_id": vehicle_id,
        "timestamp": _utc_now(),
        "monotonic_ns": time.monotonic_ns(),
        "sequence": sequence,
        "mode": mode,
        "motion": {
            "command": command,
            "fl_pwm": fl,
            "bl_pwm": bl,
            "fr_pwm": fr,
            "br_pwm": br,
        },
    }


# ------------------------------------------------------------------
# vision-events スキーマ
# ------------------------------------------------------------------

def build_vision_event(
    vehicle_id: str,
    frame_id: int,
    objects: list[dict[str, Any]],
    inference_ms: float,
) -> dict[str, Any]:
    """freenove.vision-events へ送信するペイロードを組み立てる。

    objects 要素の期待フォーマット:
        {
            "class": str,
            "confidence": float,
            "bbox": {"x": int, "y": int, "width": int, "height": int},
        }
    """
    return {
        "schema_version": 1,
        "event_id": _new_id(),
        "vehicle_id": vehicle_id,
        "timestamp": _utc_now(),
        "frame_id": frame_id,
        "objects": objects,
        "inference_ms": round(inference_ms, 2),
    }


# ------------------------------------------------------------------
# telemetry スキーマ
# ------------------------------------------------------------------

def build_telemetry_event(
    vehicle_id: str,
    sequence: int,
    *,
    battery_voltage: float | None = None,
    ultrasonic_cm: float | None = None,
    cpu_temp_c: float | None = None,
    wifi_rssi_dbm: int | None = None,
) -> dict[str, Any]:
    """freenove.telemetry へ送信するペイロードを組み立てる。

    None の項目は省略される。
    """
    sensors: dict[str, Any] = {}
    if battery_voltage is not None:
        sensors["battery_voltage"] = round(battery_voltage, 2)
    if ultrasonic_cm is not None:
        sensors["ultrasonic_cm"] = round(ultrasonic_cm, 1)
    if cpu_temp_c is not None:
        sensors["cpu_temp_c"] = round(cpu_temp_c, 1)
    if wifi_rssi_dbm is not None:
        sensors["wifi_rssi_dbm"] = wifi_rssi_dbm

    return {
        "schema_version": 1,
        "event_id": _new_id(),
        "vehicle_id": vehicle_id,
        "timestamp": _utc_now(),
        "sequence": sequence,
        "sensors": sensors,
    }


# ------------------------------------------------------------------
# alerts スキーマ
# ------------------------------------------------------------------

# alert_type の定数
ALERT_OBSTACLE   = "obstacle"
ALERT_LOW_BATTERY = "low_battery"
ALERT_CLEAR      = "clear"


def build_alert_event(
    vehicle_id: str,
    alert_type: str,
    sensor_value: float,
    threshold: float,
    message: str,
) -> dict[str, Any]:
    """freenove.alerts へ送信するペイロードを組み立てる。

    Parameters
    ----------
    alert_type : str
        "obstacle" | "low_battery" | "clear"
    sensor_value : float
        異常を検知したセンサーの実測値
    threshold : float
        判定に使用した閾値
    message : str
        人間可読なアラートメッセージ
    """
    return {
        "schema_version": 1,
        "event_id": _new_id(),
        "alert_id": _new_id(),
        "vehicle_id": vehicle_id,
        "timestamp": _utc_now(),
        "alert_type": alert_type,
        "sensor_value": round(sensor_value, 2),
        "threshold": round(threshold, 2),
        "message": message,
    }


# ------------------------------------------------------------------
# commands スキーマ（Consumer 側のバリデーション用）
# ------------------------------------------------------------------

def is_command_valid(cmd: dict[str, Any], vehicle_id: str) -> tuple[bool, str]:
    """受信コマンドの基本バリデーションを行う。

    Returns
    -------
    (ok, reason) : (bool, str)
        ok=False のとき reason に棄却理由が入る。
    """
    # vehicle_id チェック
    if cmd.get("vehicle_id") != vehicle_id:
        return False, f"vehicle_id mismatch: {cmd.get('vehicle_id')!r}"

    # 有効期限チェック
    expires_at = cmd.get("expires_at")
    if expires_at:
        try:
            exp = datetime.fromisoformat(expires_at)
            if exp < datetime.now(timezone.utc):
                return False, f"command expired at {expires_at}"
        except ValueError:
            return False, f"invalid expires_at: {expires_at!r}"

    # speed 上限チェック
    params = cmd.get("parameters", {})
    speed = params.get("speed")
    if speed is not None and speed > 60:
        return False, f"speed {speed} exceeds maximum 60"

    return True, ""
