/**
 * OPC UA 모니터링 대시보드 — 프론트엔드 (app.js)
 *
 * 모듈 구조:
 *   State   — 앱 상태 (연결 여부, 노드 맵, 업데이트 카운트 등)
 *   WS      — WebSocket 연결 · 재연결 · 메시지 처리
 *   API     — REST API 호출 (connect, disconnect, defaults)
 *   Tree    — 노드 트리 렌더링
 *   Table   — 데이터 테이블 생성 · 갱신
 *   Form    — 폼 입력 이벤트 (authMode, securityMode 연동)
 *   Init    — DOMContentLoaded 초기화
 */

'use strict';

/* ── 상태 ─────────────────────────────────────────────────────────────────── */

const State = {
  connected:    false,
  totalUpdates: 0,
  lastUpdate:   null,
  nodeMap:      {},   // node_id → { name, initialValue }
  updateCounts: {},   // node_id → number
  ws:           null,
  wsReconnTimer: null,
};

/* ── 유틸 ─────────────────────────────────────────────────────────────────── */

function el(id) { return document.getElementById(id); }

function fmtValue(val) {
  if (val === null || val === undefined) return '—';
  if (typeof val === 'number') {
    return Number.isInteger(val) ? String(val) : val.toFixed(4);
  }
  return String(val);
}

function fmtTime(isoStr) {
  if (!isoStr) return '—';
  const d = new Date(isoStr);
  return d.toLocaleTimeString('ko-KR', { hour12: false }) +
         '.' + String(d.getMilliseconds()).padStart(3, '0');
}

function flash(element) {
  element.classList.remove('flash');
  void element.offsetWidth; // reflow
  element.classList.add('flash');
  setTimeout(() => element.classList.remove('flash'), 600);
}

function showError(msg) {
  const box = el('errorBox');
  box.textContent = msg;
  box.classList.remove('hidden');
}

function clearError() {
  el('errorBox').classList.add('hidden');
}

/* ── WebSocket ────────────────────────────────────────────────────────────── */

const WS = {
  URL: `${location.protocol === 'https:' ? 'wss:' : 'ws:'}//${location.host}/ws`,

  connect() {
    clearTimeout(State.wsReconnTimer);
    try {
      State.ws = new WebSocket(this.URL);
    } catch {
      this._scheduleReconnect();
      return;
    }

    State.ws.onopen = () => {
      el('wsStatus').textContent = 'WebSocket 연결됨';
      el('wsStatus').className = 'badge badge--ws connected';
      // keep-alive ping 매 20초
      this._pingInterval = setInterval(() => {
        if (State.ws?.readyState === WebSocket.OPEN) State.ws.send('ping');
      }, 20_000);
    };

    State.ws.onmessage = (e) => this._handleMessage(e);

    State.ws.onclose = () => {
      clearInterval(this._pingInterval);
      el('wsStatus').textContent = 'WebSocket 끊김 — 재연결 중...';
      el('wsStatus').className = 'badge badge--ws error';
      this._scheduleReconnect();
    };

    State.ws.onerror = () => {
      State.ws.close();
    };
  },

  _scheduleReconnect() {
    State.wsReconnTimer = setTimeout(() => WS.connect(), 3_000);
  },

  _handleMessage(event) {
    let msg;
    try { msg = JSON.parse(event.data); } catch { return; }

    if (msg.type === 'data_change') {
      this._onDataChange(msg);
    } else if (msg.type === 'status_change') {
      console.info('[OPC] 구독 상태 변경:', msg.status);
    }
  },

  _onDataChange({ node_id, value, timestamp }) {
    State.totalUpdates += 1;
    State.lastUpdate    = timestamp;
    State.updateCounts[node_id] = (State.updateCounts[node_id] ?? 0) + 1;

    Table.updateRow(node_id, value, timestamp);
    Tree.updateValue(node_id, value);
    Stats.update();
  },
};

/* ── REST API ─────────────────────────────────────────────────────────────── */

const API = {
  async loadDefaults() {
    try {
      const r = await fetch('/api/defaults');
      const d = await r.json();
      el('endpoint').value  = d.endpoint   ?? '';
      el('clientCert').value = d.client_cert ?? '';
      el('clientKey').value  = d.client_key  ?? '';
      el('serverCert').value = d.server_cert ?? '';
    } catch { /* 기본값 유지 */ }
  },

  async connect(payload) {
    const r = await fetch('/api/connect', {
      method:  'POST',
      headers: { 'Content-Type': 'application/json' },
      body:    JSON.stringify(payload),
    });
    if (!r.ok) {
      const err = await r.json().catch(() => ({ detail: r.statusText }));
      throw new Error(err.detail ?? '연결 실패');
    }
    return r.json();
  },

  async disconnect() {
    await fetch('/api/disconnect', { method: 'POST' });
  },
};

/* ── 노드 트리 ────────────────────────────────────────────────────────────── */

const Tree = {
  render(nodes, container) {
    container.innerHTML = '';
    if (!nodes?.length) {
      container.innerHTML = '<p class="placeholder">노드가 없습니다.</p>';
      return;
    }
    nodes.forEach(n => container.appendChild(this._buildItem(n)));
  },

  _buildItem(node) {
    const wrapper = document.createElement('div');
    wrapper.className = `tree-item tree-item--${node.class}`;

    const row = document.createElement('div');
    row.className = 'tree-row';

    // 아이콘
    const icon = document.createElement('span');
    icon.className = 'tree-icon';
    icon.textContent = { Object: '▶', Variable: '◆', Method: '⚡' }[node.class] ?? '·';

    // 이름
    const name = document.createElement('span');
    name.className = 'tree-name';
    name.title = node.node_id_full;
    name.textContent = node.name;

    row.appendChild(icon);
    row.appendChild(name);

    // Variable: 값 표시
    if (node.class === 'Variable') {
      const valSpan = document.createElement('span');
      valSpan.className = 'tree-val';
      valSpan.id = `tv-${CSS.escape(String(node.node_id))}`;
      valSpan.textContent = fmtValue(node.value);
      row.appendChild(valSpan);
    }

    wrapper.appendChild(row);

    // Object: 자식 토글
    if (node.children?.length) {
      const toggle = document.createElement('button');
      toggle.className = 'tree-toggle open';
      toggle.textContent = '▶';
      toggle.title = '접기/펼치기';
      row.insertBefore(toggle, icon);

      const childWrap = document.createElement('div');
      childWrap.className = 'tree-children';
      node.children.forEach(c => childWrap.appendChild(this._buildItem(c)));
      wrapper.appendChild(childWrap);

      toggle.addEventListener('click', () => {
        const collapsed = childWrap.classList.toggle('collapsed');
        toggle.classList.toggle('open', !collapsed);
      });

      // 아이콘 클릭도 토글
      icon.style.cursor = 'pointer';
      icon.addEventListener('click', () => toggle.click());
    }

    return wrapper;
  },

  updateValue(nodeId, value) {
    const span = document.getElementById(`tv-${CSS.escape(String(nodeId))}`);
    if (!span) return;
    span.textContent = fmtValue(value);
    flash(span);
  },

  /** 트리에서 Variable 노드 이름 맵을 구성한다 (재귀). */
  buildNodeMap(nodes) {
    nodes.forEach(n => {
      if (n.class === 'Variable') {
        State.nodeMap[String(n.node_id)] = { name: n.name, initialValue: n.value };
      }
      if (n.children?.length) this.buildNodeMap(n.children);
    });
  },
};

/* ── 데이터 테이블 ────────────────────────────────────────────────────────── */

const Table = {
  /** 연결 직후 초기값으로 테이블 전체를 구성한다. */
  init() {
    const tbody = el('dataTableBody');
    tbody.innerHTML = '';
    Object.entries(State.nodeMap).forEach(([nodeId, { name, initialValue }]) => {
      tbody.appendChild(this._createRow(nodeId, name, initialValue, null));
    });
  },

  /** 데이터 변경 수신 시 해당 행을 갱신한다. */
  updateRow(nodeId, value, timestamp) {
    const row = document.getElementById(`tr-${CSS.escape(String(nodeId))}`);
    if (!row) return;

    const valCell = row.querySelector('.cell-value');
    valCell.textContent = fmtValue(value);
    flash(valCell);

    row.querySelector('.cell-ts').textContent    = fmtTime(timestamp);
    row.querySelector('.cell-count').textContent = String(State.updateCounts[nodeId] ?? 0);
  },

  _createRow(nodeId, name, initialValue, timestamp) {
    const tr = document.createElement('tr');
    tr.id = `tr-${CSS.escape(String(nodeId))}`;

    tr.innerHTML = `
      <td class="cell-nodeid" title="${nodeId}">${nodeId}</td>
      <td class="cell-name">${name}</td>
      <td><span class="cell-value">${fmtValue(initialValue)}</span></td>
      <td class="cell-ts">${fmtTime(timestamp)}</td>
      <td class="cell-count">0</td>
    `;
    return tr;
  },

  clear() {
    el('dataTableBody').innerHTML =
      '<tr><td colspan="5" class="table-empty">연결 후 데이터가 표시됩니다.</td></tr>';
  },
};

/* ── 통계 카드 ────────────────────────────────────────────────────────────── */

const Stats = {
  update() {
    el('statConnected').textContent    = State.connected ? '연결됨' : '연결 안됨';
    el('statNodeCount').textContent    = String(Object.keys(State.nodeMap).length);
    el('statLastUpdate').textContent   = fmtTime(State.lastUpdate);
    el('statTotalUpdates').textContent = String(State.totalUpdates);
    el('updateCounter').textContent    = `업데이트: ${State.totalUpdates}`;
  },
};

/* ── 연결 상태 UI ─────────────────────────────────────────────────────────── */

const UI = {
  setConnecting() {
    el('statusBadge').textContent = '연결 중...';
    el('statusBadge').className   = 'badge badge--connecting';
    el('connectBtn').disabled     = true;
    el('disconnectBtn').disabled  = true;
    el('loadingOverlay').classList.remove('hidden');
  },

  setConnected(nodeCount) {
    el('statusBadge').textContent = '연결됨';
    el('statusBadge').className   = 'badge badge--connected';
    el('connectBtn').disabled     = true;
    el('disconnectBtn').disabled  = false;
    el('loadingOverlay').classList.add('hidden');
    el('treeCount').textContent   = `${nodeCount}개`;
    State.connected = true;
    Stats.update();
  },

  setDisconnected() {
    el('statusBadge').textContent = '연결 안됨';
    el('statusBadge').className   = 'badge badge--disconnected';
    el('connectBtn').disabled     = false;
    el('disconnectBtn').disabled  = true;
    el('loadingOverlay').classList.add('hidden');
    el('treeCount').textContent   = '';
    State.connected     = false;
    State.totalUpdates  = 0;
    State.lastUpdate    = null;
    State.nodeMap       = {};
    State.updateCounts  = {};
    el('nodeTree').innerHTML = '<p class="placeholder">서버에 연결하면 노드 트리가 표시됩니다.</p>';
    Table.clear();
    Stats.update();
  },

  setError(msg) {
    el('statusBadge').textContent = '오류';
    el('statusBadge').className   = 'badge badge--error';
    el('connectBtn').disabled     = false;
    el('disconnectBtn').disabled  = true;
    el('loadingOverlay').classList.add('hidden');
    showError(msg);
  },
};

/* ── 폼 이벤트 ────────────────────────────────────────────────────────────── */

const Form = {
  init() {
    el('authMode').addEventListener('change', () => this._syncVisibility());
    el('securityMode').addEventListener('change', () => this._syncVisibility());
    el('connectForm').addEventListener('submit', (e) => {
      e.preventDefault();
      this._handleConnect();
    });
    el('disconnectBtn').addEventListener('click', () => this._handleDisconnect());
    this._syncVisibility();
  },

  _syncVisibility() {
    const auth = el('authMode').value;
    const sec  = el('securityMode').value;

    el('credGroup').classList.toggle('hidden', auth !== 'username');
    el('certGroup').classList.toggle('hidden',
      auth !== 'certificate' && sec !== 'sign_encrypt'
    );
  },

  async _handleConnect() {
    clearError();
    UI.setConnecting();

    const payload = {
      endpoint:      el('endpoint').value.trim(),
      auth_mode:     el('authMode').value,
      security_mode: el('securityMode').value,
    };

    if (payload.auth_mode === 'username') {
      payload.username = el('username').value.trim();
      payload.password = el('password').value;
    }

    const needCert = payload.auth_mode === 'certificate' ||
                     payload.security_mode === 'sign_encrypt';
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

      // 노드 맵 구성
      State.nodeMap = {};
      Tree.buildNodeMap(res.tree ?? []);

      // 트리 렌더링
      Tree.render(res.tree ?? [], el('nodeTree'));

      // 테이블 초기화
      Table.init();

      UI.setConnected(res.node_count ?? 0);
    } catch (err) {
      UI.setError(err.message ?? '알 수 없는 오류');
    }
  },

  async _handleDisconnect() {
    try {
      await API.disconnect();
    } catch { /* 무시 */ }
    UI.setDisconnected();
  },
};

/* ── 초기화 ─────────────────────────────────────────────────────────────────  */

document.addEventListener('DOMContentLoaded', async () => {
  WS.connect();
  Form.init();
  Stats.update();
  await API.loadDefaults();
});
