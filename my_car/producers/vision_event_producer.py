"""
producers/vision_event_producer.py
-----------------------------------
物体検出結果を freenove.vision-events へ送信する Producer。

全フレームは送らず、次のいずれかの条件で送信する:
  - objects が 1 件以上ある（検出あり）
  - force=True を指定した（定期送信）

使い方:
    producer = VisionEventProducer()

    # frame_id が 5 の倍数か、物体検出があるときだけ送る
    if frame_id % 5 == 0 or detections:
        producer.publish(
            frame_id=frame_id,
            detections=detections,
            inference_ms=elapsed_ms,
        )

    producer.close()
"""

from __future__ import annotations

import os
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from detector import Detection
from kafka.client import create_producer, send_json
from kafka.schemas import TOPIC_VISION_EVENTS, build_vision_event


def _detection_to_dict(d: Detection) -> dict:
    x, y, w, h = d.box
    return {
        "class": d.label,
        "confidence": round(d.conf, 4),
        "bbox": {"x": x, "y": y, "width": w, "height": h},
    }


class VisionEventProducer:
    """検出結果を freenove.vision-events へ送信する。

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

    # ------------------------------------------------------------------
    # 公開 API
    # ------------------------------------------------------------------

    def publish(
        self,
        frame_id: int,
        detections: list[Detection],
        inference_ms: float,
    ) -> None:
        """検出結果 1 フレーム分を Kafka へ送信する。

        Parameters
        ----------
        frame_id : int
            フレーム通し番号
        detections : list[Detection]
            detector.detect() の返り値
        inference_ms : float
            推論にかかった時間（ミリ秒）
        """
        objects = [_detection_to_dict(d) for d in detections]
        event = build_vision_event(
            vehicle_id=self.vehicle_id,
            frame_id=frame_id,
            objects=objects,
            inference_ms=inference_ms,
        )
        send_json(
            producer=self._producer,
            topic=TOPIC_VISION_EVENTS,
            key=self.vehicle_id,
            event=event,
        )

    def close(self, timeout: float = 10.0) -> None:
        """未送信メッセージをフラッシュして Producer を閉じる。"""
        remaining = self._producer.flush(timeout)
        if remaining:
            print(
                f"[VisionEventProducer] {remaining} message(s) undelivered"
            )
