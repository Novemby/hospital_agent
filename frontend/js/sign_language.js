// sign_language.js
// 手语识别核心逻辑

// 定义全局变量
let webcam, canvas, ctx, camera, holistic;
let cameraStarted = false;
let isRecording = false;
let framesBuffer = [];
let frameCountLocal = 0;
let lastCaptureTime = 0;
let predictionBuffer = [];
const BUFFER_SIZE = 3;
let lastDisplayedPrediction = null;
let autoConfirmTimer = null;
let countdownTimer = null;
const HIGH_CONFIDENCE = 0.85;
const MID_CONFIDENCE = 0.50;
const HIGH_CONF_DELAY = 3000;
const MID_CONF_DELAY = 5000;
let lastTopPredictions = [];

// 连续模式变量
let continuousRunning = false;
let detectState = 'IDLE';
let actionFrames = [];
let stoppingCount = 0;
let processingCount = 0;
let prevWrist = null;
let speedHistory = [];
let currentSpeed = 0;
let fpsCount = 0;
let lastFpsTime = Date.now();

// 词汇序列
let wordSequence = [];

// 后端 API 地址
const API_URL = 'http://localhost:5000';

// ==================== 核心初始化 ====================

export function initSignLanguage(patientId, onWordConfirmed) {
    webcam = document.getElementById('webcam');
    canvas = document.getElementById('canvas');
    ctx = canvas.getContext('2d');

    holistic = new Holistic({
        locateFile: (file) => `https://cdn.jsdelivr.net/npm/@mediapipe/holistic/${file}`
    });

    holistic.setOptions({
        modelComplexity: 1,
        smoothLandmarks: true,
        minDetectionConfidence: 0.3,
        minTrackingConfidence: 0.3
    });

    holistic.onResults(onHolisticResults);

    camera = new Camera(webcam, {
        onFrame: async () => {
            await holistic.send({ image: webcam });
        },
        width: 640,
        height: 480
    });

    camera.start().then(() => {
        cameraStarted = true;
        updateUIForCameraStarted();
    }).catch(err => {
        console.error('摄像头启动失败', err);
    });

    // 绑定按钮事件
    document.getElementById('startBtn').addEventListener('click', startCamera);
    document.getElementById('recordBtn').addEventListener('click', toggleRecording);

    window.onWordConfirmed = onWordConfirmed;
}

// ==================== 核心处理函数 ====================

function onHolisticResults(results) {
    canvas.width = webcam.videoWidth;
    canvas.height = webcam.videoHeight;
    ctx.save();
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.drawImage(results.image, 0, 0);

    // 绘制骨架（简化版，调用 drawing_utils）
    if (results.poseLandmarks) drawConnectors(ctx, results.poseLandmarks, POSE_CONNECTIONS, {color: '#00FF8866', lineWidth: 2});
    if (results.leftHandLandmarks) {
        drawConnectors(ctx, results.leftHandLandmarks, HAND_CONNECTIONS, {color: '#FF6B6B', lineWidth: 3});
        drawLandmarks(ctx, results.leftHandLandmarks, {color: '#FF6B6B', radius: 3});
    }
    if (results.rightHandLandmarks) {
        drawConnectors(ctx, results.rightHandLandmarks, HAND_CONNECTIONS, {color: '#4ECDC4', lineWidth: 3});
        drawLandmarks(ctx, results.rightHandLandmarks, {color: '#4ECDC4', radius: 3});
    }
    ctx.restore();

    // 录制逻辑
    if (isRecording) {
        captureRawFrame();
    }
}

function captureRawFrame() {
    const now = Date.now();
    if (now - lastCaptureTime < 33) return;
    lastCaptureTime = now;

    const tempCanvas = document.createElement('canvas');
    tempCanvas.width = webcam.videoWidth;
    tempCanvas.height = webcam.videoHeight;
    tempCanvas.getContext('2d').drawImage(webcam, 0, 0);

    framesBuffer.push(tempCanvas.toDataURL('image/jpeg', 0.8).split(',')[1]);
    frameCountLocal++;

    document.getElementById('statusText').textContent = `正在录制... ${frameCountLocal}帧`;
}

export function toggleRecording() {
    const btn = document.getElementById('recordBtn');
    if (!isRecording) {
        isRecording = true;
        framesBuffer = [];
        frameCountLocal = 0;
        btn.textContent = '⏹ 停止识别';
        btn.classList.add('btn-recording');
        document.getElementById('statusText').textContent = '请开始比划手语...';
    } else {
        isRecording = false;
        btn.textContent = '⏺ 开始识别';
        btn.classList.remove('btn-recording');
        sendFramesToBackend();
    }
}

async function sendFramesToBackend() {
    if (framesBuffer.length < 5) {
        document.getElementById('statusText').textContent = '动作太快了，请再试一次';
        return;
    }

    const wordEl = document.getElementById('currentWord');
    wordEl.textContent = '正在理解...';

    try {
        const response = await fetch(`${API_URL}/recognize`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                frames: framesBuffer,
                confidence_threshold: 0.3,
                auto_add: false
            })
        });
        const result = await response.json();

        if (result.success) {
            displayTopCandidates(result.top_predictions);
            wordEl.textContent = result.prediction;
        } else {
            document.getElementById('statusText').textContent = '未能识别，请重试';
        }
    } catch (error) {
        console.error('API请求失败', error);
    }
}

// ==================== 确认与交互逻辑 ====================

function displayTopCandidates(topPredictions) {
    const list = document.getElementById('candidatesList');
    list.innerHTML = '';

    topPredictions.forEach((pred, index) => {
        const btn = document.createElement('div');
        btn.className = 'candidate-btn' + (index === 0 ? ' top1' : '');
        btn.innerHTML = `<span class="label">${pred.label}</span>`;
        btn.onclick = () => confirmCandidate(pred.label, index);
        list.appendChild(btn);
    });
}

async function confirmCandidate(label, index, isAuto = false) {
    clearAutoConfirmTimers();
    try {
        const response = await fetch(`${API_URL}/confirm_word`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ label })
        });
        const result = await response.json();

        if (result.success) {
            // 调用回调，通知外部组件
            if (window.onWordConfirmed) {
                window.onWordConfirmed(label);
            }

            // 更新UI反馈
            const wordEl = document.getElementById('currentWord');
            wordEl.textContent = label;
            wordEl.style.color = 'var(--secondary-mint)';

            setTimeout(() => {
                wordEl.style.color = '';
            }, 1000);

            document.getElementById('statusText').textContent = `✓ 已确认「${label}」`;
        }
    } catch (error) {
        console.error('确认失败', error);
    }
}

function clearAutoConfirmTimers() {
    if (autoConfirmTimer) clearTimeout(autoConfirmTimer);
    if (countdownTimer) clearInterval(countdownTimer);
}

// 辅助函数：更新摄像头开启后的UI
function updateUIForCameraStarted() {
    document.getElementById('statusText').textContent = '摄像头已就绪';
    document.getElementById('statusDot').classList.add('active');
}