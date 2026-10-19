import { askKnowledge } from './services/knowledgeService.js';

const statusLabels = {
    answered: '已找到知识依据', clarify: '需要补充信息',
    insufficient: '知识不足，请核实', business_query: '需要实时业务查询',
    generation_unavailable: '生成失败 · 可查看检索原文', retrieval_unavailable: '检索暂不可用',
};

function node(tag, className, text) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text !== undefined) element.textContent = text;
    return element;
}

function sourceCard(source, cited) {
    const detail = node('details', 'knowledge-source');
    detail.append(node('summary', '', `${cited ? `[${source.source_id}] ` : ''}${source.title || '知识原文'} · ${source.section || ''}`));
    detail.append(node('p', 'knowledge-source-meta', `${source.doc_id || ''} · ${source.source_path || ''}`));
    if (source.status === 'demo_unreviewed') detail.append(node('p', 'knowledge-warning', '演示资料 · 未经医院审核'));
    detail.append(node('blockquote', '', source.quote || source.text || ''));
    if (source.quote && source.text) {
        const full = node('details', 'knowledge-full-text');
        full.append(node('summary', '', '查看完整片段'), node('p', 'knowledge-original', source.text));
        detail.append(full);
    }
    if (Array.isArray(source.source_refs) && source.source_refs.length) {
        detail.append(node('p', 'knowledge-source-meta', '文档列出的参考依据（不代表已查阅或逐句核验）：'));
        for (const ref of source.source_refs) {
            if (typeof ref !== 'string') continue;
            const line = node('p', 'knowledge-source-meta');
            if (/^https?:\/\//i.test(ref)) {
                const link = node('a', '', ref);
                link.href = ref;
                link.target = '_blank';
                link.rel = 'noopener noreferrer';
                line.append(link);
            } else line.textContent = ref;
            detail.append(line);
        }
    }
    return detail;
}

function mount(root) {
    const modal = root.dataset.mode === 'dialog';
    const panel = node(modal ? 'dialog' : 'section', 'knowledge-panel');
    panel.setAttribute('aria-label', '医院服务咨询');
    const header = node('header', 'knowledge-header');
    const title = node('div');
    title.append(node('h2', '', '医院服务咨询'), node('p', '', '科室介绍、挂号流程、检查须知与无障碍服务'));
    header.append(title);
    let trigger;
    if (modal) {
        trigger = node('button', 'knowledge-launcher', '医院服务咨询');
        trigger.type = 'button';
        trigger.setAttribute('aria-haspopup', 'dialog');
        trigger.addEventListener('click', () => { panel.showModal(); input.focus(); });
        const close = node('button', 'knowledge-close', '关闭');
        close.type = 'button';
        close.addEventListener('click', () => panel.close());
        header.append(close);
        panel.addEventListener('close', () => trigger.focus());
        root.append(trigger);
    }
    panel.append(header, node('p', 'knowledge-notice', '本咨询基于未审核演示知识，不代表真实医院服务承诺。每次提问独立处理，不提交挂号。'));
    const examples = node('div', 'knowledge-examples');
    const questions = ['手语识别错了怎么办？', '做B超需要憋尿吗？', '本院能免费借轮椅吗？'];
    for (const question of questions) {
        const button = node('button', '', question);
        button.type = 'button';
        button.addEventListener('click', () => { input.value = question; input.focus(); });
        examples.append(button);
    }
    panel.append(examples);
    const messages = node('div', 'knowledge-messages');
    messages.setAttribute('aria-label', '咨询记录');
    panel.append(messages);
    const feedback = node('p', 'knowledge-feedback', '请输入医院服务问题。');
    feedback.setAttribute('role', 'status');
    feedback.setAttribute('aria-live', 'polite');
    panel.append(feedback);
    const form = node('form', 'knowledge-form');
    const label = node('label', '', '您的问题');
    const input = node('textarea');
    input.rows = 2;
    input.maxLength = 2000;
    input.required = true;
    input.placeholder = '例如：听不清叫号怎么办？';
    label.append(input);
    const controls = node('div', 'knowledge-controls');
    const send = node('button', 'knowledge-send', '发送问题');
    send.type = 'submit';
    const cancel = node('button', 'knowledge-cancel', '取消等待');
    cancel.type = 'button';
    cancel.hidden = true;
    controls.append(send, cancel);
    form.append(label, controls);
    panel.append(form);
    root.append(panel);
    let controller = null;
    cancel.addEventListener('click', () => controller?.abort());
    if (modal) panel.addEventListener('close', () => controller?.abort());
    form.addEventListener('submit', async (event) => {
        event.preventDefault();
        const question = input.value.trim();
        if (controller) return;
        if (!question) { feedback.textContent = '请输入问题后再发送。'; input.focus(); return; }
        messages.append(node('p', 'knowledge-question', question));
        input.value = '';
        input.disabled = true;
        send.disabled = true;
        cancel.hidden = false;
        examples.querySelectorAll('button').forEach(button => { button.disabled = true; });
        feedback.textContent = '正在检索知识并生成回答…';
        controller = new AbortController();
        let timedOut = false;
        const timeout = setTimeout(() => { timedOut = true; controller?.abort(); }, 150000);
        try {
            const data = await askKnowledge(question, { signal: controller.signal });
            const reply = node('article', 'knowledge-reply');
            reply.append(node('p', 'knowledge-status', statusLabels[data.status] || '服务返回结果'));
            reply.append(node('p', 'knowledge-answer', data.answer));
            if (data.scope_notice) reply.append(node('p', 'knowledge-warning', data.scope_notice));
            const sources = Array.isArray(data.sources) ? data.sources : [];
            if (sources.length) {
                reply.append(node('h3', '', '回答引用 · 展开核对原文'));
                sources.forEach(source => reply.append(sourceCard(source, true)));
            } else if (data.status === 'generation_unavailable' && Array.isArray(data.retrieved_sources)) {
                reply.append(node('h3', '', '检索材料 · 尚未生成经过校验的回答'));
                data.retrieved_sources.forEach(source => reply.append(sourceCard(source, false)));
            }
            const total = data.timings_ms?.total;
            if (Number.isFinite(total)) reply.append(node('p', 'knowledge-source-meta', `处理耗时 ${(total / 1000).toFixed(2)} 秒`));
            messages.append(reply);
            feedback.textContent = '本次咨询完成。您可补充信息后重新提问。';
        } catch (error) {
            const text = error.name === 'AbortError'
                ? (timedOut ? '等待超时，请稍后重试。' : '已取消等待。')
                : (error instanceof TypeError ? '无法连接知识服务，请确认后端已启动并检查网络。' : error.message);
            messages.append(node('p', 'knowledge-error', text));
            feedback.textContent = text;
            input.value = question;
        } finally {
            clearTimeout(timeout);
            controller = null;
            input.disabled = false;
            send.disabled = false;
            cancel.hidden = true;
            examples.querySelectorAll('button').forEach(button => { button.disabled = false; });
            messages.scrollTop = messages.scrollHeight;
            if (!modal || panel.open) input.focus();
        }
    });
}

document.querySelectorAll('[data-knowledge-widget]').forEach(mount);
