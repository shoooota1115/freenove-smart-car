"""
consumers/command_consumer.py
------------------------------
freenove.commands トピックを購読し、受信したコマンドを
コールバック関数へ渡す Consumer。

安全性チェック（有効期限・vehicle_id・speed 上限）はここで行い、
不正コマンドは棄却する。実際のモーター制御はコールバック側で行う。

使い方:
    from consumers.command_consumer import CommandConsumer

    def handle(cmd: dict) -> None:
        direction = cmd["parameters"]["direction"]
        speed     = cmd["parameters"]["speed"]
        motor.forward(speed)  # 実際の制御

    consumer = CommandConsumer(on_command=handle)
    consumer.start()   # バックグラウンドスレッドで起動

    # メインループの終了時
    consumer.stop()

コマンド JSON フォーマット（最低限必要なフィールド）:
    {
        "schema_version": 1,
        "command_id": "cmd-...",
        "vehicle_id": "freenove-car-01",
        "timestamp": "2026-...",
        "expires_at": "2026-...",      # 省略可（省略時は有効期限チェックなし）
        "command": "move" | "stop" | "mode",
        "parameters": {
            "direction": "forward" | "backward" | "left" | "right" | "stop",
            "speed": 0-60,             # move コマンド時
            "duration_ms": 1000        # 省略可
        }
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
from kafka.schemas import TOPIC_COMMANDS, is_command_valid


# コマンドを受け取るコールバックの型
OnCommandCallback = Callable[[dict], None]


class CommandConsumer:
    """freenove.commands を購読してコールバックへコマンドを渡す。

    Parameters
    ----------
    on_command : Callable[[dict], None]
        バリデーション済みコマンドを受け取るコールバック
    vehicle_id : str
        環境変数 VEHICLE_ID の値（省略時 freenove-car-01）
    group_id : str
        Kafka コンシューマーグループ ID
    poll_timeout : float
        poll() のタイムアウト秒数
    """

    def __init__(
        self,
        on_command: OnCommandCallback,
        vehicle_id: str | None = None,
        group_id: str = "freenove-car-consumer",
        poll_timeout: float = 1.0,
    ) -> None:
        self.on_command = on_command
        self.vehicle_id = vehicle_id or os.getenv(
            "VEHICLE_ID", "freenove-car-01"
        )
        self.group_id = group_id
        self.poll_timeout = poll_timeout

        self._consumer = create_consumer(
            group_id=group_id,
            topics=[TOPIC_COMMANDS],
        )
        self._running = False
        self._thread: threading.Thread | None = None

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
            name="kafka-command-consumer",
            daemon=True,
        )
        self._thread.start()
        print(
            f"[CommandConsumer] Started"
            f" group={self.group_id}"
            f" topic={TOPIC_COMMANDS}"
            f" vehicle={self.vehicle_id}"
        )

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
        print("[CommandConsumer] Stopped")

    # ------------------------------------------------------------------
    # 内部ループ
    # ------------------------------------------------------------------

    def _run(self) -> None:
        while self._running:
            try:
                msg = self._consumer.poll(self.poll_timeout)
            except KafkaException as e:
                print(f"[CommandConsumer] KafkaException: {e}")
                time.sleep(2)
                continue

            if msg is None:
                continue

            if msg.error():
                if msg.error().code() == KafkaError._PARTITION_EOF:
                    # 正常終端
                    continue
                print(f"[CommandConsumer] Error: {msg.error()}")
                continue

            self._handle_message(msg.value())

    def _handle_message(self, raw: bytes | None) -> None:
        """1 メッセージをパース → バリデーション → コールバック呼び出し。"""
        if raw is None:
            return

        # JSON パース
        try:
            cmd: dict = json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            print(f"[CommandConsumer] JSON parse error: {e}")
            return

        # バリデーション
        ok, reason = is_command_valid(cmd, self.vehicle_id)
        if not ok:
            print(f"[CommandConsumer] Command rejected: {reason}")
            return

        command = cmd.get("command", "")
        command_id = cmd.get("command_id", "—")
        print(f"[CommandConsumer] Accepted command={command!r} id={command_id}")

        # コールバック呼び出し（例外を Consumer ループへ漏らさない）
        try:
            self.on_command(cmd)
        except Exception as e:
            print(f"[CommandConsumer] on_command raised: {e}")
