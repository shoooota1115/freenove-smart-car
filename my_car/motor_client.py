"""
motor_client.py
---------------
Freenove 4WD Smart Car へ TCP 経由でモーター制御コマンドを送るクライアント。

コマンドフォーマット（サーバー側 message.py / main.py より）:
    CMD_MOTOR#<duty1>#<duty2>#<duty3>#<duty4>\n
        duty: -4095 ～ 4095
        左前(duty1), 左後(duty2), 右前(duty3), 右後(duty4)
        正値=前進方向, 負値=後退方向
"""

import socket
import time

CMD_MOTOR = "CMD_MOTOR"
INTERVAL  = "#"
END       = "\n"

# 速度定数
SPEED_FULL = 2000
SPEED_SLOW = 1000


class MotorClient:
    """サーバーの TCP コマンドポート (5000) に接続してモーターを制御する。"""

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
        print(f"[MotorClient] Connected to {self.host}:{self.port}")

    def close(self) -> None:
        if self._sock:
            self.stop()
            self._sock.close()
            self._sock = None
            print("[MotorClient] Disconnected")

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *_):
        self.close()

    # ------------------------------------------------------------------
    # コマンド送信
    # ------------------------------------------------------------------

    def set_motor(self, fl: int, bl: int, fr: int, br: int) -> None:
        """4 輪それぞれの duty を直接指定する。"""
        cmd = f"{CMD_MOTOR}{INTERVAL}{fl}{INTERVAL}{bl}{INTERVAL}{fr}{INTERVAL}{br}{END}"
        self._sock.sendall(cmd.encode("utf-8"))

    # ------------------------------------------------------------------
    # 高レベル操作
    # ------------------------------------------------------------------

    def forward(self, speed: int = SPEED_FULL) -> None:
        """前進"""
        self.set_motor(speed, speed, speed, speed)

    def backward(self, speed: int = SPEED_FULL) -> None:
        """後退"""
        self.set_motor(-speed, -speed, -speed, -speed)

    def turn_left(self, speed: int = SPEED_FULL) -> None:
        """左旋回（左輪後退 / 右輪前進）"""
        self.set_motor(-speed, -speed, speed, speed)

    def turn_right(self, speed: int = SPEED_FULL) -> None:
        """右旋回（左輪前進 / 右輪後退）"""
        self.set_motor(speed, speed, -speed, -speed)

    def stop(self) -> None:
        """全輪停止"""
        self.set_motor(0, 0, 0, 0)
