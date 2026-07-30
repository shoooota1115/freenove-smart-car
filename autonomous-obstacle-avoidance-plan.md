# 自律障害物回避デモ — 実装プラン

## 概要

超音波センサーの距離データを Kafka 経由でやり取りし、前方に障害物がある場合にサーバーサイドで回避方向を決定してコマンドを送信、車が自律的に左回転→停止→再判定というループで動作するデモを実装する。

**ブランチ名:** `feature/autonomous-obstacle-avoidance`（`feature/realtime-alert-demo` から分岐）

### アーキテクチャ方針：サーバーサイド型

```
[Car Side: demo_autonomous_car.py]
  超音波センサー読み取り (LEDClient.read_ultrasonic_cm)
  → TelemetryProducer → freenove.telemetry

  CommandConsumer ← freenove.commands
  → MotorClient（前進 / 左回転 / 停止）

[Server Side: demo_autonomous_server.py]
  freenove.telemetry → 障害物判定
  → CommandProducer → freenove.commands
```

**選定理由:**
- 回避ロジックをサーバー側に集約 → 将来の PhysicalAI 化（推論モデル差し替え）が容易
- `CommandConsumer` が既に実装済みで `direction: left/right` もスキーマ定義済み
- 車載側（Raspberry Pi）はセンサー読み取りとモーター実行のみに専念

### 回避動作仕様

| 条件 | サーバー判定 | 送信コマンド | 車の動作 |
|---|---|---|---|
| 超音波 ≥ 30 cm（正常） | 前進 | direction=forward, speed=20 | 低速前進継続（衝突余裕を確保） |
| 超音波 < 30 cm（障害物） | 左回転 | direction=left, duration_ms=650 | 左回転 650ms（≒90度） → 停止 → 次の値で再判定 |

**衝突防止パラメータ設計:**
- テレメトリ送信間隔: **0.5 秒**（2 秒から短縮）
- 前進速度: `speed=20`（PWM duty ≈ 1360、SPEED_FULL=2000 の約 70%）
- 障害物検知閾値: **30 cm**（0.5 秒間隔 × 低速前進で停止余裕あり）
- 左回転時間: **650 ms**（4WD スキッドステアで約 90 度）

---

## サブタスク一覧

| # | タイトル | ステータス |
|---|---|---|
| 1 | ブランチ作成 | `[x] done` |
| 2 | CommandProducer 実装 | `[x] done` |
| 3 | サーバーサイドデモ実装 | `[x] done` |
| 4 | 車載サイドデモ実装 | `[x] done` |
| 5 | README 更新 | `[x] done` |

---

## サブタスク 1：ブランチ作成

**Intent:** `feature/realtime-alert-demo` から新しいブランチを切り、作業を隔離する。

**Expected Outcomes:**
- `feature/autonomous-obstacle-avoidance` ブランチが存在する
- 作業ブランチとして checkout 済み

**Todo List:**
1. `git checkout -b feature/autonomous-obstacle-avoidance` を実行する

**Relevant Context:**
- 現在のブランチ: `feature/realtime-alert-demo`

**Status:** `[x] done`

---

## サブタスク 2：CommandProducer 実装

**Intent:** `freenove.commands` トピックへ移動コマンドを送信する Producer クラスを実装する。既存の AlertProducer / TelemetryProducer と同じパターンに従う。

**Expected Outcomes:**
- `my_car/producers/command_producer.py` が作成される
- `CommandProducer.publish(direction, speed, duration_ms)` でコマンドを送信できる
- `freenove.commands` トピックへの JSON メッセージが `schemas.py` の `is_command_valid()` をパスする形式

**Todo List:**
1. `my_car/producers/command_producer.py` を新規作成する
2. `CommandProducer.__init__` で `create_producer()` と `vehicle_id` を初期化する
3. `CommandProducer.publish(direction, speed, duration_ms)` を実装する
   - `schemas.py` に `build_command_event()` 関数を追加する
   - `TOPIC_COMMANDS` へ `send_json()` で送信する
4. `CommandProducer.close()` で `flush()` する

**Relevant Context:**
- 既存パターン: [`AlertProducer`](my_car/producers/alert_producer.py)
- スキーマ定義: [`schemas.py`](my_car/kafka/schemas.py) — `TOPIC_COMMANDS`, `is_command_valid()`
- コマンド JSON フォーマット（`CommandConsumer` の docstring より）:
  ```json
  {
    "schema_version": 1,
    "command_id": "cmd-...",
    "vehicle_id": "freenove-car-01",
    "timestamp": "...",
    "command": "move",
    "parameters": {
      "direction": "left",
      "speed": 30,
      "duration_ms": 800
    }
  }
  ```
- `command_id` は `"cmd-" + UUID4` 形式、`expires_at` は省略可

**Status:** `[ ] pending`

---

## サブタスク 3：サーバーサイドデモ実装

**Intent:** PC 上で実行する自律制御エンジンを実装する。テレメトリを受信し、障害物判定を行い、コマンドを送信する。

**Expected Outcomes:**
- `my_car/demo_autonomous_server.py` が作成される
- `--host` で車の IP、`--threshold` で障害物検知距離を設定できる
- `--dry-run` でモーター制御なし（ターミナルに表示のみ）で動作確認できる
- 障害物検知時: `direction=left, duration_ms=800` コマンドを送信
- 正常時: `direction=forward, speed=30` コマンドを送信
- 前回と同じコマンドは送信しない（重複抑制）— サーバー側で状態管理

**Todo List:**
1. `my_car/demo_autonomous_server.py` を新規作成する
2. `create_consumer(group_id="freenove-auto-server", topics=[TOPIC_TELEMETRY])` で telemetry を subscribe する
3. `CommandProducer` をインポートして初期化する
4. メインループ:
   - `consumer.poll(1.0)` でテレメトリ受信
   - `sensors.ultrasonic_cm` が `--threshold` 未満なら `obstacle`、以上なら `clear` と判定
   - 前回判定と異なる場合のみコマンドを送信する（重複抑制）
   - 障害物時: `direction=left, duration_ms=800, speed=40` を publish
   - 正常時: `direction=forward, speed=30` を publish
5. ANSI カラーでターミナル表示（既存 `demo_alert_detector.py` に合わせる）
6. `argparse` で `--threshold`（デフォルト 30.0）, `--dry-run` を実装する
7. `KeyboardInterrupt` でグレースフルシャットダウン

**Relevant Context:**
- 参照パターン: [`demo_alert_detector.py`](my_car/demo_alert_detector.py)
- 使用する Producer: サブタスク 2 で作成する `CommandProducer`
- スキーマ: [`schemas.py`](my_car/kafka/schemas.py) — `TOPIC_TELEMETRY`

**Status:** `[x] done`

---

## サブタスク 4：車載サイドデモ実装

**Intent:** Raspberry Pi 上（または PC から接続）で実行するデバイス側デモを実装する。超音波テレメトリを Kafka に送信しながら、コマンドを受信してモーターを制御する。

**Expected Outcomes:**
- `my_car/demo_autonomous_car.py` が作成される
- `--host` で Raspberry Pi の IP を指定できる
- テレメトリを `--interval` 秒ごとに送信する
- `CommandConsumer` でコマンドを受信し、`MotorClient` でモーターを制御する
- `direction=forward` → `motor.forward(speed)` で前進
- `direction=left` → `motor.turn_left()` → `duration_ms` ms 後に `motor.stop()`
- `--dry-run` で Kafka・モーター接続なし（ダミー超音波値＋ログ出力のみ）

**Todo List:**
1. `my_car/demo_autonomous_car.py` を新規作成する
2. `LEDClient` で超音波を読み取り、`TelemetryProducer` で publish するループを実装する（`demo_alert_car.py` の送信部分を流用）
3. `CommandConsumer` をインポートして、コールバック `_handle_command(cmd)` を実装する:
   - `direction = cmd["parameters"]["direction"]`
   - `speed = cmd["parameters"].get("speed", 30)`
   - `duration_ms = cmd["parameters"].get("duration_ms", 0)`
   - `forward` → `motor.forward(speed)` を呼ぶ
   - `left` → `motor.turn_left(speed)` → `time.sleep(duration_ms / 1000)` → `motor.stop()`
4. `signal.SIGINT / SIGTERM` でグレースフルシャットダウン（`motor.stop()` → `consumer.stop()` → `producer.close()`）
5. `argparse` で `--host`, `--interval`（デフォルト 1.0）, `--dry-run` を実装する

**Relevant Context:**
- 参照パターン: [`demo_alert_car.py`](my_car/demo_alert_car.py)
- 既存 Consumer: [`CommandConsumer`](my_car/consumers/command_consumer.py) — `on_command` コールバック
- モーター制御: [`MotorClient`](my_car/motor_client.py) — `forward()`, `turn_left()`, `stop()`
- センサー読み取り: [`LEDClient.read_ultrasonic_cm()`](my_car/led_client.py)

**Status:** `[x] done`

---

## サブタスク 5：README 更新

**Intent:** 新しいデモスクリプトの使い方を `my_car/README.md` に追記する。

**Expected Outcomes:**
- `demo_autonomous_car.py` と `demo_autonomous_server.py` の起動手順が README に記載される
- 必要なトピック一覧（`freenove.telemetry`, `freenove.commands`）が明記される
- 既存の README スタイルに合わせて記載される

**Todo List:**
1. `my_car/README.md` を読んで既存のスタイルを確認する
2. 「⑦ 自律障害物回避デモ」セクションを追記する:
   - データフロー説明
   - Terminal 1（Raspberry Pi）: `python Code/Server/main.py`
   - Terminal 2（Car側）: `python my_car/demo_autonomous_car.py --host <IP>`
   - Terminal 3（Server側）: `python my_car/demo_autonomous_server.py --host <IP>`

**Relevant Context:**
- 更新対象: [`my_car/README.md`](my_car/README.md)

**Status:** `[x] done`

---

## 実装時の注意点

1. **コマンドの重複抑制:** サーバー側で `_last_direction` を管理。`forward` が続く場合は再送しない。`left` は障害物検知のたびに毎回送信（ワンショット回避動作のため）
2. **テレメトリ間隔:** **0.5 秒**固定（`--interval` デフォルト値）。これにより前方 30cm 検知から停止まで最大 1 秒以内に収まる
3. **duration_ms の扱い:** `turn_left()` 後の `stop()` は車載側コールバック内で `time.sleep(duration_ms/1000)` を使う。コールバックはバックグラウンドスレッドで動くため、メインの送信ループとは干渉しない
4. **速度変換:** コマンドの `speed` 値（0–60 の正規化スケール）を PWM duty に変換: `duty = speed * 4095 // 60`（≈ speed × 68）。`speed=20` → duty ≈ 1365
5. **左回転 90 度の根拠:** 4WD スキッドステア（左輪逆転/右輪前転）での実測ベース。650 ms でほぼ 90 度となる想定。実機調整が必要な場合は `--turn-duration` オプションで上書き可能とする
