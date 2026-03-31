"""
测试数据集生成脚本
生成用于评估智能方法的测试数据集
"""

import json
import random
import os
from datetime import datetime, timedelta
from typing import List, Dict, Any

# 设置随机种子以保证可复现性
random.seed(42)

# 输出目录
OUTPUT_DIR = os.path.dirname(os.path.abspath(__file__)) if '__file__' in globals() else os.getcwd()
os.makedirs(OUTPUT_DIR, exist_ok=True)


# ============================================================================
# 1. 智能分诊Agent测试数据集生成
# ============================================================================

def generate_triage_test_dataset():
    """生成智能分诊Agent测试数据集"""

    # 1.1 明确症状描述样本(50个)
    clear_symptoms = [
        {
            "input": "我头痛,已经持续3天了",
            "expected": {
                "department": "神经内科",
                "symptom_struct": {
                    "primary_symptom": "头痛",
                    "body_part": "头部",
                    "duration": "1-3天",
                    "severity": None,
                    "accompanying_symptoms": []
                },
                "strategy": "proceed_to_slots"
            }
        },
        {
            "input": "右上腹疼痛,吃完饭后更严重,大概一周了",
            "expected": {
                "department": "消化内科",
                "symptom_struct": {
                    "primary_symptom": "疼痛",
                    "body_part": "右上腹",
                    "duration": "超过1周",
                    "severity": "中度",
                    "accompanying_symptoms": ["餐后加重"]
                },
                "strategy": "proceed_to_slots"
            }
        },
        {
            "input": "咳嗽发烧,喉咙痛,今天早上开始的",
            "expected": {
                "department": "呼吸内科",
                "symptom_struct": {
                    "primary_symptom": "咳嗽",
                    "body_part": "呼吸道",
                    "duration": "今天刚开始",
                    "severity": None,
                    "accompanying_symptoms": ["发烧", "喉咙痛"]
                },
                "strategy": "proceed_to_slots"
            }
        },
        {
            "input": "左膝盖疼,走路的时候特别明显,已经两个月了",
            "expected": {
                "department": "骨科",
                "symptom_struct": {
                    "primary_symptom": "疼痛",
                    "body_part": "左膝盖",
                    "duration": "很久了",
                    "severity": "中度",
                    "accompanying_symptoms": ["活动时加重"]
                },
                "strategy": "proceed_to_slots"
            }
        },
        {
            "input": "胸闷气短,爬楼梯就喘,持续一周多了",
            "expected": {
                "department": "心内科",
                "symptom_struct": {
                    "primary_symptom": "胸闷",
                    "body_part": "胸部",
                    "duration": "超过1周",
                    "severity": "中度",
                    "accompanying_symptoms": ["气短", "活动后加重"]
                },
                "strategy": "proceed_to_slots"
            }
        }
    ]

    # 扩展到50个样本
    symptom_templates = [
        ("头晕", "头部", "神经内科"),
        ("恶心", "腹部", "消化内科"),
        ("腹泻", "腹部", "消化内科"),
        ("心悸", "胸部", "心内科"),
        ("流鼻涕", "鼻部", "耳鼻喉科"),
        ("耳鸣", "耳部", "耳鼻喉科"),
        ("视力模糊", "眼部", "眼科"),
        ("皮疹", "皮肤", "皮肤科"),
        ("关节肿胀", "关节", "骨科"),
        ("尿频", "泌尿系统", "泌尿外科")
    ]

    durations = ["今天刚开始", "1-3天", "超过1周", "很久了"]
    body_parts = ["左侧", "右侧", "上部", "下部", ""]

    for i in range(45):
        symptom, part, dept = random.choice(symptom_templates)
        duration = random.choice(durations)
        body_modifier = random.choice(body_parts)
        full_part = f"{body_modifier}{part}" if body_modifier else part

        clear_symptoms.append({
            "input": f"{full_part}{symptom},持续{duration}",
            "expected": {
                "department": dept,
                "symptom_struct": {
                    "primary_symptom": symptom,
                    "body_part": full_part,
                    "duration": duration,
                    "severity": None,
                    "accompanying_symptoms": []
                },
                "strategy": "proceed_to_slots"
            }
        })

    # 1.2 模糊症状描述样本(50个)
    vague_symptoms = [
        {
            "input": "我不舒服",
            "expected": {
                "department": None,
                "missing_fields": ["primary_symptom", "body_part", "duration"],
                "strategy": "collect_missing_info",
                "next_question": "请问您具体哪里不舒服?"
            }
        },
        {
            "input": "头疼",
            "expected": {
                "department": "神经内科",
                "symptom_struct": {
                    "primary_symptom": "头疼",
                    "body_part": "头部",
                    "duration": None
                },
                "missing_fields": ["duration"],
                "strategy": "collect_missing_info",
                "next_question": "请问持续多久了?"
            }
        },
        {
            "input": "肚子有点不舒服",
            "expected": {
                "department": "消化内科",
                "symptom_struct": {
                    "primary_symptom": "不适",
                    "body_part": "腹部",
                    "duration": None
                },
                "missing_fields": ["duration"],
                "strategy": "collect_missing_info"
            }
        }
    ]

    # 扩展模糊样本
    vague_templates = [
        ("疼", None, ["body_part", "duration"]),
        ("不舒服", None, ["primary_symptom", "body_part", "duration"]),
        ("有点难受", None, ["primary_symptom", "body_part", "duration"]),
        ("感觉不太好", None, ["primary_symptom", "body_part", "duration"])
    ]

    for i in range(47):
        symptom, dept, missing = random.choice(vague_templates)
        vague_symptoms.append({
            "input": symptom,
            "expected": {
                "department": dept,
                "missing_fields": missing,
                "strategy": "collect_missing_info"
            }
        })

    # 1.3 多轮对话场景(50个)
    multi_turn_scenarios = [
        {
            "turns": [
                {
                    "user": "我想挂号",
                    "expected_response": "请问您哪里不舒服?",
                    "expected_state": "COLLECT_INFO"
                },
                {
                    "user": "头疼",
                    "expected_response": "请问持续多久了?",
                    "expected_state": "COLLECT_INFO"
                },
                {
                    "user": "三天了",
                    "expected_response": "已为您匹配神经内科",
                    "expected_state": "CONFIRM"
                }
            ],
            "expected_final": {
                "department": "神经内科",
                "symptom_struct": {
                    "primary_symptom": "头疼",
                    "duration": "1-3天"
                }
            }
        },
        {
            "turns": [
                {
                    "user": "肚子疼",
                    "expected_response": "请问具体哪个部位?",
                    "expected_state": "COLLECT_INFO"
                },
                {
                    "user": "右上腹",
                    "expected_response": "请问持续多久了?",
                    "expected_state": "COLLECT_INFO"
                },
                {
                    "user": "一周多了",
                    "expected_response": "已为您匹配消化内科",
                    "expected_state": "CONFIRM"
                }
            ],
            "expected_final": {
                "department": "消化内科",
                "symptom_struct": {
                    "primary_symptom": "疼痛",
                    "body_part": "右上腹",
                    "duration": "超过1周"
                }
            }
        }
    ]

    # 扩展多轮对话场景
    for i in range(48):
        symptom = random.choice(["头晕", "咳嗽", "胸闷", "腹泻", "关节痛"])
        duration = random.choice(durations)
        multi_turn_scenarios.append({
            "turns": [
                {"user": symptom, "expected_state": "COLLECT_INFO"},
                {"user": duration, "expected_state": "CONFIRM"}
            ],
            "expected_final": {
                "symptom_struct": {
                    "primary_symptom": symptom,
                    "duration": duration
                }
            }
        })

    # 1.4 边界异常情况(50个)
    edge_cases = [
        {
            "input": "我要换科室",
            "context": {"current_department": "神经内科"},
            "expected": {
                "intent": "change_department",
                "strategy": "collect_department"
            }
        },
        {
            "input": "重新描述",
            "expected": {
                "intent": "reset",
                "strategy": "reset_session",
                "expected_state": "COLLECT_INFO"
            }
        },
        {
            "input": "快点,我很痛",
            "expected": {
                "intent": "urgent_registration",
                "strategy": "skip_soft_questions",
                "urgency": True
            }
        },
        {
            "input": "不要这个科室,换一个",
            "context": {"current_department": "消化内科"},
            "expected": {
                "intent": "change_department",
                "strategy": "collect_department"
            }
        }
    ]

    # 扩展边界情况
    edge_templates = [
        ("取消挂号", "cancel"),
        ("我不想挂了", "cancel"),
        ("换个时间", "change_time"),
        ("明天可以吗", "change_date"),
        ("有没有下午的", "specify_period")
    ]

    for i in range(46):
        text, intent = random.choice(edge_templates)
        edge_cases.append({
            "input": text,
            "expected": {
                "intent": intent,
                "strategy": "handle_edge_case"
            }
        })

    # 合并所有测试数据
    triage_dataset = {
        "metadata": {
            "dataset_name": "智能分诊Agent测试数据集",
            "version": "1.0",
            "created_at": datetime.now().isoformat(),
            "total_samples": 200,
            "categories": {
                "clear_symptoms": 50,
                "vague_symptoms": 50,
                "multi_turn": 50,
                "edge_cases": 50
            }
        },
        "test_cases": {
            "clear_symptoms": clear_symptoms,
            "vague_symptoms": vague_symptoms,
            "multi_turn_scenarios": multi_turn_scenarios,
            "edge_cases": edge_cases
        }
    }

    # 保存数据集
    output_path = os.path.join(OUTPUT_DIR, "triage_agent_test_dataset.json")
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(triage_dataset, f, ensure_ascii=False, indent=2)

    print(f"[OK] 智能分诊Agent测试数据集已生成: {output_path}")
    print(f"   - 明确症状: {len(clear_symptoms)}个")
    print(f"   - 模糊症状: {len(vague_symptoms)}个")
    print(f"   - 多轮对话: {len(multi_turn_scenarios)}个")
    print(f"   - 边界情况: {len(edge_cases)}个")

    return triage_dataset


# ============================================================================
# 2. 面诊同传Agent测试数据集生成
# ============================================================================

def generate_consultation_test_dataset():
    """生成面诊同传Agent测试数据集"""

    # 2.1 手语词汇翻译样本(50个)
    word_translation = [
        {
            "input": {
                "words": ["头", "痛", "很", "严重"],
                "context": ""
            },
            "expected": {
                "polished_text": "我头痛得很严重",
                "objective_summary": "主诉:头痛,程度严重"
            }
        },
        {
            "input": {
                "words": ["肚子", "不舒服", "恶心"],
                "context": ""
            },
            "expected": {
                "polished_text": "我肚子不舒服,还有点恶心",
                "objective_summary": "主诉:腹部不适,伴恶心"
            }
        },
        {
            "input": {
                "words": ["咳嗽", "一周", "发烧"],
                "context": ""
            },
            "expected": {
                "polished_text": "我咳嗽一周了,还发烧",
                "objective_summary": "主诉:咳嗽1周,伴发热"
            }
        }
    ]

    # 扩展词汇翻译样本
    word_templates = [
        (["头晕", "站不稳"], "我头晕,站不稳", "主诉:头晕,伴平衡障碍"),
        (["胸闷", "气短"], "我胸闷气短", "主诉:胸闷,伴气短"),
        (["腹泻", "三天"], "我腹泻三天了", "主诉:腹泻3天"),
        (["眼睛", "模糊"], "我眼睛看东西模糊", "主诉:视物模糊"),
        (["耳朵", "嗡嗡响"], "我耳朵一直嗡嗡响", "主诉:耳鸣")
    ]

    for i in range(47):
        words, polished, summary = random.choice(word_templates)
        word_translation.append({
            "input": {"words": words, "context": ""},
            "expected": {"polished_text": polished, "objective_summary": summary}
        })

    # 2.2 上下文理解样本(50个)
    context_understanding = [
        {
            "input": {
                "words": ["还是", "那样"],
                "context": "医生:您的头痛有好转吗?\n患者手语:"
            },
            "expected": {
                "polished_text": "头痛还是和之前一样,没有好转",
                "objective_summary": "头痛症状无缓解"
            }
        },
        {
            "input": {
                "words": ["更", "严重", "了"],
                "context": "医生:吃药后症状有变化吗?\n患者手语:"
            },
            "expected": {
                "polished_text": "吃药后症状更严重了",
                "objective_summary": "用药后症状加重"
            }
        },
        {
            "input": {
                "words": ["好", "一点"],
                "context": "医生:休息后疼痛有缓解吗?\n患者手语:"
            },
            "expected": {
                "polished_text": "休息后疼痛好一点了",
                "objective_summary": "休息后疼痛缓解"
            }
        }
    ]

    # 扩展上下文理解样本
    for i in range(47):
        context_understanding.append({
            "input": {
                "words": random.choice([["是的"], ["不是"], ["有点"], ["没有"]]),
                "context": "医生询问症状变化"
            },
            "expected": {
                "polished_text": "根据上下文理解患者回应",
                "objective_summary": "患者对医生询问的回应"
            }
        })

    # 2.3 长期记忆调用样本(50个)
    memory_recall = [
        {
            "input": {
                "words": ["老毛病", "又犯了"],
                "context": "",
                "patient_profile": json.dumps({
                    "chronic_conditions": ["高血压", "糖尿病"],
                    "frequent_symptoms": ["头晕", "乏力"]
                }, ensure_ascii=False)
            },
            "expected": {
                "polished_text": "我的高血压老毛病又犯了,头晕乏力",
                "objective_summary": "既往高血压病史,本次头晕乏力复发",
                "memory_used": True
            }
        },
        {
            "input": {
                "words": ["和", "上次", "一样"],
                "context": "",
                "patient_profile": json.dumps({
                    "visit_history": [
                        {"date": "2024-03-15", "symptoms": ["胃痛", "反酸"], "department": "消化内科"}
                    ]
                }, ensure_ascii=False)
            },
            "expected": {
                "polished_text": "和上次一样,胃痛反酸",
                "objective_summary": "症状同前次就诊(2024-03-15):胃痛、反酸",
                "memory_used": True
            }
        }
    ]

    # 扩展记忆调用样本
    for i in range(48):
        memory_recall.append({
            "input": {
                "words": ["又", "不舒服"],
                "context": "",
                "patient_profile": json.dumps({
                    "frequent_symptoms": [random.choice(["头痛", "胃痛", "关节痛"])]
                }, ensure_ascii=False)
            },
            "expected": {
                "polished_text": "根据病史理解患者表述",
                "objective_summary": "结合既往病史的症状描述",
                "memory_used": True
            }
        })

    # 合并所有测试数据
    consultation_dataset = {
        "metadata": {
            "dataset_name": "面诊同传Agent测试数据集",
            "version": "1.0",
            "created_at": datetime.now().isoformat(),
            "total_samples": 150,
            "categories": {
                "word_translation": 50,
                "context_understanding": 50,
                "memory_recall": 50
            }
        },
        "test_cases": {
            "word_translation": word_translation,
            "context_understanding": context_understanding,
            "memory_recall": memory_recall
        }
    }

    # 保存数据集
    output_path = os.path.join(OUTPUT_DIR, "consultation_agent_test_dataset.json")
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(consultation_dataset, f, ensure_ascii=False, indent=2)

    print(f"[OK] 面诊同传Agent测试数据集已生成: {output_path}")
    print(f"   - 词汇翻译: {len(word_translation)}个")
    print(f"   - 上下文理解: {len(context_understanding)}个")
    print(f"   - 记忆调用: {len(memory_recall)}个")

    return consultation_dataset


# ============================================================================
# 3. 手语识别模块测试数据集生成
# ============================================================================

def generate_sign_recognition_test_info():
    """生成手语识别测试数据集信息文件"""

    # 读取标签映射
    label_path = os.path.join(
        os.path.dirname(OUTPUT_DIR),
        "robust_multistream_checkpoints",
        "label_mapping.json"
    )

    if not os.path.exists(label_path):
        print(f"[WARNING] 标签文件不存在: {label_path}")
        return None

    with open(label_path, "r", encoding="utf-8") as f:
        label_mapping = json.load(f)

    # 生成测试数据集信息
    sign_dataset_info = {
        "metadata": {
            "dataset_name": "手语识别测试数据集",
            "version": "1.0",
            "created_at": datetime.now().isoformat(),
            "total_classes": len(label_mapping),
            "total_samples": 13289,
            "split": {
                "train": 10631,
                "val": 1329,
                "test": 1329
            },
            "feature_dim": 258,
            "description": "基于MediaPipe提取的骨架关键点序列"
        },
        "label_mapping": label_mapping,
        "test_strategy": {
            "offline_evaluation": {
                "method": "批量推理",
                "metrics": ["accuracy", "top5_accuracy", "precision", "recall", "f1", "confusion_matrix"]
            },
            "online_evaluation": {
                "method": "API接口测试",
                "endpoint": "/api/sign/recognize",
                "metrics": ["success_rate", "avg_latency", "throughput"]
            },
            "robustness_evaluation": {
                "keypoint_occlusion": "随机遮挡10%关键点",
                "speed_variation": "0.5x, 1.0x, 1.5x速度",
                "cross_person": "未见过的手语者"
            }
        },
        "sample_test_cases": [
            {
                "label": "头（脑）",
                "class_id": 26,
                "description": "测试头部相关手语识别"
            },
            {
                "label": "痛",
                "class_id": 50,
                "description": "测试疼痛相关手语识别"
            },
            {
                "label": "医生（大夫）",
                "class_id": 36,
                "description": "测试医疗人员相关手语识别"
            }
        ]
    }

    # 保存数据集信息
    output_path = os.path.join(OUTPUT_DIR, "sign_recognition_test_info.json")
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(sign_dataset_info, f, ensure_ascii=False, indent=2)

    print(f"[OK] 手语识别测试数据集信息已生成: {output_path}")
    print(f"   - 类别数: {len(label_mapping)}")
    print(f"   - 总样本数: 13289")
    print(f"   - 测试集: 1329个样本")

    return sign_dataset_info


# ============================================================================
# 主函数
# ============================================================================

def main():
    """生成所有测试数据集"""
    print("=" * 70)
    print("开始生成测试数据集...")
    print("=" * 70)

    # 1. 生成智能分诊Agent测试数据集
    print("\n[1/3] 生成智能分诊Agent测试数据集...")
    triage_dataset = generate_triage_test_dataset()

    # 2. 生成面诊同传Agent测试数据集
    print("\n[2/3] 生成面诊同传Agent测试数据集...")
    consultation_dataset = generate_consultation_test_dataset()

    # 3. 生成手语识别测试数据集信息
    print("\n[3/3] 生成手语识别测试数据集信息...")
    sign_dataset_info = generate_sign_recognition_test_info()

    print("\n" + "=" * 70)
    print("[OK] 所有测试数据集生成完成!")
    print("=" * 70)
    print(f"\n生成的文件:")
    print(f"  1. {os.path.join(OUTPUT_DIR, 'triage_agent_test_dataset.json')}")
    print(f"  2. {os.path.join(OUTPUT_DIR, 'consultation_agent_test_dataset.json')}")
    print(f"  3. {os.path.join(OUTPUT_DIR, 'sign_recognition_test_info.json')}")
    print(f"\n总计测试样本: {200 + 150 + 1329} 个")


if __name__ == "__main__":
    main()
