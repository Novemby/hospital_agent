from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path
from typing import Dict, List, Sequence, Tuple

import numpy as np
import torch
from torch.utils.data import DataLoader


try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:
    pass


REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CODE_ROOT = Path(r"D:\a_shijiandaima")
DEFAULT_BASE_DATA_ROOT = Path(r"D:\a_shijian\Processed_Data_DualModel")
DEFAULT_NEW_DATA_ROOT = Path(r"D:\a_shijian\new_data")
DEFAULT_OUTPUT_ROOT = REPO_ROOT / "artifacts" / "newdata_73_pipeline"
DEFAULT_TEACHER_BASE_CKPT = DEFAULT_CODE_ROOT / "best_two_stream_model_full.pth"
DEFAULT_STUDENT_BASE_CKPT = DEFAULT_CODE_ROOT / "robust_multistream_checkpoints" / "best_robust_multistream_graformer.pth"
DEFAULT_LABEL_MAP_JSON = DEFAULT_CODE_ROOT / "label_map.json"
DEFAULT_LABEL_DISPLAY_JSON = DEFAULT_CODE_ROOT / "label_mapping.json"

NEW_CLASS_ALIASES = {
    "null": "无意义词",
    "左拍手（开始结束）": "开始结束",
}
NEW_CLASS_ORDER = ("无意义词", "不", "不发烧", "不咳嗽", "不头晕", "不恶心", "开始结束")

TWO_STREAM_CLASSIFIER_KEY_PARTS = (
    "stream_joint.fc",
    "stream_bone.fc",
    "fusion_fc.8",
)

ROBUST_CLASSIFIER_KEY_PARTS = (
    "joint_encoder.aux_head",
    "bone_encoder.aux_head",
    "velocity_encoder.aux_head",
    "relation_encoder.aux_head",
    "graph_encoder.aux_head",
    "legacy_teacher.stream_joint.fc",
    "legacy_teacher.stream_bone.fc",
    "legacy_teacher.fusion_fc.8",
    "classifier",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Train a 73-class pipeline with a 73-class teacher and 73-class five-stream student.")
    parser.add_argument("--code-root", default=str(DEFAULT_CODE_ROOT))
    parser.add_argument("--base-data-root", default=str(DEFAULT_BASE_DATA_ROOT))
    parser.add_argument("--new-data-root", default=str(DEFAULT_NEW_DATA_ROOT))
    parser.add_argument("--output-root", default=str(DEFAULT_OUTPUT_ROOT))
    parser.add_argument("--teacher-base-ckpt", default=str(DEFAULT_TEACHER_BASE_CKPT))
    parser.add_argument("--student-base-ckpt", default=str(DEFAULT_STUDENT_BASE_CKPT))
    parser.add_argument("--label-map-json", default=str(DEFAULT_LABEL_MAP_JSON))
    parser.add_argument("--label-display-json", default=str(DEFAULT_LABEL_DISPLAY_JSON))
    parser.add_argument("--teacher-epochs", type=int, default=3)
    parser.add_argument("--student-epochs", type=int, default=3)
    parser.add_argument("--teacher-batch-size", type=int, default=24)
    parser.add_argument("--student-batch-size", type=int, default=8)
    parser.add_argument("--teacher-lr", type=float, default=1e-4)
    parser.add_argument("--student-lr", type=float, default=8e-5)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--num-workers", type=int, default=0)
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--smoke", action="store_true")
    return parser.parse_args()


def ensure_imports(code_root: Path):
    code_root = code_root.resolve()
    if str(code_root) not in sys.path:
        sys.path.insert(0, str(code_root))

    import double_stream_train as two_stream  # type: ignore
    import robust_multistream_graformer_distill_train as robust  # type: ignore

    return two_stream, robust


def load_json(path: Path) -> Dict[str, object]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def save_json(path: Path, payload: Dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)


def old_ordered_classes(label_map_path: Path) -> List[str]:
    label_map = load_json(label_map_path)
    return [name for name, _ in sorted(label_map.items(), key=lambda item: item[1])]


def normalize_new_label(folder_name: str) -> str:
    return NEW_CLASS_ALIASES.get(folder_name, folder_name)


def collect_dataset(
    base_data_root: Path,
    new_data_root: Path,
    ordered_old_classes: Sequence[str],
) -> Tuple[List[str], Dict[str, List[Path]], Dict[str, str]]:
    ordered_labels = list(ordered_old_classes)
    class_to_files: Dict[str, List[Path]] = {}
    display_aliases: Dict[str, str] = {}

    for class_name in ordered_old_classes:
        class_to_files[class_name] = sorted((base_data_root / class_name).glob("*.npy"))

    normalized_to_files: Dict[str, List[Path]] = defaultdict(list)
    normalized_to_sources: Dict[str, List[str]] = defaultdict(list)
    new_entries = [entry for entry in sorted(new_data_root.iterdir()) if entry.is_dir()]
    for entry in new_entries:
        normalized = normalize_new_label(entry.name)
        normalized_to_files[normalized].extend(sorted(entry.glob("*.npy")))
        normalized_to_sources[normalized].append(entry.name)

    missing_classes = [class_name for class_name in NEW_CLASS_ORDER if class_name not in normalized_to_files]
    if missing_classes:
        raise RuntimeError(f"Missing expected new-data classes: {missing_classes}")

    for class_name in NEW_CLASS_ORDER:
        class_to_files[class_name] = sorted(normalized_to_files[class_name])
        ordered_labels.append(class_name)
        display_aliases[class_name] = " / ".join(normalized_to_sources[class_name])

    extra_classes = sorted(class_name for class_name in normalized_to_files if class_name not in NEW_CLASS_ORDER)
    for class_name in extra_classes:
        class_to_files[class_name] = sorted(normalized_to_files[class_name])
        ordered_labels.append(class_name)
        display_aliases[class_name] = " / ".join(normalized_to_sources[class_name])

    return ordered_labels, class_to_files, display_aliases


def validate_class_files(class_to_files: Dict[str, List[Path]]) -> Dict[str, object]:
    summary: Dict[str, object] = {"classes": {}, "bad_files": [], "total_samples": 0}
    total_samples = 0
    bad_files: List[Dict[str, object]] = []

    for class_name, files in class_to_files.items():
        frame_lengths: List[int] = []
        dtypes = Counter()
        bad_count = 0

        for path in files:
            try:
                array = np.load(path, mmap_mode="r")
                if array.ndim != 2 or array.shape[1] != 258:
                    bad_files.append(
                        {
                            "class": class_name,
                            "path": str(path),
                            "shape": list(array.shape),
                            "dtype": str(array.dtype),
                        }
                    )
                    bad_count += 1
                    continue
                frame_lengths.append(int(array.shape[0]))
                dtypes[str(array.dtype)] += 1
                total_samples += 1
            except Exception as exc:
                bad_files.append({"class": class_name, "path": str(path), "error": str(exc)})
                bad_count += 1

        summary["classes"][class_name] = {
            "samples": len(files),
            "valid_samples": len(files) - bad_count,
            "bad_samples": bad_count,
            "min_frames": min(frame_lengths) if frame_lengths else 0,
            "max_frames": max(frame_lengths) if frame_lengths else 0,
            "avg_frames": round(sum(frame_lengths) / max(len(frame_lengths), 1), 2) if frame_lengths else 0.0,
            "dtypes": dict(sorted(dtypes.items())),
        }

    summary["bad_files"] = bad_files
    summary["total_samples"] = total_samples
    return summary


def build_label_map_and_entries(
    ordered_labels: Sequence[str],
    class_to_files: Dict[str, List[Path]],
) -> Tuple[Dict[str, int], List[str], List[int]]:
    label_map = {name: idx for idx, name in enumerate(ordered_labels)}
    data_paths: List[str] = []
    labels: List[int] = []

    for class_name in ordered_labels:
        for path in class_to_files[class_name]:
            data_paths.append(str(path))
            labels.append(label_map[class_name])

    return label_map, data_paths, labels


def build_display_map(
    ordered_labels: Sequence[str],
    display_json_path: Path,
) -> Dict[str, str]:
    base_display = load_json(display_json_path)
    by_index = {int(idx): label for label, idx in base_display.items()}

    display_map: Dict[str, str] = {}
    for idx, model_label in enumerate(ordered_labels):
        display_map[model_label] = by_index.get(idx, model_label)
    return display_map


def is_matching_classifier_key(key: str, key_parts: Sequence[str]) -> bool:
    return any(part in key for part in key_parts)


def transplant_state_dict(
    model: torch.nn.Module,
    checkpoint_state: Dict[str, torch.Tensor],
    old_label_map: Dict[str, int],
    new_label_map: Dict[str, int],
    classifier_key_parts: Sequence[str],
) -> Dict[str, object]:
    current_state = model.state_dict()
    copied_exact = 0
    copied_rows: List[Dict[str, object]] = []

    for key, value in checkpoint_state.items():
        if key not in current_state:
            continue

        target = current_state[key]
        if target.shape == value.shape:
            current_state[key] = value
            copied_exact += 1
            continue

        if not is_matching_classifier_key(key, classifier_key_parts):
            continue

        if target.ndim != value.ndim or target.shape[1:] != value.shape[1:]:
            continue

        expanded = target.clone()
        copied = 0
        for class_name, old_idx in old_label_map.items():
            if class_name not in new_label_map or old_idx >= value.shape[0]:
                continue
            new_idx = new_label_map[class_name]
            expanded[new_idx] = value[old_idx]
            copied += 1

        if copied:
            current_state[key] = expanded
            copied_rows.append({"key": key, "rows_copied": copied})

    model.load_state_dict(current_state)
    return {"exact_keys": copied_exact, "row_expanded_keys": copied_rows}


def save_mapping_json(
    out_path: Path,
    label_map: Dict[str, int],
    display_map: Dict[str, str],
    checkpoint_path: Path,
) -> None:
    by_index = {}
    for model_label, idx in sorted(label_map.items(), key=lambda item: item[1]):
        if idx < 66:
            source = "base_66"
        elif model_label == "无意义词":
            source = "null_class"
        else:
            source = "new_data"
        by_index[str(idx)] = {
            "index": idx,
            "model_label": model_label,
            "display_name": display_map.get(model_label, model_label),
            "source": source,
        }

    payload = {
        "num_classes": len(label_map),
        "checkpoint": str(checkpoint_path),
        "by_index": by_index,
        "model_label_to_index": label_map,
        "display_name_to_index": {display_map[k]: v for k, v in label_map.items()},
    }
    save_json(out_path, payload)


def build_summary_payload(
    ordered_labels: Sequence[str],
    label_map: Dict[str, int],
    display_map: Dict[str, str],
    validation: Dict[str, object],
    source_aliases: Dict[str, str],
) -> Dict[str, object]:
    return {
        "ordered_labels": list(ordered_labels),
        "label_map": label_map,
        "display_map": display_map,
        "new_data_sources": source_aliases,
        "validation": validation,
    }


def teacher_loader_kwargs(two_stream, device: torch.device, batch_size: int, num_workers: int) -> Dict[str, object]:
    return {
        "batch_size": batch_size,
        "collate_fn": two_stream.collate_fn,
        "num_workers": num_workers,
        "pin_memory": (device.type == "cuda"),
    }


def get_teacher_focus_classes(label_map: Dict[str, int]) -> List[int]:
    focus_names = [name for name in NEW_CLASS_ORDER if name != "无意义词" and name in label_map]
    return [label_map[name] for name in focus_names]


def train_teacher(
    two_stream,
    data_paths: Sequence[str],
    labels: Sequence[int],
    label_map: Dict[str, int],
    output_root: Path,
    base_checkpoint_path: Path,
    base_label_map_path: Path,
    seed: int,
    epochs: int,
    batch_size: int,
    learning_rate: float,
    num_workers: int,
) -> Path:
    two_stream.np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)

    device = torch.device("cuda" if torch.cuda.is_available() else "cpu")
    print("Teacher device:", device)
    focus_class_ids = get_teacher_focus_classes(label_map)
    inverse_label_map = {value: key for key, value in label_map.items()}
    focus_class_names = [inverse_label_map[class_id] for class_id in focus_class_ids]
    two_stream.LOW_ACCURACY_CLASSES = focus_class_ids
    print("Teacher focus classes:", focus_class_names)

    train_paths, train_labels, val_paths, val_labels, test_paths, test_labels = two_stream.stratified_split(
        list(data_paths),
        list(labels),
        train_ratio=two_stream.TRAIN_RATIO,
        val_ratio=two_stream.VAL_RATIO,
        test_ratio=two_stream.TEST_RATIO,
        seed=seed,
    )

    train_dataset = two_stream.MedicalSignDataset(
        train_paths,
        train_labels,
        label_map,
        augment=two_stream.USE_DATA_AUGMENTATION,
        oversample_classes=focus_class_ids,
        oversample_factor=2,
    )
    val_dataset = two_stream.MedicalSignDataset(
        val_paths,
        val_labels,
        label_map,
        augment=False,
        oversample_classes=None,
        oversample_factor=1,
    )
    test_dataset = two_stream.MedicalSignDataset(
        test_paths,
        test_labels,
        label_map,
        augment=False,
        oversample_classes=None,
        oversample_factor=1,
    )

    loader_kwargs = teacher_loader_kwargs(two_stream, device, batch_size, num_workers)
    train_loader = DataLoader(train_dataset, shuffle=True, **loader_kwargs)
    val_loader = DataLoader(val_dataset, shuffle=False, **loader_kwargs)
    test_loader = DataLoader(test_dataset, shuffle=False, **loader_kwargs)

    model = two_stream.TwoStreamNetwork(
        two_stream.INPUT_DIM_JOINT,
        two_stream.INPUT_DIM_BONE,
        two_stream.HIDDEN_DIM,
        len(label_map),
    ).to(device)

    checkpoint = torch.load(base_checkpoint_path, map_location="cpu")
    base_state = checkpoint["model_state_dict"] if "model_state_dict" in checkpoint else checkpoint
    base_label_map = checkpoint.get("label_map", load_json(base_label_map_path))
    transfer_report = transplant_state_dict(
        model,
        base_state,
        base_label_map,
        label_map,
        TWO_STREAM_CLASSIFIER_KEY_PARTS,
    )
    save_json(output_root / "teacher_transfer_report.json", transfer_report)

    class_counts = Counter(train_dataset.labels)
    class_weights = two_stream.compute_improved_class_weights(
        class_counts,
        len(label_map),
        low_acc_classes=focus_class_ids,
        boost_factor=1.5,
    ).to(device)
    criterion = two_stream.AdaptiveFocalLoss(
        alpha=class_weights,
        gamma_default=2,
        gamma_hard=getattr(two_stream, "FOCAL_GAMMA_LOW_ACC", 3),
        hard_classes=focus_class_ids,
    )

    optimizer = torch.optim.AdamW(
        filter(lambda parameter: parameter.requires_grad, model.parameters()),
        lr=learning_rate,
        weight_decay=two_stream.WEIGHT_DECAY,
        betas=(0.9, 0.999),
    )
    scheduler = torch.optim.lr_scheduler.ReduceLROnPlateau(
        optimizer,
        mode="max",
        factor=0.5,
        patience=4,
        min_lr=two_stream.MIN_LR,
    )

    best_val_acc = -1.0
    best_path = output_root / "teacher_73_best.pth"
    for epoch in range(1, epochs + 1):
        model.train()
        train_loss = 0.0
        train_total = 0
        train_correct = 0
        train_batches = 0

        for x_joint, x_bone, batch_labels, lengths in train_loader:
            x_joint = x_joint.to(device)
            x_bone = x_bone.to(device)
            batch_labels = batch_labels.to(device)

            optimizer.zero_grad(set_to_none=True)
            outputs = model(x_joint, x_bone, lengths)
            loss = criterion(outputs, batch_labels)
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), two_stream.GRAD_CLIP_MAX_NORM)
            optimizer.step()

            train_loss += float(loss.item())
            predictions = outputs.argmax(dim=1)
            train_total += int(batch_labels.size(0))
            train_correct += int((predictions == batch_labels).sum().item())
            train_batches += 1

        model.eval()
        val_loss = 0.0
        val_total = 0
        val_correct = 0
        val_batches = 0
        with torch.no_grad():
            for x_joint, x_bone, batch_labels, lengths in val_loader:
                x_joint = x_joint.to(device)
                x_bone = x_bone.to(device)
                batch_labels = batch_labels.to(device)
                outputs = model(x_joint, x_bone, lengths)
                loss = criterion(outputs, batch_labels)
                val_loss += float(loss.item())
                predictions = outputs.argmax(dim=1)
                val_total += int(batch_labels.size(0))
                val_correct += int((predictions == batch_labels).sum().item())
                val_batches += 1

        train_acc = 100.0 * train_correct / max(train_total, 1)
        val_acc = 100.0 * val_correct / max(val_total, 1)
        scheduler.step(val_acc)
        print(
            "Teacher epoch %02d/%02d | train loss %.4f acc %.2f%% | val loss %.4f acc %.2f%%"
            % (
                epoch,
                epochs,
                train_loss / max(train_batches, 1),
                train_acc,
                val_loss / max(val_batches, 1),
                val_acc,
            )
        )

        if val_acc >= best_val_acc:
            best_val_acc = val_acc
            torch.save(
                {
                    "epoch": epoch,
                    "model_state_dict": model.state_dict(),
                    "optimizer_state_dict": optimizer.state_dict(),
                    "val_acc": val_acc,
                    "val_loss": val_loss / max(val_batches, 1),
                    "num_classes": len(label_map),
                    "label_map": label_map,
                },
                best_path,
            )

    if best_path.exists():
        best_checkpoint = torch.load(best_path, map_location=device)
        model.load_state_dict(best_checkpoint["model_state_dict"])

    model.eval()
    test_total = 0
    test_correct = 0
    rows: List[Dict[str, object]] = []
    class_correct = Counter()
    class_total = Counter()
    with torch.no_grad():
        for x_joint, x_bone, batch_labels, lengths in test_loader:
            x_joint = x_joint.to(device)
            x_bone = x_bone.to(device)
            batch_labels = batch_labels.to(device)
            outputs = model(x_joint, x_bone, lengths)
            predictions = outputs.argmax(dim=1)
            test_total += int(batch_labels.size(0))
            test_correct += int((predictions == batch_labels).sum().item())
            for truth, pred in zip(batch_labels.tolist(), predictions.tolist()):
                class_total[truth] += 1
                if truth == pred:
                    class_correct[truth] += 1

    inverse_label_map = {value: key for key, value in label_map.items()}
    for class_id in sorted(class_total.keys()):
        total = class_total[class_id]
        correct = class_correct[class_id]
        rows.append(
            {
                "class_id": class_id,
                "class_name": inverse_label_map[class_id],
                "accuracy": 100.0 * correct / max(total, 1),
                "correct": correct,
                "total": total,
            }
        )

    save_json(
        output_root / "teacher_final_metrics.json",
        {
            "test_accuracy": 100.0 * test_correct / max(test_total, 1),
            "focus_classes": focus_class_names,
            "all_classes": rows,
        },
    )

    return best_path


def train_student(
    robust,
    data_paths: Sequence[str],
    labels: Sequence[int],
    label_map: Dict[str, int],
    output_root: Path,
    base_checkpoint_path: Path,
    base_label_map_path: Path,
    teacher_checkpoint_path: Path,
    seed: int,
    epochs: int,
    batch_size: int,
    learning_rate: float,
    num_workers: int,
) -> Path:
    robust.set_seed(seed)
    device = robust.choose_device()
    print("Student device:", device)

    config = robust.RobustTrainConfig(
        data_root=str(DEFAULT_BASE_DATA_ROOT),
        teacher_checkpoint_candidates=(str(teacher_checkpoint_path),),
        save_dir=str(output_root / "checkpoints"),
        log_dir=str(output_root / "logs"),
        batch_size=batch_size,
        epochs=epochs,
        learning_rate=learning_rate,
        seed=seed,
        num_workers=num_workers,
    )
    Path(config.save_dir).mkdir(parents=True, exist_ok=True)
    Path(config.log_dir).mkdir(parents=True, exist_ok=True)

    train_idx, val_idx, test_idx = robust.stratified_split(
        list(data_paths),
        list(labels),
        config.train_ratio,
        config.val_ratio,
        config.seed,
    )
    train_paths = [data_paths[i] for i in train_idx]
    train_labels = [labels[i] for i in train_idx]
    val_paths = [data_paths[i] for i in val_idx]
    val_labels = [labels[i] for i in val_idx]
    test_paths = [data_paths[i] for i in test_idx]
    test_labels = [labels[i] for i in test_idx]

    train_dataset = robust.RobustSignDataset(train_paths, train_labels, config, augment=True)
    val_dataset = robust.RobustSignDataset(val_paths, val_labels, config, augment=False)
    test_dataset = robust.RobustSignDataset(test_paths, test_labels, config, augment=False)

    loader_kwargs = {
        "batch_size": config.batch_size,
        "num_workers": config.num_workers,
        "collate_fn": robust.robust_collate_fn,
        "pin_memory": (device.type == "cuda"),
    }
    train_loader = DataLoader(train_dataset, shuffle=True, **loader_kwargs)
    val_loader = DataLoader(val_dataset, shuffle=False, **loader_kwargs)
    test_loader = DataLoader(test_dataset, shuffle=False, **loader_kwargs)

    model = robust.RobustMultiStreamModel(num_classes=len(label_map), config=config).to(device)
    checkpoint = torch.load(base_checkpoint_path, map_location="cpu")
    base_state = checkpoint["model_state_dict"] if "model_state_dict" in checkpoint else checkpoint
    base_label_map = checkpoint.get("label_map", load_json(base_label_map_path))
    transfer_report = transplant_state_dict(
        model,
        base_state,
        base_label_map,
        label_map,
        ROBUST_CLASSIFIER_KEY_PARTS,
    )
    save_json(output_root / "student_transfer_report.json", transfer_report)

    teacher_path = model.load_teacher(config.teacher_checkpoint_candidates, device)
    if not teacher_path:
        raise RuntimeError("73-class teacher checkpoint failed to load.")

    class_weights = robust.build_class_weights(train_labels, len(label_map)).to(device)
    criterion = robust.RobustDistillationLoss(config, class_weights=class_weights)
    optimizer = torch.optim.AdamW(
        filter(lambda parameter: parameter.requires_grad, model.parameters()),
        lr=config.learning_rate,
        weight_decay=config.weight_decay,
    )
    scheduler = torch.optim.lr_scheduler.CosineAnnealingLR(
        optimizer,
        T_max=config.epochs,
        eta_min=config.min_learning_rate,
    )
    scaler = torch.cuda.amp.GradScaler(enabled=bool(config.use_amp and device.type == "cuda"))

    best_val_acc = -1.0
    best_path = Path(config.save_dir) / "best_robust_multistream_newdata73.pth"
    for epoch in range(1, config.epochs + 1):
        train_metrics = robust.run_epoch(
            model=model,
            loader=train_loader,
            criterion=criterion,
            optimizer=optimizer,
            scaler=scaler,
            device=device,
            use_amp=config.use_amp,
        )
        val_metrics = robust.run_epoch(
            model=model,
            loader=val_loader,
            criterion=criterion,
            optimizer=None,
            scaler=None,
            device=device,
            use_amp=config.use_amp,
        )
        scheduler.step()
        print(
            "Student epoch %02d/%02d | train loss %.4f acc %.2f%% | val loss %.4f acc %.2f%%"
            % (
                epoch,
                config.epochs,
                train_metrics["total"],
                train_metrics["acc"],
                val_metrics["total"],
                val_metrics["acc"],
            )
        )

        if val_metrics["acc"] >= best_val_acc:
            best_val_acc = val_metrics["acc"]
            robust.save_checkpoint(
                str(best_path),
                model,
                optimizer,
                scheduler,
                epoch,
                val_metrics,
                label_map,
                teacher_path,
                config,
            )

    if best_path.exists():
        best_checkpoint = torch.load(best_path, map_location=device)
        model.load_state_dict(best_checkpoint["model_state_dict"])

    test_metrics = robust.run_epoch(
        model=model,
        loader=test_loader,
        criterion=criterion,
        optimizer=None,
        scaler=None,
        device=device,
        use_amp=config.use_amp,
    )
    per_class_rows = robust.evaluate_per_class(model, test_loader, label_map, device)
    save_json(
        output_root / "student_final_metrics.json",
        {
            "test_metrics": test_metrics,
            "all_classes": [
                {
                    "class_id": class_id,
                    "class_name": class_name,
                    "accuracy": accuracy,
                    "correct": correct,
                    "total": total,
                }
                for class_id, class_name, accuracy, correct, total in per_class_rows
            ],
        },
    )

    return best_path


def main() -> None:
    args = parse_args()
    if args.smoke:
        args.teacher_epochs = 1
        args.student_epochs = 1

    code_root = Path(args.code_root)
    base_data_root = Path(args.base_data_root)
    new_data_root = Path(args.new_data_root)
    output_root = Path(args.output_root)
    teacher_base_ckpt = Path(args.teacher_base_ckpt)
    student_base_ckpt = Path(args.student_base_ckpt)
    label_map_json = Path(args.label_map_json)
    label_display_json = Path(args.label_display_json)
    output_root.mkdir(parents=True, exist_ok=True)

    two_stream, robust = ensure_imports(code_root)
    ordered_labels, class_to_files, source_aliases = collect_dataset(
        base_data_root,
        new_data_root,
        old_ordered_classes(label_map_json),
    )
    validation = validate_class_files(class_to_files)
    if validation["bad_files"]:
        raise RuntimeError("Found invalid .npy files. See dataset_summary.json for details.")

    label_map, data_paths, labels = build_label_map_and_entries(ordered_labels, class_to_files)
    display_map = build_display_map(ordered_labels, label_display_json)
    save_json(output_root / "dataset_summary.json", build_summary_payload(ordered_labels, label_map, display_map, validation, source_aliases))

    print("=" * 80)
    print("New Data 73-Class Pipeline")
    print("=" * 80)
    print("Base data root:", base_data_root)
    print("New data root:", new_data_root)
    print("Output root:", output_root)
    print("Classes:", len(label_map))
    print("Samples:", len(data_paths))

    if args.validate_only:
        return

    teacher_dir = output_root / "teacher"
    student_dir = output_root / "student"
    teacher_dir.mkdir(parents=True, exist_ok=True)
    student_dir.mkdir(parents=True, exist_ok=True)

    teacher_checkpoint = train_teacher(
        two_stream=two_stream,
        data_paths=data_paths,
        labels=labels,
        label_map=label_map,
        output_root=teacher_dir,
        base_checkpoint_path=teacher_base_ckpt,
        base_label_map_path=label_map_json,
        seed=args.seed,
        epochs=args.teacher_epochs,
        batch_size=args.teacher_batch_size,
        learning_rate=args.teacher_lr,
        num_workers=args.num_workers,
    )

    student_checkpoint = train_student(
        robust=robust,
        data_paths=data_paths,
        labels=labels,
        label_map=label_map,
        output_root=student_dir,
        base_checkpoint_path=student_base_ckpt,
        base_label_map_path=label_map_json,
        teacher_checkpoint_path=teacher_checkpoint,
        seed=args.seed,
        epochs=args.student_epochs,
        batch_size=args.student_batch_size,
        learning_rate=args.student_lr,
        num_workers=args.num_workers,
    )

    save_mapping_json(output_root / "sign_label_mapping_73.json", label_map, display_map, student_checkpoint)
    print("Teacher checkpoint:", teacher_checkpoint)
    print("Student checkpoint:", student_checkpoint)


if __name__ == "__main__":
    main()
