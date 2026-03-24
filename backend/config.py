import os
from typing import List

class Settings:
    # 百度语音识别配置
    BAIDU_API_KEY: str = "lwUYmBdFPhZxU7ESNir7mF75"  # 实际应从环境变量读取
    BAIDU_SECRET_KEY: str = "Ny4uiiKwbr1k21yt2iM0SFz6hWo0hwId"
    BAIDU_TOKEN_URL: str = "https://aip.baidubce.com/oauth/2.0/token"
    BAIDU_SPEECH_URL: str = "https://vop.baidu.com/server_api"

    # 科室相关
    DEPARTMENTS: List[str] = ["骨科", "发热门诊", "体检中心", "耳鼻喉科", "内科"]
    DEPARTMENT_CODE_MAP = {
        "骨科": "GK",
        "发热门诊": "FR",
        "体检中心": "TJ",
        "耳鼻喉科": "EBH",
        "内科": "NK",
    }

settings = Settings()