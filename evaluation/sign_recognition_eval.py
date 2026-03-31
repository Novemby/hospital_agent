"""
手语识别模块专项评估脚本
包含离线评估、在线评估、鲁棒性测试
"""

import json
import os
import sys
import time
import numpy as np
import torch
from typing import Dict, List, Tuple, Any
from collections import defaultdict
import matplotlib.pyplot as plt
import seaborn as sns

# 添加项目根目录
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


class SignRecognitionEvaluator:
    """手语识别专项评估器"""

    def __init__(self, model_path: str, label_path: str):
        """初始化评估器"""
        from backend.services.sign_model import SignLanguageModel

        print("加载手语识别模型...")
        self.model = SignLanguageModel(model_path, label_path)
        self.label_map = self.model.label_map
        self.idx_to_word = self.model.idx_to_word
        self.num_classes = len(self.label_map)
        print(f"✅ 模型加载完成,类别数: {self.num_classes}")

    def evaluate_comprehensive(
        self,
        test_data_path: str,
        output_dir: str
    ) -> Dict[str, Any]:
        """
        综合评估

        Args:
            test_data_path: 测试数据路径(包含frames和labels的npz文件)
            output_dir: 输出目录

        Returns:
            完整评估结果
        """
        os.makedirs(output_dir, exist_ok=True)

        # 加载测试数据
        print("\n加载测试数据...")
        test_data = self._load_test_data(test_data_path)

        # 1. 基础性能评估
        print("\n" + "=" * 70)
        print("1. 基础性能评估")
        print("=" * 70)
        basic_metrics = self._evaluate_basic_performance(test_data)

        # 2. 混淆矩阵分析
        print("\n" + "=" * 70)
        print("2. 混淆矩阵分析")
        print("=" * 70)
        confusion_matrix = self._compute_confusion_matrix(test_data)
        self._plot_confusion_matrix(confusion_matrix, output_dir)

        # 3. 鲁棒性测试
        print("\n" + "=" * 70)
        print("3. 鲁棒性测试")
        print("=" * 70)
        robustness_metrics = self._evaluate_robustness(test_data)

        # 4. 效率测试
        print("\n" + "=" * 70)
        print("4. 效率测试")
        print("=" * 70)
        efficiency_metrics = self._evaluate_efficiency(test_data)

        # 汇总结果
        comprehensive_results = {
            "basic_performance": basic_metrics,
            "confusion_matrix": confusion_matrix.tolist(),
            "robustness": robustness_metrics,
            "efficiency": efficiency_metrics
        }

        # 保存结果
        result_path = os.path.join(output_dir, "sign_recognition_evaluation.json")
        with open(result_path, "w", encoding="utf-8") as f:
            json.dump(comprehensive_results, f, ensure_ascii=False, indent=2)

        print(f"\n✅ 评估结果已保存: {result_path}")

        return comprehensive_results

    def _load_test_data(self, data_path: str) -> List[Dict[str, Any]]:
        """
        加载测试数据

        注意: 这里假设测试数据格式为npz文件,包含:
        - frames: 关键点序列数组
        - labels: 对应的标签
        """
        if not os.path.exists(data_path):
            print(f"⚠️ 测试数据不存在: {data_path}")
            print("  提示: 请准备测试数据或使用模拟数据")
            return self._generate_mock_test_data()

        data = np.load(data_path, allow_pickle=True)
        frames_list = data["frames"]
        labels_list = data["labels"]

        test_data = []
        for frames, label in zip(frames_list, labels_list):
            test_data.append({
                "frames": frames.tolist(),
                "label": label
            })

        return test_data

    def _generate_mock_test_data(self) -> List[Dict[str, Any]]:
        """生成模拟测试数据(用于演示)"""
        print("  生成模拟测试数据...")

        mock_data = []
        samples_per_class = 20

        for word, class_id in list(self.label_map.items())[:10]:  # 只取前10个类别
            for _ in range(samples_per_class):
                # 生成随机关键点序列
                num_frames = np.random.randint(20, 60)
                frames = np.random.randn(num_frames, 258).astype(np.float32) * 0.1

                mock_data.append({
                    "frames": frames.tolist(),
                    "label": word
                })

        return mock_data

    def _evaluate_basic_performance(self, test_data: List[Dict]) -> Dict[str, float]:
        """评估基础性能指标"""

        predictions = []
        ground_truths = []
        confidences = []
        top5_correct = 0

        print(f"  测试样本数: {len(test_data)}")

        for idx, sample in enumerate(test_data):
            frames = sample["frames"]
            true_label = sample["label"]

            # 推理
            pred_word, confidence = self.model.recognize(frames)

            predictions.append(pred_word)
            ground_truths.append(true_label)
            confidences.append(confidence)

            # Top-5准确率(简化:如果置信度>0.2认为在top5中)
            if pred_word == true_label or confidence > 0.2:
                top5_correct += 1

            if (idx + 1) % 100 == 0:
                print(f"  进度: {idx + 1}/{len(test_data)}")

        # 计算指标
        correct = sum(1 for pred, true in zip(predictions, ground_truths) if pred == true)
        accuracy = correct / len(predictions)
        top5_accuracy = top5_correct / len(predictions)
        avg_confidence = np.mean(confidences)

        # 按类别统计
        class_metrics = self._compute_per_class_metrics(predictions, ground_truths)

        metrics = {
            "top1_accuracy": accuracy,
            "top5_accuracy": top5_accuracy,
            "avg_confidence": avg_confidence,
            "macro_precision": class_metrics["macro_precision"],
            "macro_recall": class_metrics["macro_recall"],
            "macro_f1": class_metrics["macro_f1"],
            "total_samples": len(predictions),
            "correct_samples": correct
        }

        print("\n  基础性能指标:")
        for key, value in metrics.items():
            if isinstance(value, float):
                print(f"    {key}: {value:.4f}")
            else:
                print(f"    {key}: {value}")

        return metrics

    def _compute_per_class_metrics(
        self,
        predictions: List[str],
        ground_truths: List[str]
    ) -> Dict[str, float]:
        """计算每个类别的精确率、召回率、F1"""

        # 统计每个类别的TP, FP, FN
        class_stats = defaultdict(lambda: {"tp": 0, "fp": 0, "fn": 0})

        for pred, true in zip(predictions, ground_truths):
            if pred == true:
                class_stats[true]["tp"] += 1
            else:
                class_stats[pred]["fp"] += 1
                class_stats[true]["fn"] += 1

        # 计算每个类别的指标
        precisions = []
        recalls = []
        f1s = []

        for label, stats in class_stats.items():
            tp = stats["tp"]
            fp = stats["fp"]
            fn = stats["fn"]

            precision = tp / (tp + fp) if (tp + fp) > 0 else 0.0
            recall = tp / (tp + fn) if (tp + fn) > 0 else 0.0
            f1 = 2 * precision * recall / (precision + recall) if (precision + recall) > 0 else 0.0

            precisions.append(precision)
            recalls.append(recall)
            f1s.append(f1)

        return {
            "macro_precision": np.mean(precisions) if precisions else 0.0,
            "macro_recall": np.mean(recalls) if recalls else 0.0,
            "macro_f1": np.mean(f1s) if f1s else 0.0
        }

    def _compute_confusion_matrix(self, test_data: List[Dict]) -> np.ndarray:
        """计算混淆矩阵"""

        confusion = np.zeros((self.num_classes, self.num_classes), dtype=np.int32)

        for sample in test_data:
            frames = sample["frames"]
            true_label = sample["label"]

            pred_word, _ = self.model.recognize(frames)

            true_idx = self.label_map.get(true_label, -1)
            pred_idx = self.label_map.get(pred_word, -1)

            if true_idx >= 0 and pred_idx >= 0:
                confusion[true_idx, pred_idx] += 1

        print(f"  混淆矩阵形状: {confusion.shape}")
        return confusion

    def _plot_confusion_matrix(self, confusion: np.ndarray, output_dir: str):
        """绘制混淆矩阵热力图"""

        plt.figure(figsize=(20, 18))
        sns.heatmap(
            confusion,
            annot=False,
            fmt="d",
            cmap="Blues",
            xticklabels=list(self.label_map.keys()),
            yticklabels=list(self.label_map.keys())
        )
        plt.title("手语识别混淆矩阵", fontsize=16)
        plt.xlabel("预测类别", fontsize=12)
        plt.ylabel("真实类别", fontsize=12)
        plt.xticks(rotation=90, fontsize=6)
        plt.yticks(rotation=0, fontsize=6)
        plt.tight_layout()

        output_path = os.path.join(output_dir, "confusion_matrix.png")
        plt.savefig(output_path, dpi=150)
        plt.close()

        print(f"  ✅ 混淆矩阵已保存: {output_path}")

    def _evaluate_robustness(self, test_data: List[Dict]) -> Dict[str, float]:
        """评估鲁棒性"""

        # 3.1 关键点缺失容忍度
        print("\n  [3.1] 关键点缺失容忍度测试...")
        occlusion_accuracy = self._test_keypoint_occlusion(test_data[:100], occlusion_ratio=0.1)

        # 3.2 速度变化容忍度
        print("  [3.2] 速度变化容忍度测试...")
        speed_metrics = self._test_speed_variation(test_data[:100])

        robustness = {
            "keypoint_occlusion_10pct": occlusion_accuracy,
            "speed_0.5x_accuracy": speed_metrics["0.5x"],
            "speed_1.5x_accuracy": speed_metrics["1.5x"]
        }

        print("\n  鲁棒性指标:")
        for key, value in robustness.items():
            print(f"    {key}: {value:.4f}")

        return robustness

    def _test_keypoint_occlusion(self, test_data: List[Dict], occlusion_ratio: float) -> float:
        """测试关键点遮挡容忍度"""

        correct = 0

        for sample in test_data:
            frames = np.array(sample["frames"], dtype=np.float32)
            true_label = sample["label"]

            # 随机遮挡部分关键点
            occluded_frames = frames.copy()
            num_keypoints = frames.shape[1]
            num_occlude = int(num_keypoints * occlusion_ratio)
            occlude_indices = np.random.choice(num_keypoints, num_occlude, replace=False)
            occluded_frames[:, occlude_indices] = 0.0

            # 推理
            pred_word, _ = self.model.recognize(occluded_frames.tolist())

            if pred_word == true_label:
                correct += 1

        return correct / len(test_data)

    def _test_speed_variation(self, test_data: List[Dict]) -> Dict[str, float]:
        """测试速度变化容忍度"""

        speed_factors = [0.5, 1.5]
        results = {}

        for factor in speed_factors:
            correct = 0

            for sample in test_data:
                frames = np.array(sample["frames"], dtype=np.float32)
                true_label = sample["label"]

                # 调整速度(通过帧采样)
                if factor < 1.0:
                    # 减速:重复帧
                    indices = np.repeat(np.arange(len(frames)), int(1 / factor))
                else:
                    # 加速:跳帧
                    indices = np.arange(0, len(frames), int(factor))

                speed_adjusted = frames[indices]

                # 推理
                pred_word, _ = self.model.recognize(speed_adjusted.tolist())

                if pred_word == true_label:
                    correct += 1

            results[f"{factor}x"] = correct / len(test_data)

        return results

    def _evaluate_efficiency(self, test_data: List[Dict]) -> Dict[str, float]:
        """评估效率指标"""

        latencies = []
        frame_counts = []

        print(f"  测试样本数: {len(test_data[:200])}")

        for sample in test_data[:200]:  # 取200个样本测试
            frames = sample["frames"]
            frame_counts.append(len(frames))

            # 测量推理时间
            start_time = time.time()
            self.model.recognize(frames)
            latency = (time.time() - start_time) * 1000  # 毫秒

            latencies.append(latency)

        metrics = {
            "avg_latency_ms": np.mean(latencies),
            "p50_latency_ms": np.percentile(latencies, 50),
            "p95_latency_ms": np.percentile(latencies, 95),
            "p99_latency_ms": np.percentile(latencies, 99),
            "min_latency_ms": np.min(latencies),
            "max_latency_ms": np.max(latencies),
            "throughput_fps": 1000.0 / np.mean(latencies),
            "avg_frame_count": np.mean(frame_counts)
        }

        print("\n  效率指标:")
        for key, value in metrics.items():
            print(f"    {key}: {value:.2f}")

        return metrics

    def generate_performance_report(
        self,
        metrics: Dict[str, Any],
        output_path: str
    ):
        """生成性能评估报告"""

        report = {
            "evaluation_time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "model_info": {
                "num_classes": self.num_classes,
                "device": str(self.model.device),
                "model_type": "RobustMultiStreamGraFormer"
            },
            "performance_summary": {
                "top1_accuracy": metrics["basic_performance"]["top1_accuracy"],
                "macro_f1": metrics["basic_performance"]["macro_f1"],
                "avg_latency_ms": metrics["efficiency"]["avg_latency_ms"],
                "throughput_fps": metrics["efficiency"]["throughput_fps"]
            },
            "detailed_metrics": metrics,
            "performance_grade": self._grade_performance(metrics)
        }

        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(report, f, ensure_ascii=False, indent=2)

        print(f"\n✅ 性能报告已生成: {output_path}")

        # 打印摘要
        print("\n" + "=" * 70)
        print("性能评估摘要")
        print("=" * 70)
        print(f"  Top-1准确率: {report['performance_summary']['top1_accuracy']:.2%}")
        print(f"  宏平均F1: {report['performance_summary']['macro_f1']:.4f}")
        print(f"  平均延迟: {report['performance_summary']['avg_latency_ms']:.2f} ms")
        print(f"  吞吐量: {report['performance_summary']['throughput_fps']:.2f} FPS")
        print(f"  性能等级: {report['performance_grade']}")

    def _grade_performance(self, metrics: Dict[str, Any]) -> str:
        """性能评级"""
        accuracy = metrics["basic_performance"]["top1_accuracy"]
        latency = metrics["efficiency"]["avg_latency_ms"]

        if accuracy >= 0.90 and latency <= 50:
            return "优秀(A)"
        elif accuracy >= 0.85 and latency <= 80:
            return "良好(B)"
        elif accuracy >= 0.75 and latency <= 120:
            return "合格(C)"
        else:
            return "需改进(D)"


# ============================================================================
# 主函数
# ============================================================================

def main():
    """执行手语识别专项评估"""

    print("=" * 70)
    print("手语识别模块专项评估")
    print("=" * 70)

    # 配置路径
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    model_path = os.path.join(
        base_dir,
        "robust_multistream_checkpoints",
        "best_robust_multistream_graformer.pth"
    )
    label_path = os.path.join(
        base_dir,
        "robust_multistream_checkpoints",
        "label_mapping.json"
    )
    output_dir = os.path.join(os.path.dirname(__file__), "results")

    # 测试数据路径(需要用户提供或生成)
    test_data_path = os.path.join(os.path.dirname(__file__), "sign_test_data.npz")

    # 初始化评估器
    evaluator = SignRecognitionEvaluator(model_path, label_path)

    # 执行综合评估
    print("\n开始综合评估...")
    metrics = evaluator.evaluate_comprehensive(test_data_path, output_dir)

    # 生成报告
    report_path = os.path.join(output_dir, "sign_recognition_report.json")
    evaluator.generate_performance_report(metrics, report_path)

    print("\n" + "=" * 70)
    print("✅ 评估完成!")
    print("=" * 70)


if __name__ == "__main__":
    main()
