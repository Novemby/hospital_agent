// 基于 localStorage 的患者数据管理
const STORAGE_KEY = 'smart_hospital_patients';
const API_BASE_URL = 'http://localhost:8000/api';

// 默认初始患者数据（仅作为后备，实际会被后端数据覆盖）
const DEFAULT_PATIENTS = [
    {
        id: 'P001', name: '张三', isDeaf: true,
        symptoms: '左边肩膀酸疼，手抬不起，持续一周',
        dept: '骨科', allergy: '无',
        medicalHistory: '初步诊断：肩周炎，建议拍X光片。',
        prescriptions: [ { drug: '布洛芬', dosage: '200mg', usage: '每日两次' } ],
        messages: [
            { id: 1, type: 'pat', content: '医生您好，我的肩膀已经疼了一周了。' },
            { id: 2, type: 'doc', content: '是活动时疼痛加重，还是休息时也疼？' },
            { id: 3, type: 'pat', content: '抬手的时候特别疼，晚上睡觉有时也会疼醒。' }
        ]
    },
    {
        id: 'P002', name: '李四', isDeaf: false,
        symptoms: '头痛、发热两天',
        dept: '发热门诊', allergy: '青霉素',
        medicalHistory: '疑似上呼吸道感染，建议查血常规。',
        prescriptions: [ { drug: '对乙酰氨基酚', dosage: '500mg', usage: '发热时服用' } ],
        messages: [ { id: 1, type: 'pat', content: '医生我发烧38.5度，头痛。' } ]
    },
    {
        id: 'P003', name: '王五', isDeaf: false,
        symptoms: '胃胀、反酸',
        dept: '消化内科', allergy: '无',
        medicalHistory: '胃炎？建议胃镜检查。',
        prescriptions: [],
        messages: [ { id: 1, type: 'pat', content: '吃完饭总是胃胀，有点反酸。' } ]
    }
];

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
    const mergedPatients = rawPatients.map(backendPatient => {
        const local = localPatients.find(p => p.id == backendPatient.id);
        if (local) {
            // 合并：保留本地存储的病史、处方、消息等，用后端返回的基本信息覆盖
            return {
                ...local,                     // 保留所有本地字段
                ...normalizePatient(backendPatient), // 覆盖基本信息（如姓名、性别、科室等）
            };
        } else {
            return normalizePatient(backendPatient);
        }
    });
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