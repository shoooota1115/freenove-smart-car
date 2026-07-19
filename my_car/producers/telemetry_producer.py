"""
producers/telemetry_producer.py
---------------------------------
定期テレメトリ（CPU 温度・バッテリー電圧・超音波距離・Wi-Fi RSSI）を
freenove.telemetry へ送信する Producer。

実際のセンサー値は呼び出し側から渡す（ハードウェア依存を切り離すため）。
センサーが存在しない環境では None を渡せばその項目は省略される。

使い方:
    producer = TelemetryProducer()

    # 1 秒ごとに送信する例
    while True:
        producer.publish(
            battery_voltage=read_battery(),
            ultrasonic_cm=read_ultrasonic(),
            cpu_temp_c=read_cpu_temp(),
        )
        time.sleep(1)

    producer.close()
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from kafka.client import create_producer, send_json
from kafka.schemas import TOPIC_TELEMETRY, build_telemetry_event


class TelemetryProducer:
    """定期テレメトリを freenove.telemetry へ送信する。

    Parameters
    ----------
    vehicle_id : str
        環境変数 VEHICLE_ID の値（省略時 freenove-car-01）
    """

    def __init__(self, vehicle_id: str | None = None) -> None:
        self.vehicle_id = vehicle_id or os.getenv(
            "VEHICLE_ID", "freenove-car-01"
        )
        self._producer = create_producer()
        self._sequence = 0

    # ------------------------------------------------------------------
    # 公開 API
    # ------------------------------------------------------------------

    def publish(
        self,
        *,
        battery_voltage: float | None = None,
        ultrasonic_cm: float | None = None,
        cpu_temp_c: float | None = None,
        wifi_rssi_dbm: int | None = None,
    ) -> None:
        """テレメトリ 1 件を Kafka へ送信する。

        引数はすべてキーワード専用。None の項目はペイロードから省略される。
        """
        self._sequence += 1
        event = build_telemetry_event(
            vehicle_id=self.vehicle_id,
            sequence=self._sequence,
            battery_voltage=battery_voltage,
            ultrasonic_cm=ultrasonic_cm,
            cpu_temp_c=cpu_temp_c,
            wifi_rssi_dbm=wifi_rssi_dbm,
        )
        send_json(
            producer=self._producer,
            topic=TOPIC_TELEMETRY,
            key=self.vehicle_id,
            event=event,
        )

    def close(self, timeout: float = 10.0) -> None:
        """未送信メッセージをフラッシュして Producer を閉じる。"""
        remaining = self._producer.flush(timeout)
        if remaining:
            print(
                f"[TelemetryProducer] {remaining} message(s) undelivered"
            )
