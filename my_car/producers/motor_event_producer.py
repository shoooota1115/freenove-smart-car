"""
producers/motor_event_producer.py
----------------------------------
MotorClient の各操作に対応する Kafka イベントを送信する Producer。

使い方:
    producer = MotorEventProducer()
    producer.publish("forward", fl=1200, bl=1200, fr=1200, br=1200)
    producer.close()
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from kafka.client import create_producer, send_json
from kafka.schemas import TOPIC_MOTOR_EVENTS, build_motor_event


class MotorEventProducer:
    """モーター操作ごとに freenove.motor-events へイベントを送信する。

    Parameters
    ----------
    vehicle_id : str
        環境変数 VEHICLE_ID の値を使用（省略時 freenove-car-01）
    mode : str
        "manual" | "autonomous"。publish() 呼び出し前に変更可能。
    """

    def __init__(
        self,
        vehicle_id: str | None = None,
        mode: str = "manual",
    ) -> None:
        self.vehicle_id = vehicle_id or os.getenv(
            "VEHICLE_ID", "freenove-car-01"
        )
        self.mode = mode
        self._producer = create_producer()
        self._sequence = 0

    # ------------------------------------------------------------------
    # 公開 API
    # ------------------------------------------------------------------

    def publish(
        self,
        command: str,
        fl: int,
        bl: int,
        fr: int,
        br: int,
    ) -> None:
        """モーター操作 1 件を Kafka へ送信する。

        Parameters
        ----------
        command : str
            "forward" / "backward" / "turn_left" / "turn_right" /
            "stop" / "stop:<reason>" など
        fl, bl, fr, br : int
            各ホイールの duty 値
        """
        self._sequence += 1
        event = build_motor_event(
            vehicle_id=self.vehicle_id,
            sequence=self._sequence,
            command=command,
            fl=fl,
            bl=bl,
            fr=fr,
            br=br,
            mode=self.mode,
        )
        send_json(
            producer=self._producer,
            topic=TOPIC_MOTOR_EVENTS,
            key=self.vehicle_id,
            event=event,
        )

    def close(self, timeout: float = 10.0) -> None:
        """未送信メッセージをフラッシュして Producer を閉じる。"""
        remaining = self._producer.flush(timeout)
        if remaining:
            print(
                f"[MotorEventProducer] {remaining} message(s) undelivered"
            )
