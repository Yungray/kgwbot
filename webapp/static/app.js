// Agit 챗봇 프론트엔드 로직
(function () {
  'use strict';

  const $ = (sel) => document.querySelector(sel);
  const $$ = (sel) => Array.from(document.querySelectorAll(sel));

  const messagesEl = $('#messages');
  const form = $('#chat-form');
  const input = $('#input');
  const sendBtn = $('#send-btn');
  const sessionBadge = $('#session-badge');
  const statusBadge = $('#status-badge');
  const resetBtn = $('#reset-btn');
  const moduleSelect = $('#lnb-module');
  const moduleSwitcherIcon = $('#lnb-module-icon');
  const moduleDot = $('#lnb-module-dot');
  const moduleIndicatorIcon = $('#module-indicator-icon');
  const moduleIndicatorLabel = $('#module-indicator-label');
  const moduleIndicatorMode = $('#module-indicator-mode');
  const modeHint = $('#mode-hint');
  const statsPanel = $('#stats-panel');
  const statsGroup = $('#stats-group');
  const statsStart = $('#stats-start');
  const statsEnd = $('#stats-end');
  const statsExcludeBot = $('#stats-exclude-bot');
  const statsRunBtn = $('#stats-run-btn');
  const welcomeState = $('#welcome-state');
  const welcomeTitle = $('#welcome-title');
  const welcomeSub = $('#welcome-sub');
  const recentQuestions = $('#recent-questions');
  const recentList = $('#recent-list');
  const imageAttachments = $('#image-attachments');
  const modelSelector = $('#model-selector');
  const modelTrigger = $('#model-trigger');
  const modelTriggerLabel = $('#model-trigger-label');
  const modelMenu = $('#model-menu');
  const userCardModelEl = document.querySelector('.user-card .user-sub');

  const MAX_ATTACHMENTS = 3;
  const MAX_IMAGE_BYTES = 5 * 1024 * 1024;
  let pendingImages = [];

  // 모듈 메타 (서버에서 주입)
  const MODULE_ICONS = window.__MODULE_ICONS__ || {};
  const MODULE_DESCS = window.__MODULE_DESCS__ || {};
  const AVAILABLE_GROUPS = window.__AVAILABLE_GROUPS__ || [];
  const STATS_GROUP_OPTIONS = window.__STATS_GROUP_OPTIONS__ || {};
  const DEFAULT_GROUP = AVAILABLE_GROUPS[0] || '';

  // 모델 카탈로그 (서버에서 주입)
  const MODELS = Array.isArray(window.__MODELS__) ? window.__MODELS__ : [];
  const MODEL_IDS = new Set(MODELS.map((m) => m.id));
  const DEFAULT_MODEL_ID = window.__DEFAULT_MODEL__ || (MODELS[0] && MODELS[0].id) || '';
  function findModel(id) {
    return MODELS.find((m) => m.id === id) || null;
  }

  // ───────── 모듈+탭 모드별 세션·메시지 저장소 ─────────
  // 세션 ID 매핑: { "전자결재::guide": "uuid", "전자결재::stats": "uuid" }
  let sessionMap = {};
  try { sessionMap = JSON.parse(localStorage.getItem('agit_sessions') || '{}'); } catch (e) {}
  function conversationKey(group, mode) {
    return `${group || DEFAULT_GROUP}::${mode || currentMode || 'guide'}`;
  }
  // 메시지 히스토리 매핑: { "모듈::모드": [{role, text, toolCalls, mode, group}] }
  const messagesMap = {};
  function loadMessages(group, mode) {
    const key = conversationKey(group, mode);
    try { return JSON.parse(localStorage.getItem(`agit_msgs_${key}`) || '[]'); }
    catch (e) { return []; }
  }
  function saveMessages(group, mode, arr) {
    const key = conversationKey(group, mode);
    try { localStorage.setItem(`agit_msgs_${key}`, JSON.stringify(arr)); }
    catch (e) { /* quota 등은 silent fail */ }
  }
  function getMessagesFor(group, mode) {
    const key = conversationKey(group, mode);
    if (!messagesMap[key]) messagesMap[key] = loadMessages(group, mode);
    return messagesMap[key];
  }
  function pushMessage(group, mode, msg) {
    const arr = getMessagesFor(group, mode);
    arr.push(msg);
    saveMessages(group, mode, arr);
  }
  function setSessionFor(group, mode, sid) {
    const key = conversationKey(group, mode);
    if (sid) sessionMap[key] = sid;
    else delete sessionMap[key];
    localStorage.setItem('agit_sessions', JSON.stringify(sessionMap));
  }
  function getSessionFor(group, mode) { return sessionMap[conversationKey(group, mode)] || null; }

  let recentQuestionMap = {};
  try { recentQuestionMap = JSON.parse(localStorage.getItem('agit_recent_questions') || '{}'); } catch (e) {}
  function saveRecentQuestions() {
    try { localStorage.setItem('agit_recent_questions', JSON.stringify(recentQuestionMap)); }
    catch (e) { /* ignore */ }
  }
  function rememberRecentQuestion(group, text) {
    const clean = (text || '').trim();
    if (!clean) return;
    const current = Array.isArray(recentQuestionMap[group]) ? recentQuestionMap[group] : [];
    recentQuestionMap[group] = [
      { text: clean, createdAt: Date.now() },
      ...current.filter((item) => item && item.text !== clean),
    ].slice(0, 5);
    saveRecentQuestions();
  }

  // 모듈 카드 상태: 진행 중, 백그라운드 완료, 마지막 대화 시각을 사이드바에 표시
  let moduleStateMap = {};
  try { moduleStateMap = JSON.parse(localStorage.getItem('agit_module_state') || '{}'); } catch (e) {}
  Object.keys(moduleStateMap).forEach((group) => {
    if (moduleStateMap[group] && moduleStateMap[group].status === 'pending') {
      moduleStateMap[group].status = 'idle';
    }
  });
  function saveModuleStates() {
    try { localStorage.setItem('agit_module_state', JSON.stringify(moduleStateMap)); }
    catch (e) { /* ignore */ }
  }
  function getModuleState(group, mode) {
    const key = conversationKey(group, mode);
    if (!moduleStateMap[key]) moduleStateMap[key] = { status: 'idle' };
    return moduleStateMap[key];
  }
  function setModuleState(group, mode, patch) {
    const key = conversationKey(group, mode);
    moduleStateMap[key] = Object.assign({}, getModuleState(group, mode), patch);
    saveModuleStates();
    renderModuleStatuses();
  }

  // 초기 모드: 대시보드 등 다른 페이지에서 /?mode=xxx 로 진입하면 그 모드를 우선,
  // 아니면 마지막으로 쓰던 모드(localStorage), 그것도 없으면 guide.
  const _navMode = window.__ACTIVE_NAV__;
  const _urlMode = ['guide', 'report', 'stats'].includes(_navMode) ? _navMode : null;
  let currentMode = _urlMode || localStorage.getItem('agit_mode') || 'guide';
  let _storedGroup = localStorage.getItem('agit_group') || '';
  let currentGroup = AVAILABLE_GROUPS.includes(_storedGroup) ? _storedGroup : DEFAULT_GROUP;
  let sessionId = getSessionFor(currentGroup, currentMode);
  // 모델 선택: localStorage → DEFAULT_MODEL_ID. 카탈로그에 없으면 default로 fallback
  let _storedModel = localStorage.getItem('agit_model') || '';
  let currentModel = MODEL_IDS.has(_storedModel) ? _storedModel : DEFAULT_MODEL_ID;
  // 초기 상태 적용은 모든 const/함수 선언 이후 (TDZ 회피) — 파일 맨 아래에서 수행

  // marked.js 설정 (XSS 방지 위해 DOMPurify 사용)
  marked.setOptions({ breaks: true, gfm: true });

  const MODE_LABELS = {
    guide: { label: '답변 가이드', icon: '💬', hint: '새 CS 문의에 대한 답변 초안을 만들어 드립니다.' },
    report: { label: 'VOC 리포트', icon: '📑', hint: '선택한 모듈의 글 현황을 통계·이슈로 정리합니다.' },
    stats: { label: '통계 리포트', icon: '📈', hint: '기간별 전체 글 수와 요청·진행·완료 건수를 집계합니다.' },
  };

  // 도구 함수명 → 사용자 친화 라벨 매핑 (chip 표시용)
  const TOOL_LABELS = {
    search_posts:           { icon: '🔍', label: '글 검색' },
    list_available_groups:  { icon: '📋', label: '그룹 목록' },
    find_similar_cases:     { icon: '🧭', label: '유사 사례 검색' },
    fetch_thread_detail:    { icon: '📜', label: '본문 상세 조회' },
    get_group_stats:        { icon: '📊', label: '그룹 현황 집계' },
    get_group_task_stats:   { icon: '📈', label: '요청 통계 집계' },
  };

  const PROGRESS_STEPS = [
    { at: 0, text: '질문을 정리하고 관련 도구를 준비하는 중입니다.' },
    { at: 4, text: '관련 글과 유사 사례를 검색하고 있습니다.' },
    { at: 10, text: '후보 사례의 맥락을 확인하고 답변을 조립하는 중입니다.' },
    { at: 18, text: '조금 더 걸리고 있어요. 결과가 도착하면 이 모듈에 표시됩니다.' },
  ];

  let loadingTimer = null;
  let loadingTimerGroup = null;

  function formatElapsed(ms, compact) {
    const seconds = Math.max(0, Math.floor(ms / 1000));
    if (seconds < 60) return compact ? `${seconds}초` : `${seconds}초 경과`;
    const minutes = Math.floor(seconds / 60);
    if (minutes < 60) return compact ? `${minutes}분` : `${minutes}분 경과`;
    const hours = Math.floor(minutes / 60);
    return compact ? `${hours}시간` : `${hours}시간 경과`;
  }

  function formatAgo(timestamp) {
    if (!timestamp) return '';
    const seconds = Math.max(1, Math.floor((Date.now() - timestamp) / 1000));
    if (seconds < 60) return '방금';
    const minutes = Math.floor(seconds / 60);
    if (minutes < 60) return `${minutes}분 전`;
    const hours = Math.floor(minutes / 60);
    if (hours < 24) return `${hours}시간 전`;
    return `${Math.floor(hours / 24)}일 전`;
  }

  function getLoadingText(elapsedSeconds) {
    let active = PROGRESS_STEPS[0];
    PROGRESS_STEPS.forEach((step) => {
      if (elapsedSeconds >= step.at) active = step;
    });
    return active.text;
  }

  function refreshLoadingProgress(group) {
    const state = getModuleState(group, currentMode);
    if (state.status !== 'pending' || !state.startedAt) return;
    const elapsedMs = Date.now() - state.startedAt;
    const elapsedSeconds = Math.floor(elapsedMs / 1000);
    const loadingStatus = $('#loading-status');
    const loadingProgress = $('#loading-progress');
    if (currentGroup === group && loadingStatus) {
      loadingStatus.textContent = `${getLoadingText(elapsedSeconds)} · ${formatElapsed(elapsedMs)} · 예상 10-20초`;
    }
    if (currentGroup === group && loadingProgress) {
      loadingProgress.style.width = Math.min(92, 12 + elapsedSeconds * 4) + '%';
    }
    if (currentGroup === group) setStatus('busy', `● 분석 중 ${formatElapsed(elapsedMs, true)}`);
    renderModuleStatuses();
  }

  function startLoadingTimer(group) {
    stopLoadingTimer();
    loadingTimerGroup = group;
    refreshLoadingProgress(group);
    loadingTimer = setInterval(() => refreshLoadingProgress(group), 1000);
  }

  function stopLoadingTimer() {
    if (loadingTimer) clearInterval(loadingTimer);
    loadingTimer = null;
    loadingTimerGroup = null;
  }

  function findPendingGroup() {
    return AVAILABLE_GROUPS.find((group) => getModuleState(group, currentMode).status === 'pending') || null;
  }

  function syncLoadingTimer() {
    const pendingGroup = findPendingGroup();
    if (!pendingGroup) {
      stopLoadingTimer();
      return;
    }
    if (loadingTimerGroup !== pendingGroup) startLoadingTimer(pendingGroup);
  }

  function renderModuleStatuses() {
    // 업무(모드) 항목: 현재 모듈의 모드별 상태 배지 — 지금 보고 있는 모드는 비워 둠
    $$('.nav-mode-status').forEach((el) => {
      const mode = el.dataset.modeStatus;
      const state = getModuleState(currentGroup, mode);
      const isViewing = (mode === currentMode);
      el.className = 'module-status nav-mode-status';
      el.textContent = '';
      el.title = '';

      if (state.status === 'pending' && state.startedAt) {
        el.classList.add('is-pending');
        el.textContent = formatElapsed(Date.now() - state.startedAt, true);
        el.title = '응답 생성 중';
      } else if (state.status === 'done' && !isViewing) {
        el.classList.add('is-done');
        el.textContent = '새 응답';
        el.title = '백그라운드 응답 완료';
      } else if (state.status === 'error' && !isViewing) {
        el.classList.add('is-error');
        el.textContent = '오류';
        el.title = '응답 실패';
      } else if (!isViewing && state.lastMessageAt) {
        el.classList.add('is-idle');
        el.textContent = formatAgo(state.lastMessageAt);
        el.title = '마지막 대화 시각';
      }
    });

    // 모듈 스위처 dot: 지금 보는 모듈이 아닌 다른 모듈에 진행중/새 응답/오류가 있으면 알림
    if (moduleDot) {
      let flag = false;
      AVAILABLE_GROUPS.forEach((g) => {
        if (g === currentGroup) return;
        ['guide', 'report', 'stats'].forEach((m) => {
          const s = getModuleState(g, m).status;
          if (s === 'pending' || s === 'done' || s === 'error') flag = true;
        });
      });
      moduleDot.hidden = !flag;
    }
  }

  function applyMode(mode, persist) {
    if (!MODE_LABELS[mode]) mode = 'guide';
    currentMode = mode;
    if (persist) localStorage.setItem('agit_mode', mode);
    $$('.nav-mode-item').forEach((b) => {
      b.classList.toggle('active', b.dataset.mode === mode);
    });
    if (modeHint) modeHint.textContent = MODE_LABELS[mode].hint;
    if (moduleIndicatorMode) moduleIndicatorMode.textContent = MODE_LABELS[mode].label;
    if (statsPanel) statsPanel.classList.toggle('is-hidden', mode !== 'stats');
    if (input) {
      input.placeholder = mode === 'stats'
        ? '추가 요청을 입력하세요... (비워두면 기본 요약 보고서 생성)'
        : '질문을 입력하세요... (Shift+Enter: 줄바꿈, Enter: 전송)';
    }
    renderCurrentConversation();
  }

  function formatDateInput(date) {
    const yyyy = date.getFullYear();
    const mm = String(date.getMonth() + 1).padStart(2, '0');
    const dd = String(date.getDate()).padStart(2, '0');
    return `${yyyy}-${mm}-${dd}`;
  }

  function initStatsDates() {
    if (!statsStart || !statsEnd) return;
    if (statsStart.value && statsEnd.value) return;
    const end = new Date();
    const start = new Date();
    start.setDate(end.getDate() - 30);
    statsStart.value = statsStart.value || formatDateInput(start);
    statsEnd.value = statsEnd.value || formatDateInput(end);
  }

  function renderStatsGroupOptions(group) {
    if (!statsGroup) return;
    const previous = statsGroup.value || localStorage.getItem(`agit_stats_group_${group}`) || '';
    const options = Array.isArray(STATS_GROUP_OPTIONS[group]) ? STATS_GROUP_OPTIONS[group] : [];
    statsGroup.innerHTML = '';

    const all = document.createElement('option');
    all.value = '';
    all.textContent = '모듈 전체';
    statsGroup.appendChild(all);

    options.forEach((item) => {
      const opt = document.createElement('option');
      opt.value = item.id || '';
      opt.textContent = item.title ? `${item.title} (${item.id})` : item.id;
      statsGroup.appendChild(opt);
    });

    const values = new Set(Array.from(statsGroup.options).map((opt) => opt.value));
    statsGroup.value = values.has(previous) ? previous : '';
  }

  function buildStatsPrompt(extraText) {
    initStatsDates();
    renderStatsGroupOptions(currentGroup);
    const start = statsStart ? statsStart.value : '';
    const end = statsEnd ? statsEnd.value : '';
    const excludeBot = !!(statsExcludeBot && statsExcludeBot.checked);
    const selectedOption = statsGroup && statsGroup.selectedOptions.length
      ? statsGroup.selectedOptions[0]
      : null;
    const selectedGroupId = statsGroup ? statsGroup.value : '';
    const selectedGroupLabel = selectedOption ? selectedOption.textContent : '모듈 전체';
    const note = (extraText || '').trim();
    return [
      `통계 리포트를 생성해줘.`,
      `- 조회 그룹: ${currentGroup}`,
      `- 조회 대상: ${selectedGroupLabel}`,
      `- 조회 대상 그룹 ID: ${selectedGroupId || '모듈 전체'}`,
      `- 조회 기간: ${start}부터 ${end}까지`,
      `- 집계 항목: 전체 작성 글 수, 요청 건수, 진행 건수, 완료 건수, 승인 건수, 요청 아님/기타`,
      `- 봇 작성 글 제외: ${excludeBot ? '예' : '아니오'}`,
      `- 기준: Agit task_status 기준으로 정확한 숫자를 계산`,
      note ? `- 추가 요청: ${note}` : `- 추가 요청: 핵심 지표 표와 짧은 해석, 운영팀 참고 사항을 포함`,
    ].join('\n');
  }

  function applyModel(modelId, persist) {
    if (!MODEL_IDS.has(modelId)) modelId = DEFAULT_MODEL_ID;
    currentModel = modelId;
    if (persist) localStorage.setItem('agit_model', modelId);
    const meta = findModel(modelId);
    const labelText = meta ? meta.label : modelId;
    if (modelTriggerLabel) modelTriggerLabel.textContent = labelText;
    if (modelTrigger) modelTrigger.title = meta ? `${meta.label}\n${meta.id}\n${meta.desc}` : modelId;
    if (userCardModelEl) userCardModelEl.textContent = labelText;
    // 메뉴 옵션 active 상태
    $$('.model-option').forEach((opt) => {
      const active = opt.dataset.model === modelId;
      opt.classList.toggle('active', active);
      opt.setAttribute('aria-selected', active ? 'true' : 'false');
    });
  }

  function toggleModelMenu(open) {
    if (!modelMenu || !modelTrigger) return;
    const shouldOpen = open !== undefined ? open : modelMenu.classList.contains('is-hidden');
    modelMenu.classList.toggle('is-hidden', !shouldOpen);
    modelTrigger.setAttribute('aria-expanded', shouldOpen ? 'true' : 'false');
  }

  function applyModule(group, persist) {
    if (!group || !AVAILABLE_GROUPS.includes(group)) group = DEFAULT_GROUP;
    const prevGroup = currentGroup;
    const isSwitch = (prevGroup !== group);
    currentGroup = group;
    if (persist) localStorage.setItem('agit_group', currentGroup);

    // LNB 전역 모듈 스위처 동기화
    const icon = MODULE_ICONS[currentGroup] || '📁';
    if (moduleSelect && moduleSelect.value !== currentGroup) moduleSelect.value = currentGroup;
    if (moduleSwitcherIcon) moduleSwitcherIcon.textContent = icon;
    // topbar 모듈 인디케이터
    if (moduleIndicatorIcon) moduleIndicatorIcon.textContent = icon;
    if (moduleIndicatorLabel) moduleIndicatorLabel.textContent = currentGroup || '모듈을 선택하세요';
    // 환영 카드 문구
    if (welcomeTitle) welcomeTitle.textContent = `${icon} ${currentGroup} 모듈`;
    if (welcomeSub) {
      welcomeSub.textContent =
        (MODULE_DESCS[currentGroup] ? MODULE_DESCS[currentGroup] + ' · ' : '') +
        '아래 추천 액션 중 하나를 선택하거나, 직접 질문을 입력하세요.';
    }
    renderStatsGroupOptions(currentGroup);

    if (isSwitch || messagesEl.childElementCount === 0) {
      renderCurrentConversation();
    }
    if (getModuleState(currentGroup, currentMode).status === 'done') {
      setModuleState(currentGroup, currentMode, { status: 'idle' });
    } else {
      renderModuleStatuses();
    }
    renderRecentQuestions();
  }

  function renderCurrentConversation() {
    if (!messagesEl) return;
    sessionId = getSessionFor(currentGroup, currentMode);
    updateSessionBadge();
    messagesEl.innerHTML = '';
    const cached = getMessagesFor(currentGroup, currentMode);
    cached.forEach((m) => {
      if (m.role === 'user') appendUserMessage(m.text);
      else appendBotMessage(m.text, m.toolCalls, m);
    });
    const isPending = getModuleState(currentGroup, currentMode).status === 'pending';
    if (isPending) {
      showLoading(currentGroup);
      startLoadingTimer(currentGroup);
    } else {
      syncLoadingTimer();
      setStatus('ok', '● 정상');
    }
    if (cached.length === 0 && !isPending) showWelcomeState();
    else hideWelcomeState();
  }

  // 마지막 실패 메시지 재시도 — 에러 봇 버블 + 직전 사용자 버블 제거 후 재전송
  function retryLastFailed(originalMsg) {
    const msgs = getMessagesFor(currentGroup, currentMode);
    if (msgs.length && msgs[msgs.length - 1].role === 'bot' && msgs[msgs.length - 1].isError) {
      msgs.pop();
    }
    if (msgs.length && msgs[msgs.length - 1].role === 'user' && msgs[msgs.length - 1].text === originalMsg) {
      msgs.pop();
    }
    saveMessages(currentGroup, currentMode, msgs);
    messagesEl.innerHTML = '';
    msgs.forEach((m) => {
      if (m.role === 'user') appendUserMessage(m.text);
      else appendBotMessage(m.text, m.toolCalls, m);
    });
    if (msgs.length === 0) showWelcomeState();
    sendMessage(originalMsg);
  }

  function showWelcomeState() {
    if (welcomeState) welcomeState.classList.remove('is-hidden');
    renderRecentQuestions();
  }
  function hideWelcomeState() {
    if (welcomeState) welcomeState.classList.add('is-hidden');
  }

  // session-badge UI는 제거됨 — 호환을 위해 noop으로 유지 (호출부 변경 최소화)
  function updateSessionBadge() {
    if (!sessionBadge) return;
    if (sessionId) {
      sessionBadge.textContent = `세션 ${sessionId.slice(0, 8)}`;
      sessionBadge.classList.remove('badge-gray');
      sessionBadge.classList.add('badge-indigo');
    } else {
      sessionBadge.textContent = '새 세션';
      sessionBadge.classList.remove('badge-indigo');
      sessionBadge.classList.add('badge-gray');
    }
  }

  function renderMarkdown(text) {
    const raw = marked.parse(text || '');
    return DOMPurify.sanitize(raw, {
      ADD_ATTR: ['target', 'rel'],
    });
  }

  async function copyTextToClipboard(text) {
    if (navigator.clipboard && window.isSecureContext) {
      await navigator.clipboard.writeText(text);
      return;
    }

    const textarea = document.createElement('textarea');
    textarea.value = text;
    textarea.setAttribute('readonly', '');
    textarea.style.position = 'fixed';
    textarea.style.top = '-9999px';
    document.body.appendChild(textarea);
    textarea.select();
    document.execCommand('copy');
    textarea.remove();
  }

  function setCopyButtonState(btn, state) {
    const labels = {
      idle: '복사',
      copied: '복사됨',
      failed: '실패',
    };
    btn.textContent = labels[state] || labels.idle;
    btn.classList.toggle('is-copied', state === 'copied');
    btn.classList.toggle('is-failed', state === 'failed');
  }

  function enhanceCopyableBlocks(content) {
    content.querySelectorAll('pre').forEach((pre) => {
      if (pre.parentElement && pre.parentElement.classList.contains('copyable-block')) return;

      const wrapper = document.createElement('div');
      wrapper.className = 'copyable-block';
      pre.parentNode.insertBefore(wrapper, pre);
      wrapper.appendChild(pre);

      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'copy-block-btn';
      btn.setAttribute('aria-label', '코드 블록 복사');
      setCopyButtonState(btn, 'idle');
      btn.addEventListener('click', async () => {
        try {
          await copyTextToClipboard(pre.innerText.trimEnd());
          setCopyButtonState(btn, 'copied');
          setTimeout(() => setCopyButtonState(btn, 'idle'), 1400);
        } catch (e) {
          setCopyButtonState(btn, 'failed');
          setTimeout(() => setCopyButtonState(btn, 'idle'), 1400);
        }
      });
      wrapper.appendChild(btn);
    });
  }

  function appendUserMessage(text) {
    const tmpl = $('#message-user-tmpl').content.cloneNode(true);
    tmpl.querySelector('.message-content').textContent = text;
    messagesEl.appendChild(tmpl);
    scrollToBottom();
  }

  function getRecentUserQuestions(group) {
    const seen = new Set();
    const stored = Array.isArray(recentQuestionMap[group]) ? recentQuestionMap[group] : [];
    const fromMessages = getMessagesFor(group, currentMode)
      .filter((m) => m.role === 'user' && m.text && m.text.trim())
      .slice()
      .reverse();
    return stored.concat(fromMessages)
      .filter((m) => {
        const key = m.text.trim();
        if (seen.has(key)) return false;
        seen.add(key);
        return true;
      })
      .slice(0, 3);
  }

  function renderRecentQuestions() {
    if (!recentQuestions || !recentList) return;
    const recent = getRecentUserQuestions(currentGroup);
    recentList.innerHTML = '';
    if (recent.length === 0) {
      recentQuestions.classList.add('is-hidden');
      return;
    }
    recent.forEach((m) => {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = 'recent-question';
      btn.textContent = m.text.trim();
      btn.title = m.text.trim();
      btn.addEventListener('click', () => {
        input.value = m.text;
        autoGrowInput();
        input.focus();
        input.setSelectionRange(input.value.length, input.value.length);
      });
      recentList.appendChild(btn);
    });
    recentQuestions.classList.remove('is-hidden');
  }

  function appendBotMessage(text, toolCalls, opts) {
    opts = opts || {};
    const tmpl = $('#message-bot-tmpl').content.cloneNode(true);
    const toolsEl = tmpl.querySelector('.message-tools');
    if (toolCalls && toolCalls.length > 0) {
      toolCalls.forEach((tc) => {
        const meta = TOOL_LABELS[tc.name] || { icon: '🔧', label: tc.name };
        const chip = document.createElement('span');
        chip.className = 'tool-chip';
        chip.textContent = `${meta.icon} ${meta.label}`;
        chip.title = `${tc.name}\n${JSON.stringify(tc.args || {}, null, 2)}`;
        toolsEl.appendChild(chip);
      });
    } else {
      toolsEl.remove();
    }
    const content = tmpl.querySelector('.message-content');
    content.innerHTML = renderMarkdown(text || '*(응답이 비어있습니다)*');
    enhanceCopyableBlocks(content);
    // 모든 링크에 target=_blank 추가
    content.querySelectorAll('a').forEach((a) => {
      a.target = '_blank';
      a.rel = 'noopener noreferrer';
    });

    const actionsEl = tmpl.querySelector('.message-actions');
    let hasAction = false;
    // 에러 → 재시도 버튼
    if (opts.isError && opts.retryMessage) {
      const retryBtn = document.createElement('button');
      retryBtn.type = 'button';
      retryBtn.className = 'retry-btn';
      retryBtn.innerHTML = '↻ 다시 시도';
      retryBtn.addEventListener('click', () => retryLastFailed(opts.retryMessage));
      actionsEl.appendChild(retryBtn);
      hasAction = true;
    }
    // VOC 리포트 모드 + 본문 있을 때 다운로드 버튼
    if (opts.mode === 'report' && text && text.trim() && !opts.isError) {
      const dlBtn = document.createElement('button');
      dlBtn.type = 'button';
      dlBtn.className = 'download-btn';
      dlBtn.innerHTML = '<span>📥</span> HTML 리포트 다운로드';
      dlBtn.addEventListener('click', () => downloadReport(text, opts, dlBtn));
      actionsEl.appendChild(dlBtn);
      hasAction = true;
    }
    if (!hasAction) actionsEl.remove();

    messagesEl.appendChild(tmpl);
    scrollToBottom();
  }

  // 스트리밍용 빈 봇 버블 생성 → delta마다 평문 갱신, done 시 마크다운+툴칩+액션으로 finalize.
  function createStreamingBot() {
    const tmpl = $('#message-bot-tmpl').content.cloneNode(true);
    const t = tmpl.querySelector('.message-tools'); if (t) t.remove();
    const a = tmpl.querySelector('.message-actions'); if (a) a.remove();
    const c = tmpl.querySelector('.message-content');
    c.classList.add('streaming');
    c.textContent = '';
    messagesEl.appendChild(tmpl);
    const msgEl = messagesEl.lastElementChild;
    scrollToBottom();
    return {
      msgEl,
      contentEl: msgEl.querySelector('.message-content'),
      setText(text) { this.contentEl.textContent = text; scrollToBottom(); },
      finalize(fullText, toolCalls, opts) {
        opts = opts || {};
        const body = this.msgEl.querySelector('.message-body');
        this.contentEl.classList.remove('streaming');
        this.contentEl.innerHTML = renderMarkdown(fullText || '*(응답이 비어있습니다)*');
        enhanceCopyableBlocks(this.contentEl);
        this.contentEl.querySelectorAll('a').forEach((el) => { el.target = '_blank'; el.rel = 'noopener noreferrer'; });
        if (toolCalls && toolCalls.length > 0) {
          const toolsEl = document.createElement('div');
          toolsEl.className = 'message-tools';
          toolCalls.forEach((tc) => {
            const meta = TOOL_LABELS[tc.name] || { icon: '🔧', label: tc.name };
            const chip = document.createElement('span');
            chip.className = 'tool-chip';
            chip.textContent = `${meta.icon} ${meta.label}`;
            chip.title = `${tc.name}\n${JSON.stringify(tc.args || {}, null, 2)}`;
            toolsEl.appendChild(chip);
          });
          body.insertBefore(toolsEl, this.contentEl);
        }
        if (opts.mode === 'report' && fullText && fullText.trim()) {
          const actionsEl = document.createElement('div');
          actionsEl.className = 'message-actions';
          const dlBtn = document.createElement('button');
          dlBtn.type = 'button';
          dlBtn.className = 'download-btn';
          dlBtn.innerHTML = '<span>📥</span> HTML 리포트 다운로드';
          dlBtn.addEventListener('click', () => downloadReport(fullText, opts, dlBtn));
          actionsEl.appendChild(dlBtn);
          body.appendChild(actionsEl);
        }
        scrollToBottom();
      },
    };
  }

  async function downloadReport(markdown, opts, btn) {
    const groupLabel = opts.group || '전체';
    const title = `VOC 리포트 — ${groupLabel}`;
    const today = new Date().toISOString().slice(0, 10);
    const subtitle = `대상 그룹: ${groupLabel} · 생성일: ${today}`;
    if (btn) {
      btn.disabled = true;
      btn.dataset.originalText = btn.innerHTML;
      btn.innerHTML = '⏳ 생성 중…';
    }
    try {
      const res = await fetch('/api/report/html', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ markdown, title, subtitle }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: res.statusText }));
        alert('리포트 생성 실패: ' + (err.detail || res.statusText));
        return;
      }
      const blob = await res.blob();
      const cd = res.headers.get('Content-Disposition') || '';
      const m = cd.match(/filename="([^"]+)"/);
      const filename = m ? m[1] : `voc-report-${today}.html`;
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = filename;
      document.body.appendChild(a);
      a.click();
      setTimeout(() => {
        URL.revokeObjectURL(url);
        a.remove();
      }, 200);
    } catch (e) {
      alert('네트워크 오류: ' + e.message);
    } finally {
      if (btn) {
        btn.disabled = false;
        btn.innerHTML = btn.dataset.originalText || '<span>📥</span> HTML 리포트 다운로드';
      }
    }
  }

  function showLoading(group) {
    hideLoading();
    const tmpl = $('#message-loading-tmpl').content.cloneNode(true);
    const wrapper = document.createElement('div');
    wrapper.id = 'loading-message';
    wrapper.appendChild(tmpl);
    messagesEl.appendChild(wrapper);
    scrollToBottom();
    if (group) refreshLoadingProgress(group);
  }

  function hideLoading() {
    const el = $('#loading-message');
    if (el) el.remove();
  }

  function scrollToBottom() {
    requestAnimationFrame(() => {
      messagesEl.scrollTop = messagesEl.scrollHeight;
    });
  }

  function setStatus(state, label) {
    statusBadge.classList.remove('badge-success', 'badge-warning', 'badge-error');
    if (state === 'ok') statusBadge.classList.add('badge-success');
    else if (state === 'busy') statusBadge.classList.add('badge-warning');
    else statusBadge.classList.add('badge-error');
    statusBadge.textContent = label;
  }

  function bytesFromDataUrl(dataUrl) {
    const comma = dataUrl.indexOf(',');
    if (comma < 0) return 0;
    return Math.floor((dataUrl.length - comma - 1) * 0.75);
  }

  function renderImageAttachments() {
    if (!imageAttachments) return;
    imageAttachments.innerHTML = '';
    if (pendingImages.length === 0) {
      imageAttachments.classList.add('is-hidden');
      return;
    }
    pendingImages.forEach((img, index) => {
      const item = document.createElement('div');
      item.className = 'image-attachment';

      const thumb = document.createElement('img');
      thumb.src = img.dataUrl;
      thumb.alt = img.name || `첨부 이미지 ${index + 1}`;
      item.appendChild(thumb);

      const meta = document.createElement('div');
      meta.className = 'image-attachment-meta';
      meta.textContent = img.name || `캡처 이미지 ${index + 1}`;
      item.appendChild(meta);

      const remove = document.createElement('button');
      remove.type = 'button';
      remove.className = 'image-attachment-remove';
      remove.setAttribute('aria-label', '첨부 이미지 제거');
      remove.textContent = '×';
      remove.addEventListener('click', () => {
        pendingImages.splice(index, 1);
        renderImageAttachments();
        input.focus();
      });
      item.appendChild(remove);

      imageAttachments.appendChild(item);
    });
    imageAttachments.classList.remove('is-hidden');
  }

  function fileToDataUrl(file) {
    return new Promise((resolve, reject) => {
      const reader = new FileReader();
      reader.onload = () => resolve(reader.result);
      reader.onerror = () => reject(reader.error || new Error('이미지를 읽지 못했습니다.'));
      reader.readAsDataURL(file);
    });
  }

  async function addImageFile(file) {
    if (!file || !file.type || !file.type.startsWith('image/')) return false;
    if (pendingImages.length >= MAX_ATTACHMENTS) {
      alert(`이미지는 최대 ${MAX_ATTACHMENTS}개까지 첨부할 수 있습니다.`);
      return true;
    }
    if (file.size > MAX_IMAGE_BYTES) {
      alert('이미지는 5MB 이하만 첨부할 수 있습니다.');
      return true;
    }
    const dataUrl = await fileToDataUrl(file);
    pendingImages.push({
      name: file.name || `clipboard-image-${pendingImages.length + 1}.png`,
      mimeType: file.type || 'image/png',
      dataUrl,
    });
    renderImageAttachments();
    return true;
  }

  async function sendMessage(msg) {
    const imagesToSend = pendingImages.slice();
    if (currentMode === 'stats') {
      msg = buildStatsPrompt(msg);
    }
    if (!msg.trim() && imagesToSend.length === 0) return;

    // 전송 시점의 모듈을 lock (응답 도착 전에 모듈 바뀌어도 결과는 원래 모듈에 귀속)
    const targetGroup = currentGroup;
    const targetMode = currentMode;
    const displayMsg = msg.trim() || '첨부 이미지 분석 요청';
    const displayWithImages = imagesToSend.length > 0
      ? `${displayMsg}\n\n[첨부 이미지 ${imagesToSend.length}개]`
      : displayMsg;

    hideWelcomeState();
    appendUserMessage(displayWithImages);
    const now = Date.now();
    pushMessage(targetGroup, targetMode, { role: 'user', text: displayWithImages, mode: targetMode, group: targetGroup, createdAt: now });
    rememberRecentQuestion(targetGroup, displayMsg);
    setModuleState(targetGroup, targetMode, { status: 'pending', startedAt: now, lastMessageAt: now });
    input.value = '';
    pendingImages = [];
    renderImageAttachments();
    autoGrowInput();
    sendBtn.disabled = true;
    setStatus('busy', '● 분석 중');
    showLoading(targetGroup);
    startLoadingTimer(targetGroup);

    const viewMatches = () => currentGroup === targetGroup && currentMode === targetMode;
    try {
      const res = await fetch('/api/chat/stream', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          message: msg,
          session_id: getSessionFor(targetGroup, targetMode),
          mode: targetMode,
          group: targetGroup || null,
          model: currentModel || null,
          images: imagesToSend.map((img) => ({
            name: img.name,
            mime_type: img.mimeType,
            data: img.dataUrl.split(',')[1] || '',
            size: bytesFromDataUrl(img.dataUrl),
          })),
        }),
      });

      if (!res.ok) {
        hideLoading();
        stopLoadingTimer();
        const err = await res.json().catch(() => ({ detail: res.statusText }));
        const errText = `❌ **오류**: ${err.detail || res.statusText}`;
        const errMsg = { role: 'bot', text: errText, toolCalls: [], mode: targetMode, group: targetGroup, isError: true, retryMessage: msg };
        if (viewMatches()) appendBotMessage(errText, [], errMsg);
        pushMessage(targetGroup, targetMode, errMsg);
        setModuleState(targetGroup, targetMode, { status: 'error', finishedAt: Date.now(), lastMessageAt: Date.now() });
        setStatus('error', '● 오류');
        return;
      }

      // SSE 소비: delta(부분 텍스트) 누적 → 첫 delta에 로딩 숨기고 버블 생성, done에 마무리.
      const reader = res.body.getReader();
      const decoder = new TextDecoder();
      let buffer = '';
      let fullText = '';
      let toolCalls = [];
      let doneData = null;
      let errDetail = null;
      let streamBot = null;

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const blocks = buffer.split('\n\n');
        buffer = blocks.pop();
        for (const block of blocks) {
          if (!block.trim()) continue;
          let ev = 'message', dataStr = '';
          block.split('\n').forEach((line) => {
            if (line.startsWith('event:')) ev = line.slice(6).trim();
            else if (line.startsWith('data:')) dataStr += line.slice(5).trim();
          });
          let payload = {};
          try { payload = JSON.parse(dataStr); } catch (_) {}
          if (ev === 'delta') {
            fullText += payload.text || '';
            if (!streamBot && viewMatches()) {
              hideLoading();
              stopLoadingTimer();
              streamBot = createStreamingBot();
            }
            if (streamBot) streamBot.setText(fullText);
          } else if (ev === 'done') {
            doneData = payload;
            toolCalls = payload.tool_calls || [];
          } else if (ev === 'error') {
            errDetail = payload.detail || '알 수 없는 오류';
          }
        }
      }

      hideLoading();
      stopLoadingTimer();
      if (errDetail) throw new Error(errDetail);

      const sid = (doneData && doneData.session_id) || getSessionFor(targetGroup, targetMode);
      setSessionFor(targetGroup, targetMode, sid);
      if (viewMatches()) {
        sessionId = sid;
        updateSessionBadge();
        if (streamBot) streamBot.finalize(fullText, toolCalls, { mode: targetMode, group: targetGroup });
        else appendBotMessage(fullText, toolCalls, { mode: targetMode, group: targetGroup });
      }
      pushMessage(targetGroup, targetMode, {
        role: 'bot', text: fullText, toolCalls,
        mode: targetMode, group: targetGroup, createdAt: Date.now(),
      });
      setModuleState(targetGroup, targetMode, {
        status: viewMatches() ? 'idle' : 'done',
        finishedAt: Date.now(),
        lastMessageAt: Date.now(),
      });
      setStatus('ok', '● 정상');
    } catch (e) {
      hideLoading();
      stopLoadingTimer();
      const errText = `❌ **네트워크 오류**: ${e.message}`;
      const errMsg = { role: 'bot', text: errText, toolCalls: [], mode: targetMode, group: targetGroup, isError: true, retryMessage: msg };
      if (currentGroup === targetGroup && currentMode === targetMode) appendBotMessage(errText, [], errMsg);
      pushMessage(targetGroup, targetMode, errMsg);
      setModuleState(targetGroup, targetMode, { status: 'error', finishedAt: Date.now(), lastMessageAt: Date.now() });
      setStatus('error', '● 연결 실패');
    } finally {
      sendBtn.disabled = false;
      input.focus();
      renderRecentQuestions();
    }
  }

  // ───────── 이벤트 핸들러 ─────────
  form.addEventListener('submit', (e) => {
    e.preventDefault();
    sendMessage(input.value);
  });

  // Shift+Enter = 줄바꿈, Enter = 전송
  // 한국어 IME 합성 중 Enter는 무시 (IME commit용) — 두 번 전송 방지
  input.addEventListener('keydown', (e) => {
    if (e.key !== 'Enter' || e.shiftKey) return;
    if (e.isComposing || e.keyCode === 229) return;
    e.preventDefault();
    form.requestSubmit();
  });

  function autoGrowInput() {
    input.style.height = 'auto';
    input.style.height = Math.min(input.scrollHeight, 180) + 'px';
  }
  input.addEventListener('input', autoGrowInput);
  input.addEventListener('paste', async (e) => {
    const items = Array.from((e.clipboardData && e.clipboardData.items) || []);
    const imageItems = items.filter((item) => item.kind === 'file' && item.type.startsWith('image/'));
    if (imageItems.length === 0) return;
    e.preventDefault();
    for (const item of imageItems) {
      const file = item.getAsFile();
      if (file) await addImageFile(file);
    }
    input.focus();
  });

  // 추천 액션 카드 (welcome 상태)
  // [여기에 …] 형태의 placeholder가 있으면 해당 영역을 자동 선택해 사용자가 바로 덮어쓰게 함
  $$('.suggest-card').forEach((btn) => {
    btn.addEventListener('click', () => {
      const prompt = btn.dataset.prompt;
      input.value = prompt;
      autoGrowInput();
      input.focus();
      const m = prompt.match(/\[[^\]]+\]/);
      if (m) {
        const start = prompt.indexOf(m[0]);
        input.setSelectionRange(start, start + m[0].length);
      } else {
        // placeholder 없으면 입력 끝으로
        input.setSelectionRange(prompt.length, prompt.length);
      }
    });
  });

  // 업무(모드) 항목 (LNB) — 링크지만 같은 페이지에서는 이동 없이 제자리 전환
  $$('.nav-mode-item').forEach((a) => {
    a.addEventListener('click', (e) => {
      e.preventDefault();
      applyMode(a.dataset.mode, true);
    });
  });

  if (statsRunBtn) {
    statsRunBtn.addEventListener('click', () => {
      if (currentMode !== 'stats') applyMode('stats', true);
      form.requestSubmit();
    });
  }
  if (statsGroup) {
    statsGroup.addEventListener('change', () => {
      localStorage.setItem(`agit_stats_group_${currentGroup}`, statsGroup.value || '');
    });
  }

  // 모듈 스위처 (LNB 전역 컨텍스트)
  if (moduleSelect) {
    moduleSelect.addEventListener('change', () => applyModule(moduleSelect.value, true));
  }

  // 모델 셀렉터 (토픽바)
  if (modelTrigger) {
    modelTrigger.addEventListener('click', (e) => {
      e.stopPropagation();
      toggleModelMenu();
    });
  }
  if (modelMenu) {
    modelMenu.addEventListener('click', (e) => {
      const opt = e.target.closest('.model-option');
      if (!opt) return;
      applyModel(opt.dataset.model, true);
      toggleModelMenu(false);
    });
  }
  // 메뉴 밖 클릭 → 닫기
  document.addEventListener('click', (e) => {
    if (!modelSelector) return;
    if (modelSelector.contains(e.target)) return;
    toggleModelMenu(false);
  });
  // ESC → 닫기
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') toggleModelMenu(false);
  });

  // 새 대화 버튼 — 현재 모듈만 초기화 (다른 모듈은 유지)
  resetBtn.addEventListener('click', async () => {
    if (!confirm(`'${currentGroup}' 모듈의 '${MODE_LABELS[currentMode].label}' 대화를 종료하고 새 세션을 시작할까요?\n(다른 탭/모듈의 대화는 유지됩니다)`)) return;
    const sid = getSessionFor(currentGroup, currentMode);
    if (sid) {
      try {
        await fetch(`/api/session/${sid}`, { method: 'DELETE' });
      } catch (e) {
        // 세션 삭제 실패해도 클라이언트는 진행
      }
    }
    setSessionFor(currentGroup, currentMode, null);
    sessionId = null;
    // 현재 모듈+탭 모드의 메시지 캐시만 비우기
    messagesMap[conversationKey(currentGroup, currentMode)] = [];
    saveMessages(currentGroup, currentMode, []);
    setModuleState(currentGroup, currentMode, { status: 'idle', startedAt: null, finishedAt: null, lastMessageAt: null });
    updateSessionBadge();
    messagesEl.innerHTML = '';
    showWelcomeState();
    input.focus();
  });

  // 헬스체크 (페이지 로딩 시 한 번)
  fetch('/api/health')
    .then((r) => r.json())
    .then((d) => {
      if (d.status !== 'ok') setStatus('error', '● 서버 오류');
    })
    .catch(() => setStatus('error', '● 연결 실패'));

  // 초기 상태 적용 (모든 함수/const 선언 이후 — TDZ 회피)
  updateSessionBadge();
  initStatsDates();
  renderStatsGroupOptions(currentGroup);
  applyMode(currentMode, /*persist*/ !!_urlMode);
  applyModule(currentGroup, /*persist*/ false);
  applyModel(currentModel, /*persist*/ false);

  // 초기 포커스
  input.focus();
})();
