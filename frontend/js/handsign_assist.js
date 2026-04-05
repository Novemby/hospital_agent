import { createApp, ref, computed, onMounted, onUnmounted, nextTick } from 'vue';
import { getPatientById, addMessage, saveChatMessage, syncPatientMessagesFromBackend, upsertPatient } from './services/patientService.js?v=20260401-2';
import { SignLanguageAgent } from './services/signLanguageAgent.js';

const app = createApp({
    setup() {
        // 解析 URL 参数获取关联信息
        const urlParams = new URLSearchParams(window.location.search);
        const patientId = urlParams.get('patientId') || '';
        const doctorId = urlParams.get('doctorId') || '';
        const registrationId = urlParams.get('registrationId') || '';

        const patientInfo = ref(null);
        const chatMessages = ref([]);
        const chatContainer = ref(null);

        // 手动文字输入与摘要
        const textInput = ref('');
        const aiSummary = ref('');
        const doctorContext = ref('');
        const riskNote = ref('');
        const patientReminder = ref('');
        const memoryHits = ref([]);

        // ==================== 🌟 新增：划词翻译与术语解释状态 ====================
        const selectedText = ref('');
        const showExplainBtn = ref(false);
        const explainBtnStyle = ref({ top: '0px', left: '0px' });
        const showExplainModal = ref(false);
        const explanationContent = ref('');
        const isExplaining = ref(false);

        // 处理鼠标划选文本的逻辑
        const handleMouseUp = () => {
            // 延迟获取，确保原生选择事件已完成
            setTimeout(() => {
                const selection = window.getSelection();
                const text = selection.toString().trim();

                // 只有选中了有效文本，且长度适中（避免全选），才弹出解释按钮
                if (text && text.length > 0 && text.length <= 20) {
                    selectedText.value = text;
                    const range = selection.getRangeAt(0);
                    const rect = range.getBoundingClientRect();

                    showExplainBtn.value = true;
                    // 将按钮定位在选中文字的正上方
                    explainBtnStyle.value = {
                        top: `${rect.top - 45}px`,
                        left: `${rect.left + (rect.width / 2) - 40}px`
                    };
                } else {
                    showExplainBtn.value = false;
                }
            }, 10);
        };

        // 隐藏悬浮按钮
        const hideExplainBtn = () => {
            showExplainBtn.value = false;
        };

        // 点击解释按钮，触发 Agent 检索
        const explainSelected = async () => {
            showExplainBtn.value = false;
            showExplainModal.value = true;
            isExplaining.value = true;
            explanationContent.value = '';

            const term = selectedText.value.trim();

            // 🌟 提取最近的对话上下文，帮助大模型更精准地解释（结合语境，消除歧义）
            const recentContext = chatMessages.value.slice(-4).filter(m => m.type !== 'sys').map(m =>
                `${m.type === 'doc' ? '医生' : '我'}：${m.content}`
            ).join('\n');

            try {
                // 🌟 修正：补上 /register 路径前缀
                const response = await fetch('http://localhost:8000/api/agent/register/consultation/explain', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        term: term,
                        context: recentContext
                    })
                });

                if (response.ok) {
                    const data = await response.json();
                    explanationContent.value = data.explanation || '未获取到解释内容。';
                } else {
                    explanationContent.value = '网络请求失败，请稍后再试。';
                }
            } catch (e) {
                console.warn("术语解释请求失败", e);
                // 兜底回复
                explanationContent.value = `Agent 提示：【${term}】是一个临床术语，目前网络异常无法查询，您可以向医生进一步询问。`;
            } finally {
                isExplaining.value = false;
            }
        };

        const closeExplainModal = () => {
            showExplainModal.value = false;
            window.getSelection().removeAllRanges(); // 关闭弹窗时取消文字选中状态
        };


        // 初始化加载患者数据
        const loadPatientData = async (syncBackend = false) => {
            if (patientId) {
                if (syncBackend) {
                    try {
                        await syncPatientMessagesFromBackend(patientId, registrationId || null);
                    } catch (e) {
                        console.warn('从后端同步消息失败', e);
                    }
                }
                // 每次都从 localStorage 获取最新数据
                const freshPatient = getPatientById(patientId);
                if (freshPatient) {
                    patientInfo.value = freshPatient;
                    if (freshPatient.messages) {
                        // 获取所有医患消息，按时间排序
                        chatMessages.value = freshPatient.messages
                            .filter(m => m.type === 'pat' || m.type === 'doc')
                            .sort((a, b) => (a.id || 0) - (b.id || 0));
                        scrollToBottom();
                    }
                }
            }
        };

        const handleStorageChange = (e) => {
            if (e.key === 'smart_hospital_patients' || e.key === null) {
                loadPatientData();
            }
        };

        // 定时刷新患者数据，确保消息实时更新
        let refreshInterval = null;

        onMounted(() => {
            loadPatientData(true);
            window.addEventListener('storage', handleStorageChange);

            // 每2秒刷新一次消息列表
            refreshInterval = setInterval(() => {
                loadPatientData(false);
            }, 2000);

            setTimeout(() => {
                startCamera();
            }, 500);

            // 点击页面空白处隐藏解释按钮
            document.addEventListener('mousedown', (e) => {
                if (showExplainBtn.value && !e.target.closest('#explain-float-btn')) {
                    hideExplainBtn();
                }
            });
        });

        onUnmounted(() => {
            window.removeEventListener('storage', handleStorageChange);
            if (refreshInterval) clearInterval(refreshInterval);
            agent.stopCamera();
            clearHeadGestureTimer();
        });

        const scrollToBottom = async () => {
            await nextTick();
            if (chatContainer.value) {
                chatContainer.value.scrollTop = chatContainer.value.scrollHeight;
            }
        };

        // ========== 摄像头与手语识别状态 ==========
        const vueWordSequence = ref([]);
        const currentWord = ref('-');
        const currentConfidence = ref('-');
        const currentCandidates = ref([]);
        const currentCandidateIndex = ref(0);

        const statusText = ref('等待开启摄像头...');
        const overlayText = ref('');
        const overlayClass = ref('overlay hidden');
        const cameraStarted = ref(false);
        const isPolishing = ref(false);

        // 连续识别模式状态
        const continuousActive = ref(false);
        const continuousStopNotice = ref(false);
        const continuousWords = ref([]);

        const waitingForHeadGesture = ref(false);
        let headGestureTimeout = null;

        // agent 在 setup() 内构造，回调直接闭包捕获上方所有 ref（与挂号界面相同结构）
        const agent = new SignLanguageAgent({
            apiUrl: 'http://localhost:5000',
            onResult: (result) => {
                currentWord.value = result.prediction;
                currentConfidence.value = `${(result.confidence * 100).toFixed(1)}%`;
                if (result.topPredictions && result.topPredictions.length) {
                    currentCandidates.value = result.topPredictions;
                    currentCandidateIndex.value = 0;
                } else {
                    currentCandidates.value = [];
                }
            },
            onSequenceUpdate: (seq) => { vueWordSequence.value = seq; },
            onWordRecognized: (word) => {
                if (continuousActive.value) {
                    continuousWords.value.push(word);
                    statusText.value = `✓ 识别到「${word}」`;
                }
            },
            onStatusChange: (status, data) => {
                if (status === 'recordingStart') {
                    statusText.value = continuousActive.value ? '检测到手势，识别中...' : '正在捕捉动作...';
                    overlayText.value = '';
                    overlayClass.value = 'overlay hidden';
                } else if (status === 'sending') {
                    overlayText.value = '';
                    overlayClass.value = 'overlay hidden';
                } else if (status === 'resultReady') {
                    overlayText.value = data.prediction;
                    overlayClass.value = 'overlay bg-green-500/80';
                } else if (status === 'continuousReady') {
                    statusText.value = '🟢 连续识别中，等待手势...';
                } else if (status === 'tooShort' || status === 'recognitionFailed') {
                    statusText.value = '没看清，请重试 😊';
                    overlayClass.value = 'overlay hidden';
                } else if (status === 'wordConfirmed') {
                    statusText.value = `✓ 已记录「${data.word}」`;
                    overlayClass.value = 'overlay hidden';
                }
            },
            onToggleByGesture: (active) => {
                continuousActive.value = active;
                if (active) {
                    continuousStopNotice.value = false;
                    statusText.value = '🟢 识别中，请比划手语...';
                } else {
                    continuousStopNotice.value = true;
                    statusText.value = '⏸ 连续识别已停止';
                    setTimeout(() => {
                        continuousStopNotice.value = false;
                        if (!continuousActive.value) {
                            statusText.value = '比划"开始结束"可重新开始';
                        }
                    }, 3000);
                }
            },
            onHeadGesture: (gesture) => {
                if (!waitingForHeadGesture.value) return;
                if (gesture.type === 'nod') {
                    const c = currentCandidates.value[currentCandidateIndex.value];
                    if (c) { agent.confirmWord(c.label); waitingForHeadGesture.value = false; clearHeadGestureTimer(); }
                } else if (gesture.type === 'shake') {
                    if (currentCandidates.value.length > 1) {
                        let next = (currentCandidateIndex.value + 1) % currentCandidates.value.length;
                        currentCandidateIndex.value = next;
                        currentWord.value = currentCandidates.value[next].label;
                        statusText.value = `切换为「${currentCandidates.value[next].label}」，请点头确认`;
                    }
                }
            }
        });

        const startHeadGestureWaiting = () => {
            waitingForHeadGesture.value = true;
            agent.enableHeadGesture(true);
            headGestureTimeout = setTimeout(() => {
                if (waitingForHeadGesture.value) {
                    waitingForHeadGesture.value = false;
                    agent.enableHeadGesture(false);
                    statusText.value = '超时未确认，请重新比划';
                    currentCandidates.value = [];
                }
            }, 8000);
        };

        const clearHeadGestureTimer = () => {
            if (headGestureTimeout) clearTimeout(headGestureTimeout);
            waitingForHeadGesture.value = false;
            agent.enableHeadGesture(false);
        };

        const startCamera = async () => {
            try {
                await agent.startCamera('webcam', 'canvas');
                cameraStarted.value = true;
                statusText.value = '摄像头已就绪，比划"开始结束"手势可自动开始识别';
            } catch (error) {
                statusText.value = '摄像头启动失败';
            }
        };

        const toggleRecording = () => {
            if (agent.isRecording) agent.stopRecording();
            else agent.startRecording();
        };

        // 连续识别控制
        const startContinuousRecognition = () => {
            if (!cameraStarted.value) return;
            continuousActive.value = true;
            // 不清空已有结果，保留之前识别的内容
            agent.startContinuous();
        };

        const stopContinuousRecognition = () => {
            continuousActive.value = false;
            continuousStopNotice.value = true;
            statusText.value = '⏸ 连续识别已停止';
            agent.stopContinuous();
            setTimeout(() => {
                continuousStopNotice.value = false;
                if (!continuousActive.value) {
                    statusText.value = '比划"开始结束"可重新开始';
                }
            }, 3000);
        };

        const clearContinuousWords = () => {
            continuousWords.value = [];
            agent.clearSentenceBuffer();
        };

        const removeWord = (index) => {
            continuousWords.value.splice(index, 1);
        };

        const clearSequence = () => {
            agent.clearSequence();
        };

        // ==================== 🌟 Agent 核心逻辑 ====================

        const callAgentPolish = async (wordsArray) => {
            isPolishing.value = true;
            let finalSentence = wordsArray.join('，');
            doctorContext.value = '';
            riskNote.value = '';
            patientReminder.value = '';
            memoryHits.value = [];

            try {
                const recentContext = chatMessages.value.slice(-4).map(m =>
                    `${m.type === 'doc' ? '医生' : '我'}：${m.content}`
                ).join('\n');

                // 🌟 修正：补上 /register 路径前缀
                const response = await fetch('http://localhost:8000/api/agent/register/consultation/polish', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        patient_id: Number(patientId),
                        words: wordsArray,
                        context: recentContext
                    })
                });

                if (response.ok) {
                    const data = await response.json();
                    if (data) {
                        finalSentence = data.patient_expression || data.polished_text || finalSentence;
                        aiSummary.value = data.objective_summary || '';
                        doctorContext.value = data.doctor_context || '';
                        riskNote.value = data.risk_note || '';
                        patientReminder.value = data.patient_gentle_reminder || '';
                        memoryHits.value = Array.isArray(data.memory_hits) ? data.memory_hits : [];

                        if (aiSummary.value.trim() !== "") {
                            const p = getPatientById(patientId);
                            if (p) {
                                p.triage_struct = {
                                    ...p.triage_struct,
                                    primary_symptom: aiSummary.value,
                                    original_text: finalSentence
                                };
                                p.ai_assist = {
                                    objective_summary: aiSummary.value,
                                    doctor_context: doctorContext.value,
                                    risk_note: riskNote.value,
                                    patient_gentle_reminder: patientReminder.value,
                                    memory_hits: memoryHits.value,
                                    patient_expression: finalSentence
                                };
                                upsertPatient(p);
                            }
                        }
                        const p = getPatientById(patientId);
                        if (p) {
                            p.ai_assist = {
                                objective_summary: aiSummary.value,
                                doctor_context: doctorContext.value,
                                risk_note: riskNote.value,
                                patient_gentle_reminder: patientReminder.value,
                                memory_hits: memoryHits.value,
                                patient_expression: finalSentence
                            };
                            upsertPatient(p);
                        }
                    }
                }
            } catch (e) {
                console.warn("Agent 提炼失败，使用基础降级拼接");
            }

            addMessage(patientId, { type: 'pat', content: finalSentence });
            try {
                await saveChatMessage(patientId, { type: 'pat', content: finalSentence }, registrationId || null);
                await loadPatientData(true);
            } catch (e) {
                console.warn('患者消息落库失败', e);
                await loadPatientData(false);
            }
            isPolishing.value = false;
        };

        const sendTextInput = async () => {
            if (!textInput.value.trim() || isPolishing.value) return;
            const text = textInput.value.trim();
            textInput.value = '';
            await callAgentPolish([text]);
        };

        const polishAndSend = async () => {
            if (continuousWords.value.length === 0) return;
            await callAgentPolish(continuousWords.value);
            clearContinuousWords();
        };

        const formatTimeDisplay = (timestamp) => {
            if (!timestamp) return '';
            const date = new Date(timestamp);
            return `${date.getHours().toString().padStart(2, '0')}:${date.getMinutes().toString().padStart(2, '0')}`;
        };

        return {
            patientId, doctorId, patientInfo, chatMessages, chatContainer,
            vueWordSequence, currentWord, statusText, overlayText, overlayClass, cameraStarted,
            textInput, patientReminder, isPolishing,
            continuousActive, continuousWords, continuousStopNotice,
            // 新增返回的状态和方法
            selectedText, showExplainBtn, explainBtnStyle, showExplainModal, explanationContent, isExplaining,
            handleMouseUp, explainSelected, closeExplainModal,
            startCamera, toggleRecording, clearSequence, polishAndSend, sendTextInput,
            startContinuousRecognition, stopContinuousRecognition, clearContinuousWords, removeWord,
            formatTimeDisplay
        };
    },
    template: `
        <div class="min-h-screen bg-[#f8fafc] flex flex-col p-4 md:p-6 font-sans relative">
            
            <!-- 🌟 划词翻译：悬浮【解释】按钮 -->
            <div id="explain-float-btn" 
                 v-if="showExplainBtn" 
                 :style="explainBtnStyle"
                 @mousedown.prevent="explainSelected"
                 class="fixed z-50 bg-[#4f46e5] text-white text-xs px-3 py-2 rounded-lg shadow-[0_4px_12px_rgba(79,70,229,0.4)] cursor-pointer hover:bg-[#4338ca] transition-all flex items-center gap-1.5 font-medium whitespace-nowrap animate-fade-in-up">
                <span class="text-sm">💡</span> 解释术语
                <div class="absolute -bottom-1.5 left-1/2 -translate-x-1/2 w-0 h-0 border-l-[6px] border-l-transparent border-r-[6px] border-r-transparent border-t-[6px] border-t-[#4f46e5]"></div>
            </div>

            <!-- 🌟 划词翻译：解释弹窗 Modal -->
            <div v-if="showExplainModal" class="fixed inset-0 bg-[#0f172a]/40 backdrop-blur-sm flex items-center justify-center z-[100]" @click.self="closeExplainModal">
                <div class="bg-white rounded-2xl p-6 w-[90%] max-w-sm shadow-2xl transform transition-all" style="animation: modalPop 0.3s cubic-bezier(0.16, 1, 0.3, 1);">
                    <div class="flex justify-between items-center mb-4">
                        <h3 class="text-lg font-bold text-[#3730a3] flex items-center gap-2">
                            <div class="bg-[#e0e7ff] p-1.5 rounded-lg leading-none">💡</div>
                            Agent 术语科普
                        </h3>
                        <button @click="closeExplainModal" class="text-gray-400 hover:text-red-500 text-2xl leading-none transition-colors">&times;</button>
                    </div>
                    
                    <div class="mb-4 text-center">
                        <span class="inline-block bg-[#eef2ff] border border-[#c7d2fe] text-[#4338ca] px-4 py-2 rounded-xl font-bold text-[17px] shadow-sm">
                            {{ selectedText }}
                        </span>
                    </div>
                    
                    <div class="bg-[#f8fafc] rounded-xl p-5 min-h-[100px] flex items-center justify-center text-[15px] text-gray-700 leading-relaxed border border-[#e2e8f0] shadow-inner">
                        <!-- Agent 检索中动画 -->
                        <div v-if="isExplaining" class="flex flex-col items-center gap-3 text-[#6366f1]">
                            <span class="animate-spin text-3xl leading-none">⚙</span>
                            <span class="text-sm font-medium">正在检索医学知识库...</span>
                        </div>
                        <!-- 解释内容 -->
                        <div v-else class="text-left w-full whitespace-pre-line">
                            {{ explanationContent }}
                        </div>
                    </div>
                    
                    <button @click="closeExplainModal" class="mt-5 w-full bg-[#f1f5f9] hover:bg-[#e2e8f0] text-gray-700 py-3 rounded-xl font-bold transition-colors">
                        我知道了
                    </button>
                </div>
            </div>

            <!-- 顶栏 -->
            <div class="bg-[#3b82f6] rounded-t-2xl p-4 flex justify-between items-center text-white shadow-sm shrink-0">
                <div class="flex items-center gap-3">
                    <span class="text-2xl leading-none">🤟</span>
                    <h1 class="font-bold text-lg tracking-wide">无障碍医患交流辅屏</h1>
                </div>
                <div class="flex items-center gap-3 text-sm">
                    <span class="bg-white/20 px-4 py-1.5 rounded-full border border-white/10 shadow-inner">主治医生: {{ doctorId }}</span>
                    <span class="bg-white/20 px-4 py-1.5 rounded-full border border-white/10 shadow-inner">患者: {{ patientInfo?.name || patientId }}</span>
                </div>
            </div>

            <!-- 主内容区 -->
            <div class="flex-1 bg-white rounded-b-2xl shadow-xl flex flex-col lg:flex-row overflow-hidden border border-[#e2e8f0] border-t-0">
                
                <!-- 左侧：手语捕捉区 -->
                <div class="w-full lg:w-[45%] p-6 flex flex-col bg-white border-r border-[#e2e8f0]">
                    <h2 class="text-[1.1rem] font-bold mb-4 flex items-center gap-2 text-gray-800">
                        <span class="w-1.5 h-5 bg-[#3b82f6] rounded-full"></span>
                        请面对屏幕，用手语回答医生
                    </h2>

                    <div :class="['video-container', cameraStarted ? (continuousActive ? 'camera-detecting' : 'camera-active') : '']">
                        <div class="video-inner" style="min-height:260px;">
                            <video id="webcam" autoplay playsinline class="w-full"></video>
                            <canvas id="canvas" class="absolute top-0 left-0 w-full h-full"></canvas>
                            <div :class="overlayClass" id="overlay">{{ overlayText }}</div>
                            <div class="detection-status absolute top-2 right-2 bg-white/90 p-2 rounded-lg text-xs">
                                <div class="detection-item"><span class="detection-dot" id="poseDot"></span>身体</div>
                                <div class="detection-item"><span class="detection-dot" id="leftHandDot"></span>左手</div>
                                <div class="detection-item"><span class="detection-dot" id="rightHandDot"></span>右手</div>
                            </div>
                        </div>
                    </div>

                    <div class="controls flex gap-2 mt-4">
                        <button v-if="!cameraStarted" class="flex-1 bg-blue-600 text-white py-2 rounded-lg hover:bg-blue-700" @click="startCamera">📷 开启摄像头</button>
                        <button v-else-if="!continuousActive" class="flex-1 bg-green-600 text-white py-2 rounded-lg hover:bg-green-700" @click="startContinuousRecognition">🤟 开始识别</button>
                        <button v-else class="flex-1 bg-orange-500 text-white py-2 rounded-lg hover:bg-orange-600" @click="stopContinuousRecognition">⏸ 暂停</button>
                    </div>

                    <div class="status-bar flex items-center gap-2 mt-3 p-2 rounded-lg text-sm" :class="continuousActive ? 'bg-green-50 border border-green-200' : 'bg-gray-100'">
                        <span class="status-dot" :class="{'active': cameraStarted && continuousActive}"></span>
                        <span>{{ statusText }}</span>
                    </div>

                    <div class="mt-3 p-3 bg-blue-50 rounded-lg text-xs text-gray-600">
                        <strong>💡 使用方式：</strong> “在身体左侧拍手”即可自动开始/停止识别，识别到的词汇会出现在右侧。
                    </div>
                </div>

                <!-- 🌟 右侧：医患双向沟通与输入区 (添加 @mouseup 监听划词) -->
                <div class="w-full lg:w-[55%] flex flex-col bg-white" @mouseup="handleMouseUp">
                    
                    <!-- 聊天室 -->
                    <div class="flex-1 flex flex-col overflow-hidden bg-white">
                        <div class="px-6 py-4 border-b border-[#e2e8f0] shrink-0 flex justify-between items-center">
                            <h2 class="text-[1.1rem] font-bold flex items-center gap-2 text-gray-800">
                                <span class="w-1.5 h-5 bg-[#6366f1] rounded-full"></span>
                                医患交流板
                            </h2>
                            <span class="text-xs text-gray-400">实时同步中...</span>
                        </div>

                        <div v-if="patientReminder" class="border-b border-[#bbf7d0] shrink-0 shadow-inner bg-[#f0fdf4]">
                            <div class="px-6 py-3 text-sm flex items-start gap-3">
                                <div class="text-xl leading-none mt-0.5">🌿</div>
                                <div class="flex-1">
                                    <div class="font-bold text-[#15803d] text-xs mb-1">给患者的温和提醒</div>
                                    <div class="text-[#166534] leading-relaxed">{{ patientReminder }}</div>
                                </div>
                            </div>
                        </div>
                        
                        <!-- 聊天滚动区，划词翻译主要在这个区域生效 -->
                        <div ref="chatContainer" class="flex-1 overflow-y-auto p-6 space-y-6 scrollbar-thin selection:bg-indigo-200 selection:text-indigo-900">
                            <div v-if="chatMessages.length === 0" class="text-center text-gray-400 text-sm mt-10">
                                医生已就绪，等待沟通...
                            </div>
                            
                            <div v-for="msg in chatMessages" :key="msg.id" class="flex w-full" :class="msg.type === 'pat' ? 'justify-end' : 'justify-start'">
                                <!-- 医生消息 -->
                                <div v-if="msg.type === 'doc'" class="flex items-start gap-3 max-w-[85%]">
                                    <div class="w-9 h-9 rounded-full bg-[#f3e8ff] flex items-center justify-center text-[#6366f1] font-bold shrink-0 text-sm">医</div>
                                    <div>
                                        <div class="bg-white border border-[#e2e8f0] rounded-2xl rounded-tl-none px-4 py-3 shadow-sm text-gray-800 text-[15px] leading-relaxed">
                                            {{ msg.content }}
                                        </div>
                                        <div class="text-xs text-gray-400 mt-1 ml-1">{{ formatTimeDisplay(msg.created_at) }}</div>
                                    </div>
                                </div>
                                
                                <!-- 患者消息 -->
                                <div v-else-if="msg.type === 'pat'" class="flex items-start gap-3 flex-row-reverse max-w-[85%]">
                                    <div class="w-9 h-9 rounded-full bg-[#eff6ff] flex items-center justify-center text-[#3b82f6] font-bold shrink-0 text-sm">我</div>
                                    <div class="text-right">
                                        <div class="bg-[#3b82f6] text-white rounded-2xl rounded-tr-none px-4 py-3 shadow-md text-[15px] leading-relaxed text-left inline-block">
                                            {{ msg.content }}
                                        </div>
                                        <div class="text-xs text-gray-400 mt-1 mr-1">{{ formatTimeDisplay(msg.created_at) }}</div>
                                    </div>
                                </div>
                            </div>
                        </div>
                    </div>

                    <!-- 底部：复合输入区 -->
                    <div class="shrink-0 border-t border-[#e2e8f0] bg-white p-6 shadow-[0_-4px_15px_-3px_rgba(0,0,0,0.03)] z-20">
                        
                        <!-- 手动文字输入区 -->
                        <div class="mb-5">
                            <span class="text-sm font-bold text-gray-800 mb-2.5 block">文字输入（手写识别接入前可直接使用）：</span>
                            <div class="flex gap-3">
                                <input v-model="textInput" @keyup.enter="sendTextInput" type="text" placeholder="请直接输入想表达的内容..." 
                                       class="flex-1 border border-[#e2e8f0] rounded-xl px-4 py-2.5 text-[15px] focus:ring-2 focus:ring-[#93c5fd] outline-none shadow-sm transition-all"
                                       :disabled="isPolishing">
                                <button @click="sendTextInput" :disabled="isPolishing"
                                        class="bg-[#818cf8] hover:bg-[#6366f1] text-white px-7 py-2.5 rounded-xl text-[15px] font-bold transition-colors shadow-sm disabled:opacity-50">
                                    发送
                                </button>
                            </div>
                        </div>

                        <!-- 手语组合板区 -->
                        <div>
                            <div class="flex justify-between items-end mb-2.5">
                                <span class="text-sm font-bold text-gray-800">📝 手语识别结果</span>
                                <button v-if="continuousWords.length > 0" @click="clearContinuousWords" class="text-xs text-gray-400 hover:text-red-500 transition-colors">清空</button>
                            </div>

                            <!-- 统一显示识别结果 - 与挂号界面一致，支持删除 -->
                            <div class="min-h-[60px] rounded-xl p-3 flex flex-wrap gap-2 items-center mb-4 shadow-inner transition-all"
                                 :class="continuousWords.length > 0 ? 'bg-green-50 border-2 border-green-300' : 'bg-gray-50 border border-gray-100'">
                                <span v-if="continuousWords.length === 0" class="text-sm text-gray-400 px-2 italic">
                                    {{ continuousActive ? '连续识别中，识别到的词汇将实时显示...' : '点击"开启连续识别"开始比划手语' }}
                                </span>
                                <span v-for="(word, idx) in continuousWords" :key="idx"
                                      class="bg-green-500 text-white px-3 py-1.5 rounded-full text-sm font-medium shadow-sm animate-fade-in relative group cursor-pointer"
                                      @click="removeWord(idx)">
                                    {{ word }}
                                    <span class="absolute -top-1 -right-1 w-4 h-4 bg-red-500 rounded-full flex items-center justify-center text-xs opacity-0 group-hover:opacity-100 transition-opacity">×</span>
                                </span>
                            </div>

                            <button @click="polishAndSend" :disabled="continuousWords.length === 0 || isPolishing"
                                    class="w-full py-3.5 rounded-xl font-bold text-white text-[17px] transition-all shadow-md flex justify-center items-center gap-2"
                                    :class="continuousWords.length === 0 ? 'bg-[#cbd5e1] cursor-not-allowed' : 'bg-gradient-to-r from-[#fb923c] to-[#f97316] hover:from-[#f97316] hover:to-[#ea580c] hover:-translate-y-0.5'">
                                <span v-if="isPolishing" class="animate-spin text-xl leading-none">⚙</span>
                                <span v-else class="text-xl leading-none">✨</span>
                                {{ isPolishing ? 'Agent 正在进行翻译与提炼...' : 'AI 润色成句并发送给医生' }}
                            </button>
                        </div>
                    </div>
                </div>

            </div>
        </div>
        <!-- 用于弹窗的动画 -->
        <style>
            @keyframes modalPop {
                0% { opacity: 0; transform: scale(0.95) translateY(10px); }
                100% { opacity: 1; transform: scale(1) translateY(0); }
            }
            .animate-fade-in-up {
                animation: fadeInUp 0.2s ease-out forwards;
            }
            @keyframes fadeInUp {
                from { opacity: 0; transform: translate(-50%, 5px); }
                to { opacity: 1; transform: translate(-50%, 0); }
            }
            .animate-fade-in {
                animation: fadeIn 0.3s ease-out forwards;
            }
            @keyframes fadeIn {
                from { opacity: 0; transform: scale(0.9); }
                to { opacity: 1; transform: scale(1); }
            }
        </style>
    `
});

app.mount('#app');
