# my_car — Freenove Smart Car デモ実装

段階的に機能を追加していくデモスクリプト集です。  
各ステップは独立しており、前のステップのコードを再利用しながら拡張していきます。

## 前提条件

| 項目 | 内容 |
|------|------|
| Raspberry Pi | Freenove 4WD Smart Car に搭載 |
| サーバー | `Code/Server/main.py` が起動済み |
| ネットワーク | PC と Raspberry Pi が同一 LAN 上にある |
| Python | 3.10 以上（PC 側） |

---

## ① モーター制御デモ

**ファイル:**
- [`motor_client.py`](motor_client.py) — TCP 接続・コマンド送信クライアント
- [`demo_motor.py`](demo_motor.py) — 走行シーケンスデモ

### コマンドプロトコル

サーバーはポート **5000** で TCP 接続を待ち受けます。  
コマンドは以下の文字列フォーマットで送信します：

```
CMD_MOTOR#<左前 duty>#<左後 duty>#<右前 duty>#<右後 duty>\n
```

- duty の範囲: `-4095` 〜 `4095`
- 正値 = 前進方向、負値 = 後退方向
- サーバー側で `scale_factor = 0.8` が乗算されます

### 走行パターン

| 操作 | 左前 | 左後 | 右前 | 右後 |
|------|------|------|------|------|
| 前進 | +speed | +speed | +speed | +speed |
| 後退 | -speed | -speed | -speed | -speed |
| 左旋回 | -speed | -speed | +speed | +speed |
| 右旋回 | +speed | +speed | -speed | -speed |
| 停止 | 0 | 0 | 0 | 0 |

### 実行方法

```bash
# 1. サーバー側 (Raspberry Pi)
cd Code/Server
python main.py

# 2. クライアント側 (PC)
cd my_car
python demo_motor.py --host 192.168.1.100

# 速度を調整する場合 (デフォルト: 2000)
python demo_motor.py --host 192.168.1.100 --speed 1500
```

### デモシーケンス

```
前進 (2秒) → 停止 → 後退 (2秒) → 停止 → 左旋回 (1.5秒) → 停止 → 右旋回 (1.5秒) → 停止
```

---

## ② カメラ取得デモ

**ファイル:**
- [`camera_client.py`](camera_client.py) — TCP:8000 ストリーム受信クライアント
- [`demo_camera.py`](demo_camera.py) — OpenCV ウィンドウライブ表示デモ

### ストリームプロトコル

サーバーはポート **8000** で TCP 接続を待ち受けます。
[`Code/Server/main.py`](../Code/Server/main.py) の `threading_video_send` が以下のフォーマットで送信します：

```
[4 bytes LE uint32 = JPEG バイト長] + [JPEG bytes]  ← これを繰り返す
```

### 実行方法

```bash
# 基本（OpenCV ウィンドウ表示）
python demo_camera.py --host 192.168.1.100

# 10 秒間録画して自動終了
python demo_camera.py --host 192.168.1.100 --duration 10

# フレームを ./frames/ に連番 JPEG で保存（ヘッドレス）
python demo_camera.py --host 192.168.1.100 --save-dir ./frames --no-display
```

### キー操作（表示モード）

| キー | 動作 |
|------|------|
| `s` | スナップショットを `snapshot_XXXX.jpg` で保存 |
| `q` / ESC | 終了 |

---

## ③ OpenCV 物体検出デモ

**ファイル:**
- [`detector.py`](detector.py) — 検出器クラス（HaarCascade / YOLOv4-tiny）
- [`demo_detection.py`](demo_detection.py) — カメラストリームへの検出枠重ね描き
- [`download_models.py`](download_models.py) — YOLOv4-tiny ONNX モデル取得スクリプト

### クラウドAI / LLM は必要か？

**不要です。** 使用する技術はすべてローカル処理です。

| バックエンド | 技術 | モデルサイズ | クラウド |
|---|---|---|---|
| `haar` | OpenCV 内蔵 Haar Cascade XML | 0 MB（内蔵済み） | 不要 |
| `yolo` | YOLOv4-tiny + `cv2.dnn` (ONNX) | ~23 MB | 不要 |

クラウドAIが必要になるのは ⑥ World Model で大規模な事前学習モデルを使う場合ですが、
それも ONNX エクスポートすれば Pi 上でローカル推論が可能です。

### 検出バックエンドの選び方

```
haar → 追加ダウンロード不要、高速、顔/体など特定形状の検出に強い
yolo → 80クラス汎用検出、日常物体（人・車・椅子・猫など）をまとめて検出
```

### 実行方法

```bash
# [方法A] Haar Cascade（モデル不要、即実行）
python demo_detection.py --host 192.168.1.100
python demo_detection.py --host 192.168.1.100 --backend haar --target body

# [方法B] YOLOv4-tiny（初回のみモデル取得が必要）
python download_models.py                    # ~23 MB ダウンロード
python demo_detection.py --host 192.168.1.100 --backend yolo

# 人・車だけ検出
python demo_detection.py --host 192.168.1.100 --backend yolo --filter person car

# 検出ログを CSV に保存
python demo_detection.py --host 192.168.1.100 --backend yolo --log detect.csv
```

### Haar Cascade ターゲット一覧

| `--target` | 検出対象 |
|---|---|
| `face`（デフォルト）| 正面顔 |
| `eye` | 目 |
| `body` | 全身 |
| `upperbody` | 上半身 |

### YOLOv4-tiny 主要検出クラス（80クラス COCO）

`person` `car` `bicycle` `cat` `dog` `chair` `bottle` `laptop` `cell phone` など

---

## ④ 自律走行

**ファイル:**
- [`steering.py`](steering.py) — 検出結果 → モーター duty を計算するコントローラー
- [`demo_autonomous.py`](demo_autonomous.py) — カメラ・検出・操舵・モーターを統合したメインループ

### 制御ロジック

```
CameraClient (TCP:8000)
    ↓ frame (numpy BGR)
Detector (haar / yolo)
    ↓ list[Detection]
SteeringController
    ↓ (fl, bl, fr, br) duty
MotorClient (TCP:5000)
```

| 条件 | 動作 |
|------|------|
| ターゲット未検出（2 秒以上） | 停止 |
| bbox 面積 < 6 % | 前進（ターゲットに近づく） |
| bbox 面積 > 20 % | 後退（距離を取る） |
| 面積 6–20 %（適正距離） | 旋回のみ（左右中央に寄せる） |
| 横偏差 < デッドゾーン | 直進扱い |

### 実行方法

```bash
# [方法A] Haar Cascade で人を追跡（モデル不要）
python demo_autonomous.py --host 192.168.1.100

# [方法B] YOLOv4-tiny で人を追跡（事前に download_models.py）
python demo_autonomous.py --host 192.168.1.100 --backend yolo

# 猫を追跡（YOLO）
python demo_autonomous.py --host 192.168.1.100 --backend yolo --target cat

# 速度を落とす（デフォルト: 1200）
python demo_autonomous.py --host 192.168.1.100 --speed 800

# 30 秒後に自動終了
python demo_autonomous.py --host 192.168.1.100 --duration 30

# ヘッドレス（GUI なし）
python demo_autonomous.py --host 192.168.1.100 --no-display
```

### キー操作（表示モード）

| キー | 動作 |
|------|------|
| `p` | 一時停止 / 再開（モーター停止） |
| `s` | スナップショットを `auto_snap_XXXX.jpg` で保存 |
| `q` / ESC | 終了 |

### チューニングパラメータ

| オプション | デフォルト | 説明 |
|---|---|---|
| `--speed` | 1200 | ベース duty（0–4095）。大きいほど速い |
| `--steer-gain` | 1.2 | 横方向 P ゲイン。大きいほど鋭く曲がる |
| `--area-min` | 0.06 | これ以下の面積比で前進 |
| `--area-max` | 0.20 | これ以上の面積比で後退 |
| `--conf` | 0.4 | YOLO 信頼度しきい値 |

---

## 今後の予定

| ステップ | 内容 |
|----------|------|
| ⑤ Kafka 化 | コマンド・センサーデータの Kafka 配信 |
| ⑥ World Model | 状態推定・経路計画の追加 |
