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
            // "开始结束"手势切换回调：参数为新状态 true=开始识别 false=停止识别
            onToggleByGesture: () => {},
            cameraWidth: 640,
            cameraHeight: 480,
            drawPose: true,
            drawHands: true,
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
        this.lastCanvasWidth = 0;
        this.lastCanvasHeight = 0;

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

        // 速度检测参数
        this.lastHandPositions = null;  // 上一帧的手部位置 {left: {x, y, z}, right: {x, y, z}}
        this.velocityBuffer = [];       // 速度历史缓冲区
        this.VELOCITY_BUFFER_SIZE = 5;  // 速度平滑窗口
        this.LOW_VELOCITY_THRESHOLD = 0.01;  // 低速阈值（归一化坐标）
        this.LOW_VELOCITY_FRAMES = 8;   // 连续低速帧数阈值
        this.lowVelocityCount = 0;      // 当前连续低速帧计数

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

        // 后台"开始结束"手势监听（摄像头开启后始终运行，与 continuousMode 无关）
        this.watchState = 'WATCH_IDLE';   // 'WATCH_IDLE' | 'WATCH_COLLECTING'
        this.watchBuffer = [];
        this.watchStopCount = 0;
        this.watchLastCapture = 0;
        this.watchPending = false;
        this.watchLastHandPositions = null;  // 独立速度计算，不与连续模式共享
        this.WATCH_START_THRESHOLD = 0.025;
        this.WATCH_STOP_THRESHOLD  = 0.008;
        this.WATCH_STOP_FRAMES     = 8;
        this.WATCH_MIN_FRAMES      = 10;
        this.WATCH_MAX_FRAMES      = 80;

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
            modelComplexity: 0,
            smoothLandmarks: true,
            minDetectionConfidence: 0.5,
            minTrackingConfidence: 0.5,
            refineFaceLandmarks: false
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

    // 计算手部中心位置（用于速度检测）
    calculateHandCenter(handLandmarks) {
        if (!handLandmarks || handLandmarks.length === 0) return null;
        let sumX = 0, sumY = 0, sumZ = 0;
        for (const lm of handLandmarks) {
            sumX += lm.x;
            sumY += lm.y;
            sumZ += lm.z;
        }
        const n = handLandmarks.length;
        return { x: sumX / n, y: sumY / n, z: sumZ / n };
    }

    // 计算两个3D点之间的欧氏距离
    calculateDistance(p1, p2) {
        if (!p1 || !p2) return 0;
        const dx = p1.x - p2.x;
        const dy = p1.y - p2.y;
        const dz = p1.z - p2.z;
        return Math.sqrt(dx * dx + dy * dy + dz * dz);
    }

    // 计算当前帧的手部速度
    calculateHandVelocity(results) {
        const currentPositions = {
            left: this.calculateHandCenter(results.leftHandLandmarks),
            right: this.calculateHandCenter(results.rightHandLandmarks)
        };

        if (!this.lastHandPositions) {
            this.lastHandPositions = currentPositions;
            return 0;
        }

        // 计算左右手的速度，取最大值
        let maxVelocity = 0;
        if (currentPositions.left && this.lastHandPositions.left) {
            const leftVel = this.calculateDistance(currentPositions.left, this.lastHandPositions.left);
            maxVelocity = Math.max(maxVelocity, leftVel);
        }
        if (currentPositions.right && this.lastHandPositions.right) {
            const rightVel = this.calculateDistance(currentPositions.right, this.lastHandPositions.right);
            maxVelocity = Math.max(maxVelocity, rightVel);
        }

        this.lastHandPositions = currentPositions;
        return maxVelocity;
    }

    // 判断是否处于低速状态（动作结束）
    isLowVelocity() {
        if (this.velocityBuffer.length < this.VELOCITY_BUFFER_SIZE) return false;
        const avgVelocity = this.velocityBuffer.reduce((a, b) => a + b, 0) / this.velocityBuffer.length;
        return avgVelocity < this.LOW_VELOCITY_THRESHOLD;
    }

    onHolisticResults(results) {
        if (!this.canvas) return;
        const nextWidth = this.webcam.videoWidth || this.options.cameraWidth;
        const nextHeight = this.webcam.videoHeight || this.options.cameraHeight;
        if (nextWidth !== this.lastCanvasWidth || nextHeight !== this.lastCanvasHeight) {
            this.canvas.width = nextWidth;
            this.canvas.height = nextHeight;
            this.lastCanvasWidth = nextWidth;
            this.lastCanvasHeight = nextHeight;
        }
        this.ctx.save();
        this.ctx.clearRect(0, 0, this.canvas.width, this.canvas.height);
        this.ctx.drawImage(results.image, 0, 0);

        // 绘制关节点
        if (this.options.drawPose && results.poseLandmarks) {
            drawConnectors(this.ctx, results.poseLandmarks, this.POSE_CONNECTIONS, { color: '#00FF00', lineWidth: 1 });
        }
        if (this.options.drawHands && results.leftHandLandmarks) {
            drawConnectors(this.ctx, results.leftHandLandmarks, this.HAND_CONNECTIONS, { color: '#FF6B6B', lineWidth: 3 });
            drawLandmarks(this.ctx, results.leftHandLandmarks, { color: '#FF6B6B', radius: 3 });
        }
        if (this.options.drawHands && results.rightHandLandmarks) {
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
                // 计算手部速度
                const velocity = this.calculateHandVelocity(results);
                this.velocityBuffer.push(velocity);
                if (this.velocityBuffer.length > this.VELOCITY_BUFFER_SIZE) {
                    this.velocityBuffer.shift();
                }

                // 检测低速状态
                if (this.isLowVelocity()) {
                    this.lowVelocityCount++;
                } else {
                    this.lowVelocityCount = 0;
                }

                // 开始录制（检测到手部且速度较高）
                if (!this.isRecording && !this.isLowVelocity()) {
                    this.startRecording();
                }

                // 触发识别（连续低速）
                if (this.isRecording && this.lowVelocityCount >= this.LOW_VELOCITY_FRAMES) {
                    if (this.keypointsBuffer.length >= this.MIN_SIGN_FRAMES) {
                        this.stopRecording();
                    } else {
                        // 帧数不足，静默丢弃
                        this.isRecording = false;
                        this.keypointsBuffer = [];
                        this.frameCount = 0;
                        this.lowVelocityCount = 0;
                        this.velocityBuffer = [];
                        this.options.onStatusChange('continuousReady');
                    }
                }
            } else {
                // 手部消失，重置状态
                if (this.isRecording) {
                    this.noHandFrames++;
                    if (this.noHandFrames >= this.NO_HAND_THRESHOLD) {
                        if (this.keypointsBuffer.length >= this.MIN_SIGN_FRAMES) {
                            this.stopRecording();
                        } else {
                            this.isRecording = false;
                            this.keypointsBuffer = [];
                            this.frameCount = 0;
                            this.noHandFrames = 0;
                            this.options.onStatusChange('continuousReady');
                        }
                    }
                } else {
                    this.noHandFrames = 0;
                    this.lowVelocityCount = 0;
                    this.velocityBuffer = [];
                    this.lastHandPositions = null;
                }
            }

            // 防止录制超时
            if (this.isRecording && this.keypointsBuffer.length >= this.MAX_SIGN_FRAMES) {
                this.stopRecording();
            }
        }

        // 始终运行后台监听，检测"开始结束"控制手势
        this._processWatchGesture(results);
    }

    // 后台监听"开始结束"手势，与 continuousMode 无关，摄像头开启后始终运行
    _processWatchGesture(results) {
        if (!this.cameraStarted || this.watchPending) return;
        const hasHand = !!(results.leftHandLandmarks || results.rightHandLandmarks);
        const now = Date.now();

        // 独立速度计算，不影响连续模式的 lastHandPositions
        let velocity = 0;
        if (hasHand) {
            const center = this.calculateHandCenter(results.rightHandLandmarks || results.leftHandLandmarks);
            if (this.watchLastHandPositions && center) {
                velocity = this.calculateDistance(center, this.watchLastHandPositions);
            }
            if (center) this.watchLastHandPositions = center;
        } else {
            this.watchLastHandPositions = null;
        }

        if (this.watchState === 'WATCH_IDLE') {
            if (hasHand && velocity > this.WATCH_START_THRESHOLD) {
                this.watchState = 'WATCH_COLLECTING';
                this.watchBuffer = [];
                this.watchStopCount = 0;
                this.watchLastCapture = 0;
            }
        } else if (this.watchState === 'WATCH_COLLECTING') {
            if (now - this.watchLastCapture >= 33) {
                this.watchBuffer.push(this.extractLandmarks(results));
                this.watchLastCapture = now;
            }
            if (!hasHand || velocity < this.WATCH_STOP_THRESHOLD) {
                this.watchStopCount++;
                if (this.watchStopCount >= this.WATCH_STOP_FRAMES) {
                    this.watchState = 'WATCH_IDLE';
                    const frames = [...this.watchBuffer];
                    this.watchBuffer = [];
                    this.watchStopCount = 0;
                    if (frames.length >= this.WATCH_MIN_FRAMES) {
                        this._sendWatchFrames(frames);
                    }
                }
            } else {
                this.watchStopCount = 0;
            }
            if (this.watchBuffer.length >= this.WATCH_MAX_FRAMES) {
                this.watchState = 'WATCH_IDLE';
                const frames = [...this.watchBuffer];
                this.watchBuffer = [];
                this.watchStopCount = 0;
                this._sendWatchFrames(frames);
            }
        }
    }

    async _sendWatchFrames(frames) {
        this.watchPending = true;
        try {
            const response = await fetch(`${this.recognitionApiUrl}/recognize`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ frames: frames.map(f => f.flat()), confidence_threshold: 0.5 })
            });
            const result = await response.json();
            if (result.word === '开始结束' || result.prediction === '开始结束') {
                const nowActive = !this.continuousMode;
                if (nowActive) {
                    this.startContinuous();
                } else {
                    this.stopContinuous();
                }
                this.options.onToggleByGesture(nowActive);
            }
        } catch (_) {}
        this.watchPending = false;
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
        this.ctx = this.canvas.getContext('2d', { alpha: true, desynchronized: true });
        this.initHolistic();
        this.camera = new Camera(this.webcam, {
            onFrame: async () => { await this.holistic.send({ image: this.webcam }); },
            width: this.options.cameraWidth,
            height: this.options.cameraHeight
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

    // 向后端发送一段帧序列，返回 { word, confidence, topPredictions } 或 null
    async _sendFrames(frames) {
        try {
            const response = await fetch(`${this.recognitionApiUrl}/recognize`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ frames: frames.map(f => f.flat()) })
            });
            const result = await response.json();
            if (response.ok && result.word) {
                return {
                    word: result.word,
                    confidence: result.confidence || 0.8,
                    topPredictions: Array.isArray(result.top_predictions) ? result.top_predictions : []
                };
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
            let bestWord = predictions[0].word, bestConf = predictions[0].confidence, bestTopPredictions = predictions[0].topPredictions || [], maxCount = 0;
            for (const p of predictions) {
                counts[p.word] = (counts[p.word] || 0) + 1;
                if (counts[p.word] > maxCount) {
                    maxCount = counts[p.word];
                    bestWord = p.word;
                    bestConf = p.confidence;
                    bestTopPredictions = p.topPredictions || [];
                }
            }

            this.displayResult(bestWord, bestConf, bestTopPredictions);
            if (bestWord !== '开始结束') {
                this.options.onResult({ prediction: bestWord, confidence: bestConf, topPredictions: bestTopPredictions });
                this.options.onStatusChange('resultReady', { prediction: bestWord, confidence: bestConf, topPredictions: bestTopPredictions });
            }
            if (this.continuousMode) {
                this.options.onStatusChange('continuousReady');
            }
        } catch (error) {
            console.error('Recognition error:', error);
            this.options.onStatusChange('networkError', { message: error.message || String(error) });
        }
    }

    displayResult(prediction, confidence, topPredictions = []) {
        if (prediction === '无意义词') return;
        if (prediction === '开始结束') return;
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
            this.options.onResult({ prediction, confidence, topPredictions });
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
            this.options.onResult({ prediction: finalPrediction, confidence, topPredictions });
        } else {
            this.options.onResult({ prediction: `${prediction}?`, confidence, topPredictions });
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
        this.lowVelocityCount = 0;
        this.velocityBuffer = [];
        this.lastHandPositions = null;
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
        this.lowVelocityCount = 0;
        this.velocityBuffer = [];
        this.lastHandPositions = null;
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
