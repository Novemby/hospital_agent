import base64
import time
import requests
from ..config import settings

_baidu_token = None
_token_expires = 0

def get_baidu_token():
    global _baidu_token, _token_expires
    if _baidu_token and time.time() < _token_expires:
        return _baidu_token
    params = {
        "grant_type": "client_credentials",
        "client_id": settings.BAIDU_API_KEY,
        "client_secret": settings.BAIDU_SECRET_KEY
    }
    resp = requests.post(settings.BAIDU_TOKEN_URL, params=params)
    if resp.status_code == 200:
        data = resp.json()
        _baidu_token = data.get("access_token")
        _token_expires = time.time() + 28 * 24 * 3600
        return _baidu_token
    else:
        raise Exception(f"获取百度token失败，状态码：{resp.status_code}")

def speech_to_text(pcm_bytes: bytes) -> dict:
    token = get_baidu_token()
    audio_base64 = base64.b64encode(pcm_bytes).decode('utf-8')
    data = {
        "format": "pcm",
        "rate": 16000,
        "channel": 1,
        "cuid": "fastapi_server",
        "token": token,
        "speech": audio_base64,
        "len": len(pcm_bytes)
    }
    headers = {"Content-Type": "application/json"}
    resp = requests.post(settings.BAIDU_SPEECH_URL, json=data, headers=headers, timeout=10)
    result = resp.json()
    if result.get("err_no") == 0:
        return {"success": True, "text": result["result"][0]}
    else:
        return {"success": False, "error": result.get("err_msg", "未知错误")}