// 基于 localStorage 的患者数据管理
const STORAGE_KEY = 'smart_hospital_patients';

// 默认初始患者数据
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

// 初始化数据
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
    return patients.find(p => p.id === id) || null;
}

// 保存患者列表
function savePatients(patients) {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(patients));
}

// 添加或更新患者
function upsertPatient(patient) {
    const patients = getPatients();
    const index = patients.findIndex(p => p.id === patient.id);
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

export { getPatients, getPatientById, upsertPatient, addMessage };