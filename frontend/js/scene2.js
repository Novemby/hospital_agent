import { createApp, ref, computed, onMounted, onUnmounted, watch } from 'vue';
import { getPatients, getPatientById, upsertPatient, addMessage } from './services/patientService.js';
import { doctorLoginAPI, setCurrentDoctor, getCurrentDoctor, clearAuth } from './services/authService.js';

const app = createApp({
    setup() {
        // 读取本地缓存的医生状态
        const currentDoc = getCurrentDoctor();
        const doctorId = ref(currentDoc ? currentDoc.work_no : '');
        const doctorPwd = ref('');
        const doctorDept = ref(currentDoc ? currentDoc.department : '');
        const view = ref(currentDoc ? 'doctor-view' : 'login');

        const refreshKey = ref(0);

        const goToRoleSelect = () => {
            window.location.href = 'index.html';
        };

        // 患者列表（响应式）
        const patients = ref([]);
        const selectedPatientId = ref('');

        // 加载并过滤患者数据
        const loadPatients = () => {
            const allPatients = getPatients();
            if (doctorDept.value) {
                patients.value = allPatients.filter(p => p.dept === doctorDept.value);
            } else {
                patients.value = allPatients;
            }
        };

        loadPatients();

        // 监听 storage 事件
        const handleStorageChange = (e) => {
            if (e.key === 'smart_hospital_patients' || e.key === null) {
                loadPatients();
            }
        };

        onMounted(() => { window.addEventListener('storage', handleStorageChange); });
        onUnmounted(() => { window.removeEventListener('storage', handleStorageChange); });

        const selectedPatient = computed(() => {
            return patients.value.find(p => p.id === selectedPatientId.value) || null;
        });

        // ==========================================
        // 核心修改：对接 FastAPI 后端的医生登录
        // ==========================================
        const doctorLogin = async () => {
            if (!doctorId.value || !doctorPwd.value) {
                return alert('工号和密码不能为空');
            }
            try {
                // 调用真实后端 API
                const backendData = await doctorLoginAPI(doctorId.value, doctorPwd.value);

                // 登录成功，更新系统状态
                setCurrentDoctor(backendData);
                doctorId.value = backendData.work_no;
                doctorDept.value = backendData.department;
                view.value = 'doctor-view';

                // 刷新患者列表
                loadPatients();
                if (patients.value.length > 0) {
                    selectedPatientId.value = patients.value[0].id;
                } else {
                    selectedPatientId.value = '';
                }
            } catch (error) {
                // 这里的报错会直接显示后端传回的“工号或密码错误”
                alert(error.message);
            }
        };

        const logout = () => {
            clearAuth();
            doctorId.value = '';
            doctorPwd.value = '';
            doctorDept.value = '';
            view.value = 'login';
        };

        const selectPatient = (id) => {
            selectedPatientId.value = id;
        };

        // --- 以下为诊间辅助功能逻辑 (保持原样) ---
        const isPatientSigning = ref(false);
        const lastPatientSignText = ref('');
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
        const showAddPrescription = ref(false);
        const newRx = ref({ drug: '', dosage: '', usage: '' });

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

        const sendVoiceMessage = () => {
            if (!voiceInput.value.trim() || !selectedPatient.value) return;
            addMessage(selectedPatient.value.id, { type: 'doc', content: voiceInput.value });
            voiceInput.value = '';
        };

        const simulatePatientSignInput = () => {
            if (isPatientSigning.value) return;
            isPatientSigning.value = true;
            lastPatientSignText.value = '';
            setTimeout(() => {
                const phrases = ["我有点头晕，想吐","这个地方疼（指着胸口）","昨晚没睡好，一直咳嗽","药吃完了，需要再开一点","医生，我血压高吗？"];
                lastPatientSignText.value = phrases[Math.floor(Math.random() * phrases.length)];
                isPatientSigning.value = false;
            }, 2000);
        };

        const sendPatientSignMessage = () => {
            if (!lastPatientSignText.value || !selectedPatient.value) return;
            addMessage(selectedPatient.value.id, { type: 'pat', content: `[手语] ${lastPatientSignText.value}` });
            lastPatientSignText.value = '';
        };

        const queryTerm = (term) => {
            selectedTerm.value = term;
            termExplanation.value = termDict[term] || '暂无解释';
        };

        const openPatientAssist = () => {
            if (!selectedPatient.value) return alert('请先选择一位患者');
            view.value = 'patient-assist';
        };

        const backToDoctor = () => {
            view.value = 'doctor-view';
        };

        const doctorMessagesForPatient = computed(() => {
            if (!selectedPatient.value) return [];
            return selectedPatient.value.messages?.filter(m => m.type === 'doc').slice(-5) || [];
        });

        watch(view, (newVal) => {
            if (newVal === 'doctor-view' && patients.value.length > 0) {
                selectedPatientId.value = patients.value[0].id;
            }
            if (newVal !== 'doctor-view') showAddPrescription.value = false;
        });

        return {
            view, doctorId, doctorPwd, doctorDept,
            patients, selectedPatientId, selectedPatient,
            isPatientSigning, lastPatientSignText, selectedTerm, termExplanation,
            voiceInput, showAddPrescription, newRx,
            doctorMessagesForPatient,
            goToRoleSelect,
            doctorLogin, logout, selectPatient,
            saveMedicalHistory, addPrescription, removePrescription, sendVoiceMessage,
            simulatePatientSignInput, sendPatientSignMessage, queryTerm,
            openPatientAssist, backToDoctor
        };
    },
    template: `
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
                <input v-model="doctorPwd" type="password" placeholder="密码" @keyup.enter="doctorLogin" class="w-full border-2 border-gray-100 p-3 rounded-xl mb-5 focus:border-purple-500 outline-none">
                <button @click="doctorLogin" class="w-full bg-purple-600 text-white py-3 rounded-xl font-bold hover:bg-purple-700">进入诊间</button>
                <div class="text-xs text-gray-400 mt-4 text-center">演示账号: d001/123456 (骨科), d002/123456 (发热门诊)</div>
            </div>
        </div>

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
                                <span class="bg-blue-100 text-blue-600 px-3 py-1 rounded text-sm">手语辅助已开启</span>
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
                        <div class="bg-slate-50 p-3 rounded-lg"><span class="text-gray-400 block">分诊科室：</span><span class="font-medium">{{ selectedPatient.dept || '待分诊' }}</span></div>
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

                <div class="bg-white p-6 rounded-2xl shadow-sm">
                    <div class="flex justify-between items-center mb-3">
                        <h3 class="font-bold text-lg flex items-center gap-2"><span class="w-1.5 h-5 bg-purple-500 rounded-full"></span>语音转文字发送</h3>
                        <span class="text-xs text-gray-400">模拟语音识别</span>
                    </div>
                    <div class="flex space-x-2">
                        <input v-model="voiceInput" @keyup.enter="sendVoiceMessage" type="text" placeholder="输入文字..." class="flex-1 border rounded-xl px-4 py-3 outline-none focus:ring-2 focus:ring-purple-400">
                        <button @click="sendVoiceMessage" class="bg-purple-600 text-white px-6 rounded-xl font-bold">发送</button>
                    </div>
                </div>
            </div>
            <div v-else class="col-span-9 bg-white rounded-2xl shadow-sm p-12 text-center text-gray-400">请从左侧选择一个患者开始就诊</div>
        </div>

        <div v-if="view === 'patient-assist'" class="max-w-6xl mx-auto p-6">
            <div class="bg-white rounded-3xl shadow-xl overflow-hidden">
                <div class="bg-indigo-600 p-4 text-white flex justify-between items-center">
                    <span class="font-bold flex items-center gap-2">
                        <svg class="w-5 h-5" fill="currentColor" viewBox="0 0 20 20"><path d="M10 12a2 2 0 100-4 2 2 0 000 4z"></path><path fill-rule="evenodd" d="M.458 10C1.732 5.943 5.522 3 10 3s8.268 2.943 9.542 7c-1.274 4.057-5.064 7-9.542 7S1.732 14.057.458 10zM14 10a4 4 0 11-8 0 4 4 0 018 0z" clip-rule="evenodd"></path></svg>
                        患者手语辅助 · {{ selectedPatient?.name }}
                    </span>
                    <button @click="backToDoctor" class="text-sm bg-white/20 px-4 py-1 rounded-full hover:bg-white/30">返回诊间</button>
                </div>

                <div class="grid md:grid-cols-2 gap-6 p-6">
                    <div class="space-y-4">
                        <div class="camera-preview h-48">
                            <div class="scan-line" v-if="isPatientSigning"></div>
                            <div class="absolute inset-0 flex items-center justify-center">
                                <span v-if="!isPatientSigning" class="text-gray-500">点击下方按钮开始手语</span>
                                <div v-else class="text-center">
                                    <div class="w-12 h-12 border-4 border-indigo-500 border-t-transparent rounded-full animate-spin mx-auto mb-2"></div>
                                    <span class="text-indigo-400 text-sm">手语翻译中...</span>
                                </div>
                            </div>
                        </div>
                        <button @click="simulatePatientSignInput" :disabled="isPatientSigning" class="w-full bg-indigo-600 text-white py-3 rounded-xl font-bold disabled:opacity-50">
                            {{ isPatientSigning ? '翻译中...' : '开始手语输入' }}
                        </button>
                        <div class="bg-slate-50 p-4 rounded-2xl border border-slate-200">
                            <h4 class="text-xs font-bold text-gray-400 uppercase mb-1">最新手语转写</h4>
                            <p class="text-lg">{{ lastPatientSignText || '暂无' }}</p>
                        </div>
                    </div>

                    <div class="space-y-4">
                        <div class="bg-blue-50 p-4 rounded-2xl border border-blue-200">
                            <div class="flex justify-between items-center mb-2">
                                <h4 class="text-xs font-bold text-blue-600 uppercase">👨‍⚕️ 医生语音</h4>
                                <span class="text-xs text-gray-400">最新{{ doctorMessagesForPatient.length }}条</span>
                            </div>
                            <div class="space-y-2 max-h-40 overflow-y-auto">
                                <div v-for="(msg, idx) in doctorMessagesForPatient" :key="idx" class="bg-white p-2 rounded-lg text-sm border-l-4 border-blue-400">{{ msg.content }}</div>
                                <div v-if="doctorMessagesForPatient.length === 0" class="text-gray-400 text-sm">暂无医生消息</div>
                            </div>
                        </div>

                        <div class="bg-purple-50 p-4 rounded-2xl border border-purple-200">
                            <h4 class="text-xs font-bold text-purple-600 uppercase mb-2">🤖 AI 医学词典</h4>
                            <p class="text-sm text-gray-600 mb-2">点击词语查看解释：</p>
                            <div class="flex flex-wrap gap-2 mb-3">
                                <span class="term-tag" @click="queryTerm('心肌缺血')">心肌缺血</span>
                                <span class="term-tag" @click="queryTerm('心律失常')">心律失常</span>
                                <span class="term-tag" @click="queryTerm('高血压')">高血压</span>
                                <span class="term-tag" @click="queryTerm('糖皮质激素')">糖皮质激素</span>
                                <span class="term-tag" @click="queryTerm('MRI')">MRI</span>
                            </div>
                            <div v-if="selectedTerm" class="bg-white p-3 rounded-lg border border-purple-300 text-sm">
                                <span class="font-bold text-purple-700">{{ selectedTerm }}:</span> {{ termExplanation }}
                            </div>
                        </div>

                        <button @click="sendPatientSignMessage" :disabled="!lastPatientSignText" class="w-full bg-green-600 text-white py-3 rounded-xl font-bold disabled:opacity-50">
                            发送手语内容给医生
                        </button>
                    </div>
                </div>
            </div>
        </div>
    `
});

app.mount('#app');