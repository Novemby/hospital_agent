import { createApp, ref, computed, onMounted, onUnmounted, nextTick } from 'vue';
import { getPatientById, addMessage } from './services/patientService.js';
import { SignLanguageAgent } from './services/signLanguageAgent.js';

const app = createApp({
    setup() {
        // 解析 URL 参数获取关联信息
        const urlParams = new URLSearchParams(window.location.search);
        const patientId = urlParams.get('patientId') || '';
        const doctorId = urlParams.get('doctorId') || '';

        const patientInfo = ref(null);
        const chatMessages = ref([]);
        const chatContainer = ref(null);

        // 初始化加载患者数据
        const loadPatientData = () => {
            if (patientId) {
                patientInfo.value = getPatientById(patientId);
                if (patientInfo.value && patientInfo.value.messages) {
                    chatMessages.value = patientInfo.value.messages.sort((a, b) => a.id - b.id);
                    scrollToBottom();
                }
            }
        };

        // 监听来自医生端（Scene2）的 localStorage 变化，实现双屏实时通讯
        const handleStorageChange = (e) => {
            if (e.key === 'smart_hospital_patients' || e.key === null) {
                loadPatientData();
            }
        };

        onMounted(() => {
            loadPatientData();
            window.addEventListener('storage', handleStorageChange);
            // 延迟一点启动摄像头，防止页面渲染卡顿
            setTimeout(() => {
                startCamera();
            }, 500);
        });

        onUnmounted(() => {
            window.removeEventListener('storage', handleStorageChange);
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
        const overlayText = ref('等待开始...');
        const overlayClass = ref('overlay');
        const cameraStarted = ref(false);
        const isPolishing = ref(false);
        const typedMessage = ref('');

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
            }, 8000);
        };

        const clearHeadGestureTimer = () => {
            if (headGestureTimeout) clearTimeout(headGestureTimeout);
            waitingForHeadGesture.value = false;
            agent.enableHeadGesture(false);
        };

        // ========== 初始化核心 Agent ==========
        const agent = new SignLanguageAgent({
            apiUrl: 'http://localhost:5000',
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
                    overlayClass.value = 'overlay recording bg-blue-500/20';
                } else if (status === 'sending') {
                    statusText.value = `正在理解... (${data.frameCount}帧)`;
                    overlayText.value = `处理中...`;
                    overlayClass.value = 'overlay bg-yellow-500/20';
                } else if (status === 'resultReady') {
                    statusText.value = '👉 请点头确认添加到输入框，或摇头切换';
                    overlayText.value = data.prediction;
                    overlayClass.value = 'overlay bg-green-500/20 text-green-100';
                    startHeadGestureWaiting();
                } else if (status === 'tooShort') {
                    statusText.value = '时间有点短，请再比划一次 😊';
                    overlayClass.value = 'overlay bg-red-500/20';
                } else if (status === 'recognitionFailed') {
                    statusText.value = '抱歉没看清，请重试 😊';
                    overlayClass.value = 'overlay bg-red-500/20';
                } else if (status === 'wordConfirmed') {
                    statusText.value = `✓ 已记录「${data.word}」，可继续比划下一个词`;
                    overlayClass.value = 'overlay hidden';
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
                    }
                } else if (gesture.type === 'shake') {
                    if (currentCandidates.value.length > 1) {
                        let nextIdx = currentCandidateIndex.value + 1;
                        if (nextIdx >= currentCandidates.value.length) nextIdx = 0;
                        currentCandidateIndex.value = nextIdx;
                        const newCandidate = currentCandidates.value[nextIdx];
                        currentWord.value = newCandidate.label;
                        currentConfidence.value = `${(newCandidate.confidence * 100).toFixed(1)}%`;
                        statusText.value = `切换为「${newCandidate.label}」，请点头确认`;
                    }
                }
            }
        });

        const startCamera = async () => {
            try {
                await agent.startCamera('webcam', 'canvas');
                cameraStarted.value = true;
                statusText.value = '就绪，可以开始比划了';
                overlayText.value = '准备就绪';
                overlayClass.value = 'overlay hidden';
            } catch (error) {
                statusText.value = '摄像头启动失败: ' + error.message;
            }
        };

        const toggleRecording = () => {
            if (agent.isRecording) {
                agent.stopRecording();
            } else {
                agent.startRecording();
            }
        };

        const clearSequence = () => {
            agent.clearSequence();
        };

        const sendTypedMessage = () => {
            const content = typedMessage.value.trim();
            if (!content) return;
            addMessage(patientId, { type: 'pat', content });
            typedMessage.value = '';
            loadPatientData();
        };

        // 🌟 核心逻辑：润色患者的零碎词汇并发送至聊天室
        const polishAndSend = async () => {
            if (vueWordSequence.value.length === 0) {
                alert("请先比划输入一些词汇！");
                return;
            }

            isPolishing.value = true;
            try {
                // 如果后端有 polishSequence 接口最好，如果没有，这里降级为直接拼接发送
                let finalSentence = vueWordSequence.value.join('，');

                // 尝试调用 AI 润色 (依赖后端的实现，如无则直接 fallback 为拼接字符串)
                const result = await agent.polishSequence();
                if (result && result.polished) {
                    finalSentence = result.polished;
                }

                // 1. 落库：将患者的消息存入该患者的对话记录中
                addMessage(patientId, { type: 'pat', content: finalSentence });

                // 2. 本地刷新：触发页面更新
                loadPatientData();

                // 3. 清空工作台
                clearSequence();
            } catch (err) {
                alert("润色发送失败，请重试！");
            } finally {
                isPolishing.value = false;
            }
        };

        const formatTimeDisplay = (timestamp) => {
            if (!timestamp) return '';
            const date = new Date(timestamp);
            return `${date.getHours().toString().padStart(2, '0')}:${date.getMinutes().toString().padStart(2, '0')}`;
        };

        return {
            patientId, doctorId, patientInfo, chatMessages, chatContainer,
            vueWordSequence, currentWord, currentConfidence,
            statusText, overlayText, overlayClass, cameraStarted, isPolishing, typedMessage,
            startCamera, toggleRecording, clearSequence, polishAndSend, sendTypedMessage,
            agent, formatTimeDisplay
        };
    },
    template: `
        <div class="min-h-screen bg-slate-100 flex flex-col p-4 md:p-6 font-sans">
            
            <!-- 顶栏 -->
            <div class="bg-blue-600 rounded-t-2xl p-4 flex justify-between items-center text-white shadow-sm shrink-0">
                <div class="flex items-center gap-3">
                    <span class="text-2xl">🤟</span>
                    <h1 class="font-bold text-xl tracking-wide">无障碍医患交流辅屏</h1>
                </div>
                <div class="flex items-center gap-4 text-sm font-medium">
                    <span class="bg-white/20 px-3 py-1 rounded-full">主治医生: {{ doctorId }}</span>
                    <span class="bg-white/20 px-3 py-1 rounded-full">患者: {{ patientInfo?.name || patientId }}</span>
                </div>
            </div>

            <!-- 主内容区 -->
            <div class="flex-1 bg-white rounded-b-2xl shadow-xl border border-t-0 flex flex-col lg:flex-row overflow-hidden">
                
                <!-- 左侧：摄像头与手语捕捉区 -->
                <div class="w-full lg:w-1/2 p-6 flex flex-col bg-slate-50/50 border-r border-gray-100">
                    <h2 class="text-lg font-bold mb-4 flex items-center gap-2 text-gray-800">
                        <span class="w-1.5 h-5 bg-blue-500 rounded-full"></span>
                        请面对屏幕，用手语回答医生
                    </h2>
                    
                    <div class="video-container relative flex-1 rounded-2xl overflow-hidden bg-black shadow-inner min-h-[300px]">
                        <video id="webcam" autoplay playsinline class="w-full h-full object-cover"></video>
                        <canvas id="canvas" class="absolute top-0 left-0 w-full h-full"></canvas>
                        
                        <!-- 状态悬浮层 -->
                        <div :class="overlayClass" id="overlay" class="absolute inset-0 flex items-center justify-center text-3xl font-bold tracking-widest transition-all duration-300 backdrop-blur-sm">
                            {{ overlayText }}
                        </div>
                        
                        <div class="absolute top-4 right-4 bg-black/50 backdrop-blur-md p-3 rounded-xl text-xs text-white/90 border border-white/10 hidden md:block space-y-2">
                            <div class="flex items-center gap-2"><span class="w-2 h-2 rounded-full bg-green-400" id="poseDot"></span>身体追踪</div>
                            <div class="flex items-center gap-2"><span class="w-2 h-2 rounded-full bg-blue-400" id="leftHandDot"></span>左手捕捉</div>
                            <div class="flex items-center gap-2"><span class="w-2 h-2 rounded-full bg-purple-400" id="rightHandDot"></span>右手捕捉</div>
                        </div>
                    </div>

                    <!-- 操作控制区 -->
                    <div class="mt-4 flex gap-3 shrink-0">
                        <button id="recordBtn" @click="toggleRecording" :disabled="!cameraStarted" 
                                class="flex-1 py-4 rounded-xl font-bold text-lg text-white shadow-lg transition-all duration-300 disabled:opacity-50 disabled:cursor-not-allowed"
                                :class="agent.isRecording ? 'bg-red-500 hover:bg-red-600 animate-pulse' : 'bg-green-500 hover:bg-green-600'">
                            {{ agent.isRecording ? '⏹ 结束本段动作' : '⏺ 按下开始比划' }}
                        </button>
                    </div>

                    <!-- 状态提示栏 -->
                    <div class="mt-4 bg-white border border-gray-200 rounded-xl p-4 flex items-center gap-3 shadow-sm shrink-0">
                        <div class="relative flex h-3 w-3">
                          <span v-if="cameraStarted && !agent.isRecording" class="animate-ping absolute inline-flex h-full w-full rounded-full bg-blue-400 opacity-75"></span>
                          <span class="relative inline-flex rounded-full h-3 w-3" :class="cameraStarted ? (agent.isRecording ? 'bg-red-500' : 'bg-blue-500') : 'bg-gray-300'"></span>
                        </div>
                        <span class="text-sm font-medium text-gray-700">{{ statusText }}</span>
                    </div>
                </div>

                <!-- 右侧：医患双向沟通区 -->
                <div class="w-full lg:w-1/2 flex flex-col bg-white">
                    
                    <!-- 1. 聊天室 (顶部区域，占满高度) -->
                    <div class="flex-1 flex flex-col overflow-hidden bg-slate-50/30">
                        <div class="px-6 py-4 border-b border-gray-100 bg-white shrink-0 shadow-sm z-10 flex justify-between items-center">
                            <h2 class="text-lg font-bold flex items-center gap-2 text-gray-800">
                                <span class="w-1.5 h-5 bg-indigo-500 rounded-full"></span>
                                医患交流板
                            </h2>
                            <span class="text-xs text-gray-400">实时同步中...</span>
                        </div>
                        
                        <!-- 消息滚动区 -->
                        <div ref="chatContainer" class="flex-1 overflow-y-auto p-6 space-y-6 scrollbar-thin">
                            <div v-if="chatMessages.length === 0" class="text-center text-gray-400 text-sm mt-10">
                                医生已就绪，等待沟通...
                            </div>
                            
                            <div v-for="msg in chatMessages" :key="msg.id" class="flex w-full" :class="msg.type === 'pat' ? 'justify-end' : 'justify-start'">
                                <!-- 医生消息 (左侧白色) -->
                                <div v-if="msg.type === 'doc'" class="flex items-start gap-3 max-w-[85%]">
                                    <div class="w-8 h-8 rounded-full bg-purple-100 flex items-center justify-center text-purple-600 font-bold shrink-0">医</div>
                                    <div>
                                        <div class="bg-white border border-gray-200 rounded-2xl rounded-tl-none px-4 py-3 shadow-sm text-gray-800 leading-relaxed">
                                            {{ msg.content }}
                                        </div>
                                        <div class="text-xs text-gray-400 mt-1 ml-1">{{ formatTimeDisplay(msg.created_at) }}</div>
                                    </div>
                                </div>
                                
                                <!-- 患者消息 (右侧蓝色) -->
                                <div v-else class="flex items-start gap-3 flex-row-reverse max-w-[85%]">
                                    <div class="w-8 h-8 rounded-full bg-blue-100 flex items-center justify-center text-blue-600 font-bold shrink-0">我</div>
                                    <div class="text-right">
                                        <div class="bg-blue-600 text-white rounded-2xl rounded-tr-none px-4 py-3 shadow-md leading-relaxed text-left inline-block">
                                            {{ msg.content }}
                                        </div>
                                        <div class="text-xs text-gray-400 mt-1 mr-1">{{ formatTimeDisplay(msg.created_at) }}</div>
                                    </div>
                                </div>
                            </div>
                        </div>
                    </div>

                    <!-- 2. 打字输入（先行方案） -->
                    <div class="shrink-0 border-t border-gray-100 bg-white px-6 pt-4 pb-3 z-20">
                        <div class="text-sm font-bold text-gray-700 mb-2">文字输入（手写识别接入前可直接使用）：</div>
                        <div class="flex gap-2">
                            <input
                                v-model="typedMessage"
                                @keyup.enter="sendTypedMessage"
                                type="text"
                                placeholder="请直接输入患者想表达的内容..."
                                class="flex-1 border border-gray-300 rounded-lg px-3 py-2 text-sm focus:outline-none focus:ring-2 focus:ring-blue-500"
                            />
                            <button
                                @click="sendTypedMessage"
                                :disabled="!typedMessage.trim()"
                                class="px-4 py-2 rounded-lg text-sm font-bold text-white transition disabled:opacity-50 disabled:cursor-not-allowed bg-blue-600 hover:bg-blue-700"
                            >发送</button>
                        </div>
                    </div>

                    <!-- 3. 手语工作台 Sandbox (底部固定) -->
                    <div class="shrink-0 border-t border-gray-100 bg-white p-6 shadow-[0_-4px_6px_-1px_rgba(0,0,0,0.05)] z-20">
                        <div class="flex justify-between items-end mb-3">
                            <span class="text-sm font-bold text-gray-700">我的手语组合板：</span>
                            <button v-if="vueWordSequence.length > 0" @click="clearSequence" class="text-xs text-gray-400 hover:text-red-500">清空重来</button>
                        </div>
                        
                        <!-- 词汇排列区 -->
                        <div class="min-h-[64px] bg-slate-50 border border-gray-200 rounded-xl p-3 flex flex-wrap gap-2 items-center mb-4">
                            <span v-if="vueWordSequence.length === 0" class="text-sm text-gray-400 px-2 italic">点头确认后的词语会在这里排队...</span>
                            <span v-for="(word, idx) in vueWordSequence" :key="idx" 
                                  class="bg-indigo-100 text-indigo-800 border border-indigo-200 px-3 py-1.5 rounded-lg text-sm font-medium flex items-center gap-1 shadow-sm animate-fade-in-up">
                                {{ word }}
                                <button @click="agent.deleteWord(idx)" class="ml-1 hover:text-red-500 font-bold leading-none">&times;</button>
                            </span>
                        </div>

                        <!-- 发送操作区 -->
                        <button @click="polishAndSend" :disabled="vueWordSequence.length === 0 || isPolishing"
                                class="w-full py-4 rounded-xl font-bold text-white text-lg transition-all duration-300 shadow-md flex justify-center items-center gap-2"
                                :class="vueWordSequence.length === 0 ? 'bg-gray-300 cursor-not-allowed' : 'bg-gradient-to-r from-blue-600 to-indigo-600 hover:from-blue-700 hover:to-indigo-700 hover:shadow-lg hover:-translate-y-0.5'">
                            <span v-if="isPolishing" class="animate-spin text-2xl leading-none">⚙</span>
                            <span v-else class="text-xl leading-none">✨</span>
                            {{ isPolishing ? 'AI 正在润色并发送...' : 'AI 润色成句并发送给医生' }}
                        </button>
                    </div>
                </div>

            </div>
        </div>
    `
});

app.mount('#app');