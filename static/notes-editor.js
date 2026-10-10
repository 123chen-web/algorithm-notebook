'use strict';

/* 所见即所得编辑器适配层（OYEditor / Tiptap）。
 * - 数据库仍存 Markdown：保存时取 getMarkdown()，content 字段不变。
 * - window.OYEditor 缺失或 create 抛错时，自动回退到原有 textarea，并给出中文提示。
 * - 图片上传复用 NotesRich.uploadFile（XHR + /api/notes/attachments）。
 * - 画板归属判定复用 DrawHost.ownedIds()；互链跳转复用 NotesLinks.openLinkTarget()。
 * 本文件只用 DOM API 与 textContent 构造界面，不写行内样式，也不直接发起网络请求。 */
(function () {
  const el = (tag, classNames) => {
    const node = document.createElement(tag);
    if (classNames) {
      classNames.split(' ').filter(Boolean).forEach((name) => node.classList.add(name));
    }
    return node;
  };

  const ATTACHMENT_PREFIX = 'attachment:';
  const DRAWING_PREFIX = 'drawing:';
  const SUGGEST_LIMIT = 20;
  const PROBLEM_PICKER_LIMIT = 30;
  const FALLBACK_HINT = '纯文本输入：可用 # 标题、- 列表、**加粗**、[[笔记标题]] 等语法。';
  const RICH_HINT = '输入 / 试试，选中文字可以加粗或加链接';

  let hooks = {
    api: null,
    notify: null,
    getUser: null
  };

  let fallback = false;
  let composerInst = null;
  let activeEdit = null;
  let inserterRegistered = false;
  let pickerOpen = false;

  function refs() {
    return {
      host: document.getElementById('notes-editor-host'),
      textarea: document.getElementById('notes-content'),
      hint: document.getElementById('notes-editor-hint'),
      notice: document.getElementById('notes-editor-fallback')
    };
  }

  function configure(nextHooks) {
    hooks = { ...hooks, ...nextHooks };
    installImageRetry();
  }

  function notify(message, isError) {
    if (typeof hooks.notify === 'function') hooks.notify(message, !!isError);
  }

  function api() {
    if (typeof hooks.api === 'function') return hooks.api;
    return null;
  }

  function maxLength() {
    return (window.Notes && typeof window.Notes.NOTE_CONTENT_MAX === 'number')
      ? window.Notes.NOTE_CONTENT_MAX
      : 20000;
  }

  function currentInst() {
    return activeEdit ? activeEdit.inst : composerInst;
  }

  function isFallback() {
    return fallback;
  }

  function editorActive() {
    return !fallback && !!composerInst;
  }

  // 画板缩略图在自动保存后的短暂窗口里可能尚未生成（GET /thumb 返回 404），
  // 附件图片也可能遇到瞬时网络抖动；图片一旦加载失败浏览器不会自动重试，
  // 这里在捕获阶段统一为这两类同源图片做有限次退避重试，避免留下永久裂图。
  function installImageRetry() {
    if (window.__notesEditorImageRetry) return;
    window.__notesEditorImageRetry = true;
    var MAX_RETRY = 5;
    var DELAYS = [600, 1000, 1800, 3000, 4500];
    document.addEventListener('error', function (event) {
      var target = event.target;
      if (!target || target.nodeType !== 1 || target.tagName !== 'IMG') return;
      var src = target.getAttribute('src') || '';
      if (src.indexOf('/api/drawings/') === -1
        && src.indexOf('/api/notes/attachments/') === -1) return;
      var n = Number(target.getAttribute('data-oy-retry') || '0');
      if (!isFinite(n) || n >= MAX_RETRY) return;
      target.setAttribute('data-oy-retry', String(n + 1));
      var base = src.split('?')[0];
      window.setTimeout(function () {
        target.setAttribute('src', base + '?retry=' + (n + 1) + '&t=' + Date.now());
      }, DELAYS[n]);
    }, true);
  }

  // ---------------------------------------------------------------------------
  // 编辑器回调
  // ---------------------------------------------------------------------------

  async function uploadImage(file) {
    if (!window.NotesRich || typeof window.NotesRich.uploadFile !== 'function') {
      throw new Error('图片上传不可用');
    }
    const data = await window.NotesRich.uploadFile(file);
    const id = data && (data.id || (data.attachment && data.attachment.id));
    if (id == null) throw new Error('图片上传返回缺少附件 ID');
    const alt = (file && file.name) ? file.name : ((data && data.filename) || '');
    return { src: ATTACHMENT_PREFIX + id, alt };
  }

  // 只对属于当前用户的画板 ID 给出缩略图地址，避免向无权资源发请求。
  function resolveImageSrc(src) {
    const raw = String(src || '');
    let match = /^attachment:(\d+)$/.exec(raw);
    if (match) return `/api/notes/attachments/${match[1]}`;
    match = /^drawing:(\d+)$/.exec(raw);
    if (match) {
      const drawingId = Number(match[1]);
      let owned = null;
      try {
        owned = (window.DrawHost && typeof window.DrawHost.ownedIds === 'function')
          ? window.DrawHost.ownedIds()
          : null;
      } catch (error) {
        owned = null;
      }
      if (owned && typeof owned.has === 'function' && owned.has(drawingId)) {
        return `/api/drawings/${drawingId}/thumb`;
      }
      return '';
    }
    return raw;
  }

  async function suggestLinks(query) {
    const call = api();
    if (!call) return [];
    const q = String(query || '');
    const data = await call(`/api/notes/suggest?q=${encodeURIComponent(q)}&limit=${SUGGEST_LIMIT}`);
    const results = (data && data.results) || [];
    return results.map((item) => ({
      label: item.title,
      kind: item.kind === 'problem' ? 'problem' : 'note'
    }));
  }

  function onLinkClick(target) {
    if (!target || !target.label) return;
    const kind = target.kind === 'problem' ? 'problem' : 'note';
    if (window.NotesLinks && typeof window.NotesLinks.openLinkTarget === 'function') {
      window.NotesLinks.openLinkTarget(target.label, kind);
    }
  }

  function onRequestDrawing() {
    try {
      registerDrawingInserter();
      if (window.DrawHost && typeof window.DrawHost.createDrawing === 'function') {
        window.DrawHost.createDrawing();
      }
    } catch (error) {
      notify('暂时打不开画板，请稍后再试。', true);
    }
  }

  function onRequestProblemPicker() {
    openProblemPicker();
  }

  function onOverLimit(length, max) {
    notify(`笔记内容最多 ${max} 字，请精简后再保存。`, true);
  }

  function buildOptions(initialMarkdown) {
    return {
      markdown: initialMarkdown || '',
      placeholder: '记下你的想法或学习笔记…',
      maxLength: maxLength(),
      uploadImage,
      resolveImageSrc,
      suggestLinks,
      onLinkClick,
      onRequestDrawing,
      onRequestProblemPicker,
      onOverLimit
    };
  }

  // ---------------------------------------------------------------------------
  // 挂载 / 降级
  // ---------------------------------------------------------------------------

  function enableFallback() {
    fallback = true;
    composerInst = null;
    const r = refs();
    if (r.host) r.host.hidden = true;
    if (r.textarea) {
      r.textarea.hidden = false;
      r.textarea.maxLength = maxLength();
    }
    if (r.hint) r.hint.textContent = FALLBACK_HINT;
    if (r.notice) {
      r.notice.hidden = false;
      r.notice.textContent = '可视化编辑器加载失败，已切换为纯文本输入，不影响记录笔记。';
    }
  }

  function attachComposer() {
    const r = refs();
    if (!r.host || !r.textarea) return false;
    if (composerInst) return true;
    const OY = window.OYEditor;
    if (!OY || typeof OY.create !== 'function') {
      enableFallback();
      return false;
    }
    try {
      composerInst = OY.create(r.host, buildOptions(''));
      fallback = false;
      r.host.hidden = false;
      r.textarea.hidden = true;
      if (r.hint) r.hint.textContent = RICH_HINT;
      if (r.notice) r.notice.hidden = true;
      registerDrawingInserter();
      return true;
    } catch (error) {
      composerInst = null;
      enableFallback();
      return false;
    }
  }

  function getComposerMarkdown() {
    if (composerInst) {
      try {
        return composerInst.getMarkdown();
      } catch (error) {
        return r_textareaValue();
      }
    }
    return r_textareaValue();
  }

  function r_textareaValue() {
    const r = refs();
    return r.textarea ? r.textarea.value : '';
  }

  function setComposerMarkdown(markdown, options) {
    const text = markdown || '';
    const opts = options || {};
    if (composerInst) {
      try {
        composerInst.setMarkdown(text);
      } catch (error) {
        const r = refs();
        if (r.textarea) r.textarea.value = text;
      }
      if (opts.focus) composerInst.focus();
      return;
    }
    const r = refs();
    if (r.textarea) {
      r.textarea.value = text;
      if (opts.focus) r.textarea.focus();
    }
  }

  function focusComposer() {
    if (composerInst) {
      try { composerInst.focus(); return; } catch (error) { /* fall through */ }
    }
    const r = refs();
    if (r.textarea) r.textarea.focus();
  }

  function joinTemplate(existing, template) {
    const base = String(existing || '').replace(/\s+$/, '');
    return base ? `${base}\n\n${template}` : String(template || '');
  }

  function insertComposerTemplate(template) {
    const inst = currentInst();
    if (!inst) return false;
    inst.setMarkdown(joinTemplate(inst.getMarkdown(), template));
    inst.focus();
    return true;
  }

  // 编辑已有笔记：在卡片内容容器里挂一个编辑器实例；失败返回 null 让宿主走 textarea。
  function mountEdit(container, markdown) {
    if (fallback || !window.OYEditor || typeof window.OYEditor.create !== 'function') return null;
    const host = el('div', 'notes-edit-host');
    container.appendChild(host);
    let inst = null;
    try {
      inst = window.OYEditor.create(host, buildOptions(markdown || ''));
    } catch (error) {
      host.remove();
      return null;
    }
    activeEdit = { inst, host };
    registerDrawingInserter();
    return {
      getMarkdown() {
        try { return inst.getMarkdown(); } catch (error) { return ''; }
      },
      focus() {
        try { inst.focus(); } catch (error) { /* ignore */ }
      },
      destroy() {
        try { inst.destroy(); } catch (error) { /* 节点可能已随卡片移除 */ }
        host.remove();
        if (activeEdit && activeEdit.inst === inst) activeEdit = null;
      }
    };
  }

  // ---------------------------------------------------------------------------
  // 画板插入钩子：DrawHost 保存新画板后，把 ![标题](drawing:ID) 插进当前编辑器。
  // ---------------------------------------------------------------------------

  function registerDrawingInserter() {
    if (inserterRegistered) return;
    if (window.DrawHost && typeof window.DrawHost.setNoteInserter === 'function') {
      window.DrawHost.setNoteInserter(async (payload) => {
        const inst = currentInst();
        if (inst && payload && payload.id != null) {
          // 先主动导出并上传一次缩略图，再把画板插进编辑器，避免图片节点在
          // 缩略图落库前抢先请求 /api/drawings/ID/thumb 而产生一次 404；
          // 上传失败仍照常插入（installImageRetry 的退避重试会兜底）。
          if (typeof window.DrawHost.requestThumbUpload === 'function') {
            try { await window.DrawHost.requestThumbUpload(); } catch (error) { /* 忽略 */ }
          }
          inst.insertDrawing({ id: payload.id, title: payload.title || '' });
        }
      });
      inserterRegistered = true;
    }
  }

  // ---------------------------------------------------------------------------
  // 关联题目选择弹层（纯 DOM + 令牌样式）
  // ---------------------------------------------------------------------------

  function closeProblemPicker() {
    const node = document.getElementById('notes-problem-picker');
    if (node) {
      // id 挂在内层 dialog 上，需连带移除外层全屏背景，否则会留下遮挡层。
      const wrap = node.classList && node.classList.contains('notes-picker-backdrop')
        ? node
        : (node.closest('.notes-picker-backdrop') || node);
      wrap.remove();
    }
    pickerOpen = false;
    document.removeEventListener('keydown', onPickerKey, true);
  }

  function onPickerKey(event) {
    if (event.key === 'Escape') closeProblemPicker();
  }

  function renderPickerItems(list, status, items) {
    list.replaceChildren();
    if (!items.length) {
      status.textContent = '没有可选题目。';
      list.appendChild(status);
      return;
    }
    items.forEach((item) => {
      const label = item.title || `题目 ${item.id}`;
      const button = el('button', 'notes-picker-item');
      button.type = 'button';
      button.textContent = label;
      button.addEventListener('click', () => {
        const inst = currentInst();
        closeProblemPicker();
        if (inst) {
          inst.insertWikilink({ label, kind: 'problem' });
          inst.focus();
        }
      });
      list.appendChild(button);
    });
  }

  function openProblemPicker() {
    if (pickerOpen) return;
    const call = api();
    if (!call) {
      notify('题目列表暂时不可用。', true);
      return;
    }
    pickerOpen = true;

    const backdrop = el('div', 'notes-picker-backdrop');
    const dialog = el('div', 'notes-picker');
    dialog.id = 'notes-problem-picker';
    dialog.setAttribute('role', 'dialog');
    dialog.setAttribute('aria-modal', 'true');
    dialog.setAttribute('aria-label', '选择要关联的题目');

    const head = el('div', 'notes-picker-head');
    const title = el('h3', 'notes-picker-title');
    title.textContent = '选择要关联的题目';
    const closeButton = el('button', 'notes-picker-close');
    closeButton.type = 'button';
    closeButton.textContent = '关闭';
    closeButton.setAttribute('aria-label', '关闭');
    head.appendChild(title);
    head.appendChild(closeButton);

    const filter = el('input', 'notes-picker-filter');
    filter.type = 'search';
    filter.setAttribute('aria-label', '筛选题目');
    filter.placeholder = '输入关键字筛选';

    const list = el('div', 'notes-picker-list');
    const status = el('p', 'notes-picker-status');
    status.textContent = '正在加载题目…';
    list.appendChild(status);

    dialog.appendChild(head);
    dialog.appendChild(filter);
    dialog.appendChild(list);
    backdrop.appendChild(dialog);
    document.body.appendChild(backdrop);

    const close = () => closeProblemPicker();
    closeButton.addEventListener('click', close);
    backdrop.addEventListener('click', (event) => {
      if (event.target === backdrop) close();
    });
    document.addEventListener('keydown', onPickerKey, true);

    let allItems = [];
    Promise.resolve()
      .then(() => call(`/api/problems?limit=${PROBLEM_PICKER_LIMIT}`))
      .then((data) => {
        if (!pickerOpen) return;
        // GET /api/problems 返回 { problems: [{ id, title, ... }] }（与 notes.js 下拉同源）。
        allItems = (data && data.problems) || [];
        renderPickerItems(list, status, allItems);
        filter.focus();
      })
      .catch(() => {
        if (!pickerOpen) return;
        status.textContent = '题目加载失败，请稍后再试。';
      });

    filter.addEventListener('input', () => {
      const keyword = filter.value.trim().toLowerCase();
      const items = keyword
        ? allItems.filter((item) => String(item.title || '').toLowerCase().includes(keyword))
        : allItems;
      renderPickerItems(list, status, items);
    });
  }

  // ---------------------------------------------------------------------------
  // 生命周期
  // ---------------------------------------------------------------------------

  function autoAttach() {
    try {
      attachComposer();
    } catch (error) {
      try { enableFallback(); } catch (inner) { /* 连降级都无法完成时保持 textarea 默认态 */ }
    }
    registerDrawingInserter();
  }

  function reset() {
    const inst = composerInst;
    composerInst = null;
    if (activeEdit) {
      try { activeEdit.inst.destroy(); } catch (error) { /* ignore */ }
      activeEdit = null;
    }
    if (inst) {
      try { inst.destroy(); } catch (error) { /* ignore */ }
    }
    fallback = false;
    const r = refs();
    if (r.host) r.host.replaceChildren();
    if (r.host && r.textarea) attachComposer();
  }

  window.NotesEditor = {
    configure,
    reset,
    attachComposer,
    isFallback,
    editorActive,
    mountEdit,
    getComposerMarkdown,
    setComposerMarkdown,
    focusComposer,
    insertComposerTemplate,
    // 暴露回调便于行为测试直接驱动
    _callbacks: {
      uploadImage,
      resolveImageSrc,
      suggestLinks,
      onLinkClick,
      onRequestDrawing,
      onRequestProblemPicker,
      onOverLimit
    }
  };

  // defer 脚本在 DOM 就绪后按顺序执行；此处直接尝试挂载，失败即降级。
  autoAttach();
})();
