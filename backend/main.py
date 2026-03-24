from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from .database import engine, Base
from .routers import patients, doctors, registrations, agent, speech

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

@app.get("/test")
def test():
    return {"message": "后端服务正常运行"}