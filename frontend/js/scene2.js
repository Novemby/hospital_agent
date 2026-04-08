import { createApp, ref, computed, onMounted, onUnmounted, watch, nextTick } from 'vue';
import { getPatients, getPatientById, upsertPatient, addMessage, saveChatMessage, syncPatientMessagesFromBackend, syncPatientsFromBackend } from './services/patientService.js?v=20260401-2';
import { doctorLoginAPI, setCurrentDoctor, getCurrentDoctor, clearAuth } from './services/authService.js';
import { SignLanguageAgent } from './services/signLanguageAgent.js';

const app = createApp({
    setup() {
        // ========== 1. 基础状态 (必须放在最前面) ==========
        const storedDoctor = getCurrentDoctor();
        const doctorId = ref(storedDoctor ? (storedDoctor.work_no || storedDoctor.id) : '');
        const doctorPwd = ref('');
        const view = ref(storedDoctor ? 'doctor-view' : 'login');
        const loginLoading = ref(false);
        const doctorDept = ref(storedDoctor ? (storedDoctor.dept || storedDoctor.department) : '');
        const patients = ref([]);
        const selectedPatientId = ref('');
        const medicalHistoryDraft = ref('');
        const diagnosisDraft = ref('');
        let patientSyncInterval = null;
        const showEvidenceTimeline = ref(false);

        // 🌟 核心修复：把 selectedPatient 挪到最上面！防止后面 computed 用到时报未初始化的错
        const selectedPatient = computed(() => {
            return patients.value.find(p => String(p.id) === String(selectedPatientId.value)) || null;
        });
        const aiAssistInfo = computed(() => selectedPatient.value?.ai_assist || selectedPatient.value?.aiAssist || null);
        const normalizeSummaryClause = (text) => String(text || '')
            .replace(/\s+/g, ' ')
            .replace(/[。！？；;]+$/g, '')
            .replace(/^患者(主诉|表示|说|自述)?[:：]?\s*/g, '')
            .replace(/^主诉[:：]?\s*/g, '')
            .trim();
        const canonicalizeSummaryForDisplay = (text) => {
            let s = normalizeSummaryClause(text);
            const numberMap = [
                [/一/g, '1'],
                [/二|两/g, '2'],
                [/三/g, '3'],
                [/四/g, '4'],
                [/五/g, '5'],
                [/六/g, '6'],
                [/七/g, '7'],
                [/八/g, '8'],
                [/九/g, '9'],
                [/十/g, '10']
            ];
            numberMap.forEach(([pattern, replacement]) => {
                s = s.replace(pattern, replacement);
            });
            return s.replace(/[，,。；;、\s]/g, '').trim();
        };
        const isNearDuplicateDisplay = (a, b) => {
            const x = canonicalizeSummaryForDisplay(a);
            const y = canonicalizeSummaryForDisplay(b);
            if (!x || !y) return false;
            if (x === y) return true;
            return x.includes(y) || y.includes(x);
        };
        const compactComprehensiveSummary = (text) => {
            const raw = normalizeSummaryClause(text || '');
            if (!raw) return '';
            const parts = raw
                .split(/[；;]/)
                .map(item => normalizeSummaryClause(item))
                .filter(Boolean);
            const deduped = [];
            parts.forEach(part => {
                const idx = deduped.findIndex(item => isNearDuplicateDisplay(item, part));
                if (idx >= 0) {
                    deduped[idx] = part.length > deduped[idx].length ? part : deduped[idx];
                } else {
                    deduped.push(part);
                }
            });
            return deduped.join('；');
        };

        const coreSymptomText = computed(() =>
            normalizeSummaryClause(aiAssistInfo.value?.core_symptom)
            || selectedPatient.value?.triage_struct?.primary_symptom
            || ''
        );
        const dynamicSummaryText = computed(() =>
            compactComprehensiveSummary(aiAssistInfo.value?.dynamic_summary)
            || compactComprehensiveSummary(aiAssistInfo.value?.objective_summary)
            || compactComprehensiveSummary(selectedPatient.value?.triage_struct?.dynamic_summary)
            || ''
        );
        const comprehensiveSummaryText = computed(() => {
            const direct = compactComprehensiveSummary(aiAssistInfo.value?.comprehensive_summary || aiAssistInfo.value?.objective_summary || '');
            const core = normalizeSummaryClause(coreSymptomText.value);
            const dynamic = normalizeSummaryClause(dynamicSummaryText.value);

            // 优先按“核心 + 动态”合成，避免只显示当前一句而丢失主诉主线。
            if (core && dynamic) {
                if (dynamic.includes(core)) return `患者${dynamic}`;
                return `患者${core}，并伴随${dynamic}`;
            }
            if (core) return `患者${core}`;
            if (dynamic) return `患者${dynamic}`;
            return direct ? `患者${direct}` : '';
        });
        const evidenceTimeline = computed(() => {
            const raw = aiAssistInfo.value?.evidence_timeline || aiAssistInfo.value?.dynamic_summary_history || [];
            const list = (Array.isArray(raw) ? raw : []).map(item => {
                if (typeof item === 'string') {
                    return {
                        text: item,
                        source: '历史摘要',
                        at: Date.now()
                    };
                }
                return {
                    text: item?.text || '',
                    source: item?.source || '历史摘要',
                    at: Number(item?.at) || Date.now()
                };
            }).filter(item => item.text);
            return list.sort((a, b) => a.at - b.at);
        });
        const comprehensiveSummaryUpdatedAt = computed(() =>
            Number(aiAssistInfo.value?.comprehensive_summary_updated_at || selectedPatient.value?.triage_struct?.summary_updated_at || 0)
        );

        // ========== 2. UI 组件与表单状态 ==========
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

        // ========== 3. 手语辅助窗口状态 ==========
        const isHandsignOpen = ref(false);
        let handsignWindow = null;
        let windowCheckInterval = null;

        // ========== 3.5. 历史病例查看功能 ==========
        const showHistoryModal = ref(false);
        const historicalRecords = ref([]);
        const loadingHistory = ref(false);

        // ========== 4. 医生语音转文字功能 (实时识别) ==========
        const isRecordingVoiceForDoc = ref(false);
        const doctorVoiceText = ref('');
        const doctorRecognizedText = ref('');
        let recognition = null;

        // 🌟 双向聊天记录 computed (现在放到 selectedPatient 后面，非常安全)
        const chatContainerDoc = ref(null);
        const chatMessages = computed(() => {
            if (!selectedPatient.value || !Array.isArray(selectedPatient.value.messages)) return [];
            return selectedPatient.value.messages
                .filter(m => m.type === 'pat' || m.type === 'doc')
                .sort((a, b) => a.id - b.id);
        });

        // 监听消息变化，自动滚动到底部
        watch(chatMessages, async () => {
            await nextTick();
            if (chatContainerDoc.value) {
                chatContainerDoc.value.scrollTop = chatContainerDoc.value.scrollHeight;
            }
        }, { deep: true });

        // ========== 5. 实例化 Agent ==========
        const agent = new SignLanguageAgent({
            apiUrl: 'http://localhost:5000',
            onResult: () => {},
            onSequenceUpdate: () => {},
            onStatusChange: () => {},
            onHeadGesture: () => {}
        });

        // ========== 6. 医生操作方法 (实时语音识别) ==========
        const startDoctorVoiceRecording = () => {
            // 检查浏览器支持
            const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
            if (!SpeechRecognition) {
                alert('您的浏览器不支持实时语音识别，请使用 Chrome、Edge 或 Safari');
                return;
            }

            try {
                recognition = new SpeechRecognition();
                recognition.lang = 'zh-CN'; // 设置为中文
                recognition.continuous = true; // 连续识别
                recognition.interimResults = true; // 返回临时结果

                // 实时识别结果
                recognition.onresult = (event) => {
                    let interimTranscript = '';
                    let finalTranscript = '';

                    for (let i = event.resultIndex; i < event.results.length; i++) {
                        const transcript = event.results[i][0].transcript;
                        if (event.results[i].isFinal) {
                            finalTranscript += transcript;
                        } else {
                            interimTranscript += transcript;
                        }
                    }

                    // 实时更新识别文本（临时结果）
                    if (interimTranscript) {
                        doctorRecognizedText.value = interimTranscript;
                    }

                    // 确认的结果追加到输入框
                    if (finalTranscript) {
                        doctorVoiceText.value += finalTranscript;
                        doctorRecognizedText.value = ''; // 清空临时结果
                    }
                };

                recognition.onerror = (event) => {
                    console.error('语音识别错误:', event.error);
                    if (event.error === 'no-speech') {
                        // 没有检测到语音，不提示错误
                        return;
                    }
                    alert('语音识别出错: ' + event.error);
                    stopDoctorVoiceRecording();
                };

                recognition.onend = () => {
                    // 如果还在录音状态，自动重启（保持连续识别）
                    if (isRecordingVoiceForDoc.value) {
                        try {
                            recognition.start();
                        } catch (e) {
                            console.warn('重启识别失败:', e);
                        }
                    }
                };

                recognition.start();
                isRecordingVoiceForDoc.value = true;
            } catch (error) {
                alert('启动语音识别失败: ' + error.message);
            }
        };

        const stopDoctorVoiceRecording = () => {
            if (recognition && isRecordingVoiceForDoc.value) {
                isRecordingVoiceForDoc.value = false;
                recognition.stop();
                recognition = null;
                doctorRecognizedText.value = ''; // 清空临时识别结果
            }
        };


        const loadSelectedPatientMessages = async () => {
            if (!selectedPatient.value) return;
            try {
                await syncPatientMessagesFromBackend(selectedPatient.value.id, selectedPatient.value.registrationId);
                const allPatients = getPatients();
                const activePatients = allPatients.filter(p => !p.is_finished);
                patients.value = doctorDept.value
                    ? activePatients.filter(p => p.dept === doctorDept.value)
                    : activePatients;
            } catch (e) {
                console.warn('同步医患消息失败', e);
            }
        };

        const sendDoctorVoiceMessage = async () => {
            if (!doctorVoiceText.value.trim() || !selectedPatient.value) return;
            const patientId = selectedPatient.value.id;
            const registrationId = selectedPatient.value.registrationId;
            const content = doctorVoiceText.value.trim();

            console.log('📤 发送消息前，当前患者消息数:', selectedPatient.value.messages?.length || 0);

            addMessage(patientId, { type: 'doc', content });
            doctorVoiceText.value = '';

            console.log('💾 消息已保存到localStorage');

            try {
                await saveChatMessage(patientId, { type: 'doc', content }, registrationId);
                await loadSelectedPatientMessages();
            } catch (e) {
                console.warn('医生消息落库失败', e);
            }

            const allPatients = getPatients();
            const activePatients = allPatients.filter(p => !p.is_finished);
            patients.value = doctorDept.value
                ? activePatients.filter(p => p.dept === doctorDept.value)
                : activePatients;

            console.log('🔄 重新加载后，当前患者消息数:', selectedPatient.value?.messages?.length || 0);
        };

        const formatTime = (timestamp) => {
            if (!timestamp) return '';
            const date = new Date(timestamp);
            return `${date.getHours().toString().padStart(2, '0')}:${date.getMinutes().toString().padStart(2, '0')}`;
        };
        const formatEpochTime = (epoch) => {
            if (!epoch) return '';
            const date = new Date(Number(epoch));
            if (Number.isNaN(date.getTime())) return '';
            return `${date.getHours().toString().padStart(2, '0')}:${date.getMinutes().toString().padStart(2, '0')}`;
        };

        const goToRoleSelect = () => {
            window.location.href = 'index.html';
        };

        const hydrateDraftFromSelectedPatient = () => {
            medicalHistoryDraft.value = selectedPatient.value?.medicalHistory || '';
            diagnosisDraft.value = selectedPatient.value?.diagnosis || '';
        };

        const syncAndLoadPatients = async (keepSelection = true) => {
            if (doctorDept.value && typeof syncPatientsFromBackend === 'function') {
                try {
                    await syncPatientsFromBackend(doctorDept.value);
                } catch (e) {
                    console.warn('从后端同步患者失败', e);
                }
            }

            const allPatients = getPatients();
            const activePatients = allPatients.filter(p => !p.is_finished);
            patients.value = doctorDept.value
                ? activePatients.filter(p => p.dept === doctorDept.value)
                : activePatients;

            if (!patients.value.length) {
                selectedPatientId.value = '';
                return;
            }

            const hasSelectedPatient = keepSelection && patients.value.some(
                p => String(p.id) === String(selectedPatientId.value)
            );
            if (!hasSelectedPatient) {
                selectedPatientId.value = String(patients.value[0].id);
            }
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
                doctorDept.value = doctorData.department || doctorData.dept || '全科';

                view.value = 'doctor-view';
                await syncAndLoadPatients(false);
                if (patientSyncInterval) clearInterval(patientSyncInterval);
                patientSyncInterval = window.setInterval(loadPatients, 15000);
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
            if (patientSyncInterval) clearInterval(patientSyncInterval);
            patientSyncInterval = null;
            if (handsignWindow && !handsignWindow.closed) handsignWindow.close();
            if (windowCheckInterval) clearInterval(windowCheckInterval);
            isHandsignOpen.value = false;
        };

        const loadPatients = () => syncAndLoadPatients(true);

        const handleStorageChange = (e) => {
            if (e.key === 'smart_hospital_patients' || e.key === null) {
                loadPatients();
            }
        };

        watch(selectedPatientId, async () => {
            showEvidenceTimeline.value = false;
            hydrateDraftFromSelectedPatient();
            await loadSelectedPatientMessages();
        });

        onMounted(() => {
            window.addEventListener('storage', handleStorageChange);
            window.addEventListener('focus', loadPatients);
            document.addEventListener('visibilitychange', loadPatients);

            if (storedDoctor && doctorDept.value) {
                loadPatients();
                patientSyncInterval = window.setInterval(loadPatients, 15000);
                hydrateDraftFromSelectedPatient();
                loadSelectedPatientMessages();
            }
        });

        onUnmounted(() => {
            window.removeEventListener('storage', handleStorageChange);
            window.removeEventListener('focus', loadPatients);
            document.removeEventListener('visibilitychange', loadPatients);
            if (windowCheckInterval) clearInterval(windowCheckInterval);
            if (patientSyncInterval) clearInterval(patientSyncInterval);
            if (handsignWindow && !handsignWindow.closed) handsignWindow.close();
        });

        const selectPatient = (id) => {
            if (isHandsignOpen.value && handsignWindow && !handsignWindow.closed) {
                handsignWindow.close();
                isHandsignOpen.value = false;
            }
            selectedPatientId.value = String(id);
            showAddPrescription.value = false;
            newRx.value = { drug: '', dosage: '', usage: '' };
        };

        const syncSelectedPatientRecord = async ({
            successMessage = '',
            fallbackMessage = '✅ 内容已暂存本地 (后端未连接，请检查 FastAPI 服务)',
            failurePrefix = '⚠️ 本地缓存已更新，但同步数据库失败：'
        } = {}) => {
            if (!selectedPatient.value) return false;

            selectedPatient.value.medicalHistory = medicalHistoryDraft.value;
            selectedPatient.value.diagnosis = diagnosisDraft.value;
            selectedPatient.value.lastUpdated = new Date().toLocaleTimeString();
            upsertPatient(selectedPatient.value);

            try {
                const currentDoctor = getCurrentDoctor();
                const response = await fetch(`http://localhost:8000/api/patients/${selectedPatient.value.id}/sync_record`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        medical_history: medicalHistoryDraft.value || '',
                        diagnosis: diagnosisDraft.value || '',
                        prescriptions: selectedPatient.value.prescriptions || [],
                        doctor_id: currentDoctor?.id || null,
                        registration_id: selectedPatient.value.registrationId || null
                    })
                });
                const result = await response.json().catch(() => ({}));
                if (response.ok && result.success !== false) {
                    if (successMessage) alert(successMessage);
                    return true;
                }

                const detail = result.detail || result.message || `HTTP ${response.status}`;
                alert(`${failurePrefix}${detail}`);
            } catch (e) {
                console.warn('同步数据库网络异常', e);
                if (fallbackMessage) alert(fallbackMessage);
            }

            return false;
        };

        // 🌟 核心升级：点击保存时，既更新本地缓存（0.01秒UI同步），也发送到后端真实落库
        const saveMedicalHistory = async () => {
            await syncSelectedPatientRecord({
                successMessage: '✅ 病历已保存，并成功落盘同步至数据库！'
            });
        };

        const applyAiSummary = () => {
            if (!comprehensiveSummaryText.value) return;

            const current = medicalHistoryDraft.value || '';
            const summary = `【AI 综合摘要】${comprehensiveSummaryText.value}`;
            if (current.includes(summary)) return;
            medicalHistoryDraft.value = current ? `${current}\n\n${summary}` : summary;
        };

        const finishConsultation = async () => {
            if (!selectedPatient.value) return;
            if (!confirm(`确定要结束患者【${selectedPatient.value.name}】的就诊吗？结束后的患者将从待诊列表中移除。`)) return;
            const patientId = Number(selectedPatient.value.id);
            const registrationId = selectedPatient.value.registrationId;

            // Agent 记忆沉淀：触发后端对本次就诊的总结与固化
            try {
                const chatHistory = (selectedPatient.value.messages || [])
                    .filter(m => m.type === 'pat' || m.type === 'doc')
                    .sort((a, b) => a.id - b.id)
                    .map(m => `${m.type === 'doc' ? '医生' : '患者'}：${m.content}`)
                    .join('\n');

                if (chatHistory.trim() !== '') {
                    fetch('http://localhost:8000/api/agent/register/memory/consolidate', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({
                            patient_id: Number(selectedPatient.value.id),
                            chat_history: chatHistory
                        })
                    }).catch(e => console.warn('记忆沉淀网络异常', e));
                }
            } catch (e) {
                console.warn('收集聊天记录失败', e);
            }

            try {
                if (registrationId) {
                    const response = await fetch(`http://localhost:8000/api/registration/${registrationId}/state`, {
                        method: 'PATCH',
                        headers: { 'Content-Type': 'application/json' },
                        body: JSON.stringify({ current_state: 'COMPLETED' })
                    });
                    if (!response.ok) {
                        const result = await response.json().catch(() => ({}));
                        const detail = result.detail || result.message || `HTTP ${response.status}`;
                        alert(`结束就诊失败：${detail}`);
                        return;
                    }
                }
            } catch (e) {
                console.warn('更新挂号状态失败', e);
                alert('结束就诊失败：后端未能更新挂号状态');
                return;
            }

            selectedPatient.value.is_finished = true;
            selectedPatient.value.currentState = 'COMPLETED';
            upsertPatient(selectedPatient.value);
            await loadPatients();

            if (patients.value.length > 0) {
                selectPatient(patients.value[0].id);
            } else {
                selectedPatientId.value = '';
            }
        };

        const addPrescription = async () => {
            if (!newRx.value.drug || !newRx.value.dosage || !newRx.value.usage) {
                alert('请完整填写药品信息');
                return;
            }
            if (!selectedPatient.value.prescriptions) selectedPatient.value.prescriptions = [];
            selectedPatient.value.prescriptions.push({ ...newRx.value });
            newRx.value = { drug: '', dosage: '', usage: '' };
            showAddPrescription.value = false;
            await syncSelectedPatientRecord({
                successMessage: '✅ 处方已保存，并已同步到患者个人中心！',
                fallbackMessage: '✅ 处方已暂存本地，待后端恢复后再同步',
                failurePrefix: '⚠️ 处方已写入本地，但同步患者个人中心失败：'
            });
        };

        const removePrescription = async (index) => {
            selectedPatient.value.prescriptions.splice(index, 1);
            await syncSelectedPatientRecord({
                successMessage: '✅ 处方变更已同步到数据库！',
                fallbackMessage: '✅ 处方变更已暂存本地，待后端恢复后再同步',
                failurePrefix: '⚠️ 处方变更已写入本地，但同步数据库失败：'
            });
        };

        const queryTerm = (term) => {
            selectedTerm.value = term;
            termExplanation.value = termDict[term] || '暂无解释';
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
            handsignWindow = window.open(`handsign_assist.html?patientId=${selectedPatient.value.id}&doctorId=${doctorId.value}&registrationId=${selectedPatient.value.registrationId || ''}`, 'handsign_assist', features);
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

        // 🌟 新增：加载患者历史病例
        const loadHistoricalRecords = async () => {
            if (!selectedPatient.value) return;
            loadingHistory.value = true;
            try {
                const response = await fetch(`http://localhost:8000/api/patients/${selectedPatient.value.id}/records`);
                if (response.ok) {
                    historicalRecords.value = await response.json();
                } else {
                    historicalRecords.value = [];
                }
            } catch (e) {
                console.error("获取历史病例失败", e);
                historicalRecords.value = [];
            } finally {
                loadingHistory.value = false;
            }
        };

        // 🌟 新增：打开历史病例弹窗
        const openHistoryModal = async () => {
            showHistoryModal.value = true;
            await loadHistoricalRecords();
        };

        return {
            view, doctorId, doctorPwd, doctorDept, loginLoading,
            patients, selectedPatientId, selectedPatient,
            aiAssistInfo,
            coreSymptomText, dynamicSummaryText,
            comprehensiveSummaryText,
            evidenceTimeline, comprehensiveSummaryUpdatedAt,
            showEvidenceTimeline,
            voiceInput,
            selectedTerm, termExplanation,
            showAddPrescription, newRx,
            medicalHistoryDraft, diagnosisDraft,
            goToRoleSelect, doctorLogin, logout, selectPatient,
            saveMedicalHistory, finishConsultation, addPrescription, removePrescription,
            applyAiSummary, // 🌟 暴露给模板
            openPatientAssist, backToDoctor,
            queryTerm,
            isHandsignOpen,
            doctorVoiceText, doctorRecognizedText, isRecordingVoiceForDoc,
            startDoctorVoiceRecording, stopDoctorVoiceRecording,
            sendDoctorVoiceMessage,
            chatMessages, chatContainerDoc, formatTime, formatEpochTime,
            // 🌟 历史病例相关
            showHistoryModal, historicalRecords, loadingHistory, openHistoryModal
        };
    },
    template: `
        <div id="scene2-wrapper" class="pb-10">
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

            <div v-else>
                <!-- 导航栏（登录后） -->
                <nav class="bg-white shadow-sm p-4 flex justify-between items-center px-8">
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
                <div v-if="view === 'doctor-view'" class="max-w-7xl mx-auto p-6 grid grid-cols-12 gap-6 items-start">
                    
                    <!-- 左侧：待诊患者列表 (始终占 3 列) -->
                    <div class="col-span-3 bg-white rounded-2xl shadow-sm p-4 sticky top-6">
                        <h3 class="font-bold border-b pb-4 mb-4">待诊患者 ({{ patients.length }}) <span class="text-sm font-normal text-gray-500 ml-2">{{ doctorDept }}</span></h3>
                        <div v-if="patients.length === 0" class="text-gray-400 text-center py-4">暂无本科室患者</div>
                        <div v-else class="space-y-3 max-h-[calc(100vh-200px)] overflow-y-auto pr-1 scrollbar-thin">
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
                    
                    <!-- 右侧大区：如果未选中患者 -->
                    <div v-if="!selectedPatient" class="col-span-9 bg-white rounded-2xl shadow-sm p-12 flex flex-col items-center justify-center text-gray-400 h-[calc(100vh-120px)]">
                        <svg class="w-16 h-16 mb-4 text-gray-200" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M12 4.354a4 4 0 110 5.292M15 21H3v-1a6 6 0 0112 0v1zm0 0h6v-1a6 6 0 00-9-5.197M13 7a4 4 0 11-8 0 4 4 0 018 0z"></path></svg>
                        <p class="text-lg">今日待诊列表为空，或请从左侧选择一位患者开始就诊</p>
                    </div>

                    <!-- 右侧大区：已选中患者 -->
                    <div v-else class="col-span-9">
                        <!-- 顶部患者信息卡片 (始终横跨全宽) -->
                        <div class="bg-white p-6 rounded-2xl shadow-sm border-t-4 border-blue-600 transition-all duration-300 mb-6">
                            <div class="flex flex-wrap justify-between items-start gap-4 mb-4">
                                <div>
                                    <h2 class="text-xl font-bold flex items-center gap-2">
                                        当前患者：{{ selectedPatient.name }}
                                        <span v-if="isHandsignOpen" class="bg-green-100 text-green-600 px-3 py-1 rounded text-sm">手语辅助已开启</span>
                                    </h2>
                                    <p class="text-sm text-gray-500 mt-1">ID: {{ selectedPatient.id }} | 挂号科室: {{ selectedPatient.dept }}</p>
                                </div>
                                <div class="flex items-center gap-3">
                                    <button @click="openHistoryModal" class="bg-amber-50 text-amber-700 hover:bg-amber-100 px-4 py-2 rounded-xl font-medium transition shadow-sm flex items-center gap-2">
                                        <svg class="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"></path></svg>
                                        查看历史病例
                                    </button>
                                    <button v-if="selectedPatient.isDeaf || selectedPatient.is_deaf" @click="openPatientAssist" :class="isHandsignOpen ? 'bg-indigo-100 text-indigo-800' : 'bg-indigo-50 text-indigo-700 hover:bg-indigo-100'" class="flex items-center gap-2 px-4 py-2 rounded-xl font-medium transition shadow-sm">
                                        <svg class="w-5 h-5" fill="currentColor" viewBox="0 0 20 20"><path d="M10 12a2 2 0 100-4 2 2 0 000 4z"></path><path fill-rule="evenodd" d="M.458 10C1.732 5.943 5.522 3 10 3s8.268 2.943 9.542 7c-1.274 4.057-5.064 7-9.542 7S1.732 14.057.458 10zM14 10a4 4 0 11-8 0 4 4 0 018 0z" clip-rule="evenodd"></path></svg>
                                        {{ isHandsignOpen ? '诊间辅助已在辅屏打开' : '打开诊间手语辅助' }}
                                    </button>
                                    <button @click="finishConsultation" class="bg-green-600 hover:bg-green-700 text-white px-5 py-2 rounded-xl font-bold transition shadow-sm flex items-center gap-1">
                                        结束就诊 (下一位) ⏭
                                    </button>
                                </div>
                            </div>

                            <div class="grid grid-cols-1 lg:grid-cols-2 gap-4 text-sm">
                                <!-- AI 结构化主诉卡片 -->
                                <div v-if="(selectedPatient.isDeaf || selectedPatient.is_deaf) && selectedPatient.triage_struct" class="lg:col-span-2 bg-indigo-50/40 p-4 rounded-xl border border-indigo-100">
                                    <div class="flex items-center justify-between mb-2">
                                        <span class="text-gray-500 font-medium">手语 AI 智能分诊摘要：</span>
                                        <span class="text-xs bg-indigo-100 text-indigo-700 px-2 py-0.5 rounded font-bold">🤖 提前采集</span>
                                    </div>
                                    <div class="flex items-center flex-wrap gap-x-4 gap-y-2 text-base mb-2">
                                        <div class="flex items-center gap-2"><span class="text-gray-400 text-xs">核心</span><span class="font-bold text-red-600">{{ coreSymptomText || '不适' }}</span></div>
                                        <span class="text-gray-300">|</span>
                                        <div class="flex items-center gap-2"><span class="text-gray-400 text-xs">部位</span><span class="font-medium text-gray-800">{{ selectedPatient.triage_struct.body_part || '未定' }}</span></div>
                                        <span class="text-gray-300">|</span>
                                        <div class="flex items-center gap-2"><span class="text-gray-400 text-xs">病程</span><span class="font-medium text-gray-800">{{ selectedPatient.triage_struct.duration || '未定' }}</span></div>
                                        <template v-if="selectedPatient.triage_struct.severity">
                                            <span class="text-gray-300">|</span>
                                            <div class="flex items-center gap-2"><span class="text-gray-400 text-xs">程度</span><span class="font-medium text-gray-800">{{ selectedPatient.triage_struct.severity }}</span></div>
                                        </template>
                                    </div>
                                    <div v-if="selectedPatient.triage_struct.accompanying_symptoms && selectedPatient.triage_struct.accompanying_symptoms.length > 0" class="text-xs text-gray-500 mt-1">
                                        伴随症状：<span v-for="sym in selectedPatient.triage_struct.accompanying_symptoms" :key="sym" class="bg-orange-100 text-orange-700 px-2 py-0.5 rounded shadow-sm mr-2">{{ sym }}</span>
                                    </div>
                                    <div v-if="dynamicSummaryText" class="text-xs text-gray-600 mt-2 bg-white p-2.5 rounded-lg border border-indigo-100 leading-relaxed">
                                        <span class="font-medium text-gray-500 mr-1">动态摘要：</span>{{ dynamicSummaryText }}
                                    </div>
                                    
                                    <div v-if="selectedPatient.triage_struct.original_text" class="text-xs text-gray-500 mt-3 bg-white p-3 rounded-lg border border-indigo-50 leading-relaxed italic shadow-inner">
                                        <span class="font-medium text-gray-400 not-italic mr-1">参考原话：</span>"{{ selectedPatient.triage_struct.original_text }}"
                                    </div>
                                </div>

                                <!-- 基础信息 -->
                                <div class="bg-slate-50 p-3 rounded-xl"><span class="text-gray-400 block mb-1">系统既往过敏史：</span><span class="font-bold text-red-600">{{ selectedPatient.allergy || '无 / 未知' }}</span></div>
                                <div class="bg-slate-50 p-3 rounded-xl"><span class="text-gray-400 block mb-1">患者档案：</span><span class="font-medium text-gray-800">{{ selectedPatient.age ? selectedPatient.age + '岁' : '不详' }} / {{ selectedPatient.genderText || '不详' }}</span></div>
                            </div>
                        </div>

                        <div v-if="aiAssistInfo && (comprehensiveSummaryText || aiAssistInfo.doctor_context || aiAssistInfo.risk_note)" class="bg-white rounded-2xl shadow-sm border border-slate-200 mb-6 overflow-hidden">
                            <div class="flex flex-col xl:flex-row xl:items-stretch">
                                <div class="xl:w-44 shrink-0 px-5 py-4 border-b xl:border-b-0 xl:border-r border-slate-200 bg-slate-50">
                                    <div class="text-[11px] font-bold tracking-[0.18em] text-slate-500 uppercase mb-1">Clinical AI</div>
                                    <div class="text-sm font-semibold text-slate-900 leading-6">辅助提示条</div>
                                </div>
                                <div class="flex-1 px-4 py-3">
                                    <div class="flex flex-col gap-2">
                                        <div v-if="aiAssistInfo.risk_note" class="flex items-start gap-3 rounded-xl border border-orange-200 bg-orange-50 px-4 py-3">
                                            <div class="mt-0.5 text-orange-600 text-sm font-bold shrink-0">风险</div>
                                            <div class="text-sm leading-6 text-slate-800">{{ aiAssistInfo.risk_note }}</div>
                                        </div>
                                        <div class="grid grid-cols-1 2xl:grid-cols-2 gap-2">
                                            <div v-if="comprehensiveSummaryText" class="flex items-start gap-3 rounded-xl border border-indigo-100 bg-indigo-50/70 px-4 py-3">
                                                <div class="mt-0.5 text-indigo-600 text-sm font-bold shrink-0">综合摘要</div>
                                                <div class="flex-1">
                                                    <div class="text-sm leading-6 text-slate-800">{{ comprehensiveSummaryText }}</div>
                                                    <div class="mt-1 text-xs text-slate-500" v-if="comprehensiveSummaryUpdatedAt || evidenceTimeline.length">
                                                        <span v-if="comprehensiveSummaryUpdatedAt">更新时间：{{ formatEpochTime(comprehensiveSummaryUpdatedAt) }}</span>
                                                        <span v-if="comprehensiveSummaryUpdatedAt && evidenceTimeline.length"> · </span>
                                                        <span v-if="evidenceTimeline.length">证据 {{ evidenceTimeline.length }} 条</span>
                                                        <button
                                                            v-if="evidenceTimeline.length"
                                                            @click="showEvidenceTimeline = !showEvidenceTimeline"
                                                            class="ml-2 text-indigo-600 hover:text-indigo-700 underline-offset-2 hover:underline"
                                                        >
                                                            {{ showEvidenceTimeline ? '收起证据' : '查看证据' }}
                                                        </button>
                                                    </div>
                                                </div>
                                            </div>
                                            <div v-if="aiAssistInfo.doctor_context" class="flex items-start gap-3 rounded-xl border border-cyan-100 bg-cyan-50/70 px-4 py-3">
                                                <div class="mt-0.5 text-cyan-700 text-sm font-bold shrink-0">辅助</div>
                                                <div class="text-sm leading-6 text-slate-800">{{ aiAssistInfo.doctor_context }}</div>
                                            </div>
                                        </div>
                                        <div v-if="showEvidenceTimeline && evidenceTimeline.length" class="rounded-xl border border-slate-200 bg-slate-50 px-4 py-3">
                                            <div class="text-xs font-bold text-slate-600 mb-2">证据时间线（最近）</div>
                                            <div class="space-y-1.5">
                                                <div v-for="(ev, idx) in evidenceTimeline.slice(-4)" :key="idx" class="text-xs text-slate-700 flex items-center gap-2">
                                                    <span class="px-1.5 py-0.5 rounded bg-white border border-slate-200 text-slate-500 shrink-0">{{ formatEpochTime(ev.at) || '--:--' }}</span>
                                                    <span class="px-1.5 py-0.5 rounded bg-indigo-100 text-indigo-700 shrink-0">{{ ev.source }}</span>
                                                    <span class="truncate">{{ ev.text }}</span>
                                                </div>
                                            </div>
                                        </div>
                                    </div>
                                </div>
                            </div>
                        </div>

                        <div class="grid grid-cols-1 lg:grid-cols-12 gap-6 items-start">
                    
                    <!-- 主工作区：病历与处方 -->
                    <div :class="isHandsignOpen ? 'lg:col-span-7' : 'lg:col-span-12'" class="flex flex-col gap-6 transition-all duration-500">
                        
                        <!-- 病历记录区域 -->
                        <div class="bg-white p-6 rounded-2xl shadow-sm">
                            <div class="flex justify-between items-center mb-3">
                                <h3 class="font-bold text-lg flex items-center gap-2"><span class="w-1.5 h-5 bg-blue-500 rounded-full"></span>病历记录与医嘱</h3>
                                <div class="flex gap-2">
                                    <!-- 🌟 只有当 AI 提炼了新内容时，才显示这个轻量级按钮 -->
                                    <button v-if="comprehensiveSummaryText" @click="applyAiSummary" class="text-xs bg-indigo-50 text-indigo-600 border border-indigo-200 px-3 py-1.5 rounded-full hover:bg-indigo-100 font-medium flex items-center transition-colors">
                                        ✨ 一键填入 AI 摘要
                                    </button>
                                    <button @click="saveMedicalHistory" class="text-sm bg-green-50 text-green-600 px-4 py-1.5 rounded-full hover:bg-green-100 font-medium transition-colors">
                                        保存当前病历
                                    </button>
                                </div>
                            </div>
                            <textarea v-model="medicalHistoryDraft" :rows="isHandsignOpen ? 6 : 4" class="w-full border border-gray-200 rounded-xl p-3 text-sm focus:ring-2 focus:ring-blue-200 outline-none leading-relaxed transition-all resize-none"></textarea>
                            <div class="mt-4">
                                <div class="text-sm text-gray-500 mb-2">临床诊断</div>
                                <textarea v-model="diagnosisDraft" rows="2" class="w-full border border-gray-200 rounded-xl p-3 text-sm focus:ring-2 focus:ring-blue-200 outline-none leading-relaxed transition-all resize-none" placeholder="请输入明确诊断，例如：急性胃肠炎"></textarea>
                            </div>
                        </div>

                        <!-- 处方管理区域 -->
                                <div class="bg-white p-6 rounded-2xl shadow-sm relative">
                                    <div class="flex justify-between items-center mb-3">
                                        <h3 class="font-bold text-lg flex items-center gap-2"><span class="w-1.5 h-5 bg-green-500 rounded-full"></span>处方管理</h3>
                                        <button @click="showAddPrescription = true" class="text-sm bg-blue-50 text-blue-600 px-3 py-1 rounded-full hover:bg-blue-100">+ 添加药品</button>
                                    </div>
                                    
                                    <div v-if="selectedPatient.prescriptions?.length" class="space-y-2 mb-4 max-h-40 overflow-y-auto pr-1">
                                        <div v-for="(rx, index) in selectedPatient.prescriptions" :key="index" class="bg-gray-50 border border-gray-100 p-3 rounded-lg flex justify-between items-center">
                                            <div>
                                                <span class="font-bold text-gray-800">{{ rx.drug }}</span>
                                                <span class="text-sm text-gray-600 ml-3 bg-white px-2 py-0.5 rounded border">{{ rx.dosage }}</span>
                                                <span class="text-xs text-gray-500 block mt-1">{{ rx.usage }}</span>
                                            </div>
                                            <button @click="removePrescription(index)" class="text-red-400 hover:text-red-600 text-xs px-2 py-1 bg-red-50 rounded">删除</button>
                                        </div>
                                    </div>
                                    
                                    <div v-else class="text-gray-400 text-sm py-8 text-center border-2 border-dashed border-gray-200 rounded-xl mb-4 bg-gray-50/50 flex flex-col items-center justify-center">
                                        <svg class="w-8 h-8 text-gray-300 mb-2" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2"></path></svg>
                                        暂无处方记录
                                    </div>
                                    
                                    <!-- 添加表单：固定在底部 -->
                                    <div v-if="showAddPrescription" class="border-t pt-4 bg-gray-50 -mx-6 -mb-6 p-6 rounded-b-2xl">
                                        <div class="grid grid-cols-2 gap-3">
                                            <input v-model="newRx.drug" placeholder="药品名" class="border rounded-lg p-2.5 text-sm focus:ring-2 focus:ring-blue-200 outline-none bg-white">
                                            <input v-model="newRx.dosage" placeholder="剂量" class="border rounded-lg p-2.5 text-sm focus:ring-2 focus:ring-blue-200 outline-none bg-white">
                                            <input v-model="newRx.usage" placeholder="用法" class="border rounded-lg p-2.5 text-sm col-span-2 focus:ring-2 focus:ring-blue-200 outline-none bg-white">
                                        </div>
                                        <div class="flex justify-end gap-3 mt-4">
                                            <button @click="showAddPrescription = false" class="text-sm text-gray-500 px-4 py-1.5 hover:bg-gray-200 rounded-lg transition-colors">取消</button>
                                            <button @click="addPrescription" class="text-sm bg-green-600 text-white px-6 py-1.5 rounded-lg shadow-sm hover:bg-green-700 transition-colors">保存处方</button>
                                        </div>
                                    </div>
                                </div>
                            </div>

                            <!-- 专属沟通面板：仅在手语开启时展示，固定在右侧占5列 -->
                            <div v-if="isHandsignOpen" class="lg:col-span-5 bg-indigo-50/50 rounded-2xl border border-indigo-100 p-4 flex flex-col h-[calc(100vh-250px)] sticky top-6 shadow-inner">
                                <!-- 🌟 聊天记录区域：双向同频显示 -->
                                <div class="bg-white rounded-xl shadow-sm border border-indigo-50 p-4 mb-4 flex-1 flex flex-col overflow-hidden">
                                    <h3 class="font-bold text-base flex items-center gap-2 mb-3 shrink-0 text-indigo-900">
                                        <span class="w-1.5 h-4 bg-indigo-500 rounded-full"></span>实时医患交流板
                                    </h3>
                                    <div ref="chatContainerDoc" class="flex-1 overflow-y-auto pr-2 space-y-4 scrollbar-thin">
                                        <div v-if="chatMessages.length === 0" class="text-gray-400 text-center py-8 text-sm">
                                            辅屏已打开，等待沟通...
                                        </div>
                                        
                                        <div v-for="msg in chatMessages" :key="msg.id" class="flex w-full" :class="msg.type === 'doc' ? 'justify-end' : 'justify-start'">
                                            
                                            <!-- 👨‍⚕️ 医生自己发出的消息 (靠右) -->
                                            <div v-if="msg.type === 'doc'" class="flex items-start gap-2 flex-row-reverse max-w-[85%]">
                                                <div class="w-8 h-8 rounded-full bg-purple-100 flex items-center justify-center text-purple-600 font-bold shrink-0 text-xs">我</div>
                                                <div class="text-right">
                                                    <div class="bg-purple-600 text-white rounded-2xl rounded-tr-none px-4 py-2.5 shadow-sm text-sm leading-relaxed text-left inline-block">
                                                        {{ msg.content }}
                                                    </div>
                                                    <div class="text-xs text-gray-400 mt-1 mr-1">{{ formatTime(msg.created_at) }}</div>
                                                </div>
                                            </div>

                                            <!-- 👤 患者的手语消息 (靠左) -->
                                            <div v-else class="flex items-start gap-2 max-w-[85%]">
                                                <div class="w-8 h-8 rounded-full bg-blue-100 flex items-center justify-center text-blue-600 font-bold shrink-0 text-xs">患</div>
                                                <div>
                                                    <div class="bg-indigo-50 border border-indigo-100 rounded-2xl rounded-tl-none px-4 py-2.5 text-sm text-gray-800 leading-relaxed shadow-sm">
                                                        {{ msg.content }}
                                                    </div>
                                                    <div class="text-xs text-gray-400 mt-1 ml-1">{{ formatTime(msg.created_at) }}</div>
                                                </div>
                                            </div>

                                        </div>
                                    </div>
                                </div>

                                <!-- 医生输入区域 (固定在底部) -->
                                <div class="bg-white rounded-xl shadow-sm border border-purple-50 p-4 shrink-0">
                                    <h3 class="font-bold text-base flex items-center gap-2 mb-3 text-purple-900">
                                        <span class="w-1.5 h-4 bg-purple-500 rounded-full"></span>向患者发送医嘱
                                    </h3>

                                    <div v-if="doctorRecognizedText" class="mb-3 p-2 bg-blue-50 border border-blue-200 rounded-lg">
                                        <div class="text-xs text-blue-600 mb-1 font-medium flex items-center gap-1">
                                            <span class="w-2 h-2 rounded-full bg-blue-500 animate-pulse"></span>
                                            正在识别...
                                        </div>
                                        <div class="text-gray-800 text-sm">{{ doctorRecognizedText }}</div>
                                    </div>

                                    <textarea v-model="doctorVoiceText" rows="2" placeholder="输入文字或点击语音输入..." class="w-full border border-gray-200 rounded-lg p-2 text-sm focus:ring-2 focus:ring-purple-200 outline-none mb-3 resize-none"></textarea>

                                    <div class="flex items-center justify-between">
                                        <button @click="isRecordingVoiceForDoc ? stopDoctorVoiceRecording() : startDoctorVoiceRecording()"
                                                :class="isRecordingVoiceForDoc ? 'bg-red-500 text-white' : 'bg-gray-100 text-gray-700 hover:bg-gray-200'"
                                                class="px-3 py-1.5 rounded-lg text-sm flex items-center gap-1 font-medium transition-colors">
                                            <span v-if="!isRecordingVoiceForDoc">🎤 实时语音</span>
                                            <span v-else>⏹ 停止</span>
                                            <span v-if="isRecordingVoiceForDoc" class="w-2 h-2 rounded-full bg-white animate-ping ml-1"></span>
                                        </button>
                                        <button @click="sendDoctorVoiceMessage" class="bg-purple-600 text-white px-4 py-1.5 rounded-lg hover:bg-purple-700 text-sm font-medium shadow-sm transition-colors">
                                            发送至辅屏
                                        </button>
                                    </div>
                                </div>

                            </div>
                        </div>
                    </div>
                </div>
            </div>

            <!-- 🌟 历史病例查看弹窗 -->
            <div v-if="showHistoryModal" class="fixed inset-0 bg-black bg-opacity-50 flex items-center justify-center z-50" @click.self="showHistoryModal = false">
                <div class="bg-white rounded-2xl p-6 max-w-2xl w-full m-4 shadow-2xl max-h-[80vh] overflow-y-auto">
                    <div class="flex justify-between items-center mb-4 sticky top-0 bg-white pb-2 border-b">
                        <h3 class="text-xl font-bold flex items-center gap-2 text-amber-700">
                            <svg class="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"></path></svg>
                            <span>历史病例记录</span>
                        </h3>
                        <div class="text-sm text-gray-500">患者：{{ selectedPatient?.name }} (ID: {{ selectedPatient?.id }})</div>
                    </div>

                    <div v-if="loadingHistory" class="text-center py-12 text-amber-600">
                        <svg class="animate-spin h-8 w-8 mx-auto mb-2" fill="none" viewBox="0 0 24 24">
                            <circle class="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" stroke-width="4"></circle>
                            <path class="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8V0C5.373 0 0 5.373 0 12h4zm2 5.291A7.962 7.962 0 014 12H0c0 3.042 1.135 5.824 3 7.938l3-2.647z"></path>
                        </svg>
                        正在加载历史病例...
                    </div>

                    <div v-else class="space-y-4">
                        <div v-if="historicalRecords.length === 0" class="text-gray-400 text-center py-12 bg-gray-50 rounded-xl">
                            <svg class="w-16 h-16 mx-auto mb-3 text-gray-300" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.5" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"></path></svg>
                            <p class="text-lg">该患者暂无历史病例记录</p>
                        </div>

                        <!-- 动态循环渲染历史病例 -->
                        <div v-for="(record, index) in historicalRecords" :key="index" class="border-l-4 border-amber-500 bg-amber-50/50 rounded-r-xl p-4 shadow-sm hover:shadow-md transition-shadow">
                            <div class="flex justify-between items-start mb-3 pb-2 border-b border-amber-200">
                                <div>
                                    <span class="font-bold text-amber-900 text-lg">{{ record.department }} 门诊</span>
                                    <span class="ml-3 text-xs bg-amber-100 text-amber-700 px-2 py-1 rounded-full">只读</span>
                                </div>
                                <span class="text-sm text-gray-600">{{ record.visit_date }}</span>
                            </div>

                            <div class="space-y-3">
                                <div class="bg-white rounded-lg p-3 border border-amber-100">
                                    <div class="text-xs text-gray-500 mb-1 font-medium">主诉</div>
                                    <div class="text-sm text-gray-800 whitespace-pre-line">{{ record.chief_complaint || '未记录' }}</div>
                                </div>

                                <div class="bg-white rounded-lg p-3 border border-amber-100">
                                    <div class="text-xs text-gray-500 mb-1 font-medium">诊断</div>
                                    <div class="text-sm text-gray-800 font-medium">{{ record.diagnosis || '未记录' }}</div>
                                </div>

                                <div v-if="record.treatment_plan && record.treatment_plan.length > 0" class="bg-white rounded-lg p-3 border border-amber-100">
                                    <div class="text-xs text-gray-500 mb-2 font-medium">处方</div>
                                    <div class="space-y-2">
                                        <div v-for="(rx, rIndex) in record.treatment_plan" :key="rIndex" class="flex items-start gap-2 text-sm bg-green-50 p-2 rounded border border-green-100">
                                            <span class="font-bold text-green-800 min-w-[120px]">{{ rx.drug }}</span>
                                            <span class="text-gray-600">{{ rx.dosage }}</span>
                                            <span class="text-gray-500 text-xs">{{ rx.usage }}</span>
                                        </div>
                                    </div>
                                </div>

                                <div class="text-xs text-amber-700 flex items-center gap-1 mt-2">
                                    <svg class="w-4 h-4" fill="currentColor" viewBox="0 0 20 20"><path fill-rule="evenodd" d="M10 9a3 3 0 100-6 3 3 0 000 6zm-7 9a7 7 0 1114 0H3z" clip-rule="evenodd"></path></svg>
                                    接诊医生：{{ record.doctor_name }}
                                </div>
                            </div>
                        </div>
                    </div>

                    <button @click="showHistoryModal = false" class="mt-6 w-full bg-gray-200 text-gray-700 px-4 py-2.5 rounded-lg hover:bg-gray-300 font-medium transition-colors">关闭</button>
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
                <p style="color: #7f1d1d; font-size: 16px;">这通常是因为之前测试残留的数据格式损坏，或缺少某些组件导致的。</p>
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
