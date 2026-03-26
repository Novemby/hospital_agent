/**
 * frontend/js/services/signLanguageAgent.js
 *
 * 手语识别核心类（修复版）
 *
 * ★ 修复说明：
 *   1. 接口与 handsign_assist.js 的调用方式完全对齐
 *      - startCamera(videoId, canvasId) 替代旧版 init()
 *      - stopCamera() 释放资源
 *      - 回调统一为 onResult / onStatusChange / onSequenceUpdate / onHeadGesture
 *   2. 端口统一为 http://localhost:8000
 *   3. 保留 base64 JPEG 帧发送逻辑（服务端 MediaPipe 提取关键点）
 *
 * 架构：
 *   前端 MediaPipe Holistic ──► 仅用于骨架可视化
 *   前端录制画面 → base64 JPEG ──► 后端 MediaPipe（Python）提取关键点 → 推理
 *
 * 依赖（HTML 中 CDN 引入）：
 *   @mediapipe/holistic@0.5.1675471629
 *   @mediapipe/camera_utils
 *   @mediapipe/drawing_utils
 */

class SignLanguageAgent {
    /**
     * @param {Object}   opts
     * @param {string}   opts.apiUrl               后端地址，默认 'http://localhost:8000'
     * @param {string}   opts.endpoint              识别接口路径，默认 '/api/sign/recognize'
     * @param {number}   opts.captureIntervalMs     录制帧间隔(ms)，默认 100（约 10fps）
     * @param {number}   opts.jpegQuality           截图质量 0~1，默认 0.7
     * @param {number}   opts.trimFrames            裁剪首尾过渡帧，默认 2
     * @param {number}   opts.confidenceThreshold   置信度阈值，默认 0.3
     * @param {Function} opts.onResult              识别结果回调
     * @param {Function} opts.onStatusChange        状态变化回调 (status, data)
     * @param {Function} opts.onSequenceUpdate      词序列变化回调 (sequence)
     * @param {Function} opts.onHeadGesture         头部手势回调 ({type})
     */
    constructor(opts = {}) {
        // ── 统一端口为 8000 ──
        this.apiUrl              = opts.apiUrl              || 'http://localhost:8000';
        this.endpoint            = opts.endpoint            || '/api/sign/recognize';
        this.captureIntervalMs   = opts.captureIntervalMs   || 100;
        this.jpegQuality         = opts.jpegQuality         || 0.7;
        this.trimFrames          = opts.trimFrames          || 2;
        this.confidenceThreshold = opts.confidenceThreshold || 0.3;

        // 回调 —— 与 handsign_assist.js 的调用方式完全对齐
        this.onResult         = opts.onResult         || (() => {});
        this.onStatusChange   = opts.onStatusChange   || (() => {});
        this.onSequenceUpdate = opts.onSequenceUpdate || (() => {});
        this.onHeadGesture    = opts.onHeadGesture    || (() => {});

        // 内部状态
        this._isRecording    = false;
        this._frames         = [];           // base64 JPEG 字符串列表
        this._lastCapture    = 0;
        this._holistic       = null;
        this._camera         = null;
        this._snapCanvas     = document.createElement('canvas');
        this._videoEl        = null;
        this._canvasEl       = null;
        this._headGestureOn  = false;

        // 词序列（确认后的词列表）
        this._wordSequence   = [];
    }

    // ════════════════════════════════════════════════════════
    //  公开属性
    // ════════════════════════════════════════════════════════

    get isRecording() { return this._isRecording; }
    get frameCount()  { return this._frames.length; }

    // ════════════════════════════════════════════════════════
    //  摄像头控制（与 handsign_assist.js 调用方式对齐）
    // ════════════════════════════════════════════════════════

    /**
     * 启动摄像头 + 初始化 MediaPipe Holistic（骨架可视化）。
     * @param {string} videoId   - <video> 元素的 id
     * @param {string} canvasId  - <canvas> 元素的 id
     */
    async startCamera(videoId, canvasId) {
        this._videoEl  = document.getElementById(videoId);
        this._canvasEl = document.getElementById(canvasId);

        if (!this._videoEl || !this._canvasEl) {
            throw new Error(`[SignAgent] 找不到元素: video=#${videoId}, canvas=#${canvasId}`);
        }

        // ★ 锁定 Holistic 版本，仅用于骨架可视化
        this._holistic = new Holistic({
            locateFile: (file) =>
                `https://cdn.jsdelivr.net/npm/@mediapipe/holistic@0.5.1675471629/${file}`
        });
        this._holistic.setOptions({
            modelComplexity:        1,
            smoothLandmarks:        true,
            minDetectionConfidence: 0.5,
            minTrackingConfidence:  0.5,
            refineFaceLandmarks:    false,
        });
        this._holistic.onResults((r) => this._onHolisticResults(r));

        this._camera = new Camera(this._videoEl, {
            onFrame: async () => {
                await this._holistic.send({ image: this._videoEl });
            },
            width: 640,
            height: 480,
        });
        await this._camera.start();
        console.log('[SignAgent] ✓ 摄像头已启动');
    }

    /** 停止摄像头，释放资源 */
    stopCamera() {
        if (this._camera) {
            try { this._camera.stop(); } catch (_) {}
            this._camera = null;
        }
        if (this._holistic) {
            try { this._holistic.close(); } catch (_) {}
            this._holistic = null;
        }
        console.log('[SignAgent] 摄像头已停止');
    }

    // ════════════════════════════════════════════════════════
    //  录制控制
    // ════════════════════════════════════════════════════════

    /** 开始录制 */
    startRecording() {
        if (this._isRecording) return;
        this._frames      = [];
        this._lastCapture = 0;
        this._isRecording = true;
        this.onStatusChange('recordingStart', {});
        console.log('[SignAgent] 开始录制');
    }

    /** 停止录制 → 自动发送识别请求 */
    async stopRecording() {
        if (!this._isRecording) return;
        this._isRecording = false;

        const frames = [...this._frames];
        this._frames = [];

        console.log(`[SignAgent] 停止录制，共 ${frames.length} 帧`);

        if (frames.length < 5) {
            this.onStatusChange('tooShort', { frameCount: frames.length });
            return;
        }

        this.onStatusChange('sending', { frameCount: frames.length });

        try {
            const result = await this._sendFrames(frames);

            if (result.success) {
                // 回调结果 —— 字段名与 handsign_assist.js 期望一致
                this.onResult({
                    prediction:     result.prediction,
                    confidence:     result.confidence,
                    topPredictions: result.top_predictions || [],
                    mode:           result.mode || '',
                    frameCount:     result.frame_count || frames.length,
                });
                this.onStatusChange('resultReady', {
                    prediction: result.prediction,
                    confidence: result.confidence,
                });
            } else {
                this.onStatusChange('recognitionFailed', { error: result.error });
            }
        } catch (e) {
            console.error('[SignAgent] 网络错误:', e);
            this.onStatusChange('networkError', { error: e.message });
        }
    }

    /** 取消录制（不发送） */
    cancelRecording() {
        this._frames      = [];
        this._isRecording = false;
    }

    // ════════════════════════════════════════════════════════
    //  词序列管理（确认 / 删除 / 清空 / 润色）
    // ════════════════════════════════════════════════════════

    /** 确认一个词，加入词序列 */
    confirmWord(word) {
        if (!word) return;
        this._wordSequence.push(word);
        this.onSequenceUpdate([...this._wordSequence]);
        this.onStatusChange('wordConfirmed', { word, isAuto: false });
    }

    /** 自动确认（超时自动选第一候选） */
    autoConfirmWord(word) {
        if (!word) return;
        this._wordSequence.push(word);
        this.onSequenceUpdate([...this._wordSequence]);
        this.onStatusChange('wordConfirmed', { word, isAuto: true });
    }

    /** 删除序列中指定位置的词 */
    deleteWord(index) {
        if (index >= 0 && index < this._wordSequence.length) {
            this._wordSequence.splice(index, 1);
            this.onSequenceUpdate([...this._wordSequence]);
        }
    }

    /** 清空词序列 */
    clearSequence() {
        this._wordSequence = [];
        this.onSequenceUpdate([]);
    }

    /** 润色词序列（调用后端 AI 润色接口，如无则本地拼接） */
    async polishSequence() {
        if (this._wordSequence.length === 0) {
            this.onStatusChange('emptySequence', {});
            return null;
        }

        const text = this._wordSequence.join(' ');

        // 尝试调用后端润色接口
        try {
            const resp = await fetch(`${this.apiUrl}/api/sign/polish`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ words: this._wordSequence }),
            });
            if (resp.ok) {
                return await resp.json();
            }
        } catch (_) {
            // 后端无 polish 接口时降级
        }

        // 降级：直接返回拼接文本
        return {
            polished: text,
            original: this._wordSequence,
            triage: null,
        };
    }

    // ════════════════════════════════════════════════════════
    //  头部手势（点头/摇头检测 — 简化实现）
    // ════════════════════════════════════════════════════════

    /** 启用/禁用头部手势检测 */
    enableHeadGesture(enabled) {
        this._headGestureOn = !!enabled;
        if (enabled) {
            this._headHistory = [];
        }
    }

    // ════════════════════════════════════════════════════════
    //  内部方法
    // ════════════════════════════════════════════════════════

    /**
     * Holistic 每帧回调：
     *   1. 绘制骨架（可视化）
     *   2. 录制时截取原始画面为 base64 JPEG
     *   3. 头部手势检测（如果启用）
     */
    _onHolisticResults(results) {
        const ctx = this._canvasEl.getContext('2d');
        this._canvasEl.width  = this._videoEl.videoWidth  || 640;
        this._canvasEl.height = this._videoEl.videoHeight || 480;

        ctx.save();
        ctx.clearRect(0, 0, this._canvasEl.width, this._canvasEl.height);
        ctx.drawImage(results.image, 0, 0, this._canvasEl.width, this._canvasEl.height);

        // 骨架可视化
        if (results.poseLandmarks) {
            drawConnectors(ctx, results.poseLandmarks, POSE_CONNECTIONS,
                { color: '#00FF8866', lineWidth: 2 });
        }
        if (results.leftHandLandmarks) {
            drawConnectors(ctx, results.leftHandLandmarks, HAND_CONNECTIONS,
                { color: '#FF6B6B', lineWidth: 3 });
            drawLandmarks(ctx, results.leftHandLandmarks,
                { color: '#FF6B6B', radius: 3 });
        }
        if (results.rightHandLandmarks) {
            drawConnectors(ctx, results.rightHandLandmarks, HAND_CONNECTIONS,
                { color: '#4ECDC4', lineWidth: 3 });
            drawLandmarks(ctx, results.rightHandLandmarks,
                { color: '#4ECDC4', radius: 3 });
        }
        ctx.restore();

        // ── 录制帧 ──
        if (this._isRecording) {
            const now = Date.now();
            if (now - this._lastCapture < this.captureIntervalMs) return;
            this._lastCapture = now;

            this._snapCanvas.width  = this._canvasEl.width;
            this._snapCanvas.height = this._canvasEl.height;
            this._snapCanvas.getContext('2d')
                .drawImage(results.image, 0, 0,
                           this._snapCanvas.width, this._snapCanvas.height);

            const b64 = this._snapCanvas
                .toDataURL('image/jpeg', this.jpegQuality)
                .split(',')[1];
            this._frames.push(b64);
        }

        // ── 头部手势检测（简化版：基于鼻尖位移） ──
        if (this._headGestureOn && results.poseLandmarks) {
            this._detectHeadGesture(results.poseLandmarks);
        }
    }

    /**
     * 简化的头部手势检测：
     *   - 点头：鼻尖 y 坐标短时间内有明显上下波动
     *   - 摇头：鼻尖 x 坐标短时间内有明显左右波动
     */
    _detectHeadGesture(poseLandmarks) {
        const nose = poseLandmarks[0]; // 鼻尖
        if (!this._headHistory) this._headHistory = [];

        this._headHistory.push({ x: nose.x, y: nose.y, t: Date.now() });

        // 只保留最近 1 秒的数据
        const cutoff = Date.now() - 1000;
        this._headHistory = this._headHistory.filter(h => h.t > cutoff);

        if (this._headHistory.length < 8) return;

        const xs = this._headHistory.map(h => h.x);
        const ys = this._headHistory.map(h => h.y);
        const xRange = Math.max(...xs) - Math.min(...xs);
        const yRange = Math.max(...ys) - Math.min(...ys);

        const NOD_THRESHOLD   = 0.04;
        const SHAKE_THRESHOLD = 0.05;

        if (yRange > NOD_THRESHOLD && yRange > xRange * 1.5) {
            this._headHistory = [];
            this.onHeadGesture({ type: 'nod' });
        } else if (xRange > SHAKE_THRESHOLD && xRange > yRange * 1.5) {
            this._headHistory = [];
            this.onHeadGesture({ type: 'shake' });
        }
    }

    /** 发送 base64 帧到后端识别接口 */
    async _sendFrames(frames) {
        const resp = await fetch(`${this.apiUrl}${this.endpoint}`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                frames_b64:           frames,
                trim_frames:          this.trimFrames,
                confidence_threshold: this.confidenceThreshold,
            }),
        });
        if (!resp.ok) {
            throw new Error(`HTTP ${resp.status}: ${resp.statusText}`);
        }
        return resp.json();
    }
}

export { SignLanguageAgent };