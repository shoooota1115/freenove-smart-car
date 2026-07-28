"""
consumers/alert_consumer.py
-----------------------------
freenove.alerts トピックを購読し、受信したアラートを
コールバック関数へ渡す Consumer。

バリデーション: vehicle_id が一致しないアラートは無視する。
スレッドベースのバックグラウンド実行。CommandConsumer と同じパターン。

使い方:
    from consumers.alert_consumer import AlertConsumer

    def handle_alert(alert: dict) -> None:
        alert_type = alert["alert_type"]
        if alert_type == "obstacle":
            led.blink(255, 200, 0)   # 黄色点滅
        elif alert_type == "low_battery":
            led.set_all(255, 0, 0)   # 赤点灯
        elif alert_type == "clear":
            led.set_all(0, 128, 0)   # 緑点灯

    consumer = AlertConsumer(on_alert=handle_alert)
    consumer.start()   # バックグラウンドスレッドで起動

    # メインループの終了時
    consumer.stop()

アラート JSON フォーマット（build_alert_event で生成されたもの）:
    {
        "schema_version": 1,
        "event_id": "...",
        "alert_id": "...",
        "vehicle_id": "freenove-car-01",
        "timestamp": "2026-...",
        "alert_type": "obstacle" | "low_battery" | "clear",
        "sensor_value": 12.5,
        "threshold": 20.0,
        "message": "Obstacle detected at 12.5 cm"
    }
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from typing import Callable

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from confluent_kafka import KafkaException, KafkaError

from kafka.client import create_consumer
from kafka.schemas import TOPIC_ALERTS


# アラートを受け取るコールバックの型
OnAlertCallback = Callable[[dict], None]


class AlertConsumer:
    """freenove.alerts を購読してコールバックへアラートを渡す。

    Parameters
    ----------
    on_alert : Callable[[dict], None]
        バリデーション済みアラート dict を受け取るコールバック
    vehicle_id : str
        環境変数 VEHICLE_ID の値（省略時 freenove-car-01）。
        自分宛て以外のアラートはフィルタリングして無視する。
    poll_timeout : float
        poll() のタイムアウト秒数
    """

    def __init__(
        self,
        on_alert: OnAlertCallback,
        vehicle_id: str | None = None,
        poll_timeout: float = 1.0,
    ) -> None:
        self.on_alert = on_alert
        self.vehicle_id = vehicle_id or os.getenv(
            "VEHICLE_ID", "freenove-car-01"
        )
        self.poll_timeout = poll_timeout

        group_id = f"freenove-alert-{self.vehicle_id}"
        self._consumer = create_consumer(
            group_id=group_id,
            topics=[TOPIC_ALERTS],
        )
        self._running = False
        self._thread: threading.Thread | None = None

        print(
            f"[AlertConsumer] Initialized"
            f" group={group_id}"
            f" topic={TOPIC_ALERTS}"
            f" vehicle={self.vehicle_id}"
        )

    # ------------------------------------------------------------------
    # ライフサイクル
    # ------------------------------------------------------------------

    def start(self) -> None:
        """バックグラウンドスレッドで Consumer ループを開始する。"""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._run,
            name="kafka-alert-consumer",
            daemon=True,
        )
        self._thread.start()
        print("[AlertConsumer] Started")

    def stop(self, timeout: float = 5.0) -> None:
        """Consumer ループを停止してスレッドを join する。"""
        self._running = False
        if self._thread:
            self._thread.join(timeout=timeout)
            self._thread = None
        try:
            self._consumer.close()
        except Exception:
            pass
        print("[AlertConsumer] Stopped")

    # ------------------------------------------------------------------
    # 内部ループ
    # ------------------------------------------------------------------

    def _run(self) -> None:
        while self._running:
            try:
                msg = self._consumer.poll(self.poll_timeout)
            except KafkaException as e:
                print(f"[AlertConsumer] KafkaException: {e}")
                time.sleep(2)
                continue

            if msg is None:
                continue

            if msg.error():
                if msg.error().code() == KafkaError._PARTITION_EOF:
                    continue
                print(f"[AlertConsumer] Error: {msg.error()}")
                continue

            self._handle_message(msg.value())

    def _handle_message(self, raw: bytes | None) -> None:
        """1 メッセージをパース → vehicle_id フィルタ → コールバック呼び出し。"""
        if raw is None:
            return

        # JSON パース
        try:
            alert: dict = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            print(f"[AlertConsumer] JSON parse error: {e}")
            return

        # vehicle_id フィルタ（自分宛てのみ処理）
        if alert.get("vehicle_id") != self.vehicle_id:
            return

        alert_type = alert.get("alert_type", "unknown")
        sensor_val = alert.get("sensor_value")
        print(
            f"[AlertConsumer] Received alert_type={alert_type!r}"
            f" sensor_value={sensor_val}"
        )

        # コールバック呼び出し（例外を Consumer ループへ漏らさない）
        try:
            self.on_alert(alert)
        except Exception as e:
            print(f"[AlertConsumer] on_alert raised: {e}")
