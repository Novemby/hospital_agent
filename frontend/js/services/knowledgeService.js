// Change window.HOSPITAL_KNOWLEDGE_API_BASE before importing to use another API port.
export async function askKnowledge(question, { signal } = {}) {
    const base = window.HOSPITAL_KNOWLEDGE_API_BASE || 'http://127.0.0.1:8000/api';
    const response = await fetch(`${base.replace(/\/$/, '')}/knowledge/chat`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ question, top_k: 3 }),
        signal,
    });
    if (!response.ok) {
        if (response.status === 503) throw new Error('知识服务暂时不可用，请检查后端配置和索引。');
        if (response.status === 422) throw new Error('请输入 1–2000 字的问题。');
        throw new Error(`请求失败（${response.status}），请稍后重试。`);
    }
    const data = await response.json();
    if (typeof data.answer !== 'string' || typeof data.status !== 'string') {
        throw new Error('服务返回格式异常，请稍后重试。');
    }
    return data;
}
