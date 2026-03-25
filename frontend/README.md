## 结构目录
frontend/
├── index.html                  # 场景选择入口
├── reg.html                    # 挂号场景
├── handsign.html
├── handsign_assist.html
├── scene2.html                 # 就诊场景
├── scene3.html                 # 我的场景
├── css/
│   ├── global.css              # 全局样式
│   ├── scene1.css              # 挂号场景专属样式（可空）
│   ├── scene2.css              # 就诊场景专属样式
│   ├── scene3.css              # 我的场景专属样式
│   └── components/             # 预留组件样式目录（空）
├── js/
│   ├── scene1.js               # 挂号场景逻辑（对应 reg.html）
│   ├── scene2.js               # 就诊场景逻辑
│   ├── scene3.js               # 我的场景逻辑
│   ├── handsign_assist.js
│   ├── services/
│   │   ├── authService.js      # 认证服务
│   │   ├── signLanguageAgent.js
│   │   └── patientService.js   # 患者数据服务
│   └── utils/                  # 工具函数（空）
└── assets/                     # 静态资源（空）

## 使用方法
# 在命令行输入 python -m http.server 8001
# 浏览器访问 http://localhost:8001/index.html