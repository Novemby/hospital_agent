# 评估系统使用指南

## 快速开始

### 1. 生成测试数据集

```bash
cd evaluation
python generate_test_dataset.py
```

这将生成以下测试数据集:
- `triage_agent_test_dataset.json` - 智能分诊Agent测试集(200样本)
- `consultation_agent_test_dataset.json` - 面诊同传Agent测试集(150样本)
- `sign_recognition_test_info.json` - 手语识别测试信息

### 2. 运行评估测试

```bash
python evaluation_test.py
```

这将执行完整的评估流程并生成 `evaluation_report.json`

### 3. 手语识别专项评估

```bash
python sign_recognition_eval.py
```

需要准备测试数据文件 `sign_test_data.npz`,格式:
```python
{
    "frames": np.array,  # shape: (n_samples, n_frames, 258)
    "labels": np.array   # shape: (n_samples,)
}
```

## 评估指标说明

### 手语识别模块

| 指标 | 说明 | 计算公式 | 目标值 |
|------|------|---------|--------|
| Top-1准确率 | 预测类别与真实类别完全匹配的比例 | 正确样本数/总样本数 | ≥90% |
| Top-5准确率 | 真实类别在预测概率前5名中的比例 | Top5命中数/总样本数 | ≥98% |
| 宏平均F1 | 所有类别F1分数的算术平均 | mean(F1_i) | ≥0.88 |
| 平均延迟 | 单样本平均推理时间 | sum(latency)/n | ≤50ms |
| 关键点缺失容忍 | 遮挡10%关键点后的准确率 | 遮挡后正确数/总数 | ≥80% |

### 智能分诊Agent

| 指标 | 说明 | 计算公式 | 目标值 |
|------|------|---------|--------|
| 科室推荐准确率 | 推荐科室与标注一致的比例 | 正确推荐数/总样本数 | ≥90% |
| 槽位提取准确率 | 所有槽位都正确提取的比例 | 完整匹配数/总样本数 | ≥85% |
| 平均对话轮次 | 完成挂号所需的平均交互轮数 | sum(turns)/n | ≤3.5轮 |
| 意图识别准确率 | 正确识别用户意图的比例 | 正确识别数/总样本数 | ≥88% |
| 重复追问率 | 对同一信息重复询问的比例 | 重复次数/总轮次 | ≤8% |

### 面诊同传Agent

| 指标 | 说明 | 计算公式 | 目标值 |
|------|------|---------|--------|
| BLEU-4 | 机器翻译质量(4-gram) | 几何平均(n-gram精确率) | ≥0.75 |
| ROUGE-L | 最长公共子序列召回率 | LCS长度/参考长度 | ≥0.70 |
| 语义相似度 | 与参考翻译的语义相似度 | 词重叠度/Jaccard | ≥0.85 |
| 上下文一致性 | 翻译与对话历史的一致性 | 一致样本数/总数 | ≥90% |
| 记忆调用准确率 | 正确引用患者历史的比例 | 正确调用数/总数 | ≥85% |

## 测试数据集结构

### 智能分诊Agent测试集

```json
{
  "metadata": {
    "dataset_name": "智能分诊Agent测试数据集",
    "total_samples": 200,
    "categories": {
      "clear_symptoms": 50,
      "vague_symptoms": 50,
      "multi_turn": 50,
      "edge_cases": 50
    }
  },
  "test_cases": {
    "clear_symptoms": [
      {
        "input": "我头痛,已经持续3天了",
        "expected": {
          "department": "神经内科",
          "symptom_struct": {
            "primary_symptom": "头痛",
            "duration": "1-3天"
          }
        }
      }
    ]
  }
}
```

### 面诊同传Agent测试集

```json
{
  "metadata": {
    "dataset_name": "面诊同传Agent测试数据集",
    "total_samples": 150
  },
  "test_cases": {
    "word_translation": [
      {
        "input": {
          "words": ["头", "痛", "很", "严重"],
          "context": ""
        },
        "expected": {
          "polished_text": "我头痛得很严重",
          "objective_summary": "主诉:头痛,程度严重"
        }
      }
    ]
  }
}
```

## 自定义评估

### 添加新的测试样本

编辑 `generate_test_dataset.py`,在对应的列表中添加测试用例:

```python
clear_symptoms.append({
    "input": "你的症状描述",
    "expected": {
        "department": "期望科室",
        "symptom_struct": {
            "primary_symptom": "主要症状",
            "duration": "持续时间"
        }
    }
})
```

### 自定义评估指标

在 `metrics_utils.py` 中添加新的指标计算函数:

```python
def calculate_custom_metric(predictions, ground_truths):
    # 你的计算逻辑
    return metric_value
```

## 评估结果解读

### 性能等级划分

**手语识别模块**:
- A级(优秀): Top-1准确率≥90%, 延迟≤50ms
- B级(良好): Top-1准确率≥85%, 延迟≤80ms
- C级(合格): Top-1准确率≥75%, 延迟≤120ms
- D级(需改进): 低于C级标准

**智能分诊Agent**:
- A级(优秀): 科室推荐≥90%, 平均轮次≤3.5
- B级(良好): 科室推荐≥85%, 平均轮次≤4.5
- C级(合格): 科室推荐≥75%, 平均轮次≤6.0
- D级(需改进): 低于C级标准

**面诊同传Agent**:
- A级(优秀): BLEU≥0.75, 记忆调用≥85%
- B级(良好): BLEU≥0.65, 记忆调用≥75%
- C级(合格): BLEU≥0.55, 记忆调用≥65%
- D级(需改进): 低于C级标准

## 常见问题

### Q1: 测试数据集如何准备?

**手语识别**: 需要从训练数据中划分测试集,或使用独立采集的测试视频。建议按8:1:1划分训练/验证/测试集。

**Agent模块**: 使用 `generate_test_dataset.py` 自动生成,或根据真实用户交互日志构建。

### Q2: 如何提高评估准确性?

1. 增加测试样本数量
2. 确保测试集与训练集无重叠
3. 包含多样化的场景和边界情况
4. 使用真实用户数据而非合成数据

### Q3: 评估时间过长怎么办?

1. 减少测试样本数量(使用采样)
2. 使用GPU加速推理
3. 并行化评估流程
4. 只评估关键指标

### Q4: 如何对比不同模型?

使用 `metrics_utils.py` 中的 `plot_metrics_comparison` 函数:

```python
from metrics_utils import plot_metrics_comparison

metrics_dict = {
    "基线模型": {"accuracy": 0.75, "f1": 0.73},
    "当前模型": {"accuracy": 0.90, "f1": 0.88}
}

plot_metrics_comparison(metrics_dict, "comparison.png")
```

## 进阶使用

### 批量评估多个模型

```python
models = [
    ("baseline", "path/to/baseline.pth"),
    ("current", "path/to/current.pth"),
    ("optimized", "path/to/optimized.pth")
]

results = {}
for name, path in models:
    evaluator = SignRecognitionEvaluator(path, label_path)
    results[name] = evaluator.evaluate_comprehensive(test_data, output_dir)

# 对比结果
plot_metrics_comparison(results, "model_comparison.png")
```

### 交叉验证

```python
from sklearn.model_selection import KFold

kf = KFold(n_splits=5, shuffle=True, random_state=42)
fold_results = []

for fold, (train_idx, test_idx) in enumerate(kf.split(data)):
    test_data = [data[i] for i in test_idx]
    metrics = evaluator.evaluate_basic_performance(test_data)
    fold_results.append(metrics)

# 计算平均性能和标准差
avg_accuracy = np.mean([r["top1_accuracy"] for r in fold_results])
std_accuracy = np.std([r["top1_accuracy"] for r in fold_results])
print(f"5折交叉验证准确率: {avg_accuracy:.2%} ± {std_accuracy:.2%}")
```

## 输出文件说明

评估完成后会生成以下文件:

```
evaluation/
├── triage_agent_test_dataset.json      # 分诊Agent测试集
├── consultation_agent_test_dataset.json # 同传Agent测试集
├── sign_recognition_test_info.json     # 手语识别测试信息
├── evaluation_report.json              # 综合评估报告
├── results/
│   ├── sign_recognition_evaluation.json # 手语识别详细结果
│   ├── sign_recognition_report.json    # 手语识别性能报告
│   ├── confusion_matrix.png            # 混淆矩阵热力图
│   └── evaluation_report.md            # Markdown格式报告
```

## 参考文献

1. 手语识别评估标准: CSL-Daily, NMFs-CSL数据集论文
2. 对话系统评估: BLEU, ROUGE指标原始论文
3. 分类任务评估: Precision, Recall, F1-Score标准定义
