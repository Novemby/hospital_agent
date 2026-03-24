from fastapi import APIRouter, UploadFile, File, HTTPException
from ..utils import audio_utils, baidu_tts
from .. import schemas

router = APIRouter(tags=["speech"])

@router.post("/speech-to-text", response_model=schemas.SpeechToTextResponse)
async def speech_to_text(audio: UploadFile = File(...)):
    try:
        audio_bytes = await audio.read()
        if len(audio_bytes) == 0:
            raise HTTPException(status_code=400, detail="音频文件为空")
        pcm_data = audio_utils.convert_to_pcm(audio_bytes)
        result = baidu_tts.speech_to_text(pcm_data)
        return result
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"语音识别服务错误: {str(e)}")