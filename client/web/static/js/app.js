/**
 * OPC UA 모니터링 대시보드 — 프론트엔드 (app.js)
 *
 * 다수의 OPC UA 서버에 동시 연결하고, 각 서버의 노드 트리와
 * 실시간 값을 통합 대시보드에 표시한다.
 *
 * 모듈 구조:
 *   State  — 연결된 서버 Map, 총 수신 횟수 등 앱 전역 상태
 *   WS     — WebSocket 연결·재연결·메시지 라우팅
 *   API    — REST API 호출 (connect, disconnect, defaults)
 *   Tree   — 사이드바 서버 섹션 및 노드 트리 렌더링
 *   Table  — 데이터 테이블 행 생성·갱신
 *   Stats  — 상단 통계 카드 업데이트
 *   Form   — 연결 폼 토글·입력·제출 처리
 */

'use strict';

/* ── 상태 ─────────────────────────────────────────────────────────────────── */

const State = {
  // Map<server_id, { endpoint: string, nodeMap: {node_id → name} }>
  servers:      new Map(),
  totalUpdates: 0,
  lastUpdate:   null,
  // `${server_id}:${node_id}` → 변경 횟수
  updateCounts: {},
};

/* ── 유틸 ─────────────────────────────────────────────────────────────────── */

function el(id) { return document.getElementById(id); }

function fmtValue(val) {
  if (val === null || val === undefined) return '—';
  if (typeof val === 'number') return Number.isInteger(val) ? String(val) : val.toFixed(4);
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
  void element.offsetWidth;  // reflow 로 애니메이션 재시작
  element.classList.add('flash');
  setTimeout(() => element.classList.remove('flash'), 600);
}

// "opc.tcp://host:4840/path" → "host:4840"
function shortEp(endpoint) {
  return endpoint.replace(/^opc\.tcp:\/\//, '').split('/')[0];
}

// ID 속성에 안전하게 사용할 수 있는 키 생성 (공백 제거)
function rowKey(server_id, node_id) {
  return `${server_id}-${String(node_id).replace(/\s+/g, '_')}`;
}

/* ── WebSocket ────────────────────────────────────────────────────────────── */

const WS = {
  URL: `${location.protocol === 'https:' ? 'wss:' : 'ws:'}//${location.host}/ws`,
  _ws:            null,
  _pingInterval:  null,
  _reconnTimer:   null,

  connect() {
    clearTimeout(this._reconnTimer);
    try {
      this._ws = new WebSocket(this.URL);
    } catch {
      this._scheduleReconnect();
      return;
    }

    this._ws.onopen = () => {
      el('wsStatus').textContent = 'WebSocket 연결됨';
      el('wsStatus').className = 'badge badge--ws connected';
      // keep-alive ping 매 20초
      this._pingInterval = setInterval(() => {
        if (this._ws?.readyState === WebSocket.OPEN) this._ws.send('ping');
      }, 20_000);
    };

    this._ws.onmessage = (e) => this._handleMessage(e);

    this._ws.onclose = () => {
      clearInterval(this._pingInterval);
      el('wsStatus').textContent = 'WebSocket 끊김 — 재연결 중...';
      el('wsStatus').className = 'badge badge--ws error';
      this._scheduleReconnect();
    };

    this._ws.onerror = () => { this._ws.close(); };
  },

  _scheduleReconnect() {
    this._reconnTimer = setTimeout(() => this.connect(), 3_000);
  },

  _handleMessage(event) {
    let msg;
    try { msg = JSON.parse(event.data); } catch { return; }

    if (msg.type === 'data_change') {
      const { server_id, node_id, value, timestamp } = msg;
      const key = `${server_id}:${node_id}`;
      State.totalUpdates += 1;
      State.lastUpdate = timestamp;
      State.updateCounts[key] = (State.updateCounts[key] ?? 0) + 1;

      Tree.updateValue(server_id, node_id, value);
      Table.updateRow(server_id, node_id, value, timestamp);
      Stats.update();
    } else if (msg.type === 'status_change') {
      console.info('[OPC]', msg.server_id, '구독 상태 변경:', msg.status);
    }
  },
};

/* ── REST API ─────────────────────────────────────────────────────────────── */

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

  connect:    (payload)  => API._call('POST', '/api/connect', payload),
  disconnect: (sid)      => API._call('POST', `/api/disconnect/${sid}`),
};

/* ── 노드 트리 ────────────────────────────────────────────────────────────── */

const Tree = {
  /** 사이드바에 서버 섹션(헤더 + 노드 트리)을 추가한다. */
  addServer(server_id, endpoint, nodes) {
    // 빈 상태 힌트 제거
    el('noServersHint')?.remove();

    const section = document.createElement('div');
    section.className = 'server-section';
    section.id = `ss-${server_id}`;

    section.innerHTML = `
      <div class="server-section-header">
        <span class="server-dot"></span>
        <div class="server-info">
          <span class="server-id-label">${server_id}</span>
          <span class="server-ep-label" title="${endpoint}">${shortEp(endpoint)}</span>
        </div>
        <button class="btn-server-disc" title="연결 해제">×</button>
      </div>
      <div class="server-tree-wrap"></div>
    `;

    section.querySelector('.btn-server-disc').addEventListener('click', () => {
      Form.handleDisconnect(server_id);
    });

    const treeWrap = section.querySelector('.server-tree-wrap');
    nodes.forEach(n => treeWrap.appendChild(this._buildItem(n, server_id)));

    el('serverList').appendChild(section);
  },

  /** 서버 섹션을 사이드바에서 제거하고 빈 상태 힌트를 복원한다. */
  removeServer(server_id) {
    el(`ss-${server_id}`)?.remove();
    if (!el('serverList').querySelector('.server-section')) {
      const hint = document.createElement('p');
      hint.className = 'placeholder';
      hint.id = 'noServersHint';
      hint.textContent = '연결된 서버가 없습니다.';
      el('serverList').appendChild(hint);
    }
  },

  _buildItem(node, server_id) {
    const wrapper = document.createElement('div');
    wrapper.className = `tree-item tree-item--${node.class}`;

    const row = document.createElement('div');
    row.className = 'tree-row';

    const icon = document.createElement('span');
    icon.className = 'tree-icon';
    icon.textContent = { Object: '▶', Variable: '◆', Method: '⚡' }[node.class] ?? '·';

    const name = document.createElement('span');
    name.className = 'tree-name';
    name.title = node.node_id_full;
    name.textContent = node.name;

    row.appendChild(icon);
    row.appendChild(name);

    if (node.class === 'Variable') {
      const valSpan = document.createElement('span');
      valSpan.className = 'tree-val';
      valSpan.id = `tv-${rowKey(server_id, node.node_id)}`;
      valSpan.textContent = fmtValue(node.value);
      row.appendChild(valSpan);
    }

    wrapper.appendChild(row);

    if (node.children?.length) {
      const toggle = document.createElement('button');
      toggle.className = 'tree-toggle open';
      toggle.textContent = '▶';
      row.insertBefore(toggle, icon);

      const childWrap = document.createElement('div');
      childWrap.className = 'tree-children';
      node.children.forEach(c => childWrap.appendChild(this._buildItem(c, server_id)));
      wrapper.appendChild(childWrap);

      toggle.addEventListener('click', () => {
        childWrap.classList.toggle('collapsed');
        toggle.classList.toggle('open', !childWrap.classList.contains('collapsed'));
      });
      icon.style.cursor = 'pointer';
      icon.addEventListener('click', () => toggle.click());
    }

    return wrapper;
  },

  /** 트리 내 Variable 노드 값 뱃지를 갱신한다. */
  updateValue(server_id, node_id, value) {
    const span = el(`tv-${rowKey(server_id, node_id)}`);
    if (!span) return;
    span.textContent = fmtValue(value);
    flash(span);
  },

  /** 노드 트리에서 Variable 노드의 node_id → name 맵을 구성한다 (재귀). */
  buildNodeMap(nodes) {
    const map = {};
    function walk(ns) {
      ns.forEach(n => {
        if (n.class === 'Variable') map[String(n.node_id)] = n.name;
        if (n.children?.length) walk(n.children);
      });
    }
    walk(nodes);
    return map;
  },
};

/* ── 데이터 테이블 ────────────────────────────────────────────────────────── */

const Table = {
  /** 서버의 모든 Variable 노드를 테이블에 행으로 추가한다. */
  addServer(server_id, endpoint, nodeMap, tree) {
    // 빈 상태 행 제거
    el('dataTableBody').querySelector('[data-empty]')?.remove();

    const ep = shortEp(endpoint);

    function addRows(nodes) {
      nodes.forEach(n => {
        if (n.class === 'Variable') {
          const tr = document.createElement('tr');
          const key = rowKey(server_id, n.node_id);
          tr.id = `tr-${key}`;
          tr.innerHTML = `
            <td><span class="ep-badge" title="${endpoint}">${ep}</span></td>
            <td class="cell-name">${nodeMap[String(n.node_id)] ?? n.node_id}</td>
            <td><span class="cell-value" id="tv-table-${key}">${fmtValue(n.value)}</span></td>
            <td class="cell-ts">—</td>
            <td class="cell-count">0</td>
          `;
          el('dataTableBody').appendChild(tr);
        }
        if (n.children?.length) addRows(n.children);
      });
    }
    addRows(tree);
  },

  /** 서버 연결 해제 시 해당 서버의 모든 행을 제거한다. */
  removeServer(server_id) {
    el('dataTableBody')
      .querySelectorAll(`[id^="tr-${server_id}-"]`)
      .forEach(r => r.remove());

    // 모든 행이 제거되면 빈 상태 행 복원
    if (!el('dataTableBody').querySelector('tr:not([data-empty])')) {
      const tr = document.createElement('tr');
      tr.setAttribute('data-empty', '1');
      tr.innerHTML = '<td colspan="5" class="table-empty">연결 후 데이터가 표시됩니다.</td>';
      el('dataTableBody').appendChild(tr);
    }
  },

  /** 데이터 변경 수신 시 해당 행의 값·타임스탬프·변경 횟수를 갱신한다. */
  updateRow(server_id, node_id, value, timestamp) {
    const key = rowKey(server_id, node_id);
    const cell = el(`tv-table-${key}`);
    if (!cell) return;
    cell.textContent = fmtValue(value);
    flash(cell);

    const tr = el(`tr-${key}`);
    if (tr) {
      tr.querySelector('.cell-ts').textContent =
        fmtTime(timestamp);
      tr.querySelector('.cell-count').textContent =
        String(State.updateCounts[`${server_id}:${node_id}`] ?? 0);
    }
  },
};

/* ── 통계 카드 ────────────────────────────────────────────────────────────── */

const Stats = {
  update() {
    let totalNodes = 0;
    for (const { nodeMap } of State.servers.values()) {
      totalNodes += Object.keys(nodeMap).length;
    }
    el('statServers').textContent      = String(State.servers.size);
    el('statNodeCount').textContent    = String(totalNodes);
    el('statLastUpdate').textContent   = fmtTime(State.lastUpdate);
    el('statTotalUpdates').textContent = String(State.totalUpdates);
    el('updateCounter').textContent    = `업데이트: ${State.totalUpdates}`;
  },
};

/* ── 연결 폼 ──────────────────────────────────────────────────────────────── */

const Form = {
  init() {
    el('btnAddServer').addEventListener('click', () => this._togglePanel());
    el('cancelBtn').addEventListener('click',    () => this._hidePanel());
    el('authMode').addEventListener('change',    () => this._syncVisibility());
    el('securityMode').addEventListener('change',() => this._syncVisibility());
    el('connectForm').addEventListener('submit', (e) => {
      e.preventDefault();
      this._handleConnect();
    });
    this._syncVisibility();
  },

  _togglePanel() {
    el('connectPanel').classList.contains('hidden')
      ? this._showPanel()
      : this._hidePanel();
  },

  _showPanel() {
    el('connectPanel').classList.remove('hidden');
    el('btnAddServer').textContent = '▲ 접기';
  },

  _hidePanel() {
    el('connectPanel').classList.add('hidden');
    el('btnAddServer').textContent = '+ 서버 추가';
    el('errorBox').classList.add('hidden');
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
    const btn = el('connectBtn');
    btn.disabled = true;
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
      // res: { status, server_id, node_count, tree }

      const nodeMap = Tree.buildNodeMap(res.tree ?? []);
      State.servers.set(res.server_id, { endpoint: payload.endpoint, nodeMap });

      Tree.addServer(res.server_id, payload.endpoint, res.tree ?? []);
      Table.addServer(res.server_id, payload.endpoint, nodeMap, res.tree ?? []);
      Stats.update();
      this._hidePanel();
    } catch (err) {
      const box = el('errorBox');
      box.textContent = err.message ?? '알 수 없는 오류';
      box.classList.remove('hidden');
    } finally {
      btn.disabled = false;
      btn.textContent = '연결';
      el('loadingOverlay').classList.add('hidden');
    }
  },

  /** 사이드바 × 버튼에서 호출 — 특정 서버 연결 해제 */
  async handleDisconnect(server_id) {
    try {
      await API.disconnect(server_id);
    } catch { /* 이미 끊긴 경우 무시 */ }

    // 해당 서버의 updateCounts 정리
    for (const key of Object.keys(State.updateCounts)) {
      if (key.startsWith(`${server_id}:`)) delete State.updateCounts[key];
    }

    State.servers.delete(server_id);
    Tree.removeServer(server_id);
    Table.removeServer(server_id);
    Stats.update();
  },
};

/* ── 초기화 ─────────────────────────────────────────────────────────────────  */

document.addEventListener('DOMContentLoaded', async () => {
  WS.connect();
  Form.init();
  Stats.update();
  await API.loadDefaults();
});
