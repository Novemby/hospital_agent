import logging
import os
import traceback
from typing import Any, List, Optional

from fastapi import APIRouter
from pydantic import BaseModel, Field


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/sign", tags=["sign_language"])

_BASE = os.path.normpath(
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..")
)
MODEL_PATH = os.path.join(
    _BASE,
    "robust_multistream_checkpoints",
    "best_robust_multistream_newdata73.pth",
)
LABEL_PATH = os.path.join(
    _BASE,
    "robust_multistream_checkpoints",
    "sign_label_mapping_73.json",
)

_model_instance = None


def get_model():
    global _model_instance
    if _model_instance is None:
        logger.info("Loading sign model from %s", MODEL_PATH)
        from backend.services.sign_model import SignLanguageModel

        _model_instance = SignLanguageModel(MODEL_PATH, LABEL_PATH)
    return _model_instance


class RecognizeRequest(BaseModel):
    # Main path used by the current frontend: flattened keypoint frames.
    frames: Optional[List[Any]] = None
    # Kept for compatibility with older callers that may still send this field.
    frames_b64: Optional[List[str]] = None
    confidence_threshold: float = 0.3
    auto_add: bool = False
    trim_frames: int = 2


class TopPrediction(BaseModel):
    label: str
    confidence: float
    rank: int


class RecognizeResponse(BaseModel):
    success: bool
    word: str = ""
    prediction: str = ""
    confidence: float = 0.0
    top_predictions: List[TopPrediction] = Field(default_factory=list)
    frame_count: int = 0
    mode: str = "keypoint-fallback"
    error: str = ""


def _sample_frame_lengths(frames: List[Any]) -> List[int]:
    lengths: List[int] = []
    for frame in frames[:10]:
        try:
            lengths.append(len(frame))
        except TypeError:
            lengths.append(-1)
    return lengths


@router.post("/recognize", response_model=RecognizeResponse)
async def recognize(request: RecognizeRequest):
    raw_frames = request.frames if request.frames is not None else request.frames_b64 or []

    if not raw_frames:
        return RecognizeResponse(success=False, error="Empty frames")

    if len(raw_frames) < 5:
        return RecognizeResponse(
            success=False,
            error=f"Too few frames: {len(raw_frames)}",
            frame_count=len(raw_frames),
        )

    first = raw_frames[0]
    if isinstance(first, str):
        return RecognizeResponse(
            success=False,
            error="The current backend expects keypoint frames, not base64 image frames.",
            frame_count=len(raw_frames),
            mode="unsupported-image-frames",
        )

    try:
        model_instance = get_model()
        word, confidence = model_instance.recognize(raw_frames)
        return RecognizeResponse(
            success=bool(word),
            word=word,
            prediction=word,
            confidence=confidence,
            frame_count=len(raw_frames),
        )
    except ValueError as exc:
        logger.warning("Sign recognition rejected input: %s", exc)
        return RecognizeResponse(
            success=False,
            error=str(exc),
            frame_count=len(raw_frames),
        )
    except Exception as exc:
        detail = (
            f"{exc} | frame_count={len(raw_frames)} "
            f"| sample_lengths={_sample_frame_lengths(raw_frames)}"
        )
        logger.error("Sign recognition failed:\n%s", traceback.format_exc())
        return RecognizeResponse(
            success=False,
            error=detail,
            frame_count=len(raw_frames),
        )


@router.get("/status")
async def status():
    try:
        model = get_model()
        return {
            "model_loaded": True,
            "num_classes": len(model.label_map),
            "device": str(model.device),
            "model_path": MODEL_PATH,
            "label_path": LABEL_PATH,
        }
    except Exception as exc:
        return {"model_loaded": False, "error": str(exc)}
