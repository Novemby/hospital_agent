// 手语识别 Agent - 自动化版本：识别结果直接回调，无需点头确认
// 保留头部姿态检测（可选），但不再用于词汇确认
const MEDIAPIPE_VERSIONS = {
    cameraUtils: '0.3',
    drawingUtils: '0.3',
    controlUtils: '0.6',
    holistic: '0.5'
};

const HOLISTIC_BASE_URL = `https://cdn.jsdelivr.net/npm/@mediapipe/holistic@${MEDIAPIPE_VERSIONS.holistic}`;

export class SignLanguageAgent {
    constructor(options = {}) {
        this.options = {
            // 原有后端地址（序列管理、润色等） - 自动化模式下可能不再需要
            apiUrl: 'http://localhost:8000',
            // 新识别服务地址
            recognitionApiUrl: 'http://localhost:8000/api/sign',
            mode: 'isolated',
            // 识别结果回调（自动化发送）
            onWordRecognized: () => {},   // 新增：直接返回识别到的词汇
            onSentenceComplete: () => {},  // 新增：句子完成回调
            // 保留旧回调兼容
            onResult: () => {},
            onSequenceUpdate: () => {},
            onStatusChange: () => {},
            onHeadGesture: () => {},
            ...options
        };
        this.apiUrl = this.options.apiUrl;
        this.recognitionApiUrl = this.options.recognitionApiUrl;
        this.mode = this.options.mode;

        // MediaPipe 相关
        this.webcam = null;
        this.canvas = null;
        this.ctx = null;
        this.camera = null;
        this.holistic = null;
        this.cameraStarted = false;

        // 孤立词识别相关
        this.isRecording = false;
        this.keypointsBuffer = [];   // 存储每帧的关键点数组
        this.frameCount = 0;
        this.lastCaptureTime = 0;

        // 滑动窗口参数
        this.WINDOW_SIZE = 45;         // 每个窗口的帧数 (~1.5s at 30fps)
        this.WINDOW_STRIDE = 15;       // 窗口滑动步长 (~0.5s)
        this.MAX_WINDOWS = 5;          // 最大窗口数量，限制API调用次数

        // 滑动窗口投票（连续识别模式下禁用，直接显示结果）
        this.predictionBuffer = [];
        this.BUFFER_SIZE = 3;
        this.lastDisplayedPrediction = null;
        this.lastRecognizedWords = [];  // 记录最近识别的词，防止短时间内重复

        // 连续识别模式
        this.continuousMode = false;
        this.noHandFrames = 0;
        this.NO_HAND_THRESHOLD = 15;   // ~0.5s，手部消失多少帧后触发识别
        this.MIN_SIGN_FRAMES = 10;     // 有效手势最少帧数
        this.MAX_SIGN_FRAMES = 150;    // 最大帧数（~5s），防止无限录制

        // 句子级别的端点检测
        this.sentenceWords = [];       // 累积的词汇
        this.sentenceTimer = null;     // 句子超时计时器
        this.SENTENCE_TIMEOUT = 2000;  // 2秒无新词汇则认为句子结束（缩短以便快速发送）
        this.WORD_DEDUPE_WINDOW = 1500; // 1.5秒内相同词汇去重

        // 词序列（可选，自动化模式下可能不再使用）
        this.wordSequence = [];

        // 头部姿态检测（保留但不再用于确认词汇，可单独用于其他交互）
        this.headGestureEnabled = false;
        this.lastHeadPose = { pitch: 0, yaw: 0 };
        this.gestureDebounceTimer = null;
        this.gestureCooldown = 500;
        this.gestureThreshold = 15;
        this.gestureStableFrames = 3;

        // Holistic 连接常量（绘制用）
        this.POSE_CONNECTIONS = [[0,1],[1,2],[2,3],[3,7],[0,4],[4,5],[5,6],[6,8],[9,10],[11,12],[11,13],[13,15],[12,14],[14,16],[11,23],[12,24],[23,24]];
        this.HAND_CONNECTIONS = [[0,1],[1,2],[2,3],[3,4],[0,5],[5,6],[6,7],[7,8],[0,9],[9,10],[10,11],[11,12],[0,13],[13,14],[14,15],[15,16],[0,17],[17,18],[18,19],[19,20],[5,9],[9,13],[13,17]];

        this.initHolistic = this.initHolistic.bind(this);
        this.onHolisticResults = this.onHolisticResults.bind(this);
        this.extractLandmarks = this.extractLandmarks.bind(this);
        this.detectHeadGesture = this.detectHeadGesture.bind(this);
    }

    initHolistic() {
        this.holistic = new Holistic({
            locateFile: (file) => `${HOLISTIC_BASE_URL}/${file}`
        });
        this.holistic.setOptions({
            modelComplexity: 1,
            smoothLandmarks: true,
            minDetectionConfidence: 0.5,
            minTrackingConfidence: 0.5,
            refineFaceLandmarks: true
        });
        this.holistic.onResults(this.onHolisticResults);
    }

    // 按训练时的输入格式提取关键点：
    // pose 33 * (x, y, z, visibility) + 左手 21 * (x, y, z) + 右手 21 * (x, y, z) = 258 维。
    extractLandmarks(results) {
        const frame = [];
        // 姿态点 (33个，保留 visibility)
        if (results.poseLandmarks) {
            for (const lm of results.poseLandmarks) {
                frame.push(lm.x, lm.y, lm.z, lm.visibility ?? 0);
            }
        } else {
            for (let i = 0; i < 33; i++) frame.push(0, 0, 0, 0);
        }
        // 左手 (21个)
        if (results.leftHandLandmarks) {
            for (const lm of results.leftHandLandmarks) {
                frame.push(lm.x, lm.y, lm.z);
            }
        } else {
            for (let i = 0; i < 21; i++) frame.push(0, 0, 0);
        }
        // 右手 (21个)
        if (results.rightHandLandmarks) {
            for (const lm of results.rightHandLandmarks) {
                frame.push(lm.x, lm.y, lm.z);
            }
        } else {
            for (let i = 0; i < 21; i++) frame.push(0, 0, 0);
        }
        return frame; // 长度应为 132 + 63 + 63 = 258
    }

    onHolisticResults(results) {
        if (!this.canvas) return;
        this.canvas.width = this.webcam.videoWidth;
        this.canvas.height = this.webcam.videoHeight;
        this.ctx.save();
        this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
        this.ctx.drawImage(results.image, 0, 0);

        // 绘制关节点
        if (results.poseLandmarks) {
            drawConnectors(this.ctx, results.poseLandmarks, this.POSE_CONNECTIONS, { color: '#00FF8866', lineWidth: 2 });
        }
        if (results.leftHandLandmarks) {
            drawConnectors(this.ctx, results.leftHandLandmarks, this.HAND_CONNECTIONS, { color: '#FF6B6B', lineWidth: 3 });
            drawLandmarks(this.ctx, results.leftHandLandmarks, { color: '#FF6B6B', radius: 3 });
        }
        if (results.rightHandLandmarks) {
            drawConnectors(this.ctx, results.rightHandLandmarks, this.HAND_CONNECTIONS, { color: '#4ECDC4', lineWidth: 3 });
            drawLandmarks(this.ctx, results.rightHandLandmarks, { color: '#4ECDC4', radius: 3 });
        }

        // 头部姿态检测（可选，但不再用于确认词汇）
        if (this.headGestureEnabled && results.faceLandmarks) {
            this.detectHeadGesture(results.faceLandmarks);
        }

        this.ctx.restore();

        // 录制时捕获关键点数据
        if (this.isRecording) {
            const landmarks = this.extractLandmarks(results);
            this.keypointsBuffer.push(landmarks);
            this.frameCount++;
            this.options.onStatusChange('recording', { frameCount: this.frameCount });
        }

        // 连续识别逻辑
        if (this.continuousMode) {
            const handsPresent = !!(results.leftHandLandmarks || results.rightHandLandmarks);
            if (handsPresent) {
                this.noHandFrames = 0;
                if (!this.isRecording) {
                    this.startRecording();
                }
            } else if (this.isRecording) {
                this.noHandFrames++;
                if (this.noHandFrames >= this.NO_HAND_THRESHOLD) {
                    if (this.keypointsBuffer.length >= this.MIN_SIGN_FRAMES) {
                        this.stopRecording();
                    } else {
                        // 帧数不足，静默丢弃
                        this.isRecording = false;
                        this.keypointsBuffer = [];
                        this.frameCount = 0;
                        this.noHandFrames = 0;
                        this.options.onStatusChange('continuousReady');
                    }
                }
            }
            // 防止录制超时
            if (this.isRecording && this.keypointsBuffer.length >= this.MAX_SIGN_FRAMES) {
                this.stopRecording();
            }
        }
    }

    detectHeadGesture(faceLandmarks) {
        // 使用左右眼中心和鼻尖估算头部姿态
        const leftEye = faceLandmarks[33];
        const rightEye = faceLandmarks[263];
        const nose = faceLandmarks[1];

        const eyeCenterX = (leftEye.x + rightEye.x) / 2;
        const yaw = (nose.x - eyeCenterX) * 180;
        const eyeCenterY = (leftEye.y + rightEye.y) / 2;
        const pitch = (nose.y - eyeCenterY) * 180;

        const deltaPitch = pitch - this.lastHeadPose.pitch;
        const deltaYaw = yaw - this.lastHeadPose.yaw;
        this.lastHeadPose = { pitch, yaw };

        if (Math.abs(deltaPitch) > this.gestureThreshold) {
            this.triggerGesture('nod');
        } else if (Math.abs(deltaYaw) > this.gestureThreshold) {
            this.triggerGesture('shake');
        }
    }

    triggerGesture(type) {
        if (this.gestureDebounceTimer) return;
        this.options.onHeadGesture({ type });
        this.gestureDebounceTimer = setTimeout(() => {
            this.gestureDebounceTimer = null;
        }, this.gestureCooldown);
    }

    enableHeadGesture(enable) {
        this.headGestureEnabled = enable;
    }

    async startCamera(videoElementId, canvasElementId) {
        if (this.cameraStarted) return;
        this.webcam = document.getElementById(videoElementId);
        this.canvas = document.getElementById(canvasElementId);
        if (!this.webcam || !this.canvas) throw new Error('Video or canvas element not found');
        this.ctx = this.canvas.getContext('2d');
        this.initHolistic();
        this.camera = new Camera(this.webcam, {
            onFrame: async () => { await this.holistic.send({ image: this.webcam }); },
            width: 640,
            height: 480
        });
        await this.camera.start();
        this.cameraStarted = true;
        this.options.onStatusChange('cameraStarted', true);
    }

    startRecording() {
        if (!this.cameraStarted) return;
        this.isRecording = true;
        this.keypointsBuffer = [];
        this.frameCount = 0;
        this.lastCaptureTime = 0;
        this.clearPredictionBuffer();
        this.options.onStatusChange('recordingStart');
    }

    // 向后端发送一段帧序列，返回 { word, confidence } 或 null
    async _sendFrames(frames) {
        try {
            const response = await fetch(`${this.recognitionApiUrl}/recognize`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ frames: frames.map(f => f.flat()) })
            });
            const result = await response.json();
            if (response.ok && result.word) {
                return { word: result.word, confidence: result.confidence || 0.8 };
            }
            return null;
        } catch (e) {
            return null;
        }
    }

    async stopRecording() {
        if (!this.isRecording) return;
        this.isRecording = false;
        const buffer = this.keypointsBuffer;
        if (buffer.length < 5) {
            this.options.onStatusChange('tooShort');
            return;
        }
        this.options.onStatusChange('sending', { frameCount: buffer.length });
        try {
            let predictions = [];

            if (buffer.length <= this.WINDOW_SIZE) {
                // 帧数较少，直接发送整段
                const r = await this._sendFrames(buffer);
                if (r) predictions.push(r);
            } else {
                // 滑动窗口：生成多个重叠子窗口 + 完整序列，并行发送
                const windowBufs = [];
                for (let start = 0; start + this.WINDOW_SIZE <= buffer.length; start += this.WINDOW_STRIDE) {
                    windowBufs.push(buffer.slice(start, start + this.WINDOW_SIZE));
                    if (windowBufs.length >= this.MAX_WINDOWS) break;
                }
                windowBufs.push(buffer); // 完整序列也参与投票
                const results = await Promise.all(windowBufs.map(w => this._sendFrames(w)));
                predictions = results.filter(Boolean);
            }

            if (!predictions.length) {
                this.options.onStatusChange('recognitionFailed');
                return;
            }

            // 多窗口投票：选出出现次数最多的词
            const counts = {};
            let bestWord = predictions[0].word, bestConf = predictions[0].confidence, maxCount = 0;
            for (const p of predictions) {
                counts[p.word] = (counts[p.word] || 0) + 1;
                if (counts[p.word] > maxCount) {
                    maxCount = counts[p.word];
                    bestWord = p.word;
                    bestConf = p.confidence;
                }
            }

            this.displayResult(bestWord, bestConf);
            this.options.onResult({ prediction: bestWord, confidence: bestConf, topPredictions: [] });
            this.options.onStatusChange('resultReady', { prediction: bestWord, confidence: bestConf, topPredictions: [] });
            if (this.continuousMode) {
                this.options.onStatusChange('continuousReady');
            }
        } catch (error) {
            console.error('Recognition error:', error);
            this.options.onStatusChange('networkError', { message: error.message || String(error) });
        }
    }

    displayResult(prediction, confidence) {
        // 连续识别模式：跳过稳定性投票，直接输出，仅做短时去重
        if (this.continuousMode) {
            const now = Date.now();
            // 检查最近 WORD_DEDUPE_WINDOW 内是否已识别过相同词
            this.lastRecognizedWords = this.lastRecognizedWords.filter(
                r => now - r.time < this.WORD_DEDUPE_WINDOW
            );
            const isDupe = this.lastRecognizedWords.some(r => r.word === prediction);
            if (!isDupe) {
                this.lastRecognizedWords.push({ word: prediction, time: now });
                this.options.onWordRecognized(prediction, confidence);
                this.sentenceWords.push(prediction);
                if (this.sentenceTimer) clearTimeout(this.sentenceTimer);
                this.sentenceTimer = setTimeout(() => {
                    if (this.sentenceWords.length > 0) {
                        this.options.onSentenceComplete([...this.sentenceWords]);
                        this.sentenceWords = [];
                    }
                    this.sentenceTimer = null;
                }, this.SENTENCE_TIMEOUT);
            }
            this.options.onResult({ prediction, confidence, topPredictions: [] });
            return;
        }

        // 非连续模式：保留原有稳定性投票逻辑
        this.predictionBuffer.push(prediction);
        if (this.predictionBuffer.length > this.BUFFER_SIZE) this.predictionBuffer.shift();
        const counts = {};
        let maxCount = 0, finalPrediction = prediction;
        for (const p of this.predictionBuffer) {
            counts[p] = (counts[p] || 0) + 1;
            if (counts[p] > maxCount) {
                maxCount = counts[p];
                finalPrediction = p;
            }
        }
        if (maxCount >= Math.ceil(this.BUFFER_SIZE / 2)) {
            if (this.lastDisplayedPrediction !== finalPrediction) {
                this.lastDisplayedPrediction = finalPrediction;
                this.options.onWordRecognized(finalPrediction, confidence);
                this.sentenceWords.push(finalPrediction);
                if (this.sentenceTimer) clearTimeout(this.sentenceTimer);
                this.sentenceTimer = setTimeout(() => {
                    if (this.sentenceWords.length > 0) {
                        this.options.onSentenceComplete([...this.sentenceWords]);
                        this.sentenceWords = [];
                    }
                    this.sentenceTimer = null;
                }, this.SENTENCE_TIMEOUT);
            }
            this.options.onResult({ prediction: finalPrediction, confidence, topPredictions: [] });
        } else {
            this.options.onResult({ prediction: `${prediction}?`, confidence, topPredictions: [] });
        }
    }

    clearPredictionBuffer() {
        this.predictionBuffer = [];
        this.lastDisplayedPrediction = null;
    }

    clearSentenceBuffer() {
        if (this.sentenceTimer) {
            clearTimeout(this.sentenceTimer);
            this.sentenceTimer = null;
        }
        this.sentenceWords = [];
    }

    startContinuous() {
        if (!this.cameraStarted) return;
        this.continuousMode = true;
        this.noHandFrames = 0;
        this.options.onStatusChange('continuousStart');
    }

    stopContinuous() {
        this.continuousMode = false;
        if (this.isRecording) {
            this.isRecording = false;
            this.keypointsBuffer = [];
            this.frameCount = 0;
        }
        this.noHandFrames = 0;
        this.options.onStatusChange('continuousStop');
    }

    // 以下方法保留但自动化模式下可能不再需要
    async confirmWord(label, isAuto = false) {
        try {
            // 调用原有后端确认词接口（序列管理）
            const response = await fetch(`${this.apiUrl}/confirm_word`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ label })
            });
            const result = await response.json();
            if (result.success) {
                this.wordSequence = result.sequence;
                this.options.onSequenceUpdate(this.wordSequence);
                this.options.onStatusChange('wordConfirmed', { word: label, isAuto });
                this.clearPredictionBuffer();
            } else {
                this.options.onStatusChange('confirmFailed');
            }
        } catch (error) {
            console.error('Confirm word error:', error);
            this.options.onStatusChange('networkError');
        }
    }

    async clearSequence() {
        this.wordSequence = [];
        this.options.onSequenceUpdate(this.wordSequence);
        try {
            await fetch(`${this.apiUrl}/clear_sequence`, { method: 'POST' });
        } catch (e) {}
    }

    async polishSequence() {
        if (this.wordSequence.length === 0) {
            this.options.onStatusChange('emptySequence');
            return null;
        }
        try {
            const response = await fetch(`${this.apiUrl}/polish_and_triage`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ words: this.wordSequence, context: '急诊分诊' })
            });
            const result = await response.json();
            if (result.success) {
                return { polished: result.polished, triage: result.triage };
            } else {
                throw new Error(result.error);
            }
        } catch (error) {
            console.error('Polish error:', error);
            this.options.onStatusChange('polishFailed');
            return null;
        }
    }

    stopCamera() {
        if (this.camera) {
            this.camera.stop();
            this.cameraStarted = false;
        }
    }

    getSequence() {
        return [...this.wordSequence];
    }

    async addWord(word) {
        try {
            const response = await fetch(`${this.apiUrl}/confirm_word`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ label: word })
            });
            const result = await response.json();
            if (result.success) {
                this.wordSequence = result.sequence;
                this.options.onSequenceUpdate(this.wordSequence);
                this.options.onStatusChange('wordAdded', { word });
            }
        } catch (error) {}
    }

    async deleteWord(index) {
        if (index >= 0 && index < this.wordSequence.length) {
            this.wordSequence.splice(index, 1);
            this.options.onSequenceUpdate(this.wordSequence);
            try {
                await fetch(`${this.apiUrl}/clear_sequence`, { method: 'POST' });
                for (const w of this.wordSequence) {
                    await fetch(`${this.apiUrl}/confirm_word`, {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ label: w })
                    });
                }
            } catch (e) {}
        }
    }
}
