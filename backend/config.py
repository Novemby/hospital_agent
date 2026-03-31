import os
from typing import List


def _strip_env_value(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def _load_local_env() -> None:
    project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    env_path = os.path.join(project_root, ".env.local")
    if not os.path.exists(env_path):
        return

    with open(env_path, "r", encoding="utf-8") as env_file:
        for raw_line in env_file:
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue

            key, value = line.split("=", 1)
            key = key.strip()
            if not key or key in os.environ:
                continue

            os.environ[key] = _strip_env_value(value)


_load_local_env()


class Settings:
    # 百度语音识别配置
    BAIDU_API_KEY: str = os.getenv("BAIDU_API_KEY", "")
    BAIDU_SECRET_KEY: str = os.getenv("BAIDU_SECRET_KEY", "")
    BAIDU_TOKEN_URL: str = "https://aip.baidubce.com/oauth/2.0/token"
    BAIDU_SPEECH_URL: str = "https://vop.baidu.com/server_api"

    # 科室相关
    DEPARTMENTS: List[str] = [
        "骨科",
        "发热门诊",
        "体检中心",
        "耳鼻喉科",
        "内科",
        "外科",
        "儿科",
        "妇科",
        "神经内科",
        "皮肤科",
    ]
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
    }


settings = Settings()
