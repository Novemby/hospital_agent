import os
from typing import List

class Settings:
    # 百度语音识别配置
    BAIDU_API_KEY: str = "lwUYmBdFPhZxU7ESNir7mF75"  # 实际应从环境变量读取
    BAIDU_SECRET_KEY: str = "Ny4uiiKwbr1k21yt2iM0SFz6hWo0hwId"
    BAIDU_TOKEN_URL: str = "https://aip.baidubce.com/oauth/2.0/token"
    BAIDU_SPEECH_URL: str = "https://vop.baidu.com/server_api"

    # 科室相关
    DEPARTMENTS: List[str] = [
        "骨科", "发热门诊", "体检中心", "耳鼻喉科", "内科",
        "外科", "儿科", "妇科", "神经内科", "皮肤科",
        "消化内科", "呼吸内科", "心血管内科", "泌尿外科",
        "眼科", "口腔科", "精神心理科", "急诊科"
    ]

    # 科室名称到数据库 dept_code 的映射
    DEPARTMENT_CODE_MAP = {
        "骨科": "GK",
        "发热门诊": "FR",
        "体检中心": "TJ",
        "耳鼻喉科": "EBH",
        "内科": "NK",
        "外科": "WK",
        "儿科": "EK",
        "妇科": "FK",
        "神经内科": "SNK",
        "皮肤科": "PFK",
        "消化内科": "XWNK",
        "呼吸内科": "HXNK",
        "心血管内科": "XXGNK",
        "泌尿外科": "MNWK",
        "眼科": "YK",
        "口腔科": "KQK",
        "精神心理科": "JSXLK",
        "急诊科": "JZK",
    }

settings = Settings()