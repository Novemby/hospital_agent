import { createApp, ref, computed, onMounted, onUnmounted } from 'vue';
import { getPatientById, upsertPatient } from './services/patientService.js?v=20260401-2';
import { setCurrentPatient, getCurrentPatient, clearAuth, patientLoginAPI } from './services/authService.js';

const app = createApp({
    setup() {
        const currentUserInfo = getCurrentPatient();
        const currentId = ref(currentUserInfo ? currentUserInfo.id : '');
        const view = ref(currentId.value ? 'myScene' : 'login');
        const idCardInput = ref('');
        const refreshKey = ref(0);

        const showReportModal = ref(false);
        const showMedicalRecordModal = ref(false);
        const showPaymentHistoryModal = ref(false);
        const showPrescriptionQueryModal = ref(false);

        // 🌟 新增：存放从后端数据库查出来的真实病历和处方
        const dbRecords = ref([]);
        const loadingRecords = ref(false);

        const goToRoleSelect = () => { window.location.href = 'index.html'; };

        const currentPatientName = computed(() => {
            refreshKey.value;
            if (!currentId.value) return '';
            try {
                const p = getPatientById(currentId.value);
                return p ? p.name : '';
            } catch (e) {
                return '数据读取异常';
            }
        });

        // 🌟 新增：向后端请求真实的病历数据
        const fetchPatientRecords = async () => {
            if (!currentId.value) return;
            loadingRecords.value = true;
            try {
                const response = await fetch(`http://localhost:8000/api/patients/${currentId.value}/records`);
                if (response.ok) {
                    dbRecords.value = await response.json();
                }
            } catch (e) {
                console.error("获取病历失败", e);
            } finally {
                loadingRecords.value = false;
            }
        };

        // 🌟 新增：打开病历弹窗时触发请求
        const openMedicalRecord = async () => {
            showMedicalRecordModal.value = true;
            await fetchPatientRecords();
        };

        // 🌟 新增：打开处方弹窗时触发请求
        const openPrescription = async () => {
            showPrescriptionQueryModal.value = true;
            await fetchPatientRecords();
        };

        const patientLogin = async () => {
            if (!idCardInput.value.trim()) return alert('请输入身份证号');
            try {
                const backendData = await patientLoginAPI(idCardInput.value);

                let patient = null;
                try {
                    patient = getPatientById(backendData.id);
                } catch (e) {
                    console.warn("读取本地缓存失败", e);
                }

                if (!patient) {
                    patient = {
                        id: backendData.id,
                        name: backendData.name,
                        isDeaf: backendData.is_deaf,
                        allergy: backendData.allergy_history,
                        symptoms: '', dept: '', medicalHistory: '', diagnosis: '', prescriptions: [], messages: []
                    };
                    try {
                        upsertPatient(patient);
                    } catch (e) {
                        console.warn("写入本地缓存失败", e);
                    }
                }

                setCurrentPatient(backendData);
                currentId.value = backendData.id;
                view.value = 'myScene';
            } catch (error) {
                alert(error.message);
            }
        };

        const logout = () => {
            clearAuth();
            currentId.value = '';
            idCardInput.value = '';
            view.value = 'login';
            dbRecords.value = []; // 清空数据
        };

        const handleStorageChange = (e) => {
            if (e.key === 'smart_hospital_patients' || e.key === null) refreshKey.value++;
        };

        onMounted(() => { window.addEventListener('storage', handleStorageChange); });
        onUnmounted(() => { window.removeEventListener('storage', handleStorageChange); });

        // 提取存在处方的记录
        const hasPrescriptions = computed(() => {
            return dbRecords.value.some(record => record.treatment_plan && record.treatment_plan.length > 0);
        });

        return {
            view, currentId, currentPatientName,
            idCardInput, showReportModal, showMedicalRecordModal,
            showPaymentHistoryModal, showPrescriptionQueryModal,
            dbRecords, loadingRecords, hasPrescriptions,
            goToRoleSelect, patientLogin, logout,
            openMedicalRecord, openPrescription
        };
    },
    template: `
        <div id="scene3-wrapper">
            <div v-if="view === 'login'" class="flex items-center justify-center min-h-screen">
                <div class="bg-white p-8 rounded-2xl shadow-2xl w-96">
                    <button @click="goToRoleSelect" class="text-sm text-blue-600 mb-4 flex items-center">← 返回场景选择</button>
                    <div class="text-center mb-6">
                        <div class="inline-block p-3 bg-purple-100 rounded-full mb-3">
                            <svg class="w-8 h-8 text-purple-600" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z"></path></svg>
                        </div>
                        <h2 class="text-2xl font-bold">患者信息登录</h2>
                    </div>
                    <input v-model="idCardInput" type="text" placeholder="演示身份证: 123456 (张三)" class="w-full border-2 border-gray-100 p-3 rounded-xl mb-5 focus:border-purple-500 outline-none">
                    <button @click="patientLogin" class="w-full bg-purple-600 text-white py-3 rounded-xl font-bold hover:bg-purple-700">进入信息大厅</button>
                </div>
            </div>

            <nav v-else class="bg-white shadow-sm p-4 flex justify-between items-center px-8">
                <div class="flex items-center space-x-2">
                    <div class="w-8 h-8 bg-purple-600 rounded-lg flex items-center justify-center text-white font-bold">+</div>
                    <span class="font-bold text-lg text-gray-700">无障碍智慧医疗 · 我的</span>
                </div>
                <div class="flex items-center space-x-4">
                    <span class="text-sm bg-purple-50 text-purple-700 px-3 py-1 rounded-full">患者ID: {{ currentId }}</span>
                    <button @click="logout" class="text-gray-400 hover:text-red-500 text-sm">退出</button>
                </div>
            </nav>

            <div v-if="view === 'myScene'" class="max-w-4xl mx-auto p-8">
                <div class="bg-white rounded-3xl shadow-xl p-8">
                    <h2 class="text-2xl font-black mb-6 flex items-center gap-2">
                        <span class="w-1.5 h-8 bg-purple-600 rounded-full"></span>
                        我的个人中心
                    </h2>
                    <div class="mb-8 p-4 bg-purple-50 rounded-xl">
                        <p class="text-purple-700 font-medium">当前登录患者：{{ currentPatientName || currentId }}</p>
                    </div>
                    <div class="grid grid-cols-2 gap-4 mt-6">
                        <div @click="showReportModal = true" class="bg-green-50 p-6 rounded-xl border border-green-200 cursor-pointer hover:shadow-md transition">
                            <h3 class="font-bold text-green-700 mb-2">📄 报告查询</h3>
                            <p class="text-sm text-gray-600">化验单、影像报告</p>
                        </div>
                        <div @click="openMedicalRecord" class="bg-purple-50 p-6 rounded-xl border border-purple-200 cursor-pointer hover:shadow-md transition">
                            <h3 class="font-bold text-purple-700 mb-2">📋 病历查询</h3>
                            <p class="text-sm text-gray-600">历史病历、诊断记录</p>
                        </div>
                        <div @click="showPaymentHistoryModal = true" class="bg-orange-50 p-6 rounded-xl border border-orange-200 cursor-pointer hover:shadow-md transition">
                            <h3 class="font-bold text-orange-700 mb-2">🧾 缴费记录</h3>
                            <p class="text-sm text-gray-600">历史缴费明细</p>
                        </div>
                        <div @click="openPrescription" class="bg-blue-50 p-6 rounded-xl border border-blue-200 cursor-pointer hover:shadow-md transition">
                            <h3 class="font-bold text-blue-700 mb-2">💊 处方查询</h3>
                            <p class="text-sm text-gray-600">查看历史处方、药品详情</p>
                        </div>
                    </div>
                    <div class="mt-8 flex gap-3">
                        <button @click="goToRoleSelect" class="bg-blue-600 text-white px-6 py-2 rounded-full text-sm hover:bg-blue-700 transition">返回场景选择</button>
                        <button @click="logout" class="bg-red-50 text-red-600 px-6 py-2 rounded-full text-sm hover:bg-red-100 transition">退出登录</button>
                    </div>
                </div>
            </div>

            <!-- 报告查询 -->
            <div v-if="showReportModal" class="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50" @click.self="showReportModal = false">
                <div class="bg-white rounded-2xl p-6 max-w-md w-full m-4 shadow-2xl">
                    <h3 class="text-xl font-bold mb-4 flex items-center gap-2 text-green-700">
                        <span>📄 报告查询</span>
                        <span class="text-sm font-normal text-gray-500">患者：{{ currentPatientName || currentId }}</span>
                    </h3>
                    <div class="text-gray-400 text-center py-8">暂未接入真实检查报告数据</div>
                    <button @click="showReportModal = false" class="mt-4 w-full bg-gray-200 text-gray-700 px-4 py-2 rounded-lg hover:bg-gray-300">关闭</button>
                </div>
            </div>

            <!-- 🌟 核心：真实病历查询（动态遍历数据库） -->
            <div v-if="showMedicalRecordModal" class="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50" @click.self="showMedicalRecordModal = false">
                <div class="bg-white rounded-2xl p-6 max-w-md w-full m-4 shadow-2xl">
                    <h3 class="text-xl font-bold mb-4 flex items-center gap-2 text-purple-700">
                        <span>📋 历史病历</span>
                        <span class="text-sm font-normal text-gray-500">患者：{{ currentPatientName || currentId }}</span>
                    </h3>
                    
                    <div v-if="loadingRecords" class="text-center py-8 text-purple-500">正在从数据库拉取病历...</div>
                    
                    <div v-else class="space-y-4 max-h-80 overflow-y-auto">
                        <div v-if="dbRecords.length === 0" class="text-gray-400 text-center py-8">
                            暂无门诊病历记录
                        </div>
                        
                        <!-- 动态循环渲染从数据库拿到的记录 -->
                        <div v-for="(record, index) in dbRecords" :key="index" class="border-l-4 border-purple-400 pl-3 py-2 bg-purple-50/30 rounded-r-lg mb-3">
                            <div class="flex justify-between items-center border-b border-purple-100 pb-1 mb-2">
                                <span class="font-bold text-purple-800">{{ record.department }} 门诊记录</span>
                                <span class="text-xs text-gray-500">{{ record.visit_date }}</span>
                            </div>
                            <div class="text-sm text-gray-700 mb-1">
                                <span class="font-bold text-gray-500">主诉：</span>
                                <span class="whitespace-pre-line">{{ record.chief_complaint || '未记录' }}</span>
                            </div>
                            <div class="text-sm text-gray-700 mb-1">
                                <span class="font-bold text-gray-500">诊断：</span>
                                <span>{{ record.diagnosis || '未记录' }}</span>
                            </div>
                            <div class="text-xs text-purple-600 mt-2 flex items-center gap-1">
                                👨‍⚕️ 医生：{{ record.doctor_name }}
                            </div>
                        </div>
                    </div>
                    <button @click="showMedicalRecordModal = false" class="mt-4 w-full bg-gray-200 text-gray-700 px-4 py-2 rounded-lg hover:bg-gray-300">关闭</button>
                </div>
            </div>

            <!-- 缴费记录 -->
            <div v-if="showPaymentHistoryModal" class="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50" @click.self="showPaymentHistoryModal = false">
                <div class="bg-white rounded-2xl p-6 max-w-md w-full m-4 shadow-2xl">
                    <h3 class="text-xl font-bold mb-4 flex items-center gap-2 text-orange-700">
                        <span>🧾 缴费记录</span>
                        <span class="text-sm font-normal text-gray-500">患者：{{ currentPatientName || currentId }}</span>
                    </h3>
                    <div class="text-gray-400 text-center py-4">暂未接入真实缴费记录数据</div>
                    <button @click="showPaymentHistoryModal = false" class="mt-4 w-full bg-gray-200 text-gray-700 px-4 py-2 rounded-lg hover:bg-gray-300">关闭</button>
                </div>
            </div>

            <!-- 🌟 核心：真实处方查询（动态遍历数据库） -->
            <div v-if="showPrescriptionQueryModal" class="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50" @click.self="showPrescriptionQueryModal = false">
                <div class="bg-white rounded-2xl p-6 max-w-md w-full m-4 shadow-2xl">
                    <h3 class="text-xl font-bold mb-4 flex items-center gap-2 text-blue-700">
                        <span>💊 处方查询</span>
                        <span class="text-sm font-normal text-gray-500">患者：{{ currentPatientName || currentId }}</span>
                    </h3>
                    
                    <div v-if="loadingRecords" class="text-center py-8 text-blue-500">正在从数据库拉取处方...</div>
                    
                    <div v-else class="space-y-4 max-h-80 overflow-y-auto">
                        <div v-if="!hasPrescriptions" class="text-gray-400 text-center py-8">
                            医生暂未开具任何处方药品
                        </div>
                        
                        <!-- 动态循环渲染从数据库拿到的记录里的处方 -->
                        <template v-for="(record, index) in dbRecords" :key="index">
                            <div v-if="record.treatment_plan && record.treatment_plan.length > 0" class="mb-4">
                                <div class="text-xs text-gray-500 mb-2">{{ record.visit_date }} - {{ record.department }}处方</div>
                                <div v-for="(rx, rIndex) in record.treatment_plan" :key="rIndex" class="p-3 border border-blue-100 rounded-xl bg-blue-50/50 mb-2 shadow-sm">
                                    <div class="font-bold text-base text-blue-900 mb-1">{{ rx.drug }}</div>
                                    <div class="flex items-center gap-2 text-sm text-gray-600 bg-white p-1.5 rounded-lg">
                                        <span class="bg-blue-100 text-blue-700 px-2 py-0.5 rounded text-xs">剂量</span> {{ rx.dosage }}
                                    </div>
                                    <div class="flex items-center gap-2 text-sm text-gray-600 bg-white p-1.5 rounded-lg mt-1">
                                        <span class="bg-green-100 text-green-700 px-2 py-0.5 rounded text-xs">用法</span> {{ rx.usage }}
                                    </div>
                                </div>
                            </div>
                        </template>
                    </div>
                    <button @click="showPrescriptionQueryModal = false" class="mt-4 w-full bg-gray-200 text-gray-700 px-4 py-2 rounded-lg hover:bg-gray-300">关闭</button>
                </div>
            </div>
        </div>
    `
});

// 全局防白屏机制
app.config.errorHandler = (err, instance, info) => {
    console.error("Vue 渲染错误:", err, info);
    document.body.innerHTML = `
        <div style="padding: 40px; text-align: center; font-family: sans-serif; background: #f8fafc; min-height: 100vh;">
            <div style="display: inline-block; text-align: left; background: #fef2f2; border: 2px solid #f87171; border-radius: 16px; padding: 30px; max-width: 600px; box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.1);">
                <h2 style="color: #b91c1c; margin-top: 0; font-size: 24px;">⚠️ 页面渲染遇到了致命错误</h2>
                <div style="background: #fff; padding: 15px; border-radius: 8px; margin: 20px 0; color: #333; font-family: monospace; overflow-x: auto; font-size: 14px;">
                    ${err.message}
                </div>
                <button onclick="localStorage.clear(); sessionStorage.clear(); location.reload();" style="background: #ef4444; color: white; border: none; padding: 12px 24px; border-radius: 8px; font-weight: bold; cursor: pointer; font-size: 16px; width: 100%; transition: background 0.2s;">
                    👉 点击此处：一键清理缓存并重试
                </button>
            </div>
        </div>
    `;
};

app.mount('#app');
