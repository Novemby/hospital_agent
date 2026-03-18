import { createApp, ref, computed, onMounted, onUnmounted } from 'vue';
import { getPatientById, upsertPatient, addMessage } from './services/patientService.js';
import { patientLoginAPI, setCurrentPatient, getCurrentPatient, clearAuth } from './services/authService.js';

const app = createApp({
    setup() {
        const currentUserInfo = getCurrentPatient();
        const currentId = ref(currentUserInfo ? currentUserInfo.id : '');
        const idCardInput = ref(''); // 专门用于绑定的身份证输入框
        const view = ref(currentId.value ? 'reg-home' : 'login');

        const isScanning = ref(false);
        const streamText = ref('');
        const triageResult = ref(null);
        const refreshKey = ref(0);

        const goToRoleSelect = () => { window.location.href = 'index.html'; };

        const currentPatientName = computed(() => {
            refreshKey.value;
            if (!currentId.value) return '';
            const p = getPatientById(currentId.value);
            return p ? p.name : '';
        });

        // ================= 核心修改：对接后端登录 =================
        const patientLogin = async () => {
            if (!idCardInput.value.trim()) return alert('请输入身份证号');
            try {
                // 1. 调用后端 API 进行真实校验
                const backendData = await patientLoginAPI(idCardInput.value);

                // 2. 将后端真实数据同步到 LocalStorage，保持原有组件可用
                let patient = getPatientById(backendData.id);
                if (!patient) {
                    patient = {
                        id: backendData.id,
                        name: backendData.name,
                        isDeaf: backendData.is_deaf,
                        allergy: backendData.allergy_history,
                        symptoms: '', dept: '', medicalHistory: '', prescriptions: [], messages: []
                    };
                    upsertPatient(patient);
                }

                // 3. 更新系统状态
                setCurrentPatient(backendData);
                currentId.value = backendData.id;
                view.value = 'reg-home';
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

        // ... (以下保留你原有的挂号、扫描逻辑)
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
        const quickTakeNumber = () => { alert(`现场取号成功，您前面还有 ${Math.floor(Math.random()*5)+1} 人`); };
        const quickPayment = () => { alert('当前待缴费金额 ￥0.00，无欠费'); };

        const startScanning = () => {
            isScanning.value = true;
            streamText.value = "识别中：肩膀...酸...疼...";
            setTimeout(() => {
                streamText.value = "完整转写：我感觉左边肩膀酸疼得很厉害，手抬不起来，已经持续一周了。";
                triageResult.value = { dept: "骨科 - 关节门诊" };
                isScanning.value = false;
            }, 2500);
        };

        const confirmRegistration = () => {
            const patient = getPatientById(currentId.value) || { id: currentId.value, name: `患者${currentId.value}`, messages: [], isDeaf: true, prescriptions: [] };
            patient.symptoms = streamText.value;
            patient.dept = triageResult.value?.dept || '待分诊';
            upsertPatient(patient);
            addMessage(currentId.value, { type: 'pat', content: `[手语主诉] ${streamText.value}` });
            alert(`ID: ${currentId.value} 挂号成功！请前往 ${triageResult.value.dept} 候诊。`);
            view.value = 'reg-home';
            streamText.value = '';
            triageResult.value = null;
        };

        const goBack = () => { view.value = 'reg-home'; };

        const handleStorageChange = (e) => {
            if (e.key === 'smart_hospital_patients' || e.key === null) refreshKey.value++;
        };

        onMounted(() => { window.addEventListener('storage', handleStorageChange); });
        onUnmounted(() => { window.removeEventListener('storage', handleStorageChange); });

        return {
            view, currentId, idCardInput, isScanning, streamText, triageResult, currentPatientName,
            goToRoleSelect, patientLogin, logout, quickAppointment, quickTakeNumber, quickPayment,
            startScanning, confirmRegistration, goBack
        };
    },
    template: `
        <div v-if="view === 'login'" class="flex items-center justify-center min-h-screen">
            <div class="bg-white p-8 rounded-2xl shadow-2xl w-96">
                <button @click="goToRoleSelect" class="text-sm text-blue-600 mb-4 flex items-center">← 返回场景选择</button>
                <div class="text-center mb-6">
                    <div class="inline-block p-3 bg-green-100 rounded-full mb-3">
                        <svg class="w-8 h-8 text-green-600" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z"></path></svg>
                    </div>
                    <h2 class="text-2xl font-bold">患者挂号登录</h2>
                </div>
                <input v-model="idCardInput" type="text" placeholder="请输入真实身份证号" class="w-full border-2 border-gray-100 p-3 rounded-xl mb-5 focus:border-green-500 outline-none">
                <button @click="patientLogin" class="w-full bg-green-600 text-white py-3 rounded-xl font-bold hover:bg-green-700">进入挂号大厅</button>
            </div>
        </div>

        <nav v-else class="bg-white shadow-sm p-4 flex justify-between items-center px-8">
            <div class="flex items-center space-x-2">
                <div class="w-8 h-8 bg-blue-600 rounded-lg flex items-center justify-center text-white font-bold">+</div>
                <span class="font-bold text-lg text-gray-700">无障碍智慧医疗 · 挂号</span>
            </div>
            <div class="flex items-center space-x-4">
                <span class="text-sm bg-blue-50 text-blue-700 px-3 py-1 rounded-full">患者ID: {{ currentId }}</span>
                <button @click="logout" class="text-gray-400 hover:text-red-500 text-sm">退出</button>
            </div>
        </nav>

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
                <div @click="view = 'sign-triage'" class="bg-gradient-to-br from-blue-600 to-indigo-700 p-8 rounded-3xl shadow-xl cursor-pointer hover:scale-105 transition-transform text-white flex flex-col justify-between">
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

        <div v-if="view === 'sign-triage'" class="max-w-4xl mx-auto p-8">
            <div class="bg-white rounded-3xl shadow-xl overflow-hidden">
                <div class="bg-blue-600 p-4 text-white flex justify-between items-center">
                    <span class="font-bold">手语识别分诊台 (患者: {{ currentId }})</span>
                    <button @click="goBack" class="text-sm bg-white/20 px-4 py-1 rounded-full">返回大厅</button>
                </div>
                <div class="grid grid-cols-2 gap-6 p-8">
                    <div>
                        <div class="camera-preview">
                            <div class="scan-line" v-if="isScanning"></div>
                            <div class="absolute inset-0 flex items-center justify-center">
                                <span v-if="!isScanning" class="text-gray-500">摄像头已就绪</span>
                                <div v-else class="text-center">
                                    <div class="w-16 h-16 border-4 border-blue-500 border-t-transparent rounded-full animate-spin mx-auto mb-4"></div>
                                    <span class="text-blue-400 font-bold">翻译手语中...</span>
                                </div>
                            </div>
                        </div>
                        <button @click="startScanning" v-if="!isScanning" class="w-full mt-4 bg-blue-600 text-white py-4 rounded-xl font-bold shadow-lg shadow-blue-200">开始手语输入</button>
                    </div>
                    <div class="flex flex-col space-y-4">
                        <div class="bg-slate-50 p-4 rounded-2xl flex-1 border-2 border-dashed border-slate-200">
                            <h4 class="text-xs font-bold text-gray-400 uppercase mb-2">手语实时转写</h4>
                            <p class="text-lg text-slate-700">{{ streamText || '等待输入...' }}</p>
                        </div>
                        <div v-if="triageResult" class="bg-green-50 p-4 rounded-2xl border border-green-200 animate-bounce">
                            <h4 class="text-xs font-bold text-green-600 uppercase mb-1">推荐科室</h4>
                            <p class="text-2xl font-black text-green-700">{{ triageResult.dept }}</p>
                        </div>
                        <button @click="confirmRegistration" v-if="triageResult" class="w-full bg-green-600 text-white py-4 rounded-xl font-bold">确认挂号并提交症状</button>
                    </div>
                </div>
            </div>
        </div>
    `
});

app.mount('#app');