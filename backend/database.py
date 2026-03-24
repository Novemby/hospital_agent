from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

# 数据库连接 URL (请将 root 和 password 替换为你实际的账号密码)
# 注意：需要安装 pymysql 驱动 (pip install pymysql)
SQLALCHEMY_DATABASE_URL = "mysql+pymysql://root:123456@localhost:3306/medical_agent_db?charset=utf8mb4"

# 创建引擎
engine = create_engine(SQLALCHEMY_DATABASE_URL, echo=False) # echo=True 可以在控制台看到生成的SQL语句

# 创建会话工厂
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

# 创建基础映射类
Base = declarative_base()

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()