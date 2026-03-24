import { createApp, ref, computed, watch, onMounted, onUnmounted } from 'vue';
import { getPatientById, upsertPatient, addMessage } from './services/patientService.js';
import { patientLoginAPI, setCurrentPatient, getCurrentPatient, clearAuth } from './services/authService.js';
import { SignLanguageAgent } from './services/signLanguageAgent.js';

const app = createApp({
    setup() {
        // ========== 原有状态 ==========
        const currentUserInfo = getCurrentPatient();
        const currentId = ref(currentUserInfo ? currentUserInfo.id : '');
        const idCardInput = ref('');
        const view = ref(currentId.value ? 'reg-home' : 'login');
        const refreshKey = ref(0);
        const showChat = ref(false);          // 人工帮助侧边栏
        const isRecordingVoice = ref(false);
        const voiceText = ref('');
        const recognizedText = ref('');
        let mediaRecorder = null;
        let audioChunks = [];

        // 手语 agent
        const agent = new SignLanguageAgent({
            apiUrl: 'http://localhost:8000',
            onResult: (result) => {
                // 更新识别结果显示
                currentWord.value = result.prediction;
                currentConfidence.value = `${(result.confidence * 100).toFixed(1)}%`;
                // 如果有候选词，启动头部动作监听（取 top1 为默认推荐）
                if (result.topPredictions && result.topPredictions.length) {
                    currentCandidates.value = result.topPredictions;
                    currentCandidateIndex.value = 0;
                    startHeadGestureWaiting();
                } else {
                    currentCandidates.value = [];
                }
            },
            onSequenceUpdate: (seq) => {
                vueWordSequence.value = seq;
            },
            onStatusChange: (status, data) => {
                if (status === 'recordingStart') {
                    statusText.value = '我在看着，请比划...';
                    overlayText.value = '录制中...';
                    overlayClass.value = 'overlay recording';
                } else if (status === 'sending') {
                    statusText.value = `正在理解... (${data.frameCount}帧)`;
                    overlayText.value = `处理 ${data.frameCount} 帧...`;
                } else if (status === 'resultReady') {
                    statusText.value = '请点头确认，或摇头切换候选词';
                    overlayText.value = data.prediction;
                    // 启动头部动作等待（超时自动取消）
                    startHeadGestureWaiting();
                } else if (status === 'tooShort') {
                    statusText.value = '时间有点短，请再比划一次 😊';
                } else if (status === 'recognitionFailed') {
                    statusText.value = '抱歉，没能理解，请再试一次 😊';
                } else if (status === 'networkError') {
                    statusText.value = '连接出了点问题';
                } else if (status === 'wordConfirmed') {
                    statusText.value = data.isAuto ? `✓ 已自动确认「${data.word}」` : `✓ 好的，已记录「${data.word}」`;
                } else if (status === 'emptySequence') {
                    alert('请先通过手语输入一些内容');
                }
            },
            onHeadGesture: (gesture) => {
                if (!waitingForHeadGesture.value) return;
                if (gesture.type === 'nod') {
                    // 点头：确认当前候选词
                    const currentCandidate = currentCandidates.value[currentCandidateIndex.value];
                    if (currentCandidate) {
                        agent.confirmWord(currentCandidate.label);
                        waitingForHeadGesture.value = false;
                        clearHeadGestureTimer();
                        statusText.value = '✅ 已确认！';
                    }
                } else if (gesture.type === 'shake') {
                    // 摇头：切换到下一个候选词
                    if (currentCandidates.value.length > 1) {
                        let nextIdx = currentCandidateIndex.value + 1;
                        if (nextIdx >= currentCandidates.value.length) nextIdx = 0; // 循环
                        currentCandidateIndex.value = nextIdx;
                        const newCandidate = currentCandidates.value[nextIdx];
                        currentWord.value = newCandidate.label;
                        currentConfidence.value = `${(newCandidate.confidence * 100).toFixed(1)}%`;
                        statusText.value = `切换到「${newCandidate.label}」，点头确认，摇头继续切换`;
                        // 重置超时计时
                        resetHeadGestureTimeout();
                    } else {
                        statusText.value = '仅有一个候选词，请点头确认或重新识别';
                    }
                }
            }
        });

        // 头部动作等待状态
        const waitingForHeadGesture = ref(false);
        let headGestureTimeout = null;

        const startHeadGestureWaiting = () => {
            waitingForHeadGesture.value = true;
            agent.enableHeadGesture(true);
            // 设置超时，如果10秒内无动作，自动放弃并提示
            headGestureTimeout = setTimeout(() => {
                if (waitingForHeadGesture.value) {
                    waitingForHeadGesture.value = false;
                    agent.enableHeadGesture(false);
                    statusText.value = '超时未检测到动作，请重新识别';
                    currentCandidates.value = [];
                }
            }, 10000);
        };

        const resetHeadGestureTimeout = () => {
            if (headGestureTimeout) clearTimeout(headGestureTimeout);
            headGestureTimeout = setTimeout(() => {
                if (waitingForHeadGesture.value) {
                    waitingForHeadGesture.value = false;
                    agent.enableHeadGesture(false);
                    statusText.value = '超时未检测到动作，请重新识别';
                    currentCandidates.value = [];
                }
            }, 10000);
        };

        const clearHeadGestureTimer = () => {
            if (headGestureTimeout) clearTimeout(headGestureTimeout);
            waitingForHeadGesture.value = false;
            agent.enableHeadGesture(false);
        };

        // Vue 响应式数据
        const vueWordSequence = ref([]);
        const currentWord = ref('-');
        const currentConfidence = ref('-');
        const currentCandidates = ref([]);
        const currentCandidateIndex = ref(0);
        const statusText = ref('请先开启摄像头');
        const overlayText = ref('等待开始...');
        const overlayClass = ref('overlay');
        const cameraStarted = ref(false);
        const recordDisabled = ref(true);

        // 当前患者姓名
        const currentPatientName = computed(() => {
            refreshKey.value;
            if (!currentId.value) return '';
            const p = getPatientById(currentId.value);
            return p ? p.name : '';
        });

        const goToRoleSelect = () => {
            window.location.href = 'index.html';
        };

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
            agent.stopCamera();
            cameraStarted.value = false;
            recordDisabled.value = true;
        };

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

        const goToSignTriage = () => {
            view.value = 'sign-triage';
        };

        const goBack = () => {
            view.value = 'reg-home';
        };

        const startCamera = async () => {
            try {
                await agent.startCamera('webcam', 'canvas');
                cameraStarted.value = true;
                recordDisabled.value = false;
                statusText.value = '就绪，可以开始比划了';
                overlayText.value = '准备就绪';
                overlayClass.value = 'overlay';
            } catch (error) {
                alert('摄像头启动失败: ' + error.message);
            }
        };

        const toggleRecording = () => {
            if (agent.isRecording) {
                agent.stopRecording();
                const btn = document.getElementById('recordBtn');
                btn.textContent = '⏺ 开始识别';
                btn.classList.remove('btn-recording');
            } else {
                agent.startRecording();
                const btn = document.getElementById('recordBtn');
                btn.textContent = '⏹ 停止';
                btn.classList.add('btn-recording');
            }
        };

        const clearSequence = () => {
            agent.clearSequence();
        };

        const polishSequence = async () => {
            const result = await agent.polishSequence();
            if (result) {
                alert(`AI 整理结果：${result.polished}`);
                if (result.triage) {
                    alert(`分诊建议：${result.triage.department} - ${result.triage.advice}`);
                }
            }
        };

        // ========== 呼叫人工功能 ==========
        const sendPatientMessage = (text) => {
            if (!text.trim() || !currentId.value) return;
            addMessage(currentId.value, { type: 'pat', content: text });
            alert('消息已发送给医生');
            voiceText.value = '';
            recognizedText.value = '';
        };

        const toggleVoiceRecording = async () => {
            if (isRecordingVoice.value) {
                stopVoiceRecording();
            } else {
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
                mediaRecorder = new MediaRecorder(stream);
                audioChunks = [];
                mediaRecorder.ondataavailable = event => {
                    audioChunks.push(event.data);
                };
                mediaRecorder.onstop = () => {
                    const audioBlob = new Blob(audioChunks, { type: 'audio/webm' });
                    sendVoiceToBackend(audioBlob);
                    stream.getTracks().forEach(track => track.stop());
                };
                mediaRecorder.start();
                isRecordingVoice.value = true;
            } catch (error) {
                alert('无法访问麦克风: ' + error.message);
            }
        };

        const stopVoiceRecording = () => {
            if (mediaRecorder && isRecordingVoice.value) {
                mediaRecorder.stop();
                isRecordingVoice.value = false;
            }
        };

        const sendVoiceToBackend = async (audioBlob) => {
            const formData = new FormData();
            formData.append('audio', audioBlob);
            try {
                const response = await fetch('http://localhost:8000/speech-to-text', {
                    method: 'POST',
                    body: formData
                });
                const result = await response.json();
                if (result.success) {
                    recognizedText.value = result.text;
                } else {
                    alert('语音识别失败');
                }
            } catch (error) {
                alert('语音识别服务连接失败');
            }
        };

        const confirmVoiceText = () => {
            if (recognizedText.value.trim()) {
                voiceText.value = recognizedText.value;
                recognizedText.value = '';
            }
        };

        const cancelVoiceText = () => {
            recognizedText.value = '';
        };

        watch(view, (newVal) => {
            if (newVal === 'sign-triage') {
                setTimeout(() => {
                    if (!cameraStarted.value) {
                        startCamera().catch(e => console.warn('摄像头启动失败', e));
                    }
                }, 300);
            }
        });

        onUnmounted(() => {
            agent.stopCamera();
            clearHeadGestureTimer();
        });

        return {
            view, currentId, idCardInput, currentPatientName, vueWordSequence,
            showChat,
            isRecordingVoice, voiceText, recognizedText,
            goToRoleSelect, patientLogin, logout,
            quickAppointment, quickTakeNumber, quickPayment,
            goToSignTriage, goBack,
            startCamera, toggleRecording, cameraStarted, recordDisabled,
            currentWord, currentConfidence,
            statusText, overlayText, overlayClass,
            clearSequence, polishSequence,
            sendPatientMessage,
            toggleVoiceRecording, confirmVoiceText, cancelVoiceText
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

            <!-- 手语分诊页面 - 移除候选词列表，保留头部动作提示 -->
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
                                    <div :class="overlayClass" id="overlay">{{ overlayText }}</div>
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
                                    <span class="status-dot" :class="{'active': cameraStarted}" id="statusDot"></span>
                                    <span id="statusText">{{ statusText }}</span>
                                </div>
                                <div class="tips-box mt-3 p-3 bg-blue-50 rounded-lg text-xs text-gray-600">
                                    <strong>💡 小贴士：</strong> 光线充足、手部在胸前比划效果更好。识别后请面对摄像头做点头或摇头动作。
                                </div>
                            </div>

                            <!-- 右侧：AI 交互区 + 语音识别区 -->
                            <div class="space-y-6">
                                <!-- AI 交互框 -->
                                <div class="bg-white rounded-2xl shadow-sm p-4 border border-blue-100">
                                    <h2 class="text-lg font-bold mb-4 flex items-center gap-2">🤖 AI 助手</h2>
                                    <!-- 当前识别结果 -->
                                    <div class="current-word text-center p-6 bg-orange-50 rounded-xl mb-4">
                                        <div class="label text-4xl font-bold text-orange-600">{{ currentWord }}</div>
                                        <div class="confidence text-sm text-gray-500 mt-2">{{ currentConfidence }}</div>
                                    </div>

                                    <!-- 已确认的表达 -->
                                    <div class="sequence-section mt-4">
                                        <h3 class="font-medium mb-2">📝 已确认的表达</h3>
                                        <div class="word-sequence flex flex-wrap gap-2 min-h-[50px] p-3 bg-gray-50 rounded-lg">
                                            <span v-if="vueWordSequence.length === 0" class="placeholder text-gray-400">通过手语识别后，点头确认添加</span>
                                            <span v-for="(word, idx) in vueWordSequence" :key="idx" class="word-tag bg-green-100 px-3 py-1 rounded-full text-sm">
                                                {{ word }}
                                                <button @click="agent.deleteWord(idx)" class="ml-2 text-red-500 hover:text-red-700">&times;</button>
                                            </span>
                                        </div>
                                        <div class="flex justify-end gap-2 mt-3">
                                            <button @click="clearSequence" class="text-sm text-gray-500 hover:text-gray-700">清空</button>
                                            <button @click="polishSequence" class="text-sm bg-indigo-50 text-indigo-600 px-3 py-1 rounded-full hover:bg-indigo-100">✨ 润色并分诊</button>
                                        </div>
                                    </div>
                                </div>

                                <!-- 人工帮助侧边栏（语音输入） -->
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
                                        <input v-model="voiceText" type="text" class="w-full border rounded-lg p-2 mb-2" placeholder="输入文字或使用语音...">
                                        <div v-if="recognizedText" class="mb-3 p-2 bg-yellow-50 border border-yellow-200 rounded-lg">
                                            <div class="text-sm text-gray-600 mb-2">识别结果：</div>
                                            <div class="flex items-center gap-2">
                                                <span class="flex-1 text-gray-800">{{ recognizedText }}</span>
                                                <button @click="confirmVoiceText" class="bg-green-500 text-white px-3 py-1 rounded text-sm">确认</button>
                                                <button @click="cancelVoiceText" class="bg-gray-300 text-gray-700 px-3 py-1 rounded text-sm">取消</button>
                                            </div>
                                        </div>
                                        <div class="flex items-center gap-2 mb-3">
                                            <button @click="toggleVoiceRecording" 
                                                    :class="isRecordingVoice ? 'bg-red-500 hover:bg-red-600' : 'bg-green-500 hover:bg-green-600'"
                                                    class="text-white px-4 py-2 rounded-lg text-sm flex items-center gap-1 transition-colors">
                                                <span v-if="!isRecordingVoice">🎤 开始语音</span>
                                                <span v-else>⏹ 停止录音</span>
                                            </button>
                                            <span v-if="isRecordingVoice" class="text-sm text-red-500 animate-pulse">录音中...</span>
                                        </div>
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