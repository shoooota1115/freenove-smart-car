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

## 今後の予定

| ステップ | 内容 |
|----------|------|
| ③ OpenCV 物体検出 | YOLO / Haar Cascade による検出 |
| ④ 自律走行 | 検出結果に基づくモーター制御 |
| ⑤ Kafka 化 | コマンド・センサーデータの Kafka 配信 |
| ⑥ World Model | 状態推定・経路計画の追加 |
