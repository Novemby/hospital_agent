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

3. 验证接口是否正常：

打开 `http://127.0.0.1:8000/docs`，应看到以下接口：

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

```
http://localhost:8001/index.html
```

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
