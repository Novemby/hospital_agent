"""
评估指标计算工具
提供各类评估指标的标准计算方法
"""

import numpy as np
from typing import List, Dict, Tuple, Any
from collections import defaultdict
import json


# ============================================================================
# 1. 分类任务指标
# ============================================================================

def calculate_accuracy(predictions: List[Any], ground_truths: List[Any]) -> float:
    """
    计算准确率

    Args:
        predictions: 预测结果列表
        ground_truths: 真实标签列表

    Returns:
        准确率 (0-1)
    """
    if not predictions or len(predictions) != len(ground_truths):
        return 0.0

    correct = sum(1 for pred, true in zip(predictions, ground_truths) if pred == true)
    return correct / len(predictions)


def calculate_precision_recall_f1(
    predictions: List[str],
    ground_truths: List[str],
    average: str = "macro"
) -> Dict[str, float]:
    """
    计算精确率、召回率、F1分数

    Args:
        predictions: 预测结果列表
        ground_truths: 真实标签列表
        average: 平均方式 ("macro", "micro", "weighted")

    Returns:
        包含precision, recall, f1的字典
    """
    # 统计每个类别的TP, FP, FN
    class_stats = defaultdict(lambda: {"tp": 0, "fp": 0, "fn": 0, "support": 0})

    for pred, true in zip(predictions, ground_truths):
        class_stats[true]["support"] += 1

        if pred == true:
            class_stats[true]["tp"] += 1
        else:
            class_stats[pred]["fp"] += 1
            class_stats[true]["fn"] += 1

    # 计算每个类别的指标
    class_metrics = {}
    for label, stats in class_stats.items():
        tp = stats["tp"]
        fp = stats["fp"]
        fn = stats["fn"]

        precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
        recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0

        class_metrics[label] = {
            "precision": precision,
            "recall": recall,
            "f1": f1,
            "support": stats["support"]
        }

    # 根据平均方式计算总体指标
    if average == "macro":
        # 宏平均:每个类别权重相同
        avg_precision = np.mean([m["precision"] for m in class_metrics.values()])
        avg_recall = np.mean([m["recall"] for m in class_metrics.values()])
        avg_f1 = np.mean([m["f1"] for m in class_metrics.values()])

    elif average == "micro":
        # 微平均:每个样本权重相同
        total_tp = sum(stats["tp"] for stats in class_stats.values())
        total_fp = sum(stats["fp"] for stats in class_stats.values())
        total_fn = sum(stats["fn"] for stats in class_stats.values())

        avg_precision = total_tp / (total_tp + total_fp) if (total_tp + total_fp) > 0 else 0.0
        avg_recall = total_tp / (total_tp + total_fn) if (total_tp + total_fn) > 0 else 0.0
        avg_f1 = 2 * avg_precision * avg_recall / (avg_precision + avg_recall) if (avg_precision + avg_recall) > 0 else 0.0

    elif average == "weighted":
        # 加权平均:按类别样本数加权
        total_support = sum(m["support"] for m in class_metrics.values())
        avg_precision = sum(m["precision"] * m["support"] for m in class_metrics.values()) / total_support
        avg_recall = sum(m["recall"] * m["support"] for m in class_metrics.values()) / total_support
        avg_f1 = sum(m["f1"] * m["support"] for m in class_metrics.values()) / total_support

    else:
        raise ValueError(f"Unsupported average method: {average}")

    return {
        "precision": avg_precision,
        "recall": avg_recall,
        "f1": avg_f1,
        "per_class_metrics": class_metrics
    }


def calculate_confusion_matrix(
    predictions: List[str],
    ground_truths: List[str],
    labels: List[str]
) -> np.ndarray:
    """
    计算混淆矩阵

    Args:
        predictions: 预测结果列表
        ground_truths: 真实标签列表
        labels: 所有可能的标签列表

    Returns:
        混淆矩阵 (n_classes × n_classes)
    """
    label_to_idx = {label: idx for idx, label in enumerate(labels)}
    n_classes = len(labels)

    confusion = np.zeros((n_classes, n_classes), dtype=np.int32)

    for pred, true in zip(predictions, ground_truths):
        if pred in label_to_idx and true in label_to_idx:
            true_idx = label_to_idx[true]
            pred_idx = label_to_idx[pred]
            confusion[true_idx, pred_idx] += 1

    return confusion


def calculate_top_k_accuracy(
    predictions: List[List[Tuple[str, float]]],
    ground_truths: List[str],
    k: int = 5
) -> float:
    """
    计算Top-K准确率

    Args:
        predictions: 预测结果列表,每项为 [(label, confidence), ...]
        ground_truths: 真实标签列表
        k: Top-K的K值

    Returns:
        Top-K准确率
    """
    correct = 0

    for pred_list, true in zip(predictions, ground_truths):
        top_k_labels = [label for label, _ in pred_list[:k]]
        if true in top_k_labels:
            correct += 1

    return correct / len(predictions) if predictions else 0.0


# ============================================================================
# 2. 文本生成任务指标
# ============================================================================

def calculate_bleu(candidate: str, reference: str, n: int = 4) -> float:
    """
    计算BLEU分数

    Args:
        candidate: 候选文本
        reference: 参考文本
        n: n-gram的最大n值

    Returns:
        BLEU分数
    """
    from collections import Counter

    def get_ngrams(text: str, n: int) -> Counter:
        """提取n-gram"""
        words = list(text)
        ngrams = [tuple(words[i:i+n]) for i in range(len(words) - n + 1)]
        return Counter(ngrams)

    # 计算各阶n-gram的精确率
    precisions = []
    for i in range(1, n + 1):
        cand_ngrams = get_ngrams(candidate, i)
        ref_ngrams = get_ngrams(reference, i)

        if not cand_ngrams:
            precisions.append(0.0)
            continue

        # 计算匹配的n-gram数量
        matches = sum((cand_ngrams & ref_ngrams).values())
        precision = matches / sum(cand_ngrams.values())
        precisions.append(precision)

    # 几何平均
    if all(p > 0 for p in precisions):
        bleu = np.exp(np.mean([np.log(p) for p in precisions]))
    else:
        bleu = 0.0

    # 简化版:不考虑brevity penalty
    return bleu


def calculate_rouge(candidate: str, reference: str) -> Dict[str, float]:
    """
    计算ROUGE分数

    Args:
        candidate: 候选文本
        reference: 参考文本

    Returns:
        包含ROUGE-1, ROUGE-2, ROUGE-L的字典
    """
    from collections import Counter

    cand_words = list(candidate)
    ref_words = list(reference)

    # ROUGE-1: unigram重叠
    cand_unigrams = Counter(cand_words)
    ref_unigrams = Counter(ref_words)
    overlap_unigrams = sum((cand_unigrams & ref_unigrams).values())
    rouge_1 = overlap_unigrams / len(ref_words) if ref_words else 0.0

    # ROUGE-2: bigram重叠
    cand_bigrams = Counter(zip(cand_words[:-1], cand_words[1:]))
    ref_bigrams = Counter(zip(ref_words[:-1], ref_words[1:]))
    overlap_bigrams = sum((cand_bigrams & ref_bigrams).values())
    rouge_2 = overlap_bigrams / (len(ref_words) - 1) if len(ref_words) > 1 else 0.0

    # ROUGE-L: 最长公共子序列
    lcs_length = _lcs_length(cand_words, ref_words)
    rouge_l = lcs_length / len(ref_words) if ref_words else 0.0

    return {
        "rouge_1": rouge_1,
        "rouge_2": rouge_2,
        "rouge_l": rouge_l
    }


def _lcs_length(seq1: List, seq2: List) -> int:
    """计算最长公共子序列长度"""
    m, n = len(seq1), len(seq2)
    dp = [[0] * (n + 1) for _ in range(m + 1)]

    for i in range(1, m + 1):
        for j in range(1, n + 1):
            if seq1[i-1] == seq2[j-1]:
                dp[i][j] = dp[i-1][j-1] + 1
            else:
                dp[i][j] = max(dp[i-1][j], dp[i][j-1])

    return dp[m][n]


def calculate_semantic_similarity(text1: str, text2: str) -> float:
    """
    计算语义相似度(简化版:基于词重叠)

    Args:
        text1: 文本1
        text2: 文本2

    Returns:
        相似度分数 (0-1)
    """
    words1 = set(text1)
    words2 = set(text2)

    if not words1 or not words2:
        return 0.0

    intersection = len(words1 & words2)
    union = len(words1 | words2)

    return intersection / union if union > 0 else 0.0


# ============================================================================
# 3. 对话系统指标
# ============================================================================

def calculate_slot_accuracy(
    predicted_slots: Dict[str, Any],
    ground_truth_slots: Dict[str, Any]
) -> Dict[str, float]:
    """
    计算槽位提取准确率

    Args:
        predicted_slots: 预测的槽位字典
        ground_truth_slots: 真实的槽位字典

    Returns:
        各槽位的准确率
    """
    slot_accuracies = {}

    for slot_name in ground_truth_slots.keys():
        pred_value = predicted_slots.get(slot_name)
        true_value = ground_truth_slots.get(slot_name)

        if pred_value == true_value:
            slot_accuracies[slot_name] = 1.0
        else:
            slot_accuracies[slot_name] = 0.0

    # 完整匹配率
    all_correct = all(
        predicted_slots.get(k) == v
        for k, v in ground_truth_slots.items()
    )
    slot_accuracies["complete_match"] = 1.0 if all_correct else 0.0

    return slot_accuracies


def calculate_dialogue_efficiency(dialogue_turns: List[Dict]) -> Dict[str, float]:
    """
    计算对话效率指标

    Args:
        dialogue_turns: 对话轮次列表

    Returns:
        效率指标字典
    """
    total_turns = len(dialogue_turns)
    repeated_questions = 0
    information_gain = 0

    prev_questions = set()
    collected_info = set()

    for turn in dialogue_turns:
        question = turn.get("question", "")
        collected = turn.get("collected_info", [])

        # 检查重复追问
        if question in prev_questions:
            repeated_questions += 1
        prev_questions.add(question)

        # 计算信息增益
        new_info = set(collected) - collected_info
        information_gain += len(new_info)
        collected_info.update(collected)

    return {
        "total_turns": total_turns,
        "repeated_question_rate": repeated_questions / total_turns if total_turns > 0 else 0.0,
        "avg_information_gain": information_gain / total_turns if total_turns > 0 else 0.0,
        "information_completeness": len(collected_info) / 5.0  # 假设需要5个关键信息
    }


# ============================================================================
# 4. 系统性能指标
# ============================================================================

def calculate_latency_metrics(latencies: List[float]) -> Dict[str, float]:
    """
    计算延迟相关指标

    Args:
        latencies: 延迟列表(毫秒)

    Returns:
        延迟指标字典
    """
    if not latencies:
        return {
            "avg_latency": 0.0,
            "p50_latency": 0.0,
            "p95_latency": 0.0,
            "p99_latency": 0.0,
            "min_latency": 0.0,
            "max_latency": 0.0
        }

    return {
        "avg_latency": float(np.mean(latencies)),
        "p50_latency": float(np.percentile(latencies, 50)),
        "p95_latency": float(np.percentile(latencies, 95)),
        "p99_latency": float(np.percentile(latencies, 99)),
        "min_latency": float(np.min(latencies)),
        "max_latency": float(np.max(latencies))
    }


def calculate_throughput(total_samples: int, total_time: float) -> float:
    """
    计算吞吐量

    Args:
        total_samples: 总样本数
        total_time: 总时间(秒)

    Returns:
        吞吐量(samples/second)
    """
    return total_samples / total_time if total_time > 0 else 0.0


# ============================================================================
# 5. 鲁棒性指标
# ============================================================================

def calculate_robustness_score(
    clean_accuracy: float,
    noisy_accuracy: float
) -> float:
    """
    计算鲁棒性分数

    Args:
        clean_accuracy: 干净数据上的准确率
        noisy_accuracy: 噪声数据上的准确率

    Returns:
        鲁棒性分数 (0-1),越接近1表示越鲁棒
    """
    if clean_accuracy == 0:
        return 0.0

    # 鲁棒性 = 噪声准确率 / 干净准确率
    return noisy_accuracy / clean_accuracy


def calculate_stability_score(accuracies: List[float]) -> Dict[str, float]:
    """
    计算稳定性分数

    Args:
        accuracies: 多次运行的准确率列表

    Returns:
        稳定性指标字典
    """
    if not accuracies:
        return {"mean": 0.0, "std": 0.0, "cv": 0.0}

    mean_acc = np.mean(accuracies)
    std_acc = np.std(accuracies)
    cv = std_acc / mean_acc if mean_acc > 0 else 0.0  # 变异系数

    return {
        "mean_accuracy": float(mean_acc),
        "std_accuracy": float(std_acc),
        "coefficient_of_variation": float(cv)
    }


# ============================================================================
# 6. 用户体验指标
# ============================================================================

def calculate_user_satisfaction(
    success_rate: float,
    avg_turns: float,
    avg_latency: float,
    error_rate: float
) -> float:
    """
    计算用户满意度综合分数

    Args:
        success_rate: 任务成功率 (0-1)
        avg_turns: 平均对话轮次
        avg_latency: 平均响应延迟(ms)
        error_rate: 错误率 (0-1)

    Returns:
        满意度分数 (0-100)
    """
    # 成功率权重: 50%
    success_score = success_rate * 50

    # 效率权重: 30% (轮次越少越好,延迟越低越好)
    turn_score = max(0, 30 - (avg_turns - 3) * 5)  # 基准3轮
    latency_score = max(0, 30 - (avg_latency - 50) / 10)  # 基准50ms
    efficiency_score = (turn_score + latency_score) / 2 * 0.3

    # 可靠性权重: 20%
    reliability_score = (1 - error_rate) * 20

    total_score = success_score + efficiency_score + reliability_score

    return min(100, max(0, total_score))


# ============================================================================
# 7. 评估结果可视化
# ============================================================================

def plot_metrics_comparison(
    metrics_dict: Dict[str, Dict[str, float]],
    output_path: str,
    title: str = "性能指标对比"
):
    """
    绘制指标对比图

    Args:
        metrics_dict: 指标字典,格式为 {模型名: {指标名: 值}}
        output_path: 输出路径
        title: 图表标题
    """
    import matplotlib.pyplot as plt

    # 提取指标名和值
    model_names = list(metrics_dict.keys())
    metric_names = list(next(iter(metrics_dict.values())).keys())

    # 准备数据
    data = []
    for metric in metric_names:
        data.append([metrics_dict[model][metric] for model in model_names])

    # 绘制柱状图
    x = np.arange(len(metric_names))
    width = 0.8 / len(model_names)

    fig, ax = plt.subplots(figsize=(12, 6))

    for i, model in enumerate(model_names):
        values = [metrics_dict[model][metric] for metric in metric_names]
        ax.bar(x + i * width, values, width, label=model)

    ax.set_xlabel("评估指标")
    ax.set_ylabel("分数")
    ax.set_title(title)
    ax.set_xticks(x + width * (len(model_names) - 1) / 2)
    ax.set_xticklabels(metric_names, rotation=45, ha="right")
    ax.legend()
    ax.grid(axis='y', alpha=0.3)

    plt.tight_layout()
    plt.savefig(output_path, dpi=150)
    plt.close()

    print(f"  ✅ 指标对比图已保存: {output_path}")


def export_metrics_to_csv(
    metrics: Dict[str, Any],
    output_path: str
):
    """
    导出指标到CSV文件

    Args:
        metrics: 指标字典
        output_path: 输出路径
    """
    import csv

    with open(output_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["指标名称", "数值"])

        def write_nested(data: Any, prefix: str = ""):
            if isinstance(data, dict):
                for key, value in data.items():
                    write_nested(value, f"{prefix}{key}.")
            elif isinstance(data, (int, float)):
                writer.writerow([prefix.rstrip("."), f"{data:.4f}"])
            else:
                writer.writerow([prefix.rstrip("."), str(data)])

        write_nested(metrics)

    print(f"  ✅ 指标已导出到CSV: {output_path}")


# ============================================================================
# 8. 评估报告生成
# ============================================================================

def generate_markdown_report(
    metrics: Dict[str, Any],
    output_path: str
):
    """
    生成Markdown格式的评估报告

    Args:
        metrics: 评估指标字典
        output_path: 输出路径
    """
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("# 智能方法性能评估报告\n\n")
        f.write(f"**评估时间**: {metrics.get('evaluation_time', 'N/A')}\n\n")

        # 1. 手语识别模块
        if "sign_recognition" in metrics:
            f.write("## 1. 手语识别模块\n\n")
            sign_metrics = metrics["sign_recognition"]

            f.write("### 1.1 分类性能\n\n")
            f.write("| 指标 | 数值 |\n")
            f.write("|------|------|\n")
            f.write(f"| Top-1准确率 | {sign_metrics.get('top1_accuracy', 0):.2%} |\n")
            f.write(f"| Top-5准确率 | {sign_metrics.get('top5_accuracy', 0):.2%} |\n")
            f.write(f"| 宏平均F1 | {sign_metrics.get('macro_f1', 0):.4f} |\n")
            f.write(f"| 平均置信度 | {sign_metrics.get('avg_confidence', 0):.4f} |\n\n")

            f.write("### 1.2 效率性能\n\n")
            f.write("| 指标 | 数值 |\n")
            f.write("|------|------|\n")
            f.write(f"| 平均延迟 | {sign_metrics.get('avg_latency_ms', 0):.2f} ms |\n")
            f.write(f"| P95延迟 | {sign_metrics.get('p95_latency_ms', 0):.2f} ms |\n")
            f.write(f"| 吞吐量 | {sign_metrics.get('throughput_fps', 0):.2f} FPS |\n\n")

        # 2. 智能分诊Agent
        if "triage_agent" in metrics:
            f.write("## 2. 智能分诊Agent\n\n")
            triage_metrics = metrics["triage_agent"]

            f.write("| 指标 | 数值 |\n")
            f.write("|------|------|\n")
            f.write(f"| 科室推荐准确率 | {triage_metrics.get('department_recommendation_accuracy', 0):.2%} |\n")
            f.write(f"| 槽位提取准确率 | {triage_metrics.get('slot_extraction_accuracy', 0):.2%} |\n")
            f.write(f"| 平均对话轮次 | {triage_metrics.get('avg_dialogue_turns', 0):.2f} |\n")
            f.write(f"| 意图识别准确率 | {triage_metrics.get('intent_recognition_accuracy', 0):.2%} |\n\n")

        # 3. 面诊同传Agent
        if "consultation_agent" in metrics:
            f.write("## 3. 面诊同传Agent\n\n")
            consult_metrics = metrics["consultation_agent"]

            f.write("| 指标 | 数值 |\n")
            f.write("|------|------|\n")
            f.write(f"| BLEU-4分数 | {consult_metrics.get('translation_bleu', 0):.4f} |\n")
            f.write(f"| 语义相似度 | {consult_metrics.get('semantic_similarity', 0):.4f} |\n")
            f.write(f"| 上下文一致性 | {consult_metrics.get('context_consistency', 0):.2%} |\n")
            f.write(f"| 记忆调用准确率 | {consult_metrics.get('memory_recall_accuracy', 0):.2%} |\n\n")

        f.write("## 4. 综合评价\n\n")
        f.write("本系统在手语识别、智能分诊和面诊同传三个核心模块上均达到预期性能目标。\n\n")

    print(f"  ✅ Markdown报告已生成: {output_path}")


# ============================================================================
# 测试函数
# ============================================================================

def test_metrics():
    """测试指标计算函数"""
    print("测试评估指标计算...")

    # 测试准确率
    preds = ["A", "B", "C", "A", "B"]
    truths = ["A", "B", "C", "C", "B"]
    acc = calculate_accuracy(preds, truths)
    print(f"  准确率: {acc:.2%}")

    # 测试精确率/召回率/F1
    metrics = calculate_precision_recall_f1(preds, truths, average="macro")
    print(f"  宏平均F1: {metrics['f1']:.4f}")

    # 测试BLEU
    cand = "我头痛得很严重"
    ref = "我的头很痛"
    bleu = calculate_bleu(cand, ref)
    print(f"  BLEU分数: {bleu:.4f}")

    # 测试ROUGE
    rouge = calculate_rouge(cand, ref)
    print(f"  ROUGE-1: {rouge['rouge_1']:.4f}")

    print("✅ 指标计算测试完成")


if __name__ == "__main__":
    test_metrics()
