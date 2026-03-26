# backend/services/sign_model.py
import os
import json
import math
import base64
import numpy as np
import cv2
import torch
import torch.nn as nn
import torch.nn.functional as F
from typing import List, Dict, Optional, Tuple, Sequence

# ========== 1. 模型配置 ==========
class SignModelConfig:
    input_dim_joint   = 262   # joint 流输入维度（258 原始 + 4 距离特征）
    input_dim_bone    = 48    # bone 流输入维度
    embed_dim         = 128   # ★ 必须与 checkpoint 一致（训练时 128，不是 192）
    transformer_depth = 3
    num_heads         = 4     # ★ 128/4=32 ✓（6 不能整除 128，会在 Transformer 内部报错）
    dropout           = 0.20
    legacy_hidden_dim = 64
    legacy_num_layers = 2
    clip_features     = 10.0


# ========== 2. 模型结构定义（与 hybrid_fpn_vit_distill_train.py 完全一致）==========

def init_trunc_normal(tensor: torch.Tensor, std: float = 0.02) -> None:
    if hasattr(nn.init, "trunc_normal_"):
        nn.init.trunc_normal_(tensor, std=std)
    else:
        nn.init.normal_(tensor, std=std)

def make_pad_mask(lengths: torch.Tensor, max_len: int, device: torch.device) -> torch.Tensor:
    lengths = lengths.to(device)
    steps   = torch.arange(max_len, device=device).unsqueeze(0)
    return steps < lengths.unsqueeze(1)

def masked_mean(x: torch.Tensor, mask: torch.Tensor) -> torch.Tensor:
    weight = mask.unsqueeze(-1).float()
    denom  = weight.sum(dim=1).clamp_min(1.0)
    return (x * weight).sum(dim=1) / denom

def stride2_lengths(lengths: torch.Tensor) -> torch.Tensor:
    return (lengths + 1) // 2

def sinusoidal_position_encoding(length: int, dim: int, device: torch.device) -> torch.Tensor:
    position = torch.arange(length, device=device, dtype=torch.float32).unsqueeze(1)
    div_term = torch.exp(
        torch.arange(0, dim, 2, device=device, dtype=torch.float32) * (-math.log(10000.0) / dim)
    )
    pe = torch.zeros(length, dim, device=device, dtype=torch.float32)
    pe[:, 0::2] = torch.sin(position * div_term)
    pe[:, 1::2] = torch.cos(position * div_term)
    return pe.unsqueeze(0)


class LegacyAttention(nn.Module):
    def __init__(self, hidden_dim: int, dropout: float):
        super().__init__()
        self.attn    = nn.Linear(hidden_dim * 2, 1)
        self.dropout = nn.Dropout(dropout)

    def forward(self, lstm_output: torch.Tensor) -> torch.Tensor:
        energy  = self.dropout(self.attn(lstm_output))
        weights = F.softmax(energy, dim=1)
        return torch.sum(lstm_output * weights, dim=1)


class LegacyBiLSTMStream(nn.Module):
    def __init__(self, input_dim: int, hidden_dim: int, num_classes: int,
                 num_layers: int, dropout: float):
        super().__init__()
        self.lstm = nn.LSTM(
            input_size=input_dim, hidden_size=hidden_dim,
            num_layers=num_layers, batch_first=True, bidirectional=True,
            dropout=dropout if num_layers > 1 else 0.0,
        )
        self.attention = LegacyAttention(hidden_dim, dropout)
        self.fc        = nn.Linear(hidden_dim * 2, num_classes)

    def extract_feature(self, x: torch.Tensor, lengths: torch.Tensor) -> torch.Tensor:
        packed        = nn.utils.rnn.pack_padded_sequence(x, lengths.cpu(),
                                                          batch_first=True, enforce_sorted=False)
        packed_out, _ = self.lstm(packed)
        lstm_out, _   = nn.utils.rnn.pad_packed_sequence(packed_out, batch_first=True)
        return self.attention(lstm_out)

    def forward(self, x: torch.Tensor, lengths: torch.Tensor) -> torch.Tensor:
        return self.fc(self.extract_feature(x, lengths))


class LegacyTwoStreamTeacher(nn.Module):
    def __init__(self, joint_dim: int, bone_dim: int, hidden_dim: int,
                 num_classes: int, num_layers: int, dropout: float):
        super().__init__()
        self.stream_joint = LegacyBiLSTMStream(joint_dim, hidden_dim,
                                                num_classes, num_layers, dropout)
        self.stream_bone  = LegacyBiLSTMStream(bone_dim,  hidden_dim,
                                                num_classes, num_layers, dropout)
        self.fusion_fc = nn.Sequential(
            nn.Linear(hidden_dim * 4, hidden_dim * 2), nn.BatchNorm1d(hidden_dim * 2),
            nn.ReLU(), nn.Dropout(dropout),
            nn.Linear(hidden_dim * 2, hidden_dim),     nn.BatchNorm1d(hidden_dim),
            nn.ReLU(), nn.Dropout(dropout),
            nn.Linear(hidden_dim, num_classes),
        )

    def forward_with_feature(self, x_joint: torch.Tensor, x_bone: torch.Tensor,
                              lengths: torch.Tensor) -> Tuple[torch.Tensor, torch.Tensor]:
        jf       = self.stream_joint.extract_feature(x_joint, lengths)
        bf       = self.stream_bone.extract_feature(x_bone,  lengths)
        combined = torch.cat([jf, bf], dim=1)
        hidden   = combined
        layers   = list(self.fusion_fc.children())
        for layer in layers[:-1]:
            hidden = layer(hidden)
        return layers[-1](hidden), hidden

    def forward(self, x_joint, x_bone, lengths):
        logits, _ = self.forward_with_feature(x_joint, x_bone, lengths)
        return logits


class SqueezeExcitation1D(nn.Module):
    def __init__(self, channels: int, reduction: int = 4):
        super().__init__()
        hidden = max(channels // reduction, 8)
        self.net = nn.Sequential(
            nn.AdaptiveAvgPool1d(1),
            nn.Conv1d(channels, hidden, kernel_size=1), nn.GELU(),
            nn.Conv1d(hidden, channels, kernel_size=1), nn.Sigmoid(),
        )
    def forward(self, x): return x * self.net(x)


class TemporalConvBlock(nn.Module):
    def __init__(self, channels: int, stride: int = 1, dropout: float = 0.1):
        super().__init__()
        self.depthwise = nn.Conv1d(channels, channels, kernel_size=3, stride=stride,
                                   padding=1, groups=channels, bias=False)
        self.pointwise = nn.Conv1d(channels, channels, kernel_size=1, bias=False)
        self.norm      = nn.BatchNorm1d(channels)
        self.act       = nn.GELU()
        self.se        = SqueezeExcitation1D(channels)
        self.dropout   = nn.Dropout(dropout)

    def forward(self, x):
        x = self.depthwise(x)
        x = self.pointwise(x)
        x = self.norm(x)
        x = self.act(x)
        x = self.se(x)
        return self.dropout(x)


class TemporalFPN(nn.Module):
    def __init__(self, channels: int, dropout: float):
        super().__init__()
        self.stem    = TemporalConvBlock(channels, stride=1, dropout=dropout)
        self.down1   = TemporalConvBlock(channels, stride=2, dropout=dropout)
        self.down2   = TemporalConvBlock(channels, stride=2, dropout=dropout)
        self.smooth1 = TemporalConvBlock(channels, stride=1, dropout=dropout)
        self.smooth0 = TemporalConvBlock(channels, stride=1, dropout=dropout)

    def forward(self, x: torch.Tensor,
                lengths: torch.Tensor) -> Tuple[List[torch.Tensor], List[torch.Tensor]]:
        x  = x.transpose(1, 2)
        c0 = self.stem(x)
        c1 = self.down1(c0)
        c2 = self.down2(c1)
        p2 = c2
        p1 = self.smooth1(c1 + F.interpolate(p2, size=c1.shape[-1],
                                              mode="linear", align_corners=False))
        p0 = self.smooth0(c0 + F.interpolate(p1, size=c0.shape[-1],
                                              mode="linear", align_corners=False))
        l0 = lengths
        l1 = stride2_lengths(l0)
        l2 = stride2_lengths(l1)
        return [p0.transpose(1, 2), p1.transpose(1, 2), p2.transpose(1, 2)], [l0, l1, l2]


class CrossStreamGate(nn.Module):
    def __init__(self, dim: int, dropout: float):
        super().__init__()
        self.gate = nn.Sequential(
            nn.Linear(dim * 4, dim), nn.GELU(), nn.Dropout(dropout),
            nn.Linear(dim, dim),     nn.Sigmoid(),
        )
        self.out = nn.Sequential(
            nn.Linear(dim * 2, dim), nn.LayerNorm(dim), nn.GELU(), nn.Dropout(dropout),
        )

    def forward(self, joint_tokens: torch.Tensor, bone_tokens: torch.Tensor) -> torch.Tensor:
        stats = torch.cat(
            [joint_tokens, bone_tokens,
             joint_tokens - bone_tokens, joint_tokens * bone_tokens], dim=-1,
        )
        gate  = self.gate(stats)
        mixed = gate * joint_tokens + (1.0 - gate) * bone_tokens
        return self.out(torch.cat([mixed, joint_tokens - bone_tokens], dim=-1))


class MultiScaleTokenAssembler(nn.Module):
    def __init__(self, num_levels: int, dim: int):
        super().__init__()
        self.level_embeddings = nn.Parameter(torch.zeros(num_levels, 1, 1, dim))
        init_trunc_normal(self.level_embeddings, std=0.02)

    def forward(self, levels: Sequence[torch.Tensor],
                level_lengths: Sequence[torch.Tensor]) -> Tuple[torch.Tensor, torch.Tensor]:
        tokens: List[torch.Tensor] = []
        masks:  List[torch.Tensor] = []
        device = levels[0].device
        for idx, (level, lengths) in enumerate(zip(levels, level_lengths)):
            tokens.append(level + self.level_embeddings[idx])
            masks.append(make_pad_mask(lengths, level.size(1), device))
        return torch.cat(tokens, dim=1), torch.cat(masks, dim=1)


class AttentionPool(nn.Module):
    def __init__(self, dim: int):
        super().__init__()
        self.score = nn.Linear(dim, 1)

    def forward(self, x: torch.Tensor, mask: torch.Tensor) -> torch.Tensor:
        score = self.score(x).squeeze(-1)
        score = score.masked_fill(~mask, -1e4)
        attn  = F.softmax(score, dim=-1)
        return torch.sum(x * attn.unsqueeze(-1), dim=1)


class TemporalTransformerEncoder(nn.Module):
    def __init__(self, dim: int, depth: int, num_heads: int, dropout: float):
        super().__init__()
        layer = nn.TransformerEncoderLayer(
            d_model=dim, nhead=num_heads, dim_feedforward=dim * 4,
            dropout=dropout, activation="gelu", batch_first=True, norm_first=True,
        )
        self.cls_token = nn.Parameter(torch.zeros(1, 1, dim))
        self.encoder   = nn.TransformerEncoder(layer, num_layers=depth)
        self.norm      = nn.LayerNorm(dim)
        self.pool      = AttentionPool(dim)
        init_trunc_normal(self.cls_token, std=0.02)

    def forward(self, tokens: torch.Tensor,
                mask: torch.Tensor) -> Tuple[torch.Tensor, torch.Tensor]:
        B   = tokens.size(0)
        cls = self.cls_token.expand(B, -1, -1)
        x   = torch.cat([cls, tokens], dim=1)
        x   = x + sinusoidal_position_encoding(x.size(1), x.size(2), x.device)
        cls_mask  = torch.ones(B, 1, dtype=torch.bool, device=x.device)
        full_mask = torch.cat([cls_mask, mask], dim=1)
        encoded   = self.encoder(x, src_key_padding_mask=~full_mask)
        encoded   = self.norm(encoded)
        cls_feat  = encoded[:, 0]
        pooled    = self.pool(encoded[:, 1:], mask)
        return cls_feat + pooled, encoded[:, 1:]


class HybridFPNFormer(nn.Module):
    def __init__(self, num_classes: int, config: SignModelConfig):
        super().__init__()
        self.config = config
        dim = config.embed_dim

        self.joint_input = nn.Sequential(
            nn.Linear(config.input_dim_joint, dim), nn.LayerNorm(dim),
            nn.GELU(), nn.Dropout(config.dropout),
        )
        self.bone_input = nn.Sequential(
            nn.Linear(config.input_dim_bone, dim), nn.LayerNorm(dim),
            nn.GELU(), nn.Dropout(config.dropout),
        )
        self.joint_fpn        = TemporalFPN(dim, config.dropout)
        self.bone_fpn         = TemporalFPN(dim, config.dropout)
        self.level_fusion     = nn.ModuleList([CrossStreamGate(dim, config.dropout)
                                               for _ in range(3)])
        self.token_assembler  = MultiScaleTokenAssembler(3, dim)
        self.temporal_encoder = TemporalTransformerEncoder(
            dim=dim, depth=config.transformer_depth,
            num_heads=config.num_heads, dropout=config.dropout,
        )
        self.joint_aux_head   = nn.Linear(dim, num_classes)
        self.bone_aux_head    = nn.Linear(dim, num_classes)
        self.student_aux_head = nn.Linear(dim, num_classes)

        self.legacy_teacher = LegacyTwoStreamTeacher(
            joint_dim=config.input_dim_joint, bone_dim=config.input_dim_bone,
            hidden_dim=config.legacy_hidden_dim, num_classes=num_classes,
            num_layers=config.legacy_num_layers, dropout=config.dropout,
        )
        self.teacher_loaded    = False
        self.teacher_projector = nn.Sequential(
            nn.Linear(config.legacy_hidden_dim, dim), nn.LayerNorm(dim),
            nn.GELU(), nn.Dropout(config.dropout),
        )
        self.teacher_gate = nn.Sequential(
            nn.Linear(dim * 2, dim), nn.GELU(), nn.Dropout(config.dropout),
            nn.Linear(dim, dim),     nn.Sigmoid(),
        )
        self.fusion_mlp = nn.Sequential(
            nn.Linear(dim * 3, dim * 2), nn.LayerNorm(dim * 2),
            nn.GELU(), nn.Dropout(config.dropout),
        )
        self.classifier = nn.Linear(dim * 2, num_classes)

    def forward(self, x_joint: torch.Tensor, x_bone: torch.Tensor,
                lengths: torch.Tensor) -> Dict[str, torch.Tensor]:
        jt = self.joint_input(x_joint)
        bt = self.bone_input(x_bone)

        jl, jlen = self.joint_fpn(jt, lengths)
        bl, _    = self.bone_fpn(bt, lengths)
        fused    = [f(j, b) for f, j, b in zip(self.level_fusion, jl, bl)]

        tokens, tmask   = self.token_assembler(fused, jlen)
        student_feat, _ = self.temporal_encoder(tokens, tmask)

        l0mask    = make_pad_mask(lengths, jl[0].size(1), jl[0].device)
        joint_aux = self.joint_aux_head(masked_mean(jl[0], l0mask))
        bone_aux  = self.bone_aux_head(masked_mean(bl[0],  l0mask))
        stu_aux   = self.student_aux_head(student_feat)

        teacher_logits    = None
        teacher_projected = torch.zeros_like(student_feat)
        if self.teacher_loaded:
            with torch.no_grad():
                teacher_logits, teacher_feat = self.legacy_teacher.forward_with_feature(
                    x_joint, x_bone, lengths
                )
            teacher_projected = self.teacher_projector(teacher_feat)

        gate         = self.teacher_gate(torch.cat([student_feat, teacher_projected], dim=-1))
        guided       = gate * student_feat + (1.0 - gate) * teacher_projected
        final_feat   = self.fusion_mlp(torch.cat([student_feat, guided, teacher_projected], dim=-1))
        logits       = self.classifier(final_feat)

        return {
            "logits":            logits,
            "teacher_logits":    teacher_logits,
            "student_alignment": student_feat,
            "teacher_alignment": teacher_projected if self.teacher_loaded else None,
            "aux_logits":        [joint_aux, bone_aux, stu_aux],
        }


# ========== 3. 特征构造（与训练脚本完全一致）==========

def build_joint_and_bone_features(
        raw_features: np.ndarray, clip_value: float
) -> Tuple[np.ndarray, np.ndarray]:
    """
    输入: raw_features (T, 258)  — [姿态33×4, 左手21×3, 右手21×3]
    输出: joint_feat  (T, 262),  bone_feat (T, 48)
    """
    f  = raw_features.astype(np.float32, copy=True)
    f  = np.nan_to_num(f, nan=0.0, posinf=0.0, neginf=0.0)

    nose_xy     = f[:, 0:2]
    leye_xy     = f[:, 8:10]
    reye_xy     = f[:, 20:22]
    mouth_xy    = (f[:, 36:38] + f[:, 40:42]) / 2.0
    rh_idx_xy   = f[:, 219:221]

    lsh = f[:, 44:46];  rsh = f[:, 48:50]
    sw0 = np.linalg.norm(lsh - rsh, axis=1, keepdims=True) + 1e-6

    d_nose  = np.linalg.norm(rh_idx_xy - nose_xy,  axis=1, keepdims=True) / sw0
    d_leye  = np.linalg.norm(rh_idx_xy - leye_xy,  axis=1, keepdims=True) / sw0
    d_reye  = np.linalg.norm(rh_idx_xy - reye_xy,  axis=1, keepdims=True) / sw0
    d_mouth = np.linalg.norm(rh_idx_xy - mouth_xy,  axis=1, keepdims=True) / sw0

    bx = (f[:, 44:45] + f[:, 48:49]) / 2.0
    by = (f[:, 45:46] + f[:, 49:50]) / 2.0
    sw = np.sqrt((f[:, 48:49] - f[:, 44:45])**2 +
                 (f[:, 49:50] - f[:, 45:46])**2) + 1e-6

    fn = f.copy()
    xi = np.arange(0, 132, 4);  yi = np.arange(1, 132, 4);  zi = np.arange(2, 132, 4)
    fn[:, xi] = (f[:, xi] - bx) / sw
    fn[:, yi] = (f[:, yi] - by) / sw
    fn[:, zi] = f[:, zi] / sw

    for s, e in [(132, 195), (195, 258)]:
        xi2 = np.arange(s,     e, 3)
        yi2 = np.arange(s + 1, e, 3)
        zi2 = np.arange(s + 2, e, 3)
        fn[:, xi2] = (f[:, xi2] - bx) / sw
        fn[:, yi2] = (f[:, yi2] - by) / sw
        fn[:, zi2] = f[:, zi2] / sw

    feat_joint = np.concatenate([fn, d_nose, d_leye, d_reye, d_mouth], axis=1)

    nose    = fn[:,   0:3];   l_sh  = fn[:, 44:47];  r_sh  = fn[:, 48:51]
    l_el    = fn[:, 52:55];   r_el  = fn[:, 56:59]
    l_wr    = fn[:, 60:63];   r_wr  = fn[:, 64:67]
    lhw     = fn[:, 132:135]; rhw   = fn[:, 195:198]
    lh_tips = [fn[:, 132 + i*3 : 132 + i*3 + 3] for i in [4, 8, 12, 16, 20]]
    rh_tips = [fn[:, 195 + i*3 : 195 + i*3 + 3] for i in [4, 8, 12, 16, 20]]

    bones = [l_sh - nose, r_sh - nose,
             l_el - l_sh, r_el - r_sh,
             l_wr - l_el, r_wr - r_el]
    bones.extend(t - lhw for t in lh_tips)
    bones.extend(t - rhw for t in rh_tips)
    feat_bone = np.concatenate(bones, axis=1)

    feat_joint = np.clip(np.nan_to_num(feat_joint), -clip_value, clip_value).astype(np.float32)
    feat_bone  = np.clip(np.nan_to_num(feat_bone),  -clip_value, clip_value).astype(np.float32)
    return feat_joint, feat_bone


# ========== 4. 服务端 MediaPipe 关键点提取 ==========
#
# 优先级（启动时自动选择，无需手动配置）：
#
#   ① MediaPipe Tasks API（需要 pose_landmarker.task + hand_landmarker.task 在项目根目录）
#      与 double_series_api_v7 训练管道完全一致 → 精度最高
#
#   ② MediaPipe classic Holistic（mp.solutions.holistic，Python 服务端运行）
#      不需要 .task 文件，visibility 为真实值（不是全 1.0）→ 精度次之
#      ★ 这是主要回退方案，保证在没有 .task 文件时也不会输出全零
#
#   ③ 均失败时：抛出 RuntimeError，不再静默返回全零导致"永远输出痒"

_pose_detector  = None
_hand_detector  = None
_holistic_model = None
_mp_mode        = "none"  # "tasks" | "holistic" | "none"


def _init_mediapipe_server():
    """
    启动时执行一次，初始化服务端 MediaPipe。

    优先级：
      ① Tasks API（需要 .task 文件）→ 精度最高
      ② classic Holistic（mp.solutions.holistic）→ 需要 mediapipe<=0.10.14
      ③ 均失败 → 给出明确修复指引
    """
    global _pose_detector, _hand_detector, _holistic_model, _mp_mode

    # ── 尝试 Tasks API ──
    try:
        import mediapipe as mp
        from mediapipe.tasks import python as mpp
        from mediapipe.tasks.python import vision as mpv

        # 项目根目录 = hospital_agent/
        _here     = os.path.dirname(os.path.abspath(__file__))        # .../backend/services/
        _root     = os.path.normpath(os.path.join(_here, "..", "..")) # .../hospital_agent/
        pose_path = os.path.join(_root, "pose_landmarker.task")
        hand_path = os.path.join(_root, "hand_landmarker.task")

        if os.path.exists(pose_path) and os.path.exists(hand_path):
            _pose_detector = mpv.PoseLandmarker.create_from_options(
                mpv.PoseLandmarkerOptions(
                    base_options=mpp.BaseOptions(model_asset_path=pose_path),
                    running_mode=mpv.RunningMode.IMAGE,
                    min_pose_detection_confidence=0.5,
                )
            )
            _hand_detector = mpv.HandLandmarker.create_from_options(
                mpv.HandLandmarkerOptions(
                    base_options=mpp.BaseOptions(model_asset_path=hand_path),
                    running_mode=mpv.RunningMode.IMAGE,
                    num_hands=2,
                    min_hand_detection_confidence=0.5,
                )
            )
            _mp_mode = "tasks"
            print(f"[SignModel] ✓ MediaPipe Tasks API 加载成功（路径: {_root}）")
            return
        else:
            print(f"[SignModel] .task 文件未找到，搜索路径: {_root}")
            print("[SignModel] 提示：将 pose_landmarker.task / hand_landmarker.task 放到上述目录可获得最高精度")
    except Exception as e:
        print(f"[SignModel] Tasks API 不可用: {e}")

    # ── 回退：classic Holistic（仅 mediapipe<=0.10.14 支持）──
    # ★ mediapipe 0.10.30+ 已移除 mp.solutions，需降级才能使用此回退
    try:
        import mediapipe as mp
        if not hasattr(mp, 'solutions'):
            raise AttributeError(
                f"当前 mediapipe 版本 ({getattr(mp, '__version__', '未知')}) "
                "已移除 mp.solutions（Holistic 等 Legacy API）。"
            )
        _holistic_model = mp.solutions.holistic.Holistic(
            static_image_mode=True,
            model_complexity=1,
            min_detection_confidence=0.5,
            min_tracking_confidence=0.5,
        )
        _mp_mode = "holistic"
        print("[SignModel] ✓ MediaPipe classic Holistic 加载成功（回退模式）")
        print("[SignModel] 注意：回退模式 visibility 为真实值，但坐标系与训练数据有微小差异")
    except Exception as e:
        _mp_mode = "none"
        print(f"[SignModel] ✗ MediaPipe Holistic 回退失败: {e}")
        print("[SignModel]")
        print("[SignModel] ╔══════════════════════════════════════════════════════╗")
        print("[SignModel] ║  请选择以下任一方式修复：                           ║")
        print("[SignModel] ║                                                      ║")
        print("[SignModel] ║  方案A（推荐，最简单）：                             ║")
        print("[SignModel] ║    pip install mediapipe==0.10.14                     ║")
        print("[SignModel] ║    （降级到支持 Holistic 的最后稳定版本）            ║")
        print("[SignModel] ║                                                      ║")
        print("[SignModel] ║  方案B（精度最高）：                                 ║")
        print("[SignModel] ║    下载 pose_landmarker.task 和 hand_landmarker.task  ║")
        print("[SignModel] ║    放到项目根目录 hospital_agent/                     ║")
        print("[SignModel] ║    下载地址：                                         ║")
        print("[SignModel] ║    https://storage.googleapis.com/mediapipe-models/   ║")
        print("[SignModel] ║      pose_landmarker/pose_landmarker_full/            ║")
        print("[SignModel] ║      float16/latest/pose_landmarker_full.task         ║")
        print("[SignModel] ║    https://storage.googleapis.com/mediapipe-models/   ║")
        print("[SignModel] ║      hand_landmarker/hand_landmarker/                 ║")
        print("[SignModel] ║      float16/latest/hand_landmarker.task              ║")
        print("[SignModel] ╚══════════════════════════════════════════════════════╝")


def _extract_tasks(frame_rgb: np.ndarray) -> np.ndarray:
    """Tasks API 提取单帧 258 维特征。"""
    import mediapipe as mp
    mp_img = mp.Image(image_format=mp.ImageFormat.SRGB, data=frame_rgb)

    pose = np.zeros(132, dtype=np.float32)
    try:
        r = _pose_detector.detect(mp_img)
        if r.pose_landmarks:
            pose = np.array(
                [[p.x, p.y, p.z, getattr(p, "visibility", 0.0)]
                 for p in r.pose_landmarks[0]], dtype=np.float32
            ).flatten()
    except Exception:
        pass

    lh = np.zeros(63, dtype=np.float32)
    rh = np.zeros(63, dtype=np.float32)
    try:
        r = _hand_detector.detect(mp_img)
        if r.hand_landmarks:
            for i, lms in enumerate(r.hand_landmarks):
                arr   = np.array([[p.x, p.y, p.z] for p in lms],
                                 dtype=np.float32).flatten()
                label = r.handedness[i][0].category_name
                if label == "Left":
                    lh = arr
                elif label == "Right":
                    rh = arr
    except Exception:
        pass

    return np.concatenate([pose, lh, rh])   # (258,)


def _extract_holistic(frame_rgb: np.ndarray) -> np.ndarray:
    """Classic Holistic 提取单帧 258 维特征（回退模式）。"""
    r = _holistic_model.process(frame_rgb)

    pose = np.zeros(132, dtype=np.float32)
    if r.pose_landmarks:
        pose = np.array(
            [[p.x, p.y, p.z, getattr(p, "visibility", 0.0)]
             for p in r.pose_landmarks.landmark], dtype=np.float32
        ).flatten()

    lh = np.zeros(63, dtype=np.float32)
    if r.left_hand_landmarks:
        lh = np.array(
            [[p.x, p.y, p.z] for p in r.left_hand_landmarks.landmark],
            dtype=np.float32
        ).flatten()

    rh = np.zeros(63, dtype=np.float32)
    if r.right_hand_landmarks:
        rh = np.array(
            [[p.x, p.y, p.z] for p in r.right_hand_landmarks.landmark],
            dtype=np.float32
        ).flatten()

    return np.concatenate([pose, lh, rh])   # (258,)


def extract_258_from_image_bytes(img_bytes: bytes) -> Optional[np.ndarray]:
    """
    从单帧图片字节提取 258 维关键点特征。
    返回 ndarray(258,) 或 None（图片解码失败时）。
    ★ 不再静默返回全零——无论哪种模式都执行真实 MediaPipe 检测。
    """
    nparr = np.frombuffer(img_bytes, np.uint8)
    frame = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
    if frame is None:
        return None

    if frame.ndim == 2:
        rgb = cv2.cvtColor(frame, cv2.COLOR_GRAY2RGB)
    elif frame.shape[2] == 4:
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGRA2RGB)
    else:
        rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)

    if _mp_mode == "tasks":
        return _extract_tasks(rgb)
    elif _mp_mode == "holistic":
        return _extract_holistic(rgb)
    else:
        return None   # MediaPipe 未初始化，调用方跳过此帧


# ========== 5. 推理封装类 ==========

class SignLanguageModel:
    def __init__(self, model_path: str, label_path: str, device: str = "cuda"):
        self.device = torch.device(device if torch.cuda.is_available() else "cpu")
        self.model       = None
        self.label_map   = None
        self.idx_to_word = None

        _init_mediapipe_server()   # 初始化服务端 MediaPipe（仅一次）
        self.load_model(model_path, label_path)

    def load_model(self, model_path: str, label_path: str):
        with open(label_path, "r", encoding="utf-8") as f:
            self.label_map = json.load(f)
        self.idx_to_word = {v: k for k, v in self.label_map.items()}
        num_classes = len(self.label_map)

        config     = SignModelConfig()
        self.model = HybridFPNFormer(num_classes, config).to(self.device)

        ckpt       = torch.load(model_path, map_location=self.device, weights_only=False)
        state_dict = ckpt.get("model_state_dict", ckpt)
        # 推理不需要 teacher 分支，过滤掉
        state_dict = {k: v for k, v in state_dict.items()
                      if not k.startswith("legacy_teacher")}
        self.model.load_state_dict(state_dict, strict=False)
        self.model.eval()
        print(f"[SignModel] ✓ 模型加载完成 | 类别={num_classes} | "
              f"设备={self.device} | 特征提取={_mp_mode}")

    # ──────────────────────────────────────────────────────────────
    #  主路径：前端发 base64 JPEG，后端用 MediaPipe 提取关键点
    # ──────────────────────────────────────────────────────────────
    def recognize_from_images(self, frames_b64: List[str],
                              trim_frames: int = 2) -> Tuple[str, float, List[Dict]]:
        if not frames_b64:
            return "", 0.0, []
        if _mp_mode == "none":
            raise RuntimeError(
                "MediaPipe 未初始化，无法提取关键点。"
                "请运行: pip install mediapipe opencv-python --break-system-packages"
            )

        raw_list = []
        for b64 in frames_b64:
            try:
                feat = extract_258_from_image_bytes(base64.b64decode(b64))
                if feat is not None:
                    raw_list.append(feat)
            except Exception as e:
                print(f"[SignModel] 帧解码失败: {e}")

        if len(raw_list) < 5:
            raise ValueError(f"有效帧太少（{len(raw_list)}帧），请重新比划")

        raw = np.array(raw_list, dtype=np.float32)
        return self._infer(self._trim(raw, trim_frames))

    # ──────────────────────────────────────────────────────────────
    #  备用路径：前端 Holistic 关键点数组（向后兼容）
    # ──────────────────────────────────────────────────────────────
    def recognize_from_keypoints(self, frames: List[List[float]],
                                 trim_frames: int = 2) -> Tuple[str, float, List[Dict]]:
        if not frames:
            return "", 0.0, []
        raw = self._kp_to_raw(frames)
        return self._infer(self._trim(raw, trim_frames))

    # ──────────────────────────────────────────────────────────────
    #  内部方法
    # ──────────────────────────────────────────────────────────────
    @staticmethod
    def _kp_to_raw(frames: List[List[float]]) -> np.ndarray:
        """
        将前端 Holistic 展平关键点转换为 (T, 258)。
        自动兼容 1629（旧版）和 1659（v0.8.7+ 含虹膜）两种帧长。
        """
        T   = len(frames)
        out = np.zeros((T, 258), dtype=np.float32)
        for t, frame in enumerate(frames):
            pose_xyz    = frame[:99]
            face_dim    = 1434 if len(frame) >= 1659 else 1404
            ls          = 99 + face_dim
            left_xyz    = frame[ls      : ls + 63]
            right_xyz   = frame[ls + 63 : ls + 126]
            for i in range(33):
                out[t, i*4]     = pose_xyz[i*3]
                out[t, i*4 + 1] = pose_xyz[i*3 + 1]
                out[t, i*4 + 2] = pose_xyz[i*3 + 2]
                out[t, i*4 + 3] = 1.0
            out[t, 132:195] = left_xyz
            out[t, 195:258] = right_xyz
        return out

    @staticmethod
    def _trim(raw: np.ndarray, trim: int) -> np.ndarray:
        if trim > 0 and len(raw) > trim * 2 + 5:
            raw = raw[trim:-trim]
        if len(raw) > 200:
            idx = np.linspace(0, len(raw) - 1, 200, dtype=int)
            raw = raw[idx]
        return raw

    def _infer(self, raw: np.ndarray) -> Tuple[str, float, List[Dict]]:
        zero_ratio = (raw == 0).sum() / raw.size
        hand_rate  = (raw[:, 132:258].sum(axis=1) != 0).mean()
        print(f"[SignModel] 质量检查 — 零值率={zero_ratio:.1%}, 手部检出率={hand_rate:.1%}")
        if zero_ratio > 0.85 or hand_rate < 0.1:
            raise ValueError(
                f"数据质量差（零值率={zero_ratio:.0%}, 手部检出率={hand_rate:.0%}）"
                "——请确保手部在摄像头中清晰可见"
            )

        jf, bf = build_joint_and_bone_features(raw, clip_value=10.0)

        jt  = torch.tensor(jf, dtype=torch.float32).unsqueeze(0).to(self.device)
        bt  = torch.tensor(bf, dtype=torch.float32).unsqueeze(0).to(self.device)
        lns = torch.tensor([jf.shape[0]], dtype=torch.long)

        with torch.no_grad():
            out   = self.model(jt, bt, lns)
            probs = F.softmax(out["logits"], dim=1)[0]

        top5 = torch.topk(probs, min(5, len(probs)))
        top_list = [
            {"label":      self.idx_to_word.get(idx.item(), f"?{idx.item()}"),
             "confidence": float(p),
             "rank":       i + 1}
            for i, (p, idx) in enumerate(zip(top5.values, top5.indices))
        ]
        pred_idx   = int(probs.argmax().item())
        confidence = float(probs[pred_idx])
        word       = self.idx_to_word.get(pred_idx, "未知")
        print(f"[SignModel] ✓ 识别结果: {word} ({confidence:.3f}) | 帧数={len(raw)}")
        return word, confidence, top_list

    # ── 向后兼容旧接口 ──
    def recognize(self, frames: List[List[float]]) -> Tuple[str, float]:
        word, conf, _ = self.recognize_from_keypoints(frames)
        return word, conf