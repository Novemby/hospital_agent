// js/services/authService.js

const API_BASE_URL = 'http://localhost:8000/api';
const STORAGE_PATIENT = 'currentPatient';
const STORAGE_DOCTOR = 'currentDoctor';

// 1. 患者：调用后端身份证登录 API
export async function patientLoginAPI(idCard) {
    const response = await fetch(`${API_BASE_URL}/patients/by-idcard/${idCard}`);
    if (!response.ok) {
        if (response.status === 404) throw new Error('未找到该身份证档案，请前往窗口建档');
        throw new Error('网络请求异常');
    }
    return await response.json();
}

// 2. 医生：调用后端工号+密码登录 API
export async function doctorLoginAPI(doctorId, password) {
    const response = await fetch(`${API_BASE_URL}/doctors/login`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        // 【已修复】这里必须是 doctorId，否则会报 workNo is not defined
        body: JSON.stringify({ work_no: doctorId, password: password })
    });
    if (!response.ok) {
        if (response.status === 401) throw new Error('工号或密码错误');
        throw new Error('网络请求异常');
    }
    return await response.json();
}

// 3. 状态管理
export function setCurrentPatient(data) {
    if (data) sessionStorage.setItem(STORAGE_PATIENT, JSON.stringify(data));
    else sessionStorage.removeItem(STORAGE_PATIENT);
}

export function getCurrentPatient() {
    const info = sessionStorage.getItem(STORAGE_PATIENT);
    return info ? JSON.parse(info) : null;
}

export function setCurrentDoctor(data) {
    if (data) sessionStorage.setItem(STORAGE_DOCTOR, JSON.stringify(data));
    else sessionStorage.removeItem(STORAGE_DOCTOR);
}

export function getCurrentDoctor() {
    const info = sessionStorage.getItem(STORAGE_DOCTOR);
    return info ? JSON.parse(info) : null;
}

export function clearAuth() {
    sessionStorage.removeItem(STORAGE_PATIENT);
    sessionStorage.removeItem(STORAGE_DOCTOR);
}