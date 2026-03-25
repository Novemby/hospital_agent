// 手语识别 Agent - 集成头部姿态检测（点头/摇头）替代候选词选择
// 适配新识别服务（5000端口）与原有后端（8000端口）协同
export class SignLanguageAgent {
    constructor(options = {}) {
        this.options = {
            // 原有后端地址（序列管理、润色等）
            apiUrl: 'http://localhost:8000',
            // 新识别服务地址
            recognitionApiUrl: 'http://localhost:8000/api/sign',
            mode: 'isolated',
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

        // 滑动窗口投票
        this.predictionBuffer = [];
        this.BUFFER_SIZE = 3;
        this.lastDisplayedPrediction = null;

        // 词序列
        this.wordSequence = [];

        // 头部姿态检测
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
            locateFile: (file) => `https://cdn.jsdelivr.net/npm/@mediapipe/holistic/${file}`
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

    // 从 results 中提取 543 个关键点（姿态33 + 左手21 + 右手21 + 面部468）
    extractLandmarks(results) {
        const landmarks = [];
        // 姿态点 (33个)
        if (results.poseLandmarks) {
            for (const lm of results.poseLandmarks) {
                landmarks.push([lm.x, lm.y, lm.z]);
            }
        } else {
            for (let i = 0; i < 33; i++) landmarks.push([0,0,0]);
        }
        // 左手 (21个)
        if (results.leftHandLandmarks) {
            for (const lm of results.leftHandLandmarks) {
                landmarks.push([lm.x, lm.y, lm.z]);
            }
        } else {
            for (let i = 0; i < 21; i++) landmarks.push([0,0,0]);
        }
        // 右手 (21个)
        if (results.rightHandLandmarks) {
            for (const lm of results.rightHandLandmarks) {
                landmarks.push([lm.x, lm.y, lm.z]);
            }
        } else {
            for (let i = 0; i < 21; i++) landmarks.push([0,0,0]);
        }
        // 面部 (468个)
        if (results.faceLandmarks) {
            for (const lm of results.faceLandmarks) {
                landmarks.push([lm.x, lm.y, lm.z]);
            }
        } else {
            for (let i = 0; i < 468; i++) landmarks.push([0,0,0]);
        }
        return landmarks; // 长度应为 33+21+21+468 = 543
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

        // 头部姿态检测
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

    async stopRecording() {
        if (!this.isRecording) return;
        this.isRecording = false;
        if (this.keypointsBuffer.length < 5) {
            this.options.onStatusChange('tooShort');
            return;
        }
        this.options.onStatusChange('sending', { frameCount: this.keypointsBuffer.length });
        try {
            // 将每个帧的关键点展平为一维数组
            const flattenedFrames = this.keypointsBuffer.map(frame => frame.flat());
            const response = await fetch(`${this.recognitionApiUrl}/recognize`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ frames: flattenedFrames })
            });
            const result = await response.json();
            if (result.word) {
                const prediction = result.word;
                const confidence = result.confidence || 0.8;
                this.displayResult(prediction, confidence);
                this.options.onResult({
                    prediction: prediction,
                    confidence: confidence,
                    topPredictions: []
                });
                this.options.onStatusChange('resultReady', { prediction, confidence, topPredictions: [] });
            } else {
                this.options.onStatusChange('recognitionFailed');
            }
        } catch (error) {
            console.error('Recognition error:', error);
            this.options.onStatusChange('networkError');
        }
    }

    displayResult(prediction, confidence) {
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
            }
            this.options.onResult({
                prediction: finalPrediction,
                confidence: confidence,
                topPredictions: []
            });
        } else {
            this.options.onResult({
                prediction: `${prediction}?`,
                confidence: confidence,
                topPredictions: []
            });
        }
    }

    clearPredictionBuffer() {
        this.predictionBuffer = [];
        this.lastDisplayedPrediction = null;
    }

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