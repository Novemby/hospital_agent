# backend/routers/sign_language.py
import logging
import traceback
import os
from typing import List, Optional

from fastapi import APIRouter
from pydantic import BaseModel

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/sign", tags=["sign_language"])

# 模型路径
_BASE      = os.path.normpath(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".."))
MODEL_PATH = os.path.join(_BASE, "robust_multistream_checkpoints",
                          "best_robust_multistream_graformer.pth")
LABEL_PATH = os.path.join(_BASE, "robust_multistream_checkpoints", "label_mapping.json")

_model_instance = None

def get_model():
    global _model_instance
    if _model_instance is None:
        logger.info(f"Loading model from {MODEL_PATH}")
        from backend.services.sign_model import SignLanguageModel
        _model_instance = SignLanguageModel(MODEL_PATH, LABEL_PATH)
    return _model_instance


# ──────────────────────────────────────────────────────────
#  请求/响应结构
# ──────────────────────────────────────────────────────────

class RecognizeRequest(BaseModel):
    # ★ 模式A（推荐）：前端发 base64 JPEG 帧
    #   字段名：frames_b64（推荐）或 frames（兼容 double_series 前端格式）
    #   两个字段都接受 List[str]，后端自动判断
    frames_b64: Optional[List[str]] = None

    # ★ 模式B（兼容）：前端发 Holistic 关键点数组
    #   字段名：frames，元素类型为 List[float]（每帧 1629 或 1659 个浮点数）
    frames: Optional[List[str]] = None   # 接受 str 或 float list，见下方路由逻辑

    confidence_threshold: float = 0.3
    trim_frames: int = 2


class TopPrediction(BaseModel):
    label:      str
    confidence: float
    rank:       int


class RecognizeResponse(BaseModel):
    success:         bool
    prediction:      str   = ""
    confidence:      float = 0.0
    top_predictions: List[TopPrediction] = []
    frame_count:     int   = 0
    mode:            str   = ""
    error:           str   = ""


# ──────────────────────────────────────────────────────────
#  识别接口
# ──────────────────────────────────────────────────────────

@router.post("/recognize", response_model=RecognizeResponse)
async def recognize(req: RecognizeRequest):
    try:
        model = get_model()

        # ── 确定帧数据和模式 ──────────────────────────────
        # 优先使用 frames_b64；
        # 若前端沿用 double_series 的 "frames" 字段发 base64，也能正确路由
        raw_frames = req.frames_b64 or req.frames or []

        if not raw_frames:
            return RecognizeResponse(success=False,
                                     error="请提供 frames_b64（base64 JPEG 列表）")

        if len(raw_frames) < 5:
            return RecognizeResponse(success=False,
                                     error=f"帧数太少（{len(raw_frames)}帧），至少需要 5 帧")

        # ── 判断内容类型：base64 字符串 vs 关键点数组 ──────
        # base64 字符串长度远大于单个浮点数字符串；
        # 最简单的判断：第一个元素是否为纯 str（base64）还是 list（关键点）
        first = raw_frames[0]
        if isinstance(first, str):
            # base64 JPEG → 服务端 MediaPipe 提取关键点
            logger.info(f"模式: server-MediaPipe | 帧数={len(raw_frames)}")
            word, conf, top = model.recognize_from_images(
                raw_frames, trim_frames=req.trim_frames
            )
            mode = "server-mediapipe"
        else:
            # List[float]（关键点数组）→ 直接转换
            logger.info(f"模式: keypoint-fallback | 帧数={len(raw_frames)}, "
                        f"首帧长度={len(first)}")
            word, conf, top = model.recognize_from_keypoints(
                raw_frames, trim_frames=req.trim_frames
            )
            mode = "keypoint-fallback"

        return RecognizeResponse(
            success=True,
            prediction=word,
            confidence=conf,
            top_predictions=[TopPrediction(**t) for t in top],
            frame_count=len(raw_frames),
            mode=mode,
        )

    except ValueError as e:
        logger.warning(f"数据质量问题: {e}")
        return RecognizeResponse(success=False, error=str(e))
    except Exception as e:
        logger.error(f"识别异常:\n{traceback.format_exc()}")
        return RecognizeResponse(success=False, error=str(e))


# ──────────────────────────────────────────────────────────
#  状态接口（调试用）
# ──────────────────────────────────────────────────────────

@router.get("/status")
async def status():
    """
    返回当前模型和 MediaPipe 的工作状态，方便排查问题。
    访问：GET http://localhost:8000/api/sign/status
    """
    try:
        from backend.services.sign_model import _mp_mode
        model = get_model()
        return {
            "model_loaded":  True,
            "num_classes":   len(model.label_map),
            "mediapipe_mode": _mp_mode,          # "tasks" | "holistic" | "none"
            "device":        str(model.device),
            "model_path":    MODEL_PATH,
            "label_path":    LABEL_PATH,
        }
    except Exception as e:
        return {"model_loaded": False, "error": str(e)}