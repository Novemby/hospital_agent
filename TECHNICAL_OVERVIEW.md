# 手语分诊系统：连续识别逻辑与 AI 助手智能方法说明

## 目录

1. [连续手语识别流水线](#一连续手语识别流水线)
2. [AI 挂号助手智能方法](#二ai-挂号助手智能方法)
3. [前后端数据流](#三前后端数据流)

---
 
## 一、连续手语识别流水线

### 1.1 整体架构

```
摄像头视频流
    │
    ▼
MediaPipe Holistic（浏览器端实时推理）
    │  提取 33 姿态点 + 21×2 手部关键点 = 258 维/帧
    ▼
SignLanguageAgent（前端 JS 类）
    │  连续识别状态机 + 滑动窗口
    ▼
后端 /api/sign/recognize（FastAPI）
    │  RobustMultiStreamModel（PyTorch）
    ▼
识别结果回调 → Vue 界面更新
```

### 1.2 关键点提取（`extractLandmarks`）

每帧提取三组关键点，拼接为 258 维向量：

| 部位 | 关键点数 | 维度 | 说明 |
|------|---------|------|------|
| 人体姿态 | 33 | 33×4 = 132 | x, y, z, visibility |
| 左手 | 21 | 21×3 = 63 | x, y, z |
| 右手 | 21 | 21×3 = 63 | x, y, z |

缺失部位（手未入镜）时补零，保证维度恒定。

### 1.3 连续识别状态机

`SignLanguageAgent` 通过 `continuousMode` 标志驱动一个轻量状态机，使用**速度检测**进行动作分割：

```
[等待手势]
    │  检测到手部关键点 + 高速运动
    ▼
[录制中] ──── 连续低速 ≥ 8 帧 ──→ [触发识别]
    │                                │
    │  帧数 ≥ MAX_SIGN_FRAMES(150)   │  帧数 < MIN_SIGN_FRAMES(10)
    └──────────────────────────────→ │  静默丢弃，回到等待
                                     ▼
                                [发送后端]
                                     │
                                     ▼
                                [等待手势]
```

核心参数：

| 参数 | 值 | 含义 |
|------|----|------|
| `LOW_VELOCITY_THRESHOLD` | 0.01 | 低速阈值（归一化坐标系下的距离） |
| `LOW_VELOCITY_FRAMES` | 8 帧 (~0.27s) | 连续低速多少帧后触发识别 |
| `VELOCITY_BUFFER_SIZE` | 5 帧 | 速度平滑窗口大小 |
| `MIN_SIGN_FRAMES` | 10 帧 | 有效手势最少帧数，不足则丢弃 |
| `MAX_SIGN_FRAMES` | 150 帧 (~5s) | 防止无限录制的上限 |
| `NO_HAND_THRESHOLD` | 15 帧 (~0.5s) | 手部消失多少帧后触发识别（备用机制） |
| `SENTENCE_TIMEOUT` | 2000ms | 无新词汇多久后触发句子结束回调 |
| `WORD_DEDUPE_WINDOW` | 1500ms | 短时间内相同词汇去重窗口 |

**速度检测原理**：
1. 每帧计算左右手中心点的3D位置
2. 计算相邻帧之间的欧氏距离作为速度
3. 维护一个5帧的速度滑动窗口，计算平均速度
4. 当平均速度低于阈值且持续8帧以上时，判定为动作结束
5. 手部消失时作为备用分割机制

### 1.4 滑动窗口多数投票

录制结束后，`stopRecording` 对帧缓冲区做滑动窗口处理，再并行发送多个子窗口到后端：

```
帧缓冲区（N 帧）
    │
    ├── 若 N ≤ WINDOW_SIZE(45)：直接发送整段
    │
    └── 若 N > 45：
        ├── 窗口 1: frames[0:45]
        ├── 窗口 2: frames[15:60]   ← 步长 WINDOW_STRIDE=15
        ├── ...（最多 MAX_WINDOWS=5 个子窗口）
        └── 完整序列 frames[0:N]

        → Promise.all 并行发送
        → 多数投票：选出现次数最多的词作为最终结果
```

这样做的好处：单次手势动作的起止边界不精确时，多个重叠窗口能互相补偿，提升识别稳定性。

### 1.5 词汇去重与句子边界检测

识别到词汇后：

1. **短时去重**：1.5 秒内相同词汇只触发一次 `onWordRecognized`，防止同一手势被多个窗口重复计入。
2. **句子计时器**：每次识别到新词，重置 2 秒倒计时。倒计时归零时触发 `onSentenceComplete`，通知上层"这句话说完了"。

### 1.6 后端识别模型（`RobustMultiStreamModel`）

模型位于 `backend/services/sign_model.py`，采用**鲁棒多流骨架时序建模**架构：

```
输入：258 维关键点序列（T 帧）
    │
    ├── 特征构造层
    │   ├── 关节流 (Joint, 262 维)：归一化坐标
    │   ├── 骨骼流 (Bone, 48 维)：相邻关键点向量差
    │   ├── 速度流 (Velocity, 262 维)：相邻帧差分
    │   ├── 关系流 (Relation, 76 维)：手与面部关键部位相对向量
    │   └── 图结构流 (Graph, 75 节点)：骨架拓扑图
    │
    ├── 主干建模层
    │   ├── 关节/骨骼/速度/关系流 → Temporal FPN → Temporal Transformer
    │   └── 图结构流 → 图卷积 (GCN) → 时间编码
    │
    ├── 融合层：跨流 Transformer，联合建模五个分支
    │
    └── 分类头 → 66 类医学词汇
```

拒识机制：置信度 < `reject_threshold(0.55)` 或 Top-1 与 Top-2 差距 < `reject_margin(0.10)` 时，返回"未知"而非强行输出低置信度结果。

---

## 二、AI 挂号助手智能方法

### 2.1 整体设计：统一智慧医疗 Agent

后端使用 `SmartHospitalAgent`（`backend/utils/langchain_agent.py`）作为全局单例，贯穿整个就诊流程。它有两个阶段性任务：

| 阶段 | 方法 | 场景 |
|------|------|------|
| 挂号分诊阶段 | `triage_stage_understand` | 大厅挂号，追问症状、推荐科室 |
| 诊间面诊阶段 | `consultation_stage_polish` | 诊室内，将手语词汇润色为自然语句 |

### 2.2 挂号分诊阶段（`triage_stage_understand`）

#### 调用链

```
用户输入文本
    │
    ▼
LangChain PromptTemplate（注入今日日期、科室白名单、已知槽位、已知症状）
    │
    ▼
DeepSeek Chat LLM（temperature=0.1，低温保证稳定性）
    │
    ▼
JsonOutputParser → HybridAgentDecision（Pydantic 结构化输出）
    │
    ▼
返回：intent / department / symptom_struct / missing_fields / strategy
```

#### Prompt 核心指令

```
你是三甲医院的统一智慧医疗 Agent，当前处于【大厅挂号分诊阶段】。
1. 推断意图与科室。如果只有症状，推荐最合适白名单科室。
2. 精准提取 symptom_struct 字段。只要表达了身体不适，primary_symptom 绝不能为 null！
3. 绝对不允许捏造症状。
```

#### 结构化输出（`HybridAgentDecision`）

```python
intent: str              # register / confirm / change_department
department: str          # 科室名（必须在白名单内）
recommended_department: str
date: str                # YYYY-MM-DD
period: str              # 上午 / 下午
confirm: bool
missing_fields: List[str]
strategy: str            # collect_missing_info / proceed_to_slots
symptom_struct: SymptomStruct
```

`SymptomStruct` 字段：

```python
primary_symptom: str     # 核心症状，如"头痛"
body_part: str           # 部位，如"左肩"
duration: str            # 持续时间，如"1周"
severity: str            # 严重程度
accompanying_symptoms: List[str]
original_text: str       # 用户原话完整保留
```

### 2.3 会话状态机（`agent.py`）

挂号流程由后端 `registration_agent_chat` 维护一个内存会话（`helpers.get_or_create_agent_session`），状态机如下：

```
INIT
 │  用户首次输入
 ▼
COLLECT_INFO  ←──────────────────────────────┐
 │  primary_symptom 已提取 + 科室已推荐        │
 ▼                                           │
QUERY_SLOTS   ──── 查询号源失败 ──────────────┘
 │  号源查询成功，返回可用时段
 ▼
CONFIRM
 │  用户确认时段
 ▼
SUCCESS
```

#### 防覆盖机制

LLM 返回的 `symptom_struct` 中，只有非空字段才会更新会话，防止大模型返回 `null` 抹掉已有记录：

```python
for k, v in llm_symptom.items():
    if v:  # 只有真的提取到内容才更新
        session["symptom_struct"][k] = v
```

#### 软槽位推进策略

避免无限追问，满足以下任一条件即推进到查询号源：

- `duration` 已填写（软槽位完整）
- 用户已被追问 ≥ 2 次（`soft_ask_count >= 2`）
- 用户表达紧急意图（"快点"、"很痛"等关键词）

### 2.4 兜底降级机制

LLM 不可用（无 API Key 或网络异常）时，自动降级到本地规则：

- `detect_department(text)`：关键词匹配科室（如"头痛" → 神经内科）
- `detect_date(text)`：正则匹配日期表达（"明天"、"下周一"等）
- `_detect_period(text)`：匹配"上午"/"下午"

### 2.5 诊间面诊阶段（`consultation_stage_polish`）

面诊场景下，手语词汇不再用于挂号，而是实时翻译给医生看。Agent 接收零碎词汇列表和医患对话上下文，输出一句自然语句：

```
输入：["头", "痛", "三天"]  +  上下文（医患对话记录）
    │
    ▼
Prompt：结合上下文，将词汇翻译为患者对医生说话的礼貌口吻
    │
    ▼
输出："医生，我头痛已经持续三天了。"
```

约束：不允许超出患者提供词汇范畴捏造病情。

---

## 三、前后端数据流

### 3.1 手语识别请求

```
POST /api/sign/recognize
{
  "frames": [[258个浮点数], ...]   // T 帧关键点序列
}

响应：
{
  "word": "头痛",
  "confidence": 0.87
}
```

### 3.2 挂号 Agent 对话请求

```
POST /api/agent/register/chat
{
  "session_id": "session_xxx",
  "patient_id": 1,
  "message": "我头痛发烧"
}

响应：
{
  "reply": "您好，根据您描述的头痛发烧，建议挂内科。请问持续多久了？",
  "state": "COLLECT_INFO",
  "options": ["今天刚开始", "1-3天", "超过1周", "很久了"],
  "slots": {
    "department": "内科",
    "triage_symptoms": "{\"primary_symptom\": \"头痛\", ...}"
  }
}
```

### 3.3 前端状态与 UI 对应关系

| `assistantPhase` | 触发时机 | 界面表现 |
|-----------------|---------|---------|
| `WELCOME` | 进入手语分诊页 | 大字"请描述您的症状" |
| `LISTENING` | 点击"开始识别" / 识别到词汇 | "我在看，请继续比划..." + 已识别词汇标签 |
| `THINKING` | 点击"确认发送" | "正在分析您的症状..." + 跳动动画 |
| `REPLY` | 收到 Agent 回复 | 大字显示回复内容 + 选项按钮 |
| `SUCCESS` | `agentState === 'SUCCESS'` | "挂号成功！" |
