# hospital_agent

为听障人士提供的无障碍医院就诊系统。

## 运行前准备

- 已安装 Python 3.9+
- 已安装并启动 MySQL
- 数据库已创建并导入测试数据（见 `db/init.sql`）

### 初始化数据库

在项目根目录执行：

```bash
mysql -u root -p < db/init.sql
```
# 先在“服务”启动mysql

随后请在 `backend/database.py` 中确认数据库账号与密码。如下所示：
SQLALCHEMY_DATABASE_URL = "mysql+pymysql://[填入账号]:[填入密码]@localhost:3306/medical_agent_db?charset=utf8mb4"

## 后端启动

1. 进入项目根目录：

```bash
cd E:\hospital_sys
```

2. 启动 FastAPI：

```bash
uvicorn backend.main:app --reload --port 8000
```
# python -m uvicorn backend.main:app --reload --port 8000

3. 验证接口是否正常：

打开 `http://127.0.0.1:8000/docs`
应看到以下接口：

- `POST /api/doctors/login`
- `GET /api/patients/by-idcard/{id_card}`

## 前端启动

1. 进入前端目录：

```bash
cd E:\hospital_sys\frontend
```

2. 启动静态服务器：

```bash
python -m http.server 8001
```

3. 用浏览器打开：


'http://localhost:8001/index.html'


## 前后端联调说明

- 前端请求的后端地址在 `frontend/js/services/authService.js`
- `API_BASE_URL = http://localhost:8000/api`
- 后端运行在 `http://127.0.0.1:8000`
- 前端运行在 `http://localhost:8001`

## 常见问题

### 1. 405 Method Not Allowed
说明后端没有加载到登录接口，请确认：
- 启动命令为 `uvicorn backend.main:app --reload --port 8000`
- `http://127.0.0.1:8000/openapi.json` 中包含 `/api/doctors/login`

### 2. 打开 `http://localhost:8000/index.html` 显示 Not Found
这是后端端口，不提供静态页面。前端必须使用 `http://localhost:8001/index.html`。

### 3. 登录失败但数据库有账号
请确认后端连接的数据库与测试数据一致，并检查 `sys_doctor` 表中的 `work_no` 与 `password` 是否匹配输入。

## 项目结构
hospital_agent/                                # 项目根目录
├── design.md                                  # 项目设计文档
├── backend/                                   # 核心后端服务（FastAPI）
│   ├── main.py                                # FastAPI 应用入口，挂载路由、CORS
│   ├── config.py                              # 后端配置（数据库、百度API密钥等）
│   ├── database.py                            # 数据库连接与会话管理
│   ├── models.py                              # SQLAlchemy 模型
│   ├── schemas.py                             # Pydantic 模型（请求/响应结构）
│   ├── crud.py                                # 数据库操作函数
│   ├── dependencies.py                        # 依赖注入（如获取当前用户）
│   ├── routers/                               # API 路由模块
│   │   ├── patients.py                        # 患者相关接口（登录、列表、详情）
│   │   ├── doctors.py                         # 医生登录接口
│   │   ├── registrations.py                   # 挂号相关（科室、号源、提交）
│   │   ├── agent.py                           # 挂号智能体对话接口（状态机）
│   │   └── speech.py                          # 语音转文字接口（调用百度API）
│   └── utils/                                 # 工具函数
│       ├── audio_utils.py                     # 音频格式转换
│       └── baidu_tts.py                       # 百度语音 API 封装
├── db/                                        # 数据库初始化脚本
│   └── init.sql                               # 建表语句（患者、医生、挂号、病历等）
├── robust_multistream_checkpoints/            # 手语模型存放目录
│   ├── label_mapping.json                     # 手语标签映射（如 {"头痛":0, "发烧":1,...}）
│   └── best_robust_multistream_graformer.pth  # 训练好的模型权重
├── hybrid_fpn_vit_distill_train.py            # 模型定义及推理文件
└── frontend/                                  # 前端静态资源（原项目）
    ├── index.html                             # 场景选择入口
    ├── reg.html                               # 挂号场景页面
    ├── scene2.html                            # 医生诊间工作台页面
    ├── scene3.html                            # 我的场景页面
    ├── handsign_assist.html                   # 手语辅助窗口（独立页面，与医生端联动）
    ├── css/
    │   ├── global.css                         # 全局样式
    │   ├── scene1.css, scene2.css, scene3.css # 各场景专属样式
    │   └── components/                        # 预留组件样式
    ├── js/
    │   ├── scene1.js, scene2.js, scene3.js    # 各页面逻辑
    │   ├── handsign_assist.js                 # 手语辅助窗口逻辑（调用手语识别服务）
    │   ├── services/
    │   │   ├── authService.js                 # 认证服务（token 管理）
    │   │   ├── signLanguageAgent.js           # 手语识别核心类（封装MediaPipe、调用识别接口）
    │   │   └── patientService.js              # 患者数据服务（localStorage + 后端同步）
    │   └── utils/                             # 工具函数（预留）
    └── assets/                                # 图片等静态资源
