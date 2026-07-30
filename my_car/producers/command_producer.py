"""
producers/command_producer.py
------------------------------
自律制御コマンドを freenove.commands へ送信する Producer。

サーバーサイドの自律制御デモ（demo_autonomous_server.py）から使用する。

使い方:
    producer = CommandProducer()

    # 前進コマンド（継続）
    producer.publish(direction="forward", speed=20)

    # 左回転コマンド（650 ms = 約 90 度）
    producer.publish(direction="left", duration_ms=650)

    producer.close()
"""

from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from kafka.client import create_producer, send_json
from kafka.schemas import TOPIC_COMMANDS, build_command_event


class CommandProducer:
    """移動コマンドを freenove.commands へ送信する。

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
        direction: str,
        speed: int = 20,
        duration_ms: int = 0,
    ) -> None:
        """コマンド 1 件を Kafka へ送信する。

        Parameters
        ----------
        direction : str
            "forward" | "backward" | "left" | "right" | "stop"
        speed : int
            正規化速度（0–60）。省略時 20（低速前進用デフォルト）。
        duration_ms : int
            動作持続時間 ms。0 の場合は次の stop コマンドまで継続。
        """
        event = build_command_event(
            vehicle_id=self.vehicle_id,
            direction=direction,
            speed=speed,
            duration_ms=duration_ms,
        )
        send_json(
            producer=self._producer,
            topic=TOPIC_COMMANDS,
            key=self.vehicle_id,
            event=event,
        )

    def close(self, timeout: float = 10.0) -> None:
        """未送信メッセージをフラッシュして Producer を閉じる。"""
        remaining = self._producer.flush(timeout)
        if remaining:
            print(
                f"[CommandProducer] {remaining} message(s) undelivered"
            )
