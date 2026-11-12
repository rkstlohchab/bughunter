#!/usr/bin/env python3
# language: Python 3.11+, file: train_model.py
# YOLOv8 trainer for GameLab vision models. Local run OR Kaggle notebook.
#
# Dataset layout (YOLO format, one game per dataset):
#   dataset/
#     data.yaml
#     images/train/*.jpg
#     images/val/*.jpg
#     labels/train/*.txt
#     labels/val/*.txt
#
# data.yaml:
#   path: ./dataset
#   train: images/train
#   val: images/val
#   names: {0: enemy}            # one class is enough for pure ESP
#
# Export best.pt to weights/<game>_v8n.pt.

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Train a YOLOv8 game-detection model")
    ap.add_argument("--data", required=True, help="path to data.yaml")
    ap.add_argument("--model", default="yolov8n.pt", help="pretrained base (n/s/m)")
    ap.add_argument("--epochs", type=int, default=150)
    ap.add_argument("--imgsz", type=int, default=640)
    ap.add_argument("--batch", type=int, default=32)
    ap.add_argument("--device", default="0")
    ap.add_argument("--project", default="runs/detect")
    ap.add_argument("--name", default="gamelab")
    ap.add_argument("--out", default="weights/best.pt",
                    help="where to copy the final best.pt")
    ns = ap.parse_args(argv)

    from ultralytics import YOLO
    model = YOLO(ns.model)
    results = model.train(
        data=ns.data,
        epochs=ns.epochs,
        imgsz=ns.imgsz,
        batch=ns.batch,
        device=ns.device,
        project=ns.project,
        name=ns.name,
        augment=True,
        mosaic=1.0,
        mixup=0.1,
        hsv_h=0.015,
        hsv_s=0.7,
        hsv_v=0.4,
        patience=25,
        optimizer="AdamW",
        lr0=0.001,
    )
    best = Path(results.save_dir) / "weights" / "best.pt"
    if best.exists():
        out = Path(ns.out)
        out.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(best, out)
        print(f"[train] exported best.pt -> {out}")
    else:
        print(f"[train] best.pt not found at {best}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
