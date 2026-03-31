import { createApp, ref, computed, watch, onUnmounted, nextTick } from 'vue';
import { getPatientById, upsertPatient } from './services/patientService.js';
import { patientLoginAPI, setCurrentPatient, getCurrentPatient, clearAuth } from './services/authService.js';
import { SignLanguageAgent } from './services/signLanguageAgent.js';

const API_BASE_URL = 'http://localhost:8000/api';

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

        // 聊天消息列表（用于展示发送的消息）
        const chatMessages = ref([]);
        const chatContainer = ref(null); // 对话框滚动锚点

        // 挂号 Agent 文本会话
        const agentSessionId = ref(`reg-${Date.now()}`);
        const sessionId = ref(`session_${Date.now()}`);
        const agentReply = ref('你好，我是挂号助手。你可以说：我想挂发热门诊，或者向我描述你的不适。');
        const agentInputText = ref('');
        const streamText = ref('');
        const triageResult = ref(null);
        // 🌟 新增：存储后端传回的 JSON 结构化症状，用于实时卡片渲染
        const parsedSymptoms = ref({});

        const agentMessages = ref([
            { role: 'assistant', content: '你好，我是挂号助手。你可以说：我想挂发热门诊，或者向我描述你的不适。' }
        ]);
        const agentState = ref('INIT');
        const agentOptions = ref([]);
        const agentLoading = ref(false);

        // 连续识别状态
        const continuousActive = ref(false);
        const continuousWords = ref([]);  // 当前句子已识别的词

        // 沉浸式助手状态
        const assistantPhase = ref('WELCOME');  // WELCOME | LISTENING | THINKING | REPLY | SUCCESS
        const assistantDisplayText = ref('');

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
            onWordRecognized: (word, confidence) => {
                // 连续识别：将词汇追加到输入框
                continuousWords.value.push(word);
                const current = agentInputText.value.trim();
                agentInputText.value = current ? `${current} ${word}` : word;
                statusText.value = `✅ 识别到「${word}」(${(confidence * 100).toFixed(0)}%)`;
                overlayText.value = `✓ ${word}`;
                overlayClass.value = 'overlay result';
                assistantPhase.value = 'LISTENING';
            },
            onSentenceComplete: (words) => {
                // 句子结束：只提示用户确认，不自动发送
                statusText.value = '✅ 识别完成，请确认后发送';
                overlayText.value = '等待确认';
                overlayClass.value = 'overlay';
            },
            onSequenceUpdate: (seq) => {
                vueWordSequence.value = seq;
            },
            onStatusChange: (status, data) => {
                if (status === 'recordingStart') {
                    statusText.value = continuousActive.value ? '检测到手势，识别中...' : '我在看着，请比划...';
                    overlayText.value = '录制中...';
                    overlayClass.value = 'overlay recording';
                } else if (status === 'sending') {
                    statusText.value = `正在理解... (${data.frameCount}帧)`;
                    overlayText.value = `处理 ${data.frameCount} 帧...`;
                } else if (status === 'resultReady') {
                    if (!continuousActive.value) {
                        statusText.value = '请点头确认，或摇头切换候选词';
                        overlayText.value = data.prediction;
                        startHeadGestureWaiting();
                    }
                } else if (status === 'continuousReady') {
                    statusText.value = '🟢 连续识别中，请比划手语...';
                    overlayText.value = '等待手势';
                    overlayClass.value = 'overlay';
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

        // 头部动作等待状态
        const waitingForHeadGesture = ref(false);
        let headGestureTimeout = null;

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

        // 挂号弹窗状态
        const showAppointmentModal = ref(false);
        const appointmentStep = ref(1);
        const departmentOptions = ref([]);
        const selectedDepartment = ref('');
        const selectedDate = ref(new Date().toISOString().slice(0, 10));
        const slotOptions = ref([]);
        const selectedSlotId = ref(null);
        const loadingSlots = ref(false);

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
                agentSessionId.value = `reg-${backendData.id}-${Date.now()}`;
                sessionId.value = `session_${backendData.id}_${Date.now()}`;
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

        const resetAppointmentModal = () => {
            appointmentStep.value = 1;
            departmentOptions.value = [];
            selectedDepartment.value = '';
            selectedDate.value = new Date().toISOString().slice(0, 10);
            slotOptions.value = [];
            selectedSlotId.value = null;
            loadingSlots.value = false;
        };

        const quickAppointment = async () => {
            if (!currentId.value) {
                alert('请先登录患者账号');
                return;
            }
            resetAppointmentModal();
            showAppointmentModal.value = true;

            try {
                const deptResp = await fetch(`${API_BASE_URL}/registration/departments`);
                const deptResult = await deptResp.json();
                if (!deptResp.ok) throw new Error(deptResult.detail || '获取科室列表失败');
                departmentOptions.value = deptResult.departments || [];
                if (departmentOptions.value.length > 0) {
                    selectedDepartment.value = departmentOptions.value[0];
                }
            } catch (error) {
                showAppointmentModal.value = false;
                alert(error.message || '加载科室失败');
            }
        };

        const closeAppointmentModal = () => {
            showAppointmentModal.value = false;
            resetAppointmentModal();
        };

        const loadSlotOptions = async () => {
            if (!selectedDepartment.value || !selectedDate.value) {
                alert('请先选择科室和日期');
                return;
            }
            loadingSlots.value = true;
            selectedSlotId.value = null;
            try {
                const slotResp = await fetch(`${API_BASE_URL}/registration/slots?department=${encodeURIComponent(selectedDepartment.value)}&date=${encodeURIComponent(selectedDate.value)}`);
                const slotResult = await slotResp.json();
                if (!slotResp.ok) throw new Error(slotResult.detail || '查询号源失败');
                slotOptions.value = (slotResult.slots || []).filter(s => (s.remain || 0) > 0);
                appointmentStep.value = 2;
                if (!slotOptions.value.length) alert('该日期暂无可用号源，请更换日期');
            } catch (error) {
                alert(error.message || '查询号源失败');
            } finally {
                loadingSlots.value = false;
            }
        };

        const submitAppointment = async () => {
            if (!selectedSlotId.value) {
                alert('请选择一个可用时段');
                return;
            }
            const selectedSlot = slotOptions.value.find(s => String(s.slot_id) === String(selectedSlotId.value));
            if (!selectedSlot) return alert('所选时段无效，请重新选择');

            try {
                const response = await fetch(`${API_BASE_URL}/registration/submit`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        patient_id: Number(currentId.value),
                        department: selectedDepartment.value,
                        schedule_id: selectedSlot.slot_id,
                        triage_symptoms: null
                    })
                });

                const result = await response.json();
                if (!response.ok) throw new Error(result.detail || '挂号失败');

                const patient = getPatientById(currentId.value);
                if (patient) {
                    patient.dept = selectedDepartment.value;
                    upsertPatient(patient);
                }

                closeAppointmentModal();
                alert(`挂号成功：${result.department}（${selectedSlot.period}），排队号 ${result.queue_number}`);
            } catch (error) {
                alert(error.message || '挂号失败，请稍后再试');
            }
        };

        const scrollToBottom = async () => {
            await nextTick();
            if (chatContainer.value) {
                chatContainer.value.scrollTop = chatContainer.value.scrollHeight;
            }
        };

        const sendToAgent = async (text) => {
            const content = (text || '').trim();
            if (!content || !currentId.value) return;

            agentMessages.value.push({ role: 'user', content });
            agentLoading.value = true;
            assistantPhase.value = 'THINKING';

            try {
                const response = await fetch('http://localhost:8000/api/agent/register/chat', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        session_id: sessionId.value,
                        patient_id: parseInt(currentId.value),
                        message: content,
                    }),
                });

                const data = await response.json();
                if (!response.ok) throw new Error(data.detail || 'Agent 请求失败');

                agentReply.value = data.reply || '收到。';
                agentOptions.value = Array.isArray(data.options) ? data.options : [];
                agentState.value = data.state || agentState.value;
                agentMessages.value.push({ role: 'assistant', content: agentReply.value });
                assistantDisplayText.value = agentReply.value;
                assistantPhase.value = data.state === 'SUCCESS' ? 'SUCCESS' : 'REPLY';

                // 🌟 解析并渲染后端传来的结构化症状
                if (data?.slots?.triage_symptoms) {
                    try {
                        parsedSymptoms.value = JSON.parse(data.slots.triage_symptoms);
                    } catch (e) {
                        console.warn('解析 triage_symptoms 失败:', e);
                    }
                }

                if (data?.slots?.department) {
                    triageResult.value = {
                        department: data.slots.department,
                        date: data.slots.date || null,
                        period: data.slots.period || null,
                    };
                }

                scrollToBottom();

                if (data.state === 'SUCCESS') {
                    // 等待渲染完成后再提示成功
                    setTimeout(() => {
                        alert(data.reply || '挂号成功');
                        view.value = 'reg-home';
                    }, 500);
                }
            } catch (error) {
                const errMsg = `请求失败：${error.message || '网络异常'}`;
                agentReply.value = errMsg;
                agentMessages.value.push({ role: 'assistant', content: errMsg });
                assistantDisplayText.value = errMsg;
                assistantPhase.value = 'REPLY';
            } finally {
                agentLoading.value = false;
                agentInputText.value = '';
            }
        };

        const sendAgentInput = async () => {
            await sendToAgent(agentInputText.value);
        };

        const clickOption = async (option) => {
            const text = typeof option === 'string'
                ? option
                : (option?.period || option?.department || option?.doctor_name || '确认');
            streamText.value = text;
            await sendToAgent(text);
        };

        const startScanning = async () => {
            streamText.value = '我感觉左边肩膀有点酸痛，想挂号';
            await sendToAgent(streamText.value);
        };

        const quickTakeNumber = () => {
            alert(`现场取号成功，您前面还有 ${Math.floor(Math.random()*5)+1} 人`);
        };
        const quickPayment = () => {
            alert('当前待缴费金额 ￥0.00，无欠费');
        };

        const goToSignTriage = () => {
            view.value = 'sign-triage';
            // 初始化清空上一次的状态
            parsedSymptoms.value = {};
            agentState.value = 'INIT';
            assistantPhase.value = 'WELCOME';
            assistantDisplayText.value = '';
        };

        const goBack = () => {
            if (continuousActive.value) {
                agent.stopContinuous();
                continuousActive.value = false;
            }
            agent.stopCamera();
            cameraStarted.value = false;
            view.value = 'reg-home';
        };

        const startCamera = async () => {
            try {
                await agent.startCamera('webcam', 'canvas');
                cameraStarted.value = true;
                recordDisabled.value = false;
                continuousActive.value = false;
                statusText.value = '摄像头已就绪，点击"开始识别"';
                overlayText.value = '等待开始';
                overlayClass.value = 'overlay';
            } catch (error) {
                alert('摄像头启动失败: ' + error.message);
            }
        };

        const startContinuousRecognition = () => {
            agent.startContinuous();
            continuousActive.value = true;
            assistantPhase.value = 'LISTENING';
            statusText.value = '🟢 识别中，请比划手语...';
            overlayText.value = '等待手势';
            overlayClass.value = 'overlay';
        };

        const toggleContinuous = () => {
            if (continuousActive.value) {
                agent.stopContinuous();
                agent.clearSentenceBuffer();
                continuousActive.value = false;
                statusText.value = '连续识别已暂停';
            } else {
                agent.startContinuous();
                continuousActive.value = true;
                assistantPhase.value = 'LISTENING';
                statusText.value = '🟢 连续识别已启动，请比划手语...';
            }
        };

        const clearSignInput = () => {
            agentInputText.value = '';
            continuousWords.value = [];
            agent.clearSentenceBuffer();
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

        const sendPatientMessage = (text) => {
            if (!text.trim()) return;
            chatMessages.value.push({
                id: Date.now(),
                text: text.trim(),
                sender: 'patient',
                time: new Date().toLocaleTimeString()
            });
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
                assistantPhase.value = 'WELCOME';
                assistantDisplayText.value = '';
            }
        });

        onUnmounted(() => {
            if (continuousActive.value) {
                agent.stopContinuous();
            }
            agent.stopCamera();
            clearHeadGestureTimer();
        });

        return {
            view, currentId, idCardInput, currentPatientName, vueWordSequence,
            showChat, isRecordingVoice, voiceText, recognizedText, chatMessages, chatContainer,
            sessionId, agentReply, agentInputText, streamText, triageResult, parsedSymptoms,
            agentMessages, agentState, agentOptions, agentLoading,
            showAppointmentModal, appointmentStep, departmentOptions, selectedDepartment, selectedDate,
            slotOptions, selectedSlotId, loadingSlots,
            goToRoleSelect, patientLogin, logout,
            quickAppointment, closeAppointmentModal, loadSlotOptions, submitAppointment,
            sendAgentInput, clickOption, sendToAgent, startScanning,
            quickTakeNumber, quickPayment, goToSignTriage, goBack,
            startCamera, startContinuousRecognition, toggleRecording, cameraStarted, recordDisabled,
            currentWord, currentConfidence, statusText, overlayText, overlayClass,
            clearSequence, polishSequence, sendPatientMessage,
            toggleVoiceRecording, confirmVoiceText, cancelVoiceText,
            continuousActive, continuousWords, toggleContinuous, clearSignInput,
            assistantPhase, assistantDisplayText
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

            <!-- 预约挂号弹窗 -->
            <div v-if="showAppointmentModal" class="fixed inset-0 bg-black/40 flex items-center justify-center z-50">
                <div class="bg-white w-[92%] max-w-2xl rounded-2xl shadow-2xl overflow-hidden">
                    <div class="px-6 py-4 border-b flex items-center justify-between">
                        <h3 class="text-xl font-bold text-gray-800">预约挂号</h3>
                        <button @click="closeAppointmentModal" class="text-gray-400 hover:text-gray-600 text-2xl leading-none">&times;</button>
                    </div>

                    <div class="p-6 space-y-5">
                        <div class="text-sm text-gray-500">步骤 {{ appointmentStep }} / 2</div>

                        <div v-if="appointmentStep === 1" class="space-y-4">
                            <div>
                                <label class="block text-sm font-medium text-gray-700 mb-2">选择科室</label>
                                <select v-model="selectedDepartment" class="w-full border rounded-xl px-3 py-2 focus:outline-none focus:ring-2 focus:ring-blue-400">
                                    <option v-for="dept in departmentOptions" :key="dept" :value="dept">{{ dept }}</option>
                                </select>
                            </div>
                            <div>
                                <label class="block text-sm font-medium text-gray-700 mb-2">选择日期</label>
                                <input v-model="selectedDate" type="date" class="w-full border rounded-xl px-3 py-2 focus:outline-none focus:ring-2 focus:ring-blue-400">
                            </div>
                            <div class="flex justify-end gap-3 pt-2">
                                <button @click="closeAppointmentModal" class="px-4 py-2 rounded-lg border text-gray-600 hover:bg-gray-50">取消</button>
                                <button @click="loadSlotOptions" class="px-4 py-2 rounded-lg bg-blue-600 text-white hover:bg-blue-700" :disabled="loadingSlots">
                                    {{ loadingSlots ? '加载中...' : '下一步：选择时段' }}
                                </button>
                            </div>
                        </div>

                        <div v-else class="space-y-4">
                            <div class="text-sm text-gray-600">科室：<span class="font-semibold text-gray-800">{{ selectedDepartment }}</span>，日期：<span class="font-semibold text-gray-800">{{ selectedDate }}</span></div>
                            <div class="grid grid-cols-1 md:grid-cols-2 gap-3 max-h-72 overflow-y-auto">
                                <label v-for="slot in slotOptions" :key="slot.slot_id" class="border rounded-xl p-4 cursor-pointer hover:border-blue-400" :class="String(selectedSlotId) === String(slot.slot_id) ? 'border-blue-500 bg-blue-50' : 'border-gray-200'">
                                    <input type="radio" class="mr-2" :value="slot.slot_id" v-model="selectedSlotId">
                                    <span class="font-medium text-gray-800">{{ slot.period }}</span>
                                    <div class="text-sm text-gray-500 mt-1">余号：{{ slot.remain }} / {{ slot.capacity_total }}</div>
                                </label>
                            </div>
                            <div v-if="slotOptions.length === 0" class="text-sm text-orange-600 bg-orange-50 border border-orange-200 rounded-lg p-3">当前日期无可用号源，请返回修改日期或科室。</div>
                            <div class="flex justify-between gap-3 pt-2">
                                <button @click="appointmentStep = 1" class="px-4 py-2 rounded-lg border text-gray-600 hover:bg-gray-50">上一步</button>
                                <div class="flex gap-3">
                                    <button @click="closeAppointmentModal" class="px-4 py-2 rounded-lg border text-gray-600 hover:bg-gray-50">取消</button>
                                    <button @click="submitAppointment" class="px-4 py-2 rounded-lg bg-green-600 text-white hover:bg-green-700">确认挂号</button>
                                </div>
                            </div>
                        </div>
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
                                    <div :class="overlayClass" id="overlay">{{ overlayText }}</div>
                                    <div class="detection-status absolute top-2 right-2 bg-white/90 p-2 rounded-lg text-xs hidden md:block">
                                        <div class="detection-item"><span class="detection-dot" id="poseDot"></span>身体</div>
                                        <div class="detection-item"><span class="detection-dot" id="leftHandDot"></span>左手</div>
                                        <div class="detection-item"><span class="detection-dot" id="rightHandDot"></span>右手</div>
                                    </div>
                                </div>
                                <div class="controls flex gap-2 mt-4">
                                    <button v-if="!cameraStarted" id="startBtn" class="flex-1 bg-blue-600 text-white py-2 rounded-lg hover:bg-blue-700" @click="startCamera">📷 开启摄像头</button>
                                    <button v-else-if="!continuousActive" class="flex-1 bg-green-600 text-white py-2 rounded-lg hover:bg-green-700" @click="startContinuousRecognition">🤟 开始识别</button>
                                    <button v-else class="flex-1 bg-orange-500 text-white py-2 rounded-lg hover:bg-orange-600" @click="toggleContinuous">⏸ 暂停</button>
                                </div>
                                <div class="status-bar flex items-center gap-2 mt-3 p-2 rounded-lg text-sm" :class="continuousActive ? 'bg-green-50 border border-green-200' : 'bg-gray-100'">
                                    <span class="status-dot" :class="{'active': cameraStarted && continuousActive}" id="statusDot"></span>
                                    <span id="statusText">{{ statusText }}</span>
                                </div>
                                <div class="tips-box mt-3 p-3 bg-blue-50 rounded-lg text-xs text-gray-600">
                                    <strong>💡 使用方式：</strong> 开启摄像头后点击"开始识别"，识别到的词汇会出现在右侧，确认无误后点击"确认发送"。
                                </div>

                                <!-- 🌟 优化一：实时症状感知卡片（消除虚无感） -->
                                <div v-if="parsedSymptoms && parsedSymptoms.primary_symptom" class="bg-indigo-50 rounded-xl p-4 mt-4 border border-indigo-100 shadow-sm transition-all">
                                    <h3 class="text-sm font-bold text-indigo-800 mb-2 flex items-center gap-1">✨ AI 实时感知病情摘要</h3>
                                    <div class="grid grid-cols-2 gap-2 text-sm">
                                        <div class="bg-white p-2 rounded border border-indigo-50 flex items-center justify-between">
                                            <span class="text-gray-500">核心症状</span> 
                                            <span class="font-medium text-gray-800">{{parsedSymptoms.primary_symptom}}</span>
                                        </div>
                                        <div class="bg-white p-2 rounded border border-indigo-50 flex items-center justify-between">
                                            <span class="text-gray-500">具体部位</span> 
                                            <span class="font-medium" :class="parsedSymptoms.body_part ? 'text-gray-800' : 'text-orange-500'">{{parsedSymptoms.body_part || '待确认'}}</span>
                                        </div>
                                        <div class="bg-white p-2 rounded border border-indigo-50 flex items-center justify-between">
                                            <span class="text-gray-500">持续时间</span> 
                                            <span class="font-medium" :class="parsedSymptoms.duration ? 'text-gray-800' : 'text-orange-500'">{{parsedSymptoms.duration || '待确认'}}</span>
                                        </div>
                                        <div class="bg-white p-2 rounded border border-indigo-50 flex items-center justify-between">
                                            <span class="text-gray-500">推荐科室</span> 
                                            <span class="font-bold" :class="triageResult?.department ? 'text-indigo-600' : 'text-gray-400'">{{triageResult?.department || '分析中...'}}</span>
                                        </div>
                                    </div>
                                    <div v-if="parsedSymptoms.accompanying_symptoms && parsedSymptoms.accompanying_symptoms.length" class="mt-2 text-xs bg-white p-2 rounded border border-indigo-50">
                                        <span class="text-gray-500 mr-2">伴随症状:</span>
                                        <span v-for="sym in parsedSymptoms.accompanying_symptoms" :key="sym" class="inline-block bg-orange-100 text-orange-700 px-2 py-0.5 rounded mr-1 mb-1">{{sym}}</span>
                                    </div>
                                </div>
                            </div>

                            <!-- 右侧：AI 交互区 + 人工帮助侧边栏 -->
                            <div class="space-y-6">
                                <!-- AI 交互框 -->
                                <div class="bg-white rounded-2xl shadow-sm p-4 border border-blue-100 space-y-4 flex flex-col h-[650px]">
                                    <h2 class="text-lg font-bold flex items-center gap-2 shrink-0">🤖 AI 挂号助手</h2>

                                    <!-- 进度条 -->
                                    <div class="bg-slate-50 rounded-xl p-3 shrink-0">
                                        <div class="grid grid-cols-2 md:grid-cols-4 gap-2 text-xs text-center">
                                            <div class="px-2 py-1.5 rounded-lg font-medium transition-colors" :class="['INIT', 'COLLECT_INFO', 'QUERY_SLOTS', 'CONFIRM', 'SUCCESS'].includes(agentState) ? 'bg-green-100 text-green-700' : 'bg-white border text-gray-400'">1. 症状采录</div>
                                            <div class="px-2 py-1.5 rounded-lg font-medium transition-colors" :class="['QUERY_SLOTS', 'CONFIRM', 'SUCCESS'].includes(agentState) ? 'bg-green-100 text-green-700' : 'bg-white border text-gray-400'">2. 智能分诊</div>
                                            <div class="px-2 py-1.5 rounded-lg font-medium transition-colors" :class="['CONFIRM', 'SUCCESS'].includes(agentState) ? 'bg-green-100 text-green-700' : 'bg-white border text-gray-400'">3. 匹配时段</div>
                                            <div class="px-2 py-1.5 rounded-lg font-medium transition-colors" :class="agentState === 'SUCCESS' ? 'bg-green-100 text-green-700' : 'bg-white border text-gray-400'">4. 确认挂号</div>
                                        </div>
                                    </div>

                                    <!-- 沉浸式助手状态区 -->
                                    <div class="assistant-display flex-1 flex flex-col items-center justify-center rounded-2xl p-6 text-center transition-all" :class="'phase-' + assistantPhase.toLowerCase()">
                                        <!-- WELCOME -->
                                        <div v-if="assistantPhase === 'WELCOME'" class="space-y-3">
                                            <div class="text-5xl">🏥</div>
                                            <div class="text-2xl font-bold text-gray-800">请描述您的症状</div>
                                            <div class="text-gray-500 text-sm">比划手语，或直接打字告诉我</div>
                                        </div>
                                        <!-- LISTENING -->
                                        <div v-else-if="assistantPhase === 'LISTENING'" class="space-y-3">
                                            <div class="text-5xl">👂</div>
                                            <div class="text-xl font-bold text-green-700">我在看，请继续比划...</div>
                                            <div v-if="continuousWords.length > 0" class="flex flex-wrap gap-2 justify-center mt-2">
                                                <span v-for="(w, i) in continuousWords" :key="i" class="bg-green-100 text-green-800 px-3 py-1 rounded-full text-sm font-medium">{{ w }}</span>
                                            </div>
                                        </div>
                                        <!-- THINKING -->
                                        <div v-else-if="assistantPhase === 'THINKING'" class="space-y-3">
                                            <div class="text-5xl">🤔</div>
                                            <div class="text-xl font-bold text-blue-700">正在分析您的症状...</div>
                                            <div class="thinking-dots flex gap-2 justify-center mt-2">
                                                <span></span><span></span><span></span>
                                            </div>
                                        </div>
                                        <!-- REPLY -->
                                        <div v-else-if="assistantPhase === 'REPLY'" class="space-y-3 w-full">
                                            <div class="text-4xl">🤖</div>
                                            <div class="text-base text-gray-800 leading-relaxed bg-white rounded-2xl p-4 shadow-sm border border-orange-100 text-left">"{{ assistantDisplayText }}"</div>
                                            <!-- 选项按钮 -->
                                            <div class="flex flex-wrap gap-2 justify-center mt-2" v-if="agentOptions && agentOptions.length && agentState !== 'SUCCESS'">
                                                <button v-for="(op, idx) in agentOptions" :key="idx" @click="clickOption(op)" class="px-4 py-1.5 rounded-full border border-blue-300 text-blue-700 bg-white hover:bg-blue-50 text-sm shadow-sm">
                                                    {{ typeof op === 'string' ? op : (op.period || op.department || op.doctor_name || '确认') }}
                                                </button>
                                            </div>
                                        </div>
                                        <!-- SUCCESS -->
                                        <div v-else-if="assistantPhase === 'SUCCESS'" class="space-y-3">
                                            <div class="text-5xl">🎉</div>
                                            <div class="text-xl font-bold text-green-700">挂号成功！</div>
                                            <div class="text-gray-600 text-sm">{{ assistantDisplayText }}</div>
                                        </div>
                                    </div>

                                    <!-- 底部：识别结果 + 输入框 + 确认发送 -->
                                    <div class="shrink-0 space-y-2">
                                        <div class="flex items-center justify-between">
                                            <h3 class="font-medium text-sm text-gray-600">📝 手语识别结果</h3>
                                            <button @click="clearSignInput" class="text-xs text-gray-400 hover:text-red-500">清空</button>
                                        </div>
                                        <div class="word-sequence flex flex-wrap gap-2 min-h-[36px] p-2 rounded-lg items-center" :class="continuousWords.length > 0 ? 'bg-green-50 border-2 border-green-300' : 'bg-gray-50 border border-gray-100'">
                                            <span v-if="continuousWords.length === 0" class="text-gray-400 text-xs px-1">识别到的词汇将显示在这里</span>
                                            <span v-for="(word, idx) in continuousWords" :key="idx" class="word-tag bg-green-500 text-white px-2 py-1 rounded-full text-xs font-medium animate-fadeIn">{{ word }}</span>
                                        </div>
                                        <div class="flex gap-2">
                                            <input v-model="agentInputText" @keyup.enter="sendAgentInput" placeholder="可直接编辑或打字..." class="flex-1 border rounded-xl px-3 py-2 text-sm focus:ring-2 focus:ring-orange-300 outline-none" />
                                            <button @click="sendAgentInput" class="bg-blue-600 text-white px-4 py-2 rounded-xl text-sm hover:bg-blue-700 font-medium whitespace-nowrap">确认发送 →</button>
                                        </div>
                                    </div>
                                </div>

                                <!-- 人工帮助侧边栏 -->
                                <div class="bg-white rounded-2xl shadow-sm p-4 transition-all duration-300">
                                    <div class="flex justify-between items-center cursor-pointer" @click="showChat = !showChat">
                                        <h3 class="font-bold text-lg flex items-center gap-2">
                                            <span class="text-xl">📞</span> 呼叫人工导诊台
                                        </h3>
                                        <button class="text-blue-600 text-sm hover:underline">
                                            {{ showChat ? '收起 ▲' : '展开 ▼' }}
                                        </button>
                                    </div>
                                    <div v-show="showChat" class="mt-4 transition-all duration-300">
                                        <div class="chat-messages max-h-48 overflow-y-auto mb-4 space-y-2 border-t pt-2">
                                            <div v-for="msg in chatMessages" :key="msg.id" class="flex justify-end">
                                                <div class="bg-blue-500 text-white rounded-lg px-3 py-2 max-w-xs break-words">
                                                    <div class="text-sm">{{ msg.text }}</div>
                                                    <div class="text-xs text-blue-100 mt-1 text-right">{{ msg.time }}</div>
                                                </div>
                                            </div>
                                            <div v-if="chatMessages.length === 0" class="text-center text-gray-400 text-sm py-4">
                                                人工座席在线，如 AI 无法解决您的问题，请在此沟通
                                            </div>
                                        </div>

                                        <input v-model="voiceText" type="text" placeholder="输入求助信息..." class="w-full border rounded-lg p-2 mb-2 text-sm outline-none focus:border-blue-400">
                                        <div class="flex justify-end">
                                            <button @click="sendPatientMessage(voiceText)" class="bg-blue-600 text-white px-4 py-2 rounded-lg hover:bg-blue-700 text-sm">
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