// js/services/authService.js

const API_BASE_URL = 'http://localhost:8000/api';
const STORAGE_PATIENT = 'currentPatient';
const STORAGE_DOCTOR = 'currentDoctor';

// ==================== 患者相关 ====================
/**
 * 患者登录：调用后端身份证查询接口
 * @param {string} idCard - 患者身份证号
 * @returns {Promise<Object>} 患者信息
 */
export async function patientLoginAPI(idCard) {
    const response = await fetch(`${API_BASE_URL}/patients/by-idcard/${idCard}`);
    if (!response.ok) {
        if (response.status === 404) throw new Error('未找到该身份证档案，请前往窗口建档');
        throw new Error('网络请求异常');
    }
    return await response.json();
}

export function setCurrentPatient(data) {
    if (data) sessionStorage.setItem(STORAGE_PATIENT, JSON.stringify(data));
    else sessionStorage.removeItem(STORAGE_PATIENT);
}

export function getCurrentPatient() {
    const info = sessionStorage.getItem(STORAGE_PATIENT);
    return info ? JSON.parse(info) : null;
}

// ==================== 医生相关 ====================
/**
 * 医生登录：调用后端工号+密码验证接口
 * @param {string} workNo - 医生工号
 * @param {string} password - 密码
 * @returns {Promise<Object>} 医生信息（包含 id, work_no, name, department 等）
 */
export async function doctorLoginAPI(workNo, password) {
    const response = await fetch(`${API_BASE_URL}/doctors/login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ work_no: workNo, password: password })
    });
    if (!response.ok) {
        if (response.status === 401) throw new Error('工号或密码错误');
        throw new Error('网络请求异常');
    }
    return await response.json();
}

/**
 * 同步验证医生（已废弃，仅用于向后兼容，实际应使用异步 doctorLoginAPI）
 * @deprecated 请改用 doctorLoginAPI 进行异步验证
 */
export function validateDoctor(doctorId, password) {
    console.warn('validateDoctor 已废弃，请使用 doctorLoginAPI 进行异步登录');
    return false;
}

/**
 * 存储当前登录医生信息（兼容后端返回的字段，统一转换为前端所需格式）
 * @param {Object|string} doctorData - 医生对象或医生工号（若传入字符串则从本地获取完整信息，已不再支持）
 */
export function setCurrentDoctor(doctorData) {
    if (!doctorData) {
        sessionStorage.removeItem(STORAGE_DOCTOR);
        return;
    }
    // 如果传入的是对象（通常从后端返回），确保有 dept 字段（后端可能返回 department）
    if (typeof doctorData === 'object') {
        const doctorToStore = {
            ...doctorData,
            // 统一使用 dept 字段，优先使用 doctorData.dept，否则用 doctorData.department
            dept: doctorData.dept || doctorData.department,
            // 保留原始 id 字段（可能为 work_no 或 id）
            id: doctorData.work_no || doctorData.id
        };
        sessionStorage.setItem(STORAGE_DOCTOR, JSON.stringify(doctorToStore));
    } else {
        // 如果传入字符串（工号），警告并忽略（演示数据已移除）
        console.warn('setCurrentDoctor 接收字符串参数已不再支持，请直接传入后端返回的医生对象');
        sessionStorage.removeItem(STORAGE_DOCTOR);
    }
}

export function getCurrentDoctor() {
    const info = sessionStorage.getItem(STORAGE_DOCTOR);
    return info ? JSON.parse(info) : null;
}

/**
 * 获取当前登录医生的科室
 * @returns {string|null}
 */
export function getCurrentDoctorDept() {
    const doctor = getCurrentDoctor();
    return doctor ? (doctor.dept || doctor.department) : null;
}

export function clearAuth() {
    sessionStorage.removeItem(STORAGE_PATIENT);
    sessionStorage.removeItem(STORAGE_DOCTOR);
}