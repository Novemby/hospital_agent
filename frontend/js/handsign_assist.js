import { createApp, ref, onMounted, onUnmounted } from 'vue';
import { SignLanguageAgent } from './services/signLanguageAgent.js';
import { getPatientById, addMessage } from './services/patientService.js';

const app = createApp({
    setup() {
        // ==================== 手语识别相关状态 ====================
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
        const waitingForHeadGesture = ref(false);
        let headGestureTimeout = null;

        // 手语 agent
        const agent = new SignLanguageAgent({
            apiUrl: 'http://localhost:8000',
            onResult: (result) => {
                currentWord.value = result.prediction;
                currentConfidence.value = `${(result.confidence * 100).toFixed(1)}%`;
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
                    startHeadGestureWaiting();
                } else if (status === 'tooShort') {
                    statusText.value = '时间有点短，请再比划一次 😊';
                } else if (status === 'recognitionFailed') {
                    statusText.value = '抱歉，没能理解，请再试一次 😊';
                } else if (status === 'networkError') {
                    statusText.value = '连接出了点问题';
                } else if (status === 'wordConfirmed') {
                    statusText.value = data.isAuto ? `✓ 已自动确认「${data.word}」` : `✓ 好的，已记录「${data.word}」`;
                    // 将确认的词作为患者消息发送给医生
                    if (patientId) {
                        addMessage(patientId, { type: 'pat', content: `[手语] ${data.word}` });
                    }
                } else if (status === 'emptySequence') {
                    alert('请先通过手语输入一些内容');
                }
            },
            onHeadGesture: (gesture) => {
                if (!waitingForHeadGesture.value) return;
                if (gesture.type === 'nod') {
                    const currentCandidate = currentCandidates.value[currentCandidateIndex.value];
                    if (currentCandidate) {
                        agent.confirmWord(currentCandidate.label);
                        waitingForHeadGesture.value = false;
                        clearHeadGestureTimer();
                        statusText.value = '✅ 已确认！';
                    }
                } else if (gesture.type === 'shake') {
                    if (currentCandidates.value.length > 1) {
                        let nextIdx = currentCandidateIndex.value + 1;
                        if (nextIdx >= currentCandidates.value.length) nextIdx = 0;
                        currentCandidateIndex.value = nextIdx;
                        const newCandidate = currentCandidates.value[nextIdx];
                        currentWord.value = newCandidate.label;
                        currentConfidence.value = `${(newCandidate.confidence * 100).toFixed(1)}%`;
                        statusText.value = `切换到「${newCandidate.label}」，点头确认，摇头继续切换`;
                        resetHeadGestureTimeout();
                    } else {
                        statusText.value = '仅有一个候选词，请点头确认或重新识别';
                    }
                }
            }
        });

        // 头部动作辅助函数
        const startHeadGestureWaiting = () => {
            waitingForHeadGesture.value = true;
            agent.enableHeadGesture(true);
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

        // 手语识别操作方法
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

        // ==================== 医生消息相关 ====================
        const doctorMessages = ref([]);
        const patientId = new URLSearchParams(window.location.search).get('patientId');

        const loadDoctorMessages = () => {
            if (!patientId) return;
            const patient = getPatientById(patientId);
            if (patient && patient.messages) {
                // 筛选医生发送的消息，并按 id（时间）升序排列
                doctorMessages.value = patient.messages
                    .filter(m => m.type === 'doc')
                    .sort((a, b) => a.id - b.id);
            } else {
                doctorMessages.value = [];
            }
        };

        const handleStorageChange = (e) => {
            if (e.key === 'smart_hospital_patients') {
                loadDoctorMessages();
            }
        };

        const formatTime = (timestamp) => {
            if (!timestamp) return '';
            const date = new Date(timestamp);
            return `${date.getHours().toString().padStart(2, '0')}:${date.getMinutes().toString().padStart(2, '0')}`;
        };

        // ==================== 生命周期 ====================
        onMounted(() => {
            // 加载医生消息
            loadDoctorMessages();
            window.addEventListener('storage', handleStorageChange);
        });

        onUnmounted(() => {
            agent.stopCamera();
            clearHeadGestureTimer();
            window.removeEventListener('storage', handleStorageChange);
        });

        return {
            // 手语识别
            vueWordSequence,
            currentWord,
            currentConfidence,
            statusText,
            overlayText,
            overlayClass,
            cameraStarted,
            recordDisabled,
            startCamera,
            toggleRecording,
            clearSequence,
            polishSequence,
            // 医生消息
            doctorMessages,
            formatTime,
            // 辅助
            agent // 用于模板中可能的事件绑定
        };
    },
    template: `
        <div class="max-w-7xl mx-auto p-4">
            <div class="bg-white rounded-3xl shadow-xl overflow-hidden">
                <div class="bg-blue-600 p-4 text-white flex justify-between items-center">
                    <span class="font-bold">🤟 手语翻译官 · 患者辅助窗口</span>
                    <button @click="window.close()" class="text-sm bg-white/20 px-4 py-1 rounded-full hover:bg-white/30">关闭窗口</button>
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

                        <!-- 右侧：AI 交互区 + 医生消息区 -->
                        <div class="space-y-6">
                            <!-- AI 交互框 -->
                            <div class="bg-white rounded-2xl shadow-sm p-4 border border-blue-100">
                                <h2 class="text-lg font-bold mb-4 flex items-center gap-2">🤖 AI 助手</h2>
                                <div class="current-word text-center p-6 bg-orange-50 rounded-xl mb-4">
                                    <div class="label text-4xl font-bold text-orange-600">{{ currentWord }}</div>
                                    <div class="confidence text-sm text-gray-500 mt-2">{{ currentConfidence }}</div>
                                </div>

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

                            <!-- 医生消息窗口 -->
                            <div class="bg-white rounded-2xl shadow-sm p-4 border border-gray-200">
                                <h3 class="font-bold text-lg mb-3 flex items-center gap-2">
                                    <span>👨‍⚕️</span> 医生消息
                                </h3>
                                <div class="space-y-2 max-h-80 overflow-y-auto">
                                    <div v-if="doctorMessages.length === 0" class="text-gray-400 text-center py-4">
                                        暂无医生消息
                                    </div>
                                    <div v-for="msg in doctorMessages" :key="msg.id" class="bg-blue-50 p-3 rounded-lg">
                                        <div class="text-sm text-blue-800">{{ msg.content }}</div>
                                        <div class="text-xs text-gray-400 mt-1">{{ formatTime(msg.created_at) }}</div>
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