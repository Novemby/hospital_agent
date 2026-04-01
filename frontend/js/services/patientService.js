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
            // 合并：保留本地存储的病史、处方、消息等，用后端返回的基本信息覆盖
            const merged = {
                ...normalized,                // 后端的基本信息
                messages: local.messages || [], // 保留本地消息
                prescriptions: local.prescriptions || normalized.prescriptions, // 保留本地处方
                diagnosis: local.diagnosis || normalized.diagnosis, // 保留本地诊断
                medicalHistory: local.medicalHistory || normalized.medicalHistory, // 保留本地病史
            };
            patientMap.set(backendPatient.id, merged);
            if (merged.messages && merged.messages.length > 0) {
                console.log(`✅ 患者 ${backendPatient.id} 合并后保留了 ${merged.messages.length} 条消息`);
            }
        } else {
            patientMap.set(backendPatient.id, normalized);
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
        patients[index] = { ...patients[index], ...patient };
    } else {
        patients.push(patient);
    }
    savePatients(patients);
}

// 添加消息到患者对话
function addMessage(patientId, message) {
    const patient = getPatientById(patientId);
    if (patient) {
        if (!patient.messages) patient.messages = [];
        patient.messages.push({
            id: Date.now(),
            created_at: Date.now(),
            ...message
        });
        upsertPatient(patient);
    }
}

// 统一导出
export {
    getPatients,
    getPatientById,
    upsertPatient,
    addMessage,
    syncPatientsFromBackend
};
