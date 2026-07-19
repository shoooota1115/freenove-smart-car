"""
kafka/client.py
---------------
IBM Event Streams (Apache Kafka) への共通接続ファクトリ。

環境変数（.env または OS 環境変数）から接続情報を読み込む。
confluent-kafka を使用するため事前に:
    pip install confluent-kafka python-dotenv
"""

from __future__ import annotations

import json
import os
from typing import Any

from confluent_kafka import Producer, Consumer, KafkaException
from dotenv import load_dotenv

load_dotenv()


# ------------------------------------------------------------------
# 内部ヘルパー
# ------------------------------------------------------------------

def _base_config() -> dict[str, Any]:
    """SASL_SSL 接続に共通の設定辞書を返す。"""
    return {
        "bootstrap.servers": os.environ["KAFKA_BOOTSTRAP_SERVERS"],
        "security.protocol": os.getenv("KAFKA_SECURITY_PROTOCOL", "SASL_SSL"),
        "sasl.mechanism": os.getenv("KAFKA_SASL_MECHANISM", "PLAIN"),
        "sasl.username": os.environ["KAFKA_USERNAME"],
        "sasl.password": os.environ["KAFKA_PASSWORD"],
        # 接続切断を早めに検知
        "socket.timeout.ms": 10_000,
        "reconnect.backoff.ms": 1_000,
        "reconnect.backoff.max.ms": 10_000,
    }


# ------------------------------------------------------------------
# Producer
# ------------------------------------------------------------------

def _delivery_report(error, message) -> None:
    if error is not None:
        print(f"[Kafka] Delivery failed topic={message.topic()} err={error}")
    # 成功時は冗長になるため通常は出力しない


def create_producer() -> Producer:
    """IBM Event Streams 向け Producer を生成して返す。"""
    cfg = _base_config()
    cfg.update(
        {
            # データ損失を減らす
            "acks": "all",
            "enable.idempotence": True,
            "retries": 10,
            # 少量イベントをまとめて効率化
            "linger.ms": 20,
            "compression.type": "snappy",
            # Producer バッファ上限（メモリ保護）
            "queue.buffering.max.messages": 10_000,
        }
    )
    return Producer(cfg)


def send_json(
    producer: Producer,
    topic: str,
    key: str,
    event: dict[str, Any],
) -> None:
    """dict を JSON にシリアライズして topic へ送信する。

    flush はしない。呼び出し側が定期的に producer.poll(0) か
    producer.flush() を呼ぶこと。
    """
    payload = json.dumps(
        event,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    producer.produce(
        topic=topic,
        key=key.encode("utf-8"),
        value=payload,
        on_delivery=_delivery_report,
    )
    producer.poll(0)  # コールバック処理のみ（ブロックしない）


# ------------------------------------------------------------------
# Consumer
# ------------------------------------------------------------------

def create_consumer(group_id: str, topics: list[str]) -> Consumer:
    """IBM Event Streams 向け Consumer を生成して返す。

    Parameters
    ----------
    group_id : str
        コンシューマーグループ ID
    topics : list[str]
        サブスクライブするトピック一覧
    """
    cfg = _base_config()
    cfg.update(
        {
            "group.id": group_id,
            "auto.offset.reset": "latest",
            "enable.auto.commit": True,
            "auto.commit.interval.ms": 5_000,
            # セッション切断検知
            "session.timeout.ms": 30_000,
            "heartbeat.interval.ms": 10_000,
        }
    )
    consumer = Consumer(cfg)
    consumer.subscribe(topics)
    return consumer
