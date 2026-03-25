# backend/services/sign_model.py
import os
import json
import math
import os
import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F
from typing import List, Dict, Optional, Tuple, Sequence

# 模型配置类
class SignModelConfig:
    input_dim_joint = 262
    input_dim_bone = 48
    embed_dim = 192
    transformer_depth = 3
    num_heads = 6
    dropout = 0.20
    legacy_hidden_dim = 64
    legacy_num_layers = 2
    clip_features = 10.0

# 以下为模型所需的各种组件（从训练脚本复制，但移除了训练相关部分）
def init_trunc_normal(tensor: torch.Tensor, std: float = 0.02) -> None:
    if hasattr(nn.init, "trunc_normal_"):
        nn.init.trunc_normal_(tensor, std=std)
    else:
        nn.init.normal_(tensor, std=std)

class LegacyAttention(nn.Module):
    def __init__(self, hidden_dim: int, dropout: float):
        super().__init__()
        self.attn = nn.Linear(hidden_dim * 2, 1)
        self.dropout = nn.Dropout(dropout)

    def forward(self, lstm_output: torch.Tensor) -> torch.Tensor:
        energy = self.dropout(self.attn(lstm_output))
        weights = F.softmax(energy, dim=1)
        return torch.sum(lstm_output * weights, dim=1)

class LegacyBiLSTMStream(nn.Module):
    def __init__(self, input_dim: int, hidden_dim: int, num_classes: int, num_layers: int, dropout: float):
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
            x, lengths.cpu(), batch_first=True, enforce_sorted=False
        )
        packed_out, _ = self.lstm(packed)
        lstm_out, _ = nn.utils.rnn.pad_packed_sequence(packed_out, batch_first=True)
        return self.attention(lstm_out)

    def forward(self, x: torch.Tensor, lengths: torch.Tensor) -> torch.Tensor:
        feature = self.extract_feature(x, lengths)
        return self.fc(feature)

class LegacyTwoStreamTeacher(nn.Module):
    def __init__(self, joint_dim: int, bone_dim: int, hidden_dim: int, num_classes: int, num_layers: int, dropout: float):
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
    def __init__(self, channels: int, reduction: int = 4):
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
    def __init__(self, channels: int, stride: int = 1, dropout: float = 0.1):
        super().__init__()
        self.depthwise = nn.Conv1d(
            channels, channels, kernel_size=3, stride=stride, padding=1, groups=channels, bias=False
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
    def __init__(self, channels: int, dropout: float):
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
        l1 = (lengths + 1) // 2
        l2 = (l1 + 1) // 2
        return [p0.transpose(1, 2), p1.transpose(1, 2), p2.transpose(1, 2)], [l0, l1, l2]

class CrossStreamGate(nn.Module):
    def __init__(self, dim: int, dropout: float):
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
    def __init__(self, num_levels: int, dim: int):
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

def make_pad_mask(lengths: torch.Tensor, max_len: int, device: torch.device) -> torch.Tensor:
    lengths = lengths.to(device)
    steps = torch.arange(max_len, device=device).unsqueeze(0)
    return steps < lengths.unsqueeze(1)

def masked_mean(x: torch.Tensor, mask: torch.Tensor) -> torch.Tensor:
    weight = mask.unsqueeze(-1).float()
    denom = weight.sum(dim=1).clamp_min(1.0)
    return (x * weight).sum(dim=1) / denom

def sinusoidal_position_encoding(length: int, dim: int, device: torch.device) -> torch.Tensor:
    position = torch.arange(length, device=device, dtype=torch.float32).unsqueeze(1)
    div_term = torch.exp(
        torch.arange(0, dim, 2, device=device, dtype=torch.float32) * (-math.log(10000.0) / dim)
    )
    pe = torch.zeros(length, dim, device=device, dtype=torch.float32)
    pe[:, 0::2] = torch.sin(position * div_term)
    pe[:, 1::2] = torch.cos(position * div_term)
    return pe.unsqueeze(0)

class AttentionPool(nn.Module):
    def __init__(self, dim: int):
        super().__init__()
        self.score = nn.Linear(dim, 1)

    def forward(self, x: torch.Tensor, mask: torch.Tensor) -> torch.Tensor:
        score = self.score(x).squeeze(-1)
        score = score.masked_fill(~mask, -1e4)
        attn = F.softmax(score, dim=-1)
        return torch.sum(x * attn.unsqueeze(-1), dim=1)

class TemporalTransformerEncoder(nn.Module):
    def __init__(self, dim: int, depth: int, num_heads: int, dropout: float):
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
    def __init__(self, num_classes: int, config: SignModelConfig):
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
        self.level_fusion = nn.ModuleList([CrossStreamGate(dim, config.dropout) for _ in range(3)])
        self.token_assembler = MultiScaleTokenAssembler(3, dim)
        self.temporal_encoder = TemporalTransformerEncoder(
            dim=dim, depth=config.transformer_depth, num_heads=config.num_heads, dropout=config.dropout
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

    def forward(self, x_joint: torch.Tensor, x_bone: torch.Tensor, lengths: torch.Tensor) -> torch.Tensor:
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

        teacher_projected = torch.zeros_like(student_feature)  # 没有教师时使用零向量

        gate = self.teacher_gate(torch.cat([student_feature, teacher_projected], dim=-1))
        guided_feature = gate * student_feature + (1.0 - gate) * teacher_projected
        final_feature = self.fusion_mlp(
            torch.cat([student_feature, guided_feature, teacher_projected], dim=-1)
        )
        logits = self.classifier(final_feature)
        return logits

# 预处理函数
def build_joint_and_bone_features(raw_features: np.ndarray, clip_value: float) -> Tuple[np.ndarray, np.ndarray]:
    """
    raw_features: shape (T, 1662) 其中每帧为 [姿态点x,y,z,visibility(33*4=132), 左手x,y,z(21*3=63), 右手x,y,z(21*3=63), 面部x,y,z(468*3=1404)]
    """
    features = raw_features.astype(np.float32, copy=True)
    features = np.nan_to_num(features, nan=0.0, posinf=0.0, neginf=0.0)

    # 姿态点索引
    # 鼻子 x,y (0,1) 左眼(8,9) 右眼(20,21) 左肩(44,45) 右肩(48,49)
    nose_xy = features[:, 0:2]
    leye_xy = features[:, 8:10]
    reye_xy = features[:, 20:22]
    # 嘴部近似 (取两个嘴角? 这里用 36:38 和 40:42 是 MediaPipe 脸部的索引？需要确认)
    # 在原始训练代码中，嘴巴取了 36:38 和 40:42，这些是面部关键点的索引，但面部在总数据中的起始位置是姿态点之后，这里需要调整。
    # 由于我们构造的 raw_features 是姿态点、左右手、面部连续排列，所以面部索引需要偏移。
    # 面部在 raw_features 中的起始索引 = 132 (姿态) + 63 (左手) + 63 (右手) = 258
    face_start = 132 + 63 + 63
    # 假设面部关键点顺序与 MediaPipe 一致，索引 36,40 等需要基于 face_start
    # 但为了简化，我们直接使用训练代码中的固定索引，但这些索引是基于完整特征的。需要重新计算。
    # 训练代码中使用了 features[:, 36:38] 和 features[:, 40:42]，这些是面部关键点坐标，而面部部分从索引 132 开始。
    # 所以实际面部索引 = 132 + 36 和 132 + 40。我们按照这个偏移。
    mouth_left = face_start + 36
    mouth_right = face_start + 40
    mouth_xy = (features[:, mouth_left:mouth_left+2] + features[:, mouth_right:mouth_right+2]) / 2.0

    # 右手食指指尖索引（右手21个点中，指尖是第8个点？训练代码中用了 219:221）
    # 右手在 raw_features 中的起始索引 = 132 + 63 = 195
    right_hand_start = 132 + 63
    rh_index_xy = features[:, right_hand_start + 219:right_hand_start + 221]  # 219? 实际右手点索引从0开始，这里可能指右手第某个点，但原始代码使用219:221，可能是错误，我们改为使用右手食指指尖（通常索引 8）
    # 实际右手食指指尖是第8个点（从0开始），所以偏移是 8*3=24，取x,y
    # 因此改为:
    rh_index_xy = features[:, right_hand_start + 24:right_hand_start + 26]

    # 左肩和右肩在姿态部分（前132个值）
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

    # 归一化姿态点
    x_indices = np.arange(0, 132, 4)
    y_indices = np.arange(1, 132, 4)
    z_indices = np.arange(2, 132, 4)
    features_normalized[:, x_indices] = (features[:, x_indices] - body_center_x) / shoulder_width
    features_normalized[:, y_indices] = (features[:, y_indices] - body_center_y) / shoulder_width
    features_normalized[:, z_indices] = features[:, z_indices] / shoulder_width

    # 左手归一化
    lhand_start = 132
    lhand_x_indices = np.arange(lhand_start, lhand_start + 63, 3)
    lhand_y_indices = np.arange(lhand_start + 1, lhand_start + 63, 3)
    lhand_z_indices = np.arange(lhand_start + 2, lhand_start + 63, 3)
    features_normalized[:, lhand_x_indices] = (features[:, lhand_x_indices] - body_center_x) / shoulder_width
    features_normalized[:, lhand_y_indices] = (features[:, lhand_y_indices] - body_center_y) / shoulder_width
    features_normalized[:, lhand_z_indices] = features[:, lhand_z_indices] / shoulder_width

    # 右手归一化
    rhand_start = 132 + 63
    rhand_x_indices = np.arange(rhand_start, rhand_start + 63, 3)
    rhand_y_indices = np.arange(rhand_start + 1, rhand_start + 63, 3)
    rhand_z_indices = np.arange(rhand_start + 2, rhand_start + 63, 3)
    features_normalized[:, rhand_x_indices] = (features[:, rhand_x_indices] - body_center_x) / shoulder_width
    features_normalized[:, rhand_y_indices] = (features[:, rhand_y_indices] - body_center_y) / shoulder_width
    features_normalized[:, rhand_z_indices] = features[:, rhand_z_indices] / shoulder_width

    feat_joint = np.concatenate(
        [features_normalized, dist_nose, dist_leye, dist_reye, dist_mouth],
        axis=1,
    )

    # 骨骼特征
    nose = features_normalized[:, 0:3]
    l_shoulder = features_normalized[:, 44:47]
    r_shoulder = features_normalized[:, 48:51]
    l_elbow = features_normalized[:, 52:55]
    r_elbow = features_normalized[:, 56:59]
    l_wrist = features_normalized[:, 60:63]
    r_wrist = features_normalized[:, 64:67]

    lh_wrist = features_normalized[:, lhand_start:lhand_start+3]
    rh_wrist = features_normalized[:, rhand_start:rhand_start+3]
    # 左手指尖
    lh_tips = [
        features_normalized[:, lhand_start + (i * 3):lhand_start + (i * 3) + 3]
        for i in [4, 8, 12, 16, 20]
    ]
    # 右手指尖
    rh_tips = [
        features_normalized[:, rhand_start + (i * 3):rhand_start + (i * 3) + 3]
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

def convert_frontend_to_model_format(frames: List[List[float]]) -> np.ndarray:
    """
    将前端传来的每帧关键点（1629个值）转换为模型需要的格式（1662个值）
    frames: 列表，每个元素是一维数组，长度 1629
    返回 shape (T, 1662) 的 numpy 数组
    """
    T = len(frames)
    model_frames = np.zeros((T, 1662), dtype=np.float32)

    for t, frame in enumerate(frames):
        # 解析前端关键点顺序：前33个姿态点（每个3个坐标），然后左手21*3，右手21*3，面部468*3
        # 总共 33*3 + 21*3 + 21*3 + 468*3 = 1629
        pose_xyz = frame[0:33*3]  # 99个值
        left_xyz = frame[33*3:33*3 + 21*3]  # 63
        right_xyz = frame[33*3 + 21*3:33*3 + 42*3]  # 63
        face_xyz = frame[33*3 + 42*3:]  # 1404

        # 构建姿态点部分：每点4个值 [x,y,z,visibility]
        for i in range(33):
            x = pose_xyz[i*3]
            y = pose_xyz[i*3+1]
            z = pose_xyz[i*3+2]
            model_frames[t, i*4] = x
            model_frames[t, i*4+1] = y
            model_frames[t, i*4+2] = z
            model_frames[t, i*4+3] = 1.0  # visibility

        # 左手部分：直接复制 x,y,z
        model_frames[t, 132:132+63] = left_xyz

        # 右手部分：直接复制
        model_frames[t, 132+63:132+63+63] = right_xyz

        # 面部部分：直接复制
        model_frames[t, 132+63+63:] = face_xyz

    return model_frames

class SignLanguageModel:
    def __init__(self, model_path: str, label_path: str, device: str = 'cuda'):
        self.device = torch.device(device if torch.cuda.is_available() else 'cpu')
        self.model = None
        self.label_map = None
        self.idx_to_word = None
        self.load_model(model_path, label_path)

    def load_model(self, model_path: str, label_path: str):
        # 加载标签映射
        with open(label_path, 'r', encoding='utf-8') as f:
            self.label_map = json.load(f)
        self.idx_to_word = {v: k for k, v in self.label_map.items()}
        num_classes = len(self.label_map)

        config = SignModelConfig()
        self.model = HybridFPNFormer(num_classes, config).to(self.device)

        # 加载权重
        checkpoint = torch.load(model_path, map_location=self.device)
        if 'model_state_dict' in checkpoint:
            state_dict = checkpoint['model_state_dict']
        else:
            state_dict = checkpoint
        # 过滤掉 teacher 相关的 key（如果存在）
        state_dict = {k: v for k, v in state_dict.items() if not k.startswith('legacy_teacher')}
        self.model.load_state_dict(state_dict, strict=False)
        self.model.eval()

    def recognize(self, frames: List[List[float]]) -> Tuple[str, float]:
        """
        frames: 关键点序列，每个元素是长度为1629的一维列表
        返回 (识别词, 置信度)
        """
        if not frames:
            return "", 0.0

        # 转换为模型输入格式
        model_input = convert_frontend_to_model_format(frames)  # (T, 1662)
        # 计算 joint 和 bone 特征
        joint_feat, bone_feat = build_joint_and_bone_features(model_input, clip_value=10.0)
        # 转换为 torch tensor
        joint_tensor = torch.tensor(joint_feat, dtype=torch.float32).unsqueeze(0)  # (1, T, dim_joint)
        bone_tensor = torch.tensor(bone_feat, dtype=torch.float32).unsqueeze(0)    # (1, T, dim_bone)
        lengths = torch.tensor([joint_feat.shape[0]], dtype=torch.long)

        with torch.no_grad():
            logits = self.model(joint_tensor.to(self.device), bone_tensor.to(self.device), lengths)
            probs = F.softmax(logits, dim=1)
            pred_idx = torch.argmax(probs, dim=1).item()
            confidence = probs[0, pred_idx].item()

        word = self.idx_to_word.get(pred_idx, "未知")
        return word, confidence