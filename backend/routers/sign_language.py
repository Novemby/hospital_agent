# backend/routers/sign_language.py
import os
from typing import List, Dict, Any
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from backend.services.sign_model import SignLanguageModel

# 模型路径配置（可根据实际情况调整）
MODEL_PATH = "robust_multistream_checkpoints/best_robust_multistream_graformer.pth"
LABEL_PATH = "robust_multistream_checkpoints/label_mapping.json"

# 初始化模型（全局单例）
model = None

def get_model():
    global model
    if model is None:
        if not os.path.exists(MODEL_PATH):
            raise RuntimeError(f"Model file not found: {MODEL_PATH}")
        if not os.path.exists(LABEL_PATH):
            raise RuntimeError(f"Label file not found: {LABEL_PATH}")
        model = SignLanguageModel(MODEL_PATH, LABEL_PATH)
    return model

router = APIRouter(prefix="/api/sign", tags=["sign_language"])

class RecognizeRequest(BaseModel):
    frames: List[List[float]]  # 每帧关键点列表，每个元素为长度为1629的一维数组

class RecognizeResponse(BaseModel):
    word: str
    confidence: float

@router.post("/recognize", response_model=RecognizeResponse)
async def recognize(request: RecognizeRequest):
    """
    手语识别接口
    接收前端采集的关键点序列，返回识别结果
    """
    if not request.frames:
        raise HTTPException(status_code=400, detail="Empty frames")
    try:
        model_instance = get_model()
        word, conf = model_instance.recognize(request.frames)
        return RecognizeResponse(word=word, confidence=conf)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))