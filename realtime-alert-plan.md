# リアルタイム異常検知デモ — 実装プラン

## 概要

Freenove Smart Car（ラズパイ）を **テレメトリ送信エンドポイント** として、
PC を **異常検知エンジン** として機能させることで、
Kafka を中継した IoT ⇄ クラウド間のリアルタイム異常検知アーキテクチャをデモする。

### アーキテクチャ概要

```
[ラズパイ + Freenove]                [IBM Event Streams / Kafka]       [PC / サーバー]
  超音波 + 電池電圧
       ↓ 2秒ごと
  TelemetryProducer  ────→  freenove.telemetry  ────→  AlertDetector
  AlertConsumer      ←────  freenove.alerts     ←────  AlertProducer
       ↓
  LEDClient (TCP)
       ↓
  Freenove LED
    🟡 障害物接近（<20cm）→ 黄色点滅
    🔴 電池低下（<7.0V） → 全LED赤点灯
    🟢 正常              → 全LED緑点灯
```

### スコープ

- **対象ブランチ:** `feature/ibm-kafka`
- **新規ファイル:** 5ファイル（`led_client.py`, `alert_producer.py`, `alert_consumer.py`, `demo_alert_car.py`, `demo_alert_detector.py`）
- **変更ファイル:** 1ファイル（`kafka/schemas.py` — alertスキーマ追加）
- **非対象:** 既存デモの変更、MotorClient の変更、Visionイベント統合

---

## サブタスク一覧

---

### サブタスク 1: LEDClient の新規作成

- **Status:** [x] done

#### Intent
`MotorClient` と同じパターンで、Freenove の TCPサーバー（ポート5000）に
`CMD_LED#<index>#<R>#<G>#<B>` コマンドを送る `LEDClient` を実装する。

これにより、ラズパイ側の Python スクリプトから Freenove の 8 個の RGB LED を制御できる。

#### Expected Outcomes
- `my_car/led_client.py` が存在する
- `LEDClient(host, port=5000)` がインスタンス化できる
- `set_color(index, r, g, b)` で指定インデックスの LED を制御できる
- `set_all(r, g, b)` で全 LED を同色に設定できる
- `blink(r, g, b, count, interval)` で点滅制御できる
- `off()` で全LED消灯できる
- MotorClient 同様にコンテキストマネージャー対応

#### Todo List
1. `my_car/led_client.py` を新規作成
2. コマンドフォーマット `CMD_LED#<index>#<R>#<G>#<B>\n` を確認して実装
   - index は 1ビットずつのビットマスク（`ledIndex` の仕様と合わせる）
   - 全LED同時 = index `0xFF` (255)
3. `set_color(index, r, g, b)` メソッド実装
4. `set_all(r, g, b)` — index=255で全LED
5. `blink(r, g, b, count=3, interval=0.3)` — set_all → off を繰り返す同期点滅
6. `off()` — set_all(0, 0, 0)
7. `connect()`, `close()`, `__enter__`/`__exit__` を実装

#### Relevant Context
- `my_car/motor_client.py` — 実装パターンの参照元
- `Code/Server/led.py` — `ledIndex(index, R, G, B)` のビットマスク仕様
- `Code/Server/command.py` — `CMD_LED` 定数の確認（コマンド名文字列）

---

### サブタスク 2: alertスキーマ追加 と AlertProducer 新規作成

- **Status:** [x] done

#### Intent
`kafka/schemas.py` に `freenove.alerts` トピック用のスキーマビルダー関数を追加し、
`producers/alert_producer.py` を実装する。

このプロデューサーは PC 側の AlertDetector から呼び出し、
異常種別・センサー値・閾値をアラートメッセージとして Kafka に発行する。

#### Expected Outcomes
- `kafka/schemas.py` に `build_alert_event()` 関数が追加されている
- `producers/alert_producer.py` が存在する
- `AlertProducer.publish(alert_type, sensor_value, threshold, vehicle_id)` が動作する
- 既存スキーマ関数（`build_motor_event` 等）は変更されていない

#### Todo List
1. `kafka/schemas.py` に `build_alert_event()` を追加
   - フィールド: `schema_version`, `event_id`, `alert_id`, `vehicle_id`, `timestamp`,
     `alert_type`（`"obstacle"` | `"low_battery"` | `"clear"`）,
     `sensor_value`（float）, `threshold`（float）, `message`（str）
2. `producers/alert_producer.py` を新規作成
   - 既存 Producer クラスのパターン（`TelemetryProducer` 等）を踏襲
   - `publish(alert_type, sensor_value, threshold, message)` メソッド
   - 重複アラート抑制: 前回と同じ `alert_type` なら発行しない（`_last_type` 管理）

#### Relevant Context
- `my_car/kafka/schemas.py` — 既存スキーマビルダーのパターン
- `my_car/producers/telemetry_producer.py` — Producer 実装パターンの参照元
- `my_car/kafka/client.py` — `send_json()`, `create_producer()` の使い方

---

### サブタスク 3: AlertConsumer 新規作成

- **Status:** [x] done

#### Intent
`consumers/alert_consumer.py` を実装する。
`freenove.alerts` トピックを購読し、受信した `alert_type` に応じて
コールバックを呼び出す Consumer。ラズパイ側の `demo_alert_car.py` から使用する。

#### Expected Outcomes
- `consumers/alert_consumer.py` が存在する
- `AlertConsumer(on_alert=callback)` がインスタンス化できる
- `start()` でバックグラウンドスレッドが起動し `freenove.alerts` を購読する
- `stop()` でグレースフルシャットダウンする
- コールバック引数は alert dict（`alert_type`, `sensor_value` 等）

#### Todo List
1. `consumers/alert_consumer.py` を新規作成
2. `CommandConsumer` のスレッドベースパターンを踏襲
3. メッセージ受信後に `on_alert(alert_dict)` コールバックを呼び出す
4. `vehicle_id` フィルタリング: 自分宛て以外のアラートは無視
5. Consumer グループID: `"freenove-alert-{vehicle_id}"`

#### Relevant Context
- `my_car/consumers/command_consumer.py` — Consumer 実装パターンの参照元
- `my_car/kafka/client.py` — `create_consumer()` の使い方
- `my_car/kafka/schemas.py` — `TOPIC_ALERTS` 定数

---

### サブタスク 4: demo_alert_car.py の作成（ラズパイ側メインスクリプト）

- **Status:** [x] done

#### Intent
ラズパイ上で動かすメインデモスクリプト。以下を同時に行う：
1. 超音波センサーと電池電圧を 2 秒ごとに `TelemetryProducer` で Kafka へ送信
2. `AlertConsumer` でアラートを受信し、`LEDClient` で LED を制御

これが「IoT デバイス側」の実装となる。

#### Expected Outcomes
- `my_car/demo_alert_car.py` が存在する
- `--host <RasPi IP>` を必須引数として受け取る（MotorClient 等と同じ）
- `--interval` でテレメトリ送信間隔を変更可能（デフォルト: 2秒）
- `--dry-run` フラグ対応（Kafka 接続なしで動作確認）
- Ctrl+C で全リソースをクリーンアップして終了

#### Todo List
1. `my_car/demo_alert_car.py` を新規作成
2. `argparse` で引数定義: `--host`, `--interval`, `--dry-run`
3. テレメトリ収集ループ（メインスレッド）:
   - `TelemetryProducer` を初期化
   - ループ内で超音波・電池電圧を収集（`--dry-run` 時はダミー値）
   - `producer.publish()` で Kafka へ送信
   - `time.sleep(interval)` 待機
4. アラート受信:
   - `AlertConsumer(on_alert=_handle_alert)` を初期化して `start()`
5. `_handle_alert(alert)` コールバック実装:
   - `"obstacle"` → `led.blink(255, 200, 0, ...)` (黄色)
   - `"low_battery"` → `led.set_all(255, 0, 0)` (赤)
   - `"clear"` → `led.set_all(0, 128, 0)` (緑)
6. `signal.signal(SIGINT, ...)` でクリーンアップ処理

#### Relevant Context
- `my_car/demo_kafka.py` — デモスクリプトの構成パターン
- `my_car/producers/telemetry_producer.py` — TelemetryProducer の使い方
- `my_car/led_client.py` — LEDClient（サブタスク1で作成）
- `my_car/consumers/alert_consumer.py` — AlertConsumer（サブタスク3で作成）

---

### サブタスク 5: demo_alert_detector.py の作成（PC側検知スクリプト）

- **Status:** [x] done

#### Intent
PC 上で動かす異常検知スクリプト。`freenove.telemetry` を購読し、
センサー値が閾値を超えたら `AlertProducer` でアラートを Kafka に発行する。

これが「エッジクラウド側」の実装となり、IoT ↔ クラウド間の
リアルタイム異常検知アーキテクチャを形成する。

#### Expected Outcomes
- `my_car/demo_alert_detector.py` が存在する
- 実行すると `freenove.telemetry` を購読し続ける
- 閾値超え時に `freenove.alerts` へアラートを発行する
- 正常復帰時に `"clear"` アラートを発行する
- `--dry-run` フラグ対応（アラートを Kafka へ送らずターミナルに表示）
- 閾値を CLI 引数でカスタマイズ可能

#### Todo List
1. `my_car/demo_alert_detector.py` を新規作成
2. `argparse` で引数定義:
   - `--obstacle-threshold` (default: 20.0 cm)
   - `--battery-threshold` (default: 7.0 V)
   - `--dry-run`
3. `create_consumer(group_id="freenove-alert-detector", topics=[TOPIC_TELEMETRY])` で
   テレメトリを購読
4. 各メッセージについて判定ロジック:
   ```
   ultrasonic_cm < obstacle_threshold  → alert_type = "obstacle"
   battery_voltage < battery_threshold → alert_type = "low_battery"
   両方正常かつ前回がアラート状態    → alert_type = "clear"
   ```
5. `AlertProducer.publish()` でアラートを送信（dry-run 時はprint のみ）
6. ターミナルに受信テレメトリとアラート発行状況をリアルタイム表示

#### Relevant Context
- `my_car/kafka/client.py` — `create_consumer()` の使い方
- `my_car/producers/alert_producer.py` — AlertProducer（サブタスク2で作成）
- `my_car/kafka/schemas.py` — `TOPIC_TELEMETRY` 定数

---

## 実装順序の依存関係

```
サブタスク1 (LEDClient)          → サブタスク4が依存
サブタスク2 (AlertProducer)      → サブタスク5が依存
サブタスク3 (AlertConsumer)      → サブタスク4が依存
サブタスク4 (demo_alert_car)     → 1, 3 完了後に実装
サブタスク5 (demo_alert_detector)→ 2 完了後に実装
```

**推奨実装順:** 1 → 2 → 3 → 4 → 5（依存関係順）

---

## 動作確認手順（実装完了後）

### ステップ 1: ラズパイ側（Freenove 接続済み）
```bash
cd my_car
python demo_alert_car.py --host <FreenoveIP> --interval 2
```

### ステップ 2: PC側（別ターミナル）
```bash
cd my_car
python demo_alert_detector.py
```

### ステップ 3: 動作確認
- 超音波センサーに手を近づける（< 20cm）→ LED 黄色点滅
- 電池電圧が低い場合 → LED 赤点灯
- 手を離す → LED 緑点灯（正常復帰）

### Dry-Run（Kafka 不要）
```bash
python demo_alert_car.py --host <IP> --dry-run   # テレメトリをコンソールに出力
python demo_alert_detector.py --dry-run           # アラートをコンソールに出力
```
