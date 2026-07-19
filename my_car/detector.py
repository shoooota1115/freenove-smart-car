"""
detector.py
-----------
OpenCV ベースの物体検出器。クラウド/LLM 不要、完全ローカル動作。

2 つのバックエンドを同一インターフェースで提供:
  - HaarCascade   : OpenCV 内蔵 XML。追加ダウンロード不要。顔・全身など。
  - YoloDetector  : cv2.dnn + YOLOv4-tiny (ONNX)。80クラス COCO 汎用検出。
                    初回実行時に download_models.py でモデルを取得する。

共通の返り値: list[Detection]
    Detection.label  : str   クラス名
    Detection.conf   : float 信頼度 0.0-1.0
    Detection.box    : (x, y, w, h) 整数ピクセル座標
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Protocol

import cv2
import numpy as np


# ------------------------------------------------------------------
# 共通データ型
# ------------------------------------------------------------------

@dataclass
class Detection:
    label: str
    conf: float
    box: tuple[int, int, int, int]  # x, y, w, h


# ------------------------------------------------------------------
# 描画ユーティリティ
# ------------------------------------------------------------------

# クラスごとに色を固定するためのパレット
_PALETTE: dict[str, tuple[int, int, int]] = {}
_COLORS = [
    (0, 255, 0), (0, 128, 255), (255, 0, 0), (0, 0, 255),
    (255, 128, 0), (128, 0, 255), (0, 255, 128), (255, 255, 0),
]


def _class_color(label: str) -> tuple[int, int, int]:
    if label not in _PALETTE:
        _PALETTE[label] = _COLORS[len(_PALETTE) % len(_COLORS)]
    return _PALETTE[label]


def draw_detections(
    frame: np.ndarray,
    detections: list[Detection],
    fps: float = 0.0,
) -> np.ndarray:
    """検出結果をフレームに重ね描きして返す（元フレームは変更しない）。"""
    out = frame.copy()
    for d in detections:
        x, y, w, h = d.box
        color = _class_color(d.label)
        cv2.rectangle(out, (x, y), (x + w, y + h), color, 2)
        text = f"{d.label} {d.conf:.0%}"
        (tw, th), baseline = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
        cv2.rectangle(out, (x, y - th - baseline - 4), (x + tw + 2, y), color, cv2.FILLED)
        cv2.putText(out, text, (x + 1, y - baseline - 2),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 0, 0), 1, cv2.LINE_AA)
    # FPS オーバーレイ
    if fps > 0:
        cv2.putText(out, f"FPS: {fps:.1f}  Objects: {len(detections)}",
                    (8, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.55,
                    (0, 255, 0), 1, cv2.LINE_AA)
    return out


# ------------------------------------------------------------------
# Detector プロトコル（型ヒント用）
# ------------------------------------------------------------------

class DetectorProtocol(Protocol):
    def detect(self, frame: np.ndarray) -> list[Detection]: ...


# ------------------------------------------------------------------
# 1. Haar Cascade 検出器
# ------------------------------------------------------------------

HAAR_PRESETS: dict[str, str] = {
    "face":      "haarcascade_frontalface_default.xml",
    "face_alt":  "haarcascade_frontalface_alt2.xml",
    "eye":       "haarcascade_eye.xml",
    "body":      "haarcascade_fullbody.xml",
    "upperbody": "haarcascade_upperbody.xml",
}


class HaarCascadeDetector:
    """
    OpenCV 内蔵 Haar Cascade 検出器。追加モデル不要。

    Parameters
    ----------
    target : str
        プリセット名 ("face", "eye", "body", "upperbody") または
        cascade XML のフルパス
    scale_factor : float
        detectMultiScale の scaleFactor
    min_neighbors : int
        detectMultiScale の minNeighbors
    min_size : tuple
        検出する最小サイズ (px)
    """

    def __init__(
        self,
        target: str = "face",
        scale_factor: float = 1.1,
        min_neighbors: int = 5,
        min_size: tuple[int, int] = (40, 40),
    ) -> None:
        self.label = target
        self.scale_factor = scale_factor
        self.min_neighbors = min_neighbors
        self.min_size = min_size

        # XML パス解決
        if os.path.isfile(target):
            xml_path = target
        elif target in HAAR_PRESETS:
            xml_path = os.path.join(cv2.data.haarcascades, HAAR_PRESETS[target])
        else:
            xml_path = os.path.join(cv2.data.haarcascades, target)

        self._cascade = cv2.CascadeClassifier(xml_path)
        if self._cascade.empty():
            raise FileNotFoundError(f"Haar Cascade XML が見つかりません: {xml_path}")
        print(f"[HaarCascade] Loaded: {os.path.basename(xml_path)}")

    def detect(self, frame: np.ndarray) -> list[Detection]:
        gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        rects = self._cascade.detectMultiScale(
            gray,
            scaleFactor=self.scale_factor,
            minNeighbors=self.min_neighbors,
            minSize=self.min_size,
        )
        results: list[Detection] = []
        if len(rects) > 0:
            for (x, y, w, h) in rects:
                results.append(Detection(label=self.label, conf=1.0, box=(x, y, w, h)))
        return results


# ------------------------------------------------------------------
# 2. YOLOv4-tiny 検出器（cv2.dnn + ONNX）
# ------------------------------------------------------------------

# COCO 80 クラスラベル
COCO_CLASSES = [
    "person", "bicycle", "car", "motorbike", "aeroplane", "bus", "train",
    "truck", "boat", "traffic light", "fire hydrant", "stop sign",
    "parking meter", "bench", "bird", "cat", "dog", "horse", "sheep",
    "cow", "elephant", "bear", "zebra", "giraffe", "backpack", "umbrella",
    "handbag", "tie", "suitcase", "frisbee", "skis", "snowboard",
    "sports ball", "kite", "baseball bat", "baseball glove", "skateboard",
    "surfboard", "tennis racket", "bottle", "wine glass", "cup", "fork",
    "knife", "spoon", "bowl", "banana", "apple", "sandwich", "orange",
    "broccoli", "carrot", "hot dog", "pizza", "donut", "cake", "chair",
    "sofa", "pottedplant", "bed", "diningtable", "toilet", "tvmonitor",
    "laptop", "mouse", "remote", "keyboard", "cell phone", "microwave",
    "oven", "toaster", "sink", "refrigerator", "book", "clock", "vase",
    "scissors", "teddy bear", "hair drier", "toothbrush",
]

_MODELS_DIR = os.path.join(os.path.dirname(__file__), "models")
_YOLO_ONNX   = os.path.join(_MODELS_DIR, "yolov4-tiny.onnx")


class YoloDetector:
    """
    YOLOv4-tiny (ONNX) + cv2.dnn による汎用物体検出。完全ローカル動作。

    初回実行前に download_models.py でモデルを取得してください:
        python download_models.py

    Parameters
    ----------
    model_path : str
        ONNX モデルのパス（省略時は models/yolov4-tiny.onnx）
    conf_threshold : float
        信頼度しきい値
    nms_threshold : float
        NMS IoU しきい値
    input_size : int
        ネットワーク入力サイズ（416 or 608）
    filter_classes : list[str] | None
        検出対象クラス名リスト。None で全クラス対象。
    """

    def __init__(
        self,
        model_path: str = _YOLO_ONNX,
        conf_threshold: float = 0.4,
        nms_threshold: float = 0.4,
        input_size: int = 416,
        filter_classes: list[str] | None = None,
    ) -> None:
        if not os.path.isfile(model_path):
            raise FileNotFoundError(
                f"ONNX モデルが見つかりません: {model_path}\n"
                "  → python my_car/download_models.py を実行してモデルを取得してください。"
            )
        self.net = cv2.dnn.readNetFromONNX(model_path)
        self.net.setPreferableBackend(cv2.dnn.DNN_BACKEND_OPENCV)
        self.net.setPreferableTarget(cv2.dnn.DNN_TARGET_CPU)
        self.conf_threshold = conf_threshold
        self.nms_threshold  = nms_threshold
        self.input_size     = input_size
        self.filter_ids: set[int] | None = None
        if filter_classes:
            self.filter_ids = {
                i for i, c in enumerate(COCO_CLASSES)
                if c in filter_classes
            }
        print(f"[YoloDetector] Loaded: {os.path.basename(model_path)}")

    def detect(self, frame: np.ndarray) -> list[Detection]:
        h, w = frame.shape[:2]
        blob = cv2.dnn.blobFromImage(
            frame, 1 / 255.0,
            (self.input_size, self.input_size),
            swapRB=True, crop=False,
        )
        self.net.setInput(blob)
        outputs = self.net.forward(self.net.getUnconnectedOutLayersNames())

        boxes, confidences, class_ids = [], [], []
        for out in outputs:
            for det in out:
                scores = det[5:]
                cid = int(np.argmax(scores))
                conf = float(scores[cid])
                if conf < self.conf_threshold:
                    continue
                if self.filter_ids and cid not in self.filter_ids:
                    continue
                cx, cy, bw, bh = det[:4]
                x = int((cx - bw / 2) * w)
                y = int((cy - bh / 2) * h)
                bw_px = int(bw * w)
                bh_px = int(bh * h)
                boxes.append([x, y, bw_px, bh_px])
                confidences.append(conf)
                class_ids.append(cid)

        indices = cv2.dnn.NMSBoxes(
            boxes, confidences, self.conf_threshold, self.nms_threshold
        )
        results: list[Detection] = []
        if len(indices) > 0:
            for i in indices.flatten():
                label = COCO_CLASSES[class_ids[i]] if class_ids[i] < len(COCO_CLASSES) else str(class_ids[i])
                results.append(Detection(
                    label=label,
                    conf=confidences[i],
                    box=tuple(boxes[i]),
                ))
        return results


# ------------------------------------------------------------------
# ファクトリ関数
# ------------------------------------------------------------------

def make_detector(backend: str = "haar", **kwargs) -> DetectorProtocol:
    """
    backend : "haar" | "yolo"
    """
    if backend == "haar":
        return HaarCascadeDetector(**kwargs)
    elif backend == "yolo":
        return YoloDetector(**kwargs)
    else:
        raise ValueError(f"Unknown backend: {backend!r}. Choose 'haar' or 'yolo'.")
