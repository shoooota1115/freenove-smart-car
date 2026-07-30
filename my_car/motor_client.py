"""
motor_client.py
---------------
Freenove 4WD Smart Car へ TCP 経由でモーター制御コマンドを送るクライアント。

コマンドフォーマット（サーバー側 message.py / main.py より）:
    CMD_MOTOR#<duty1>#<duty2>#<duty3>#<duty4>\n
        duty: -4095 ～ 4095
        左前(duty1), 左後(duty2), 右前(duty3), 右後(duty4)
        正値=前進方向, 負値=後退方向

注意: サーバー（Code/Server/main.py）は max_clients=1 のため、
      このクライアントが唯一の接続となる。超音波センサーの読み取りも
      このソケット経由で行う（LEDClient とソケットを共有しない）。
"""

import socket
import time
import threading

CMD_MOTOR = "CMD_MOTOR"
CMD_SONIC = "CMD_SONIC"
INTERVAL  = "#"
END       = "\n"

# 速度定数
SPEED_FULL = 2000
SPEED_SLOW = 1000


class MotorClient:
    """サーバーの TCP コマンドポート (5000) に接続してモーターと超音波センサーを制御する。

    サーバーは max_clients=1 のため、このクライアントが唯一の接続となる。
    超音波センサーの読み取りも同一ソケット経由で行う。
    """

    def __init__(self, host: str, port: int = 5000, timeout: float = 5.0):
        self.host = host
        self.port = port
        self.timeout = timeout
        self._sock: socket.socket | None = None
        self._recv_lock = threading.Lock()

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
    # 超音波センサー読み取り
    # ------------------------------------------------------------------

    def read_ultrasonic_cm(self) -> float | None:
        """超音波センサーの距離 (cm) を取得する。

        サーバーへ CMD_SONIC を送り、返ってくる CMD_MODE#3#<値> を受け取る。
        サーバー側に 0.5 秒のレートリミットがあるため、0.55 秒待ってから recv する。
        排他ロックで並行呼び出しを防ぐ。
        """
        with self._recv_lock:
            try:
                self._sock.sendall(f"{CMD_SONIC}{END}".encode("utf-8"))
                time.sleep(0.55)
                self._sock.settimeout(1.0)
                data = self._sock.recv(128).decode("utf-8", errors="ignore")
                self._sock.settimeout(self.timeout)
                for line in data.strip().splitlines():
                    parts = line.split("#")
                    if len(parts) >= 3 and parts[0] == "CMD_MODE" and parts[1] == "3":
                        return round(float(parts[2]), 1)
            except Exception as e:
                print(f"[MotorClient] ultrasonic read failed: {type(e).__name__}: {e}")
        return None

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
