"""
producers/alert_producer.py
----------------------------
アラートイベントを freenove.alerts へ送信する Producer。

PC 側の異常検知スクリプト（demo_alert_detector.py）から使用する。

重複アラート抑制機能: 前回と同じ alert_type のアラートは発行しない。
これにより、同一異常状態が継続していてもアラートが一度だけ送られる。
"clear" も同様に、連続して正常状態が続いても最初の 1 回のみ送信される。

使い方:
    producer = AlertProducer()

    # センサー値が閾値を超えた時
    producer.publish(
        alert_type="obstacle",
        sensor_value=12.5,
        threshold=20.0,
        message="Obstacle detected at 12.5 cm (threshold: 20.0 cm)",
    )

    producer.close()
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from kafka.client import create_producer, send_json
from kafka.schemas import TOPIC_ALERTS, build_alert_event


class AlertProducer:
    """アラートイベントを freenove.alerts へ送信する。

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
        self._last_type: str | None = None

    # ------------------------------------------------------------------
    # 公開 API
    # ------------------------------------------------------------------

    def publish(
        self,
        alert_type: str,
        sensor_value: float,
        threshold: float,
        message: str,
    ) -> bool:
        """アラート 1 件を Kafka へ送信する。

        前回と同じ alert_type の場合は送信をスキップする（重複抑制）。

        Returns
        -------
        bool
            送信した場合 True、重複でスキップした場合 False。
        """
        if alert_type == self._last_type:
            return False

        event = build_alert_event(
            vehicle_id=self.vehicle_id,
            alert_type=alert_type,
            sensor_value=sensor_value,
            threshold=threshold,
            message=message,
        )
        send_json(
            producer=self._producer,
            topic=TOPIC_ALERTS,
            key=self.vehicle_id,
            event=event,
        )
        self._last_type = alert_type
        return True

    def close(self, timeout: float = 10.0) -> None:
        """未送信メッセージをフラッシュして Producer を閉じる。"""
        remaining = self._producer.flush(timeout)
        if remaining:
            print(
                f"[AlertProducer] {remaining} message(s) undelivered"
            )
