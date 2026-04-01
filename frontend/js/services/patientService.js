// 基于 localStorage 的患者数据管理
const STORAGE_KEY = 'smart_hospital_patients';
const API_BASE_URL = 'http://localhost:8000/api';

// 默认初始患者数据（仅作为后备，实际会被后端数据覆盖）
const DEFAULT_PATIENTS = [];

/**
 * 计算年龄（基于出生日期）
 */
function calculateAge(birthDateStr) {
    if (!birthDateStr) return null;
    const birth = new Date(birthDateStr);
    const today = new Date();
    let age = today.getFullYear() - birth.getFullYear();
    const m = today.getMonth() - birth.getMonth();
    if (m < 0 || (m === 0 && today.getDate() < birth.getDate())) {
        age--;
    }
    return age;
}

/**
 * 标准化患者数据：添加 age、genderText、dept 等字段
 */
function normalizePatient(patient) {
    return {
        ...patient,
        age: calculateAge(patient.birth_date),
        genderText: patient.gender === 1 ? '男' : (patient.gender === 0 ? '女' : '未知'),
        dept: patient.dept || patient.department || '',
        registrationId: patient.registration_id || patient.registrationId || null,
        currentState: patient.current_state || patient.currentState || '',
        isDeaf: patient.is_deaf,
        allergy: patient.allergy_history,
        triageStruct: patient.triage_struct || null,
        diagnosis: patient.diagnosis || '',
        prescriptions: Array.isArray(patient.prescriptions) ? patient.prescriptions : [],
        messages: Array.isArray(patient.messages) ? patient.messages : [],
        // 后端同步到医生端的列表本身就是当前待诊患者，必须清掉旧缓存里的“已结束”标记
        is_finished: false,
    };
}

function buildFreshEncounterState(patient = {}) {
    return {
        ...patient,
        medicalHistory: '',
        diagnosis: '',
        prescriptions: [],
        messages: [],
        triageStruct: patient.triageStruct || patient.triage_struct || null,
        ai_assist: null,
        aiAssist: null,
        is_finished: false,
    };
}

function normalizeMessage(message) {
    return {
        ...message,
        id: message.id ?? Date.now(),
        created_at: message.created_at ?? Date.now(),
    };
}

function messageKey(message) {
    if (message.registration_id && message.id) {
        return `db:${message.registration_id}:${message.id}`;
    }
    if (message.id) {
        return `local:${message.id}`;
    }
    return `${message.type || message.sender_type}:${message.content || ''}:${message.created_at || ''}`;
}

function mergeMessages(existingMessages = [], incomingMessages = []) {
    const mergedMap = new Map();

    [...existingMessages, ...incomingMessages]
        .filter(Boolean)
        .map(normalizeMessage)
        .forEach(message => {
            const key = messageKey(message);
            const previous = mergedMap.get(key);
            if (previous) {
                mergedMap.set(key, { ...previous, ...message });
                return;
            }

            const normalizedType = message.type || message.sender_type || '';
            const normalizedContent = (message.content || '').trim();
            const currentTime = new Date(message.created_at || 0).getTime();

            const duplicateKey = Array.from(mergedMap.keys()).find(existingKey => {
                const existingMessage = mergedMap.get(existingKey);
                const existingType = existingMessage.type || existingMessage.sender_type || '';
                const existingContent = (existingMessage.content || '').trim();
                const existingTime = new Date(existingMessage.created_at || 0).getTime();
                return (
                    existingType === normalizedType &&
                    existingContent === normalizedContent &&
                    Math.abs(existingTime - currentTime) <= 5000
                );
            });

            if (duplicateKey) {
                const duplicateMessage = mergedMap.get(duplicateKey) || {};
                mergedMap.set(duplicateKey, { ...duplicateMessage, ...message });
                return;
            }

            mergedMap.set(key, message);
        });

    return Array.from(mergedMap.values()).sort((a, b) => {
        const timeA = new Date(a.created_at || 0).getTime();
        const timeB = new Date(b.created_at || 0).getTime();
        if (timeA !== timeB) return timeA - timeB;
        return Number(a.id || 0) - Number(b.id || 0);
    });
}

// 从后端获取患者列表并更新本地存储（合并更新，保留本地数据）
async function syncPatientsFromBackend(dept = null) {
    let url = `${API_BASE_URL}/patients/`;
    if (dept) {
        url += `?dept=${encodeURIComponent(dept)}`;
    }
    const response = await fetch(url);
    if (!response.ok) {
        throw new Error(`获取患者列表失败: ${response.status}`);
    }
    const rawPatients = await response.json();
    // 获取本地现有患者数据
    const localPatients = getPatients();

    console.log('🔄 同步前本地患者数:', localPatients.length);
    console.log('📥 后端返回患者数:', rawPatients.length);

    // 创建一个Map来存储所有患者（以ID为键）
    const patientMap = new Map();

    // 先添加所有本地患者
    localPatients.forEach(p => {
        patientMap.set(p.id, p);
        if (p.messages && p.messages.length > 0) {
            console.log(`💬 患者 ${p.id} 本地有 ${p.messages.length} 条消息`);
        }
    });

    // 然后用后端数据更新，但保留本地的messages等字段
    rawPatients.forEach(backendPatient => {
        const local = patientMap.get(backendPatient.id);
        const normalized = normalizePatient(backendPatient);

        if (local) {
            const sameRegistration =
                String(local.registrationId || '') === String(normalized.registrationId || '');

            const merged = sameRegistration
                ? {
                    ...normalized,
                    messages: local.messages || [],
                    prescriptions: local.prescriptions || normalized.prescriptions,
                    diagnosis: local.diagnosis || normalized.diagnosis,
                    medicalHistory: local.medicalHistory || normalized.medicalHistory,
                    ai_assist: local.ai_assist || local.aiAssist || null,
                    aiAssist: local.aiAssist || local.ai_assist || null,
                }
                : buildFreshEncounterState(normalized);

            patientMap.set(backendPatient.id, merged);
            if (sameRegistration && merged.messages && merged.messages.length > 0) {
                console.log(`✅ 患者 ${backendPatient.id} 合并后保留了 ${merged.messages.length} 条消息`);
            } else if (!sameRegistration) {
                console.log(`🧹 患者 ${backendPatient.id} 检测到新挂号 ${normalized.registrationId}，已清空上一轮诊间缓存`);
            }
        } else {
            patientMap.set(backendPatient.id, buildFreshEncounterState(normalized));
        }
    });

    const mergedPatients = Array.from(patientMap.values());
    console.log('💾 同步后总患者数:', mergedPatients.length);
    localStorage.setItem(STORAGE_KEY, JSON.stringify(mergedPatients));
    return mergedPatients;
}

// 初始化本地数据（若本地无数据则使用默认）
function initPatients() {
    if (!localStorage.getItem(STORAGE_KEY)) {
        localStorage.setItem(STORAGE_KEY, JSON.stringify(DEFAULT_PATIENTS));
    }
}

// 获取所有患者
function getPatients() {
    initPatients();
    return JSON.parse(localStorage.getItem(STORAGE_KEY));
}

// 根据ID获取患者
function getPatientById(id) {
    const patients = getPatients();
    return patients.find(p => p.id == id) || null;
}

// 保存患者列表
function savePatients(patients) {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(patients));
}

// 添加或更新患者
function upsertPatient(patient) {
    const patients = getPatients();
    const index = patients.findIndex(p => p.id == patient.id);
    if (index >= 0) {
        const existingPatient = patients[index];
        const registrationChanged =
            String(existingPatient.registrationId || '') !== String(patient.registrationId || existingPatient.registrationId || '');

        const nextPatient = registrationChanged
            ? buildFreshEncounterState({ ...existingPatient, ...patient })
            : { ...existingPatient, ...patient };

        if (!registrationChanged && (existingPatient.messages || patient.messages)) {
            nextPatient.messages = mergeMessages(existingPatient.messages || [], patient.messages || []);
        }
        patients[index] = nextPatient;
    } else {
        const nextPatient = buildFreshEncounterState({ ...patient });
        if (nextPatient.messages) {
            nextPatient.messages = mergeMessages([], nextPatient.messages);
        }
        patients.push(nextPatient);
    }
    savePatients(patients);
}

// 添加消息到患者对话
function addMessage(patientId, message) {
    const patient = getPatientById(patientId);
    if (patient) {
        if (!patient.messages) patient.messages = [];
        patient.messages.push(normalizeMessage({
            id: Date.now(),
            created_at: Date.now(),
            ...message
        }));
        upsertPatient(patient);
    }
}

async function saveChatMessage(patientId, message, registrationId = null) {
    const response = await fetch(`${API_BASE_URL}/patients/${patientId}/messages`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
            sender_type: message.type,
            content: message.content,
            registration_id: registrationId
        })
    });
    if (!response.ok) {
        let detail = `HTTP ${response.status}`;
        try {
            const data = await response.json();
            detail = data.detail || data.message || detail;
        } catch (e) {
            console.warn('解析消息落库错误失败', e);
        }
        throw new Error(detail);
    }
    return response.json();
}

async function syncPatientMessagesFromBackend(patientId, registrationId = null) {
    const url = new URL(`${API_BASE_URL}/patients/${patientId}/messages`);
    if (registrationId) {
        url.searchParams.set('registration_id', registrationId);
    }
    const response = await fetch(url.toString());
    if (!response.ok) {
        throw new Error(`获取消息失败: ${response.status}`);
    }

    const backendMessages = await response.json();
    const patient = getPatientById(patientId);
    if (!patient) return backendMessages;

    patient.messages = mergeMessages(patient.messages || [], backendMessages);
    upsertPatient(patient);
    return patient.messages;
}

// 统一导出
export {
    getPatients,
    getPatientById,
    upsertPatient,
    addMessage,
    saveChatMessage,
    syncPatientMessagesFromBackend,
    syncPatientsFromBackend
};
