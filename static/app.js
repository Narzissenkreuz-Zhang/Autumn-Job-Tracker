const state = { companies: [], options: { natures: [], submitted: [], stages: [] }, query: '', filter: '全部' };
const list = document.querySelector('#company-list');
const emptyState = document.querySelector('#empty-state');
const summary = document.querySelector('#summary');
const companyDialog = document.querySelector('#company-dialog');
const companyForm = document.querySelector('#company-form');
const positionDialog = document.querySelector('#position-dialog');
const positionForm = document.querySelector('#position-form');
let dragContext = null;
let editingCompanyId = null;
let editingPositionId = null;

const esc = value => String(value ?? '').replace(/[&<>'"]/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;',"'":'&#39;','"':'&quot;'}[char]));
const safeUrl = value => /^https?:\/\//i.test(value || '') ? value : '';

async function api(url, options = {}) {
  const response = await fetch(url, { headers: { 'Content-Type': 'application/json' }, ...options });
  const data = await response.json();
  if (!response.ok) throw new Error(data.error || '操作失败');
  return data;
}

function toast(message) {
  const element = document.querySelector('#toast');
  element.textContent = message;
  element.classList.add('show');
  clearTimeout(toast.timer);
  toast.timer = setTimeout(() => element.classList.remove('show'), 1800);
}

function selectOptions(values, current = '', allowEmpty = false, emptyLabel = '跟随公司') {
  const options = allowEmpty ? [''] : [];
  return [...options, ...values].map(value => `<option value="${esc(value)}" ${value === current ? 'selected' : ''}>${esc(value || emptyLabel)}</option>`).join('');
}

function stageClass(stage) {
  if (stage === 'Offer') return 'submitted';
  if (stage === '淘汰') return 'rejected';
  if (stage === '流程结束') return 'ended';
  return '';
}

function matchesFilter(company) {
  const text = [company.name, company.nature, company.stage, company.exam_time, company.interview_time,
    ...company.positions.flatMap(position => [position.base, position.department, position.title, position.status_override])].join(' ').toLowerCase();
  if (state.query && !text.includes(state.query.toLowerCase())) return false;
  if (state.filter === '全部') return true;
  if (state.filter === '未投递') return company.submitted !== '已投递';
  if (state.filter === '面试') return company.stage.includes('面试') || company.positions.some(p => p.status_override.includes('面'));
  if (state.filter === '已结束') return ['流程结束', '淘汰'].includes(company.stage);
  return company.stage.includes(state.filter) || company.positions.some(p => p.status_override.includes(state.filter));
}

function positionRows(company) {
  if (!company.positions.length) return `<div class="positions-empty">还没有岗位，点击下方按钮添加。</div>`;
  return `<table class="positions"><thead><tr><th></th><th>Base</th><th>部门 / 机构</th><th>岗位</th><th>岗位状态</th><th>笔试时间</th><th>面试时间</th><th></th></tr></thead><tbody data-company-id="${company.id}">${company.positions.map(position => {
    const link = safeUrl(position.link);
    return `<tr data-position-id="${position.id}">
      <td class="drag-cell"><button type="button" class="drag-handle position-drag-handle" draggable="true" aria-label="拖动岗位排序" title="按住拖动调整岗位顺序"><span></span><span></span><span></span></button></td>
      <td>${esc(position.base) || '<span class="inherited">未填写</span>'}</td>
      <td>${esc(position.department) || '<span class="inherited">未填写</span>'}</td>
      <td>${link ? `<a class="text-link" href="${esc(link)}" target="_blank" rel="noreferrer">${esc(position.title || '打开岗位')}</a>` : esc(position.title) || '<span class="inherited">未填写</span>'}</td>
      <td>${position.status_override ? `<span class="badge ${stageClass(position.status_override)}">${esc(position.status_override)}</span>` : `<span class="inherited">跟随公司：${esc(company.stage)}</span>`}</td>
      <td>${esc(position.exam_override) || `<span class="inherited">${company.exam_time ? `跟随公司：${esc(company.exam_time)}` : '待通知'}</span>`}</td>
      <td>${esc(position.interview_override) || `<span class="inherited">${company.interview_time ? `跟随公司：${esc(company.interview_time)}` : '待通知'}</span>`}</td>
      <td class="row-actions">
        ${link ? `<a class="button ghost small" href="${esc(link)}" target="_blank" rel="noreferrer">投递入口</a>` : ''}
        <button class="button ghost small edit-position" data-id="${position.id}" data-company="${company.id}">编辑</button>
      </td>
    </tr>`;
  }).join('')}</tbody></table>`;
}

function companyCard(company, index) {
  const website = safeUrl(company.website);
  const stage = company.stage || '待投递';
  const detail = [company.exam_time && `笔试 ${company.exam_time}`, company.interview_time && `面试 ${company.interview_time}`, company.deadline && `截止 ${company.deadline}`].filter(Boolean);
  const bases = [...new Set(company.positions.map(position => position.base?.trim()).filter(Boolean))];
  const baseSummary = `${bases.slice(0, 3).join('、')}${bases.length > 3 ? ` +${bases.length - 3}` : ''}`;
  return `<details class="company-card" data-company-id="${company.id}" ${index === 0 && state.query ? 'open' : ''}>
    <summary>
      <button type="button" class="drag-handle company-drag-handle" draggable="true" aria-label="拖动公司排序" title="按住拖动调整公司顺序；展开时不可拖动"><span></span><span></span><span></span></button>
      <div class="identity"><span class="chevron">›</span><span class="company-name">${esc(company.name)}</span>${company.nature ? `<span class="nature">${esc(company.nature)}</span>` : ''}</div>
      <div class="status-line">
        <span class="badge ${company.submitted === '已投递' ? 'submitted' : 'pending'}">${esc(company.submitted)}</span>
        <span class="badge ${stageClass(stage)}">${esc(stage)}</span>
        ${detail.map(item => `<span class="meta">${esc(item)}</span>`).join('')}
      </div>
      <div class="base-line" title="${esc(bases.join('、'))}"><span>Base</span>${esc(baseSummary) || '待补充'}</div>
      <div class="summary-actions">
        <span class="meta">${company.position_count} 个岗位</span>
        ${website ? `<a class="button ghost small" href="${esc(website)}" target="_blank" rel="noreferrer" onclick="event.stopPropagation()">投递入口</a>` : ''}
        <button class="button ghost small edit-company" data-id="${company.id}">编辑</button>
      </div>
    </summary>
    <div class="company-body">
      ${company.notes ? `<p class="company-notes">${esc(company.notes)}</p>` : ''}
      ${positionRows(company)}
      <div class="company-footer"><button class="button ghost small add-position" data-company="${company.id}">添加岗位</button></div>
    </div>
  </details>`;
}

function render() {
  const openCompanyIds = new Set(
    [...list.querySelectorAll('.company-card[open]')].map(card => Number(card.dataset.companyId))
  );
  const visible = state.companies.filter(matchesFilter);
  list.innerHTML = visible.map(companyCard).join('');
  list.querySelectorAll('.company-card').forEach(card => {
    if (openCompanyIds.has(Number(card.dataset.companyId))) card.open = true;
  });
  syncCompanySortLock();
  emptyState.classList.toggle('hidden', visible.length > 0);
  const positions = visible.reduce((total, company) => total + company.position_count, 0);
  summary.textContent = `${visible.length} 家单位 · ${positions} 个岗位`;
}

function syncCompanySortLock() {
  const locked = Boolean(list.querySelector('.company-card[open]'));
  list.classList.toggle('company-sort-locked', locked);
  list.querySelectorAll('.company-drag-handle').forEach(handle => {
    handle.disabled = locked;
    handle.draggable = !locked;
  });
}

async function reload(message = '') {
  const data = await api('/api/companies');
  state.companies = data.companies;
  state.options = data.options;
  render();
  if (message) toast(message);
}

function fillForm(form, data) {
  Object.entries(data).forEach(([key, value]) => { if (form.elements[key]) form.elements[key].value = value ?? ''; });
}

function openCompany(company = null) {
  editingCompanyId = company?.id ?? null;
  companyForm.reset();
  companyForm.elements.nature.innerHTML = selectOptions(state.options.natures, '', true, '未选择');
  companyForm.elements.submitted.innerHTML = selectOptions(state.options.submitted, company?.submitted || '未投递');
  companyForm.elements.stage.innerHTML = selectOptions(state.options.stages, company?.stage || '待投递');
  document.querySelector('#company-dialog-title').textContent = company ? '编辑单位' : '新增单位';
  document.querySelector('#delete-company').classList.toggle('hidden', !company);
  if (company) fillForm(companyForm, company);
  companyDialog.showModal();
}

function openPosition(company, position = null) {
  editingPositionId = position?.id ?? null;
  positionForm.reset();
  positionForm.elements.status_override.innerHTML = selectOptions(state.options.stages, position?.status_override || '', true);
  positionForm.elements.company_id.value = company.id;
  document.querySelector('#position-company-name').textContent = company.name;
  document.querySelector('#position-dialog-title').textContent = position ? '编辑岗位' : '添加岗位';
  document.querySelector('#delete-position').classList.toggle('hidden', !position);
  if (position) fillForm(positionForm, position);
  positionDialog.showModal();
}

document.querySelector('#add-company').addEventListener('click', () => openCompany());
document.querySelector('#search').addEventListener('input', event => { state.query = event.target.value.trim(); render(); });
document.querySelector('#filters').addEventListener('click', event => {
  const button = event.target.closest('.filter');
  if (!button) return;
  document.querySelectorAll('.filter').forEach(item => item.classList.remove('active'));
  button.classList.add('active');
  state.filter = button.dataset.filter;
  render();
});

list.addEventListener('click', event => {
  const dragHandle = event.target.closest('.drag-handle');
  const editCompany = event.target.closest('.edit-company');
  const addPosition = event.target.closest('.add-position');
  const editPosition = event.target.closest('.edit-position');
  if (dragHandle) { event.preventDefault(); event.stopPropagation(); return; }
  if (editCompany) {
    event.preventDefault(); event.stopPropagation();
    openCompany(state.companies.find(c => c.id === Number(editCompany.dataset.id)));
    return;
  }
  if (addPosition) { openPosition(state.companies.find(c => c.id === Number(addPosition.dataset.company))); return; }
  if (editPosition) {
    const company = state.companies.find(c => c.id === Number(editPosition.dataset.company));
    openPosition(company, company.positions.find(p => p.id === Number(editPosition.dataset.id)));
  }
});

list.addEventListener('toggle', event => {
  if (event.target.matches?.('.company-card')) syncCompanySortLock();
}, true);

function currentCompanyIds() {
  return [...list.querySelectorAll('.company-card')].map(card => Number(card.dataset.companyId));
}

function currentPositionIds(tbody) {
  return [...tbody.querySelectorAll('tr[data-position-id]')].map(row => Number(row.dataset.positionId));
}

list.addEventListener('dragstart', event => {
  const companyHandle = event.target.closest('.company-drag-handle');
  const positionHandle = event.target.closest('.position-drag-handle');
  if (companyHandle) {
    const card = companyHandle.closest('.company-card');
    if (list.querySelector('.company-card[open]')) {
      event.preventDefault(); toast('请先收起所有公司，再调整公司顺序'); return;
    }
    if (state.query || state.filter !== '全部') {
      event.preventDefault(); toast('请清除搜索并选择“全部”后再排序'); return;
    }
    dragContext = { type: 'company', element: card, original: currentCompanyIds().join(',') };
  } else if (positionHandle) {
    const row = positionHandle.closest('tr[data-position-id]');
    const tbody = row.closest('tbody[data-company-id]');
    dragContext = { type: 'position', element: row, parent: tbody, companyId: Number(tbody.dataset.companyId), original: currentPositionIds(tbody).join(',') };
  } else {
    event.preventDefault(); return;
  }
  dragContext.element.classList.add('dragging');
  event.dataTransfer.effectAllowed = 'move';
  event.dataTransfer.setData('text/plain', dragContext.type);
});

list.addEventListener('dragover', event => {
  if (!dragContext) return;
  if (dragContext.type === 'company') {
    const target = event.target.closest('.company-card');
    if (!target || target === dragContext.element) return;
    event.preventDefault();
    const rect = target.getBoundingClientRect();
    list.insertBefore(dragContext.element, event.clientY > rect.top + rect.height / 2 ? target.nextSibling : target);
  } else {
    const target = event.target.closest('tr[data-position-id]');
    if (!target || target === dragContext.element || target.parentElement !== dragContext.parent) return;
    event.preventDefault();
    const rect = target.getBoundingClientRect();
    dragContext.parent.insertBefore(dragContext.element, event.clientY > rect.top + rect.height / 2 ? target.nextSibling : target);
  }
});

list.addEventListener('dragend', async () => {
  if (!dragContext) return;
  const context = dragContext;
  dragContext = null;
  context.element.classList.remove('dragging');
  try {
    if (context.type === 'company') {
      const ids = currentCompanyIds();
      if (ids.join(',') === context.original) return;
      await api('/api/companies/reorder', { method: 'PUT', body: JSON.stringify({ ids }) });
      state.companies.sort((a, b) => ids.indexOf(a.id) - ids.indexOf(b.id));
      toast('公司顺序已保存');
    } else {
      const ids = currentPositionIds(context.parent);
      if (ids.join(',') === context.original) return;
      await api(`/api/companies/${context.companyId}/positions/reorder`, { method: 'PUT', body: JSON.stringify({ ids }) });
      const company = state.companies.find(item => item.id === context.companyId);
      company.positions.sort((a, b) => ids.indexOf(a.id) - ids.indexOf(b.id));
      toast('岗位顺序已保存');
    }
  } catch (error) {
    toast(error.message);
    await reload();
  }
});

document.querySelectorAll('.close-dialog').forEach(button => button.addEventListener('click', () => button.closest('dialog').close()));
[companyDialog, positionDialog].forEach(dialog => {
  let pressedOnBackdrop = false;
  dialog.addEventListener('pointerdown', event => { pressedOnBackdrop = event.target === dialog; });
  dialog.addEventListener('pointerup', event => {
    if (pressedOnBackdrop && event.target === dialog) dialog.close();
    pressedOnBackdrop = false;
  });
  dialog.addEventListener('pointercancel', () => { pressedOnBackdrop = false; });
});

companyForm.addEventListener('submit', async event => {
  event.preventDefault();
  const data = Object.fromEntries(new FormData(companyForm));
  const id = editingCompanyId; delete data.id;
  try {
    await api(id ? `/api/companies/${id}` : '/api/companies', { method: id ? 'PUT' : 'POST', body: JSON.stringify(data) });
    companyDialog.close(); await reload(id ? '单位已更新' : '单位已添加');
  } catch (error) { toast(error.message); }
});

positionForm.addEventListener('submit', async event => {
  event.preventDefault();
  const data = Object.fromEntries(new FormData(positionForm));
  const id = editingPositionId; const companyId = data.company_id; delete data.id; delete data.company_id;
  try {
    await api(id ? `/api/positions/${id}` : `/api/companies/${companyId}/positions`, { method: id ? 'PUT' : 'POST', body: JSON.stringify(data) });
    positionDialog.close(); await reload(id ? '岗位已更新' : '岗位已添加');
  } catch (error) { toast(error.message); }
});

document.querySelector('#delete-company').addEventListener('click', async () => {
  const id = editingCompanyId;
  const company = state.companies.find(c => c.id === Number(id));
  if (!company || !confirm(`确定删除“${company.name}”及其全部岗位吗？`)) return;
  await api(`/api/companies/${id}`, { method: 'DELETE' });
  companyDialog.close(); await reload('单位已删除');
});

document.querySelector('#delete-position').addEventListener('click', async () => {
  const id = editingPositionId;
  if (!id || !confirm('确定删除这个岗位吗？')) return;
  await api(`/api/positions/${id}`, { method: 'DELETE' });
  positionDialog.close(); await reload('岗位已删除');
});

reload().catch(error => { list.innerHTML = `<div class="empty"><h2>无法加载数据</h2><p>${esc(error.message)}</p></div>`; });
