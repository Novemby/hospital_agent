import { createApp, ref, computed, onMounted, onUnmounted, watch } from 'vue';
import { getPatients, getPatientById, upsertPatient, addMessage, syncPatientsFromBackend } from './services/patientService.js';
import { doctorLoginAPI, setCurrentDoctor, getCurrentDoctor, getCurrentDoctorDept, clearAuth } from './services/authService.js';
import { SignLanguageAgent } from './services/signLanguageAgent.js';

const app = createApp({
    setup() {
        // ========== 原有状态 ==========
        const storedDoctor = getCurrentDoctor();
        const doctorId = ref(storedDoctor ? storedDoctor.id : '');
        const doctorPwd = ref('');
        const view = ref(storedDoctor ? 'doctor-view' : 'login');
        const loginLoading = ref(false);
        const doctorDept = ref(storedDoctor ? (storedDoctor.dept || storedDoctor.department) : '');
        const patients = ref([]);
        const selectedPatientId = ref('');

        const showAddPrescription = ref(false);
        const newRx = ref({ drug: '', dosage: '', usage: '' });
        const selectedTerm = ref('');
        const termExplanation = ref('');
        const termDict = {
            '心肌缺血': '指心脏肌肉供血不足，可能引发胸痛。',
            '心律失常': '心跳节奏异常，可能过快、过慢或不规则。',
            '高血压': '动脉血压持续偏高。',
            '糖皮质激素': '一类抗炎药物，常用于过敏、哮喘等。',
            'MRI': '磁共振成像，无辐射检查。'
        };
        const voiceInput = ref('');  // 医生语音消息（原有，保留）

        // ========== 新增：手语辅助窗口状态 ==========
        const isHandsignOpen = ref(false);
        let handsignWindow = null;
        let windowCheckInterval = null;

        // ========== 新增：医生语音转文字功能 ==========
        const isRecordingVoiceForDoc = ref(false);
        const doctorVoiceText = ref('');
        const doctorRecognizedText = ref('');
        let mediaRecorderDoc = null;
        let audioChunksDoc = [];

        // ========== 手语 agent（用于医生端？实际上新窗口独立使用，但为了保持代码一致保留） ==========
        const agent = new SignLanguageAgent({
            apiUrl: 'http://localhost:5000',
            onResult: () => {},
            onSequenceUpdate: () => {},
            onStatusChange: () => {},
            onHeadGesture: () => {}
        });

        // 医生语音转文字方法
        const startDoctorVoiceRecording = async () => {
            if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
                alert('您的浏览器不支持语音输入');
                return;
            }
            try {
                const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
                mediaRecorderDoc = new MediaRecorder(stream);
                audioChunksDoc = [];
                mediaRecorderDoc.ondataavailable = event => {
                    audioChunksDoc.push(event.data);
                };
                mediaRecorderDoc.onstop = () => {
                    const audioBlob = new Blob(audioChunksDoc, { type: 'audio/webm' });
                    sendVoiceToBackendForDoc(audioBlob);
                    stream.getTracks().forEach(track => track.stop());
                };
                mediaRecorderDoc.start();
                isRecordingVoiceForDoc.value = true;
            } catch (error) {
                alert('无法访问麦克风: ' + error.message);
            }
        };

        const stopDoctorVoiceRecording = () => {
            if (mediaRecorderDoc && isRecordingVoiceForDoc.value) {
                mediaRecorderDoc.stop();
                isRecordingVoiceForDoc.value = false;
            }
        };

        const sendVoiceToBackendForDoc = async (audioBlob) => {
            const formData = new FormData();
            formData.append('audio', audioBlob);
            try {
                const response = await fetch('http://localhost:8000/speech-to-text', {
                    method: 'POST',
                    body: formData
                });
                const result = await response.json();
                if (result.success) {
                    doctorRecognizedText.value = result.text;
                } else {
                    alert('语音识别失败');
                }
            } catch (error) {
                alert('语音识别服务连接失败');
            }
        };

        const confirmDoctorVoiceText = () => {
            if (doctorRecognizedText.value.trim()) {
                doctorVoiceText.value = doctorRecognizedText.value;
                doctorRecognizedText.value = '';
            }
        };

        const cancelDoctorVoiceText = () => {
            doctorRecognizedText.value = '';
        };

        const sendDoctorVoiceMessage = () => {
            if (!doctorVoiceText.value.trim() || !selectedPatient.value) return;
            addMessage(selectedPatient.value.id, { type: 'doc', content: doctorVoiceText.value });
            doctorVoiceText.value = '';
        };

        // ========== 新增：患者手语消息显示 ==========
        const patientMessages = computed(() => {
            if (!selectedPatient.value) return [];
            return (selectedPatient.value.messages || [])
                .filter(m => m.type === 'pat')
                .sort((a, b) => a.id - b.id);
        });

        const formatTime = (timestamp) => {
            if (!timestamp) return '';
            const date = new Date(timestamp);
            return `${date.getHours().toString().padStart(2, '0')}:${date.getMinutes().toString().padStart(2, '0')}`;
        };

        // ========== 原有函数（医生登录、患者列表等）==========
        const goToRoleSelect = () => {
            window.location.href = 'index.html';
        };

        const doctorLogin = async () => {
            if (!doctorId.value || !doctorPwd.value) {
                alert('工号和密码不能为空');
                return;
            }
            loginLoading.value = true;
            try {
                const doctorData = await doctorLoginAPI(doctorId.value, doctorPwd.value);
                setCurrentDoctor(doctorData);
                doctorDept.value = getCurrentDoctorDept() || doctorData.dept || doctorData.department;
                await syncPatientsFromBackend(doctorDept.value);
                view.value = 'doctor-view';
                loadPatients();
                if (patients.value.length > 0) {
                    selectedPatientId.value = patients.value[0].id;
                } else {
                    selectedPatientId.value = '';
                }
            } catch (error) {
                alert(error.message || '登录失败，请检查工号或密码');
            } finally {
                loginLoading.value = false;
            }
        };

        const logout = () => {
            clearAuth();
            doctorId.value = '';
            doctorPwd.value = '';
            doctorDept.value = '';
            view.value = 'login';
            // 关闭手语辅助窗口
            if (handsignWindow && !handsignWindow.closed) handsignWindow.close();
            if (windowCheckInterval) clearInterval(windowCheckInterval);
            isHandsignOpen.value = false;
        };

        const loadPatients = () => {
            const allPatients = getPatients();
            if (doctorDept.value) {
                patients.value = allPatients.filter(p => p.dept === doctorDept.value);
            } else {
                patients.value = allPatients;
            }
        };

        const handleStorageChange = (e) => {
            if (e.key === 'smart_hospital_patients' || e.key === null) {
                loadPatients();
            }
        };

        onMounted(() => {
            window.addEventListener('storage', handleStorageChange);
        });

        onUnmounted(() => {
            window.removeEventListener('storage', handleStorageChange);
            if (windowCheckInterval) clearInterval(windowCheckInterval);
            if (handsignWindow && !handsignWindow.closed) handsignWindow.close();
        });

        const selectedPatient = computed(() => {
            return patients.value.find(p => p.id === selectedPatientId.value) || null;
        });

        const selectPatient = (id) => {
            selectedPatientId.value = id;
        };

        const saveMedicalHistory = () => {
            if (selectedPatient.value) {
                selectedPatient.value.lastUpdated = new Date().toLocaleTimeString();
                upsertPatient(selectedPatient.value);
                alert('病历已保存');
            }
        };

        const addPrescription = () => {
            if (!newRx.value.drug || !newRx.value.dosage || !newRx.value.usage) {
                alert('请完整填写药品信息');
                return;
            }
            if (!selectedPatient.value.prescriptions) selectedPatient.value.prescriptions = [];
            selectedPatient.value.prescriptions.push({ ...newRx.value });
            upsertPatient(selectedPatient.value);
            newRx.value = { drug: '', dosage: '', usage: '' };
            showAddPrescription.value = false;
        };

        const removePrescription = (index) => {
            selectedPatient.value.prescriptions.splice(index, 1);
            upsertPatient(selectedPatient.value);
        };

        const queryTerm = (term) => {
            selectedTerm.value = term;
            termExplanation.value = termDict[term] || '暂无解释';
        };

        const doctorMessagesForPatient = computed(() => {
            if (!selectedPatient.value) return [];
            return selectedPatient.value.messages?.filter(m => m.type === 'doc').slice(-5) || [];
        });

        const sendVoiceMessage = () => {
            if (!voiceInput.value.trim() || !selectedPatient.value) return;
            addMessage(selectedPatient.value.id, { type: 'doc', content: voiceInput.value });
            voiceInput.value = '';
        };

        // ========== 手语辅助窗口打开逻辑 ==========
        const openPatientAssist = () => {
            if (!selectedPatient.value) return alert('请先选择一位患者');
            if (handsignWindow && !handsignWindow.closed) {
                handsignWindow.focus();
                return;
            }
            const width = 1200;
            const height = 800;
            const left = (screen.width - width) / 2;
            const top = (screen.height - height) / 2;
            const features = `width=${width},height=${height},left=${left},top=${top},resizable=yes,scrollbars=yes`;
            handsignWindow = window.open(`handsign_assist.html?patientId=${selectedPatient.value.id}&doctorId=${doctorId.value}`, 'handsign_assist', features);
            if (handsignWindow) {
                isHandsignOpen.value = true;
                if (windowCheckInterval) clearInterval(windowCheckInterval);
                windowCheckInterval = setInterval(() => {
                    if (handsignWindow.closed) {
                        clearInterval(windowCheckInterval);
                        windowCheckInterval = null;
                        isHandsignOpen.value = false;
                        handsignWindow = null;
                    }
                }, 500);
            }
        };

        // 为了兼容，保留一个空方法（原页面切换已废弃）
        const backToDoctor = () => {};

        return {
            view, doctorId, doctorPwd, doctorDept, loginLoading,
            patients, selectedPatientId, selectedPatient,
            doctorMessagesForPatient, voiceInput,
            selectedTerm, termExplanation,
            showAddPrescription, newRx,
            goToRoleSelect, doctorLogin, logout, selectPatient,
            saveMedicalHistory, addPrescription, removePrescription,
            sendVoiceMessage, openPatientAssist, backToDoctor,
            queryTerm,
            // 新增变量
            isHandsignOpen,
            // 医生语音转文字
            doctorVoiceText, doctorRecognizedText, isRecordingVoiceForDoc,
            startDoctorVoiceRecording, stopDoctorVoiceRecording,
            confirmDoctorVoiceText, cancelDoctorVoiceText, sendDoctorVoiceMessage,
            // 患者手语消息
            patientMessages, formatTime
        };
    },
    template: `
        <!-- 医生登录界面 -->
        <div v-if="view === 'login'" class="flex items-center justify-center min-h-screen">
            <div class="bg-white p-8 rounded-2xl shadow-2xl w-96">
                <button @click="goToRoleSelect" class="text-sm text-blue-600 mb-4 flex items-center">← 返回场景选择</button>
                <div class="text-center mb-6">
                    <div class="inline-block p-3 bg-purple-100 rounded-full mb-3">
                        <svg class="w-8 h-8 text-purple-600" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M19 21V5a2 2 0 00-2-2H7a2 2 0 00-2 2v16l3.5-2 3.5 2 3.5-2 3.5 2zM9 7h6M9 11h6"></path></svg>
                    </div>
                    <h2 class="text-2xl font-bold">医生专用登录</h2>
                </div>
                <input v-model="doctorId" type="text" placeholder="工号" class="w-full border-2 border-gray-100 p-3 rounded-xl mb-3 focus:border-purple-500 outline-none">
                <input v-model="doctorPwd" type="password" placeholder="密码" class="w-full border-2 border-gray-100 p-3 rounded-xl mb-5 focus:border-purple-500 outline-none">
                <button @click="doctorLogin" :disabled="loginLoading" class="w-full bg-purple-600 text-white py-3 rounded-xl font-bold hover:bg-purple-700 disabled:opacity-50">
                    {{ loginLoading ? '登录中...' : '进入诊间' }}
                </button>
                <div class="text-xs text-gray-400 mt-4 text-center">演示账号: d001/123456 (骨科), d002/123456 (发热门诊)</div>
            </div>
        </div>

        <!-- 导航栏（登录后） -->
        <nav v-else class="bg-white shadow-sm p-4 flex justify-between items-center px-8">
            <div class="flex items-center space-x-2">
                <div class="w-8 h-8 bg-purple-600 rounded-lg flex items-center justify-center text-white font-bold">+</div>
                <span class="font-bold text-lg text-gray-700">无障碍智慧医疗 · 诊间</span>
            </div>
            <div class="flex items-center space-x-4">
                <span class="text-sm bg-purple-50 text-purple-700 px-3 py-1 rounded-full">医生: {{ doctorId }} ({{ doctorDept }})</span>
                <button @click="logout" class="text-gray-400 hover:text-red-500 text-sm">退出</button>
            </div>
        </nav>

        <!-- 医生工作区主界面 -->
        <div v-if="view === 'doctor-view'" class="max-w-7xl mx-auto p-6 grid grid-cols-12 gap-6">
            <div class="col-span-3 bg-white rounded-2xl shadow-sm p-4 h-fit">
                <h3 class="font-bold border-b pb-4 mb-4">待诊患者 ({{ patients.length }}) <span class="text-sm font-normal text-gray-500 ml-2">{{ doctorDept }}</span></h3>
                <div v-if="patients.length === 0" class="text-gray-400 text-center py-4">暂无本科室患者</div>
                <div v-else class="space-y-3 max-h-[600px] overflow-y-auto">
                    <div v-for="p in patients" :key="p.id" @click="selectPatient(p.id)" class="p-3 rounded-xl cursor-pointer transition-colors" :class="selectedPatientId === p.id ? 'bg-blue-50 border-l-4 border-blue-500' : 'bg-gray-50 hover:bg-gray-100'">
                        <div class="flex items-center justify-between">
                            <span class="font-medium">{{ p.name }} <span v-if="p.isDeaf" class="text-xs bg-blue-200 text-blue-800 px-2 py-0.5 rounded-full ml-2">聋哑</span></span>
                        </div>
                        <div class="text-xs text-gray-500 mt-1 truncate">{{ p.symptoms || '无病历' }}</div>
                    </div>
                </div>
            </div>
            <div class="col-span-9 space-y-6" v-if="selectedPatient">
                <div class="bg-white p-6 rounded-2xl shadow-sm border-t-4 border-blue-600">
                    <div class="flex flex-wrap justify-between items-start gap-4 mb-4">
                        <div>
                            <h2 class="text-xl font-bold flex items-center gap-2">
                                当前患者：{{ selectedPatient.name }}
                                <span v-if="isHandsignOpen" class="bg-green-100 text-green-600 px-3 py-1 rounded text-sm">手语辅助已开启</span>
                            </h2>
                            <p class="text-sm text-gray-500">ID: {{ selectedPatient.id }} | 科室: {{ selectedPatient.dept }}</p>
                        </div>
                        <button @click="openPatientAssist" class="flex items-center gap-2 bg-indigo-50 hover:bg-indigo-100 text-indigo-700 px-4 py-2 rounded-xl font-medium transition">
                            <svg class="w-5 h-5" fill="currentColor" viewBox="0 0 20 20"><path d="M10 12a2 2 0 100-4 2 2 0 000 4z"></path><path fill-rule="evenodd" d="M.458 10C1.732 5.943 5.522 3 10 3s8.268 2.943 9.542 7c-1.274 4.057-5.064 7-9.542 7S1.732 14.057.458 10zM14 10a4 4 0 11-8 0 4 4 0 018 0z" clip-rule="evenodd"></path></svg>
                            手语辅助
                        </button>
                    </div>
                    <div class="grid grid-cols-3 gap-4 text-sm mb-2">
                        <div class="bg-slate-50 p-3 rounded-lg"><span class="text-gray-400 block">主诉/症状：</span><span class="font-medium text-blue-700">{{ selectedPatient.symptoms || '未记录' }}</span></div>
                        <div class="bg-slate-50 p-3 rounded-lg"><span class="text-gray-400 block">过敏史：</span><span class="font-medium text-red-600">{{ selectedPatient.allergy || '不详' }}</span></div>
                        <div class="bg-slate-50 p-3 rounded-lg"><span class="text-gray-400 block">年龄/性别：</span><span class="font-medium">{{ selectedPatient.age ? selectedPatient.age + '岁' : '?' }} / {{ selectedPatient.genderText || '?' }}</span></div>
                    </div>
                </div>
                <div class="bg-white p-6 rounded-2xl shadow-sm">
                    <div class="flex justify-between items-center mb-3">
                        <h3 class="font-bold text-lg flex items-center gap-2"><span class="w-1.5 h-5 bg-blue-500 rounded-full"></span>病历记录</h3>
                        <button @click="saveMedicalHistory" class="text-sm bg-green-50 text-green-600 px-3 py-1 rounded-full hover:bg-green-100">保存病历</button>
                    </div>
                    <textarea v-model="selectedPatient.medicalHistory" rows="3" class="w-full border border-gray-200 rounded-xl p-3 text-sm focus:ring-2 focus:ring-blue-200 outline-none" placeholder="输入诊断..."></textarea>
                </div>
                <div class="bg-white p-6 rounded-2xl shadow-sm">
                    <div class="flex justify-between items-center mb-3">
                        <h3 class="font-bold text-lg flex items-center gap-2"><span class="w-1.5 h-5 bg-green-500 rounded-full"></span>处方管理</h3>
                        <button @click="showAddPrescription = true" class="text-sm bg-blue-50 text-blue-600 px-3 py-1 rounded-full hover:bg-blue-100">+ 添加药品</button>
                    </div>
                    <div v-if="selectedPatient.prescriptions?.length" class="space-y-2 mb-4">
                        <div v-for="(rx, index) in selectedPatient.prescriptions" :key="index" class="prescription-item p-3 rounded-lg flex justify-between items-center">
                            <div>
                                <span class="font-medium">{{ rx.drug }}</span>
                                <span class="text-sm text-gray-600 ml-2">{{ rx.dosage }}</span>
                                <span class="text-xs text-gray-400 block">{{ rx.usage }}</span>
                            </div>
                            <button @click="removePrescription(index)" class="text-red-400 hover:text-red-600 text-xs">删除</button>
                        </div>
                    </div>
                    <div v-else class="text-gray-400 text-sm py-2">暂无处方</div>
                    <div v-if="showAddPrescription" class="mt-4 border-t pt-4">
                        <div class="grid grid-cols-2 gap-3">
                            <input v-model="newRx.drug" placeholder="药品名" class="border rounded-lg p-2 text-sm">
                            <input v-model="newRx.dosage" placeholder="剂量" class="border rounded-lg p-2 text-sm">
                            <input v-model="newRx.usage" placeholder="用法" class="border rounded-lg p-2 text-sm col-span-2">
                        </div>
                        <div class="flex justify-end gap-2 mt-3">
                            <button @click="showAddPrescription = false" class="text-xs text-gray-500 px-3 py-1">取消</button>
                            <button @click="addPrescription" class="text-xs bg-green-600 text-white px-4 py-1 rounded-full">保存</button>
                        </div>
                    </div>
                </div>

                <!-- 患者手语输入区域，仅在手语辅助窗口打开时显示 -->
                <div v-if="isHandsignOpen" class="bg-white p-6 rounded-2xl shadow-sm mt-4">
                    <h3 class="font-bold text-lg flex items-center gap-2">
                        <span class="w-1.5 h-5 bg-green-500 rounded-full"></span>患者手语输入
                    </h3>
                    <div class="space-y-2 max-h-60 overflow-y-auto">
                        <div v-if="patientMessages.length === 0" class="text-gray-400 text-center py-4">
                            暂无手语消息
                        </div>
                        <div v-for="msg in patientMessages" :key="msg.id" class="bg-green-50 p-3 rounded-lg">
                            <div class="text-sm text-green-800">{{ msg.content }}</div>
                            <div class="text-xs text-gray-400 mt-1">{{ formatTime(msg.created_at) }}</div>
                        </div>
                    </div>
                </div>

                <!-- 医生语音转文字区域，仅在手语辅助窗口打开时显示 -->
                <div v-if="isHandsignOpen" class="bg-white p-6 rounded-2xl shadow-sm mt-4">
                    <div class="flex justify-between items-center mb-3">
                        <h3 class="font-bold text-lg flex items-center gap-2"><span class="w-1.5 h-5 bg-purple-500 rounded-full"></span>医生语音转文字</h3>
                    </div>
                    <div>
                        <input v-model="doctorVoiceText" type="text" placeholder="输入文字或使用语音..." class="w-full border rounded-lg p-2 mb-2">
                        <div v-if="doctorRecognizedText" class="mb-3 p-2 bg-yellow-50 border border-yellow-200 rounded-lg">
                            <div class="text-sm text-gray-600 mb-2">识别结果：</div>
                            <div class="flex items-center gap-2">
                                <span class="flex-1 text-gray-800">{{ doctorRecognizedText }}</span>
                                <button @click="confirmDoctorVoiceText" class="bg-green-500 text-white px-3 py-1 rounded text-sm">确认</button>
                                <button @click="cancelDoctorVoiceText" class="bg-gray-300 text-gray-700 px-3 py-1 rounded text-sm">取消</button>
                            </div>
                        </div>
                        <div class="flex items-center gap-2 mb-3">
                            <button @click="isRecordingVoiceForDoc ? stopDoctorVoiceRecording() : startDoctorVoiceRecording()" 
                                    :class="isRecordingVoiceForDoc ? 'bg-red-500 hover:bg-red-600' : 'bg-green-500 hover:bg-green-600'"
                                    class="text-white px-4 py-2 rounded-lg text-sm flex items-center gap-1 transition-colors">
                                <span v-if="!isRecordingVoiceForDoc">🎤 开始语音</span>
                                <span v-else>⏹ 停止录音</span>
                            </button>
                            <span v-if="isRecordingVoiceForDoc" class="text-sm text-red-500 animate-pulse">录音中...</span>
                        </div>
                        <div class="flex justify-end">
                            <button @click="sendDoctorVoiceMessage" class="bg-blue-600 text-white px-4 py-2 rounded-lg hover:bg-blue-700">
                                发送给患者
                            </button>
                        </div>
                    </div>
                </div>
            </div>
            <div v-else class="col-span-9 bg-white rounded-2xl shadow-sm p-12 text-center text-gray-400">请从左侧选择一个患者开始就诊</div>
        </div>
    `
});

app.mount('#app');