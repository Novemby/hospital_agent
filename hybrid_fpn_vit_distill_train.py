"""
Hybrid sign-language training script for the medical sign dataset.

Design goals:
1. Reuse the last semester two-stream BiLSTM model as a legacy branch.
2. Add a more modern-looking Temporal FPN + ViT-style transformer branch.
3. Keep the original joint/bone preprocessing so the new model can plug into
   the existing data pipeline and checkpoints.
4. Avoid editing the original training scripts.
"""

import math
import os
import random
import time
from collections import Counter, defaultdict
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
from torch.nn.utils.rnn import pad_sequence
from torch.utils.data import DataLoader, Dataset


BASE_DIR = Path(__file__).resolve().parent


@dataclass
class TrainConfig:
    data_root: str = str(BASE_DIR / "Processed_Data_DualModel")
    teacher_checkpoint_candidates: Tuple[str, ...] = (
        str(BASE_DIR / "best_two_stream_model_full.pth"),
        str(BASE_DIR / "two_stream_checkpoints_622" / "best_two_stream_model_full.pth"),
        str(BASE_DIR / "two_stream_checkpoints" / "best_two_stream_model_full.pth"),
    )
    save_dir: str = str(BASE_DIR / "hybrid_fpnformer_checkpoints")
    batch_size: int = 24
    epochs: int = 35
    learning_rate: float = 3e-4
    min_learning_rate: float = 1e-6
    weight_decay: float = 1e-4
    train_ratio: float = 0.70
    val_ratio: float = 0.15
    test_ratio: float = 0.15
    seed: int = 42
    num_workers: int = 0
    use_amp: bool = True
    max_grad_norm: float = 1.0
    use_augmentation: bool = True
    freeze_teacher: bool = True
    input_dim_joint: int = 262
    input_dim_bone: int = 48
    legacy_hidden_dim: int = 64
    legacy_num_layers: int = 2
    embed_dim: int = 192
    transformer_depth: int = 3
    num_heads: int = 6
    dropout: float = 0.20
    distill_temperature: float = 2.5
    ce_weight: float = 1.0
    kd_weight: float = 0.35
    feature_weight: float = 0.15
    aux_weight: float = 0.20
    clip_features: float = 10.0


def set_seed(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)


def choose_device() -> torch.device:
    if torch.cuda.is_available():
        if hasattr(torch, "set_float32_matmul_precision"):
            torch.set_float32_matmul_precision("high")
        return torch.device("cuda")
    return torch.device("cpu")


def make_pad_mask(lengths: torch.Tensor, max_len: int, device: torch.device) -> torch.Tensor:
    lengths = lengths.to(device)
    steps = torch.arange(max_len, device=device).unsqueeze(0)
    return steps < lengths.unsqueeze(1)


def masked_mean(x: torch.Tensor, mask: torch.Tensor) -> torch.Tensor:
    weight = mask.unsqueeze(-1).float()
    denom = weight.sum(dim=1).clamp_min(1.0)
    return (x * weight).sum(dim=1) / denom


def stride2_lengths(lengths: torch.Tensor) -> torch.Tensor:
    return (lengths + 1) // 2


def find_existing_path(candidates: Sequence[str]) -> Optional[str]:
    for candidate in candidates:
        if candidate and os.path.exists(candidate):
            return candidate
    return None


def load_state_dict_from_checkpoint(path: str, device: torch.device) -> Dict[str, torch.Tensor]:
    checkpoint = torch.load(path, map_location=device)
    if isinstance(checkpoint, dict) and "model_state_dict" in checkpoint:
        return checkpoint["model_state_dict"]
    return checkpoint


def init_trunc_normal(tensor: torch.Tensor, std: float = 0.02) -> None:
    if hasattr(nn.init, "trunc_normal_"):
        nn.init.trunc_normal_(tensor, std=std)
    else:
        nn.init.normal_(tensor, std=std)


class DatasetIndex:
    def __init__(self, data_root: str):
        self.data_root = data_root
        self.data_paths: List[str] = []
        self.labels: List[int] = []
        self.label_map: Dict[str, int] = {}

        if not os.path.exists(data_root):
            raise FileNotFoundError("Data root not found: %s" % data_root)

        classes = sorted(
            entry
            for entry in os.listdir(data_root)
            if os.path.isdir(os.path.join(data_root, entry))
        )
        if not classes:
            raise RuntimeError("No class folders were found under %s" % data_root)

        self.label_map = {name: idx for idx, name in enumerate(classes)}
        for class_name in classes:
            class_dir = os.path.join(data_root, class_name)
            for file_name in sorted(os.listdir(class_dir)):
                if file_name.endswith(".npy"):
                    self.data_paths.append(os.path.join(class_dir, file_name))
                    self.labels.append(self.label_map[class_name])

        if not self.data_paths:
            raise RuntimeError("No .npy samples were found under %s" % data_root)


def stratified_split(
    paths: Sequence[str],
    labels: Sequence[int],
    train_ratio: float,
    val_ratio: float,
    seed: int,
) -> Tuple[List[int], List[int], List[int]]:
    rng = random.Random(seed)
    per_class_indices: Dict[int, List[int]] = defaultdict(list)
    for idx, label in enumerate(labels):
        per_class_indices[label].append(idx)

    train_idx: List[int] = []
    val_idx: List[int] = []
    test_idx: List[int] = []

    for _, indices in per_class_indices.items():
        rng.shuffle(indices)
        n = len(indices)

        train_count = max(1, int(round(n * train_ratio)))
        val_count = int(round(n * val_ratio))

        if n >= 3 and val_count == 0:
            val_count = 1

        if train_count + val_count >= n:
            val_count = max(0, n - train_count - 1)
        if train_count >= n:
            train_count = max(1, n - 1)

        test_count = n - train_count - val_count
        if test_count == 0 and n >= 3:
            if train_count > 1:
                train_count -= 1
            elif val_count > 1:
                val_count -= 1

        train_idx.extend(indices[:train_count])
        val_idx.extend(indices[train_count:train_count + val_count])
        test_idx.extend(indices[train_count + val_count:])

    rng.shuffle(train_idx)
    rng.shuffle(val_idx)
    rng.shuffle(test_idx)
    return train_idx, val_idx, test_idx


class KeypointSequenceAugmenter:
    def __init__(self) -> None:
        self.noise_scale = 0.01

    def __call__(self, features: np.ndarray) -> np.ndarray:
        augmented = features.astype(np.float32, copy=True)

        if len(augmented) > 8 and np.random.rand() < 0.35:
            scale = np.random.uniform(0.90, 1.10)
            new_len = max(8, int(round(len(augmented) * scale)))
            indices = np.linspace(0, len(augmented) - 1, new_len).astype(np.int64)
            augmented = augmented[indices]

        if np.random.rand() < 0.50:
            noise = np.random.normal(0.0, self.noise_scale, augmented.shape).astype(np.float32)
            augmented = augmented + noise

        if len(augmented) > 12 and np.random.rand() < 0.25:
            mask_len = max(1, int(len(augmented) * np.random.uniform(0.05, 0.12)))
            start = np.random.randint(0, len(augmented) - mask_len + 1)
            augmented[start:start + mask_len] = 0.0

        if len(augmented) > 8 and np.random.rand() < 0.25:
            shift = np.random.randint(-3, 4)
            if shift > 0:
                augmented = np.vstack([augmented[shift:], np.zeros((shift, augmented.shape[1]), dtype=np.float32)])
            elif shift < 0:
                augmented = np.vstack([np.zeros((-shift, augmented.shape[1]), dtype=np.float32), augmented[:shift]])

        return augmented


def build_joint_and_bone_features(raw_features: np.ndarray, clip_value: float) -> Tuple[np.ndarray, np.ndarray]:
    features = raw_features.astype(np.float32, copy=True)
    features = np.nan_to_num(features, nan=0.0, posinf=0.0, neginf=0.0)

    nose_xy = features[:, 0:2]
    leye_xy = features[:, 8:10]
    reye_xy = features[:, 20:22]
    mouth_xy = (features[:, 36:38] + features[:, 40:42]) / 2.0
    rh_index_xy = features[:, 219:221]

    l_shoulder_original = features[:, 44:46]
    r_shoulder_original = features[:, 48:50]
    shoulder_width_original = np.linalg.norm(
        l_shoulder_original - r_shoulder_original,
        axis=1,
        keepdims=True,
    ) + 1e-6

    dist_nose = np.linalg.norm(rh_index_xy - nose_xy, axis=1, keepdims=True) / shoulder_width_original
    dist_leye = np.linalg.norm(rh_index_xy - leye_xy, axis=1, keepdims=True) / shoulder_width_original
    dist_reye = np.linalg.norm(rh_index_xy - reye_xy, axis=1, keepdims=True) / shoulder_width_original
    dist_mouth = np.linalg.norm(rh_index_xy - mouth_xy, axis=1, keepdims=True) / shoulder_width_original

    l_shoulder_x = features[:, 44:45]
    l_shoulder_y = features[:, 45:46]
    r_shoulder_x = features[:, 48:49]
    r_shoulder_y = features[:, 49:50]
    body_center_x = (l_shoulder_x + r_shoulder_x) / 2.0
    body_center_y = (l_shoulder_y + r_shoulder_y) / 2.0
    shoulder_width = np.sqrt((r_shoulder_x - l_shoulder_x) ** 2 + (r_shoulder_y - l_shoulder_y) ** 2) + 1e-6

    features_normalized = features.copy()

    x_indices = np.arange(0, 132, 4)
    y_indices = np.arange(1, 132, 4)
    z_indices = np.arange(2, 132, 4)
    features_normalized[:, x_indices] = (features[:, x_indices] - body_center_x) / shoulder_width
    features_normalized[:, y_indices] = (features[:, y_indices] - body_center_y) / shoulder_width
    features_normalized[:, z_indices] = features[:, z_indices] / shoulder_width

    lhand_x_indices = np.arange(132, 195, 3)
    lhand_y_indices = np.arange(133, 195, 3)
    lhand_z_indices = np.arange(134, 195, 3)
    features_normalized[:, lhand_x_indices] = (features[:, lhand_x_indices] - body_center_x) / shoulder_width
    features_normalized[:, lhand_y_indices] = (features[:, lhand_y_indices] - body_center_y) / shoulder_width
    features_normalized[:, lhand_z_indices] = features[:, lhand_z_indices] / shoulder_width

    rhand_x_indices = np.arange(195, 258, 3)
    rhand_y_indices = np.arange(196, 258, 3)
    rhand_z_indices = np.arange(197, 258, 3)
    features_normalized[:, rhand_x_indices] = (features[:, rhand_x_indices] - body_center_x) / shoulder_width
    features_normalized[:, rhand_y_indices] = (features[:, rhand_y_indices] - body_center_y) / shoulder_width
    features_normalized[:, rhand_z_indices] = features[:, rhand_z_indices] / shoulder_width

    feat_joint = np.concatenate(
        [features_normalized, dist_nose, dist_leye, dist_reye, dist_mouth],
        axis=1,
    )

    nose = features_normalized[:, 0:3]
    l_shoulder = features_normalized[:, 44:47]
    r_shoulder = features_normalized[:, 48:51]
    l_elbow = features_normalized[:, 52:55]
    r_elbow = features_normalized[:, 56:59]
    l_wrist = features_normalized[:, 60:63]
    r_wrist = features_normalized[:, 64:67]

    lh_wrist = features_normalized[:, 132:135]
    rh_wrist = features_normalized[:, 195:198]
    lh_tips = [
        features_normalized[:, 132 + (i * 3):132 + (i * 3) + 3]
        for i in [4, 8, 12, 16, 20]
    ]
    rh_tips = [
        features_normalized[:, 195 + (i * 3):195 + (i * 3) + 3]
        for i in [4, 8, 12, 16, 20]
    ]

    bone_vectors = [
        l_shoulder - nose,
        r_shoulder - nose,
        l_elbow - l_shoulder,
        r_elbow - r_shoulder,
        l_wrist - l_elbow,
        r_wrist - r_elbow,
    ]
    bone_vectors.extend(tip - lh_wrist for tip in lh_tips)
    bone_vectors.extend(tip - rh_wrist for tip in rh_tips)
    feat_bone = np.concatenate(bone_vectors, axis=1)

    feat_joint = np.nan_to_num(feat_joint, nan=0.0, posinf=0.0, neginf=0.0)
    feat_bone = np.nan_to_num(feat_bone, nan=0.0, posinf=0.0, neginf=0.0)
    feat_joint = np.clip(feat_joint, -clip_value, clip_value).astype(np.float32)
    feat_bone = np.clip(feat_bone, -clip_value, clip_value).astype(np.float32)
    return feat_joint, feat_bone


class HybridSignDataset(Dataset):
    def __init__(
        self,
        paths: Sequence[str],
        labels: Sequence[int],
        clip_value: float,
        augment: bool = False,
    ) -> None:
        self.paths = list(paths)
        self.labels = list(labels)
        self.clip_value = clip_value
        self.augment = augment
        self.augmenter = KeypointSequenceAugmenter()

    def __len__(self) -> int:
        return len(self.paths)

    def __getitem__(self, idx: int) -> Tuple[torch.Tensor, torch.Tensor, int]:
        raw = np.load(self.paths[idx])
        if self.augment:
            raw = self.augmenter(raw)
        joint, bone = build_joint_and_bone_features(raw, self.clip_value)
        return (
            torch.tensor(joint, dtype=torch.float32),
            torch.tensor(bone, dtype=torch.float32),
            int(self.labels[idx]),
        )


def collate_fn(batch: Sequence[Tuple[torch.Tensor, torch.Tensor, int]]) -> Tuple[torch.Tensor, torch.Tensor, torch.Tensor, torch.Tensor]:
    batch = sorted(batch, key=lambda item: len(item[0]), reverse=True)
    seq_joint, seq_bone, labels = zip(*batch)
    lengths = torch.tensor([len(item) for item in seq_joint], dtype=torch.long)
    padded_joint = pad_sequence(seq_joint, batch_first=True, padding_value=0.0)
    padded_bone = pad_sequence(seq_bone, batch_first=True, padding_value=0.0)
    labels_tensor = torch.tensor(labels, dtype=torch.long)
    return padded_joint, padded_bone, labels_tensor, lengths


class LegacyAttention(nn.Module):
    def __init__(self, hidden_dim: int, dropout: float) -> None:
        super().__init__()
        self.attn = nn.Linear(hidden_dim * 2, 1)
        self.dropout = nn.Dropout(dropout)

    def forward(self, lstm_output: torch.Tensor) -> torch.Tensor:
        energy = self.dropout(self.attn(lstm_output))
        weights = F.softmax(energy, dim=1)
        return torch.sum(lstm_output * weights, dim=1)


class LegacyBiLSTMStream(nn.Module):
    def __init__(self, input_dim: int, hidden_dim: int, num_classes: int, num_layers: int, dropout: float) -> None:
        super().__init__()
        self.lstm = nn.LSTM(
            input_size=input_dim,
            hidden_size=hidden_dim,
            num_layers=num_layers,
            batch_first=True,
            bidirectional=True,
            dropout=dropout if num_layers > 1 else 0.0,
        )
        self.attention = LegacyAttention(hidden_dim, dropout)
        self.fc = nn.Linear(hidden_dim * 2, num_classes)

    def extract_feature(self, x: torch.Tensor, lengths: torch.Tensor) -> torch.Tensor:
        packed = nn.utils.rnn.pack_padded_sequence(
            x,
            lengths.cpu(),
            batch_first=True,
            enforce_sorted=False,
        )
        packed_out, _ = self.lstm(packed)
        lstm_out, _ = nn.utils.rnn.pad_packed_sequence(packed_out, batch_first=True)
        return self.attention(lstm_out)

    def forward(self, x: torch.Tensor, lengths: torch.Tensor) -> torch.Tensor:
        feature = self.extract_feature(x, lengths)
        return self.fc(feature)


class LegacyTwoStreamTeacher(nn.Module):
    def __init__(self, joint_dim: int, bone_dim: int, hidden_dim: int, num_classes: int, num_layers: int, dropout: float) -> None:
        super().__init__()
        self.stream_joint = LegacyBiLSTMStream(joint_dim, hidden_dim, num_classes, num_layers, dropout)
        self.stream_bone = LegacyBiLSTMStream(bone_dim, hidden_dim, num_classes, num_layers, dropout)
        self.fusion_fc = nn.Sequential(
            nn.Linear(hidden_dim * 4, hidden_dim * 2),
            nn.BatchNorm1d(hidden_dim * 2),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(hidden_dim * 2, hidden_dim),
            nn.BatchNorm1d(hidden_dim),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(hidden_dim, num_classes),
        )

    def forward_with_feature(self, x_joint: torch.Tensor, x_bone: torch.Tensor, lengths: torch.Tensor) -> Tuple[torch.Tensor, torch.Tensor]:
        joint_feature = self.stream_joint.extract_feature(x_joint, lengths)
        bone_feature = self.stream_bone.extract_feature(x_bone, lengths)
        combined = torch.cat([joint_feature, bone_feature], dim=1)

        hidden = combined
        layers = list(self.fusion_fc.children())
        for layer in layers[:-1]:
            hidden = layer(hidden)
        logits = layers[-1](hidden)
        return logits, hidden

    def forward(self, x_joint: torch.Tensor, x_bone: torch.Tensor, lengths: torch.Tensor) -> torch.Tensor:
        logits, _ = self.forward_with_feature(x_joint, x_bone, lengths)
        return logits


class SqueezeExcitation1D(nn.Module):
    def __init__(self, channels: int, reduction: int = 4) -> None:
        super().__init__()
        hidden = max(channels // reduction, 8)
        self.net = nn.Sequential(
            nn.AdaptiveAvgPool1d(1),
            nn.Conv1d(channels, hidden, kernel_size=1),
            nn.GELU(),
            nn.Conv1d(hidden, channels, kernel_size=1),
            nn.Sigmoid(),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return x * self.net(x)


class TemporalConvBlock(nn.Module):
    def __init__(self, channels: int, stride: int = 1, dropout: float = 0.1) -> None:
        super().__init__()
        self.depthwise = nn.Conv1d(
            channels,
            channels,
            kernel_size=3,
            stride=stride,
            padding=1,
            groups=channels,
            bias=False,
        )
        self.pointwise = nn.Conv1d(channels, channels, kernel_size=1, bias=False)
        self.norm = nn.BatchNorm1d(channels)
        self.act = nn.GELU()
        self.se = SqueezeExcitation1D(channels)
        self.dropout = nn.Dropout(dropout)

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        x = self.depthwise(x)
        x = self.pointwise(x)
        x = self.norm(x)
        x = self.act(x)
        x = self.se(x)
        return self.dropout(x)


class TemporalFPN(nn.Module):
    def __init__(self, channels: int, dropout: float) -> None:
        super().__init__()
        self.stem = TemporalConvBlock(channels, stride=1, dropout=dropout)
        self.down1 = TemporalConvBlock(channels, stride=2, dropout=dropout)
        self.down2 = TemporalConvBlock(channels, stride=2, dropout=dropout)
        self.smooth1 = TemporalConvBlock(channels, stride=1, dropout=dropout)
        self.smooth0 = TemporalConvBlock(channels, stride=1, dropout=dropout)

    def forward(self, x: torch.Tensor, lengths: torch.Tensor) -> Tuple[List[torch.Tensor], List[torch.Tensor]]:
        x = x.transpose(1, 2)
        c0 = self.stem(x)
        c1 = self.down1(c0)
        c2 = self.down2(c1)

        p2 = c2
        p1 = self.smooth1(c1 + F.interpolate(p2, size=c1.shape[-1], mode="linear", align_corners=False))
        p0 = self.smooth0(c0 + F.interpolate(p1, size=c0.shape[-1], mode="linear", align_corners=False))

        l0 = lengths
        l1 = stride2_lengths(lengths)
        l2 = stride2_lengths(l1)
        return [p0.transpose(1, 2), p1.transpose(1, 2), p2.transpose(1, 2)], [l0, l1, l2]


class CrossStreamGate(nn.Module):
    def __init__(self, dim: int, dropout: float) -> None:
        super().__init__()
        self.gate = nn.Sequential(
            nn.Linear(dim * 4, dim),
            nn.GELU(),
            nn.Dropout(dropout),
            nn.Linear(dim, dim),
            nn.Sigmoid(),
        )
        self.out = nn.Sequential(
            nn.Linear(dim * 2, dim),
            nn.LayerNorm(dim),
            nn.GELU(),
            nn.Dropout(dropout),
        )

    def forward(self, joint_tokens: torch.Tensor, bone_tokens: torch.Tensor) -> torch.Tensor:
        stats = torch.cat(
            [joint_tokens, bone_tokens, joint_tokens - bone_tokens, joint_tokens * bone_tokens],
            dim=-1,
        )
        gate = self.gate(stats)
        mixed = gate * joint_tokens + (1.0 - gate) * bone_tokens
        return self.out(torch.cat([mixed, joint_tokens - bone_tokens], dim=-1))


class MultiScaleTokenAssembler(nn.Module):
    def __init__(self, num_levels: int, dim: int) -> None:
        super().__init__()
        self.level_embeddings = nn.Parameter(torch.zeros(num_levels, 1, 1, dim))
        init_trunc_normal(self.level_embeddings, std=0.02)

    def forward(self, levels: Sequence[torch.Tensor], level_lengths: Sequence[torch.Tensor]) -> Tuple[torch.Tensor, torch.Tensor]:
        tokens: List[torch.Tensor] = []
        masks: List[torch.Tensor] = []
        device = levels[0].device
        for idx, (level, lengths) in enumerate(zip(levels, level_lengths)):
            tokens.append(level + self.level_embeddings[idx])
            masks.append(make_pad_mask(lengths, level.size(1), device))
        return torch.cat(tokens, dim=1), torch.cat(masks, dim=1)


def sinusoidal_position_encoding(length: int, dim: int, device: torch.device) -> torch.Tensor:
    position = torch.arange(length, device=device, dtype=torch.float32).unsqueeze(1)
    div_term = torch.exp(
        torch.arange(0, dim, 2, device=device, dtype=torch.float32)
        * (-math.log(10000.0) / dim)
    )
    pe = torch.zeros(length, dim, device=device, dtype=torch.float32)
    pe[:, 0::2] = torch.sin(position * div_term)
    pe[:, 1::2] = torch.cos(position * div_term)
    return pe.unsqueeze(0)


class AttentionPool(nn.Module):
    def __init__(self, dim: int) -> None:
        super().__init__()
        self.score = nn.Linear(dim, 1)

    def forward(self, x: torch.Tensor, mask: torch.Tensor) -> torch.Tensor:
        score = self.score(x).squeeze(-1)
        score = score.masked_fill(~mask, -1e4)
        attn = F.softmax(score, dim=-1)
        return torch.sum(x * attn.unsqueeze(-1), dim=1)


class TemporalTransformerEncoder(nn.Module):
    def __init__(self, dim: int, depth: int, num_heads: int, dropout: float) -> None:
        super().__init__()
        layer = nn.TransformerEncoderLayer(
            d_model=dim,
            nhead=num_heads,
            dim_feedforward=dim * 4,
            dropout=dropout,
            activation="gelu",
            batch_first=True,
            norm_first=True,
        )
        self.cls_token = nn.Parameter(torch.zeros(1, 1, dim))
        self.encoder = nn.TransformerEncoder(layer, num_layers=depth)
        self.norm = nn.LayerNorm(dim)
        self.pool = AttentionPool(dim)
        init_trunc_normal(self.cls_token, std=0.02)

    def forward(self, tokens: torch.Tensor, mask: torch.Tensor) -> Tuple[torch.Tensor, torch.Tensor]:
        batch_size = tokens.size(0)
        cls = self.cls_token.expand(batch_size, -1, -1)
        x = torch.cat([cls, tokens], dim=1)
        pos = sinusoidal_position_encoding(x.size(1), x.size(2), x.device)
        x = x + pos

        cls_mask = torch.ones(batch_size, 1, dtype=torch.bool, device=x.device)
        full_mask = torch.cat([cls_mask, mask], dim=1)
        encoded = self.encoder(x, src_key_padding_mask=~full_mask)
        encoded = self.norm(encoded)
        cls_feature = encoded[:, 0]
        pooled_feature = self.pool(encoded[:, 1:], mask)
        return cls_feature + pooled_feature, encoded[:, 1:]


class HybridFPNFormer(nn.Module):
    def __init__(self, num_classes: int, config: TrainConfig) -> None:
        super().__init__()
        self.config = config
        dim = config.embed_dim

        self.joint_input = nn.Sequential(
            nn.Linear(config.input_dim_joint, dim),
            nn.LayerNorm(dim),
            nn.GELU(),
            nn.Dropout(config.dropout),
        )
        self.bone_input = nn.Sequential(
            nn.Linear(config.input_dim_bone, dim),
            nn.LayerNorm(dim),
            nn.GELU(),
            nn.Dropout(config.dropout),
        )

        self.joint_fpn = TemporalFPN(dim, config.dropout)
        self.bone_fpn = TemporalFPN(dim, config.dropout)
        self.level_fusion = nn.ModuleList(
            [CrossStreamGate(dim, config.dropout) for _ in range(3)]
        )
        self.token_assembler = MultiScaleTokenAssembler(3, dim)
        self.temporal_encoder = TemporalTransformerEncoder(
            dim=dim,
            depth=config.transformer_depth,
            num_heads=config.num_heads,
            dropout=config.dropout,
        )

        self.joint_aux_head = nn.Linear(dim, num_classes)
        self.bone_aux_head = nn.Linear(dim, num_classes)
        self.student_aux_head = nn.Linear(dim, num_classes)

        self.legacy_teacher = LegacyTwoStreamTeacher(
            joint_dim=config.input_dim_joint,
            bone_dim=config.input_dim_bone,
            hidden_dim=config.legacy_hidden_dim,
            num_classes=num_classes,
            num_layers=config.legacy_num_layers,
            dropout=config.dropout,
        )
        self.teacher_loaded = False
        self.teacher_projector = nn.Sequential(
            nn.Linear(config.legacy_hidden_dim, dim),
            nn.LayerNorm(dim),
            nn.GELU(),
            nn.Dropout(config.dropout),
        )
        self.teacher_gate = nn.Sequential(
            nn.Linear(dim * 2, dim),
            nn.GELU(),
            nn.Dropout(config.dropout),
            nn.Linear(dim, dim),
            nn.Sigmoid(),
        )
        self.fusion_mlp = nn.Sequential(
            nn.Linear(dim * 3, dim * 2),
            nn.LayerNorm(dim * 2),
            nn.GELU(),
            nn.Dropout(config.dropout),
        )
        self.classifier = nn.Linear(dim * 2, num_classes)

    def load_teacher(self, candidates: Sequence[str], device: torch.device) -> Optional[str]:
        checkpoint_path = find_existing_path(candidates)
        if checkpoint_path is None:
            self.teacher_loaded = False
            return None

        state_dict = load_state_dict_from_checkpoint(checkpoint_path, device)
        current_state = self.legacy_teacher.state_dict()
        filtered_state = {
            key: value
            for key, value in state_dict.items()
            if key in current_state and current_state[key].shape == value.shape
        }
        skipped = len(state_dict) - len(filtered_state)

        missing, unexpected = self.legacy_teacher.load_state_dict(filtered_state, strict=False)
        if missing:
            print("Teacher missing keys:", len(missing))
        if unexpected:
            print("Teacher unexpected keys:", len(unexpected))
        if skipped:
            print("Teacher skipped keys due to shape/name mismatch:", skipped)

        self.teacher_loaded = True
        if self.config.freeze_teacher:
            for parameter in self.legacy_teacher.parameters():
                parameter.requires_grad = False
            self.legacy_teacher.eval()
        return checkpoint_path

    def forward(self, x_joint: torch.Tensor, x_bone: torch.Tensor, lengths: torch.Tensor) -> Dict[str, torch.Tensor]:
        joint_tokens = self.joint_input(x_joint)
        bone_tokens = self.bone_input(x_bone)

        joint_levels, joint_lengths = self.joint_fpn(joint_tokens, lengths)
        bone_levels, _ = self.bone_fpn(bone_tokens, lengths)
        fused_levels = [
            fusion(joint_level, bone_level)
            for fusion, joint_level, bone_level in zip(self.level_fusion, joint_levels, bone_levels)
        ]
        tokens, token_mask = self.token_assembler(fused_levels, joint_lengths)
        student_feature, _ = self.temporal_encoder(tokens, token_mask)

        level0_mask = make_pad_mask(lengths, joint_levels[0].size(1), joint_levels[0].device)
        joint_aux = self.joint_aux_head(masked_mean(joint_levels[0], level0_mask))
        bone_aux = self.bone_aux_head(masked_mean(bone_levels[0], level0_mask))
        student_aux = self.student_aux_head(student_feature)

        teacher_logits = None
        teacher_projected = torch.zeros_like(student_feature)

        if self.teacher_loaded:
            teacher_context = torch.no_grad() if self.config.freeze_teacher else nullcontext()
            with teacher_context:
                if self.config.freeze_teacher:
                    self.legacy_teacher.eval()
                teacher_logits, teacher_feature = self.legacy_teacher.forward_with_feature(
                    x_joint,
                    x_bone,
                    lengths,
                )
            teacher_projected = self.teacher_projector(teacher_feature)

        gate = self.teacher_gate(torch.cat([student_feature, teacher_projected], dim=-1))
        guided_feature = gate * student_feature + (1.0 - gate) * teacher_projected
        final_feature = self.fusion_mlp(
            torch.cat([student_feature, guided_feature, teacher_projected], dim=-1)
        )
        logits = self.classifier(final_feature)

        return {
            "logits": logits,
            "teacher_logits": teacher_logits,
            "student_alignment": student_feature,
            "teacher_alignment": teacher_projected if self.teacher_loaded else None,
            "aux_logits": [joint_aux, bone_aux, student_aux],
        }


class HybridDistillationLoss(nn.Module):
    def __init__(self, config: TrainConfig, class_weights: Optional[torch.Tensor] = None) -> None:
        super().__init__()
        self.config = config
        if class_weights is not None:
            self.register_buffer("class_weights", class_weights)
        else:
            self.class_weights = None

    def forward(self, outputs: Dict[str, torch.Tensor], targets: torch.Tensor) -> Tuple[torch.Tensor, Dict[str, float]]:
        weight = self.class_weights
        ce = F.cross_entropy(outputs["logits"], targets, weight=weight)
        total = self.config.ce_weight * ce
        metrics = {"ce": float(ce.detach().item())}

        teacher_logits = outputs.get("teacher_logits")
        if teacher_logits is not None:
            temperature = self.config.distill_temperature
            kd = F.kl_div(
                F.log_softmax(outputs["logits"] / temperature, dim=1),
                F.softmax(teacher_logits / temperature, dim=1),
                reduction="batchmean",
            ) * (temperature ** 2)
            total = total + self.config.kd_weight * kd
            metrics["kd"] = float(kd.detach().item())
        else:
            metrics["kd"] = 0.0

        teacher_alignment = outputs.get("teacher_alignment")
        if teacher_alignment is not None:
            feature_loss = F.smooth_l1_loss(outputs["student_alignment"], teacher_alignment)
            total = total + self.config.feature_weight * feature_loss
            metrics["feature"] = float(feature_loss.detach().item())
        else:
            metrics["feature"] = 0.0

        aux_logits = outputs.get("aux_logits", [])
        if aux_logits:
            aux_loss = torch.stack(
                [F.cross_entropy(logits, targets, weight=weight) for logits in aux_logits]
            ).mean()
            total = total + self.config.aux_weight * aux_loss
            metrics["aux"] = float(aux_loss.detach().item())
        else:
            metrics["aux"] = 0.0

        metrics["total"] = float(total.detach().item())
        return total, metrics


def build_class_weights(labels: Sequence[int], num_classes: int) -> torch.Tensor:
    counts = Counter(labels)
    weights = []
    for class_id in range(num_classes):
        count = counts.get(class_id, 1)
        weights.append(1.0 / math.sqrt(float(count)))
    tensor = torch.tensor(weights, dtype=torch.float32)
    return tensor / tensor.mean()


def run_epoch(
    model: HybridFPNFormer,
    loader: DataLoader,
    criterion: HybridDistillationLoss,
    optimizer: Optional[torch.optim.Optimizer],
    scaler: Optional[torch.cuda.amp.GradScaler],
    device: torch.device,
    use_amp: bool,
) -> Dict[str, float]:
    is_train = optimizer is not None
    model.train(is_train)
    if not is_train and model.teacher_loaded and model.config.freeze_teacher:
        model.legacy_teacher.eval()

    total_loss = 0.0
    total_ce = 0.0
    total_kd = 0.0
    total_feature = 0.0
    total_aux = 0.0
    total_correct = 0
    total_samples = 0
    total_batches = 0

    autocast_enabled = bool(use_amp and device.type == "cuda")

    for x_joint, x_bone, labels, lengths in loader:
        x_joint = x_joint.to(device)
        x_bone = x_bone.to(device)
        labels = labels.to(device)

        if is_train:
            optimizer.zero_grad(set_to_none=True)

        with torch.set_grad_enabled(is_train):
            with torch.cuda.amp.autocast(enabled=autocast_enabled):
                outputs = model(x_joint, x_bone, lengths)
                loss, stats = criterion(outputs, labels)

            if is_train:
                if scaler is not None and autocast_enabled:
                    scaler.scale(loss).backward()
                    scaler.unscale_(optimizer)
                    torch.nn.utils.clip_grad_norm_(model.parameters(), model.config.max_grad_norm)
                    scaler.step(optimizer)
                    scaler.update()
                else:
                    loss.backward()
                    torch.nn.utils.clip_grad_norm_(model.parameters(), model.config.max_grad_norm)
                    optimizer.step()

        logits = outputs["logits"].detach()
        predictions = logits.argmax(dim=1)
        total_correct += int((predictions == labels).sum().item())
        total_samples += int(labels.size(0))
        total_batches += 1
        total_loss += stats["total"]
        total_ce += stats["ce"]
        total_kd += stats["kd"]
        total_feature += stats["feature"]
        total_aux += stats["aux"]

    divisor = max(total_batches, 1)
    return {
        "loss": total_loss / divisor,
        "ce": total_ce / divisor,
        "kd": total_kd / divisor,
        "feature": total_feature / divisor,
        "aux": total_aux / divisor,
        "acc": 100.0 * total_correct / max(total_samples, 1),
    }


@torch.no_grad()
def evaluate_per_class(
    model: HybridFPNFormer,
    loader: DataLoader,
    label_map: Dict[str, int],
    device: torch.device,
) -> List[Tuple[int, str, float, int, int]]:
    model.eval()
    if model.teacher_loaded and model.config.freeze_teacher:
        model.legacy_teacher.eval()

    correct = defaultdict(int)
    total = defaultdict(int)
    inverse_label_map = {value: key for key, value in label_map.items()}

    for x_joint, x_bone, labels, lengths in loader:
        x_joint = x_joint.to(device)
        x_bone = x_bone.to(device)
        labels = labels.to(device)

        outputs = model(x_joint, x_bone, lengths)
        predictions = outputs["logits"].argmax(dim=1)

        for truth, pred in zip(labels.tolist(), predictions.tolist()):
            total[truth] += 1
            if truth == pred:
                correct[truth] += 1

    rows = []
    for class_id in sorted(total.keys()):
        class_total = total[class_id]
        class_correct = correct[class_id]
        accuracy = 100.0 * class_correct / max(class_total, 1)
        rows.append((class_id, inverse_label_map.get(class_id, str(class_id)), accuracy, class_correct, class_total))
    return rows


def save_checkpoint(
    path: str,
    model: HybridFPNFormer,
    optimizer: torch.optim.Optimizer,
    scheduler: torch.optim.lr_scheduler._LRScheduler,
    epoch: int,
    val_metrics: Dict[str, float],
    label_map: Dict[str, int],
    teacher_path: Optional[str],
    config: TrainConfig,
) -> None:
    checkpoint = {
        "epoch": epoch,
        "model_state_dict": model.state_dict(),
        "optimizer_state_dict": optimizer.state_dict(),
        "scheduler_state_dict": scheduler.state_dict(),
        "val_metrics": val_metrics,
        "label_map": label_map,
        "teacher_checkpoint": teacher_path,
        "config": vars(config),
    }
    torch.save(checkpoint, path)


def main() -> None:
    config = TrainConfig()
    os.makedirs(config.save_dir, exist_ok=True)
    set_seed(config.seed)
    device = choose_device()

    print("=" * 80)
    print("Hybrid FPN-Former Distillation Training")
    print("=" * 80)
    print("Device:", device)
    print("Data root:", config.data_root)

    dataset_index = DatasetIndex(config.data_root)
    num_classes = len(dataset_index.label_map)
    print("Classes:", num_classes)
    print("Samples:", len(dataset_index.data_paths))

    train_idx, val_idx, test_idx = stratified_split(
        dataset_index.data_paths,
        dataset_index.labels,
        config.train_ratio,
        config.val_ratio,
        config.seed,
    )

    train_paths = [dataset_index.data_paths[i] for i in train_idx]
    train_labels = [dataset_index.labels[i] for i in train_idx]
    val_paths = [dataset_index.data_paths[i] for i in val_idx]
    val_labels = [dataset_index.labels[i] for i in val_idx]
    test_paths = [dataset_index.data_paths[i] for i in test_idx]
    test_labels = [dataset_index.labels[i] for i in test_idx]

    train_dataset = HybridSignDataset(
        train_paths,
        train_labels,
        clip_value=config.clip_features,
        augment=config.use_augmentation,
    )
    val_dataset = HybridSignDataset(
        val_paths,
        val_labels,
        clip_value=config.clip_features,
        augment=False,
    )
    test_dataset = HybridSignDataset(
        test_paths,
        test_labels,
        clip_value=config.clip_features,
        augment=False,
    )

    train_loader = DataLoader(
        train_dataset,
        batch_size=config.batch_size,
        shuffle=True,
        num_workers=config.num_workers,
        collate_fn=collate_fn,
        pin_memory=(device.type == "cuda"),
    )
    val_loader = DataLoader(
        val_dataset,
        batch_size=config.batch_size,
        shuffle=False,
        num_workers=config.num_workers,
        collate_fn=collate_fn,
        pin_memory=(device.type == "cuda"),
    )
    test_loader = DataLoader(
        test_dataset,
        batch_size=config.batch_size,
        shuffle=False,
        num_workers=config.num_workers,
        collate_fn=collate_fn,
        pin_memory=(device.type == "cuda"),
    )

    print("Split sizes -> train: %d, val: %d, test: %d" % (len(train_dataset), len(val_dataset), len(test_dataset)))

    model = HybridFPNFormer(num_classes=num_classes, config=config).to(device)
    teacher_path = model.load_teacher(config.teacher_checkpoint_candidates, device)
    if teacher_path:
        print("Legacy teacher loaded from:", teacher_path)
    else:
        print("Legacy teacher checkpoint not found. Training will fall back to student-only mode.")

    class_weights = build_class_weights(train_labels, num_classes).to(device)
    criterion = HybridDistillationLoss(config, class_weights=class_weights)

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
    best_checkpoint_path = os.path.join(config.save_dir, "best_hybrid_fpnformer_distill.pth")
    start_time = time.time()

    for epoch in range(1, config.epochs + 1):
        epoch_start = time.time()
        train_metrics = run_epoch(
            model=model,
            loader=train_loader,
            criterion=criterion,
            optimizer=optimizer,
            scaler=scaler,
            device=device,
            use_amp=config.use_amp,
        )
        val_metrics = run_epoch(
            model=model,
            loader=val_loader,
            criterion=criterion,
            optimizer=None,
            scaler=None,
            device=device,
            use_amp=config.use_amp,
        )
        scheduler.step()

        elapsed = time.time() - epoch_start
        current_lr = optimizer.param_groups[0]["lr"]
        print(
            "Epoch %02d/%02d | %.1fs | lr %.6f | train loss %.4f acc %.2f%% | val loss %.4f acc %.2f%%"
            % (
                epoch,
                config.epochs,
                elapsed,
                current_lr,
                train_metrics["loss"],
                train_metrics["acc"],
                val_metrics["loss"],
                val_metrics["acc"],
            )
        )
        print(
            "  loss parts -> ce %.4f | kd %.4f | feat %.4f | aux %.4f"
            % (
                val_metrics["ce"],
                val_metrics["kd"],
                val_metrics["feature"],
                val_metrics["aux"],
            )
        )

        if val_metrics["acc"] >= best_val_acc:
            best_val_acc = val_metrics["acc"]
            save_checkpoint(
                best_checkpoint_path,
                model,
                optimizer,
                scheduler,
                epoch,
                val_metrics,
                dataset_index.label_map,
                teacher_path,
                config,
            )
            print("  Saved new best checkpoint.")

    total_minutes = (time.time() - start_time) / 60.0
    print("\nTraining finished in %.1f minutes" % total_minutes)

    if os.path.exists(best_checkpoint_path):
        best_state = torch.load(best_checkpoint_path, map_location=device)
        model.load_state_dict(best_state["model_state_dict"])

    test_metrics = run_epoch(
        model=model,
        loader=test_loader,
        criterion=criterion,
        optimizer=None,
        scaler=None,
        device=device,
        use_amp=config.use_amp,
    )
    print("\nFinal test accuracy: %.2f%% | test loss: %.4f" % (test_metrics["acc"], test_metrics["loss"]))

    per_class_rows = evaluate_per_class(model, test_loader, dataset_index.label_map, device)
    worst_rows = sorted(per_class_rows, key=lambda row: row[2])[:10]
    print("\nWorst 10 classes on the test split:")
    for class_id, class_name, accuracy, correct, total in worst_rows:
        print("  class %02d (%s): %.1f%% [%d/%d]" % (class_id, class_name, accuracy, correct, total))

    print("\nBest checkpoint:", best_checkpoint_path)


if __name__ == "__main__":
    main()
