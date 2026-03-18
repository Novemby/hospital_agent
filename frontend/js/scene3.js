import { createApp, ref, computed, onMounted, onUnmounted } from 'vue';
// 添加了 ?v=2 强制浏览器跳过旧缓存，读取最新文件
import { getPatientById, upsertPatient } from './services/patientService.js?v=2';
import { setCurrentPatient, getCurrentPatient, clearAuth, patientLoginAPI } from './services/authService.js?v=2';

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

        const goToRoleSelect = () => { window.location.href = 'index.html'; };

        // 增加 Try-Catch 防止单条数据损坏导致整个 computed 崩溃
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

        const currentPatient = computed(() => {
            refreshKey.value;
            if (!currentId.value) return null;
            try {
                return getPatientById(currentId.value);
            } catch (e) {
                return null;
            }
        });

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
                        symptoms: '', dept: '', medicalHistory: '', prescriptions: [], messages: []
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
        };

        const handleStorageChange = (e) => {
            if (e.key === 'smart_hospital_patients' || e.key === null) refreshKey.value++;
        };

        onMounted(() => { window.addEventListener('storage', handleStorageChange); });
        onUnmounted(() => { window.removeEventListener('storage', handleStorageChange); });

        return {
            view, currentId, currentPatientName, currentPatient,
            idCardInput, showReportModal, showMedicalRecordModal,
            showPaymentHistoryModal, showPrescriptionQueryModal,
            goToRoleSelect, patientLogin, logout
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
                    <!-- 更新了提示文案，匹配数据库中的张三数据 -->
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
                        <div @click="showMedicalRecordModal = true" class="bg-purple-50 p-6 rounded-xl border border-purple-200 cursor-pointer hover:shadow-md transition">
                            <h3 class="font-bold text-purple-700 mb-2">📋 病历查询</h3>
                            <p class="text-sm text-gray-600">历史病历、诊断记录</p>
                        </div>
                        <div @click="showPaymentHistoryModal = true" class="bg-orange-50 p-6 rounded-xl border border-orange-200 cursor-pointer hover:shadow-md transition">
                            <h3 class="font-bold text-orange-700 mb-2">🧾 缴费记录</h3>
                            <p class="text-sm text-gray-600">历史缴费明细</p>
                        </div>
                        <div @click="showPrescriptionQueryModal = true" class="bg-blue-50 p-6 rounded-xl border border-blue-200 cursor-pointer hover:shadow-md transition">
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

            <!-- Modals 恢复为带详细内容的版本 -->
            <div v-if="showReportModal" class="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50" @click.self="showReportModal = false">
                <div class="bg-white rounded-2xl p-6 max-w-md w-full m-4 shadow-2xl">
                    <h3 class="text-xl font-bold mb-4 flex items-center gap-2 text-green-700">
                        <span>📄 报告查询</span>
                        <span class="text-sm font-normal text-gray-500">患者：{{ currentPatientName || currentId }}</span>
                    </h3>
                    <div class="space-y-3 max-h-80 overflow-y-auto">
                        <div class="p-3 border border-green-200 rounded-lg hover:bg-green-50 cursor-pointer">
                            <div class="flex justify-between items-center">
                                <span class="font-medium">血常规报告</span>
                                <span class="text-xs bg-green-100 text-green-800 px-2 py-1 rounded">已完成</span>
                            </div>
                            <div class="text-sm text-gray-500">检验科</div>
                        </div>
                        <div class="p-3 border border-yellow-200 rounded-lg hover:bg-yellow-50 cursor-pointer">
                            <div class="flex justify-between items-center">
                                <span class="font-medium">肝功能检查</span>
                                <span class="text-xs bg-yellow-100 text-yellow-800 px-2 py-1 rounded">报告中</span>
                            </div>
                            <div class="text-sm text-gray-500">检验科</div>
                        </div>
                    </div>
                    <button @click="showReportModal = false" class="mt-4 w-full bg-gray-200 text-gray-700 px-4 py-2 rounded-lg hover:bg-gray-300">关闭</button>
                </div>
            </div>

            <div v-if="showMedicalRecordModal" class="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50" @click.self="showMedicalRecordModal = false">
                <div class="bg-white rounded-2xl p-6 max-w-md w-full m-4 shadow-2xl">
                    <h3 class="text-xl font-bold mb-4 flex items-center gap-2 text-purple-700">
                        <span>📋 病历查询</span>
                        <span class="text-sm font-normal text-gray-500">患者：{{ currentPatientName || currentId }}</span>
                    </h3>
                    <div class="space-y-4 max-h-80 overflow-y-auto">
                        <div class="border-l-4 border-purple-400 pl-3 py-1">
                            <div class="font-medium">门诊记录</div>
                            <div class="text-sm text-gray-600">主诉：左肩疼痛一周，活动受限</div>
                            <div class="text-sm text-gray-600">诊断：肩周炎</div>
                            <div class="text-xs text-gray-400 mt-1">医生：张伟</div>
                        </div>
                    </div>
                    <button @click="showMedicalRecordModal = false" class="mt-4 w-full bg-gray-200 text-gray-700 px-4 py-2 rounded-lg hover:bg-gray-300">关闭</button>
                </div>
            </div>

            <div v-if="showPaymentHistoryModal" class="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50" @click.self="showPaymentHistoryModal = false">
                <div class="bg-white rounded-2xl p-6 max-w-md w-full m-4 shadow-2xl">
                    <h3 class="text-xl font-bold mb-4 flex items-center gap-2 text-orange-700">
                        <span>🧾 缴费记录</span>
                        <span class="text-sm font-normal text-gray-500">患者：{{ currentPatientName || currentId }}</span>
                    </h3>
                    <div class="space-y-3 max-h-80 overflow-y-auto">
                        <div class="flex justify-between items-center p-2 border-b">
                            <div>
                                <div class="font-medium">门诊缴费</div>
                                <div class="text-xs text-gray-500">挂号费及检查费</div>
                            </div>
                            <span class="text-gray-700">¥120.00</span>
                        </div>
                    </div>
                    <button @click="showPaymentHistoryModal = false" class="mt-4 w-full bg-gray-200 text-gray-700 px-4 py-2 rounded-lg hover:bg-gray-300">关闭</button>
                </div>
            </div>

            <div v-if="showPrescriptionQueryModal" class="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50" @click.self="showPrescriptionQueryModal = false">
                <div class="bg-white rounded-2xl p-6 max-w-md w-full m-4 shadow-2xl">
                    <h3 class="text-xl font-bold mb-4 flex items-center gap-2 text-blue-700">
                        <span>💊 处方查询</span>
                        <span class="text-sm font-normal text-gray-500">患者：{{ currentPatientName || currentId }}</span>
                    </h3>
                    <div class="space-y-3 max-h-80 overflow-y-auto">
                        <div v-if="currentPatient && currentPatient.prescriptions && currentPatient.prescriptions.length > 0">
                            <div v-for="(rx, index) in currentPatient.prescriptions" :key="index" class="p-3 border border-blue-200 rounded-lg bg-blue-50 mb-2">
                                <div class="font-medium">{{ rx.drug }}</div>
                                <div class="text-sm text-gray-600">剂量：{{ rx.dosage }}</div>
                                <div class="text-sm text-gray-600">用法：{{ rx.usage }}</div>
                            </div>
                        </div>
                        <div v-else class="text-gray-400 text-center py-4">
                            暂无处方记录
                        </div>
                    </div>
                    <button @click="showPrescriptionQueryModal = false" class="mt-4 w-full bg-gray-200 text-gray-700 px-4 py-2 rounded-lg hover:bg-gray-300">关闭</button>
                </div>
            </div>
        </div>
    `
});

// 全局防白屏机制：如果 Vue 发生致命崩溃，将白屏替换为带有重置按钮的错误提示
app.config.errorHandler = (err, instance, info) => {
    console.error("Vue 渲染错误:", err, info);
    document.body.innerHTML = `
        <div style="padding: 40px; text-align: center; font-family: sans-serif; background: #f8fafc; min-height: 100vh;">
            <div style="display: inline-block; text-align: left; background: #fef2f2; border: 2px solid #f87171; border-radius: 16px; padding: 30px; max-width: 600px; box-shadow: 0 10px 25px -5px rgba(0, 0, 0, 0.1);">
                <h2 style="color: #b91c1c; margin-top: 0; font-size: 24px;">⚠️ 页面渲染遇到了致命错误</h2>
                <p style="color: #7f1d1d; font-size: 16px;">这通常是因为之前测试残留的数据格式损坏导致的。</p>
                <div style="background: #fff; padding: 15px; border-radius: 8px; margin: 20px 0; color: #333; font-family: monospace; overflow-x: auto; font-size: 14px;">
                    ${err.message}
                </div>
                <button onclick="localStorage.clear(); sessionStorage.clear(); location.reload();" style="background: #ef4444; color: white; border: none; padding: 12px 24px; border-radius: 8px; font-weight: bold; cursor: pointer; font-size: 16px; width: 100%; transition: background 0.2s;">
                    👉 点击此处：一键清理损坏的缓存并重试
                </button>
            </div>
        </div>
    `;
};

app.mount('#app');