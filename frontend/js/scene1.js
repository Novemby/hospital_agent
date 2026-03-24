import { createApp, ref, computed, watch, onMounted } from 'vue';
import { getPatientById, upsertPatient, addMessage } from './services/patientService.js';
import { patientLoginAPI, setCurrentPatient, getCurrentPatient, clearAuth } from './services/authService.js';

// ==================== 手语识别全局变量 ====================
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
let lastTopPredictions = [];
let wordSequence = []; // 本地词序列（与Vue同步）
const API_URL = 'http://localhost:5000';

// Holistic 连接常量
const POSE_CONNECTIONS = [[0,1],[1,2],[2,3],[3,7],[0,4],[4,5],[5,6],[6,8],[9,10],[11,12],[11,13],[13,15],[12,14],[14,16],[11,23],[12,24],[23,24]];
const HAND_CONNECTIONS = [[0,1],[1,2],[2,3],[3,4],[0,5],[5,6],[6,7],[7,8],[0,9],[9,10],[10,11],[11,12],[0,13],[13,14],[14,15],[15,16],[0,17],[17,18],[18,19],[19,20],[5,9],[9,13],[13,17]];

// ==================== Vue 应用 ====================
const app = createApp({
    setup() {
        const currentUserInfo = getCurrentPatient();
        const currentId = ref(currentUserInfo ? currentUserInfo.id : '');
        const idCardInput = ref('');
        const view = ref(currentId.value ? 'reg-home' : 'login');
        const refreshKey = ref(0);

        // 侧边栏展开状态
        const showChat = ref(false);

        // 语音输入相关
        const isRecordingVoice = ref(false);
        const voiceText = ref('');               // 最终确认后的文本
        const recognizedText = ref('');           // 语音识别出的临时文本（需确认）
        const mediaRecorder = ref(null);
        const audioChunks = ref([]);

        // 当前患者姓名
        const currentPatientName = computed(() => {
            refreshKey.value;
            if (!currentId.value) return '';
            const p = getPatientById(currentId.value);
            return p ? p.name : '';
        });

        // Vue 维护的词序列（与手语识别模块同步）
        const vueWordSequence = ref([]);

        // 返回场景选择
        const goToRoleSelect = () => {
            window.location.href = 'index.html';
        };

        // 患者登录
        const patientLogin = async () => {
            if (!idCardInput.value.trim()) {
                alert('请输入身份证号');
                return;
            }
            try {
                const backendData = await patientLoginAPI(idCardInput.value.trim());

                let patient = getPatientById(backendData.id);
                if (!patient) {
                    patient = {
                        id: backendData.id,
                        name: backendData.name,
                        isDeaf: backendData.is_deaf,
                        symptoms: '',
                        dept: '',
                        allergy: backendData.allergy_history || '未记录',
                        medicalHistory: '',
                        prescriptions: [],
                        messages: []
                    };
                    upsertPatient(patient);
                }

                setCurrentPatient(backendData);
                currentId.value = backendData.id;
                idCardInput.value = '';
                view.value = 'reg-home';
            } catch (error) {
                alert(error.message || '登录失败');
            }
        };

        const logout = () => {
            clearAuth();
            currentId.value = '';
            idCardInput.value = '';
            view.value = 'login';
            // 关闭摄像头
            if (camera) camera.stop();
            cameraStarted = false;
        };

        // 挂号大厅功能
        const quickAppointment = () => {
            const dept = prompt('请输入预约科室', '内科');
            if (dept) {
                const patient = getPatientById(currentId.value);
                if (patient) {
                    patient.dept = dept;
                    upsertPatient(patient);
                    alert(`已预约 ${dept}，号序 A0${Math.floor(Math.random()*10)}`);
                }
            }
        };
        const quickTakeNumber = () => {
            alert(`现场取号成功，您前面还有 ${Math.floor(Math.random()*5)+1} 人`);
        };
        const quickPayment = () => {
            alert('当前待缴费金额 ￥0.00，无欠费');
        };

        // 进入手语分诊
        const goToSignTriage = () => {
            view.value = 'sign-triage';
        };

        const goBack = () => {
            view.value = 'reg-home';
        };

        // ========== 手语识别函数 ==========
        const initHolistic = () => {
            holistic = new Holistic({ locateFile: (file) => `https://cdn.jsdelivr.net/npm/@mediapipe/holistic/${file}` });
            holistic.setOptions({
                modelComplexity: 1,
                smoothLandmarks: true,
                minDetectionConfidence: 0.3,
                minTrackingConfidence: 0.3,
                refineFaceLandmarks: false
            });
            holistic.onResults(onHolisticResults);
        };

        const onHolisticResults = (results) => {
            canvas.width = webcam.videoWidth;
            canvas.height = webcam.videoHeight;
            ctx.save();
            ctx.clearRect(0, 0, canvas.width, canvas.height);
            ctx.drawImage(results.image, 0, 0);

            // 绘制关节点
            if (results.poseLandmarks) {
                drawConnectors(ctx, results.poseLandmarks, POSE_CONNECTIONS, { color: '#00FF8866', lineWidth: 2 });
            }
            if (results.leftHandLandmarks) {
                drawConnectors(ctx, results.leftHandLandmarks, HAND_CONNECTIONS, { color: '#FF6B6B', lineWidth: 3 });
                drawLandmarks(ctx, results.leftHandLandmarks, { color: '#FF6B6B', radius: 3 });
            }
            if (results.rightHandLandmarks) {
                drawConnectors(ctx, results.rightHandLandmarks, HAND_CONNECTIONS, { color: '#4ECDC4', lineWidth: 3 });
                drawLandmarks(ctx, results.rightHandLandmarks, { color: '#4ECDC4', radius: 3 });
            }
            ctx.restore();

            if (isRecording) captureRawFrame();
        };

        const captureRawFrame = () => {
            const now = Date.now();
            if (now - lastCaptureTime < 33) return;
            lastCaptureTime = now;
            const tempCanvas = document.createElement('canvas');
            tempCanvas.width = webcam.videoWidth;
            tempCanvas.height = webcam.videoHeight;
            tempCanvas.getContext('2d').drawImage(webcam, 0, 0);
            framesBuffer.push(tempCanvas.toDataURL('image/jpeg', 0.8).split(',')[1]);
            frameCountLocal++;
            document.getElementById('statusText').textContent = `正在看... ${frameCountLocal}帧`;
            document.getElementById('overlay').textContent = `录制: ${frameCountLocal}帧`;
            document.getElementById('overlay').classList.add('recording');
        };

        const toggleRecording = () => {
            const btn = document.getElementById('recordBtn');
            if (!isRecording) {
                isRecording = true;
                framesBuffer = [];
                frameCountLocal = 0;
                lastCaptureTime = 0;
                btn.textContent = '⏹ 停止';
                btn.classList.add('btn-recording');
                document.getElementById('statusText').textContent = '我在看着，请比划...';
                clearCandidates();
                clearPredictionBuffer();
            } else {
                isRecording = false;
                btn.textContent = '⏺ 开始识别';
                btn.classList.remove('btn-recording');
                document.getElementById('overlay').classList.remove('recording');
                document.getElementById('statusText').textContent = `正在理解... (${framesBuffer.length}帧)`;
                document.getElementById('overlay').textContent = `处理 ${framesBuffer.length} 帧...`;
                sendFramesToBackend();
            }
        };

        const sendFramesToBackend = async () => {
            if (framesBuffer.length < 5) {
                document.getElementById('statusText').textContent = '时间有点短，请再比划一次 😊';
                return;
            }
            const wordEl = document.getElementById('currentWord');
            const confEl = document.getElementById('confidence');
            wordEl.textContent = '请稍后...';
            confEl.textContent = '正在识别中';
            wordEl.style.color = 'var(--text-light)';
            clearCandidates();
            clearPredictionBuffer();

            try {
                const response = await fetch(`${API_URL}/recognize`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ frames: framesBuffer, confidence_threshold: 0.3, auto_add: false, trim_frames: 2 })
                });
                const result = await response.json();
                wordEl.style.color = '';

                if (result.success) {
                    displayResult(result.prediction, result.confidence);
                    document.getElementById('statusText').textContent = result.confidence >= 0.5 ? `看起来像是「${result.prediction}」，请确认 👇` : '不太确定，请帮我选择 👇';
                    document.getElementById('overlay').textContent = result.prediction;
                    if (result.top_predictions && result.top_predictions.length > 0) {
                        displayTopCandidates(result.top_predictions);
                    }
                } else {
                    document.getElementById('statusText').textContent = '抱歉，没能理解，请再试一次 😊';
                    wordEl.textContent = '-';
                    confEl.textContent = '-';
                }
            } catch (error) {
                document.getElementById('statusText').textContent = '连接出了点问题';
                wordEl.textContent = '-';
                confEl.textContent = '-';
                wordEl.style.color = '';
            }
        };

        const displayResult = (prediction, confidence) => {
            const wordEl = document.getElementById('currentWord');
            const confEl = document.getElementById('confidence');
            wordEl.style.color = '';
            predictionBuffer.push(prediction);
            if (predictionBuffer.length > BUFFER_SIZE) predictionBuffer.shift();
            const counts = {};
            let maxCount = 0, finalPrediction = prediction;
            for (const p of predictionBuffer) {
                counts[p] = (counts[p] || 0) + 1;
                if (counts[p] > maxCount) {
                    maxCount = counts[p];
                    finalPrediction = p;
                }
            }
            if (maxCount >= Math.ceil(BUFFER_SIZE / 2)) {
                if (lastDisplayedPrediction !== finalPrediction) {
                    wordEl.style.transform = 'scale(1.1)';
                    setTimeout(() => { wordEl.style.transform = 'scale(1)'; }, 200);
                    lastDisplayedPrediction = finalPrediction;
                }
                wordEl.textContent = finalPrediction;
                confEl.textContent = `匹配度: ${(confidence * 100).toFixed(1)}%`;
            } else {
                wordEl.textContent = `${prediction}?`;
                confEl.textContent = `匹配度: ${(confidence * 100).toFixed(1)}% (分析中...)`;
            }
        };

        const displayTopCandidates = (topPredictions) => {
            const list = document.getElementById('candidatesList');
            clearAutoConfirmTimers();
            if (!topPredictions || topPredictions.length === 0) {
                list.innerHTML = '<span class="placeholder">暂无候选词</span>';
                hideAutoConfirmHint();
                return;
            }
            lastTopPredictions = topPredictions;
            const top1Confidence = topPredictions[0].confidence;
            list.innerHTML = '';

            topPredictions.forEach((pred, index) => {
                const btn = document.createElement('div');
                btn.className = 'candidate-btn' + (index === 0 ? ' top1' : '');
                btn.id = index === 0 ? 'top1Btn' : '';
                btn.innerHTML = `<span class="rank">${pred.rank}</span><span class="label">${pred.label}</span><span class="conf">匹配 ${(pred.confidence * 100).toFixed(0)}%</span>`;
                btn.onclick = () => confirmCandidate(pred.label, index);
                list.appendChild(btn);
            });
            startAutoConfirmCountdown(topPredictions[0].label, top1Confidence);
        };

        const startAutoConfirmCountdown = (defaultLabel, confidence) => {
            const hintEl = document.getElementById('autoConfirmHint');
            const manualHintEl = document.getElementById('manualSelectHint');
            const progressEl = document.getElementById('countdownProgress');
            const top1Btn = document.getElementById('top1Btn');
            hintEl.classList.add('hidden');
            manualHintEl.classList.add('hidden');

            let delayMs = 0, animationClass = '';
            const HIGH_CONFIDENCE = 0.85;
            const MID_CONFIDENCE = 0.50;
            const HIGH_CONF_DELAY = 3000;
            const MID_CONF_DELAY = 5000;

            if (confidence >= HIGH_CONFIDENCE) {
                delayMs = HIGH_CONF_DELAY;
                animationClass = 'counting-3s';
            } else if (confidence >= MID_CONFIDENCE) {
                delayMs = MID_CONF_DELAY;
                animationClass = 'counting-5s';
            } else {
                manualHintEl.classList.remove('hidden');
                if (top1Btn) top1Btn.classList.remove('counting-3s', 'counting-5s');
                return;
            }

            let countdownSeconds = Math.ceil(delayMs / 1000);
            const totalSeconds = countdownSeconds;
            document.querySelector('.countdown-text').innerHTML = `<span id="countdownNum">${countdownSeconds}</span>秒后自动确认`;
            hintEl.classList.remove('hidden');
            progressEl.style.transition = 'none';
            progressEl.style.width = '100%';
            if (top1Btn) {
                top1Btn.classList.remove('counting-3s', 'counting-5s');
                void top1Btn.offsetWidth;
                top1Btn.classList.add(animationClass);
            }

            countdownTimer = setInterval(() => {
                countdownSeconds--;
                const numEl = document.getElementById('countdownNum');
                if (numEl) numEl.textContent = countdownSeconds;
                progressEl.style.transition = 'width 1s linear';
                progressEl.style.width = (countdownSeconds / totalSeconds * 100) + '%';
                if (countdownSeconds <= 0) clearInterval(countdownTimer);
            }, 1000);
            autoConfirmTimer = setTimeout(() => confirmCandidate(defaultLabel, 0, true), delayMs);
        };

        const clearAutoConfirmTimers = () => {
            if (autoConfirmTimer) { clearTimeout(autoConfirmTimer); autoConfirmTimer = null; }
            if (countdownTimer) { clearInterval(countdownTimer); countdownTimer = null; }
            const top1Btn = document.getElementById('top1Btn');
            if (top1Btn) top1Btn.classList.remove('counting-3s', 'counting-5s');
        };

        const hideAutoConfirmHint = () => {
            document.getElementById('autoConfirmHint')?.classList.add('hidden');
            document.getElementById('manualSelectHint')?.classList.add('hidden');
        };

        const clearCandidates = () => {
            clearAutoConfirmTimers();
            hideAutoConfirmHint();
            document.getElementById('candidatesList').innerHTML = '<span class="placeholder">识别后会显示候选词供您选择</span>';
            lastTopPredictions = [];
        };

        const clearPredictionBuffer = () => {
            predictionBuffer = [];
            lastDisplayedPrediction = null;
        };

        const confirmCandidate = async (label, index, isAuto = false) => {
            clearAutoConfirmTimers();
            hideAutoConfirmHint();
            try {
                const response = await fetch(`${API_URL}/confirm_word`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ label })
                });
                const result = await response.json();
                if (result.success) {
                    wordSequence = result.sequence;
                    vueWordSequence.value = [...wordSequence];

                    const wordEl = document.getElementById('currentWord');
                    const confEl = document.getElementById('confidence');
                    wordEl.textContent = label;
                    confEl.textContent = '已确认 ✓';
                    wordEl.style.color = 'var(--secondary-mint)';
                    wordEl.style.transform = 'scale(1.1)';
                    setTimeout(() => {
                        wordEl.style.color = '';
                        wordEl.style.transform = '';
                    }, 300);
                    clearCandidates();
                    clearPredictionBuffer();
                    document.getElementById('statusText').textContent = isAuto ? `✓ 已自动确认「${label}」` : `✓ 好的，已记录「${label}」`;
                }
            } catch (error) {
                alert('操作失败，请再试一次');
            }
        };

        // 摄像头启动
        const startCamera = async () => {
            if (cameraStarted) return;
            webcam = document.getElementById('webcam');
            canvas = document.getElementById('canvas');
            ctx = canvas.getContext('2d');
            initHolistic();
            camera = new Camera(webcam, {
                onFrame: async () => { await holistic.send({ image: webcam }); },
                width: 640,
                height: 480
            });
            await camera.start();
            cameraStarted = true;
            document.getElementById('startBtn').disabled = true;
            document.getElementById('startBtn').textContent = '✓ 已开启';
            document.getElementById('recordBtn').disabled = false;
            document.getElementById('statusDot').classList.add('active');
            document.getElementById('statusText').textContent = '就绪，可以开始比划了';
            document.getElementById('overlay').textContent = '准备就绪';
        };

        // ========== 呼叫人工功能 ==========
        const sendPatientMessage = (text) => {
            if (!text.trim() || !currentId.value) return;
            addMessage(currentId.value, { type: 'pat', content: text });
            alert('消息已发送给医生');
            voiceText.value = ''; // 清空输入
            recognizedText.value = '';
        };

        // 语音识别 - 修改：开始/停止切换，识别结果存入 recognizedText
        const toggleVoiceRecording = async () => {
            if (isRecordingVoice.value) {
                // 停止录音
                stopVoiceRecording();
            } else {
                // 开始录音
                await startVoiceRecording();
            }
        };

        const startVoiceRecording = async () => {
            if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
                alert('您的浏览器不支持语音输入');
                return;
            }
            try {
                const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
                mediaRecorder.value = new MediaRecorder(stream);
                audioChunks.value = [];
                mediaRecorder.value.ondataavailable = event => {
                    audioChunks.value.push(event.data);
                };
                mediaRecorder.value.onstop = () => {
                    const audioBlob = new Blob(audioChunks.value, { type: 'audio/webm' });
                    sendVoiceToBackend(audioBlob);
                    // 停止所有轨道
                    stream.getTracks().forEach(track => track.stop());
                };
                mediaRecorder.value.start();
                isRecordingVoice.value = true;
            } catch (error) {
                alert('无法访问麦克风: ' + error.message);
            }
        };

        const stopVoiceRecording = () => {
            if (mediaRecorder.value && isRecordingVoice.value) {
                mediaRecorder.value.stop();
                isRecordingVoice.value = false;
            }
        };

        const sendVoiceToBackend = async (audioBlob) => {
            const formData = new FormData();
            formData.append('audio', audioBlob);
            try {
                const response = await fetch(`${API_URL}/speech-to-text`, {
                    method: 'POST',
                    body: formData
                });
                const result = await response.json();
                if (result.success) {
                    // 将识别结果存入临时文本，等待用户确认
                    recognizedText.value = result.text;
                } else {
                    alert('语音识别失败');
                }
            } catch (error) {
                alert('语音识别服务连接失败');
            }
        };

        // 确认语音识别结果
        const confirmVoiceText = () => {
            if (recognizedText.value.trim()) {
                voiceText.value = recognizedText.value;
                recognizedText.value = ''; // 清空临时区域
            }
        };

        // 取消语音识别结果
        const cancelVoiceText = () => {
            recognizedText.value = '';
        };

        // 监听 iframe 消息（手语序列同步）
        onMounted(() => {
            window.addEventListener('message', (event) => {
                const data = event.data;
                if (data && data.type === 'wordConfirmed') {
                    vueWordSequence.value.push(data.word);
                }
                if (data && data.type === 'sequenceUpdated') {
                    vueWordSequence.value = data.sequence;
                }
            });
        });

        // 进入手语分诊时自动开启摄像头
        watch(view, (newVal) => {
            if (newVal === 'sign-triage') {
                setTimeout(() => {
                    if (!cameraStarted) {
                        startCamera().catch(e => console.warn('摄像头启动失败', e));
                    }
                }, 300);
            }
        });

        return {
            view, currentId, idCardInput, currentPatientName, vueWordSequence,
            showChat,            // 新增侧边栏展开状态
            isRecordingVoice, voiceText, recognizedText,
            goToRoleSelect, patientLogin, logout,
            quickAppointment, quickTakeNumber, quickPayment,
            goToSignTriage, goBack,
            startCamera,
            toggleRecording,
            cameraStarted,
            sendPatientMessage,
            toggleVoiceRecording,
            confirmVoiceText,
            cancelVoiceText,
        };
    },
    template: `
        <!-- 患者登录界面 -->
        <div v-if="view === 'login'" class="flex items-center justify-center min-h-screen">
            <div class="bg-white p-8 rounded-2xl shadow-2xl w-96">
                <button @click="goToRoleSelect" class="text-sm text-blue-600 mb-4 flex items-center">← 返回场景选择</button>
                <div class="text-center mb-6">
                    <div class="inline-block p-3 bg-green-100 rounded-full mb-3">
                        <svg class="w-8 h-8 text-green-600" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z"></path></svg>
                    </div>
                    <h2 class="text-2xl font-bold">患者挂号登录</h2>
                </div>
                <input v-model="idCardInput" type="text" placeholder="就诊卡号 / 身份证号" class="w-full border-2 border-gray-100 p-3 rounded-xl mb-5 focus:border-green-500 outline-none">
                <button @click="patientLogin" class="w-full bg-green-600 text-white py-3 rounded-xl font-bold hover:bg-green-700">进入挂号大厅</button>
                <p class="text-xs text-gray-400 mt-4 text-center">无密码，仅凭ID追踪状态</p>
            </div>
        </div>

        <!-- 非登录页显示导航栏和内容 -->
        <div v-else>
            <!-- 导航栏 -->
            <nav class="bg-white shadow-sm p-4 flex justify-between items-center px-8">
                <div class="flex items-center space-x-2">
                    <div class="w-8 h-8 bg-blue-600 rounded-lg flex items-center justify-center text-white font-bold">+</div>
                    <span class="font-bold text-lg text-gray-700">无障碍智慧医疗 · 挂号</span>
                </div>
                <div class="flex items-center space-x-4">
                    <span class="text-sm bg-blue-50 text-blue-700 px-3 py-1 rounded-full">患者ID: {{ currentId }}</span>
                    <button @click="logout" class="text-gray-400 hover:text-red-500 text-sm">退出</button>
                </div>
            </nav>

            <!-- 挂号大厅 -->
            <div v-if="view === 'reg-home'" class="max-w-5xl mx-auto p-8">
                <div class="mb-6 flex items-center justify-between bg-white p-4 rounded-2xl shadow-sm">
                    <div>
                        <span class="text-gray-500">当前患者：</span>
                        <span class="font-bold text-xl text-green-700">{{ currentPatientName || currentId }}</span>
                    </div>
                    <div class="text-sm text-gray-400">使用ID追踪您的挂号、症状记录</div>
                </div>
                <div class="grid grid-cols-3 gap-8">
                    <div class="col-span-2 grid grid-cols-2 gap-6">
                        <div @click="quickAppointment" class="bg-white p-8 rounded-2xl shadow-sm hover:shadow-md cursor-pointer transition-all border-b-4 border-blue-500">
                            <h3 class="text-xl font-bold mb-2">预约挂号</h3>
                            <p class="text-gray-500 text-sm">普通、专家号源预约</p>
                        </div>
                        <div @click="quickTakeNumber" class="bg-white p-8 rounded-2xl shadow-sm hover:shadow-md cursor-pointer transition-all border-b-4 border-green-500">
                            <h3 class="text-xl font-bold mb-2">现场取号</h3>
                            <p class="text-gray-500 text-sm">凭身份证或诊疗码取号</p>
                        </div>
                        <div @click="quickPayment" class="bg-white p-8 rounded-2xl shadow-sm hover:shadow-md cursor-pointer transition-all border-b-4 border-orange-500">
                            <h3 class="text-xl font-bold mb-2">自助缴费</h3>
                            <p class="text-gray-500 text-sm">门诊、检查及药费缴纳</p>
                        </div>
                    </div>
                    <div @click="goToSignTriage" class="bg-gradient-to-br from-blue-600 to-indigo-700 p-8 rounded-3xl shadow-xl cursor-pointer hover:scale-105 transition-transform text-white flex flex-col justify-between">
                        <div>
                            <div class="bg-white/20 w-12 h-12 rounded-xl flex items-center justify-center mb-4">
                                <svg class="w-8 h-8" fill="currentColor" viewBox="0 0 20 20"><path d="M10 12a2 2 0 100-4 2 2 0 000 4z"></path><path fill-rule="evenodd" d="M.458 10C1.732 5.943 5.522 3 10 3s8.268 2.943 9.542 7c-1.274 4.057-5.064 7-9.542 7S1.732 14.057.458 10zM14 10a4 4 0 11-8 0 4 4 0 018 0z" clip-rule="evenodd"></path></svg>
                            </div>
                            <h2 class="text-2xl font-black">手语分诊辅助</h2>
                            <p class="mt-2 opacity-80">专为聋哑人士提供手语转写及智能分诊</p>
                        </div>
                        <div class="bg-white text-blue-600 font-bold py-3 rounded-xl text-center mt-4">立即进入</div>
                    </div>
                </div>
            </div>

            <!-- 手语分诊页面 -->
            <div v-if="view === 'sign-triage'" class="max-w-7xl mx-auto p-4">
                <div class="bg-white rounded-3xl shadow-xl overflow-hidden">
                    <div class="bg-blue-600 p-4 text-white flex justify-between items-center">
                        <span class="font-bold">🤟 手语翻译官 · 患者: {{ currentId }}</span>
                        <button @click="goBack" class="text-sm bg-white/20 px-4 py-1 rounded-full hover:bg-white/30">返回大厅</button>
                    </div>
                    <div class="p-6">
                        <div class="grid grid-cols-1 md:grid-cols-2 gap-6">
                            <!-- 左侧：摄像头区 -->
                            <div class="bg-white rounded-2xl shadow-sm p-4">
                                <h2 class="text-lg font-bold mb-4 flex items-center gap-2">📹 请对着摄像头比划</h2>
                                <div class="video-container relative">
                                    <video id="webcam" autoplay playsinline class="w-full rounded-lg"></video>
                                    <canvas id="canvas" class="absolute top-0 left-0 w-full h-full"></canvas>
                                    <div class="overlay" id="overlay">等待开始...</div>
                                    <div class="detection-status absolute top-2 right-2 bg-white/90 p-2 rounded-lg text-xs hidden md:block">
                                        <div class="detection-item"><span class="detection-dot" id="poseDot"></span>身体</div>
                                        <div class="detection-item"><span class="detection-dot" id="leftHandDot"></span>左手</div>
                                        <div class="detection-item"><span class="detection-dot" id="rightHandDot"></span>右手</div>
                                    </div>
                                </div>
                                <div class="controls flex gap-2 mt-4">
                                    <button id="startBtn" class="flex-1 bg-blue-600 text-white py-2 rounded-lg hover:bg-blue-700 disabled:opacity-50" :disabled="cameraStarted" @click="startCamera">📷 开启摄像头</button>
                                    <button id="recordBtn" class="flex-1 bg-green-600 text-white py-2 rounded-lg hover:bg-green-700 disabled:opacity-50" :disabled="!cameraStarted" @click="toggleRecording">⏺ 开始识别</button>
                                </div>
                                <div class="status-bar flex items-center gap-2 mt-3 p-2 bg-gray-100 rounded-lg text-sm">
                                    <span class="status-dot" id="statusDot"></span>
                                    <span id="statusText">请先开启摄像头</span>
                                </div>
                                <div class="tips-box mt-3 p-3 bg-blue-50 rounded-lg text-xs text-gray-600">
                                    <strong>💡 小贴士：</strong> 光线充足、手部在胸前比划效果更好。
                                </div>
                            </div>
                            <!-- 右侧：结果区 + 侧边栏（人工帮助） -->
                            <div class="space-y-6">
                                <!-- 识别结果卡片 -->
                                <div class="bg-white rounded-2xl shadow-sm p-4">
                                    <h2 class="text-lg font-bold mb-4 flex items-center gap-2">💬 识别结果</h2>
                                    <div class="current-word text-center p-6 bg-orange-50 rounded-xl mb-4">
                                        <div class="label text-4xl font-bold text-orange-600" id="currentWord">-</div>
                                        <div class="confidence text-sm text-gray-500 mt-2" id="confidence">-</div>
                                    </div>
                                    <div class="top-candidates" id="topCandidates">
                                        <h3 class="font-medium mb-3">🤔 您想表达的是这个意思吗？</h3>
                                        <div class="candidates-list flex flex-wrap gap-2" id="candidatesList">
                                            <span class="placeholder text-gray-400">识别后会显示候选词</span>
                                        </div>
                                        <div class="auto-confirm-hint hidden mt-3 p-2 bg-yellow-50 rounded-lg text-sm" id="autoConfirmHint">
                                            <span class="countdown-text">若不选择，<span id="countdownNum">5</span>秒后自动确认</span>
                                            <div class="countdown-bar h-1 bg-gray-200 mt-2 rounded overflow-hidden"><div class="countdown-progress h-full bg-green-500" id="countdownProgress" style="width:100%"></div></div>
                                        </div>
                                        <div class="manual-select-hint hidden mt-3 p-2 bg-red-50 rounded-lg text-sm" id="manualSelectHint">
                                            <span>🙏 不太确定，请帮我选择正确的词</span>
                                        </div>
                                    </div>
                                    <div class="sequence-section mt-4">
                                        <h3 class="font-medium mb-2">📝 已确认的表达</h3>
                                        <div class="word-sequence flex flex-wrap gap-2 min-h-[50px] p-3 bg-gray-50 rounded-lg" id="wordSequence">
                                            <span v-if="vueWordSequence.length === 0" class="placeholder text-gray-400">点击候选词确认</span>
                                            <span v-for="(word, idx) in vueWordSequence" :key="idx" class="word-tag bg-green-100 px-3 py-1 rounded-full text-sm">{{ word }}</span>
                                        </div>
                                    </div>
                                </div>

                                <!-- 人工帮助侧边栏（可折叠） -->
                                <div class="bg-white rounded-2xl shadow-sm p-4 transition-all duration-300">
                                    <div class="flex justify-between items-center cursor-pointer" @click="showChat = !showChat">
                                        <h3 class="font-bold text-lg flex items-center gap-2">
                                            <span>📞</span> 人工帮助
                                        </h3>
                                        <button class="text-blue-600 text-sm hover:underline">
                                            {{ showChat ? '收起 ▲' : '展开 ▼' }}
                                        </button>
                                    </div>
                                    <div v-show="showChat" class="mt-4 transition-all duration-300">
                                        <!-- 输入框 -->
                                        <input v-model="voiceText" type="text" class="w-full border rounded-lg p-2 mb-2" placeholder="输入文字或使用语音...">
                                        <!-- 语音识别临时结果区 -->
                                        <div v-if="recognizedText" class="mb-3 p-2 bg-yellow-50 border border-yellow-200 rounded-lg">
                                            <div class="text-sm text-gray-600 mb-2">识别结果：</div>
                                            <div class="flex items-center gap-2">
                                                <span class="flex-1 text-gray-800">{{ recognizedText }}</span>
                                                <button @click="confirmVoiceText" class="bg-green-500 text-white px-3 py-1 rounded text-sm">确认</button>
                                                <button @click="cancelVoiceText" class="bg-gray-300 text-gray-700 px-3 py-1 rounded text-sm">取消</button>
                                            </div>
                                        </div>
                                        <!-- 语音控制按钮 -->
                                        <div class="flex items-center gap-2 mb-3">
                                            <button @click="toggleVoiceRecording" 
                                                    :class="isRecordingVoice ? 'bg-red-500 hover:bg-red-600' : 'bg-green-500 hover:bg-green-600'"
                                                    class="text-white px-4 py-2 rounded-lg text-sm flex items-center gap-1 transition-colors">
                                                <span v-if="!isRecordingVoice">🎤 开始语音</span>
                                                <span v-else>⏹ 停止录音</span>
                                            </button>
                                            <span v-if="isRecordingVoice" class="text-sm text-red-500 animate-pulse">录音中...</span>
                                        </div>
                                        <!-- 发送按钮 -->
                                        <div class="flex justify-end">
                                            <button @click="sendPatientMessage(voiceText)" class="bg-blue-600 text-white px-4 py-2 rounded-lg hover:bg-blue-700">
                                                发送消息
                                            </button>
                                        </div>
                                    </div>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    `
});

app.mount('#app');