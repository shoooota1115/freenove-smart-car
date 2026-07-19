"""
camera_client.py
----------------
Freenove 4WD Smart Car のビデオストリーム (TCP:8000) を受信するクライアント。

ストリームフォーマット（サーバー側 main.py / threading_video_send より）:
    [4 bytes LE uint32 = JPEG バイト長] + [JPEG bytes] を繰り返す

使い方（モジュールとして）:
    from camera_client import CameraClient

    with CameraClient("192.168.1.100") as cam:
        for frame in cam.frames():
            # frame は numpy ndarray (BGR, H x W x 3)
            ...
"""

import socket
import struct
import io

import numpy as np
import cv2
from PIL import Image


_HEADER_SIZE = 4  # struct '<I'


def _is_valid_jpeg(buf: bytes) -> bool:
    """JPEG バイト列の最低限の整合性チェック。"""
    if len(buf) < 2:
        return False
    # JFIF / Exif ヘッダーを持つ場合は末尾 FFD9 を確認
    if buf[6:10] in (b"JFIF", b"Exif"):
        return buf.rstrip(b"\x00\r\n").endswith(b"\xff\xd9")
    # それ以外は PIL で検証
    try:
        Image.open(io.BytesIO(buf)).verify()
        return True
    except Exception:
        return False


class CameraClient:
    """
    TCP ポート 8000 に接続してビデオストリームを受信するクライアント。

    Parameters
    ----------
    host : str
        Raspberry Pi の IP アドレス
    port : int
        ビデオポート（デフォルト 8000）
    timeout : float
        ソケットタイムアウト秒数
    """

    def __init__(self, host: str, port: int = 8000, timeout: float = 10.0):
        self.host = host
        self.port = port
        self.timeout = timeout
        self._sock: socket.socket | None = None
        self._conn: socket.socket | None = None  # makefile 用

    # ------------------------------------------------------------------
    # 接続管理
    # ------------------------------------------------------------------

    def connect(self) -> None:
        self._sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self._sock.settimeout(self.timeout)
        self._sock.connect((self.host, self.port))
        self._conn = self._sock.makefile("rb")
        print(f"[CameraClient] Connected to {self.host}:{self.port}")

    def close(self) -> None:
        if self._conn:
            try:
                self._conn.close()
            except Exception:
                pass
            self._conn = None
        if self._sock:
            try:
                self._sock.close()
            except Exception:
                pass
            self._sock = None
            print("[CameraClient] Disconnected")

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *_):
        self.close()

    # ------------------------------------------------------------------
    # フレーム受信
    # ------------------------------------------------------------------

    def read_raw_frame(self) -> bytes | None:
        """
        JPEG バイト列を 1 フレーム分読み取って返す。
        不正なデータは None を返す。接続が切れた場合は StopIteration を raise。
        """
        header = self._conn.read(_HEADER_SIZE)
        if len(header) < _HEADER_SIZE:
            raise StopIteration("ストリーム終了")
        (length,) = struct.unpack("<I", header)
        jpeg = self._conn.read(length)
        if len(jpeg) < length:
            raise StopIteration("ストリーム途中で切断")
        return jpeg if _is_valid_jpeg(jpeg) else None

    def frames(self):
        """
        有効なフレームを numpy ndarray (BGR) で yield するジェネレータ。
        接続が切れると StopIteration で終了。
        """
        while True:
            jpeg = self.read_raw_frame()
            if jpeg is None:
                continue  # 不正フレームはスキップ
            img = cv2.imdecode(np.frombuffer(jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
            if img is not None:
                yield img

    def frames_jpeg(self):
        """
        有効なフレームを生 JPEG bytes で yield するジェネレータ。
        OpenCV 不要の用途（Kafka 送信など）向け。
        """
        while True:
            jpeg = self.read_raw_frame()
            if jpeg is not None:
                yield jpeg
