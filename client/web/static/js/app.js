'use strict';

/* ═══════════════════════════════════════════════════════════════
   State  — 앱 전역 상태
   servers: Map<server_id, {
     endpoint, tree, nodeMap(id→name), nameToId(name→id), values(id→{value,ts})
   }>
═══════════════════════════════════════════════════════════════ */
const State = {
  servers:          new Map(),
  totalUpdates:     0,
  lastUpdate:       null,
  updateCounts:     {},
  currentView:      'list',   // 'list' | 'detail'
  selectedServerId: null,
};

/* ═══════════════════════════════════════════════════════════════
   유틸리티
═══════════════════════════════════════════════════════════════ */

function el(id) { return document.getElementById(id); }

// "opc.tcp://host:port/path" → "host:port"
function shortEp(ep) {
  return ep.replace(/^opc\.tcp:\/\//, '').split('/')[0];
}

// seconds → "HH:MM:SS"
function fmtDuration(secs) {
  if (secs == null || isNaN(Number(secs))) return '—';
  const s = Math.max(0, Math.floor(Number(secs)));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = s % 60;
  return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:${String(sec).padStart(2, '0')}`;
}

// Unix timestamp (seconds) → localized string
function fmtUnixTime(unix) {
  if (!unix || Number(unix) === 0) return '—';
  return new Date(Number(unix) * 1000).toLocaleString('ko-KR', { hour12: false });
}

// 타임스탬프 ISO → 시:분:초.밀리초
function fmtTime(isoStr) {
  if (!isoStr) return '—';
  const d = new Date(isoStr);
  return d.toLocaleTimeString('ko-KR', { hour12: false }) +
         '.' + String(d.getMilliseconds()).padStart(3, '0');
}

function flash(elem) {
  if (!elem) return;
  elem.classList.remove('flash');
  void elem.offsetWidth;
  elem.classList.add('flash');
  setTimeout(() => elem.classList.remove('flash'), 600);
}

// format 에 따라 값을 문자열로 변환
function fmtField(format, value, unit = '') {
  if (value === null || value === undefined) return '—';
  let display;
  switch (format) {
    case 'float':    display = typeof value === 'number' ? value.toFixed(2) : String(value); break;
    case 'number':   display = String(Math.floor(Number(value))); break;
    case 'duration': display = fmtDuration(value); break;
    case 'unixtime': display = fmtUnixTime(value); break;
    default:         display = String(value);
  }
  return unit ? `${display} ${unit}` : display;
}

// 트리에서 Variable 이름 → node_id 맵 생성 (재귀)
function buildNameToId(nodes, result = {}) {
  nodes.forEach(n => {
    if (n.class === 'Variable') result[n.name] = String(n.node_id);
    if (n.children?.length)    buildNameToId(n.children, result);
  });
  return result;
}

// 트리에서 node_id → 이름 맵 생성 (재귀)
function buildNodeMap(nodes, result = {}) {
  nodes.forEach(n => {
    if (n.class === 'Variable') result[String(n.node_id)] = n.name;
    if (n.children?.length)    buildNodeMap(n.children, result);
  });
  return result;
}

// 서버의 특정 이름 노드 값을 반환
function getNodeValue(serverId, nodeName) {
  const srv = State.servers.get(serverId);
  if (!srv) return null;
  const nodeId = srv.nameToId[nodeName];
  if (nodeId == null) return null;
  return srv.values[nodeId]?.value ?? null;
}

// State 정수 + StateText → CSS 클래스
function getStateClass(state, stateText) {
  if (Number(state) === 0) return 'state--idle';
  const t = String(stateText || '').toLowerCase();
  if (t.includes('error') || t.includes('fail') || t.includes('오류')) return 'state--error';
  if (t.includes('complet') || t.includes('done') || t.includes('finish') || t.includes('완료')) return 'state--complete';
  return 'state--active';
}

/* ═══════════════════════════════════════════════════════════════
   WebSocket
═══════════════════════════════════════════════════════════════ */
const WS = {
  URL: `${location.protocol === 'https:' ? 'wss:' : 'ws:'}//${location.host}/ws`,
  _ws: null, _ping: null, _timer: null,

  connect() {
    clearTimeout(this._timer);
    try { this._ws = new WebSocket(this.URL); } catch { this._retry(); return; }

    this._ws.onopen = () => {
      el('wsStatus').textContent = 'WebSocket 연결됨';
      el('wsStatus').className = 'badge badge--ws connected';
      this._ping = setInterval(() => {
        if (this._ws?.readyState === WebSocket.OPEN) this._ws.send('ping');
      }, 20_000);
    };

    this._ws.onmessage = e => this._handle(e);
    this._ws.onclose   = () => {
      clearInterval(this._ping);
      el('wsStatus').textContent = 'WebSocket 끊김 — 재연결 중...';
      el('wsStatus').className = 'badge badge--ws error';
      this._retry();
    };
    this._ws.onerror = () => this._ws.close();
  },

  _retry() { this._timer = setTimeout(() => this.connect(), 3_000); },

  _handle(event) {
    let msg;
    try { msg = JSON.parse(event.data); } catch { return; }

    if (msg.type !== 'data_change') return;

    const { server_id, node_id, value, timestamp } = msg;
    const nodeIdStr = String(node_id);

    State.totalUpdates++;
    State.lastUpdate = timestamp;
    const key = `${server_id}:${nodeIdStr}`;
    State.updateCounts[key] = (State.updateCounts[key] ?? 0) + 1;

    // 값 캐시 갱신
    const srv = State.servers.get(server_id);
    if (!srv) return;
    srv.values[nodeIdStr] = { value, timestamp };
    const nodeName = srv.nodeMap[nodeIdStr];

    // 뷰별 라우팅
    if (State.currentView === 'list') {
      DeviceList.handleValueChange(server_id, nodeName);
    } else if (State.currentView === 'detail' && State.selectedServerId === server_id) {
      DeviceDetail.updateField(server_id, nodeIdStr, value, nodeName);
    }

    Stats.update();
  },
};

/* ═══════════════════════════════════════════════════════════════
   REST API
═══════════════════════════════════════════════════════════════ */
const API = {
  async _call(method, path, body) {
    const opts = {
      method,
      headers: body ? { 'Content-Type': 'application/json' } : {},
      body:    body ? JSON.stringify(body) : undefined,
    };
    const res = await fetch(path, opts);
    if (!res.ok) {
      const err = await res.json().catch(() => ({ detail: res.statusText }));
      throw new Error(err.detail ?? res.statusText);
    }
    return res.json();
  },

  async loadDefaults() {
    try {
      const d = await this._call('GET', '/api/defaults');
      el('endpoint').value   = d.endpoint    ?? '';
      el('clientCert').value = d.client_cert ?? '';
      el('clientKey').value  = d.client_key  ?? '';
      el('serverCert').value = d.server_cert ?? '';
    } catch { /* 기본값 유지 */ }
  },

  connect:    payload => API._call('POST', '/api/connect', payload),
  disconnect: sid     => API._call('POST', `/api/disconnect/${sid}`),
  servers:    ()      => API._call('GET',  '/api/servers'),
};

/* ═══════════════════════════════════════════════════════════════
   Router — 뷰 전환
═══════════════════════════════════════════════════════════════ */
const Router = {
  showList() {
    el('viewDetail').classList.add('hidden');
    el('viewList').classList.remove('hidden');
    State.currentView      = 'list';
    State.selectedServerId = null;
  },

  showDetail(serverId) {
    el('viewList').classList.add('hidden');
    el('viewDetail').classList.remove('hidden');
    State.currentView      = 'detail';
    State.selectedServerId = serverId;
    DeviceDetail.render(serverId);
  },
};

/* ═══════════════════════════════════════════════════════════════
   DeviceList — 장비 목록 카드
═══════════════════════════════════════════════════════════════ */
const DeviceList = {
  // 카드 업데이트가 필요한 노드 이름 집합
  CARD_NODES: new Set(['State', 'StateText', 'Progress', 'CurrentLayer', 'TotalLayers', 'Model', 'Manufacturer']),

  addCard(serverId) {
    el('noDevicesHint')?.remove();

    const srv  = State.servers.get(serverId);
    const card = document.createElement('div');
    card.className = 'device-card';
    card.id        = `dc-${serverId}`;

    card.innerHTML = `
      <div class="card-header">
        <span class="card-status-dot state--idle"></span>
        <span class="card-endpoint" title="${srv.endpoint}">${shortEp(srv.endpoint)}</span>
        <button class="btn-card-disc" title="연결 해제">×</button>
      </div>
      <div class="card-body">
        <div class="card-device-name">—</div>
        <div class="card-device-maker">—</div>
        <div class="card-state-badge state--idle">—</div>
        <div class="card-progress hidden">
          <div class="card-progress-track">
            <div class="card-progress-fill" style="width:0%"></div>
          </div>
          <div class="card-progress-bottom">
            <span class="card-progress-pct">0%</span>
          </div>
        </div>
        <div class="card-layer-info hidden"></div>
      </div>
    `;

    card.querySelector('.btn-card-disc').addEventListener('click', e => {
      e.stopPropagation();
      Form.handleDisconnect(serverId);
    });
    card.addEventListener('click', () => Router.showDetail(serverId));

    el('deviceGrid').appendChild(card);
    this._refreshCard(serverId);
    Stats.update();
  },

  removeCard(serverId) {
    el(`dc-${serverId}`)?.remove();
    if (!el('deviceGrid').querySelector('.device-card')) {
      const hint = document.createElement('div');
      hint.id        = 'noDevicesHint';
      hint.className = 'empty-state';
      hint.innerHTML = `
        <div class="empty-icon">◈</div>
        <p class="empty-title">연결된 장비가 없습니다</p>
        <p class="empty-sub">위의 '+ 장비 추가' 버튼으로 OPC UA 서버에 연결하세요.</p>
      `;
      el('deviceGrid').appendChild(hint);
    }
    Stats.update();
  },

  handleValueChange(serverId, nodeName) {
    if (!this.CARD_NODES.has(nodeName)) return;
    this._refreshCard(serverId);
  },

  _refreshCard(serverId) {
    const card = el(`dc-${serverId}`);
    if (!card) return;

    const stateVal    = getNodeValue(serverId, 'State');
    const stateText   = String(getNodeValue(serverId, 'StateText') ?? '—');
    const progress    = Math.max(0, Math.min(100, Number(getNodeValue(serverId, 'Progress') ?? 0)));
    const curLayer    = getNodeValue(serverId, 'CurrentLayer');
    const totLayer    = getNodeValue(serverId, 'TotalLayers');
    const model       = String(getNodeValue(serverId, 'Model')        ?? '—');
    const maker       = String(getNodeValue(serverId, 'Manufacturer') ?? '—');
    const stateClass  = getStateClass(stateVal, stateText);

    card.querySelector('.card-status-dot').className  = `card-status-dot ${stateClass}`;
    card.querySelector('.card-device-name').textContent = model;
    card.querySelector('.card-device-maker').textContent = maker;

    const badge = card.querySelector('.card-state-badge');
    badge.textContent = stateText;
    badge.className   = `card-state-badge ${stateClass}`;

    const progWrap = card.querySelector('.card-progress');
    if (progress > 0) {
      progWrap.classList.remove('hidden');
      card.querySelector('.card-progress-fill').style.width = `${progress}%`;
      card.querySelector('.card-progress-pct').textContent  = `${progress}%`;
    } else {
      progWrap.classList.add('hidden');
    }

    const layerElem = card.querySelector('.card-layer-info');
    if (curLayer != null && totLayer != null) {
      layerElem.textContent = `레이어 ${curLayer} / ${totLayer}`;
      layerElem.classList.remove('hidden');
    } else {
      layerElem.classList.add('hidden');
    }
  },
};

/* ═══════════════════════════════════════════════════════════════
   DeviceDetail — 장비 상세 화면
═══════════════════════════════════════════════════════════════ */
const DeviceDetail = {
  render(serverId) {
    const srv = State.servers.get(serverId);
    if (!srv) return;

    el('detailTitle').textContent = shortEp(srv.endpoint);
    el('btnDetailDisc').onclick   = () => Form.handleDisconnect(serverId);

    const stateVal  = getNodeValue(serverId, 'State');
    const stateText = String(getNodeValue(serverId, 'StateText') ?? '—');
    const stateClass = getStateClass(stateVal, stateText);
    const progress  = Math.max(0, Math.min(100, Number(getNodeValue(serverId, 'Progress') ?? 0)));
    const curLayer  = getNodeValue(serverId, 'CurrentLayer');
    const totLayer  = getNodeValue(serverId, 'TotalLayers');
    const layerTxt  = curLayer != null ? `레이어 ${curLayer} / ${totLayer}` : '';

    el('detailContent').innerHTML = `
      <!-- 장비 식별 -->
      <div class="detail-section">
        <div class="detail-section-title">장비 식별</div>
        <div class="detail-fields-grid">
          ${this._field(serverId, 'Manufacturer', '제조사',       'text')}
          ${this._field(serverId, 'Model',        '모델',         'text')}
          ${this._field(serverId, 'SerialNumber', '시리얼 번호',  'text')}
        </div>
      </div>

      <!-- 작업 상태 -->
      <div class="detail-section">
        <div class="detail-section-title">작업 상태</div>
        <div class="detail-status-row">
          <span class="status-badge ${stateClass}" id="dsi-${serverId}">
            <span class="status-dot"></span>
            <span id="dsi-text-${serverId}">${stateText}</span>
          </span>
        </div>
        <div class="detail-progress-row">
          <div class="progress-track">
            <div class="progress-fill" id="dp-fill-${serverId}" style="width:${progress}%"></div>
          </div>
          <span class="progress-pct"    id="dp-pct-${serverId}">${progress}%</span>
          <span class="progress-layers" id="dp-layers-${serverId}">${layerTxt}</span>
        </div>
        <div class="detail-fields-grid">
          ${this._field(serverId, 'BuildJob',       '작업명',       'text')}
          ${this._field(serverId, 'CurrentLayer',   '현재 레이어',  'number')}
          ${this._field(serverId, 'TotalLayers',    '전체 레이어',  'number')}
          ${this._field(serverId, 'RemainingTime',  '남은 시간',    'duration')}
          ${this._field(serverId, 'TotalBuildTime', '총 빌드 시간', 'duration')}
          ${this._field(serverId, 'StartTime',      '시작 시각',    'unixtime')}
          ${this._field(serverId, 'EndTime',        '종료 시각',    'unixtime')}
        </div>
      </div>

      <!-- 센서 -->
      <div class="detail-section">
        <div class="detail-section-title">센서</div>
        <div class="detail-fields-grid">
          ${this._field(serverId, 'BuildPlatformZPosition', '플랫폼 Z위치',   'float', 'mm')}
          ${this._field(serverId, 'LevelTankZPosition',     '탱크 Z위치',     'float', 'mm')}
          ${this._field(serverId, 'BladeState',             '블레이드 상태',  'number')}
          ${this._field(serverId, 'CollectBladeState',      '수집 블레이드',  'number')}
          ${this._field(serverId, 'PrintBladeState',        '출력 블레이드',  'number')}
          ${this._field(serverId, 'ResinTemp',              '레진 온도',      'float', '°C')}
          ${this._field(serverId, 'ResinLevel',             '레진 수위',      'float')}
          ${this._field(serverId, 'ResinLevelStablity',     '수위 안정도',    'float')}
          ${this._field(serverId, 'VatPres',                '배트 압력',      'float')}
          ${this._field(serverId, 'UVLTemp',                'UV 좌측 온도',   'float', '°C')}
          ${this._field(serverId, 'UVRTemp',                'UV 우측 온도',   'float', '°C')}
        </div>
      </div>
    `;
  },

  // WebSocket 값 변경 시 해당 필드만 갱신
  updateField(serverId, _nodeId, value, nodeName) {
    if (!nodeName) return;

    // 일반 필드 갱신
    const fieldElem = el(`df-${serverId}-${nodeName}`);
    if (fieldElem) {
      fieldElem.textContent = fmtField(fieldElem.dataset.format, value, fieldElem.dataset.unit || '');
      flash(fieldElem);
    }

    // 상태 배지 갱신
    if (nodeName === 'State' || nodeName === 'StateText') {
      const stateVal   = getNodeValue(serverId, 'State');
      const stateText  = String(getNodeValue(serverId, 'StateText') ?? '—');
      const stateClass = getStateClass(stateVal, stateText);
      const badge      = el(`dsi-${serverId}`);
      const textElem   = el(`dsi-text-${serverId}`);
      if (badge)    badge.className   = `status-badge ${stateClass}`;
      if (textElem) textElem.textContent = stateText;
    }

    // 진행률 갱신
    if (nodeName === 'Progress') {
      const pct = Math.max(0, Math.min(100, Number(value) || 0));
      const fill = el(`dp-fill-${serverId}`);
      const pctE = el(`dp-pct-${serverId}`);
      if (fill) fill.style.width   = `${pct}%`;
      if (pctE) pctE.textContent   = `${pct}%`;
    }

    // 레이어 정보 갱신
    if (nodeName === 'CurrentLayer' || nodeName === 'TotalLayers') {
      const cur   = getNodeValue(serverId, 'CurrentLayer');
      const tot   = getNodeValue(serverId, 'TotalLayers');
      const layE  = el(`dp-layers-${serverId}`);
      if (layE) layE.textContent = cur != null ? `레이어 ${cur} / ${tot}` : '';
    }
  },

  // 단일 상세 필드 HTML 생성
  _field(serverId, nodeName, label, format, unit = '') {
    const srv    = State.servers.get(serverId);
    const nodeId = srv?.nameToId[nodeName];
    const raw    = nodeId != null ? (srv.values[nodeId]?.value ?? null) : null;
    return `
      <div class="detail-field">
        <div class="detail-field-label">${label}</div>
        <div class="detail-field-value"
             id="df-${serverId}-${nodeName}"
             data-format="${format}"
             data-unit="${unit}">${fmtField(format, raw, unit)}</div>
      </div>`;
  },
};

/* ═══════════════════════════════════════════════════════════════
   Stats — 헤더 장비 수 뱃지 갱신
═══════════════════════════════════════════════════════════════ */
const Stats = {
  update() {
    el('deviceCount').textContent = String(State.servers.size);
  },
};

/* ═══════════════════════════════════════════════════════════════
   Form — 연결 모달 + 연결/해제 처리
═══════════════════════════════════════════════════════════════ */
const Form = {
  init() {
    el('btnAddServer').addEventListener('click',  () => this.show());
    el('cancelBtn').addEventListener('click',     () => this.hide());
    el('btnModalClose').addEventListener('click', () => this.hide());
    el('modalBackdrop').addEventListener('click', () => this.hide());
    el('btnBack').addEventListener('click',       () => Router.showList());
    el('authMode').addEventListener('change',     () => this._syncVis());
    el('securityMode').addEventListener('change', () => this._syncVis());
    el('connectForm').addEventListener('submit',  e  => { e.preventDefault(); this._connect(); });
    this._syncVis();
  },

  show() {
    el('connectModal').classList.remove('hidden');
    el('endpoint').focus();
  },

  hide() {
    el('connectModal').classList.add('hidden');
    el('errorBox').classList.add('hidden');
  },

  _syncVis() {
    const auth = el('authMode').value;
    const sec  = el('securityMode').value;
    el('credGroup').classList.toggle('hidden', auth !== 'username');
    el('certGroup').classList.toggle('hidden', auth !== 'certificate' && sec !== 'sign_encrypt');
  },

  async _connect() {
    const btn = el('connectBtn');
    btn.disabled    = true;
    btn.textContent = '연결 중...';
    el('errorBox').classList.add('hidden');
    el('loadingOverlay').classList.remove('hidden');

    const payload = {
      endpoint:      el('endpoint').value.trim(),
      auth_mode:     el('authMode').value,
      security_mode: el('securityMode').value,
    };
    if (payload.auth_mode === 'username') {
      payload.username = el('username').value.trim();
      payload.password = el('password').value;
    }
    const needCert = payload.auth_mode === 'certificate' || payload.security_mode === 'sign_encrypt';
    if (needCert) {
      const cc = el('clientCert').value.trim();
      const ck = el('clientKey').value.trim();
      const sc = el('serverCert').value.trim();
      if (cc) payload.client_cert = cc;
      if (ck) payload.client_key  = ck;
      if (sc) payload.server_cert = sc;
    }

    try {
      const res = await API.connect(payload);
      // res: { server_id, node_count, tree }
      this._registerServer(res.server_id, payload.endpoint, res.tree ?? [], res.values ?? {});
      this.hide();
    } catch (err) {
      el('errorBox').textContent = err.message ?? '알 수 없는 오류';
      el('errorBox').classList.remove('hidden');
    } finally {
      btn.disabled    = false;
      btn.textContent = '연결';
      el('loadingOverlay').classList.add('hidden');
    }
  },

  _registerServer(serverId, endpoint, tree, externalValues = {}) {
    const nodeMap  = buildNodeMap(tree);
    const nameToId = buildNameToId(tree);

    // 트리 노드의 인라인 value 로 초기값 구성
    const values = {};
    const now = new Date().toISOString();
    (function extractValues(nodes) {
      nodes.forEach(n => {
        if (n.class === 'Variable' && n.value !== null && n.value !== undefined) {
          values[String(n.node_id)] = { value: n.value, timestamp: now };
        }
        if (n.children?.length) extractValues(n.children);
      });
    })(tree);

    // /api/servers 에서 온 최신 값으로 덮어씌움
    Object.assign(values, externalValues);

    State.servers.set(serverId, { endpoint, tree, nodeMap, nameToId, values });
    DeviceList.addCard(serverId);
  },

  async handleDisconnect(serverId) {
    try { await API.disconnect(serverId); } catch { /* 이미 끊긴 경우 무시 */ }

    // updateCounts 정리
    for (const k of Object.keys(State.updateCounts)) {
      if (k.startsWith(`${serverId}:`)) delete State.updateCounts[k];
    }

    // 상세 뷰에서 이 서버를 보고 있었다면 목록으로 복귀
    if (State.selectedServerId === serverId) {
      Router.showList();
    }

    State.servers.delete(serverId);
    DeviceList.removeCard(serverId);
    Stats.update();
  },
};

/* ═══════════════════════════════════════════════════════════════
   초기화
═══════════════════════════════════════════════════════════════ */
document.addEventListener('DOMContentLoaded', async () => {
  WS.connect();
  Form.init();
  await API.loadDefaults();

  // 이미 연결된 서버 복원 (페이지 새로고침 시)
  try {
    const { servers } = await API.servers();
    for (const s of servers) {
      Form._registerServer(s.server_id, s.endpoint, s.tree ?? [], s.values ?? {});
    }
  } catch { /* 연결 없거나 오류 → 무시 */ }

  Stats.update();
});
