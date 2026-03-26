import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

# =====================================================================
# 🌟 终极环境诊断与加载：强制寻找 .env，彻底解决 PyCharm 路径玄学问题
# =====================================================================
print("\n" + "=" * 50)
print("🚀 [系统启动诊断雷达]")
try:
    from dotenv import load_dotenv

    # 强制获取 main.py 所在的绝对路径目录，防止 PyCharm 工作目录偏差
    current_dir = os.path.dirname(os.path.abspath(__file__))
    env_path = os.path.join(current_dir, '.env')

    if os.path.exists(env_path):
        load_dotenv(env_path)
        print(f"✅ 找到 .env 文件，路径: {env_path}")
    else:
        print(f"❌ 未找到 .env 文件！请确保它在这个目录下: {current_dir}")
        print("   (注意：Windows下请确保文件没被存为 .env.txt)")

except ImportError:
    print("⚠️ 未安装 python-dotenv，请在终端运行: pip install python-dotenv")

# 检查 Key 是否真的读到了环境里
api_key = os.getenv("OPENAI_API_KEY")
if api_key:
    print(f"🔑 成功读到 API KEY: {api_key[:6]}......{api_key[-4:]} (长度: {len(api_key)})")
else:
    print("❌ 致命警告：OPENAI_API_KEY 依然为空！大模型将无法工作，系统已降级为「无脑复读机」模式！")
print("=" * 50 + "\n")

from backend.database import engine, Base
from backend.routers import patients, doctors, registrations, agent, speech
from backend.routers import sign_language

# 创建数据库表（生产环境应使用 Alembic）
Base.metadata.create_all(bind=engine)

app = FastAPI(title="无障碍智慧医疗辅助系统 API")

# 配置 CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 注册路由
app.include_router(patients.router)
app.include_router(doctors.router)
app.include_router(registrations.router)
app.include_router(agent.router)
app.include_router(speech.router)
app.include_router(sign_language.router)


@app.get("/test")
def test():
    return {"message": "后端服务正常运行"}