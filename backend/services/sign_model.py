import json
import math
from dataclasses import dataclass
from typing import Dict, List, Sequence, Tuple

import numpy as np
import torch
import torch.nn as nn
import torch.nn.functional as F


POSE_LANDMARKS = 33
HAND_LANDMARKS = 21
GRAPH_NODES = 75
RAW_FEATURE_DIM = 258
JOINT_FEATURE_DIM = 262
BONE_FEATURE_DIM = 48
RELATION_FEATURE_DIM = 76
LEGACY_HIDDEN_DIM = 64


@dataclass
class SignModelConfig:
    clip_features: float = 10.0
    input_dim_joint: int = JOINT_FEATURE_DIM
    input_dim_bone: int = BONE_FEATURE_DIM
    input_dim_velocity: int = JOINT_FEATURE_DIM
    input_dim_relation: int = RELATION_FEATURE_DIM
    graph_node_dim: int = 4
    graph_num_nodes: int = GRAPH_NODES
    legacy_hidden_dim: int = LEGACY_HIDDEN_DIM
    legacy_num_layers: int = 2
    stream_embed_dim: int = 128
    stream_depth: int = 3
    stream_heads: int = 4
    fusion_depth: int = 2
    graph_depth: int = 2
    dropout: float = 0.20
    reject_threshold: float = 0.55
    reject_margin: float = 0.10

    @classmethod
    def from_dict(cls, data: Dict[str, float]) -> "SignModelConfig":
        known_fields = {field.name for field in cls.__dataclass_fields__.values()}
        filtered = {key: value for key, value in (data or {}).items() if key in known_fields}
        return cls(**filtered)


def init_trunc_normal(tensor: torch.Tensor, std: float = 0.02) -> None:
    if hasattr(nn.init, "trunc_normal_"):
        nn.init.trunc_normal_(tensor, std=std)
    else:
        nn.init.normal_(tensor, std=std)


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
    def __init__(
        self,
        input_dim: int,
        hidden_dim: int,
        num_classes: int,
        num_layers: int,
        dropout: float,
    ) -> None:
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
    def __init__(
        self,
        joint_dim: int,
        bone_dim: int,
        hidden_dim: int,
        num_classes: int,
        num_layers: int,
        dropout: float,
    ) -> None:
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

    def forward_with_feature(
        self,
        x_joint: torch.Tensor,
        x_bone: torch.Tensor,
        lengths: torch.Tensor,
    ) -> Tuple[torch.Tensor, torch.Tensor]:
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


class MultiScaleTokenAssembler(nn.Module):
    def __init__(self, num_levels: int, dim: int) -> None:
        super().__init__()
        self.level_embeddings = nn.Parameter(torch.zeros(num_levels, 1, 1, dim))
        init_trunc_normal(self.level_embeddings, std=0.02)

    def forward(
        self,
        levels: Sequence[torch.Tensor],
        level_lengths: Sequence[torch.Tensor],
    ) -> Tuple[torch.Tensor, torch.Tensor]:
        tokens: List[torch.Tensor] = []
        masks: List[torch.Tensor] = []
        device = levels[0].device
        for idx, (level, lengths) in enumerate(zip(levels, level_lengths)):
            tokens.append(level + self.level_embeddings[idx])
            masks.append(make_pad_mask(lengths, level.size(1), device))
        return torch.cat(tokens, dim=1), torch.cat(masks, dim=1)


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


class TemporalStreamEncoder(nn.Module):
    def __init__(
        self,
        input_dim: int,
        embed_dim: int,
        depth: int,
        num_heads: int,
        dropout: float,
        num_classes: int,
    ) -> None:
        super().__init__()
        self.input_proj = nn.Sequential(
            nn.Linear(input_dim, embed_dim),
            nn.LayerNorm(embed_dim),
            nn.GELU(),
            nn.Dropout(dropout),
        )
        self.temporal_fpn = TemporalFPN(embed_dim, dropout)
        self.assembler = MultiScaleTokenAssembler(3, embed_dim)
        self.temporal_encoder = TemporalTransformerEncoder(embed_dim, depth, num_heads, dropout)
        self.aux_head = nn.Linear(embed_dim, num_classes)

    def forward(self, x: torch.Tensor, lengths: torch.Tensor) -> Tuple[torch.Tensor, torch.Tensor]:
        tokens = self.input_proj(x)
        levels, level_lengths = self.temporal_fpn(tokens, lengths)
        assembled_tokens, token_mask = self.assembler(levels, level_lengths)
        feature, _ = self.temporal_encoder(assembled_tokens, token_mask)
        level0_mask = make_pad_mask(lengths, levels[0].size(1), levels[0].device)
        aux_logits = self.aux_head(masked_mean(levels[0], level0_mask))
        return feature, aux_logits


class GraphNodePool(nn.Module):
    def __init__(self, dim: int) -> None:
        super().__init__()
        self.score = nn.Linear(dim, 1)

    def forward(self, x: torch.Tensor, mask: torch.Tensor) -> torch.Tensor:
        score = self.score(x).squeeze(-1)
        score = score.masked_fill(~mask, -1e4)
        attn = F.softmax(score, dim=-1)
        return torch.sum(x * attn.unsqueeze(-1), dim=2)


class GraphTemporalBlock(nn.Module):
    def __init__(self, dim: int) -> None:
        super().__init__()
        self.spatial = nn.Linear(dim, dim)
        self.norm1 = nn.LayerNorm(dim)
        self.temporal = nn.Sequential(
            nn.Conv1d(dim, dim, kernel_size=3, padding=1, groups=dim, bias=False),
            nn.BatchNorm1d(dim),
            nn.GELU(),
            nn.Conv1d(dim, dim, kernel_size=1, bias=False),
        )
        self.norm2 = nn.LayerNorm(dim)

    def forward(self, x: torch.Tensor, adjacency: torch.Tensor) -> torch.Tensor:
        spatial_context = torch.einsum("ij,btjd->btid", adjacency, x)
        x = self.norm1(x + self.spatial(spatial_context))

        batch_size, time_steps, nodes, dim = x.shape
        temporal_input = x.permute(0, 2, 3, 1).reshape(batch_size * nodes, dim, time_steps)
        temporal_output = self.temporal(temporal_input)
        temporal_output = temporal_output.reshape(batch_size, nodes, dim, time_steps).permute(0, 3, 1, 2)
        return self.norm2(x + temporal_output)


def build_graph_adjacency() -> torch.Tensor:
    adjacency = torch.eye(GRAPH_NODES, dtype=torch.float32)

    pose_edges = [
        (0, 1), (1, 2), (2, 3), (3, 7),
        (0, 4), (4, 5), (5, 6), (6, 8),
        (9, 10),
        (11, 12),
        (11, 13), (13, 15),
        (12, 14), (14, 16),
        (15, 17), (15, 19), (15, 21),
        (16, 18), (16, 20), (16, 22),
        (11, 23), (12, 24), (23, 24),
        (23, 25), (24, 26),
        (25, 27), (26, 28),
        (27, 29), (28, 30),
        (29, 31), (30, 32),
    ]

    hand_edges = [
        (0, 1), (1, 2), (2, 3), (3, 4),
        (0, 5), (5, 6), (6, 7), (7, 8),
        (0, 9), (9, 10), (10, 11), (11, 12),
        (0, 13), (13, 14), (14, 15), (15, 16),
        (0, 17), (17, 18), (18, 19), (19, 20),
        (5, 9), (9, 13), (13, 17),
    ]

    left_offset = POSE_LANDMARKS
    right_offset = POSE_LANDMARKS + HAND_LANDMARKS

    for start, end in pose_edges:
        adjacency[start, end] = 1.0
        adjacency[end, start] = 1.0

    for start, end in hand_edges:
        adjacency[left_offset + start, left_offset + end] = 1.0
        adjacency[left_offset + end, left_offset + start] = 1.0
        adjacency[right_offset + start, right_offset + end] = 1.0
        adjacency[right_offset + end, right_offset + start] = 1.0

    adjacency[15, left_offset + 0] = 1.0
    adjacency[left_offset + 0, 15] = 1.0
    adjacency[16, right_offset + 0] = 1.0
    adjacency[right_offset + 0, 16] = 1.0

    degree = adjacency.sum(dim=1, keepdim=True).clamp_min(1.0)
    return adjacency / degree


class GraphStreamEncoder(nn.Module):
    def __init__(self, config: SignModelConfig, num_classes: int) -> None:
        super().__init__()
        self.register_buffer("adjacency", build_graph_adjacency())
        self.node_proj = nn.Sequential(
            nn.Linear(config.graph_node_dim, config.stream_embed_dim),
            nn.LayerNorm(config.stream_embed_dim),
            nn.GELU(),
            nn.Dropout(config.dropout),
        )
        self.blocks = nn.ModuleList(
            [GraphTemporalBlock(config.stream_embed_dim) for _ in range(config.graph_depth)]
        )
        self.node_pool = GraphNodePool(config.stream_embed_dim)
        self.temporal_encoder = TemporalTransformerEncoder(
            config.stream_embed_dim,
            config.graph_depth,
            config.stream_heads,
            config.dropout,
        )
        self.aux_head = nn.Linear(config.stream_embed_dim, num_classes)

    def forward(self, x: torch.Tensor, lengths: torch.Tensor) -> Tuple[torch.Tensor, torch.Tensor]:
        projected = self.node_proj(x)
        for block in self.blocks:
            projected = block(projected, self.adjacency)

        node_mask = x[..., 3] > 0
        pooled_nodes = self.node_pool(projected, node_mask)
        time_mask = make_pad_mask(lengths, pooled_nodes.size(1), pooled_nodes.device)
        feature, _ = self.temporal_encoder(pooled_nodes, time_mask)
        aux_logits = self.aux_head(masked_mean(pooled_nodes, time_mask))
        return feature, aux_logits


class StreamFusionTransformer(nn.Module):
    def __init__(self, num_streams: int, dim: int, depth: int, num_heads: int, dropout: float) -> None:
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
        self.stream_embeddings = nn.Parameter(torch.zeros(num_streams, 1, dim))
        self.cls_token = nn.Parameter(torch.zeros(1, 1, dim))
        self.encoder = nn.TransformerEncoder(layer, num_layers=depth)
        self.norm = nn.LayerNorm(dim)
        self.score = nn.Linear(dim, 1)
        init_trunc_normal(self.stream_embeddings, std=0.02)
        init_trunc_normal(self.cls_token, std=0.02)

    def forward(self, stream_features: Sequence[torch.Tensor]) -> torch.Tensor:
        tokens = torch.stack(list(stream_features), dim=1)
        stream_embed = self.stream_embeddings.transpose(0, 1)
        x = tokens + stream_embed

        batch_size = x.size(0)
        cls = self.cls_token.expand(batch_size, -1, -1)
        x = torch.cat([cls, x], dim=1)
        encoded = self.encoder(x)
        encoded = self.norm(encoded)

        cls_feature = encoded[:, 0]
        score = self.score(encoded[:, 1:]).squeeze(-1)
        attn = F.softmax(score, dim=-1)
        pooled = torch.sum(encoded[:, 1:] * attn.unsqueeze(-1), dim=1)
        return cls_feature + pooled


class RobustMultiStreamGraFormer(nn.Module):
    def __init__(self, num_classes: int, config: SignModelConfig) -> None:
        super().__init__()
        self.config = config

        self.joint_encoder = TemporalStreamEncoder(
            config.input_dim_joint,
            config.stream_embed_dim,
            config.stream_depth,
            config.stream_heads,
            config.dropout,
            num_classes,
        )
        self.bone_encoder = TemporalStreamEncoder(
            config.input_dim_bone,
            config.stream_embed_dim,
            config.stream_depth,
            config.stream_heads,
            config.dropout,
            num_classes,
        )
        self.velocity_encoder = TemporalStreamEncoder(
            config.input_dim_velocity,
            config.stream_embed_dim,
            config.stream_depth,
            config.stream_heads,
            config.dropout,
            num_classes,
        )
        self.relation_encoder = TemporalStreamEncoder(
            config.input_dim_relation,
            config.stream_embed_dim,
            config.stream_depth,
            config.stream_heads,
            config.dropout,
            num_classes,
        )
        self.graph_encoder = GraphStreamEncoder(config, num_classes)
        self.stream_fusion = StreamFusionTransformer(
            5,
            config.stream_embed_dim,
            config.fusion_depth,
            config.stream_heads,
            config.dropout,
        )

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
            nn.Linear(config.legacy_hidden_dim, config.stream_embed_dim),
            nn.LayerNorm(config.stream_embed_dim),
            nn.GELU(),
            nn.Dropout(config.dropout),
        )
        self.teacher_gate = nn.Sequential(
            nn.Linear(config.stream_embed_dim * 2, config.stream_embed_dim),
            nn.GELU(),
            nn.Dropout(config.dropout),
            nn.Linear(config.stream_embed_dim, config.stream_embed_dim),
            nn.Sigmoid(),
        )
        self.fusion_mlp = nn.Sequential(
            nn.Linear(config.stream_embed_dim * 3, config.stream_embed_dim * 2),
            nn.LayerNorm(config.stream_embed_dim * 2),
            nn.GELU(),
            nn.Dropout(config.dropout),
        )
        self.classifier = nn.Linear(config.stream_embed_dim * 2, num_classes)

    def forward(
        self,
        x_joint: torch.Tensor,
        x_bone: torch.Tensor,
        x_velocity: torch.Tensor,
        x_relation: torch.Tensor,
        x_graph: torch.Tensor,
        lengths: torch.Tensor,
    ) -> torch.Tensor:
        joint_feature, _ = self.joint_encoder(x_joint, lengths)
        bone_feature, _ = self.bone_encoder(x_bone, lengths)
        velocity_feature, _ = self.velocity_encoder(x_velocity, lengths)
        relation_feature, _ = self.relation_encoder(x_relation, lengths)
        graph_feature, _ = self.graph_encoder(x_graph, lengths)

        student_feature = self.stream_fusion(
            [joint_feature, bone_feature, velocity_feature, relation_feature, graph_feature]
        )

        teacher_projected = torch.zeros_like(student_feature)
        if self.teacher_loaded:
            _, teacher_feature = self.legacy_teacher.forward_with_feature(x_joint, x_bone, lengths)
            teacher_projected = self.teacher_projector(teacher_feature)

        gate = self.teacher_gate(torch.cat([student_feature, teacher_projected], dim=-1))
        guided_feature = gate * student_feature + (1.0 - gate) * teacher_projected
        final_feature = self.fusion_mlp(
            torch.cat([student_feature, guided_feature, teacher_projected], dim=-1)
        )
        return self.classifier(final_feature)


def _normalize_single_frame(frame: List[float], index: int) -> np.ndarray:
    frame_array = np.asarray(frame, dtype=np.float32)
    frame_array = np.nan_to_num(frame_array, nan=0.0, posinf=0.0, neginf=0.0)

    if frame_array.ndim == 2:
        frame_array = frame_array.reshape(-1)
    elif frame_array.ndim != 1:
        raise ValueError(f"Frame {index} has unsupported shape {frame_array.shape}.")

    frame_dim = int(frame_array.shape[0])

    if frame_dim == RAW_FEATURE_DIM:
        return frame_array.astype(np.float32)

    if frame_dim == 1662:
        return frame_array[:RAW_FEATURE_DIM].astype(np.float32)

    if frame_dim == 1629:
        output = np.zeros((RAW_FEATURE_DIM,), dtype=np.float32)
        pose_xyz = frame_array[: POSE_LANDMARKS * 3]
        left_xyz = frame_array[POSE_LANDMARKS * 3 : POSE_LANDMARKS * 3 + HAND_LANDMARKS * 3]
        right_xyz = frame_array[
            POSE_LANDMARKS * 3 + HAND_LANDMARKS * 3 : POSE_LANDMARKS * 3 + HAND_LANDMARKS * 6
        ]

        for idx in range(POSE_LANDMARKS):
            source = pose_xyz[idx * 3 : idx * 3 + 3]
            output[idx * 4 : idx * 4 + 3] = source
            output[idx * 4 + 3] = 1.0 if np.linalg.norm(source) > 1e-6 else 0.0

        output[132:195] = left_xyz
        output[195:258] = right_xyz
        return output

    raise ValueError(
        f"Frame {index} has unsupported dimension {frame_dim}. Expected 258, 1629, or 1662."
    )


def convert_frontend_to_model_format(frames: List[List[float]]) -> np.ndarray:
    """
    Accepted per-frame formats:
    - 258: pose (33 * xyzw) + left hand (21 * xyz) + right hand (21 * xyz)
    - 1629: pose (33 * xyz) + left hand (21 * xyz) + right hand (21 * xyz) + face (468 * xyz)
    - 1662: pose (33 * xyzw) + left hand (21 * xyz) + right hand (21 * xyz) + face (468 * xyz)
    - nested keypoint arrays that flatten to one of the above
    """
    if not frames:
        raise ValueError("Frames must not be empty.")

    normalized_frames = [_normalize_single_frame(frame, index) for index, frame in enumerate(frames)]
    return np.stack(normalized_frames, axis=0).astype(np.float32)


def normalize_raw_features(raw_features: np.ndarray) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    features = raw_features.astype(np.float32, copy=True)
    features = np.nan_to_num(features, nan=0.0, posinf=0.0, neginf=0.0)

    l_shoulder_x = features[:, 44:45]
    l_shoulder_y = features[:, 45:46]
    r_shoulder_x = features[:, 48:49]
    r_shoulder_y = features[:, 49:50]

    body_center_x = (l_shoulder_x + r_shoulder_x) / 2.0
    body_center_y = (l_shoulder_y + r_shoulder_y) / 2.0
    shoulder_width = np.sqrt((r_shoulder_x - l_shoulder_x) ** 2 + (r_shoulder_y - l_shoulder_y) ** 2) + 1e-6

    normalized = features.copy()

    pose_x_indices = np.arange(0, 132, 4)
    pose_y_indices = np.arange(1, 132, 4)
    pose_z_indices = np.arange(2, 132, 4)
    normalized[:, pose_x_indices] = (features[:, pose_x_indices] - body_center_x) / shoulder_width
    normalized[:, pose_y_indices] = (features[:, pose_y_indices] - body_center_y) / shoulder_width
    normalized[:, pose_z_indices] = features[:, pose_z_indices] / shoulder_width

    left_start = 132
    left_x_indices = np.arange(left_start, left_start + 63, 3)
    left_y_indices = np.arange(left_start + 1, left_start + 63, 3)
    left_z_indices = np.arange(left_start + 2, left_start + 63, 3)
    normalized[:, left_x_indices] = (features[:, left_x_indices] - body_center_x) / shoulder_width
    normalized[:, left_y_indices] = (features[:, left_y_indices] - body_center_y) / shoulder_width
    normalized[:, left_z_indices] = features[:, left_z_indices] / shoulder_width

    right_start = 195
    right_x_indices = np.arange(right_start, right_start + 63, 3)
    right_y_indices = np.arange(right_start + 1, right_start + 63, 3)
    right_z_indices = np.arange(right_start + 2, right_start + 63, 3)
    normalized[:, right_x_indices] = (features[:, right_x_indices] - body_center_x) / shoulder_width
    normalized[:, right_y_indices] = (features[:, right_y_indices] - body_center_y) / shoulder_width
    normalized[:, right_z_indices] = features[:, right_z_indices] / shoulder_width

    body_center_xyz = np.concatenate(
        [np.zeros_like(body_center_x), np.zeros_like(body_center_y), np.zeros_like(body_center_x)],
        axis=1,
    )
    return normalized, shoulder_width, body_center_xyz


def build_joint_and_bone_features(raw_features: np.ndarray, clip_value: float) -> Tuple[np.ndarray, np.ndarray]:
    features = raw_features.astype(np.float32, copy=True)
    features = np.nan_to_num(features, nan=0.0, posinf=0.0, neginf=0.0)
    normalized, shoulder_width, _ = normalize_raw_features(features)

    nose_xy = features[:, 0:2]
    left_eye_xy = features[:, 8:10]
    right_eye_xy = features[:, 20:22]
    mouth_xy = (features[:, 36:38] + features[:, 40:42]) / 2.0
    right_index_xy = features[:, 219:221]

    dist_nose = np.linalg.norm(right_index_xy - nose_xy, axis=1, keepdims=True) / shoulder_width
    dist_left_eye = np.linalg.norm(right_index_xy - left_eye_xy, axis=1, keepdims=True) / shoulder_width
    dist_right_eye = np.linalg.norm(right_index_xy - right_eye_xy, axis=1, keepdims=True) / shoulder_width
    dist_mouth = np.linalg.norm(right_index_xy - mouth_xy, axis=1, keepdims=True) / shoulder_width

    feat_joint = np.concatenate(
        [normalized, dist_nose, dist_left_eye, dist_right_eye, dist_mouth],
        axis=1,
    )

    nose = normalized[:, 0:3]
    left_shoulder = normalized[:, 44:47]
    right_shoulder = normalized[:, 48:51]
    left_elbow = normalized[:, 52:55]
    right_elbow = normalized[:, 56:59]
    left_wrist = normalized[:, 60:63]
    right_wrist = normalized[:, 64:67]

    left_hand_wrist = normalized[:, 132:135]
    right_hand_wrist = normalized[:, 195:198]
    left_hand_tips = [
        normalized[:, 132 + idx * 3 : 132 + idx * 3 + 3]
        for idx in [4, 8, 12, 16, 20]
    ]
    right_hand_tips = [
        normalized[:, 195 + idx * 3 : 195 + idx * 3 + 3]
        for idx in [4, 8, 12, 16, 20]
    ]

    bone_vectors = [
        left_shoulder - nose,
        right_shoulder - nose,
        left_elbow - left_shoulder,
        right_elbow - right_shoulder,
        left_wrist - left_elbow,
        right_wrist - right_elbow,
    ]
    bone_vectors.extend(tip - left_hand_wrist for tip in left_hand_tips)
    bone_vectors.extend(tip - right_hand_wrist for tip in right_hand_tips)
    feat_bone = np.concatenate(bone_vectors, axis=1)

    feat_joint = np.nan_to_num(feat_joint, nan=0.0, posinf=0.0, neginf=0.0)
    feat_bone = np.nan_to_num(feat_bone, nan=0.0, posinf=0.0, neginf=0.0)
    feat_joint = np.clip(feat_joint, -clip_value, clip_value).astype(np.float32)
    feat_bone = np.clip(feat_bone, -clip_value, clip_value).astype(np.float32)
    return feat_joint, feat_bone


def build_velocity_features(joint_features: np.ndarray, clip_value: float) -> np.ndarray:
    velocity = np.zeros_like(joint_features, dtype=np.float32)
    if len(joint_features) > 1:
        velocity[1:] = joint_features[1:] - joint_features[:-1]
    velocity = np.nan_to_num(velocity, nan=0.0, posinf=0.0, neginf=0.0)
    return np.clip(velocity, -clip_value, clip_value).astype(np.float32)


def build_relation_features(raw_features: np.ndarray, clip_value: float) -> np.ndarray:
    normalized, _, body_center = normalize_raw_features(raw_features)

    nose = normalized[:, 0:3]
    left_eye = normalized[:, 8:11]
    right_eye = normalized[:, 20:23]
    mouth = (normalized[:, 36:39] + normalized[:, 40:43]) / 2.0
    left_shoulder = normalized[:, 44:47]
    right_shoulder = normalized[:, 48:51]

    left_wrist = normalized[:, 132:135]
    right_wrist = normalized[:, 195:198]
    left_index_tip = normalized[:, 132 + 8 * 3 : 132 + 8 * 3 + 3]
    right_index_tip = normalized[:, 195 + 8 * 3 : 195 + 8 * 3 + 3]

    anchors = [nose, left_eye, right_eye, mouth, left_shoulder, right_shoulder, body_center]
    relation_pairs = []

    for anchor in anchors:
        relation_pairs.append((left_wrist, anchor))
    for anchor in anchors:
        relation_pairs.append((right_wrist, anchor))

    relation_pairs.extend(
        [
            (left_index_tip, nose),
            (left_index_tip, mouth),
            (right_index_tip, nose),
            (right_index_tip, mouth),
            (left_index_tip, right_index_tip),
        ]
    )

    relation_vectors = []
    for source, target in relation_pairs:
        delta = source - target
        distance = np.linalg.norm(delta, axis=1, keepdims=True)
        relation_vectors.append(np.concatenate([delta, distance], axis=1))

    relations = np.concatenate(relation_vectors, axis=1)
    relations = np.nan_to_num(relations, nan=0.0, posinf=0.0, neginf=0.0)
    return np.clip(relations, -clip_value, clip_value).astype(np.float32)


def build_graph_nodes(raw_features: np.ndarray, clip_value: float) -> np.ndarray:
    normalized, _, _ = normalize_raw_features(raw_features)
    time_steps = normalized.shape[0]
    nodes = np.zeros((time_steps, GRAPH_NODES, 4), dtype=np.float32)

    for idx in range(POSE_LANDMARKS):
        start = idx * 4
        nodes[:, idx, :3] = normalized[:, start : start + 3]
        nodes[:, idx, 3] = raw_features[:, start + 3]

    for idx in range(HAND_LANDMARKS):
        left_start = 132 + idx * 3
        right_start = 195 + idx * 3
        left_coords = normalized[:, left_start : left_start + 3]
        right_coords = normalized[:, right_start : right_start + 3]

        left_presence = (np.linalg.norm(raw_features[:, left_start : left_start + 3], axis=1) > 1e-6).astype(np.float32)
        right_presence = (np.linalg.norm(raw_features[:, right_start : right_start + 3], axis=1) > 1e-6).astype(np.float32)

        nodes[:, POSE_LANDMARKS + idx, :3] = left_coords
        nodes[:, POSE_LANDMARKS + idx, 3] = left_presence
        nodes[:, POSE_LANDMARKS + HAND_LANDMARKS + idx, :3] = right_coords
        nodes[:, POSE_LANDMARKS + HAND_LANDMARKS + idx, 3] = right_presence

    nodes = np.nan_to_num(nodes, nan=0.0, posinf=0.0, neginf=0.0)
    return np.clip(nodes, -clip_value, clip_value).astype(np.float32)


def build_model_features(
    raw_features: np.ndarray,
    clip_value: float,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    joint_features, bone_features = build_joint_and_bone_features(raw_features, clip_value)
    velocity_features = build_velocity_features(joint_features, clip_value)
    relation_features = build_relation_features(raw_features, clip_value)
    graph_nodes = build_graph_nodes(raw_features, clip_value)
    return joint_features, bone_features, velocity_features, relation_features, graph_nodes


class SignLanguageModel:
    def __init__(self, model_path: str, label_path: str, device: str = "cuda") -> None:
        self.device = torch.device(device if torch.cuda.is_available() else "cpu")
        self.model: RobustMultiStreamGraFormer
        self.label_map: Dict[str, int] = {}
        self.idx_to_word: Dict[int, str] = {}
        self.config = SignModelConfig()
        self.load_model(model_path, label_path)

    def load_model(self, model_path: str, label_path: str) -> None:
        with open(label_path, "r", encoding="utf-8") as file:
            raw = json.load(file)
        # 兼容两种标注格式：
        # 旧格式: {"词语": index, ...}
        # 新格式: {"num_classes": N, "by_index": {"0": {"display_name": "词语", "index": 0, ...}}, ...}
        if "by_index" in raw:
            self.label_map = {
                entry["display_name"]: entry["index"]
                for entry in raw["by_index"].values()
            }
        else:
            self.label_map = raw
        self.idx_to_word = {value: key for key, value in self.label_map.items()}

        checkpoint = torch.load(model_path, map_location=self.device)
        config_dict = checkpoint.get("config", {})
        self.config = SignModelConfig.from_dict(config_dict)
        self.model = RobustMultiStreamGraFormer(len(self.label_map), self.config).to(self.device)

        state_dict = checkpoint["model_state_dict"] if "model_state_dict" in checkpoint else checkpoint
        self.model.load_state_dict(state_dict, strict=True)
        self.model.teacher_loaded = True
        self.model.eval()

    def recognize(self, frames: List[List[float]]) -> Tuple[str, float]:
        if not frames:
            return "", 0.0

        raw_features = convert_frontend_to_model_format(frames)
        joint_feat, bone_feat, velocity_feat, relation_feat, graph_nodes = build_model_features(
            raw_features,
            clip_value=self.config.clip_features,
        )

        lengths = torch.tensor([joint_feat.shape[0]], dtype=torch.long, device=self.device)
        joint_tensor = torch.tensor(joint_feat, dtype=torch.float32, device=self.device).unsqueeze(0)
        bone_tensor = torch.tensor(bone_feat, dtype=torch.float32, device=self.device).unsqueeze(0)
        velocity_tensor = torch.tensor(velocity_feat, dtype=torch.float32, device=self.device).unsqueeze(0)
        relation_tensor = torch.tensor(relation_feat, dtype=torch.float32, device=self.device).unsqueeze(0)
        graph_tensor = torch.tensor(graph_nodes, dtype=torch.float32, device=self.device).unsqueeze(0)

        with torch.no_grad():
            logits = self.model(
                joint_tensor,
                bone_tensor,
                velocity_tensor,
                relation_tensor,
                graph_tensor,
                lengths,
            )
            probs = F.softmax(logits, dim=1)
            pred_idx = torch.argmax(probs, dim=1).item()
            confidence = probs[0, pred_idx].item()

        return self.idx_to_word.get(pred_idx, "未识别"), confidence
