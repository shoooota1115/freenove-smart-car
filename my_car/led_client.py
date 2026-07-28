"""
led_client.py
-------------
Freenove 4WD Smart Car へ TCP 経由で LED 制御コマンドを送るクライアント。

コマンドフォーマット（サーバー側 message.py / main.py より）:
    CMD_LED_MOD#1\\n           ← 手動 LED モード（mode=1）に切り替え
    CMD_LED#<index>#<R>#<G>#<B>\\n
        index : ビットマスク（bit0=LED0 … bit7=LED7、0xFF=全8灯）
        R, G, B : 0 ～ 255

注意: CMD_LED は CMD_LED_MOD#1 を先に送って手動モードにしないと無視される。
"""

import socket
import time

CMD_LED     = "CMD_LED"
CMD_LED_MOD = "CMD_LED_MOD"
INTERVAL    = "#"
END         = "\n"

# 全LED同時制御用マスク
ALL_LEDS = 0xFF


class LEDClient:
    """サーバーの TCP コマンドポート (5000) に接続して LED を制御する。

    MotorClient と同じ接続パターンを採用。
    コンテキストマネージャー対応: ``with LEDClient(host) as led: ...``
    """

    def __init__(self, host: str, port: int = 5000, timeout: float = 5.0):
        self.host = host
        self.port = port
        self.timeout = timeout
        self._sock: socket.socket | None = None

    # ------------------------------------------------------------------
    # 接続管理
    # ------------------------------------------------------------------

    def connect(self) -> None:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.settimeout(self.timeout)
        self._sock.connect((self.host, self.port))
        # 手動 LED モードに切り替えて CMD_LED コマンドを有効化する
        self._send_raw(f"{CMD_LED_MOD}{INTERVAL}1{END}")
        print(f"[LEDClient] Connected to {self.host}:{self.port}")

    def close(self) -> None:
        if self._sock:
            self.off()
            self._sock.close()
            self._sock = None
            print("[LEDClient] Disconnected")

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *_):
        self.close()

    # ------------------------------------------------------------------
    # 内部ヘルパー
    # ------------------------------------------------------------------

    def _send_raw(self, cmd: str) -> None:
        self._sock.sendall(cmd.encode("utf-8"))

    # ------------------------------------------------------------------
    # LED 制御
    # ------------------------------------------------------------------

    def set_color(self, index: int, r: int, g: int, b: int) -> None:
        """指定インデックス（ビットマスク）の LED を設定する。

        Parameters
        ----------
        index : int
            ビットマスク。0x01=LED0, 0x02=LED1, … 0xFF=全8灯。
        r, g, b : int
            色成分（0–255）。
        """
        cmd = f"{CMD_LED}{INTERVAL}{index}{INTERVAL}{r}{INTERVAL}{g}{INTERVAL}{b}{END}"
        self._send_raw(cmd)

    def set_all(self, r: int, g: int, b: int) -> None:
        """全 8 LED を同じ色に設定する。"""
        self.set_color(ALL_LEDS, r, g, b)

    def off(self) -> None:
        """全 LED を消灯する。"""
        self.set_all(0, 0, 0)

    def blink(self, r: int, g: int, b: int, count: int = 3, interval: float = 0.3) -> None:
        """全 LED を指定色で count 回点滅させる（同期）。"""
        for _ in range(count):
            self.set_all(r, g, b)
            time.sleep(interval)
            self.off()
            time.sleep(interval)
