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
        const voiceInput = ref('');

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
                    selectedPatientId.value = String(patients.value[0].id);
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
            if (handsignWindow && !handsignWindow.closed) handsignWindow.close();
            if (windowCheckInterval) clearInterval(windowCheckInterval);
            isHandsignOpen.value = false;
        };

        const loadPatients = () => {
            const allPatients = getPatients();
            // 🌟 新增过滤条件：只保留没有被标记为 is_finished 的患者
            const activePatients = allPatients.filter(p => !p.is_finished);

            if (doctorDept.value) {
                patients.value = activePatients.filter(p => p.dept === doctorDept.value);
            } else {
                patients.value = activePatients;
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

        // 🌟 修复 Bug：强制转换为 String 比较，避免 Number 与 String 的类型隐式不一致导致无法切换
        const selectedPatient = computed(() => {
            return patients.value.find(p => String(p.id) === String(selectedPatientId.value)) || null;
        });

        const selectPatient = (id) => {
            // 如果手语辅助窗口开着，切换患者时关闭它，避免混乱
            if (isHandsignOpen.value && handsignWindow && !handsignWindow.closed) {
                handsignWindow.close();
                isHandsignOpen.value = false;
            }
            selectedPatientId.value = String(id);
            // 重置添加处方状态
            showAddPrescription.value = false;
            newRx.value = { drug: '', dosage: '', usage: '' };
        };

        const saveMedicalHistory = () => {
            if (selectedPatient.value) {
                selectedPatient.value.lastUpdated = new Date().toLocaleTimeString();
                upsertPatient(selectedPatient.value);
                alert('病历已保存');
            }
        };

        // 🌟 新增：结束就诊功能
        const finishConsultation = () => {
            if (!selectedPatient.value) return;
            if (!confirm(`确定要结束患者【${selectedPatient.value.name}】的就诊吗？结束后的患者将从待诊列表中移除。`)) return;

            // 1. 标记患者就诊完成（写入 localStorage）
            selectedPatient.value.is_finished = true;
            upsertPatient(selectedPatient.value);

            // 2. 重新加载患者列表（已自动过滤掉 is_finished = true 的患者）
            loadPatients();

            // 3. 自动叫号（选择列表中的下一个患者），如果列表为空则清空选中状态
            if (patients.value.length > 0) {
                selectPatient(patients.value[0].id);
            } else {
                selectedPatientId.value = '';
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

        const backToDoctor = () => {};

        return {
            view, doctorId, doctorPwd, doctorDept, loginLoading,
            patients, selectedPatientId, selectedPatient,
            doctorMessagesForPatient, voiceInput,
            selectedTerm, termExplanation,
            showAddPrescription, newRx,
            goToRoleSelect, doctorLogin, logout, selectPatient,
            saveMedicalHistory, finishConsultation, addPrescription, removePrescription,
            sendVoiceMessage, openPatientAssist, backToDoctor,
            queryTerm,
            isHandsignOpen,
            doctorVoiceText, doctorRecognizedText, isRecordingVoiceForDoc,
            startDoctorVoiceRecording, stopDoctorVoiceRecording,
            confirmDoctorVoiceText, cancelDoctorVoiceText, sendDoctorVoiceMessage,
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
                    <!-- 🌟 修复点击高亮匹配问题：将 p.id 强转为 String -->
                    <div v-for="p in patients" :key="p.id" @click="selectPatient(p.id)" class="p-3 rounded-xl cursor-pointer transition-colors" :class="String(selectedPatientId) === String(p.id) ? 'bg-blue-50 border-l-4 border-blue-500' : 'bg-gray-50 hover:bg-gray-100'">
                        <div class="flex items-center justify-between">
                            <span class="font-medium">
                                {{ p.name }} 
                                <span v-if="p.isDeaf || p.is_deaf" class="text-xs bg-blue-200 text-blue-800 px-2 py-0.5 rounded-full ml-2">聋哑</span>
                            </span>
                        </div>
                        <div class="text-xs text-gray-500 mt-1 truncate">
                            <span v-if="p.triage_struct" class="text-indigo-600 font-bold mr-1">[AI分诊]</span>
                            {{ p.triage_struct?.primary_symptom || p.symptoms || '待接诊' }}
                        </div>
                    </div>
                </div>
            </div>
            
            <div class="col-span-9 space-y-6" v-if="selectedPatient">
                <!-- 顶部：患者信息与大屏看板 -->
                <div class="bg-white p-6 rounded-2xl shadow-sm border-t-4 border-blue-600 transition-all duration-300">
                    <div class="flex flex-wrap justify-between items-start gap-4 mb-4">
                        <div>
                            <h2 class="text-xl font-bold flex items-center gap-2">
                                当前患者：{{ selectedPatient.name }}
                                <span v-if="isHandsignOpen" class="bg-green-100 text-green-600 px-3 py-1 rounded text-sm">手语辅助已开启</span>
                            </h2>
                            <p class="text-sm text-gray-500 mt-1">ID: {{ selectedPatient.id }} | 挂号科室: {{ selectedPatient.dept }}</p>
                        </div>
                        <!-- 🌟 新增：结束就诊按钮组合 -->
                        <div class="flex items-center gap-3">
                            <button @click="openPatientAssist" class="flex items-center gap-2 bg-indigo-50 hover:bg-indigo-100 text-indigo-700 px-4 py-2 rounded-xl font-medium transition">
                                <svg class="w-5 h-5" fill="currentColor" viewBox="0 0 20 20"><path d="M10 12a2 2 0 100-4 2 2 0 000 4z"></path><path fill-rule="evenodd" d="M.458 10C1.732 5.943 5.522 3 10 3s8.268 2.943 9.542 7c-1.274 4.057-5.064 7-9.542 7S1.732 14.057.458 10zM14 10a4 4 0 11-8 0 4 4 0 018 0z" clip-rule="evenodd"></path></svg>
                                打开诊间手语辅助
                            </button>
                            <button @click="finishConsultation" class="bg-green-600 hover:bg-green-700 text-white px-5 py-2 rounded-xl font-bold transition shadow-sm flex items-center gap-1">
                                结束就诊 (下一位) ⏭
                            </button>
                        </div>
                    </div>

                    <!-- 🌟 核心修改：极简与智能并存的自适应网格布局 -->
                    <div class="grid grid-cols-1 md:grid-cols-2 gap-4 text-sm mb-2">
                        
                        <!-- AI 结构化主诉卡片 (仅聋哑人且有 AI 分诊数据时显示，横跨 2 列) -->
                        <div v-if="(selectedPatient.isDeaf || selectedPatient.is_deaf) && selectedPatient.triage_struct" class="md:col-span-2 bg-indigo-50/40 p-5 rounded-2xl border border-indigo-100 shadow-sm transition-all duration-500">
                            
                            <div class="flex items-center justify-between mb-3">
                                <span class="text-gray-500 font-medium">手语 AI 智能分诊摘要：</span>
                                <span class="text-xs bg-indigo-100 text-indigo-700 px-2.5 py-1 rounded-md font-bold flex items-center gap-1">
                                    🤖 提前采集
                                </span>
                            </div>

                            <div class="space-y-3">
                                <div class="flex items-center flex-wrap gap-x-4 gap-y-2 text-base">
                                    <div class="flex items-center gap-2"><span class="text-gray-400 text-xs">核心</span><span class="font-bold text-red-600">{{ selectedPatient.triage_struct.primary_symptom || '不适' }}</span></div>
                                    <span class="text-gray-300">|</span>
                                    <div class="flex items-center gap-2"><span class="text-gray-400 text-xs">部位</span><span class="font-medium text-gray-800">{{ selectedPatient.triage_struct.body_part || '未定' }}</span></div>
                                    <span class="text-gray-300">|</span>
                                    <div class="flex items-center gap-2"><span class="text-gray-400 text-xs">病程</span><span class="font-medium text-gray-800">{{ selectedPatient.triage_struct.duration || '未定' }}</span></div>
                                    
                                    <template v-if="selectedPatient.triage_struct.severity">
                                        <span class="text-gray-300">|</span>
                                        <div class="flex items-center gap-2"><span class="text-gray-400 text-xs">程度</span><span class="font-medium text-gray-800">{{ selectedPatient.triage_struct.severity }}</span></div>
                                    </template>
                                </div>
                                
                                <div v-if="selectedPatient.triage_struct.accompanying_symptoms && selectedPatient.triage_struct.accompanying_symptoms.length > 0" class="flex items-center flex-wrap gap-2 pt-2 border-t border-indigo-100/50">
                                    <span class="text-xs text-gray-500">伴随症状：</span>
                                    <span v-for="sym in selectedPatient.triage_struct.accompanying_symptoms" :key="sym" class="bg-orange-100 text-orange-700 text-xs px-2 py-1 rounded shadow-sm">
                                        {{ sym }}
                                    </span>
                                </div>

                                <div v-if="selectedPatient.triage_struct.original_text" class="text-xs text-gray-500 mt-3 bg-white p-3 rounded-lg border border-indigo-50 leading-relaxed italic shadow-inner">
                                    <span class="font-medium text-gray-400 not-italic mr-1">参考原话：</span>"{{ selectedPatient.triage_struct.original_text }}"
                                </div>
                            </div>
                        </div>

                        <!-- 基础档案信息（对普通患者来说，这两块并排显示，干净利落） -->
                        <div class="bg-slate-50 p-4 rounded-xl flex flex-col justify-center">
                            <span class="text-gray-400 block mb-1">系统既往过敏史：</span>
                            <span class="font-bold text-red-600 text-base">{{ selectedPatient.allergy || '无 / 未知' }}</span>
                        </div>
                        <div class="bg-slate-50 p-4 rounded-xl flex flex-col justify-center">
                            <span class="text-gray-400 block mb-1">患者档案：</span>
                            <span class="font-medium text-gray-800 text-base">{{ selectedPatient.age ? selectedPatient.age + '岁' : '年龄不详' }} / {{ selectedPatient.genderText || '性别不详' }}</span>
                        </div>
                    </div>
                </div>

                <div class="bg-white p-6 rounded-2xl shadow-sm">
                    <div class="flex justify-between items-center mb-3">
                        <h3 class="font-bold text-lg flex items-center gap-2"><span class="w-1.5 h-5 bg-blue-500 rounded-full"></span>病历记录与医嘱下达</h3>
                        <button @click="saveMedicalHistory" class="text-sm bg-green-50 text-green-600 px-4 py-1.5 rounded-full hover:bg-green-100 font-medium">保存当前病历</button>
                    </div>
                    <textarea v-model="selectedPatient.medicalHistory" rows="4" class="w-full border border-gray-200 rounded-xl p-3 text-sm focus:ring-2 focus:ring-blue-200 outline-none leading-relaxed" placeholder="请结合 AI 摘要与面诊情况输入最终诊断与处置方案..."></textarea>
                </div>

                <div class="bg-white p-6 rounded-2xl shadow-sm">
                    <div class="flex justify-between items-center mb-3">
                        <h3 class="font-bold text-lg flex items-center gap-2"><span class="w-1.5 h-5 bg-green-500 rounded-full"></span>处方管理</h3>
                        <button @click="showAddPrescription = true" class="text-sm bg-blue-50 text-blue-600 px-3 py-1 rounded-full hover:bg-blue-100">+ 添加处方药品</button>
                    </div>
                    <div v-if="selectedPatient.prescriptions?.length" class="space-y-2 mb-4">
                        <div v-for="(rx, index) in selectedPatient.prescriptions" :key="index" class="prescription-item bg-gray-50 border border-gray-100 p-3 rounded-lg flex justify-between items-center hover:shadow-sm transition">
                            <div>
                                <span class="font-bold text-gray-800">{{ rx.drug }}</span>
                                <span class="text-sm text-gray-600 ml-3 bg-white px-2 py-0.5 rounded border">{{ rx.dosage }}</span>
                                <span class="text-xs text-gray-500 block mt-1">{{ rx.usage }}</span>
                            </div>
                            <button @click="removePrescription(index)" class="text-red-400 hover:text-red-600 text-xs px-2 py-1 bg-red-50 rounded">删除</button>
                        </div>
                    </div>
                    <div v-else class="text-gray-400 text-sm py-4 text-center border-2 border-dashed rounded-xl">暂无处方</div>
                    
                    <div v-if="showAddPrescription" class="mt-4 border-t pt-4 bg-gray-50 -mx-6 -mb-6 p-6 rounded-b-2xl">
                        <div class="grid grid-cols-2 gap-3">
                            <input v-model="newRx.drug" placeholder="药品名称 (如: 阿莫西林胶囊)" class="border rounded-lg p-2.5 text-sm focus:ring-2 focus:ring-blue-200 outline-none">
                            <input v-model="newRx.dosage" placeholder="单次剂量 (如: 0.5g)" class="border rounded-lg p-2.5 text-sm focus:ring-2 focus:ring-blue-200 outline-none">
                            <input v-model="newRx.usage" placeholder="用法频次 (如: 口服, 每日3次)" class="border rounded-lg p-2.5 text-sm col-span-2 focus:ring-2 focus:ring-blue-200 outline-none">
                        </div>
                        <div class="flex justify-end gap-3 mt-4">
                            <button @click="showAddPrescription = false" class="text-sm text-gray-500 px-4 py-1.5 hover:bg-gray-200 rounded-lg">取消</button>
                            <button @click="addPrescription" class="text-sm bg-green-600 text-white px-6 py-1.5 rounded-lg shadow-sm hover:bg-green-700">保存到处方单</button>
                        </div>
                    </div>
                </div>

                <!-- 底部：手语辅助聊天室 (仅开启时展示) -->
                <div v-if="isHandsignOpen" class="grid grid-cols-2 gap-6 mt-4 pb-10">
                    <div class="bg-white p-6 rounded-2xl shadow-sm border border-indigo-50">
                        <h3 class="font-bold text-lg flex items-center gap-2 mb-4">
                            <span class="w-1.5 h-5 bg-indigo-500 rounded-full"></span>患者最新手语动态
                        </h3>
                        <div class="space-y-3 max-h-60 overflow-y-auto pr-2 scrollbar-thin">
                            <div v-if="patientMessages.length === 0" class="text-gray-400 text-center py-8">
                                患者正在病床前，请引导患者面对摄像头比划手语。
                            </div>
                            <div v-for="msg in patientMessages" :key="msg.id" class="bg-indigo-50/50 border border-indigo-100 p-3 rounded-xl">
                                <div class="text-sm text-indigo-900 leading-relaxed">{{ msg.content }}</div>
                                <div class="text-xs text-indigo-400 mt-2 text-right">{{ formatTime(msg.created_at) }}</div>
                            </div>
                        </div>
                    </div>

                    <div class="bg-white p-6 rounded-2xl shadow-sm border border-purple-50 flex flex-col justify-between">
                        <div>
                            <div class="flex justify-between items-center mb-4">
                                <h3 class="font-bold text-lg flex items-center gap-2"><span class="w-1.5 h-5 bg-purple-500 rounded-full"></span>向患者发送语音/文字</h3>
                            </div>
                            <div v-if="doctorRecognizedText" class="mb-4 p-3 bg-yellow-50 border border-yellow-200 rounded-xl">
                                <div class="text-xs text-yellow-600 mb-1 font-medium">识别结果确认：</div>
                                <div class="flex items-center gap-3">
                                    <span class="flex-1 text-gray-800 text-sm">{{ doctorRecognizedText }}</span>
                                    <button @click="confirmDoctorVoiceText" class="bg-green-500 text-white px-3 py-1 rounded text-sm hover:bg-green-600 shadow-sm">✓</button>
                                    <button @click="cancelDoctorVoiceText" class="bg-gray-300 text-gray-700 px-3 py-1 rounded text-sm hover:bg-gray-400">✗</button>
                                </div>
                            </div>
                            <textarea v-model="doctorVoiceText" rows="3" placeholder="打字输入或点击下方使用麦克风..." class="w-full border border-gray-200 rounded-xl p-3 text-sm focus:ring-2 focus:ring-purple-200 outline-none mb-3 resize-none"></textarea>
                        </div>
                        <div class="flex items-center justify-between">
                            <button @click="isRecordingVoiceForDoc ? stopDoctorVoiceRecording() : startDoctorVoiceRecording()" 
                                    :class="isRecordingVoiceForDoc ? 'bg-red-500 text-white shadow-inner' : 'bg-gray-100 text-gray-700 hover:bg-gray-200'"
                                    class="px-4 py-2 rounded-xl text-sm flex items-center gap-2 transition-all font-medium">
                                <span v-if="!isRecordingVoiceForDoc">🎤 麦克风录入</span>
                                <span v-else>⏹ 结束录音</span>
                                <span v-if="isRecordingVoiceForDoc" class="w-2 h-2 rounded-full bg-white animate-ping ml-1"></span>
                            </button>
                            <button @click="sendDoctorVoiceMessage" class="bg-purple-600 text-white px-6 py-2 rounded-xl hover:bg-purple-700 shadow-sm font-medium transition-all">
                                发送至患者屏幕
                            </button>
                        </div>
                    </div>
                </div>
            </div>
            
            <div v-else class="col-span-9 bg-white rounded-2xl shadow-sm p-12 flex flex-col items-center justify-center text-gray-400 h-[600px]">
                <svg class="w-16 h-16 mb-4 text-gray-200" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M12 4.354a4 4 0 110 5.292M15 21H3v-1a6 6 0 0112 0v1zm0 0h6v-1a6 6 0 00-9-5.197M13 7a4 4 0 11-8 0 4 4 0 018 0z"></path></svg>
                <p class="text-lg">今日待诊列表为空，或请从左侧选择一位患者开始就诊</p>
            </div>
        </div>
    `
});

app.mount('#app');