// Bridge browser app - Phase 1.
// Polls /version cheaply, refetches heavy endpoints when a component
// version moves. Three-layer freshness UI. Hash-based routing.

const state = {
  token: null,
  port: null,
  user: null,
  cwd: null,
  tz: 'UTC',          // per-instance local timezone (from /_bootstrap)
  tzLabel: 'UTC',     // short display label for the timezone
  refresh: {},
  lastVersions: {},
  lastEtag: null,
  pollInterval: null,
  missedHeartbeats: 0,
  lastInboxVersion: null,   // version counter seen on last poll
  lastInboxTopId: null,     // id of the most-recent unread conversation
};

let _nextMeetingTickInterval = null;

function escapeHtml(s) {
  return (s ?? '').toString()
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

// Phase 1.120: structured breadcrumb spans matching v8 .page-eyebrow.
// All page renderers now go through this helper instead of hand-rolled
// flat strings, so the .num / .sep / .crumb / tail colour hierarchy
// stays consistent across the daemon.
//   num   - the section number ('01', '02', ...)
//   crumb - the route, e.g. 'Today · Approvals'
//   tail  - the status clause, e.g. '5 pending' (optional)
//   dataTime - ISO timestamp to surface a relative '5m ago' marker (optional)
function _breadcrumb(num, crumb, tail, dataTime) {
  let html = `<span class="num">${escapeHtml(num)}</span>`
    + `<span class="sep">&middot;</span>`
    + `<span class="crumb">${escapeHtml(crumb)}</span>`;
  if (tail) {
    html += `<span class="sep">&middot;</span><span>${escapeHtml(tail)}</span>`;
  }
  if (dataTime) {
    html += `<span class="sep">&middot;</span><span class="tstamp">${escapeHtml(formatRelative(dataTime))}</span>`;
  }
  return html;
}

// Phase 1.70: escape a string for safe interpolation into a CSS attribute
// selector (`[data-x="..."]`). Backslash-escape the quote chars and \\.
function cssEscapeAttr(s) {
  return (s ?? '').toString().replace(/\\/g, '\\\\').replace(/"/g, '\\"');
}

// Phase 1.70: shared helper for deep-link focus on a list row.
// Scrolls the row into view, runs the page's expand handler, and pulses
// a temporary highlight class so the eye lands on the right element.
function _focusRow(row, expandFn) {
  if (!row) return;
  // Defer to next frame so layout settles before measuring scroll position.
  requestAnimationFrame(() => {
    row.scrollIntoView({ behavior: 'smooth', block: 'center' });
    if (typeof expandFn === 'function') expandFn();
    row.classList.add('row-focus-pulse');
    setTimeout(() => row.classList.remove('row-focus-pulse'), 1800);
  });
}

function showToast(title, subject, onClick) {
  const container = document.getElementById('toast-container');
  if (!container) return;
  const toast = document.createElement('div');
  toast.className = 'toast';
  toast.innerHTML = `
    <div class="toast-title">${escapeHtml(title)}</div>
    <div class="toast-subject">${escapeHtml(subject)}</div>
  `;
  if (typeof onClick === 'function') {
    toast.addEventListener('click', () => {
      onClick();
      _dismissToast(toast);
    });
  }
  container.appendChild(toast);
  // Auto-dismiss after 5 seconds.
  setTimeout(() => _dismissToast(toast), 5000);
}

function _dismissToast(toast) {
  if (!toast || !toast.parentNode) return;
  toast.classList.add('toast-fade');
  // Remove from DOM after the fade animation completes.
  setTimeout(() => {
    if (toast.parentNode) toast.parentNode.removeChild(toast);
  }, 300);
}

async function bootstrap() {
  const r = await fetch('/_bootstrap');
  if (!r.ok) throw new Error(`bootstrap failed: ${r.status}`);
  const b = await r.json();
  state.token = b.token;
  state.user = b.user;
  state.cwd = b.workspace;
  state.tz = b.tz || 'UTC';
  state.tzLabel = b.tz_label || 'UTC';
  state.refresh = b.refresh || {};
}

function authFetch(path, opts = {}) {
  return fetch(path, {
    ...opts,
    headers: {
      ...(opts.headers || {}),
      'Authorization': `Bearer ${state.token}`,
    },
  });
}

async function checkVersion() {
  try {
    const r = await authFetch('/version', {
      headers: state.lastEtag ? { 'If-None-Match': state.lastEtag } : {},
    });
    if (r.status === 304) {
      state.missedHeartbeats = 0;
      setConnection('live');
      return;
    }
    if (r.status !== 200) throw new Error(`status ${r.status}`);
    state.lastEtag = r.headers.get('ETag');
    state.missedHeartbeats = 0;
    setConnection('live');
    const snap = await r.json();
    const changes = [];
    for (const [c, v] of Object.entries(snap.components || {})) {
      if (state.lastVersions[c] !== v) changes.push(c);
      state.lastVersions[c] = v;
    }
    // Phase 1.27: detect new email arrival and toast.
    await _maybeNotifyNewEmail(snap);
    if (changes.length) await renderCurrentPage();
  } catch (e) {
    state.missedHeartbeats++;
    if (state.missedHeartbeats >= 3) setConnection('stale');
    else setConnection('warn');
  }
}

// Phase 1.32: the top 'needs you' (P1/P2) conversation, or null. The
// new-email toast fires when this id changes between polls.
function _inboxTopNeedsYou(d) {
  return ((d && d.bands && d.bands['needs-you']) || [])[0] || null;
}

async function _maybeNotifyNewEmail(snap) {
  const currentVersion = snap.components ? snap.components.inbox : null;
  if (currentVersion === null || currentVersion === undefined) return;
  const prevVersion = state.lastInboxVersion;
  // First-ever poll: just record the version. Don't notify on initial load.
  if (prevVersion === null) {
    state.lastInboxVersion = currentVersion;
    // Also record current top id so we have a baseline.
    try {
      const inboxR = await authFetch('/inbox');
      if (inboxR.ok) {
        const inboxD = await inboxR.json();
        const topUnread = _inboxTopNeedsYou(inboxD);
        state.lastInboxTopId = topUnread ? topUnread.id : null;
      }
    } catch (e) { /* ignore */ }
    return;
  }
  // No bump means no new emails.
  if (currentVersion === prevVersion) return;
  // Inbox version bumped. Fetch /inbox and check for new unread.
  state.lastInboxVersion = currentVersion;
  try {
    const r = await authFetch('/inbox');
    if (!r.ok) return;
    const d = await r.json();
    const topUnread = _inboxTopNeedsYou(d);
    // No 'needs you' conversation - nothing to toast.
    if (!topUnread) {
      state.lastInboxTopId = null;
      return;
    }
    // Same top conversation as before - the bump was about something else.
    if (topUnread.id === state.lastInboxTopId) return;
    // New top-of-inbox conversation. Toast it.
    state.lastInboxTopId = topUnread.id;
    showToast(
      'New email',
      topUnread.subject || '(no subject)',
      () => { window.location.hash = '#/inbox'; },
    );
  } catch (e) {
    // Silent - toast is best-effort.
  }
}

function setConnection(status) {
  // v8 port: connection-pill is gone; reflect state via classes on the
  // sync-pill (which absorbed its role). 'syncing' tints the icon accent,
  // 'just-synced' pops it green, default is neutral.
  const pill = document.getElementById('sync-btn');
  if (!pill) return;
  pill.classList.remove('conn-live', 'conn-warn', 'conn-stale');
  pill.classList.add(`conn-${status}`);
  pill.title = status === 'live'
    ? 'Daemon live - click to refresh'
    : status === 'warn'
      ? `Reconnecting (missed ${state.missedHeartbeats}) - click to refresh`
      : 'Stale - click to refresh';
}

function formatRelative(iso) {
  if (!iso) return '-';
  const sec = (Date.now() - new Date(iso).getTime()) / 1000;
  if (sec < 60) return `${Math.round(sec)}s ago`;
  if (sec < 3600) return `${Math.round(sec / 60)}m ago`;
  return `${Math.round(sec / 3600)}h ago`;
}

// Render the topbar sync indicator (#data-time + #sync-dot + #sync-status)
// from a page API response. Uses d.data_time (when data was actually
// computed server-side), d.server_now (server clock at response time), and
// d.watching (whether the daemon actively keeps this source fresh).
//
// Clock-skew correction: browser vs daemon clocks can drift (laptop sleep,
// NTP gaps). We compute skew = Date.now() - server_now at render time, then
// translate the server's data_time into client time before subtracting. A
// browser running 2 hours ahead of the daemon would otherwise display
// "2h ago" for data that's seconds old.
//
// Status dot: 'live' when daemon has a Watchdog mapping or refresher for
// this component (data refreshes without user action), 'on-demand' for
// per-request reads (the data IS fresh but no live watch).
function renderSyncIndicator(d) {
  const tEl = document.getElementById('data-time');
  if (!tEl) return;
  // Explicit null: empty state (e.g., search page with no query). We want a
  // truly empty indicator here, not '—', because there's no data being
  // shown that could be stale.
  if (d === null) {
    tEl.textContent = '';
    const dotEl0 = document.getElementById('sync-dot');
    const sEl0 = document.getElementById('sync-status');
    if (dotEl0) dotEl0.setAttribute('data-state', 'unknown');
    if (sEl0) sEl0.textContent = '';
    return;
  }
  const dt = d && d.data_time ? new Date(d.data_time).getTime() : null;
  const srv = d && d.server_now ? new Date(d.server_now).getTime() : null;
  if (dt !== null && !isNaN(dt)) {
    let sec;
    if (srv !== null && !isNaN(srv)) {
      const skew = Date.now() - srv;
      sec = Math.max(0, (Date.now() - (dt + skew)) / 1000);
    } else {
      sec = Math.max(0, (Date.now() - dt) / 1000);
    }
    if (sec < 60) tEl.textContent = `${Math.round(sec)}s ago`;
    else if (sec < 3600) tEl.textContent = `${Math.round(sec / 60)}m ago`;
    else tEl.textContent = `${Math.round(sec / 3600)}h ago`;
  } else {
    tEl.textContent = '—';
  }
  const dotEl = document.getElementById('sync-dot');
  const sEl = document.getElementById('sync-status');
  if (dotEl && sEl) {
    if (d && d.watching === true) {
      dotEl.setAttribute('data-state', 'live');
      sEl.textContent = 'live';
    } else if (d && d.watching === false) {
      dotEl.setAttribute('data-state', 'on-demand');
      sEl.textContent = 'on-demand';
    } else {
      dotEl.setAttribute('data-state', 'unknown');
      sEl.textContent = '—';
    }
  }
}

function freshnessLevel(iso) {
  if (!iso) return 'unknown';
  const sec = (Date.now() - new Date(iso).getTime()) / 1000;
  if (sec < 30) return 'fresh';
  if (sec < 120) return 'warn';
  return 'stale';
}

function fmtMoney(n) {
  const v = Number(n) || 0;
  if (v >= 1_000_000) return `$${(v / 1_000_000).toFixed(1)}M`;
  if (v >= 1000) return `$${(v / 1000).toFixed(0)}K`;
  return `$${v.toLocaleString()}`;
}

function fmtInt(n) {
  return (Number(n) || 0).toLocaleString();
}

function fmtMinutesUntil(min) {
  if (min === null || min === undefined) return '';
  if (min <= 0) return 'now';
  if (min < 60) return `in ${min}m`;
  const h = Math.floor(min / 60);
  const m = min % 60;
  return m === 0 ? `in ${h}h` : `in ${h}h ${m}m`;
}

function nextMeetingHtml(m) {
  // Returns a SAFE HTML string for the next-meeting line.
  // All interpolations go through escapeHtml.
  if (!m || !m.time) return 'none scheduled';
  const base = `${escapeHtml(m.time)} - ${escapeHtml(m.subject || '(no subject)')}`;
  const mins = fmtMinutesUntil(m.minutes_until);
  const minsHtml = mins
    ? ` <span class="next-mins" data-event-iso="${escapeHtml(m.event_utc_iso || '')}">(${escapeHtml(mins)})</span>`
    : '';
  if (m.location && m.location.startsWith('http')) {
    return `${base} <a href="${escapeHtml(m.location)}" target="_blank" rel="noopener noreferrer" class="next-join">join</a>${minsHtml}`;
  }
  return `${base}${minsHtml}`;
}

function _tickNextMeetingMins() {
  const spans = document.querySelectorAll('.next-mins[data-event-iso]');
  if (spans.length === 0) {
    // No active next-meeting spans on the page; cancel the interval.
    if (_nextMeetingTickInterval) {
      clearInterval(_nextMeetingTickInterval);
      _nextMeetingTickInterval = null;
    }
    return;
  }
  spans.forEach(span => {
    const iso = span.dataset.eventIso;
    if (!iso) return;
    const eventMs = new Date(iso).getTime();
    if (Number.isNaN(eventMs)) return;
    const minsRemaining = Math.floor((eventMs - Date.now()) / 60000);
    if (span.dataset.focal === '1') {
      // Focal countdown: big '1h 30m' / 'now' format, no parens.
      span.textContent = _formatCountdownLarge(minsRemaining);
    } else {
      // Inline next-mins span: '(in 77m)' style.
      span.textContent = `(${fmtMinutesUntil(minsRemaining)})`;
    }
  });
}

function _timeOfDayGreeting() {
  // Pulse opens with a conversational, time-of-day greeting in the configured
  // local timezone (state.tz, injected by the daemon; UTC default).
  const hourStr = new Intl.DateTimeFormat('en-GB', {
    timeZone: state.tz, hour: '2-digit', hour12: false,
  }).format(new Date());
  const hour = parseInt(hourStr, 10) % 24;
  if (hour < 5) return 'Working late';
  if (hour < 12) return 'Good morning';
  if (hour < 17) return 'Good afternoon';
  if (hour < 22) return 'Good evening';
  return 'Working late';
}

function _pulseActivityHtml(d) {
  // Phase 1.64: today's activity recap. Shows below the subhead when
  // the CEO has touched anything today via the dashboard's mutating
  // workflows. Hidden when total = 0 to avoid noise on quiet mornings.
  // Phase 1.69: clickable to expand entries inline.
  const a = (d.kpi && d.kpi.today_activity) || null;
  if (!a || (a.total || 0) === 0) return '';
  const bits = [];
  if (a.tasks_done > 0) bits.push(`<strong>${escapeHtml(a.tasks_done)}</strong> task${a.tasks_done === 1 ? '' : 's'} done`);
  if (a.investors_sent > 0) bits.push(`<strong>${escapeHtml(a.investors_sent)}</strong> investor${a.investors_sent === 1 ? '' : 's'} sent`);
  if (a.pipeline_touched > 0) bits.push(`<strong>${escapeHtml(a.pipeline_touched)}</strong> deal${a.pipeline_touched === 1 ? '' : 's'} touched`);
  if (a.approvals_sent > 0) bits.push(`<strong>${escapeHtml(a.approvals_sent)}</strong> draft${a.approvals_sent === 1 ? '' : 's'} sent`);
  if (a.inbox_dismissed > 0) bits.push(`<strong>${escapeHtml(a.inbox_dismissed)}</strong> conversation${a.inbox_dismissed === 1 ? '' : 's'} cleared`);
  if (bits.length === 0) return '';
  // The whole line is the click target; the inner caret hints at expandability.
  return `
    <div class="pulse-activity" data-expandable="1" id="pulse-activity-line">
      <span class="pulse-activity-toggle">Today &middot; ${bits.join(' &middot; ')}.</span>
      <span class="pulse-activity-caret">&#9656;</span>
    </div>
    <div class="pulse-activity-expanded" id="pulse-activity-expanded" hidden></div>`;
}

function _pulseActivityEntriesHtml(activity) {
  // Render the expanded entries panel from kpi.today_activity.entries.
  const groups = (activity && activity.entries) || {};
  const order = [
    ['tasks_done', 'Tasks done', null],  // done tasks are filtered out of /tasks
    ['investors_sent', 'Investors sent', '#/investors'],
    ['pipeline_touched', 'Deals touched', '#/pipeline'],
    ['approvals_sent', 'Drafts sent', null],  // sent drafts are filtered out of /approvals
    ['inbox_dismissed', 'Conversations cleared', null],  // dismissed rows are hidden on /inbox
  ];
  const formatTime = (iso) => {
    if (!iso) return '';
    const m = iso.match(/T(\d{2}):(\d{2})/);
    return m ? `${m[1]}:${m[2]}` : '';
  };
  const sections = order
    .filter(([key]) => (groups[key] || []).length > 0)
    .map(([key, label, baseRoute]) => {
      const rows = groups[key].map(e => {
        // Phase 1.70: when an entry carries a navigable ref and the kind
        // owns a destination route, wrap the target as a deep-link that the
        // destination renderer picks up via ?focus=...
        const targetHtml = (baseRoute && e.ref !== '' && e.ref !== null && e.ref !== undefined)
          ? `<a class="pulse-activity-target pulse-activity-link" href="${escapeHtml(baseRoute)}?focus=${encodeURIComponent(String(e.ref))}">${escapeHtml(e.target)}</a>`
          : `<span class="pulse-activity-target">${escapeHtml(e.target)}</span>`;
        return `
          <li class="pulse-activity-entry">
            <span class="pulse-activity-time">${escapeHtml(formatTime(e.ts))}</span>
            ${targetHtml}
            ${e.note ? `<span class="pulse-activity-note">${escapeHtml(e.note)}</span>` : ''}
          </li>`;
      }).join('');
      return `
        <div class="pulse-activity-group">
          <div class="pulse-activity-group-head">${escapeHtml(label)}</div>
          <ul class="pulse-activity-list">${rows}</ul>
        </div>`;
    }).join('');
  return sections;
}

function _pulseActivityToggle() {
  const line = document.getElementById('pulse-activity-line');
  const exp = document.getElementById('pulse-activity-expanded');
  if (!line || !exp) return;
  const willOpen = exp.hidden;
  if (willOpen) {
    // Lazy-render the entries from the cached pulse payload.
    if (!exp.dataset.rendered && window._lastPulseData) {
      const a = (window._lastPulseData.kpi && window._lastPulseData.kpi.today_activity) || null;
      if (a) exp.innerHTML = _pulseActivityEntriesHtml(a);
      exp.dataset.rendered = '1';
    }
    exp.hidden = false;
    line.classList.add('pulse-activity-open');
  } else {
    exp.hidden = true;
    line.classList.remove('pulse-activity-open');
  }
}

function _pulseSubheadHtml(d) {
  // Conversational summary line. Each fragment is data-driven; the page
  // omits a fragment when its data is empty.
  const fragments = [];
  // Sea state + mood - canonical signal pair. Prefer kpi.sea_state (1.53)
  // payload; fall back to topbar dot's data-state for the state when the
  // server hasn't shipped it yet.
  const seaKpi = (d.kpi && d.kpi.sea_state) || null;
  const seaDot = document.querySelector('#sea-pill .sea-dot');
  const seaState = (seaKpi && seaKpi.state) || (seaDot && seaDot.getAttribute('data-state')) || 'calm';
  const mood = seaKpi && seaKpi.mood;
  if (mood) {
    fragments.push(`Sea state <strong>${escapeHtml(seaState)}</strong>, mood <strong>${escapeHtml(mood)}</strong>`);
  } else {
    fragments.push(`Sea state <strong>${escapeHtml(seaState)}</strong>`);
  }

  // Phase 1.56: approvals waiting.
  const approvalsTotal = (d.kpi && d.kpi.approvals_total) || 0;
  if (approvalsTotal > 0) {
    fragments.push(`<strong>${escapeHtml(approvalsTotal)} approval${approvalsTotal === 1 ? '' : 's'}</strong> waiting`);
  }

  // Next meeting fragment - same data the KPI tile uses.
  if (d.kpi.next_meeting && d.kpi.next_meeting.minutes_until !== undefined && d.kpi.next_meeting.minutes_until !== null) {
    const m = d.kpi.next_meeting;
    const mins = m.minutes_until;
    let when;
    if (mins <= 0) when = 'starting now';
    else if (mins < 60) when = `in ${mins}m`;
    else {
      const h = Math.floor(mins / 60);
      const r = mins % 60;
      when = r === 0 ? `in ${h}h` : `in ${h}h ${r}m`;
    }
    fragments.push(`${escapeHtml(m.subject || 'next meeting')} <strong>${escapeHtml(when)}</strong>`);
  }

  // Pipeline posture: holding (no overdue), shipping (Won > 0), drifting (overdue > 0).
  const overdue = d.kpi.pipeline_overdue || 0;
  const stages = d.kpi.pipeline_stages || {};
  let pipelineWord;
  if (overdue > 5) pipelineWord = 'drifting';
  else if (stages.Won && stages.Won > 0) pipelineWord = 'shipping';
  else if (overdue > 0) pipelineWord = 'flagged';
  else pipelineWord = 'holding';
  fragments.push(`pipeline <strong>${escapeHtml(pipelineWord)}</strong>`);

  // Raise fragment, if a raise is in flight.
  const rp = d.kpi.raise_progress;
  if (rp && rp.total > 0) {
    const drafts = rp.sendable_drafts || 0;
    const sent = rp.sendable_sent || 0;
    if (sent === 0 && drafts > 0) {
      fragments.push(`<strong>${escapeHtml(drafts)} first-touch drafts</strong> ready`);
    } else if (sent > 0) {
      fragments.push(`<strong>${escapeHtml(sent)} first-touches</strong> sent`);
    }
  }

  // Phase 1.113: Days-to-ODIN-5 now lives in the subhead instead of
  // its own KPI tile (CEO directive: 'put this as text in the same
  // section as Sea state ...'). Rendered with the .pulse-subhead-meta
  // class so it picks up an ink-dim colour that preserves the line's
  // readability without competing with the bold strong-emphasis.
  const dOdin = (d.kpi && d.kpi.days_to_odin_5);
  if (typeof dOdin === 'number') {
    fragments.push(`<span class="pulse-subhead-meta"><strong>${escapeHtml(dOdin)}d</strong> to ODIN-5</span>`);
  }

  // Comma after the first sea-state phrase, then middots between the rest.
  if (fragments.length <= 1) return fragments[0] + '.';
  return `${fragments[0]}. ${fragments.slice(1).join(' &middot; ')}.`;
}

function _pulseKpiTile(eyebrow, value, sublabel, opts) {
  // v8-style KPI tile: eyebrow / value / sublabel column.
  const cls = (opts && opts.cls) ? ` ${opts.cls}` : '';
  const link = (opts && opts.href) ? opts.href : null;
  const inner = `
    <div class="kpi-eyebrow">${escapeHtml(eyebrow)}</div>
    <div class="kpi-value">${value}</div>
    ${sublabel ? `<div class="kpi-sublabel">${sublabel}</div>` : ''}`;
  return link
    ? `<a class="kpi-tile${cls}" href="${escapeHtml(link)}">${inner}</a>`
    : `<div class="kpi-tile${cls}">${inner}</div>`;
}

function pulseNowHtml(now) {
  // Phase 1.105: render as a compact pill banner instead of a full-card
  // section. Only shows when a meeting is in progress (most of the day
  // it's hidden). Positioned above the hero so it reads as ambient state
  // rather than yet another section between bento and triage.
  if (!now || !now.focus) return '';
  const remain = now.minutes_remaining !== null && now.minutes_remaining !== undefined
    ? `<span class="pulse-now-remain">${escapeHtml(now.minutes_remaining)}m left</span>`
    : '';
  const untilLine = now.until ? `<span class="pulse-now-until">until ${escapeHtml(now.until)}</span>` : '';
  return `
    <div class="pulse-now-banner" data-route="#/day">
      <span class="pulse-now-pulse" aria-hidden="true"></span>
      <span class="pulse-now-label">In progress</span>
      <span class="pulse-now-focus">${escapeHtml(now.focus)}</span>
      ${untilLine}
      ${remain}
    </div>`;
}

// Phase 1.103: Pulse Next panel caps at top 3 with overflow link to /day,
// matching the Signals (1.101) and Approvals (1.102) treatment.
const PULSE_NEXT_TOP = 3;

function pulseNextHtml(next) {
  // Phase 1.121: always render the Next card. Empty state shows a quiet
  // 'Clear day. Tomorrow's first item appears here.' message instead
  // of hiding the section (CEO observed sections disappearing felt
  // broken). Server-side next_items() already falls back to tomorrow's
  // first event when today is empty; if even tomorrow is clear we
  // show the empty state.
  const items = next || [];
  if (items.length === 0) {
    return `
      <section class="pulse-section">
        <div class="pulse-section-head">
          <div class="pulse-section-eyebrow">Next</div>
          <span class="pulse-section-badge pulse-section-badge-quiet">0</span>
        </div>
        <div class="card pulse-next-card pulse-next-empty">Clear runway. Nothing scheduled today and tomorrow's calendar is empty too.</div>
      </section>`;
  }
  const top = items.slice(0, PULSE_NEXT_TOP);
  const overflow = Math.max(0, items.length - top.length);
  const rows = top.map(item => {
    // Phase 1.121: tomorrow marker - server-side surfaces is_next_day
    // when it falls back to tomorrow's calendar. Renders as a small
    // chip in front of the time so the CEO can't mistake it for today.
    const dayBadge = item.is_next_day
      ? `<span class="pulse-next-day-badge">${escapeHtml(item.day_label || 'Tomorrow')}</span>`
      : '';
    if (item.kind === 'meeting') {
      const loc = item.location && item.location.startsWith('http')
        ? `<a href="${escapeHtml(item.location)}" target="_blank" rel="noopener noreferrer" class="next-join" onclick="event.stopPropagation()">join</a>`
        : '';
      return `
        <div class="pulse-next-item${item.is_next_day ? ' pulse-next-item-future' : ''}">
          ${dayBadge}
          <span class="pulse-next-time">${escapeHtml(item.time)}</span>
          <span class="pulse-next-label">${escapeHtml(item.label)}</span>
          ${loc}
        </div>`;
    }
    return `
      <div class="pulse-next-item${item.is_next_day ? ' pulse-next-item-future' : ''}">
        ${dayBadge}
        <span class="pulse-next-time">${escapeHtml(item.priority || 'task')}</span>
        <span class="pulse-next-label">${escapeHtml(item.label)}</span>
      </div>`;
  }).join('');
  const footer = overflow > 0
    ? `<a class="pulse-next-more" href="#/day" onclick="event.stopPropagation()">${escapeHtml(overflow)} more upcoming &rarr;</a>`
    : `<a class="pulse-next-more pulse-next-more-quiet" href="#/day" onclick="event.stopPropagation()">Open Day &rarr;</a>`;
  // Phase 1.108: section-head with neutral count chip (matches the v8
  // grouping pattern used by Approvals + Signals). Next is non-urgent
  // by nature, so the badge uses the muted surface-2 colour rather
  // than accent or warn.
  // Phase 1.121: aside text gives extra context for the tomorrow case.
  const isNextDay = items.some(it => it.is_next_day);
  const aside = isNextDay
    ? '<span class="pulse-section-aside">next day</span>'
    : '';
  return `
    <section class="pulse-section">
      <div class="pulse-section-head">
        <div class="pulse-section-eyebrow">Next</div>
        <span class="pulse-section-badge pulse-section-badge-quiet">${escapeHtml(items.length)}</span>
        ${aside}
      </div>
      <div class="card pulse-next-card" data-route="#/day">${rows}${footer}</div>
    </section>`;
}

// Phase 1.128: hero triage row replaces the old Signals/Watch cards
// (CEO flagged them as redundant - Critical Signals already in the
// hero right column, Watch was just a count rollup). The new row is
// Next | Threads | Inbox urgent.

const PULSE_INBOX_URGENT_TOP = 3;

async function pulseInboxUrgentHtml() {
  // Fetches /inbox and surfaces the top 3 'now'-zone messages. Same
  // section-head + badge + overflow-link pattern as the other triage
  // cards. Returns '' on fetch failure (the slot collapses).
  let d;
  try {
    const r = await authFetch('/inbox');
    if (!r.ok) return '';
    d = await r.json();
  } catch (e) {
    return '';
  }
  // Phase 1.32: the 'needs you' band (P1/P2) is the urgent set.
  const nowItems = (d && d.bands && d.bands['needs-you']) || [];
  const top = nowItems.slice(0, PULSE_INBOX_URGENT_TOP);
  const overflow = Math.max(0, nowItems.length - top.length);
  // Empty-state path - always render the card so the layout is stable.
  if (top.length === 0) {
    return `
      <section class="pulse-section">
        <div class="pulse-section-head">
          <div class="pulse-section-eyebrow">Inbox urgent</div>
          <span class="pulse-section-badge pulse-section-badge-quiet">0</span>
        </div>
        <div class="card pulse-inbox-urgent-card">
          <div class="pulse-inbox-urgent-empty">Inbox clear. No urgent conversations right now.</div>
          <a class="pulse-inbox-urgent-more pulse-inbox-urgent-more-quiet" href="#/inbox" onclick="event.stopPropagation()">Open Inbox &rarr;</a>
        </div>
      </section>`;
  }
  const items = top.map(m => `
    <a class="pulse-inbox-urgent-item" href="#/inbox" onclick="event.stopPropagation()">
      <span class="pulse-inbox-urgent-source">EMAIL</span>
      <div class="pulse-inbox-urgent-body">
        <div class="pulse-inbox-urgent-subject"><strong>${escapeHtml(m.subject || '(no subject)')}</strong></div>
        <div class="pulse-inbox-urgent-when">${escapeHtml(formatRelative(m.latest_datetime))}</div>
      </div>
    </a>`).join('');
  const footer = overflow > 0
    ? `<a class="pulse-inbox-urgent-more" href="#/inbox" onclick="event.stopPropagation()">${escapeHtml(overflow)} more &rarr;</a>`
    : `<a class="pulse-inbox-urgent-more pulse-inbox-urgent-more-quiet" href="#/inbox" onclick="event.stopPropagation()">Open Inbox &rarr;</a>`;
  return `
    <section class="pulse-section">
      <div class="pulse-section-head">
        <div class="pulse-section-eyebrow">Inbox urgent</div>
        <span class="pulse-section-badge pulse-section-badge-warn">${escapeHtml(nowItems.length)}</span>
        <span class="pulse-section-aside">need you now</span>
      </div>
      <div class="card pulse-inbox-urgent-card">${items}${footer}</div>
    </section>`;
}

const PULSE_ACTIONQ_TOP = 3;

// Surfaces Action Queue items awaiting the CEO's approval (gated outbound
// sends - the lethal-trifecta human gate). Pulse is where Misha lives (~68%
// of views), so a pending send must be visible here, not one click away.
// Returns '' when nothing is awaiting approval so the slot collapses - no
// clutter when the queue is clear.
async function pulseActionQueueHtml() {
  let d;
  try {
    const r = await authFetch('/action-queue');
    if (!r.ok) return '';
    d = await r.json();
  } catch (e) {
    return '';
  }
  const isGatedSend = it => it && it.status === 'pending'
    && (it.tier === 'gated' || it.action_type === 'email_send' || it.action_type === 'telegram_send');
  const pending = (d.items || []).filter(isGatedSend);
  if (pending.length === 0) return '';  // collapse when clear
  const top = pending.slice(0, PULSE_ACTIONQ_TOP);
  const overflow = Math.max(0, pending.length - top.length);
  const label = at => (at === 'telegram_send' ? 'TELEGRAM' : 'EMAIL');
  const items = top.map(it => `
    <a class="pulse-inbox-urgent-item" href="#/action-queue" onclick="event.stopPropagation()">
      <span class="pulse-inbox-urgent-source">${escapeHtml(label(it.action_type))}</span>
      <div class="pulse-inbox-urgent-body">
        <div class="pulse-inbox-urgent-subject"><strong>${escapeHtml(it.title || '(untitled)')}</strong></div>
        <div class="pulse-inbox-urgent-when">${escapeHtml(it.priority || '')} &middot; awaiting your approval</div>
      </div>
    </a>`).join('');
  const footer = overflow > 0
    ? `<a class="pulse-inbox-urgent-more" href="#/action-queue" onclick="event.stopPropagation()">${escapeHtml(overflow)} more &rarr;</a>`
    : `<a class="pulse-inbox-urgent-more" href="#/action-queue" onclick="event.stopPropagation()">Open Action Queue &rarr;</a>`;
  return `
    <section class="pulse-section">
      <div class="pulse-section-head">
        <div class="pulse-section-eyebrow">Awaiting approval</div>
        <span class="pulse-section-badge pulse-section-badge-warn">${escapeHtml(pending.length)}</span>
        <span class="pulse-section-aside">gated sends need your click</span>
      </div>
      <div class="card pulse-inbox-urgent-card">${items}${footer}</div>
    </section>`;
}

// Phase 1.128: Threads moves from the footer row up to the triage row.
// Reuses the existing pulseThreadsHtml output (already styled as a
// .pulse-footer-card); the parent slot gets a small CSS override so
// it inherits the triage row's column treatment.
function pulseSuggestedHtml(d) {
  // Phase 1.94: rule-based 'Suggested for now' panel - what to do next,
  // grounded in current workspace state.
  // Phase 1.112: group items into 'WHY NOW · STALLED DEALS' and
  // 'WHY NOW · THIS WEEK' sections per the v8 reference.
  // Phase 1.118: always render the card; empty data shows a quiet
  // 'queue clear' state instead of vanishing (CEO noticed sections
  // disappearing when rules didn't fire).
  const items = (d && d.suggested) || [];
  if (items.length === 0) {
    return `
      <article class="card pulse-footer-card pulse-suggested-card">
        <div class="pulse-footer-head">
          <span class="pulse-footer-title">Suggested for now</span>
          <span class="pulse-footer-count">based on today's state</span>
        </div>
        <div class="pulse-suggested-empty">Nothing pressing. Pipeline is on heading, no overdue actions, queue clear.</div>
      </article>`;
  }

  const stalledAgents = new Set(['/follow-up', '/deal-strategy']);
  const weeklyAgents = new Set(['/meeting-prep', '/tribe-monday', '/tribe-message']);
  const buckets = { stalled: [], weekly: [], urgent: [] };
  for (const it of items) {
    const a = it.agent || '';
    if (stalledAgents.has(a)) buckets.stalled.push(it);
    else if (weeklyAgents.has(a)) buckets.weekly.push(it);
    else buckets.urgent.push(it);
  }

  // Phase 1.114: agent slug -> v8 [data-agent] token. Drives the
  // coloured dot inside each chip (followup=orange, deal=magenta,
  // meeting=green, content=teal, osint=cyan, voss=yellow, etc).
  const _agentToken = a => {
    if (!a) return 'content';
    if (a.includes('follow-up') || a.includes('email-respond')) return 'followup';
    if (a.includes('deal-strategy') || a.includes('voss')) return 'deal';
    if (a.includes('meeting')) return 'meeting';
    if (a.includes('tribe') || a.includes('linkedin')) return 'content';
    if (a.includes('osint')) return 'osint';
    if (a.includes('intel')) return 'intel';
    if (a.includes('tasks')) return 'meeting';
    return 'content';
  };
  const renderRow = it => `
    <a class="pulse-suggested-row" href="${escapeHtml(it.link || '#/pulse')}" onclick="event.stopPropagation()">
      <span class="pulse-suggested-agent" data-agent="${escapeHtml(_agentToken(it.agent))}">${escapeHtml(it.agent || '')}</span>
      <span class="pulse-suggested-reason">${escapeHtml(it.reason || '')}</span>
      <span class="pulse-suggested-arrow">&rarr;</span>
    </a>`;
  const renderSection = (label, bucket) => bucket.length === 0 ? '' : `
    <div class="pulse-suggested-section">
      <div class="pulse-suggested-divider">Why now &middot; ${escapeHtml(label)}</div>
      ${bucket.map(renderRow).join('')}
    </div>`;

  const body = [
    renderSection('Stalled deals', buckets.stalled),
    renderSection('This week', buckets.weekly),
    renderSection('Urgent', buckets.urgent),
  ].filter(Boolean).join('');

  return `
    <article class="card pulse-footer-card pulse-suggested-card">
      <div class="pulse-footer-head">
        <span class="pulse-footer-title">Suggested for now</span>
        <span class="pulse-footer-count">based on today's state</span>
      </div>
      <div class="pulse-suggested-list">${body}</div>
    </article>`;
}

function pulseRecentOutputsHtml(d) {
  // Phase 1.93: v8 'Recent outputs' panel - top-5 in-flight items from
  // /studio surfaced compactly on the Pulse footer.
  // Phase 1.118: always render so the visual layout is stable even
  // when studio has no recent items in the last 7 days.
  const items = (d && d.recent_outputs) || [];
  if (items.length === 0) {
    return `
      <article class="card pulse-footer-card pulse-recent-card" data-route="#/studio">
        <div class="pulse-footer-head">
          <span class="pulse-footer-title">Recent outputs</span>
          <span class="pulse-footer-count">last 7 days</span>
        </div>
        <div class="pulse-recent-empty">No deliverables shipped in the last 7 days. Open Studio &rarr;</div>
      </article>`;
  }
  // Strip path noise to a human-readable label. The studio source's
  // 'name' field is the filename; we humanise it modestly.
  const _label = (it) => {
    const raw = it.name || it.path || '';
    return raw
      .replace(/\.(md|html|pdf|docx|pptx|json|txt|py)$/i, '')
      .replace(/^[0-9]{4}-[0-9]{2}-[0-9]{2}[_-]?/, '')
      .replace(/[-_]+/g, ' ')
      .trim() || raw;
  };
  const _shortDate = (iso) => {
    if (!iso) return '';
    const m = iso.match(/^(\d{4})-(\d{2})-(\d{2})/);
    return m ? `${m[1]}-${m[2]}-${m[3]}` : iso;
  };
  const rows = items.map(it => `
    <a class="pulse-recent-row" href="#/studio" onclick="event.stopPropagation()">
      <span class="pulse-recent-cat" data-cat="${escapeHtml(it.category || 'other')}">${escapeHtml(it.category || 'other')}</span>
      <span class="pulse-recent-label">${escapeHtml(_label(it))}</span>
      <span class="pulse-recent-date">${escapeHtml(_shortDate(it.mtime))}</span>
    </a>`).join('');
  // Phase 1.104: explicit overflow link to /studio for consistency with
  // the other Pulse panels (Signals 1.101, Approvals 1.102, Next 1.103).
  // The card-level data-route still catches background clicks; the link
  // gives a discoverable text path for keyboard/screen-reader users.
  const moreLink = `<a class="pulse-recent-more" href="#/studio" onclick="event.stopPropagation()">View all in Studio &rarr;</a>`;
  return `
    <article class="card pulse-footer-card pulse-recent-card" data-route="#/studio">
      <div class="pulse-footer-head">
        <span class="pulse-footer-title">Recent outputs</span>
        <span class="pulse-footer-count">last ${escapeHtml(items.length)}</span>
      </div>
      <div class="pulse-recent-list">${rows}</div>
      ${moreLink}
    </article>`;
}

function pulseThreadsHtml(d) {
  const ts = d.kpi && d.kpi.threads_state;
  if (!ts || !ts.threads || ts.threads.length === 0) return '';
  // Phase 1.77: each row deep-links to /threads?focus=ID via the existing
  // focus-and-expand handler.
  const rows = ts.threads.map(t => {
    const meta = (t.days_since === null || t.days_since === undefined)
      ? 'no date'
      : (t.days_since === 0 ? 'today' : `${t.days_since}d ago`);
    const href = `#/threads?focus=${encodeURIComponent(String(t.id || ''))}`;
    return `
      <a class="pulse-thread-row pulse-thread-link" href="${escapeHtml(href)}" onclick="event.stopPropagation()">
        <span class="pulse-thread-title">${escapeHtml(t.title)}</span>
        <span class="pulse-thread-meta">${escapeHtml(meta)}</span>
      </a>`;
  }).join('');
  return `
    <article class="card pulse-footer-card pulse-threads-card" data-route="#/threads">
      <div class="pulse-footer-head">
        <span class="pulse-footer-title">Threads</span>
        <span class="pulse-footer-count">${escapeHtml(ts.active_total)} active</span>
      </div>
      <div class="pulse-thread-list">${rows}</div>
    </article>`;
}

function pulseTribeHtml(d) {
  const ts = d.kpi && d.kpi.tribe_state;
  if (!ts || !ts.members || ts.members.length === 0) return '';
  // Phase 1.77: each row deep-links to /tribe?focus=SLUG.
  const rows = ts.members.map(m => {
    const meta = m.days_since === null || m.days_since === undefined
      ? escapeHtml(m.role || '')
      : `${escapeHtml(m.role || '')} &middot; ${escapeHtml(m.days_since)}d`;
    const href = `#/tribe?focus=${encodeURIComponent(String(m.slug || ''))}`;
    return `
      <a class="pulse-tribe-row pulse-tribe-link" href="${escapeHtml(href)}" onclick="event.stopPropagation()">
        <span class="pulse-tribe-dot" data-presence="${escapeHtml(m.presence)}"></span>
        <span class="pulse-tribe-name">${escapeHtml(m.name)}</span>
        <span class="pulse-tribe-meta">${meta}</span>
      </a>`;
  }).join('');
  return `
    <article class="card pulse-footer-card pulse-tribe-card" data-route="#/tribe">
      <div class="pulse-footer-head">
        <span class="pulse-footer-title">Tribe state</span>
        <span class="pulse-footer-count">${escapeHtml(ts.on_watch)} / ${escapeHtml(ts.total)} on watch</span>
      </div>
      <div class="pulse-tribe-list">${rows}</div>
    </article>`;
}

async function _pulseApprovalToggle(rowEl) {
  // Inline-expand the draft body. Pattern matches the other 8 drill-downs.
  const existing = document.querySelector('.pulse-approval-expanded');
  if (existing) {
    const isThisRow = existing.previousElementSibling === rowEl;
    existing.remove();
    if (isThisRow) return;
  }
  const path = rowEl.dataset.path;
  const title = rowEl.dataset.title || '(draft)';
  const to = rowEl.dataset.to || '';
  const subject = rowEl.dataset.subject || '';
  if (!path) return;
  const panel = document.createElement('div');
  panel.className = 'card pulse-approval-expanded';
  panel.innerHTML = '<div class="pulse-approval-loading">Loading...</div>';
  rowEl.insertAdjacentElement('afterend', panel);
  try {
    const r = await authFetch(`/approvals/draft?path=${encodeURIComponent(path)}`);
    if (!r.ok) {
      panel.innerHTML = `<div class="pulse-approval-loading">Failed to load: HTTP ${escapeHtml(r.status)}</div>`;
      return;
    }
    const d = await r.json();
    panel.innerHTML = `
      <div class="pulse-approval-head">
        <span class="pulse-approval-title-large">${escapeHtml(title)}</span>
        <span class="pulse-approval-path">${escapeHtml(d.path)}</span>
      </div>
      <div class="pulse-approval-headers">
        <div><strong>To:</strong> ${escapeHtml(to)}</div>
        <div><strong>Subject:</strong> ${escapeHtml(subject)}</div>
      </div>
      <pre class="pulse-approval-body-pre">${escapeHtml(d.content)}</pre>
      <div class="pulse-approval-actions">
        <input class="pulse-approval-note" type="text" maxlength="200"
               placeholder="Optional note (channel, recipient, ...)" />
        <button class="pulse-approval-mark-btn" data-path="${escapeHtml(d.path)}">Mark sent</button>
      </div>
      <div class="pulse-approval-foot">Sending stays manual via <code>scripts/send-email.py</code>. Mark removes the draft from the queue.</div>`;
    const btn = panel.querySelector('.pulse-approval-mark-btn');
    if (btn) btn.addEventListener('click', (e) => {
      e.stopPropagation();
      _pulseApprovalMarkSent(panel, btn);
    });
  } catch (e) {
    panel.innerHTML = `<div class="pulse-approval-loading">Failed to load.</div>`;
  }
}

async function _pulseApprovalInlineMarkSent(btn) {
  // Phase 1.89: one-click mark-sent from the Pulse approval row (no drill-down).
  const path = btn.dataset.path;
  if (!path) return;
  btn.disabled = true;
  btn.textContent = '...';
  try {
    const r = await authFetch('/approvals/mark-sent', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path, note: '' }),
    });
    if (!r.ok) {
      btn.textContent = 'Retry';
      btn.disabled = false;
      showToast('Mark sent failed', 'check daemon log');
      return;
    }
    showToast('Draft cleared', path.split('/').pop());
    renderCurrentPage();
  } catch (e) {
    btn.textContent = 'Retry';
    btn.disabled = false;
    showToast('Mark sent failed', 'check daemon log');
  }
}

async function _pulseApprovalMarkSent(panelEl, btn) {
  // Phase 1.71: clear a draft from the approvals queue after manual send.
  const path = btn.dataset.path;
  if (!path) return;
  const noteInput = panelEl.querySelector('.pulse-approval-note');
  const note = noteInput ? noteInput.value.trim() : '';
  btn.disabled = true;
  btn.textContent = 'Saving...';
  try {
    const r = await authFetch('/approvals/mark-sent', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path, note }),
    });
    if (!r.ok) {
      btn.textContent = 'Failed - retry';
      btn.disabled = false;
      showToast('Mark sent failed', 'check daemon log');
      return;
    }
    const d = await r.json();
    showToast('Draft cleared', `${path.split('/').pop()} - ${d.date}`);
    renderCurrentPage();
  } catch (e) {
    btn.textContent = 'Failed - retry';
    btn.disabled = false;
    showToast('Mark sent failed', 'check daemon log');
  }
}

async function pulseApprovalsHtml() {
  // Fetch /approvals lazily; if empty, return ''. The outer caller already
  // skips us when kpi.approvals_total === 0 to avoid the extra round-trip.
  try {
    const r = await authFetch('/approvals');
    if (!r.ok) return '';
    const d = await r.json();
    if (!d.items || d.items.length === 0) return '';
    // Phase 1.96: v8 approval-card layout - 2-col x 3-row grid so the
    // subject doesn't compete with the action button for horizontal
    // space in the narrow Pulse right column. Row 1: chip + title;
    // row 2: meta (full-width); row 3: actions (right-aligned).
    // Phase 1.102: cap the Pulse-embedded list at the top 3 most-recent
    // drafts; overflow link routes to the full /approvals page.
    const PULSE_APPROVALS_TOP = 3;
    const top = d.items.slice(0, PULSE_APPROVALS_TOP);
    const overflow = Math.max(0, d.total - top.length);
    // Phase 1.106: classify each row by source agent. Only signal we have
    // is the filename / title - 'follow-up' fires 'followup', everything
    // else defaults to 'email-respond'. The CSS uses [data-agent] to pick
    // the dot colour (green for respond, orange for follow-up).
    const _agentFor = it => {
      const hay = `${it.path || ''} ${it.filename || ''} ${it.title || ''}`.toLowerCase();
      if (hay.includes('follow-up') || hay.includes('followup')) return 'followup';
      return 'email-respond';
    };
    const _agentLabel = a => a === 'followup' ? '/follow-up' : '/email-respond';
    const items = top.map(it => {
      const agent = _agentFor(it);
      return `
      <div class="pulse-approval-item" data-agent="${escapeHtml(agent)}"
           data-path="${escapeHtml(it.path)}"
           data-title="${escapeHtml(it.title)}"
           data-to="${escapeHtml(it.to)}"
           data-subject="${escapeHtml(it.subject)}">
        <span class="pulse-approval-kind" data-agent="${escapeHtml(agent)}">${escapeHtml(_agentLabel(agent))}</span>
        <div class="pulse-approval-subject">${escapeHtml(it.subject || it.title)}</div>
        <div class="pulse-approval-meta">to ${escapeHtml(it.to || '-')} &middot; ${escapeHtml(formatRelative(it.mtime))}</div>
        <div class="pulse-approval-actions-row">
          <button class="pulse-approval-inline-mark" title="Mark this draft sent" data-path="${escapeHtml(it.path)}">Mark sent</button>
          <a class="pulse-approval-edit" href="#/approvals" onclick="event.stopPropagation()" title="Open in Approvals">Edit</a>
        </div>
      </div>`;
    }).join('');
    const footer = overflow > 0
      ? `<a class="pulse-approvals-more" href="#/approvals" onclick="event.stopPropagation()">${escapeHtml(overflow)} more draft${overflow === 1 ? '' : 's'} waiting &rarr;</a>`
      : `<a class="pulse-approvals-more pulse-approvals-more-quiet" href="#/approvals" onclick="event.stopPropagation()">Open Approvals &rarr;</a>`;
    // Phase 1.106: header uses a real badge chip (replaces 'Approvals waiting · N')
    return `
      <section class="pulse-section">
        <div class="pulse-section-head">
          <div class="pulse-section-eyebrow">Approvals waiting</div>
          <span class="pulse-approvals-badge">${escapeHtml(d.total)}</span>
        </div>
        <div class="card pulse-approvals-card">${items}${footer}</div>
      </section>`;
  } catch (e) {
    return '';
  }
}

function _formatCountdownLarge(min) {
  // 'X hr Y min' style for the focal countdown. <1m -> 'now'.
  if (min === null || min === undefined || Number.isNaN(min)) return '';
  if (min <= 0) return 'now';
  if (min < 60) return `${min}m`;
  const h = Math.floor(min / 60);
  const m = min % 60;
  return m === 0 ? `${h}h` : `${h}h ${m}m`;
}

function pulseNextFocalHtml(d) {
  // The v8 hero countdown card. Sits at the top of Pulse just below the
  // greeting. When there's no upcoming meeting today, we omit the card
  // (the KPI grid still tells the operational story).
  const m = d.kpi && d.kpi.next_meeting;
  if (!m || !m.subject) return '';
  // Top meta strip: time-of-day (local) + sea + mood (with inline dot).
  const seaKpi = (d.kpi && d.kpi.sea_state) || {};
  const dataTimeAbbr = formatRelative(d.data_time);

  // Phase 1.106: v8 meta line format - time + tz label + sea-dot + sea state + mood
  const metaBits = [];
  if (m.time) metaBits.push(`<span>${escapeHtml(m.time)}</span>`);
  metaBits.push(`<span>${escapeHtml(state.tzLabel)}</span>`);
  if (seaKpi.state) {
    metaBits.push(`<span class="pulse-focal-sea"><span class="sea-dot" data-state="${escapeHtml(seaKpi.state)}"></span>sea ${escapeHtml(seaKpi.state)}</span>`);
  }
  if (seaKpi.mood) metaBits.push(`<span>mood ${escapeHtml(seaKpi.mood)}</span>`);

  const subjectClean = m.subject.replace(/\s+/g, ' ').trim();

  // Phase 1.106: v8 chip - 'Zoom · 60 min' style. Bridge calendar feed
  // doesn't expose meeting duration, so we show the source only (Zoom /
  // Teams / Google Meet / Video) when the location is a URL.
  let locChip = '';
  const loc = m.location || '';
  if (loc.startsWith('http')) {
    let kind = 'Video';
    if (loc.includes('zoom')) kind = 'Zoom';
    else if (loc.includes('teams')) kind = 'Teams';
    else if (loc.includes('meet.google')) kind = 'Google Meet';
    locChip = `<span class="pulse-focal-chip">${escapeHtml(kind)}</span>`;
  } else if (loc) {
    locChip = `<span class="pulse-focal-chip">${escapeHtml(loc)}</span>`;
  }

  // Phase 1.106: v8 action button row - Join (primary, accent-filled),
  // Pre-call notes (secondary outlined, routes to /day so the row's
  // expand panel + meeting-prep link is one click away).
  const joinBtn = loc.startsWith('http')
    ? `<a class="pulse-focal-btn pulse-focal-btn-primary" href="${escapeHtml(loc)}" target="_blank" rel="noopener noreferrer" onclick="event.stopPropagation()">Join ${escapeHtml(loc.includes('zoom') ? 'Zoom' : loc.includes('teams') ? 'Teams' : 'meeting')}</a>`
    : '';
  const preCallBtn = `<a class="pulse-focal-btn pulse-focal-btn-secondary" href="#/day" onclick="event.stopPropagation()">Pre-call notes</a>`;

  // Phase 1.106: v8 'Voss prep' secondary link sits below the buttons.
  // Routes to /day (Pulse hero is read-only; deeper prep happens via
  // the /voss skill from the CEO's terminal).
  const vossLink = `<a class="pulse-focal-link" href="#/day" onclick="event.stopPropagation()">Voss prep</a>`;

  // Countdown - re-uses event_utc_iso so the existing tick handler can
  // update it in place. Selector matches .next-mins[data-event-iso].
  const initialCountdown = m.minutes_until !== null && m.minutes_until !== undefined
    ? _formatCountdownLarge(m.minutes_until)
    : '';

  return `
    <section class="pulse-focal-card card">
      <div class="pulse-focal-meta">
        <span class="pulse-focal-meta-bits">${metaBits.join(' &middot; ')}</span>
        <span class="pulse-focal-data-time">${escapeHtml(dataTimeAbbr)}</span>
      </div>
      <div class="pulse-focal-body">
        <div class="pulse-focal-countdown">
          <span class="pulse-focal-number next-mins" data-event-iso="${escapeHtml(m.event_utc_iso || '')}" data-focal="1">${escapeHtml(initialCountdown)}</span>
          <span class="pulse-focal-label">UNTIL NEXT</span>
        </div>
        <div class="pulse-focal-meeting">
          <div class="pulse-focal-subject">${escapeHtml(subjectClean)}</div>
          ${locChip ? `<div class="pulse-focal-chip-row">${locChip}</div>` : ''}
          <div class="pulse-focal-actions">
            ${joinBtn}
            ${preCallBtn}
          </div>
          <div class="pulse-focal-aux">${vossLink}</div>
        </div>
      </div>
    </section>`;
}

function pulsePipelineHtml(d) {
  // Horizontal bar chart by stage. The widest stage owns the full bar
  // width; other stages scale proportionally. Click any row -> /pipeline.
  const stages = d.kpi.pipeline_stages || {};
  const order = ['Won', 'Negotiation', 'Proposal', 'Demo/POC', 'Qualified', 'Lead'];
  const entries = order.filter(s => (stages[s] || 0) > 0).map(s => [s, stages[s]]);
  if (entries.length === 0) return '';
  const max = Math.max(...entries.map(([, n]) => n));
  const total = entries.reduce((acc, [, n]) => acc + n, 0);
  const stageClassFor = s => `pulse-stage-${s.toLowerCase().replace(/[^a-z]+/g, '-')}`;
  const rows = entries.map(([s, n]) => {
    const pct = max > 0 ? Math.round((n / max) * 100) : 0;
    return `
      <div class="pulse-bar-row">
        <span class="pulse-bar-label">${escapeHtml(s)}</span>
        <span class="pulse-bar-track">
          <span class="pulse-bar-fill ${stageClassFor(s)}" data-pct="${escapeHtml(pct)}"></span>
        </span>
        <span class="pulse-bar-count">${escapeHtml(n)}</span>
      </div>`;
  }).join('');
  return `
    <article class="card pulse-footer-card pulse-pipeline-card" data-route="#/pipeline">
      <div class="pulse-footer-head">
        <span class="pulse-footer-title">Pipeline</span>
        <span class="pulse-footer-count">${escapeHtml(total)} active</span>
      </div>
      <div class="pulse-bar-list">${rows}</div>
    </article>`;
}

// Critical Signals UI removed 2026-06-22 (CEO: the section read as all-red
// noise on long-running Demo/POC deals - the signals() heuristic flags any
// forward-stage deal older than 30 days, but 31C POCs legitimately run
// 50-130 days). The signals() SOURCE is kept (it still feeds the
// Suggested-for-now panel); only the dashboard surfaces were removed:
// the hero Critical-Signals card, the /signals page, and the KPI red count.

function pulseWatchHtml(watch) {
  if (!watch || watch.length === 0) return '';
  const sevClass = {
    red: 'pulse-watch-red',
    yellow: 'pulse-watch-yellow',
  };
  const items = watch.map(w => {
    const cls = sevClass[w.severity] || 'pulse-watch-default';
    const link = w.link
      ? `<a href="${escapeHtml(w.link)}">${escapeHtml(w.count)} ${escapeHtml(w.label)}</a>`
      : `${escapeHtml(w.count)} ${escapeHtml(w.label)}`;
    return `<div class="pulse-watch-item ${cls}">${link}</div>`;
  }).join('');
  // Phase 1.108: Watch card uses the section-head pattern. Badge colour
  // tracks the most severe item (red -> warn, anything else -> quiet)
  // so the eye lands on a Watch with red items immediately.
  const hasRed = watch.some(w => w.severity === 'red');
  const badgeCls = hasRed ? 'pulse-section-badge-warn' : 'pulse-section-badge-quiet';
  return `
    <section class="pulse-section">
      <div class="pulse-section-head">
        <div class="pulse-section-eyebrow">Watch</div>
        <span class="pulse-section-badge ${badgeCls}">${escapeHtml(watch.length)}</span>
      </div>
      <div class="card pulse-watch-card">${items}</div>
    </section>`;
}

async function renderPulse() {
  const r = await authFetch('/pulse');
  if (!r.ok) {
    document.getElementById('canvas').innerHTML = '<div class="card">Failed to load Pulse.</div>';
    return;
  }
  const d = await r.json();
  // Cache the pulse payload so the Today-activity toggle can lazy-render
  // entries without a second round-trip.
  window._lastPulseData = d;

  // Stage rollup sub-label for the deals tile.
  const stageOrder = ['Won', 'Negotiation', 'Proposal', 'Demo/POC', 'Qualified', 'Lead'];
  const stageAbbrev = {Won: 'Won', Negotiation: 'Neg', Proposal: 'Prop', 'Demo/POC': 'Demo', Qualified: 'Qual', Lead: 'Lead'};
  const stages = d.kpi.pipeline_stages || {};
  const stageBits = stageOrder
    .filter(s => (stages[s] || 0) > 0)
    .map(s => `${escapeHtml(stageAbbrev[s])} ${escapeHtml(stages[s])}`)
    .join(' · ');

  // Phase 1.110: bento KPI grid rebuilt to v8 reference image. Four
  // fixed tiles, no conditional Overdue/Raise/Sea-State variants. Sea
  // State lived in the bento grid through Phase 1.95 but duplicated
  // the topbar sea-pill, so the CEO dropped it (2026-05-19). Tiles
  // ordered to match v8 reference image:
  //   1. PIPELINE · COMMITTED (featured) - $ value + stage rollup
  //   2. DEALS IN FLIGHT       - active-deals count + stage flow
  //   3. DRAFTS PENDING        - approvals queue count
  //   4. DAYS TO ODIN-5        - countdown + 'target landfall'
  const tiles = [];

  // Featured tile: pipeline committed value. Sublabel leads with the
  // stage rollup so the eye lands on 'where the value sits' rather
  // than the active-deals count (the count lives in tile 2).
  //
  // Phase 1.110 (fix): the tile itself is an <a>; inner <a> in the
  // sublabel triggers the HTML parser's nested-anchor recovery, which
  // re-parents the inner link as a sibling of the outer <a> and turns
  // it into a phantom grid cell. Result was a 5-cell layout in a
  // 4-column track and Days-to-ODIN-5 wrapping to row 2. Keep the
  // sublabel pure text - tile click already routes to /pipeline.
  const dealsBit = `${escapeHtml(fmtInt(d.kpi.active_deals))} deal${d.kpi.active_deals === 1 ? '' : 's'}`;
  const featuredSub = stageBits
    ? `${stageBits} &middot; ${dealsBit}`
    : dealsBit;
  tiles.push(_pulseKpiTile(
    'Pipeline',
    `<strong>${escapeHtml(fmtMoney(d.kpi.pipeline_value))}</strong>`,
    featuredSub,
    { href: '#/pipeline', cls: 'kpi-tile-featured' }
  ));

  // Deals in flight: total active deals across the pipeline. Sublabel
  // surfaces the two most-loaded stages as 'Stage X · Stage Y' so the
  // CEO sees where the volume is concentrated at a glance.
  const topStages = stageOrder
    .filter(s => (stages[s] || 0) > 0)
    .slice(0, 2)
    .map(s => `${escapeHtml(s)} ${escapeHtml(stages[s])}`)
    .join(' &middot; ');
  tiles.push(_pulseKpiTile(
    'Deals in flight',
    `<strong>${escapeHtml(fmtInt(d.kpi.active_deals))}</strong>`,
    topStages || 'no active deals',
    { href: '#/pipeline' }
  ));

  // Phase 1.131: third tile is NEEDS YOU - the systemic attention
  // queue. CEO directive (2026-05-19): 'I need to know what is
  // important, what I need to do, what I need to put my attention
  // to, what is critical, what requires my attention, what is the
  // deadline today'.
  //
  // Today's actions tile (1.130) was backward-looking; the CEO wants
  // forward-looking. NEEDS YOU sums the auto-derived attention items:
  //   - overdue deals (kpi.pipeline_overdue)
  //   - drafts waiting for approval (kpi.approvals_total)
  //
  // Complement to Important (manual flag) - NEEDS YOU is the system's
  // call on what should grab attention; Important is the CEO's
  // explicit pin. Together they answer 'what should I do next?'
  const needsOverdueDeals = (d.kpi && d.kpi.pipeline_overdue) || 0;
  const needsApprovals = (d.kpi && d.kpi.approvals_total) || 0;
  // Red-signal count dropped 2026-06-22 with the Critical Signals removal -
  // it counted miscalibrated POC-drift noise. 'Needs you' is now overdue
  // deals + drafts awaiting approval, both genuine attention items.
  const needsTotal = needsOverdueDeals + needsApprovals;
  let needsSub;
  if (needsTotal === 0) {
    needsSub = 'all clear';
  } else {
    const subBits = [];
    if (needsOverdueDeals > 0) subBits.push(`${needsOverdueDeals} overdue`);
    if (needsApprovals > 0) subBits.push(`${needsApprovals} to approve`);
    needsSub = subBits.slice(0, 2).join(' &middot; ');
  }
  // Route to the page holding the dominant attention item: overdue deals
  // live on /pipeline (with deep-link refs), drafts on /approvals.
  const needsHref = needsOverdueDeals > 0 ? '#/pipeline'
    : (needsApprovals > 0 ? '#/approvals' : '#/pulse');
  tiles.push(_pulseKpiTile(
    'Needs you',
    `<strong>${escapeHtml(fmtInt(needsTotal))}</strong>`,
    needsSub,
    { href: needsHref, cls: needsTotal > 0 ? 'kpi-tile-warn' : '' }
  ));

  // Fourth tile (IMPORTANT slot - CEO directive 'always keep IMPORTANT
  // in this position'). CEO-flagged items log; count fetched lazily
  // after render. --state-warn colour pin so it reads as 'attention'
  // material on the right side of the bento row.
  tiles.push(_pulseKpiTile(
    'Important',
    `<strong id="kpi-critical-value">-</strong>`,
    `<span id="kpi-critical-sub">flagged items</span>`,
    { href: '#/critical', cls: 'kpi-tile-critical' }
  ));
  // Phase 1.54: next meeting is now the focal card above the grid via
  // pulseNextFocalHtml(). No KPI tile here to avoid duplication.

  // Hero: the upcoming-meeting focal card, single-column. The right-hand
  // Critical Signals column was removed 2026-06-22 (see note above the
  // signals-render removal). When there's no meeting, the hero collapses.
  const focalHtml = pulseNextFocalHtml(d);
  const heroBlock = focalHtml
    ? `<section class="pulse-now-hero single">
         <div class="pulse-now-left">${focalHtml}</div>
       </section>`
    : '';

  document.getElementById('canvas').innerHTML = `
    <header class="pulse-hero" data-freshness="${escapeHtml(d.data_time)}" data-stale="${freshnessLevel(d.data_time)}">
      <div class="pulse-breadcrumb">${_breadcrumb('01', 'Today · Pulse', 'Operational state')}</div>
      <h1 class="pulse-greeting">${escapeHtml(_timeOfDayGreeting())}, Misha.</h1>
      <p class="pulse-subhead">${_pulseSubheadHtml(d)}</p>
      ${_pulseActivityHtml(d)}
    </header>
    ${pulseNowHtml(d.now)}
    ${heroBlock}
    <section class="pulse-kpi-grid">${tiles.join('')}</section>
    <div id="pulse-actionq-slot"></div>
    <section class="pulse-triage-grid">
      <div class="pulse-triage-col">${pulseNextHtml(d.next)}</div>
      <div class="pulse-triage-col">${pulseThreadsHtml(d)}</div>
      <div class="pulse-triage-col" id="pulse-inbox-urgent-slot"></div>
    </section>
    <section class="pulse-footer-row pulse-footer-row-2up">
      ${pulsePipelineHtml(d)}
      ${pulseTribeHtml(d)}
    </section>
    <section class="pulse-r2-row">
      ${pulseSuggestedHtml(d)}
      ${pulseRecentOutputsHtml(d)}
    </section>`;
  // Click-through on any Pulse card with data-route -> navigate to that hash.
  document.querySelectorAll('#canvas [data-route]').forEach(card => {
    card.addEventListener('click', () => { location.hash = card.dataset.route; });
  });

  // CSP-safe bar widths: the strict CSP (style-src 'self') forbids inline
  // style="" attributes, so apply each pipeline-stage fill width via CSSOM
  // after mount. CSSOM is not governed by style-src, only declarative styles.
  document.querySelectorAll('#canvas .pulse-bar-fill[data-pct]').forEach(el => {
    el.style.width = el.dataset.pct + '%';
  });

  // Phase 1.69: clickable Today-activity line expands entries below the subhead.
  document.getElementById('pulse-activity-line')?.addEventListener('click', _pulseActivityToggle);

  // Phase 1.149: prime sidebar nav counts from the Pulse payload so
  // sibling badges aren't '-' until those pages are visited. Each
  // page's own renderer still updates its count when entered; this
  // just gives the first-load posture some signal.
  const _navCountSet = (id, n) => {
    const el = document.getElementById(id);
    if (el) el.textContent = String(n);
  };
  if (d.kpi) {
    if (typeof d.kpi.approvals_total === 'number') _navCountSet('approvals-count', d.kpi.approvals_total);
    if (typeof d.kpi.active_deals === 'number')    _navCountSet('pipeline-count', d.kpi.active_deals);
    if (d.kpi.tribe_state && typeof d.kpi.tribe_state.total === 'number') _navCountSet('tribe-count', d.kpi.tribe_state.total);
    if (d.kpi.threads_state && typeof d.kpi.threads_state.active_total === 'number') _navCountSet('threads-count', d.kpi.threads_state.active_total);
  }

  // Phase 1.116: lazy-fill for approvals removed. Approvals are surfaced
  // via the /approvals page and the Suggested-for-now list. (The hero
  // right column - Critical Signals - was removed 2026-06-22.)

  // Phase 1.128: lazy-fill the Inbox-urgent triage card. Separate
  // /inbox endpoint; failures collapse the slot silently.
  (async () => {
    const slot = document.getElementById('pulse-inbox-urgent-slot');
    if (!slot) return;
    const html = await pulseInboxUrgentHtml();
    slot.innerHTML = html;
  })();

  // Action Queue gated-send approvals: surface the human send-gate on Pulse.
  // Separate /action-queue endpoint; failures collapse the slot silently.
  (async () => {
    const slot = document.getElementById('pulse-actionq-slot');
    if (!slot) return;
    slot.innerHTML = await pulseActionQueueHtml();
  })();

  // Phase 1.127b: lazy-fill the Critical KPI tile after Pulse renders.
  // The count isn't in pulse_data yet (separate endpoint), so we fetch
  // /critical once and update the strong + sub spans. Failures swallow
  // silently - the tile shows '-' which is acceptable.
  // Phase 1.135: same fetch also primes the sidebar Important count.
  (async () => {
    try {
      const r = await authFetch('/critical');
      if (!r.ok) return;
      const cd = await r.json();
      const total = cd.total || 0;
      const valueEl = document.getElementById('kpi-critical-value');
      const subEl = document.getElementById('kpi-critical-sub');
      if (valueEl) valueEl.textContent = String(total);
      if (subEl) {
        subEl.textContent = total === 0
          ? 'nothing flagged'
          : `flagged item${total === 1 ? '' : 's'}`;
      }
      const sidebarCount = document.getElementById('critical-count');
      if (sidebarCount) sidebarCount.textContent = String(total);
    } catch (e) {
      // Silent - tile keeps placeholder.
    }
  })();

  renderSyncIndicator(d);
  // Phase 1.26: schedule a 60s tick for the next_meeting countdown.
  if (_nextMeetingTickInterval) {
    clearInterval(_nextMeetingTickInterval);
    _nextMeetingTickInterval = null;
  }
  if (d.kpi.next_meeting && d.kpi.next_meeting.event_utc_iso) {
    _nextMeetingTickInterval = setInterval(_tickNextMeetingMins, 60_000);
  }
  await trackPageView('pulse');
}

// Phase 1.32: source chip. Email is the only live source; the label
// is data-driven so telegram/other slot in without a render change.
function _inboxSourceChip(source) {
  return `<span class="inbox-source-chip">${escapeHtml((source || 'email').toUpperCase())}</span>`;
}

// Full card for a 'needs you' (P1/P2) conversation: the analyzed
// summary, recommended actions, CRM/pipeline context, and the two
// actions the CEO chooses between - handle it in a terminal, or
// dismiss it. Everything renders from the /inbox list payload, so the
// card is complete without a drill-down round-trip.
function _inboxNeedsYouCard(m, d) {
  const pri = escapeHtml((m.priority || 'P2').toLowerCase());
  const actions = (m.proposed_actions && m.proposed_actions.length)
    ? `<div class="inbox-card-label">Recommended</div>
       <ul class="inbox-card-actions">${m.proposed_actions.map(x => `<li>${escapeHtml(x)}</li>`).join('')}</ul>`
    : '';
  const ctxBits = [];
  if (m.crm && m.crm.name) {
    ctxBits.push(`CRM: ${escapeHtml(m.crm.name)}${m.crm.company ? ' (' + escapeHtml(m.crm.company) + ')' : ''}`);
  }
  if (m.pipeline && m.pipeline.stage) {
    ctxBits.push(`Pipeline: ${escapeHtml(m.pipeline.stage)}${m.pipeline.est_value ? ' &middot; ' + escapeHtml(m.pipeline.est_value) : ''}`);
  }
  const ctxLine = ctxBits.length ? `<div class="inbox-card-meta">${ctxBits.join(' &middot; ')}</div>` : '';
  const catChip = m.category
    ? `<span class="cat-chip">${escapeHtml(String(m.category).replace(/_/g, ' '))}</span>` : '';
  const agingChip = m.aging
    ? `<span class="inbox-aging-chip" title="Unread more than 24h">aging</span>` : '';
  const byline = [m.sender, formatRelative(m.latest_datetime)]
    .filter(Boolean).map(escapeHtml).join(' &middot; ');
  const idAttr = escapeHtml(m.id || '');
  const subjAttr = escapeHtml(m.subject || '');
  // Phase 1.33: 'Log to CRM' shows only when the conversation has a
  // linked contact; once logged it disables so it cannot double-log.
  const crmBtn = m.crm
    ? (m.crm_logged
        ? `<button class="inbox-crmlog-btn" disabled>Logged to CRM &#10003;</button>`
        : `<button class="inbox-crmlog-btn" data-id="${idAttr}">Log to CRM</button>`)
    : '';
  return `
    <div class="card inbox-card inbox-card-needs"
         data-id="${idAttr}" data-subject="${subjAttr}"
         data-freshness="${escapeHtml(d.data_time)}" data-stale="${freshnessLevel(d.data_time)}">
      <div class="inbox-card-head">
        <span class="inbox-priority inbox-pri-${pri}">${escapeHtml(m.priority || '')}</span>
        ${_inboxSourceChip(m.source)}
        <span class="inbox-card-subject">${escapeHtml(m.subject || '(no subject)')}</span>
      </div>
      <div class="inbox-card-byline">${byline}${catChip ? ' ' + catChip : ''}${agingChip ? ' ' + agingChip : ''}</div>
      ${m.summary ? `<div class="inbox-card-summary">${escapeHtml(m.summary)}</div>` : ''}
      ${actions}
      ${ctxLine}
      <div class="inbox-action-bar">
        <button class="inbox-continue-btn" data-id="${idAttr}" data-subject="${subjAttr}">Handle in terminal &rsaquo;</button>
        ${crmBtn}
        <button class="inbox-defer-btn" data-id="${idAttr}">Defer</button>
        <button class="inbox-dismiss-btn" data-id="${idAttr}" title="Marks this email read in Outlook, then clears the card (undoable)">Done</button>
      </div>
      <div class="inbox-defer-presets" hidden>
        <span class="inbox-defer-label">Defer until</span>
        <button class="inbox-defer-preset" data-id="${idAttr}" data-days="1">Tomorrow</button>
        <button class="inbox-defer-preset" data-id="${idAttr}" data-days="3">In 3 days</button>
        <button class="inbox-defer-preset" data-id="${idAttr}" data-days="7">Next week</button>
        <button class="inbox-defer-cancel">Cancel</button>
      </div>
    </div>`;
}

// Compressed one-liner for an FYI / low-priority row: analyzed, but no
// action needed. Click expands the full drill-down panel (raw thread).
function _inboxCompactRow(m, d) {
  const byline = [m.sender, formatRelative(m.latest_datetime)].filter(Boolean).join(' · ');
  return `
    <div class="card inbox-row inbox-row-compact"
         data-id="${escapeHtml(m.id || '')}"
         data-subject="${escapeHtml(m.subject || '')}"
         data-freshness="${escapeHtml(d.data_time)}" data-stale="${freshnessLevel(d.data_time)}">
      ${_inboxSourceChip(m.source)}
      <div class="inbox-row-body">
        <div class="inbox-row-subject">${escapeHtml(m.subject || '(no subject)')}</div>
        <div class="inbox-row-when">${escapeHtml(byline)}${m.aging ? ' <span class="inbox-aging-chip" title="Unread more than 24h">aging</span>' : ''}</div>
        ${m.summary ? `<div class="inbox-row-summary">${escapeHtml(m.summary)}</div>` : ''}
      </div>
    </div>`;
}

// A collapsible band section (FYI / low-priority): a clickable head
// with a count, and a body of compact rows hidden until expanded.
function _inboxBandSection(id, title, rows, d, expanded = false) {
  if (!rows.length) return '';
  return `
    <section class="inbox-band inbox-band-collapsible" id="inbox-band-${id}">
      <div class="inbox-band-head inbox-band-toggle${expanded ? ' inbox-band-open' : ''}" data-band="${id}">
        <span class="inbox-band-title">${escapeHtml(title)}</span>
        <span class="inbox-band-count">${escapeHtml(rows.length)}</span>
        <span class="inbox-band-caret">&#9656;</span>
      </div>
      <div class="inbox-band-body list-card"${expanded ? '' : ' hidden'}>${rows.map(m => _inboxCompactRow(m, d)).join('')}</div>
    </section>`;
}

async function renderInbox(params) {
  const r = await authFetch('/inbox');
  if (!r.ok) {
    document.getElementById('canvas').innerHTML = '<div class="card">Failed to load Inbox.</div>';
    return;
  }
  const d = await r.json();
  const bands = d.bands || { 'needs-you': [], fyi: [], noise: [] };
  const needsYou = bands['needs-you'] || [];
  const fyi = bands['fyi'] || [];
  const noise = bands['noise'] || [];
  const total = needsYou.length + fyi.length + noise.length;

  const countEl = document.getElementById('inbox-count');
  if (countEl) countEl.textContent = needsYou.length;

  const eyebrow = _breadcrumb('01', 'Today · Inbox', `${needsYou.length} need you`, d.data_time);
  const greeting = needsYou.length === 0
    ? 'Nothing needs you.'
    : `${escapeHtml(needsYou.length)} need${needsYou.length === 1 ? 's' : ''} you.`;
  const dismissedSuffix = (d.dismissed_count || 0) > 0
    ? ` <span class="inbox-dismissed-pill" title="Marked done - read in Outlook and cleared">${escapeHtml(d.dismissed_count)} done</span>`
    : '';
  const subBits = [];
  if (fyi.length) subBits.push(`<strong>${escapeHtml(fyi.length)}</strong> FYI`);
  if (noise.length) subBits.push(`<strong>${escapeHtml(noise.length)}</strong> low-priority`);
  const subhead = total === 0
    ? `No analyzed conversations. Run /email-intel to refresh.${dismissedSuffix}`
    : `Analyzed ${escapeHtml(formatRelative(d.data_time))}.${subBits.length ? ' ' + subBits.join(' &middot; ') + '.' : ''}${dismissedSuffix}`;

  // Phase 1.86: v8-style filter chip row. Email is the only source the
  // bridge currently ingests; the other chips are visual scaffolding so
  // the layout matches v8 - they'll be wired when those sources land.
  const filterChips = `
    <div class="inbox-filter-row">
      <button class="inbox-filter-chip active" data-filter="all">All</button>
      <button class="inbox-filter-chip" data-filter="email">Email</button>
      <button class="inbox-filter-chip is-stub" data-filter="telegram" disabled>Telegram</button>
      <button class="inbox-filter-chip is-stub" data-filter="linkedin" disabled>LinkedIn</button>
      <button class="inbox-filter-chip is-stub" data-filter="viraid" disabled>Viraid</button>
      <button class="inbox-filter-chip is-stub" data-filter="calendar" disabled>Calendar</button>
      <button class="inbox-filter-chip is-stub" data-filter="signals" disabled>Signals</button>
    </div>`;

  // Phase 1.32: 'Needs you' renders as full cards; FYI + low-priority
  // collapse into count-only bands so nothing is hidden but nothing
  // shouts either.
  const needsSection = needsYou.length
    ? `<section class="inbox-band">
         <div class="inbox-band-head">
           <span class="inbox-band-title">Needs you</span>
           <span class="inbox-band-count">${escapeHtml(needsYou.length)}</span>
         </div>
         ${needsYou.map(m => _inboxNeedsYouCard(m, d)).join('')}
       </section>`
    : `<section class="inbox-band"><div class="card inbox-empty">Nothing needs you right now.</div></section>`;
  // When nothing needs the CEO, auto-expand FYI so a quiet morning still
  // shows its mail instead of a collapsed count. Low-priority stays collapsed.
  const fyiSection = _inboxBandSection('fyi', 'FYI', fyi, d, needsYou.length === 0);
  const noiseSection = _inboxBandSection('noise', 'Low-priority', noise, d);

  // Phase 1.92: 'Recently done' restore footer, parallel to the
  // /tasks Recently-done and /approvals Recently-sent footers.
  const dismissFootHtml = (d.dismiss_log_count || 0) > 0
    ? `<div class="inbox-dismissed-foot" id="inbox-dismissed-foot" data-expandable="1">
         <span class="inbox-dismissed-toggle">Recently done &middot; ${escapeHtml(d.dismiss_log_count)}</span>
         <span class="inbox-dismissed-caret">&#9656;</span>
       </div>
       <div class="inbox-dismissed-expanded" id="inbox-dismissed-expanded" hidden></div>`
    : '';

  // Phase 1.33: 'Deferred' footer - the deferred conversations the CEO
  // pushed to a future date, with their resurface date and a restore.
  const deferFootHtml = (d.defer_log_count || 0) > 0
    ? `<div class="inbox-dismissed-foot" id="inbox-deferred-foot" data-expandable="1">
         <span class="inbox-dismissed-toggle">Deferred &middot; ${escapeHtml(d.defer_log_count)}</span>
         <span class="inbox-dismissed-caret">&#9656;</span>
       </div>
       <div class="inbox-dismissed-expanded" id="inbox-deferred-expanded" hidden></div>`
    : '';

  document.getElementById('canvas').innerHTML = `
    <header class="pulse-hero">
      <div class="pulse-breadcrumb">${eyebrow}</div>
      <h1 class="pulse-greeting">${greeting}</h1>
      <p class="pulse-subhead">${subhead}</p>
    </header>
    ${filterChips}
    ${needsSection}
    ${fyiSection}
    ${noiseSection}
    ${deferFootHtml}
    ${dismissFootHtml}`;

  // Wire the per-card actions on 'needs you' cards.
  document.querySelectorAll('.inbox-card-needs').forEach(card => {
    const contBtn = card.querySelector('.inbox-continue-btn');
    if (contBtn) contBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      _continueInSession({
        action: 'email-respond',
        title: `email: ${contBtn.dataset.subject || ''}`.slice(0, 80),
        context: { conv_id: contBtn.dataset.id, subject: contBtn.dataset.subject },
      }, contBtn);
    });
    const dBtn = card.querySelector('.inbox-dismiss-btn');
    if (dBtn) dBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      _inboxDismiss(dBtn);
    });
    // Phase 1.33: Log to CRM (skipped when already logged / disabled).
    const crmBtn = card.querySelector('.inbox-crmlog-btn');
    if (crmBtn && !crmBtn.disabled) crmBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      _inboxCrmLog(crmBtn);
    });
    // Phase 1.33: Defer reveals a preset-date row; presets POST the defer.
    const presets = card.querySelector('.inbox-defer-presets');
    const deferBtn = card.querySelector('.inbox-defer-btn');
    if (deferBtn && presets) deferBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      presets.hidden = !presets.hidden;
    });
    card.querySelectorAll('.inbox-defer-preset').forEach(p => {
      p.addEventListener('click', (e) => {
        e.stopPropagation();
        _inboxDefer(p, parseInt(p.dataset.days, 10));
      });
    });
    const cancelBtn = card.querySelector('.inbox-defer-cancel');
    if (cancelBtn && presets) cancelBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      presets.hidden = true;
    });
  });
  // Compact FYI / low-priority rows expand to the drill-down panel.
  document.querySelectorAll('.inbox-row-compact').forEach(el => {
    el.addEventListener('click', () => _inboxToggleExpand(el));
  });
  // Band collapse/expand toggles.
  document.querySelectorAll('.inbox-band-toggle').forEach(h => {
    h.addEventListener('click', () => {
      const body = h.parentElement.querySelector('.inbox-band-body');
      if (!body) return;
      body.hidden = !body.hidden;
      h.classList.toggle('inbox-band-open', !body.hidden);
    });
  });
  if (dismissFootHtml) {
    document.getElementById('inbox-dismissed-foot')?.addEventListener('click', _inboxDismissedToggle);
  }
  if (deferFootHtml) {
    document.getElementById('inbox-deferred-foot')?.addEventListener('click', _inboxDeferredToggle);
  }
  // Phase 1.79: deep-link from search results -> focus the conversation.
  const inboxFocus = params && params.get ? params.get('focus') : null;
  if (inboxFocus) {
    const compact = document.querySelector(`.inbox-row-compact[data-id="${cssEscapeAttr(inboxFocus)}"]`);
    if (compact) {
      _inboxOpenBandFor(compact);
      _focusRow(compact, () => _inboxToggleExpand(compact));
    } else {
      const card = document.querySelector(`.inbox-card-needs[data-id="${cssEscapeAttr(inboxFocus)}"]`);
      if (card) _focusRow(card, () => {});
    }
  } else if (_inboxExpandedId) {
    // Preserve a drill-down expansion across polling re-renders (the
    // page renderer re-fires on every component bump, ~30s).
    const row = document.querySelector(`.inbox-row-compact[data-id="${cssEscapeAttr(_inboxExpandedId)}"]`);
    if (row) {
      _inboxOpenBandFor(row);
      _inboxToggleExpand(row);
    } else {
      _inboxExpandedId = null;
    }
  }
  await trackPageView('inbox');
}

// Ensure the collapsible band containing `row` is expanded, so a
// focused/re-expanded compact row is actually visible.
function _inboxOpenBandFor(row) {
  const body = row.closest('.inbox-band-body');
  if (!body || !body.hidden) return;
  body.hidden = false;
  const head = body.parentElement.querySelector('.inbox-band-toggle');
  if (head) head.classList.add('inbox-band-open');
}

// Module-level: id of the currently-expanded inbox conversation, or
// null. Maintained by _inboxToggleExpand and consulted by renderInbox
// to re-expand the same row after a polling-triggered re-render.
let _inboxExpandedId = null;

// Phase 1.88: extract the conversation panel renderer so /conversations
// can reuse the exact same drill-down body without duplicating the
// chip-and-list assembly. Takes the /inbox/conversation payload + the
// row element (for the fallback id/subject) and returns the inner HTML.
function _conversationPanelHtml(d, rowEl) {
  const c = d.conversation;
  const a = c.analysis || {};
  const fallbackSubject = (rowEl && rowEl.dataset && rowEl.dataset.subject) || (rowEl && rowEl.dataset && rowEl.dataset.topic) || '(no subject)';

  const priorityClass = `inbox-pri-${escapeHtml((c.priority || '').toLowerCase())}`;
  const priorityChip = c.priority
    ? `<span class="inbox-priority ${priorityClass}">${escapeHtml(c.priority)}</span>`
    : '';
  const signalChip = a.relationship_signal
    ? `<span class="cat-chip inbox-signal-${escapeHtml(a.relationship_signal)}">${escapeHtml(a.relationship_signal.replace(/_/g, ' '))}</span>`
    : '';
  const directionChip = c.direction
    ? `<span class="cat-chip">${escapeHtml(c.direction)}</span>`
    : '';
  const msgCountChip = c.message_count
    ? `<span class="cat-chip">${escapeHtml(c.message_count)} msg${c.message_count === 1 ? '' : 's'}</span>`
    : '';
  const categoryChip = a.category
    ? `<span class="cat-chip">${escapeHtml(a.category.replace(/_/g, ' '))}</span>`
    : '';

  const summary = a.summary
    ? `<div class="inbox-row-summary">${escapeHtml(a.summary)}</div>`
    : '';

  const actionsList = (a.proposed_actions && a.proposed_actions.length)
    ? `<div class="inbox-row-label">Proposed actions</div>
       <ul class="inbox-row-list">${a.proposed_actions.map(x => `<li>${escapeHtml(x)}</li>`).join('')}</ul>`
    : '';

  const commitmentsList = (a.commitments && a.commitments.length)
    ? `<div class="inbox-row-label">Commitments</div>
       <ul class="inbox-row-list">${a.commitments.map(x => `<li>${escapeHtml(x)}</li>`).join('')}</ul>`
    : '';

  const participants = (c.participants || []).slice(0, 8).map(p => {
    const role = p.role === 'sender' ? '&rarr;' : '';
    return `<li>${role ? `<span class="inbox-row-role">${role}</span> ` : ''}${escapeHtml(p.name || p.email || '(unknown)')} <span class="inbox-row-email">${escapeHtml(p.email || '')}</span></li>`;
  }).join('');
  const participantsList = participants
    ? `<div class="inbox-row-label">Participants</div>
       <ul class="inbox-row-list">${participants}</ul>`
    : '';

  const crm = c.crm_context;
  const crmLine = (crm && crm.name)
    ? `<div class="inbox-row-meta">CRM: ${escapeHtml(crm.name)}${crm.company ? ' (' + escapeHtml(crm.company) + ')' : ''}${crm.last_touch ? ' &middot; last touch ' + escapeHtml(crm.last_touch) : ''}${crm.days_since !== null && crm.days_since !== undefined ? ' &middot; ' + escapeHtml(crm.days_since) + 'd ago' : ''}</div>`
    : '';
  const pipe = c.pipeline_context;
  const pipeLine = (pipe && pipe.company)
    ? `<div class="inbox-row-meta">Pipeline: ${escapeHtml(pipe.company)}${pipe.stage ? ' &middot; ' + escapeHtml(pipe.stage) : ''}${pipe.est_value ? ' &middot; ' + escapeHtml(pipe.est_value) : ''}</div>`
    : '';

  // Action bar - all three actions in one horizontal row. Continue is
  // the primary action (accent color); Dismiss + Flag are secondary
  // (outlined). The hint text below describes the row so the CEO
  // doesn't need a label next to every button.
  const convIdAttr = escapeHtml(c.id || (rowEl && rowEl.dataset && rowEl.dataset.id) || '');
  const convSubject = escapeHtml(c.subject || c.topic || fallbackSubject || '');
  const dismissFooter = `
    <div class="inbox-action-bar">
      <button class="inbox-continue-btn" data-id="${convIdAttr}" data-subject="${convSubject}">Continue in session &rsaquo;</button>
      <button class="pipe-flag-btn inbox-flag-btn inbox-flag-btn-compact" data-ref="${convIdAttr}" data-label="${convSubject}">
        <span class="pipe-flag-icon">!</span>
        <span class="pipe-flag-text">Flag</span>
      </button>
      <button class="inbox-dismiss-btn" data-id="${convIdAttr}">Done</button>
    </div>
    <div class="inbox-action-hint">
      Continue opens a Claude Code terminal pre-loaded with this conversation. Flag pins it to the Important section. Done marks it read in Outlook and clears it (undoable).
    </div>`;

  // Phase 1.100: degraded fallback - conversation isn't in the last
  // /email-intel rich payload but is still in the rolling state. Tell
  // the CEO honestly + show how to refresh.
  const degradedBanner = c.degraded
    ? `<div class="inbox-degraded-banner">
         <div class="inbox-degraded-title">Limited info available</div>
         <div class="inbox-degraded-body">
           This conversation is older than the last <code>/email-intel</code> fetch window,
           so we have only the basic record from the rolling state - no analysis,
           proposed actions, participants, or CRM context.
         </div>
         <div class="inbox-degraded-hint">
           Re-run <code>/email-intel</code> with a wider window to get the full drill-down,
           or open the thread directly in Outlook to read it.
         </div>
       </div>`
    : '';

  return `
    <div class="inbox-row-heading">${priorityChip}${escapeHtml(c.topic || fallbackSubject)}</div>
    <div class="cat-chips">${signalChip}${directionChip}${categoryChip}${msgCountChip}</div>
    ${crmLine}
    ${pipeLine}
    ${degradedBanner}
    ${summary}
    ${actionsList}
    ${commitmentsList}
    ${participantsList}
    ${dismissFooter}`;
}

async function _inboxToggleExpand(rowEl) {
  const existing = document.querySelector('.inbox-row-expanded');
  if (existing) {
    const isThisRow = existing.previousElementSibling === rowEl;
    existing.remove();
    if (isThisRow) {
      // Same-row click = collapse. Clear the persisted expanded id so
      // re-renders don't re-open it.
      _inboxExpandedId = null;
      return;
    }
  }
  const id = rowEl.dataset.id || '';
  const subject = rowEl.dataset.subject || '(no subject)';
  if (!id) return;
  // Persist for re-expansion on the next polling re-render.
  _inboxExpandedId = id;
  const panel = document.createElement('div');
  panel.className = 'card inbox-row-expanded';
  panel.innerHTML = '<div class="inbox-row-loading">Loading...</div>';
  rowEl.insertAdjacentElement('afterend', panel);
  try {
    const r = await authFetch(`/inbox/conversation?id=${encodeURIComponent(id)}`);
    if (!r.ok) {
      let msg = `HTTP ${r.status}`;
      try { const j = await r.json(); if (j.detail) msg = j.detail; } catch (_) {}
      panel.innerHTML = `
        <div class="inbox-row-heading">${escapeHtml(subject)}</div>
        <div class="inbox-row-muted">${escapeHtml(msg)}</div>`;
      return;
    }
    const d = await r.json();
    panel.innerHTML = _conversationPanelHtml(d, rowEl);
    const dBtn = panel.querySelector('.inbox-dismiss-btn');
    if (dBtn) dBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      _inboxDismiss(dBtn);
    });
    // Phase 1.134: wire Flag-Important on the inbox expanded panel.
    const flagBtn = panel.querySelector('.inbox-flag-btn');
    if (flagBtn) _wireFlagImportant(flagBtn, 'conversation', '#/inbox');
    // Continue-in-session: deep-link to terminal with the conv as context.
    const contBtn = panel.querySelector('.inbox-continue-btn');
    if (contBtn) contBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      _continueInSession({
        action: 'email-respond',
        title: `email: ${contBtn.dataset.subject || ''}`.slice(0, 80),
        context: { conv_id: contBtn.dataset.id, subject: contBtn.dataset.subject },
      }, contBtn);
    });
  } catch (e) {
    panel.innerHTML = `<div class="inbox-row-muted">Failed to load.</div>`;
  }
}

async function _inboxDismissedToggle() {
  const foot = document.getElementById('inbox-dismissed-foot');
  const exp = document.getElementById('inbox-dismissed-expanded');
  if (!foot || !exp) return;
  const willOpen = exp.hidden;
  if (willOpen) {
    if (!exp.dataset.loaded) {
      exp.innerHTML = '<div class="inbox-dismissed-loading">Loading...</div>';
      try {
        const r = await authFetch('/inbox/dismiss-log?limit=20');
        const d = await r.json();
        const items = d.items || [];
        if (items.length === 0) {
          exp.innerHTML = '<div class="inbox-dismissed-loading">Nothing marked done yet.</div>';
        } else {
          exp.innerHTML = items.map(it => {
            const when = it.ts ? formatRelative(it.ts) : '';
            const noteHtml = it.note ? `<span class="inbox-dismissed-note">${escapeHtml(it.note)}</span>` : '';
            return `
              <div class="inbox-dismissed-row" data-conv-id="${escapeHtml(it.conv_id)}">
                <span class="inbox-dismissed-topic">${escapeHtml(it.topic)}</span>
                <span class="inbox-dismissed-when">${escapeHtml(when)}</span>
                ${noteHtml}
                <button class="inbox-restore-btn" data-conv-id="${escapeHtml(it.conv_id)}">Restore</button>
              </div>`;
          }).join('');
          exp.querySelectorAll('.inbox-restore-btn').forEach(btn => {
            btn.addEventListener('click', (e) => {
              e.stopPropagation();
              _inboxRestoreDismissed(btn);
            });
          });
        }
      } catch (e) {
        exp.innerHTML = '<div class="inbox-dismissed-loading">Failed to load recent dismisses.</div>';
      }
      exp.dataset.loaded = '1';
    }
    exp.hidden = false;
    foot.classList.add('inbox-dismissed-open');
  } else {
    exp.hidden = true;
    foot.classList.remove('inbox-dismissed-open');
  }
}

async function _inboxRestoreDismissed(btn) {
  const convId = btn.dataset.convId;
  if (!convId) return;
  btn.disabled = true;
  btn.textContent = 'Restoring...';
  try {
    const r = await authFetch('/inbox/undo-dismiss', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ conv_id: convId }),
    });
    if (!r.ok) {
      btn.textContent = 'Failed';
      btn.disabled = false;
      showToast('Restore failed', 'check daemon log');
      return;
    }
    showToast('Conversation restored', convId.slice(0, 40));
    renderCurrentPage();
  } catch (e) {
    btn.textContent = 'Failed';
    btn.disabled = false;
    showToast('Restore failed', 'check daemon log');
  }
}

async function _inboxDismiss(btn) {
  const id = btn.dataset.id;
  if (!id) return;
  btn.disabled = true;
  btn.textContent = 'Marking done...';
  try {
    const r = await authFetch('/inbox/dismiss', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ conv_id: id }),
    });
    if (!r.ok) {
      // 502 carries the Exchange failure detail - surface it: the email
      // is still unread in Outlook and would re-appear.
      let msg = 'check daemon log';
      try { const j = await r.json(); if (j.detail) msg = j.detail; } catch (_) {}
      btn.textContent = 'Failed - retry';
      btn.disabled = false;
      showToast('Not marked done', msg);
      return;
    }
    showToast('Marked done', 'read in Outlook + cleared');
    renderCurrentPage();
  } catch (e) {
    btn.textContent = 'Failed - retry';
    btn.disabled = false;
    showToast('Not marked done', 'check daemon log');
  }
}

// ============================================================
// Phase 1.33: Defer + Log-to-CRM card actions
// ============================================================

// Local calendar date + N days, formatted YYYY-MM-DD. Built from local
// getFullYear/Month/Date (not toISOString) so it matches the daemon's
// date.today() and never drifts a day across the UTC boundary.
function _localDatePlus(days) {
  const d = new Date();
  d.setDate(d.getDate() + days);
  const m = String(d.getMonth() + 1).padStart(2, '0');
  const day = String(d.getDate()).padStart(2, '0');
  return `${d.getFullYear()}-${m}-${day}`;
}

async function _inboxDefer(btn, days) {
  const id = btn.dataset.id;
  if (!id || !Number.isFinite(days)) return;
  const deferUntil = _localDatePlus(days);
  btn.disabled = true;
  const orig = btn.textContent;
  btn.textContent = 'Deferring...';
  try {
    const r = await authFetch('/inbox/defer', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ conv_id: id, defer_until: deferUntil }),
    });
    if (!r.ok) {
      let msg = 'check daemon log';
      try { const j = await r.json(); if (j.detail) msg = j.detail; } catch (_) {}
      btn.textContent = orig;
      btn.disabled = false;
      showToast('Defer failed', msg);
      return;
    }
    showToast('Conversation deferred', `until ${deferUntil}`);
    renderCurrentPage();
  } catch (e) {
    btn.textContent = orig;
    btn.disabled = false;
    showToast('Defer failed', 'check daemon log');
  }
}

async function _inboxCrmLog(btn) {
  const id = btn.dataset.id;
  if (!id) return;
  btn.disabled = true;
  btn.textContent = 'Logging...';
  try {
    const r = await authFetch('/inbox/crm-log', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ conv_id: id }),
    });
    if (!r.ok) {
      let msg = 'check daemon log';
      try { const j = await r.json(); if (j.detail) msg = j.detail; } catch (_) {}
      btn.textContent = 'Log to CRM';
      btn.disabled = false;
      showToast('CRM log failed', msg);
      return;
    }
    const d = await r.json();
    // Stays disabled - the conversation is now in _crm-logged.jsonl and
    // the next render confirms crm_logged from the daemon.
    btn.textContent = 'Logged to CRM ✓';
    showToast('Logged to CRM', d.slug || '');
  } catch (e) {
    btn.textContent = 'Log to CRM';
    btn.disabled = false;
    showToast('CRM log failed', 'check daemon log');
  }
}

async function _inboxDeferredToggle() {
  const foot = document.getElementById('inbox-deferred-foot');
  const exp = document.getElementById('inbox-deferred-expanded');
  if (!foot || !exp) return;
  const willOpen = exp.hidden;
  if (willOpen) {
    if (!exp.dataset.loaded) {
      exp.innerHTML = '<div class="inbox-dismissed-loading">Loading...</div>';
      try {
        const r = await authFetch('/inbox/defer-log?limit=20');
        const d = await r.json();
        const items = d.items || [];
        if (items.length === 0) {
          exp.innerHTML = '<div class="inbox-dismissed-loading">Nothing deferred.</div>';
        } else {
          exp.innerHTML = items.map(it => {
            const noteHtml = it.note ? `<span class="inbox-dismissed-note">${escapeHtml(it.note)}</span>` : '';
            return `
              <div class="inbox-dismissed-row" data-conv-id="${escapeHtml(it.conv_id)}">
                <span class="inbox-dismissed-topic">${escapeHtml(it.topic)}</span>
                <span class="inbox-dismissed-when">until ${escapeHtml(it.defer_until)}</span>
                ${noteHtml}
                <button class="inbox-restore-btn" data-conv-id="${escapeHtml(it.conv_id)}">Restore now</button>
              </div>`;
          }).join('');
          exp.querySelectorAll('.inbox-restore-btn').forEach(btn => {
            btn.addEventListener('click', (e) => {
              e.stopPropagation();
              _inboxRestoreDeferred(btn);
            });
          });
        }
      } catch (e) {
        exp.innerHTML = '<div class="inbox-dismissed-loading">Failed to load deferred list.</div>';
      }
      exp.dataset.loaded = '1';
    }
    exp.hidden = false;
    foot.classList.add('inbox-dismissed-open');
  } else {
    exp.hidden = true;
    foot.classList.remove('inbox-dismissed-open');
  }
}

async function _inboxRestoreDeferred(btn) {
  const convId = btn.dataset.convId;
  if (!convId) return;
  btn.disabled = true;
  btn.textContent = 'Restoring...';
  try {
    const r = await authFetch('/inbox/undo-defer', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ conv_id: convId }),
    });
    if (!r.ok) {
      btn.textContent = 'Failed';
      btn.disabled = false;
      showToast('Restore failed', 'check daemon log');
      return;
    }
    showToast('Conversation restored', 'back in the Inbox');
    renderCurrentPage();
  } catch (e) {
    btn.textContent = 'Failed';
    btn.disabled = false;
    showToast('Restore failed', 'check daemon log');
  }
}

// ============================================================
// Phase 1.72: /approvals page
// Mirrors the inbox/pipeline list pattern - drill-down row to see
// the draft body, inline Mark sent action, plus a "Recently sent"
// expandable footer that restores via /approvals/undo-sent.
// ============================================================
async function renderApprovals(params) {
  const r = await authFetch('/approvals');
  if (!r.ok) {
    document.getElementById('canvas').innerHTML = '<div class="card">Failed to load Approvals.</div>';
    return;
  }
  const d = await r.json();
  const countEl = document.getElementById('approvals-count');
  if (countEl) countEl.textContent = d.total;

  if (d.total === 0 && (d.sent_count || 0) === 0) {
    document.getElementById('canvas').innerHTML = `
      <header class="pulse-hero">
        <div class="pulse-breadcrumb">${_breadcrumb('01', 'Today · Approvals', 'Queue clear', d.data_time)}</div>
        <h1 class="pulse-greeting">No drafts waiting.</h1>
        <p class="pulse-subhead">Drafts in <code>outputs/communications/email/</code> show up here for go/no-go. Sending stays manual via <code>scripts/send-email.py</code>.</p>
      </header>`;
    renderSyncIndicator(d);
    await trackPageView('approvals');
    return;
  }

  const rows = (d.items || []).map(it => `
    <div class="card appr-row"
         data-path="${escapeHtml(it.path)}"
         data-title="${escapeHtml(it.title)}"
         data-to="${escapeHtml(it.to)}"
         data-subject="${escapeHtml(it.subject)}"
         data-freshness="${escapeHtml(d.data_time)}" data-stale="${freshnessLevel(d.data_time)}">
      <div class="appr-row-main">
        <div class="appr-row-subject">${escapeHtml(it.subject || it.title)}</div>
        <div class="appr-row-meta">to ${escapeHtml(it.to || '-')} &middot; ${escapeHtml(formatRelative(it.mtime))}</div>
      </div>
      <div class="appr-row-side">${escapeHtml(it.path.replace('outputs/communications/email/', ''))}</div>
      <button class="appr-inline-mark" title="Mark this draft sent" data-path="${escapeHtml(it.path)}">Mark sent</button>
    </div>`).join('');

  const sentSection = (d.sent_count || 0) > 0
    ? `<div class="appr-sent-foot" id="appr-sent-foot" data-expandable="1">
         <span class="appr-sent-toggle">Recently sent &middot; ${escapeHtml(d.sent_count)}</span>
         <span class="appr-sent-caret">&#9656;</span>
       </div>
       <div class="appr-sent-expanded" id="appr-sent-expanded" hidden></div>`
    : '';

  document.getElementById('canvas').innerHTML = `
    <header class="pulse-hero">
      <div class="pulse-breadcrumb">${_breadcrumb('01', 'Today · Approvals', `${d.total} pending`, d.data_time)}</div>
      <h1 class="pulse-greeting">${escapeHtml(d.total)} draft${d.total === 1 ? '' : 's'} waiting.</h1>
      <p class="pulse-subhead">Click a row to read the draft body. Mark sent removes it from the queue; restore re-surfaces a mistakenly cleared draft.</p>
    </header>
    <div class="list-card">${rows}</div>
    ${sentSection}`;

  document.querySelectorAll('.appr-row').forEach(el => {
    el.addEventListener('click', () => _apprToggleExpand(el));
  });
  // Phase 1.98: inline Mark-sent button on each /approvals row, parallel
  // to the Pulse inline action (Phase 1.89). Lets the CEO clear the queue
  // without drilling into the draft body.
  document.querySelectorAll('.appr-inline-mark').forEach(btn => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      _apprInlineMarkSent(btn);
    });
  });
  const footEl = document.getElementById('appr-sent-foot');
  if (footEl) footEl.addEventListener('click', _apprSentToggle);

  // Phase 1.145: auto-focus from ?focus=draft-path (deep-link from
  // /critical Open). The .appr-row carries data-path.
  const apprFocus = params && params.get ? params.get('focus') : null;
  if (apprFocus) {
    const row = document.querySelector(`.appr-row[data-path="${cssEscapeAttr(apprFocus)}"]`);
    if (row) _focusRow(row, () => _apprToggleExpand(row));
  }
  renderSyncIndicator(d);
  await trackPageView('approvals');
}

async function _apprToggleExpand(rowEl) {
  // Inline-expand the draft body. Mirrors the Pulse approval drill-down.
  const existing = document.querySelector('.appr-row-expanded');
  if (existing) {
    const isThisRow = existing.previousElementSibling === rowEl;
    existing.remove();
    if (isThisRow) return;
  }
  const path = rowEl.dataset.path;
  const title = rowEl.dataset.title || '(draft)';
  const to = rowEl.dataset.to || '';
  const subject = rowEl.dataset.subject || '';
  if (!path) return;
  const panel = document.createElement('div');
  panel.className = 'card appr-row-expanded';
  panel.innerHTML = '<div class="pulse-approval-loading">Loading...</div>';
  rowEl.insertAdjacentElement('afterend', panel);
  try {
    const r = await authFetch(`/approvals/draft?path=${encodeURIComponent(path)}`);
    if (!r.ok) {
      panel.innerHTML = `<div class="pulse-approval-loading">Failed to load: HTTP ${escapeHtml(r.status)}</div>`;
      return;
    }
    const d = await r.json();
    panel.innerHTML = `
      <div class="pulse-approval-head">
        <span class="pulse-approval-title-large">${escapeHtml(title)}</span>
        <span class="pulse-approval-path">${escapeHtml(d.path)}</span>
      </div>
      <div class="pulse-approval-headers">
        <div><strong>To:</strong> ${escapeHtml(to)}</div>
        <div><strong>Subject:</strong> ${escapeHtml(subject)}</div>
      </div>
      <pre class="pulse-approval-body-pre">${escapeHtml(d.content)}</pre>
      <div class="pulse-approval-actions">
        <input class="pulse-approval-note" type="text" maxlength="200"
               placeholder="Optional note (channel, recipient, ...)" />
        <button class="pulse-approval-mark-btn" data-path="${escapeHtml(d.path)}">Mark sent</button>
      </div>
      <div class="pulse-approval-foot">Sending stays manual via <code>scripts/send-email.py</code>. Mark removes the draft from the queue.</div>
      <div class="pipe-flag-row">
        <button class="pipe-flag-btn" data-ref="${escapeHtml(d.path)}" data-label="${escapeHtml(subject || title)}">
          <span class="pipe-flag-icon">!</span>
          <span class="pipe-flag-text">Flag as Important</span>
        </button>
      </div>`;
    const btn = panel.querySelector('.pulse-approval-mark-btn');
    if (btn) btn.addEventListener('click', (e) => {
      e.stopPropagation();
      _pulseApprovalMarkSent(panel, btn);
    });
    const flagBtn = panel.querySelector('.pipe-flag-btn');
    if (flagBtn) _wireFlagImportant(flagBtn, 'draft', '#/approvals');
  } catch (e) {
    panel.innerHTML = `<div class="pulse-approval-loading">Failed to load.</div>`;
  }
}

async function _apprInlineMarkSent(btn) {
  // Phase 1.98: one-click clear from the /approvals row (no drill-down).
  const path = btn.dataset.path;
  if (!path) return;
  btn.disabled = true;
  btn.textContent = '...';
  try {
    const r = await authFetch('/approvals/mark-sent', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path, note: '' }),
    });
    if (!r.ok) {
      btn.textContent = 'Retry';
      btn.disabled = false;
      showToast('Mark sent failed', 'check daemon log');
      return;
    }
    showToast('Draft cleared', path.split('/').pop());
    renderCurrentPage();
  } catch (e) {
    btn.textContent = 'Retry';
    btn.disabled = false;
    showToast('Mark sent failed', 'check daemon log');
  }
}

async function _apprSentToggle() {
  const foot = document.getElementById('appr-sent-foot');
  const exp = document.getElementById('appr-sent-expanded');
  if (!foot || !exp) return;
  const willOpen = exp.hidden;
  if (willOpen) {
    if (!exp.dataset.loaded) {
      exp.innerHTML = '<div class="appr-sent-loading">Loading...</div>';
      try {
        const r = await authFetch('/approvals/sent-log?limit=20');
        const d = await r.json();
        const items = d.items || [];
        if (items.length === 0) {
          exp.innerHTML = '<div class="appr-sent-loading">No recent sends.</div>';
        } else {
          exp.innerHTML = items.map(it => {
            const when = it.ts ? formatRelative(it.ts) : '';
            const noteHtml = it.note ? `<span class="appr-sent-note">${escapeHtml(it.note)}</span>` : '';
            return `
              <div class="appr-sent-row" data-path="${escapeHtml(it.path)}">
                <span class="appr-sent-filename">${escapeHtml(it.filename)}</span>
                <span class="appr-sent-when">${escapeHtml(when)}</span>
                ${noteHtml}
                <button class="appr-restore-btn" data-path="${escapeHtml(it.path)}">Restore</button>
              </div>`;
          }).join('');
          exp.querySelectorAll('.appr-restore-btn').forEach(btn => {
            btn.addEventListener('click', (e) => {
              e.stopPropagation();
              _apprRestoreSent(btn);
            });
          });
        }
      } catch (e) {
        exp.innerHTML = '<div class="appr-sent-loading">Failed to load recent sends.</div>';
      }
      exp.dataset.loaded = '1';
    }
    exp.hidden = false;
    foot.classList.add('appr-sent-open');
  } else {
    exp.hidden = true;
    foot.classList.remove('appr-sent-open');
  }
}

async function _apprRestoreSent(btn) {
  const path = btn.dataset.path;
  if (!path) return;
  btn.disabled = true;
  btn.textContent = 'Restoring...';
  try {
    const r = await authFetch('/approvals/undo-sent', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ path }),
    });
    if (!r.ok) {
      btn.textContent = 'Failed';
      btn.disabled = false;
      showToast('Restore failed', 'check daemon log');
      return;
    }
    showToast('Draft restored', path.split('/').pop());
    renderCurrentPage();
  } catch (e) {
    btn.textContent = 'Failed';
    btn.disabled = false;
    showToast('Restore failed', 'check daemon log');
  }
}

async function renderTasks(params) {
  const r = await authFetch('/tasks');
  if (!r.ok) {
    document.getElementById('canvas').innerHTML = '<div class="card">Failed to load Tasks.</div>';
    return;
  }
  const d = await r.json();
  const countEl = document.getElementById('tasks-count');
  if (countEl) countEl.textContent = d.tasks.length;
  // Phase 1.91: footer for restoring accidentally-marked-done tasks.
  // Shows when any dashboard-side done entries exist; lazy-fetches the
  // /tasks/done-log readout on expand.
  const doneFootHtml = (d.done_log_count || 0) > 0
    ? `<div class="task-done-foot" id="task-done-foot" data-expandable="1">
         <span class="task-done-toggle">Recently done &middot; ${escapeHtml(d.done_log_count)}</span>
         <span class="task-done-caret">&#9656;</span>
       </div>
       <div class="task-done-expanded" id="task-done-expanded" hidden></div>`
    : '';

  if (d.tasks.length === 0) {
    document.getElementById('canvas').innerHTML = `
      <header class="pulse-hero">
        <div class="pulse-breadcrumb">${_breadcrumb('01', 'Today · Tasks', 'None active', d.data_time)}</div>
        <h1 class="pulse-greeting">Tasks clear.</h1>
        <p class="pulse-subhead">No active tasks tracked. Source: <code>outputs/operations/viraid/tasks.md</code>.</p>
      </header>
      ${doneFootHtml}`;
    if (doneFootHtml) _wireTaskDoneFoot();
    renderSyncIndicator(d);
    await trackPageView('tasks');
    return;
  }
  const chips = Object.entries(d.counts)
    .sort((a, b) => a[0].localeCompare(b[0]))  // P1, P2, P3...
    .map(([p, n]) => `<span class="cat-chip">${escapeHtml(p)} ${escapeHtml(n)}</span>`)
    .join('');
  const overdueChip = d.overdue_count > 0
    ? `<span class="cat-chip is-overdue">overdue ${escapeHtml(d.overdue_count)}</span>`
    : '';
  const renderTaskRow = t => {
    let dueStr = '';
    if (t.due) {
      if (t.is_overdue) dueStr = `${escapeHtml(t.due)} (${escapeHtml(-t.days_until_due)}d late)`;
      else if (t.days_until_due === 0) dueStr = 'today';
      else if (t.days_until_due === 1) dueStr = 'tomorrow';
      else dueStr = `in ${escapeHtml(t.days_until_due)}d`;
    }
    const overdueClass = t.is_overdue ? ' task-overdue' : '';
    const priClass = `task-pri-${t.priority.toLowerCase()}`;
    const priSlug = (t.priority || 'p3').toLowerCase();
    // Phase 1.90: inline Done button drops the task out of the bridge's
    // listing (tasks.md remains canonical - /viraid still owns true
    // completion + Completed-section bookkeeping).
    return `
      <div class="card task-row${overdueClass}" data-pri-slug="${escapeHtml(priSlug)}" data-task-key="${escapeHtml(t.task_key || '')}" data-freshness="${escapeHtml(d.data_time)}" data-stale="${freshnessLevel(d.data_time)}">
        <div class="task-pri ${priClass}">${escapeHtml(t.priority)}</div>
        <div>
          <div class="task-desc">${escapeHtml(t.description)}</div>
          <div class="task-meta">${t.kind ? escapeHtml(t.kind) : ''}${t.kind && t.source ? ' &middot; ' : ''}${t.source ? escapeHtml(t.source) : ''}</div>
        </div>
        <div class="task-due">${dueStr}</div>
        <button class="task-flag-btn pipe-flag-btn-icon" title="Flag as Important" data-ref="${escapeHtml(t.task_key || t.description)}" data-label="${escapeHtml(t.description)}"><span class="pipe-flag-icon">!</span></button>
        <button class="task-done-btn" title="Mark done on the dashboard (tasks.md unchanged)" data-task-key="${escapeHtml(t.task_key || '')}">Done</button>
      </div>`;
  };
  // Phase 1.68: group tasks by priority (P1 -> P2 -> P3 -> P4).
  const priOrder = ['P1', 'P2', 'P3', 'P4', 'P5'];
  const taskGrouped = {};
  for (const t of d.tasks) {
    (taskGrouped[t.priority] = taskGrouped[t.priority] || []).push(t);
  }
  for (const k of Object.keys(taskGrouped)) {
    if (!priOrder.includes(k)) priOrder.push(k);
  }
  const taskSections = priOrder
    .filter(p => taskGrouped[p] && taskGrouped[p].length)
    .map(p => `
      <h3 class="task-section-head" data-pri-slug="${escapeHtml(p.toLowerCase())}">
        <span class="task-section-title">${escapeHtml(p)}</span>
        <span class="task-section-count">${escapeHtml(taskGrouped[p].length)}</span>
      </h3>
      <div class="list-card">${taskGrouped[p].map(renderTaskRow).join('')}</div>`)
    .join('');
  document.getElementById('canvas').innerHTML = `
    <header class="pulse-hero">
      <div class="pulse-breadcrumb">${_breadcrumb('01', 'Today · Tasks', `${d.tasks.length} active`, d.data_time)}</div>
      <h1 class="pulse-greeting">${escapeHtml(d.tasks.length)} active task${d.tasks.length === 1 ? '' : 's'}.</h1>
      <p class="pulse-subhead">${d.overdue_count > 0 ? `<strong>${escapeHtml(d.overdue_count)}</strong> overdue` : 'Nothing overdue'}${Object.keys(d.counts || {}).length > 0 ? ' &middot; ' + Object.entries(d.counts).sort((a,b) => a[0].localeCompare(b[0])).map(([p,n]) => `<strong>${escapeHtml(p)}</strong> ${escapeHtml(n)}`).join(' &middot; ') : ''}.</p>
    </header>
    <div class="cat-chips">${overdueChip}${chips}</div>
    ${taskSections}
    ${doneFootHtml}`;
  // Phase 1.90: inline Done button on each task row.
  document.querySelectorAll('.task-done-btn').forEach(btn => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      _taskMarkDone(btn);
    });
  });
  // Phase 1.134: inline Flag-Important button per task row.
  document.querySelectorAll('.task-flag-btn').forEach(btn => {
    _wireFlagImportant(btn, 'task', '#/tasks');
  });
  // Phase 1.91: wire the Recently-done footer if present.
  if (doneFootHtml) _wireTaskDoneFoot();
  // Phase 1.144: auto-focus from ?focus=task-key (deep-link from
  // /critical Open). Tasks have no expand action - _focusRow just
  // scrolls + flashes the row.
  const taskFocus = params && params.get ? params.get('focus') : null;
  if (taskFocus) {
    const row = document.querySelector(`.task-row[data-task-key="${cssEscapeAttr(taskFocus)}"]`);
    if (row) _focusRow(row);
  }
  renderSyncIndicator(d);
  await trackPageView('tasks');
}

function _wireTaskDoneFoot() {
  const foot = document.getElementById('task-done-foot');
  if (foot) foot.addEventListener('click', _taskDoneToggle);
}

async function _taskDoneToggle() {
  const foot = document.getElementById('task-done-foot');
  const exp = document.getElementById('task-done-expanded');
  if (!foot || !exp) return;
  const willOpen = exp.hidden;
  if (willOpen) {
    if (!exp.dataset.loaded) {
      exp.innerHTML = '<div class="task-done-loading">Loading...</div>';
      try {
        const r = await authFetch('/tasks/done-log?limit=20');
        const d = await r.json();
        const items = d.items || [];
        if (items.length === 0) {
          exp.innerHTML = '<div class="task-done-loading">No recent dones.</div>';
        } else {
          exp.innerHTML = items.map(it => {
            const when = it.ts ? formatRelative(it.ts) : '';
            const noteHtml = it.note ? `<span class="task-done-note">${escapeHtml(it.note)}</span>` : '';
            const priChip = it.priority ? `<span class="task-done-pri task-pri-${escapeHtml(it.priority.toLowerCase())}">${escapeHtml(it.priority)}</span>` : '';
            return `
              <div class="task-done-row" data-task-key="${escapeHtml(it.task_key)}">
                ${priChip}
                <span class="task-done-desc">${escapeHtml(it.description)}</span>
                <span class="task-done-when">${escapeHtml(when)}</span>
                ${noteHtml}
                <button class="task-restore-btn" data-task-key="${escapeHtml(it.task_key)}">Restore</button>
              </div>`;
          }).join('');
          exp.querySelectorAll('.task-restore-btn').forEach(btn => {
            btn.addEventListener('click', (e) => {
              e.stopPropagation();
              _taskRestoreDone(btn);
            });
          });
        }
      } catch (e) {
        exp.innerHTML = '<div class="task-done-loading">Failed to load recent dones.</div>';
      }
      exp.dataset.loaded = '1';
    }
    exp.hidden = false;
    foot.classList.add('task-done-open');
  } else {
    exp.hidden = true;
    foot.classList.remove('task-done-open');
  }
}

async function _taskRestoreDone(btn) {
  const key = btn.dataset.taskKey;
  if (!key) return;
  btn.disabled = true;
  btn.textContent = 'Restoring...';
  try {
    const r = await authFetch('/tasks/undo-done', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ task_key: key }),
    });
    if (!r.ok) {
      btn.textContent = 'Failed';
      btn.disabled = false;
      showToast('Restore failed', 'check daemon log');
      return;
    }
    showToast('Task restored', key.split('|', 3)[2] || key);
    renderCurrentPage();
  } catch (e) {
    btn.textContent = 'Failed';
    btn.disabled = false;
    showToast('Restore failed', 'check daemon log');
  }
}

async function _taskMarkDone(btn) {
  const key = btn.dataset.taskKey;
  if (!key) return;
  btn.disabled = true;
  btn.textContent = '...';
  try {
    const r = await authFetch('/tasks/mark-done', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ task_key: key, note: '' }),
    });
    if (!r.ok) {
      btn.textContent = 'Retry';
      btn.disabled = false;
      showToast('Mark done failed', 'check daemon log');
      return;
    }
    showToast('Task done', key.split('|', 3)[2] || key);
    renderCurrentPage();
  } catch (e) {
    btn.textContent = 'Retry';
    btn.disabled = false;
    showToast('Mark done failed', 'check daemon log');
  }
}

function _fmtMoneyShort(v) {
  if (v === null || v === undefined || v === 0) return '';
  if (v >= 1_000_000) return `$${(v / 1_000_000).toFixed(1)}M`;
  if (v >= 1000) return `$${Math.round(v / 1000)}K`;
  return `$${v.toLocaleString()}`;
}

async function renderPipeline(params) {
  const r = await authFetch('/pipeline');
  if (!r.ok) {
    document.getElementById('canvas').innerHTML = '<div class="card">Failed to load Pipeline.</div>';
    return;
  }
  const d = await r.json();
  const countEl = document.getElementById('pipeline-count');
  if (countEl) countEl.textContent = d.deals.length;
  if (d.deals.length === 0) {
    document.getElementById('canvas').innerHTML = `
      <header class="pulse-hero">
        <div class="pulse-breadcrumb">${_breadcrumb('01', 'Today · Pipeline', 'No deals', d.data_time)}</div>
        <h1 class="pulse-greeting">Empty pipeline.</h1>
        <p class="pulse-subhead">No active deals in context/pipeline.md. Add a row to start tracking.</p>
      </header>`;
    renderSyncIndicator(d);
    await trackPageView('pipeline');
    return;
  }
  // Top stat strip.
  const overdueChip = d.overdue_count > 0
    ? `<span class="cat-chip is-overdue">overdue ${escapeHtml(d.overdue_count)}</span>`
    : '';
  const stageChips = Object.entries(d.counts)
    .sort((a, b) => {
      // Order chips by STAGE progression
      const order = ['Won', 'Negotiation', 'Proposal', 'Demo/POC', 'Qualified', 'Lead'];
      return order.indexOf(a[0]) - order.indexOf(b[0]);
    })
    .map(([s, n]) => `<span class="cat-chip">${escapeHtml(s)} ${escapeHtml(n)}</span>`)
    .join('');
  const totalDisplay = d.total_value_usd > 0
    ? `<span class="cat-chip">priced ${escapeHtml(_fmtMoneyShort(d.total_value_usd))}</span>`
    : '';
  const tbdDisplay = d.tbd_count > 0
    ? `<span class="cat-chip">TBD ${escapeHtml(d.tbd_count)}</span>`
    : '';
  // Phase 1.67: group deals by stage. Stage order matches the funnel
  // (Won at the top so closed wins anchor the page; Lead at the bottom).
  const stageOrder = ['Won', 'Negotiation', 'Proposal', 'Demo/POC', 'Qualified', 'Lead'];
  const grouped = {};
  for (const deal of d.deals) {
    (grouped[deal.stage] = grouped[deal.stage] || []).push(deal);
  }
  // Append any unmapped stages (defensive) at the end alphabetically.
  for (const k of Object.keys(grouped)) {
    if (!stageOrder.includes(k)) stageOrder.push(k);
  }
  const _stageSlug = s => s.toLowerCase().replace(/[^a-z]+/g, '-');
  const renderRow = deal => {
    const valueStr = deal.value_usd
      ? escapeHtml(_fmtMoneyShort(deal.value_usd))
      : escapeHtml(deal.value_display || 'TBD');
    const stageClass = `pipe-stage-${_stageSlug(deal.stage)}`;
    const overdueClass = deal.is_overdue ? ' pipe-overdue' : '';
    let dueStr = '';
    if (deal.due_date) {
      if (deal.is_overdue) dueStr = `${escapeHtml(deal.due_date)} (${escapeHtml(-deal.days_until_due)}d late)`;
      else if (deal.days_until_due === 0) dueStr = 'today';
      else if (deal.days_until_due === 1) dueStr = 'tomorrow';
      else if (deal.days_until_due > 0) dueStr = `in ${escapeHtml(deal.days_until_due)}d`;
    }
    const touchBadge = deal.touched_date
      ? `<span class="pipe-touch-badge">touched ${escapeHtml(deal.touched_date)}</span>`
      : '';
    return `
      <div class="card pipe-row${overdueClass}${deal.touched_date ? ' pipe-touched' : ''}"
           data-stage-slug="${escapeHtml(_stageSlug(deal.stage))}"
           data-company="${escapeHtml(deal.company)}"
           data-stage="${escapeHtml(deal.stage)}"
           data-stage-date="${escapeHtml(deal.stage_date || '')}"
           data-next-action="${escapeHtml(deal.next_action || '')}"
           data-touched-date="${escapeHtml(deal.touched_date || '')}"
           data-touched-note="${escapeHtml(deal.touched_note || '')}"
           data-freshness="${escapeHtml(d.data_time)}" data-stale="${freshnessLevel(d.data_time)}">
        <div class="pipe-stage ${stageClass}">${escapeHtml(deal.stage)}</div>
        <div class="pipe-main">
          <div class="pipe-company">${escapeHtml(deal.company)} ${touchBadge}</div>
          <div class="pipe-meta">${escapeHtml(deal.country)}${deal.owner ? ' &middot; ' + escapeHtml(deal.owner) : ''}</div>
        </div>
        <div class="pipe-value">${valueStr}</div>
        <div class="pipe-due">${dueStr}</div>
      </div>`;
  };
  const sections = stageOrder
    .filter(s => grouped[s] && grouped[s].length)
    .map(s => `
      <h3 class="pipe-section-head" data-stage-slug="${escapeHtml(_stageSlug(s))}">
        <span class="pipe-section-title">${escapeHtml(s)}</span>
        <span class="pipe-section-count">${escapeHtml(grouped[s].length)}</span>
      </h3>
      <div class="list-card">${grouped[s].map(renderRow).join('')}</div>`)
    .join('');

  document.getElementById('canvas').innerHTML = `
    <header class="pulse-hero">
      <div class="pulse-breadcrumb">${_breadcrumb('01', 'Today · Pipeline', `${d.deals.length} active`, d.data_time)}</div>
      <h1 class="pulse-greeting">${escapeHtml(d.deals.length)} active deal${d.deals.length === 1 ? '' : 's'}.</h1>
      <p class="pulse-subhead">
        ${d.total_value_usd > 0 ? `<strong>${escapeHtml(_fmtMoneyShort(d.total_value_usd))}</strong> committed across priced deals` : 'No priced totals yet'}${d.tbd_count > 0 ? ` &middot; <strong>${escapeHtml(d.tbd_count)}</strong> TBD` : ''}${d.overdue_count > 0 ? ` &middot; <strong>${escapeHtml(d.overdue_count)}</strong> overdue` : ''}${d.touched_total > 0 ? ` &middot; <strong>${escapeHtml(d.touched_total)}</strong> touched this week` : ''}.
      </p>
    </header>
    <div class="cat-chips">${overdueChip}${totalDisplay}${tbdDisplay}${stageChips}</div>
    ${sections}`;
  document.querySelectorAll('.pipe-row').forEach(el => {
    el.addEventListener('click', () => _pipeToggleExpand(el));
  });
  // Phase 1.70: auto-focus from ?focus=Company (deep-link from Pulse activity).
  const pipeFocus = params && params.get ? params.get('focus') : null;
  if (pipeFocus) {
    const row = document.querySelector(`.pipe-row[data-company="${cssEscapeAttr(pipeFocus)}"]`);
    if (row) _focusRow(row, () => _pipeToggleExpand(row));
  }
  renderSyncIndicator(d);
  await trackPageView('pipeline');
}

function _pipeToggleExpand(rowEl) {
  // Collapse any existing expanded panel.
  const existing = document.querySelector('.pipe-row-expanded');
  if (existing) {
    const isThisRow = existing.previousElementSibling === rowEl;
    existing.remove();
    if (isThisRow) return;
  }
  const company = rowEl.dataset.company || '(unknown)';
  const stage = rowEl.dataset.stage || '';
  const stageDate = rowEl.dataset.stageDate || '';
  const nextAction = rowEl.dataset.nextAction || '';
  const touchedDate = rowEl.dataset.touchedDate || '';
  const touchedNote = rowEl.dataset.touchedNote || '';
  // Compute days at current stage if stage_date is a valid ISO date.
  let daysAtStage = '';
  if (stageDate && /^\d{4}-\d{2}-\d{2}$/.test(stageDate)) {
    const d = new Date(stageDate + 'T00:00:00Z').getTime();
    if (!Number.isNaN(d)) {
      const days = Math.floor((Date.now() - d) / 86400000);
      daysAtStage = ` (${days} day${days === 1 ? '' : 's'} ago)`;
    }
  }
  // Touch tracking block: prior-touch line + Mark-touched control.
  let touchBlock;
  if (touchedDate) {
    const noteHtml = touchedNote
      ? ` <span class="pipe-row-muted">&middot; ${escapeHtml(touchedNote)}</span>`
      : '';
    touchBlock = `
      <div class="pipe-touch-row">
        <span class="pipe-touched-line">Touched ${escapeHtml(touchedDate)}${noteHtml}</span>
        <button class="pipe-mark-btn" data-company="${escapeHtml(company)}">Re-mark touched today</button>
      </div>`;
  } else {
    touchBlock = `
      <div class="pipe-touch-row">
        <input class="pipe-touch-note" type="text" maxlength="200" placeholder="Optional note (call, email, status)" />
        <button class="pipe-mark-btn pipe-mark-primary" data-company="${escapeHtml(company)}">Mark touched today</button>
      </div>`;
  }
  // Phase 1.133: Flag-Important control. Sits below the touch row.
  // Hits POST /critical/mark with kind=deal, ref=company. The button
  // text toggles between 'Flag as Important' and 'Unflag' based on
  // current state - checked async after panel mount so the panel
  // shows up immediately.
  const flagBlock = `
    <div class="pipe-flag-row">
      <button class="pipe-flag-btn" data-ref="${escapeHtml(company)}" data-label="${escapeHtml(company + (stage ? ' - ' + stage : ''))}">
        <span class="pipe-flag-icon">!</span>
        <span class="pipe-flag-text">Flag as Important</span>
      </button>
    </div>`;
  const panel = document.createElement('div');
  panel.className = 'card pipe-row-expanded';
  panel.innerHTML = `
    <div class="pipe-row-heading">${escapeHtml(company)}</div>
    <div class="pipe-row-stagedate">Stage entered: ${escapeHtml(stageDate || '-')}${escapeHtml(daysAtStage)}</div>
    <div class="pipe-row-label">Next action</div>
    <div class="pipe-row-action">${escapeHtml(nextAction)}</div>
    ${touchBlock}
    ${flagBlock}`;
  rowEl.insertAdjacentElement('afterend', panel);
  const btn = panel.querySelector('.pipe-mark-btn');
  if (btn) btn.addEventListener('click', (e) => {
    e.stopPropagation();
    _pipeMarkTouched(panel, btn);
  });
  const flagBtn = panel.querySelector('.pipe-flag-btn');
  if (flagBtn) {
    _wireFlagImportant(flagBtn, 'deal', '#/pipeline');
  }
}

// Phase 1.133: shared mark-important wiring. Used by Pipeline rows
// now, planned for Tasks/Approvals/Inbox rows in 1.134.
// The button data-ref + data-label are the canonical inputs; the
// caller passes kind + source_page. Toggles between 'Flag as Important'
// and 'Unflag' based on the server's response and the current state.
async function _wireFlagImportant(btn, kind, sourcePage) {
  // Initial state check - fetch /critical once, search for this ref.
  // Cached at the window level so each row check is O(1) after the
  // first fetch.
  if (!window._criticalCache || (Date.now() - window._criticalCacheAt) > 30_000) {
    try {
      const r = await authFetch('/critical');
      if (r.ok) {
        const d = await r.json();
        const byRef = {};
        for (const it of (d.items || [])) byRef[`${it.kind}|${it.ref}`] = it.id;
        window._criticalCache = byRef;
        window._criticalCacheAt = Date.now();
      } else {
        window._criticalCache = {};
      }
    } catch (e) {
      window._criticalCache = {};
    }
  }
  const ref = btn.dataset.ref;
  const label = btn.dataset.label || ref;
  const key = `${kind}|${ref}`;
  const existingId = window._criticalCache[key];
  const textEl = btn.querySelector('.pipe-flag-text') || btn;
  const iconEl = btn.querySelector('.pipe-flag-icon');
  const applyFlagged = () => {
    btn.classList.add('is-flagged');
    if (textEl) textEl.textContent = 'Flagged - click to unflag';
  };
  const applyUnflagged = () => {
    btn.classList.remove('is-flagged');
    if (textEl) textEl.textContent = 'Flag as Important';
  };
  if (existingId) applyFlagged();

  btn.addEventListener('click', async (e) => {
    e.stopPropagation();
    btn.disabled = true;
    const wasFlagged = btn.classList.contains('is-flagged');
    try {
      if (wasFlagged) {
        const currentId = window._criticalCache[key];
        if (currentId) {
          const r = await authFetch('/critical/unmark', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ id: currentId }),
          });
          if (r.ok) {
            delete window._criticalCache[key];
            applyUnflagged();
            showToast('Unflagged', label);
            _refreshCriticalSidebarCount();
          } else {
            showToast('Unflag failed', 'check daemon log');
          }
        }
      } else {
        const r = await authFetch('/critical/mark', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ kind, ref, label, source_page: sourcePage }),
        });
        if (r.ok) {
          const d = await r.json();
          window._criticalCache[key] = d.id;
          applyFlagged();
          showToast('Flagged as Important', label);
          _refreshCriticalSidebarCount();
        } else {
          showToast('Flag failed', 'check daemon log');
        }
      }
    } catch (err) {
      showToast('Flag failed', 'check daemon log');
    } finally {
      btn.disabled = false;
    }
  });
}

// Phase 1.135: refresh the sidebar Important count after any
// flag/unflag. Cheap: count cached entries in window._criticalCache.
function _refreshCriticalSidebarCount() {
  const el = document.getElementById('critical-count');
  if (!el) return;
  const n = window._criticalCache ? Object.keys(window._criticalCache).length : 0;
  el.textContent = String(n);
}

async function _pipeMarkTouched(panelEl, btn) {
  const company = btn.dataset.company;
  if (!company) return;
  const noteInput = panelEl.querySelector('.pipe-touch-note');
  const note = noteInput ? noteInput.value.trim() : '';
  btn.disabled = true;
  btn.textContent = 'Saving...';
  try {
    const r = await authFetch('/pipeline/mark-touched', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ company, note }),
    });
    if (!r.ok) {
      btn.textContent = 'Failed - retry';
      btn.disabled = false;
      showToast('Mark touched failed', 'check daemon log');
      return;
    }
    const d = await r.json();
    showToast('Pipeline touched', `${company} - ${d.date}`);
    renderCurrentPage();
  } catch (e) {
    btn.textContent = 'Failed - retry';
    btn.disabled = false;
    showToast('Mark touched failed', 'check daemon log');
  }
}

const _INV_STATUS_ORDER = ['first-5', 'parallel-week-1-2', 'wave-2', 'wave-3', 'TBD', 'out-of-scope'];
const _INV_STATUS_HEADING = {
  'first-5': 'First 5 (this week)',
  'parallel-week-1-2': 'Parallel-track Week 1-2',
  'wave-2': 'Wave 2 (warm-intro first)',
  'wave-3': 'Wave 3 (deferred)',
  'out-of-scope': 'Out of scope',
  'TBD': 'Unassigned',
};

async function renderInvestors(params) {
  const r = await authFetch('/investors');
  if (!r.ok) {
    document.getElementById('canvas').innerHTML = '<div class="card">Failed to load Investors.</div>';
    return;
  }
  const d = await r.json();
  const countEl = document.getElementById('investors-count');
  if (countEl) countEl.textContent = d.total;
  if (d.firms.length === 0) {
    document.getElementById('canvas').innerHTML = `
      <header class="pulse-hero">
        <div class="pulse-breadcrumb">${_breadcrumb('04', 'Investors', 'No active raise')}</div>
        <h1 class="pulse-greeting">No active raise.</h1>
        <p class="pulse-subhead">Drop a shortlist in <code>outputs/operations/fundraising/</code> to populate.</p>
      </header>`;
    renderSyncIndicator(d);
    await trackPageView('investors');
    return;
  }
  const raiseLabel = d.raise_target ? `${escapeHtml(d.raise_target)} anchor` : 'Series B';
  const chips = _INV_STATUS_ORDER
    .filter(s => d.counts[s])
    .map(s => `<span class="cat-chip inv-chip-${escapeHtml(s)}">${escapeHtml(_INV_STATUS_HEADING[s] || s)} ${escapeHtml(d.counts[s])}</span>`)
    .join('');

  // Group firms by status. Preserve sort order from the server.
  const groups = {};
  for (const f of d.firms) {
    (groups[f.status] = groups[f.status] || []).push(f);
  }
  const sections = _INV_STATUS_ORDER
    .filter(s => groups[s] && groups[s].length)
    .map(s => {
      const rows = groups[s].map(f => {
        const dossierAttr = f.dossier_path ? `data-dossier="${escapeHtml(f.dossier_path)}"` : '';
        const messageAttr = f.message_path ? `data-message="${escapeHtml(f.message_path)}"` : '';
        const fitClass = `inv-fit-${escapeHtml((f.fit || '').toLowerCase().replace(/[^a-z0-9]+/g, '-'))}`;
        const sentBadge = f.sent_date
          ? `<span class="inv-sent-badge">sent ${escapeHtml(f.sent_date)}</span>`
          : '';
        // Phase 1.99: inline Mark-sent for unsent rows; sent rows show a
        // small static check marker. Drill-down still hosts the verbose
        // flow with note + remark/undo (the lighter inline path covers
        // the common 'just sent it' click).
        const inlineAction = f.sent_date
          ? `<span class="inv-inline-sent" title="First-touch sent ${escapeHtml(f.sent_date)}">&#10003;</span>`
          : `<button class="inv-inline-mark" title="Mark first-touch sent today" data-num="${escapeHtml(f.num)}">Mark sent</button>`;
        return `
          <div class="card inv-row inv-status-${escapeHtml(s)}${f.sent_date ? ' inv-sent' : ''}"
               data-firm="${escapeHtml(f.firm)}"
               data-num="${escapeHtml(f.num)}"
               data-region="${escapeHtml(f.region)}"
               data-type="${escapeHtml(f.type)}"
               data-hq="${escapeHtml(f.hq)}"
               data-cheque="${escapeHtml(f.cheque)}"
               data-fit="${escapeHtml(f.fit)}"
               data-notes="${escapeHtml(f.notes)}"
               data-sent-date="${escapeHtml(f.sent_date || '')}"
               data-sent-note="${escapeHtml(f.sent_note || '')}"
               ${dossierAttr} ${messageAttr}
               data-freshness="${escapeHtml(d.data_time)}" data-stale="${freshnessLevel(d.data_time)}">
            <div class="inv-badge ${fitClass}">${escapeHtml(f.fit || '')}</div>
            <div class="inv-main">
              <div class="inv-firm">${escapeHtml(f.firm)} ${sentBadge}</div>
              <div class="inv-meta">${escapeHtml(f.region)} &middot; ${escapeHtml(f.hq)} &middot; ${escapeHtml(f.type)}</div>
            </div>
            <div class="inv-cheque">${escapeHtml(f.cheque)}</div>
            ${inlineAction}
          </div>`;
      }).join('');
      return `
        <h3 class="inv-section">${escapeHtml(_INV_STATUS_HEADING[s] || s)} <span class="inv-section-count">${escapeHtml(groups[s].length)}</span></h3>
        <div class="list-card">${rows}</div>`;
    }).join('');

  const first5 = (d.firms || []).filter(f => f.status === 'first-5').length;
  const first5Sent = (d.firms || []).filter(f => f.status === 'first-5' && f.sent_date).length;
  document.getElementById('canvas').innerHTML = `
    <header class="pulse-hero">
      <div class="pulse-breadcrumb">${_breadcrumb('04', 'Investors', raiseLabel, d.data_time)}</div>
      <h1 class="pulse-greeting">${escapeHtml(d.total)} firms on the shortlist.</h1>
      <p class="pulse-subhead">
        ${first5 > 0 ? `<strong>${escapeHtml(first5Sent)}/${escapeHtml(first5)} first-touches sent</strong> in the lead wave` : `${escapeHtml(d.total)} firms tracked`}${d.sent_total ? ` &middot; <strong>${escapeHtml(d.sent_total)}</strong> total sent` : ''}.
      </p>
    </header>
    <div class="cat-chips">${chips}</div>
    ${sections}`;
  document.querySelectorAll('.inv-row').forEach(el => {
    el.addEventListener('click', () => _invToggleExpand(el));
  });
  // Phase 1.99: inline Mark-sent on each unsent investor row.
  document.querySelectorAll('.inv-inline-mark').forEach(btn => {
    btn.addEventListener('click', (e) => {
      e.stopPropagation();
      _invInlineMarkSent(btn);
    });
  });
  // Phase 1.70: auto-focus from ?focus=N (deep-link from Pulse activity).
  const invFocus = params && params.get ? params.get('focus') : null;
  if (invFocus) {
    const row = document.querySelector(`.inv-row[data-num="${cssEscapeAttr(invFocus)}"]`);
    if (row) _focusRow(row, () => _invToggleExpand(row));
  }
  renderSyncIndicator(d);
  await trackPageView('investors');
}

function _invToggleExpand(rowEl) {
  const existing = document.querySelector('.inv-row-expanded');
  if (existing) {
    const isThisRow = existing.previousElementSibling === rowEl;
    existing.remove();
    if (isThisRow) return;
  }
  const firm = rowEl.dataset.firm || '(unknown)';
  const num = parseInt(rowEl.dataset.num, 10);
  const notes = rowEl.dataset.notes || '';
  const dossierPath = rowEl.dataset.dossier || '';
  const messagePath = rowEl.dataset.message || '';
  const sentDate = rowEl.dataset.sentDate || '';
  const sentNote = rowEl.dataset.sentNote || '';
  const panel = document.createElement('div');
  panel.className = 'card inv-row-expanded';
  const dossierBtn = dossierPath
    ? `<button class="inv-load-btn" data-path="${escapeHtml(dossierPath)}" data-kind="dossier">Load full dossier</button>`
    : '<span class="inv-row-muted">No dossier on file.</span>';
  const messageBtn = messagePath
    ? `<button class="inv-load-btn" data-path="${escapeHtml(messagePath)}" data-kind="message">Load first-touch message</button>`
    : '';
  // Send tracking: either show prior-sent line or render the Mark-sent control.
  let sendBlock = '';
  if (sentDate) {
    const noteHtml = sentNote ? ` <span class="inv-row-muted">&middot; ${escapeHtml(sentNote)}</span>` : '';
    sendBlock = `
      <div class="inv-send-row">
        <span class="inv-sent-line">First-touch sent ${escapeHtml(sentDate)}${noteHtml}</span>
        <button class="inv-load-btn inv-mark-btn" data-num="${escapeHtml(num)}" data-kind="remark">Re-mark sent today</button>
        <button class="inv-load-btn inv-undo-btn" data-num="${escapeHtml(num)}" data-kind="undo">Undo sent</button>
      </div>`;
  } else if (Number.isFinite(num)) {
    sendBlock = `
      <div class="inv-send-row">
        <input class="inv-send-note" type="text" maxlength="200" placeholder="Optional note (channel, recipient, ...)" />
        <button class="inv-load-btn inv-mark-btn inv-mark-primary" data-num="${escapeHtml(num)}" data-kind="mark">Mark first-touch sent</button>
      </div>`;
  }
  // Phase 1.137: Flag-Important on the investor expanded panel.
  // kind='deal' (investors are deal-domain too), ref=firm, label=firm.
  const flagBlock = Number.isFinite(num) ? `
    <div class="pipe-flag-row">
      <button class="pipe-flag-btn" data-ref="${escapeHtml(`investor-${num}`)}" data-label="${escapeHtml(firm)}">
        <span class="pipe-flag-icon">!</span>
        <span class="pipe-flag-text">Flag as Important</span>
      </button>
    </div>` : '';
  panel.innerHTML = `
    <div class="inv-row-heading">${escapeHtml(firm)}</div>
    <div class="inv-row-notes">${escapeHtml(notes)}</div>
    ${sendBlock}
    <div class="inv-row-actions">${dossierBtn} ${messageBtn}</div>
    <div class="inv-row-content"></div>
    ${flagBlock}`;
  rowEl.insertAdjacentElement('afterend', panel);
  panel.querySelectorAll('.inv-load-btn').forEach(btn => {
    if (btn.classList.contains('inv-undo-btn')) {
      btn.addEventListener('click', () => _invUndoSent(panel, btn));
    } else if (btn.classList.contains('inv-mark-btn')) {
      btn.addEventListener('click', () => _invMarkSent(panel, btn));
    } else {
      btn.addEventListener('click', () => _invLoadContent(panel, btn.dataset.path, btn.dataset.kind));
    }
  });
  const flagBtn = panel.querySelector('.pipe-flag-btn');
  if (flagBtn) _wireFlagImportant(flagBtn, 'deal', '#/investors');
}

async function _invUndoSent(panelEl, btn) {
  const num = parseInt(btn.dataset.num, 10);
  if (!Number.isFinite(num)) return;
  btn.disabled = true;
  btn.textContent = 'Undoing...';
  try {
    const r = await authFetch('/investors/undo-sent', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ firm_num: num }),
    });
    if (!r.ok) {
      btn.textContent = 'Failed - retry';
      btn.disabled = false;
      showToast('Undo failed', 'check daemon log');
      return;
    }
    showToast('First-touch undone', `firm #${num}`);
    renderCurrentPage();
  } catch (e) {
    btn.textContent = 'Failed - retry';
    btn.disabled = false;
    showToast('Undo failed', 'check daemon log');
  }
}

async function _invInlineMarkSent(btn) {
  // Phase 1.99: one-click mark first-touch sent from an investor row.
  // The drill-down still hosts the with-note + remark/undo flow.
  const num = parseInt(btn.dataset.num, 10);
  if (!Number.isFinite(num)) return;
  btn.disabled = true;
  btn.textContent = '...';
  try {
    const r = await authFetch('/investors/mark-sent', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ firm_num: num, note: '' }),
    });
    if (!r.ok) {
      btn.textContent = 'Retry';
      btn.disabled = false;
      showToast('Mark sent failed', 'check daemon log');
      return;
    }
    const d = await r.json();
    showToast('First-touch sent', `firm #${num}${d.date ? ' . ' + d.date : ''}`);
    renderCurrentPage();
  } catch (e) {
    btn.textContent = 'Retry';
    btn.disabled = false;
    showToast('Mark sent failed', 'check daemon log');
  }
}

async function _invMarkSent(panelEl, btn) {
  const num = parseInt(btn.dataset.num, 10);
  if (!Number.isFinite(num)) return;
  const noteInput = panelEl.querySelector('.inv-send-note');
  const note = noteInput ? noteInput.value.trim() : '';
  btn.disabled = true;
  btn.textContent = 'Saving...';
  try {
    const r = await authFetch('/investors/mark-sent', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ firm_num: num, note: note }),
    });
    if (!r.ok) {
      btn.textContent = 'Failed - retry';
      btn.disabled = false;
      showToast('Mark sent failed', 'check daemon log');
      return;
    }
    const d = await r.json();
    showToast('First-touch recorded', `sent ${d.date}`);
    // Re-render the Investors page so the row picks up the new sent_date.
    renderCurrentPage();
  } catch (e) {
    btn.textContent = 'Failed - retry';
    btn.disabled = false;
    showToast('Mark sent failed', 'error');
  }
}

async function _invLoadContent(panelEl, path, kind) {
  const target = panelEl.querySelector('.inv-row-content');
  if (!target) return;
  target.innerHTML = '<div class="inv-row-loading">Loading...</div>';
  try {
    const r = await authFetch(`/investors/dossier?path=${encodeURIComponent(path)}`);
    if (!r.ok) {
      target.innerHTML = `<div class="inv-row-error">Failed to load: HTTP ${escapeHtml(r.status)}</div>`;
      return;
    }
    const d = await r.json();
    target.innerHTML = `
      <div class="inv-row-path">${escapeHtml(d.path)} <span class="inv-row-size">(${escapeHtml(d.size)} bytes &middot; ${escapeHtml(kind)})</span></div>
      <pre class="inv-row-pre">${escapeHtml(d.content)}</pre>`;
  } catch (e) {
    target.innerHTML = `<div class="inv-row-error">Failed to load.</div>`;
  }
}

// Phase 1.38: Studio is the reference to artifacts created for human
// attention - LinkedIn posts and articles, each with its images.
async function _studioImgUrl(path) {
  // <img> cannot send an auth header (F-M1/F-L5): instead of riding the
  // long-lived bearer in ?t= (which leaks it into HTTP logs / Referer /
  // history), mint a short-lived single-use nonce via the bearer-authed
  // POST /studio/image-nonce and pass ?n=<nonce>. A fresh nonce is minted
  // immediately before each image render.
  const r = await authFetch('/studio/image-nonce', { method: 'POST' });
  if (!r.ok) return '';
  const { nonce } = await r.json();
  return `/studio/image?path=${encodeURIComponent(path)}&n=${encodeURIComponent(nonce)}`;
}

// Hydrate studio images after their markup is in the DOM. Each <img>/<a> is
// rendered with a data-img-path attribute and no src/href; here we mint one
// nonce per element and set the attribute, so the bearer token never appears
// in an image URL. authFetch supplies the bearer only on the mint call.
async function _hydrateStudioImages(root) {
  const els = (root || document).querySelectorAll('[data-img-path]');
  await Promise.all(Array.from(els).map(async el => {
    const path = el.dataset.imgPath;
    if (!path) return;
    const url = await _studioImgUrl(path);
    if (!url) return;
    if (el.tagName === 'A') el.setAttribute('href', url);
    else el.setAttribute('src', url);
  }));
}

async function renderStudio(params) {
  const r = await authFetch('/studio');
  if (!r.ok) {
    document.getElementById('canvas').innerHTML = '<div class="card">Failed to load Studio.</div>';
    return;
  }
  const d = await r.json();
  const countEl = document.getElementById('studio-count');
  if (countEl) countEl.textContent = d.total;

  if (!d.artifacts || d.artifacts.length === 0) {
    document.getElementById('canvas').innerHTML = `
      <header class="pulse-hero">
        <div class="pulse-breadcrumb">${_breadcrumb('02', 'Work · Studio', 'No artifacts', d.data_time)}</div>
        <h1 class="pulse-greeting">Studio empty.</h1>
        <p class="pulse-subhead">No LinkedIn posts or articles found in the content archive.</p>
      </header>`;
    renderSyncIndicator(d);
    await trackPageView('studio');
    return;
  }

  const counts = Object.entries(d.counts || {})
    .sort((a, b) => b[1] - a[1])
    .map(([k, n]) => `<span class="cat-chip">${escapeHtml(k)} ${escapeHtml(n)}</span>`)
    .join('');

  const card = a => {
    const thumb = (a.images && a.images.length)
      ? `<img class="studio-art-img" loading="lazy" data-img-path="${escapeHtml(a.images[0])}" alt="">`
      : '<div class="studio-art-noimg">no image</div>';
    const meta = [
      a.date, a.series, a.format, a.status,
      a.image_count ? `${a.image_count} image${a.image_count === 1 ? '' : 's'}` : '',
    ].filter(Boolean).map(escapeHtml).join(' &middot; ');
    return `
      <div class="card studio-artifact" data-kind="${escapeHtml(a.kind)}" data-slug="${escapeHtml(a.slug)}"
           data-freshness="${escapeHtml(d.data_time)}" data-stale="${freshnessLevel(d.data_time)}">
        <div class="studio-art-thumb">${thumb}</div>
        <div class="studio-art-body">
          <div class="studio-art-head">
            <span class="cat-chip">${escapeHtml(a.kind)}</span>
            <span class="studio-art-title">${escapeHtml(a.title)}</span>
          </div>
          <div class="studio-art-meta">${meta}</div>
          ${a.summary ? `<div class="studio-art-summary">${escapeHtml(a.summary)}</div>` : ''}
        </div>
      </div>`;
  };

  document.getElementById('canvas').innerHTML = `
    <header class="pulse-hero">
      <div class="pulse-breadcrumb">${_breadcrumb('02', 'Work · Studio', `${d.total} artifacts`, d.data_time)}</div>
      <h1 class="pulse-greeting">${escapeHtml(d.total)} artifact${d.total === 1 ? '' : 's'}.</h1>
      <p class="pulse-subhead">LinkedIn posts and articles created, with their images. Click any to see the full text and visuals.</p>
    </header>
    <div class="cat-chips">${counts}</div>
    <div class="studio-art-list">${d.artifacts.map(card).join('')}</div>`;
  await _hydrateStudioImages(document.getElementById('canvas'));
  document.querySelectorAll('.studio-artifact').forEach(el => {
    el.addEventListener('click', () => _studioToggleExpand(el));
  });
  const focus = params && params.get ? params.get('focus') : null;
  if (focus) {
    const row = document.querySelector(`.studio-artifact[data-slug="${cssEscapeAttr(focus)}"]`);
    if (row) _focusRow(row, () => _studioToggleExpand(row));
  }
  renderSyncIndicator(d);
  await trackPageView('studio');
}

async function _studioToggleExpand(rowEl) {
  const existing = document.querySelector('.studio-row-expanded');
  if (existing) {
    const isThisRow = existing.previousElementSibling === rowEl;
    existing.remove();
    if (isThisRow) return;
  }
  const kind = rowEl.dataset.kind;
  const slug = rowEl.dataset.slug;
  if (!kind || !slug) return;
  const panel = document.createElement('div');
  panel.className = 'card studio-row-expanded';
  panel.innerHTML = '<div class="studio-row-loading">Loading...</div>';
  rowEl.insertAdjacentElement('afterend', panel);
  try {
    const r = await authFetch(`/studio/artifact?kind=${encodeURIComponent(kind)}&slug=${encodeURIComponent(slug)}`);
    if (!r.ok) {
      panel.innerHTML = `<div class="studio-row-error">Failed to load: HTTP ${escapeHtml(r.status)}</div>`;
      return;
    }
    const d = await r.json();
    const gallery = (d.images && d.images.length)
      ? `<div class="studio-art-gallery">${d.images.map(p =>
          `<a data-img-path="${escapeHtml(p)}" target="_blank" rel="noopener"><img loading="lazy" data-img-path="${escapeHtml(p)}" alt=""></a>`
        ).join('')}</div>`
      : '';
    panel.innerHTML = `
      <div class="studio-row-path">${escapeHtml(d.title)} <span class="studio-row-size">${escapeHtml(d.kind)} &middot; ${escapeHtml(d.date)}</span></div>
      ${gallery}
      <pre class="studio-row-content">${escapeHtml(d.content)}</pre>`;
    await _hydrateStudioImages(panel);
  } catch (e) {
    panel.innerHTML = `<div class="studio-row-error">Failed to load.</div>`;
  }
}

async function _tribeToggleExpand(rowEl) {
  const existing = document.querySelector('.tribe-row-expanded');
  if (existing) {
    const isThisRow = existing.previousElementSibling === rowEl;
    existing.remove();
    if (isThisRow) return;
  }
  const ds = rowEl.dataset;
  const slug = ds.slug;
  const panel = document.createElement('div');
  panel.className = 'card tribe-row-expanded';
  rowEl.insertAdjacentElement('afterend', panel);

  // Phase 1.37: roster (org) block - always available from the row's
  // data attributes, shown whether or not there is a CRM record.
  const orgRows = [
    ['Title', ds.title], ['Department', ds.department],
    ['Reports to', ds.reportsTo], ['Telegram', ds.telegram],
  ].filter(([, v]) => v)
   .map(([k, v]) => `<div class="tribe-fm-row"><span class="tribe-fm-key">${escapeHtml(k)}</span><span class="tribe-fm-val">${escapeHtml(v)}</span></div>`)
   .join('');
  const orgBlock = orgRows
    ? `<div class="tribe-section-label">Roster</div><div class="tribe-fm-grid">${orgRows}</div>`
    : '';

  if (!slug) {
    // Roster-only member - no CRM contact file, show org info only.
    panel.innerHTML = `
      <div class="tribe-row-name">${escapeHtml(ds.name || '')}</div>
      ${orgBlock}
      <div class="tribe-row-meta">Roster entry &mdash; no CRM record or interaction log.</div>`;
    return;
  }
  panel.innerHTML = '<div class="tribe-row-loading">Loading...</div>';
  try {
    const r = await authFetch(`/tribe/contact?slug=${encodeURIComponent(slug)}`);
    if (!r.ok) {
      panel.innerHTML = `${orgBlock}<div class="tribe-row-error">CRM record failed to load: HTTP ${escapeHtml(r.status)}</div>`;
      return;
    }
    const d = await r.json();
    const fm = d.frontmatter || {};
    const fmRows = Object.entries(fm)
      .filter(([k]) => ['last_touch', 'created', 'status', 'pipeline_company', 'owner'].includes(k))
      .map(([k, v]) => `<div class="tribe-fm-row"><span class="tribe-fm-key">${escapeHtml(k)}</span><span class="tribe-fm-val">${escapeHtml(v)}</span></div>`)
      .join('');
    const commitments = d.active_commitments
      ? `<div class="tribe-section-label">Active commitments</div><pre class="tribe-section-body">${escapeHtml(d.active_commitments)}</pre>`
      : '';
    const log = d.interaction_log
      ? `<div class="tribe-section-label">Interaction log</div><pre class="tribe-section-body">${escapeHtml(d.interaction_log)}</pre>`
      : '';
    panel.innerHTML = `
      <div class="tribe-row-name">${escapeHtml(d.name)} <span class="tribe-row-meta">${escapeHtml(d.slug)}</span></div>
      ${orgBlock}
      <div class="tribe-fm-grid">${fmRows}</div>
      ${commitments}
      ${log}`;
  } catch (e) {
    panel.innerHTML = `${orgBlock}<div class="tribe-row-error">CRM record failed to load.</div>`;
  }
}

// ============================================================
// Phase 1.76: /threads page
// Sectioned by recency (today / this week / older), drill-down expands
// the thread body. Read-only - mutations belong to the /thread skill.
// ============================================================
const _THREADS_BUCKET_LABEL = {
  today: 'Today',
  this_week: 'This week',
  older: 'Older',
};

async function renderThreads(params) {
  const r = await authFetch('/threads');
  if (!r.ok) {
    document.getElementById('canvas').innerHTML = '<div class="card">Failed to load Threads.</div>';
    return;
  }
  const d = await r.json();
  const countEl = document.getElementById('threads-count');
  if (countEl) countEl.textContent = d.total;

  if (d.total === 0) {
    document.getElementById('canvas').innerHTML = `
      <header class="pulse-hero">
        <div class="pulse-breadcrumb">${_breadcrumb('01', 'Today · Threads', 'None active', d.data_time)}</div>
        <h1 class="pulse-greeting">No active threads.</h1>
        <p class="pulse-subhead">Use the <code>/thread</code> skill to open one. Active threads land in <code>threads/business/</code> and surface here.</p>
      </header>`;
    renderSyncIndicator(d);
    await trackPageView('threads');
    return;
  }

  // Group by bucket, preserving server-supplied bucket_order.
  const grouped = {};
  for (const t of (d.threads || [])) {
    (grouped[t.bucket] = grouped[t.bucket] || []).push(t);
  }
  const renderRow = t => {
    const days = t.days_since;
    const daysStr = days === null || days === undefined
      ? 'no date'
      : days === 0 ? 'today'
      : days === 1 ? '1d'
      : `${days}d`;
    return `
      <div class="card thread-row"
           data-path="${escapeHtml(t.path)}"
           data-id="${escapeHtml(t.id)}"
           data-title="${escapeHtml(t.title)}"
           data-bucket="${escapeHtml(t.bucket)}"
           data-freshness="${escapeHtml(d.data_time)}" data-stale="${freshnessLevel(d.data_time)}">
        <div class="thread-row-main">
          <div class="thread-row-title">${escapeHtml(t.title)}</div>
          <div class="thread-row-id">${escapeHtml(t.id)}</div>
        </div>
        <div class="thread-row-days">${escapeHtml(daysStr)}</div>
      </div>`;
  };
  const sections = (d.bucket_order || []).map(b => {
    const items = grouped[b] || [];
    if (items.length === 0) return '';
    const label = _THREADS_BUCKET_LABEL[b] || b;
    return `
      <h3 class="thread-section-head" data-bucket="${escapeHtml(b)}">
        <span class="thread-section-title">${escapeHtml(label)}</span>
        <span class="thread-section-count">${escapeHtml(items.length)}</span>
      </h3>
      <div class="list-card">${items.map(renderRow).join('')}</div>`;
  }).join('');

  const countChips = (d.bucket_order || [])
    .map(b => `<span class="cat-chip">${escapeHtml(_THREADS_BUCKET_LABEL[b] || b)} ${escapeHtml(d.counts[b] || 0)}</span>`)
    .join('');

  document.getElementById('canvas').innerHTML = `
    <header class="pulse-hero">
      <div class="pulse-breadcrumb">${_breadcrumb('01', 'Today · Threads', `${d.total} active`, d.data_time)}</div>
      <h1 class="pulse-greeting">${escapeHtml(d.total)} active thread${d.total === 1 ? '' : 's'}.</h1>
      <p class="pulse-subhead">Click any row to read the thread body. Mutations (log, hold, close) stay on the <code>/thread</code> skill.</p>
    </header>
    <div class="cat-chips">${countChips}</div>
    ${sections}`;

  document.querySelectorAll('.thread-row').forEach(el => {
    el.addEventListener('click', () => _threadToggleExpand(el));
  });
  // Phase 1.77: deep-link from Pulse footer card -> auto-focus thread row.
  // Phase 1.146: also accept a path-keyed focus (used by /critical Open
  // since flagged threads ref=path). Try id first, fall back to path.
  const threadFocus = params && params.get ? params.get('focus') : null;
  if (threadFocus) {
    let row = document.querySelector(`.thread-row[data-id="${cssEscapeAttr(threadFocus)}"]`);
    if (!row) row = document.querySelector(`.thread-row[data-path="${cssEscapeAttr(threadFocus)}"]`);
    if (row) _focusRow(row, () => _threadToggleExpand(row));
  }
  renderSyncIndicator(d);
  await trackPageView('threads');
}

async function _threadToggleExpand(rowEl) {
  const existing = document.querySelector('.thread-row-expanded');
  if (existing) {
    const isThisRow = existing.previousElementSibling === rowEl;
    existing.remove();
    if (isThisRow) return;
  }
  const path = rowEl.dataset.path;
  const title = rowEl.dataset.title || '(thread)';
  if (!path) return;
  const panel = document.createElement('div');
  panel.className = 'card thread-row-expanded';
  panel.innerHTML = '<div class="thread-row-loading">Loading...</div>';
  rowEl.insertAdjacentElement('afterend', panel);
  try {
    const r = await authFetch(`/threads/thread?path=${encodeURIComponent(path)}`);
    if (!r.ok) {
      panel.innerHTML = `<div class="thread-row-loading">Failed to load: HTTP ${escapeHtml(r.status)}</div>`;
      return;
    }
    const d = await r.json();
    // Phase 1.137: Flag-Important on the thread expanded panel.
    // kind='other' (threads are operational, not deal/task/draft).
    // ref=path so /critical's Open link can re-open the same thread.
    panel.innerHTML = `
      <div class="thread-row-head">
        <span class="thread-row-title-large">${escapeHtml(title)}</span>
        <span class="thread-row-path">${escapeHtml(d.path)}</span>
      </div>
      <pre class="thread-row-content">${escapeHtml(d.content)}</pre>
      <div class="pipe-flag-row">
        <button class="pipe-flag-btn" data-ref="${escapeHtml(d.path)}" data-label="${escapeHtml(title)}">
          <span class="pipe-flag-icon">!</span>
          <span class="pipe-flag-text">Flag as Important</span>
        </button>
      </div>`;
    const flagBtn = panel.querySelector('.pipe-flag-btn');
    if (flagBtn) _wireFlagImportant(flagBtn, 'other', '#/threads');
  } catch (e) {
    panel.innerHTML = `<div class="thread-row-loading">Failed to load.</div>`;
  }
}

// Phase 1.132: /critical (a.k.a. 'Important') list page. CEO-flagged
// items live in outputs/operations/bridge/critical-items.jsonl;
// fetched via /critical and surfaced here as a list with unmark
// buttons. The bento KPI tile links here.
async function renderCritical(params) {
  const r = await authFetch('/critical');
  if (!r.ok) {
    document.getElementById('canvas').innerHTML = '<div class="card">Failed to load Important items.</div>';
    return;
  }
  const d = await r.json();
  const countEl = document.getElementById('critical-count');
  if (countEl) countEl.textContent = d.total;

  // Phase 1.148: kind filter chip. ?kind=deal narrows the list to one
  // kind; the chip row is always rendered (when total > 0) so the CEO
  // can switch focus across kinds without losing context.
  const kindFilter = params && params.get ? params.get('kind') : null;
  const allItems = d.items || [];
  const kindCounts = {};
  for (const it of allItems) kindCounts[it.kind] = (kindCounts[it.kind] || 0) + 1;
  const visibleItems = kindFilter ? allItems.filter(it => it.kind === kindFilter) : allItems;

  if (d.total === 0) {
    document.getElementById('canvas').innerHTML = `
      <header class="pulse-hero">
        <div class="pulse-breadcrumb">${_breadcrumb('01', 'Today · Important', 'Nothing flagged', d.data_time)}</div>
        <h1 class="pulse-greeting">Nothing flagged.</h1>
        <p class="pulse-subhead">Flag any deal, task, draft, or conversation as Important from its row. Flagged items land here and surface in the Pulse Important tile.</p>
      </header>
      ${await _criticalRestoreFootHtml()}`;
    _wireCriticalRestoreFoot();
    renderSyncIndicator(d);
    await trackPageView('critical');
    return;
  }

  const _kindIcon = (k) => {
    if (k === 'deal') return '#';
    if (k === 'task') return '*';
    if (k === 'draft') return '@';
    if (k === 'conversation') return '~';
    return '!';
  };

  // Phase 1.142 -> 1.143 -> 1.145: deep-link 'Open' to the source row
  // when the source page's focus handler can be reached from the
  // flagged ref. Per-source transform handles the cases where ref
  // shape differs from data-focus-attribute shape.
  const _focusParamFor = (it) => {
    const sp = it.source_page || '';
    const ref = it.ref || '';
    if (!ref) return null;
    if (sp === '#/pipeline')       return ref;             // data-company == ref
    if (sp === '#/inbox')          return ref;             // data-id == ref
    if (sp === '#/conversations')  return ref;             // data-id == ref
    if (sp === '#/tasks')          return ref;             // data-task-key == ref
    if (sp === '#/approvals')      return ref;             // data-path == ref
    if (sp === '#/day')            return ref;             // {date}T{time}; handler matches on time suffix
    if (sp === '#/threads')        return ref;             // data-path fallback in /threads focus handler (Phase 1.146)
    if (sp === '#/studio')         return ref;             // data-path == ref
    if (sp === '#/investors' && ref.startsWith('investor-')) {
      return ref.slice('investor-'.length);                // data-num == numeric tail
    }
    return null;
  };
  const _openHref = (it) => {
    if (!it.source_page) return '';
    const focus = _focusParamFor(it);
    if (focus) return `${it.source_page}?focus=${encodeURIComponent(focus)}`;
    return it.source_page;
  };
  const rows = visibleItems.map(it => `
    <div class="card critical-row"
         data-id="${escapeHtml(it.id)}"
         data-kind="${escapeHtml(it.kind)}"
         data-ref="${escapeHtml(it.ref)}"
         data-freshness="${escapeHtml(it.ts)}" data-stale="${freshnessLevel(it.ts)}">
      <span class="critical-kind" data-kind="${escapeHtml(it.kind)}" title="${escapeHtml(it.kind)}">${_kindIcon(it.kind)}</span>
      <div class="critical-row-main">
        <div class="critical-row-label">${escapeHtml(it.label)}</div>
        <div class="critical-row-meta">
          ${escapeHtml(it.kind)} &middot; flagged ${escapeHtml(formatRelative(it.ts))}${it.note ? ' &middot; ' + escapeHtml(it.note) : ''}
        </div>
      </div>
      <div class="critical-row-actions">
        ${it.source_page ? `<a class="critical-row-open" href="${escapeHtml(_openHref(it))}" onclick="event.stopPropagation()">Open</a>` : ''}
        <button class="critical-row-unmark" data-id="${escapeHtml(it.id)}" title="Remove from Important">Unflag</button>
      </div>
    </div>`).join('');

  // Phase 1.148: filter chip row. 'All' shows everything; each kind
  // chip filters by that kind. Counts are static (always show the
  // full count regardless of active filter). Chips routed via hash
  // so back/forward navigation works.
  const _kinds = ['deal', 'task', 'draft', 'conversation', 'other'];
  const _kindChips = _kinds
    .filter(k => kindCounts[k] && kindCounts[k] > 0)
    .map(k => `<a class="critical-filter-chip${kindFilter === k ? ' is-active' : ''}" href="#/critical?kind=${escapeHtml(k)}">${escapeHtml(k)} <span class="critical-filter-count">${escapeHtml(kindCounts[k])}</span></a>`);
  const allChip = `<a class="critical-filter-chip${!kindFilter ? ' is-active' : ''}" href="#/critical">All <span class="critical-filter-count">${escapeHtml(d.total)}</span></a>`;
  const filterRow = `<div class="critical-filter-row">${allChip}${_kindChips.join('')}</div>`;

  const filterCrumb = kindFilter
    ? `${kindFilter} &middot; ${visibleItems.length} of ${d.total}`
    : `${d.total} flagged`;

  document.getElementById('canvas').innerHTML = `
    <header class="pulse-hero">
      <div class="pulse-breadcrumb">${_breadcrumb('01', 'Today · Important', filterCrumb, d.data_time)}</div>
      <h1 class="pulse-greeting">${escapeHtml(d.total)} item${d.total === 1 ? '' : 's'} flagged.</h1>
      <p class="pulse-subhead">CEO-flagged items in attention order. Click <strong>Open</strong> to jump to the source row, or <strong>Unflag</strong> to remove from Important.</p>
    </header>
    ${filterRow}
    <div class="list-card">${rows || '<div class="critical-empty-filter">No items match this filter.</div>'}</div>
    ${await _criticalRestoreFootHtml()}`;

  document.querySelectorAll('.critical-row-unmark').forEach(btn => {
    btn.addEventListener('click', async (e) => {
      e.stopPropagation();
      const id = btn.dataset.id;
      btn.textContent = 'Unflagging...';
      btn.disabled = true;
      try {
        const resp = await authFetch('/critical/unmark', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ id }),
        });
        if (!resp.ok) {
          btn.textContent = 'Failed - retry';
          btn.disabled = false;
          showToast('Unflag failed', 'check daemon log');
          return;
        }
        showToast('Unflagged', 'item removed from Important');
        renderCurrentPage();
      } catch (err) {
        btn.textContent = 'Failed - retry';
        btn.disabled = false;
        showToast('Unflag failed', 'check daemon log');
      }
    });
  });
  _wireCriticalRestoreFoot();

  renderSyncIndicator(d);
  await trackPageView('critical');
}

// Restore footer for the /critical page - lists recently-unflagged
// items so the CEO can re-flag a mistaken unmark. Mirrors the
// /approvals + /tasks recently-X footers.
async function _criticalRestoreFootHtml() {
  try {
    const r = await authFetch('/critical/recent-unmarked?limit=10');
    if (!r.ok) return '';
    const d = await r.json();
    const items = d.items || [];
    if (items.length === 0) return '';
    return `
      <div class="critical-restore-foot" id="critical-restore-foot" data-expandable="1">
        <span class="critical-restore-toggle">Recently unflagged &middot; ${escapeHtml(items.length)}</span>
        <span class="critical-restore-caret">&#9656;</span>
      </div>
      <div class="critical-restore-expanded" id="critical-restore-expanded" hidden>
        ${items.map(it => `
          <div class="critical-restore-row" data-id="${escapeHtml(it.id)}" data-kind="${escapeHtml(it.kind)}" data-ref="${escapeHtml(it.ref)}" data-label="${escapeHtml(it.label)}" data-source-page="${escapeHtml(it.source_page)}">
            <span class="critical-restore-label">${escapeHtml(it.label)}</span>
            <span class="critical-restore-meta">${escapeHtml(it.kind)} &middot; ${escapeHtml(formatRelative(it.ts))}</span>
            <button class="critical-restore-btn">Re-flag</button>
          </div>`).join('')}
      </div>`;
  } catch (e) {
    return '';
  }
}

function _wireCriticalRestoreFoot() {
  const foot = document.getElementById('critical-restore-foot');
  const panel = document.getElementById('critical-restore-expanded');
  if (foot && panel) {
    foot.addEventListener('click', () => {
      const open = !panel.hidden;
      panel.hidden = open;
      foot.querySelector('.critical-restore-caret').textContent = open ? '▸' : '▾';
    });
  }
  document.querySelectorAll('.critical-restore-btn').forEach(btn => {
    btn.addEventListener('click', async (e) => {
      e.stopPropagation();
      const row = btn.closest('.critical-restore-row');
      if (!row) return;
      btn.textContent = 'Re-flagging...';
      btn.disabled = true;
      try {
        const resp = await authFetch('/critical/mark', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            kind: row.dataset.kind,
            ref: row.dataset.ref,
            label: row.dataset.label,
            source_page: row.dataset.sourcePage || '',
          }),
        });
        if (!resp.ok) {
          btn.textContent = 'Failed - retry';
          btn.disabled = false;
          return;
        }
        showToast('Re-flagged', 'item back in Important');
        renderCurrentPage();
      } catch (err) {
        btn.textContent = 'Failed - retry';
        btn.disabled = false;
      }
    });
  });
}

async function renderTribe(params) {
  const r = await authFetch('/tribe');
  if (!r.ok) {
    document.getElementById('canvas').innerHTML = '<div class="card">Failed to load Tribe.</div>';
    return;
  }
  const d = await r.json();
  const countEl = document.getElementById('tribe-count');
  if (countEl) countEl.textContent = d.members.length;
  if (d.members.length === 0) {
    document.getElementById('canvas').innerHTML = `
      <header class="pulse-hero">
        <div class="pulse-breadcrumb">${_breadcrumb('05', 'Tribe', 'No members')}</div>
        <h1 class="pulse-greeting">No Tribe yet.</h1>
        <p class="pulse-subhead">No Tribe contacts found in <code>crm/contacts/</code>.</p>
      </header>
      <div class="card">No Tribe members found.</div>`;
    renderSyncIndicator(d);
    await trackPageView('tribe');
    return;
  }
  const counts = Object.entries(d.counts)
    .sort((a, b) => b[1] - a[1])
    .map(([role, n]) => `<span class="cat-chip">${escapeHtml(role)} ${escapeHtml(n)}</span>`)
    .join('');
  const renderTribeRow = m => {
    const days = m.days_since_touch;
    const daysStr = days === null || days === undefined
      ? 'no log'
      : days === 0 ? 'today'
      : days === 1 ? '1 day'
      : `${days} days`;
    const roleSlug = (m.role || 'other').toLowerCase().replace(/[^a-z]+/g, '-');
    // Phase 1.37: the role cell shows the org title + department from the
    // roster; falls back to the relationship_type for CRM-only members.
    const orgLine = m.title
      ? escapeHtml(m.title) + (m.department ? ' &middot; ' + escapeHtml(m.department) : '')
      : escapeHtml(m.role || '');
    return `
      <div class="card tribe-row" data-role-slug="${escapeHtml(roleSlug)}" data-slug="${escapeHtml(m.slug || '')}"
           data-name="${escapeHtml(m.name)}" data-title="${escapeHtml(m.title || '')}"
           data-department="${escapeHtml(m.department || '')}" data-reports-to="${escapeHtml(m.reports_to || '')}"
           data-telegram="${escapeHtml(m.telegram || '')}"
           data-freshness="${escapeHtml(d.data_time)}" data-stale="${freshnessLevel(d.data_time)}">
        <div class="tribe-name">${m.role === 'tribe-leadership' ? '<strong>' : ''}${escapeHtml(m.name)}${m.role === 'tribe-leadership' ? '</strong>' : ''}</div>
        <div class="tribe-role">${orgLine}</div>
        <div class="tribe-touch">${escapeHtml(daysStr)}</div>
      </div>`;
  };
  // Phase 1.68: group members by role. Leadership first.
  const roleOrder = ['tribe-leadership', 'tribe'];
  const tribeGrouped = {};
  for (const m of d.members) {
    (tribeGrouped[m.role] = tribeGrouped[m.role] || []).push(m);
  }
  for (const k of Object.keys(tribeGrouped)) {
    if (!roleOrder.includes(k)) roleOrder.push(k);
  }
  const tribeSections = roleOrder
    .filter(r => tribeGrouped[r] && tribeGrouped[r].length)
    .map(r => {
      const slug = r.toLowerCase().replace(/[^a-z]+/g, '-');
      const label = r === 'tribe-leadership' ? 'Tribe leadership' : r === 'tribe' ? 'Tribe' : r;
      return `
        <h3 class="tribe-section-head" data-role-slug="${escapeHtml(slug)}">
          <span class="tribe-section-title">${escapeHtml(label)}</span>
          <span class="tribe-section-count">${escapeHtml(tribeGrouped[r].length)}</span>
        </h3>
        <div class="list-card">${tribeGrouped[r].map(renderTribeRow).join('')}</div>`;
    }).join('');
  document.getElementById('canvas').innerHTML = `
    <header class="pulse-hero">
      <div class="pulse-breadcrumb">${_breadcrumb('05', 'Tribe', `${d.members.length} members`, d.data_time)}</div>
      <h1 class="pulse-greeting">${escapeHtml(d.members.length)} on the Tribe.</h1>
      <p class="pulse-subhead">Click any row for the contact's recent interaction log.</p>
    </header>
    <div class="cat-chips">${counts}</div>
    ${tribeSections}`;
  document.querySelectorAll('.tribe-row').forEach(el => {
    el.addEventListener('click', () => _tribeToggleExpand(el));
  });
  // Phase 1.77: deep-link from Pulse footer card -> auto-focus member row.
  const tribeFocus = params && params.get ? params.get('focus') : null;
  if (tribeFocus) {
    const row = document.querySelector(`.tribe-row[data-slug="${cssEscapeAttr(tribeFocus)}"]`);
    if (row) _focusRow(row, () => _tribeToggleExpand(row));
  }
  renderSyncIndicator(d);
  await trackPageView('tribe');
}

// ============================================================
// Phase 1.35: /contacts page
// Every CRM contact the CEO sees - the CEO's own (crm/contacts/) plus
// every executive's (aggregated crm-central). Same row format as Tribe;
// grouped by relationship_type; an owner chip marks executive contacts.
// Drill-down keys on (owner, slug) - the same slug can recur per owner.
// ============================================================
async function renderContacts(params) {
  const r = await authFetch('/contacts');
  if (!r.ok) {
    document.getElementById('canvas').innerHTML = '<div class="card">Failed to load Contacts.</div>';
    return;
  }
  const d = await r.json();
  const countEl = document.getElementById('contacts-count');
  if (countEl) countEl.textContent = d.total;

  if (!d.contacts || d.contacts.length === 0) {
    document.getElementById('canvas').innerHTML = `
      <header class="pulse-hero">
        <div class="pulse-breadcrumb">${_breadcrumb('05', 'People · Contacts', 'None', d.data_time)}</div>
        <h1 class="pulse-greeting">No contacts.</h1>
        <p class="pulse-subhead">No contacts found in <code>crm/contacts/</code> or the aggregated executive CRM.</p>
      </header>`;
    renderSyncIndicator(d);
    await trackPageView('contacts');
    return;
  }

  const counts = Object.entries(d.counts || {})
    .sort((a, b) => b[1] - a[1])
    .map(([t, n]) => `<span class="cat-chip">${escapeHtml(t.replace(/-/g, ' '))} ${escapeHtml(n)}</span>`)
    .join('');

  const renderContactRow = m => {
    const days = m.days_since_touch;
    const daysStr = days === null || days === undefined ? 'no log'
      : days === 0 ? 'today' : days === 1 ? '1 day' : `${days} days`;
    // Owner chip only for executive contacts - the CEO's own are the default.
    const ownerChip = m.owner && m.owner !== 'ceo'
      ? ` <span class="cat-chip">${escapeHtml(m.owner_label || m.owner)}</span>` : '';
    const company = m.company ? ` &middot; ${escapeHtml(m.company)}` : '';
    return `
      <div class="card tribe-row contact-row" data-owner="${escapeHtml(m.owner)}" data-slug="${escapeHtml(m.slug)}" data-freshness="${escapeHtml(d.data_time)}" data-stale="${freshnessLevel(d.data_time)}">
        <div class="tribe-name">${escapeHtml(m.name)}</div>
        <div class="tribe-role">${escapeHtml((m.relationship_type || 'other').replace(/-/g, ' '))}${company}${ownerChip}</div>
        <div class="tribe-touch">${escapeHtml(daysStr)}</div>
      </div>`;
  };

  // Group by relationship_type, like Tribe groups by role. Biggest first.
  const grouped = {};
  for (const m of d.contacts) {
    (grouped[m.relationship_type] = grouped[m.relationship_type] || []).push(m);
  }
  const typeOrder = Object.keys(grouped).sort((a, b) => grouped[b].length - grouped[a].length);
  const sections = typeOrder.map(t => {
    const slug = t.toLowerCase().replace(/[^a-z0-9]+/g, '-');
    const label = t.replace(/-/g, ' ');
    return `
      <h3 class="tribe-section-head" data-role-slug="${escapeHtml(slug)}">
        <span class="tribe-section-title">${escapeHtml(label)}</span>
        <span class="tribe-section-count">${escapeHtml(grouped[t].length)}</span>
      </h3>
      <div class="list-card">${grouped[t].map(renderContactRow).join('')}</div>`;
  }).join('');

  document.getElementById('canvas').innerHTML = `
    <header class="pulse-hero">
      <div class="pulse-breadcrumb">${_breadcrumb('05', 'People · Contacts', `${d.total} contacts`, d.data_time)}</div>
      <h1 class="pulse-greeting">${escapeHtml(d.total)} contacts.</h1>
      <p class="pulse-subhead">Every CRM contact you see - yours and all executives' combined. Click any row for the interaction log.</p>
    </header>
    <div class="cat-chips">${counts}</div>
    ${sections}`;
  document.querySelectorAll('.contact-row').forEach(el => {
    el.addEventListener('click', () => _contactsToggleExpand(el));
  });
  const focus = params && params.get ? params.get('focus') : null;
  if (focus) {
    const row = document.querySelector(`.contact-row[data-slug="${cssEscapeAttr(focus)}"]`);
    if (row) _focusRow(row, () => _contactsToggleExpand(row));
  }
  renderSyncIndicator(d);
  await trackPageView('contacts');
}

async function _contactsToggleExpand(rowEl) {
  const existing = document.querySelector('.contact-row-expanded');
  if (existing) {
    const isThisRow = existing.previousElementSibling === rowEl;
    existing.remove();
    if (isThisRow) return;
  }
  const owner = rowEl.dataset.owner;
  const slug = rowEl.dataset.slug;
  if (!owner || !slug) return;
  const panel = document.createElement('div');
  panel.className = 'card tribe-row-expanded contact-row-expanded';
  panel.innerHTML = '<div class="tribe-row-loading">Loading...</div>';
  rowEl.insertAdjacentElement('afterend', panel);
  try {
    const r = await authFetch(`/contacts/contact?owner=${encodeURIComponent(owner)}&slug=${encodeURIComponent(slug)}`);
    if (!r.ok) {
      panel.innerHTML = `<div class="tribe-row-error">Failed to load: HTTP ${escapeHtml(r.status)}</div>`;
      return;
    }
    const d = await r.json();
    const fm = d.frontmatter || {};
    const fmRows = Object.entries(fm)
      .filter(([k]) => ['relationship_type', 'company', 'last_touch', 'created', 'status'].includes(k))
      .map(([k, v]) => `<div class="tribe-fm-row"><span class="tribe-fm-key">${escapeHtml(k)}</span><span class="tribe-fm-val">${escapeHtml(v)}</span></div>`)
      .join('');
    const commitments = d.active_commitments
      ? `<div class="tribe-section-label">Active commitments</div><pre class="tribe-section-body">${escapeHtml(d.active_commitments)}</pre>`
      : '';
    const log = d.interaction_log
      ? `<div class="tribe-section-label">Interaction log</div><pre class="tribe-section-body">${escapeHtml(d.interaction_log)}</pre>`
      : '';
    panel.innerHTML = `
      <div class="tribe-row-name">${escapeHtml(d.name)} <span class="tribe-row-meta">${escapeHtml(d.owner_label || d.owner)} &middot; ${escapeHtml(d.slug)}</span></div>
      <div class="tribe-fm-grid">${fmRows}</div>
      ${commitments}
      ${log}`;
  } catch (e) {
    panel.innerHTML = '<div class="tribe-row-error">Failed to load.</div>';
  }
}

async function renderCapabilities() {
  const r = await authFetch('/capabilities');
  if (!r.ok) {
    document.getElementById('canvas').innerHTML = '<div class="card">Failed to load Capabilities.</div>';
    return;
  }
  const d = await r.json();
  const countEl = document.getElementById('capabilities-count');
  if (countEl) countEl.textContent = d.count;
  if (d.skills.length === 0) {
    document.getElementById('canvas').innerHTML = `
      <header class="pulse-hero">
        <div class="pulse-breadcrumb">${_breadcrumb('02', 'Work · Capabilities', 'No skills')}</div>
        <h1 class="pulse-greeting">No skills loaded.</h1>
        <p class="pulse-subhead">No SKILL.md files found in <code>.claude/skills/</code>.</p>
      </header>
      <div class="card">No skills found.</div>`;
    renderSyncIndicator(d);
    await trackPageView('capabilities');
    return;
  }
  // Group skills by category in the order the server gave us.
  const order = d.category_order || ['Intel','Communication','Content','CRM','Design','Strategy','Operations'];
  const counts = d.category_counts || {};
  const grouped = {};
  for (const s of d.skills) {
    (grouped[s.category] = grouped[s.category] || []).push(s);
  }
  const sections = order
    .filter(cat => grouped[cat] && grouped[cat].length)
    .map(cat => {
      const rows = grouped[cat].map(s => {
        const cap = s.capability || {};
        const what = cap.what || s.description || '';
        return `
        <div class="card cap-row" data-slug="${escapeHtml(s.slug)}" data-category="${escapeHtml(cat)}" data-freshness="${escapeHtml(d.data_time)}" data-stale="${freshnessLevel(d.data_time)}">
          <div class="cap-name">/${escapeHtml(s.name)}${s.version ? ` <span class="cap-ver">v${escapeHtml(s.version)}</span>` : ''}</div>
          <div class="cap-desc">${escapeHtml(what)}</div>
          ${cap.how ? `<div class="cap-meta"><span class="cap-meta-k">How</span> ${escapeHtml(cap.how)}</div>` : ''}
          ${cap.when ? `<div class="cap-meta"><span class="cap-meta-k">When</span> ${escapeHtml(cap.when)}</div>` : ''}
        </div>`;
      }).join('');
      return `
        <h3 class="cap-section-head" data-category="${escapeHtml(cat)}">
          <span class="cap-section-title">${escapeHtml(cat)}</span>
          <span class="cap-section-count">${escapeHtml(counts[cat] || grouped[cat].length)}</span>
        </h3>
        <div class="list-card">${rows}</div>`;
    }).join('');
  document.getElementById('canvas').innerHTML = `
    <header class="pulse-hero">
      <div class="pulse-breadcrumb">${_breadcrumb('02', 'Work · Capabilities', `${d.count} skills`, d.data_time)}</div>
      <h1 class="pulse-greeting">${escapeHtml(d.count)} skills available.</h1>
      <p class="pulse-subhead">Every action this workspace can take, grouped by domain. Click any card for the SKILL.md or run via <code>&#8984;K</code>.</p>
    </header>
    ${sections}`;
  document.querySelectorAll('.cap-row').forEach(el => {
    el.addEventListener('click', () => _capToggleExpand(el));
  });
  renderSyncIndicator(d);
  await trackPageView('capabilities');
}

async function _capToggleExpand(rowEl) {
  const existing = document.querySelector('.cap-row-expanded');
  if (existing) {
    const isThisRow = existing.previousElementSibling === rowEl;
    existing.remove();
    if (isThisRow) return;
  }
  const slug = rowEl.dataset.slug;
  if (!slug) return;
  const panel = document.createElement('div');
  panel.className = 'card cap-row-expanded';
  panel.innerHTML = '<div class="cap-row-loading">Loading...</div>';
  rowEl.insertAdjacentElement('afterend', panel);
  try {
    const r = await authFetch(`/capabilities/skill?slug=${encodeURIComponent(slug)}`);
    if (!r.ok) {
      panel.innerHTML = `<div class="cap-row-error">Failed to load: HTTP ${escapeHtml(r.status)}</div>`;
      return;
    }
    const d = await r.json();
    panel.innerHTML = `
      <div class="cap-row-slug">${escapeHtml(d.slug)} <span class="cap-row-size">(${escapeHtml(d.size)} bytes)</span></div>
      <pre class="cap-row-content">${escapeHtml(d.content)}</pre>`;
  } catch (e) {
    panel.innerHTML = `<div class="cap-row-error">Failed to load.</div>`;
  }
}

async function _libraryToggleExpand(rowEl) {
  // Collapse any existing expanded panel.
  const existing = document.querySelector('.lib-row-expanded');
  if (existing) {
    const isThisRow = existing.previousElementSibling === rowEl;
    existing.remove();
    if (isThisRow) return;  // click on already-expanded row -> just collapse
  }
  const path = rowEl.dataset.path;
  if (!path) return;
  // Show a loading panel immediately.
  const panel = document.createElement('div');
  panel.className = 'card lib-row-expanded';
  panel.innerHTML = '<div class="lib-row-loading">Loading...</div>';
  rowEl.insertAdjacentElement('afterend', panel);
  try {
    const r = await authFetch(`/library/note?path=${encodeURIComponent(path)}`);
    if (!r.ok) {
      panel.innerHTML = `<div class="lib-row-error">Failed to load: HTTP ${escapeHtml(r.status)}</div>`;
      return;
    }
    const d = await r.json();
    panel.innerHTML = `
      <div class="lib-row-path">${escapeHtml(d.path)} <span class="lib-row-size">(${escapeHtml(d.size)} bytes)</span></div>
      <pre class="lib-row-content">${escapeHtml(d.content)}</pre>`;
  } catch (e) {
    panel.innerHTML = `<div class="lib-row-error">Failed to load.</div>`;
  }
}

async function renderLibrary(params) {
  const r = await authFetch('/library');
  if (!r.ok) {
    document.getElementById('canvas').innerHTML = '<div class="card">Failed to load Library.</div>';
    return;
  }
  const d = await r.json();
  const countEl = document.getElementById('library-count');
  if (countEl) countEl.textContent = d.total;
  if (d.notes.length === 0) {
    document.getElementById('canvas').innerHTML = `
      <header class="pulse-hero">
        <div class="pulse-breadcrumb">${_breadcrumb('02', 'Work · Library', 'No notes')}</div>
        <h1 class="pulse-greeting">Library empty.</h1>
        <p class="pulse-subhead">No knowledge notes found in <code>knowledge/</code>.</p>
      </header>
      <div class="card">No notes found in knowledge/.</div>`;
    renderSyncIndicator(d);
    await trackPageView('library');
    return;
  }
  // Phase 1.66: group notes by type. Untyped notes get an explicit '(untyped)'
  // bucket at the end so they don't disappear.
  const typeOrder = (d.type_order || []).slice();
  const grouped = {};
  for (const n of d.notes) {
    const t = n.type || '(untyped)';
    if (!grouped[t]) grouped[t] = [];
    grouped[t].push(n);
  }
  // Untyped goes last if present and not already in type_order.
  if (grouped['(untyped)'] && !typeOrder.includes('(untyped)')) typeOrder.push('(untyped)');
  // If we see types that the server didn't list (defensive), append them.
  for (const t of Object.keys(grouped)) {
    if (!typeOrder.includes(t)) typeOrder.push(t);
  }

  const rowHtml = (n) => {
    const updatedStr = n.updated ? escapeHtml(n.updated) : 'no date';
    return `
      <div class="card lib-row" data-type="${escapeHtml(n.type || 'untyped')}" data-path="${escapeHtml(n.path)}" data-freshness="${escapeHtml(d.data_time)}" data-stale="${freshnessLevel(d.data_time)}">
        <div class="lib-title">${escapeHtml(n.title)}</div>
        <div class="lib-meta">${updatedStr}${n.status ? ' &middot; ' + escapeHtml(n.status) : ''}</div>
      </div>`;
  };

  const sections = typeOrder
    .filter(t => grouped[t] && grouped[t].length)
    .map(t => `
      <h3 class="lib-section-head" data-type="${escapeHtml(t)}">
        <span class="lib-section-title">${escapeHtml(t)}</span>
        <span class="lib-section-count">${escapeHtml((d.counts || {})[t] || grouped[t].length)} total &middot; ${escapeHtml(grouped[t].length)} shown</span>
      </h3>
      <div class="list-card">${grouped[t].map(rowHtml).join('')}</div>`)
    .join('');

  document.getElementById('canvas').innerHTML = `
    <header class="pulse-hero">
      <div class="pulse-breadcrumb">${_breadcrumb('02', 'Work · Library', `${d.total} notes`, d.data_time)}</div>
      <h1 class="pulse-greeting">${escapeHtml(d.total)} notes in the library.</h1>
      <p class="pulse-subhead"><strong>${escapeHtml(d.notes.length)}</strong> most-recently-updated shown, grouped by type. Click any row to read inline.</p>
    </header>
    ${sections}`;
  document.querySelectorAll('.lib-row').forEach(el => {
    el.addEventListener('click', () => _libraryToggleExpand(el));
  });
  // Phase 1.79: deep-link from search results -> auto-expand the note row.
  const libFocus = params && params.get ? params.get('focus') : null;
  if (libFocus) {
    const row = document.querySelector(`.lib-row[data-path="${cssEscapeAttr(libFocus)}"]`);
    if (row) _focusRow(row, () => _libraryToggleExpand(row));
  }
  renderSyncIndicator(d);
  await trackPageView('library');
}

function _formatDayDate(iso) {
  // 'Friday, 18 May 2026' from an ISO date string.
  if (!iso) return '';
  const m = iso.match(/^(\d{4})-(\d{2})-(\d{2})/);
  if (!m) return iso;
  const dt = new Date(Date.UTC(+m[1], +m[2] - 1, +m[3]));
  const wk = ['Sunday','Monday','Tuesday','Wednesday','Thursday','Friday','Saturday'][dt.getUTCDay()];
  const mo = ['Jan','Feb','Mar','Apr','May','Jun','Jul','Aug','Sep','Oct','Nov','Dec'][dt.getUTCMonth()];
  return `${wk}, ${dt.getUTCDate()} ${mo} ${dt.getUTCFullYear()}`;
}

async function renderDay(params) {
  const r = await authFetch('/day');
  if (!r.ok) {
    document.getElementById('canvas').innerHTML = '<div class="card">Failed to load Day.</div>';
    return;
  }
  const d = await r.json();
  const dateLabel = _formatDayDate(d.date);

  if (d.events.length === 0) {
    document.getElementById('canvas').innerHTML = `
      <header class="pulse-hero">
        <div class="pulse-breadcrumb">${_breadcrumb('01', 'Today · Day', `${dateLabel} · ${escapeHtml(state.tzLabel)}`)}</div>
        <h1 class="pulse-greeting">Clear day.</h1>
        <p class="pulse-subhead">No calendar events found for today.${d.data_time ? ` Last sync ${escapeHtml(formatRelative(d.data_time))}.` : ''}</p>
      </header>`;
    renderSyncIndicator(d);
    await trackPageView('day');
    return;
  }

  // Hero stat strip metrics derived from events list.
  const total = d.events.length;
  const past = d.events.filter(e => e.is_past).length;
  const upcoming = total - past;
  const nextEvt = d.events.find(e => e.is_next);
  const nextLabel = nextEvt
    ? (nextEvt.minutes_until > 0
        ? (nextEvt.minutes_until < 60
            ? `${nextEvt.minutes_until}m`
            : `${Math.floor(nextEvt.minutes_until / 60)}h ${nextEvt.minutes_until % 60}m`)
        : 'now')
    : '-';
  const nextSubject = nextEvt ? nextEvt.subject : 'nothing upcoming';

  // Phase 1.87: v8 day-card layout with type-coloured accent dot. Type is
  // a best-effort heuristic over subject + location since the Exchange
  // calendar feed doesn't expose explicit categories.
  const _dayCardType = (subject, location) => {
    const s = (subject || '').toLowerCase();
    const l = (location || '').toLowerCase();
    if (/\blunch\b|\bdinner\b|\bbreakfast\b/.test(s)) return { kind: 'lunch', label: 'Break' };
    if (/\bfireside\b|\btribe\b|\bweekly\b/.test(s)) return { kind: 'tribe', label: 'Tribe' };
    if (/\bfocus\b|\bblock\b/.test(s)) return { kind: 'focus', label: 'Focus Block' };
    if (/\btravel\b|\bflight\b/.test(s)) return { kind: 'focus', label: 'Travel' };
    // Anything with a Zoom / Teams / Meet URL is a video meeting.
    if (l.startsWith('http')) {
      if (l.includes('zoom')) return { kind: 'meeting', label: 'Meeting · Zoom' };
      if (l.includes('teams')) return { kind: 'meeting', label: 'Meeting · Teams' };
      if (l.includes('meet.google')) return { kind: 'meeting', label: 'Meeting · Google Meet' };
      return { kind: 'meeting', label: 'Meeting · Video' };
    }
    if (l) return { kind: 'meeting', label: 'Meeting' };
    return { kind: 'focus', label: 'Focus Block' };
  };

  const rows = d.events.map(e => {
    const type = _dayCardType(e.subject, e.location);
    const classes = ['day-card'];
    if (e.is_past) classes.push('day-past');
    if (e.is_next) classes.push('day-next');
    const locText = e.location && !e.location.startsWith('http') ? e.location : '';
    const joinBtn = (e.location && e.location.startsWith('http'))
      ? `<a class="btn-sm btn-sm-primary" href="${escapeHtml(e.location)}" target="_blank" rel="noopener noreferrer" onclick="event.stopPropagation()">Join</a>`
      : '';
    const briefBits = [];
    if (locText) briefBits.push(escapeHtml(locText));
    const briefHtml = briefBits.length
      ? `<div class="dc-brief">${briefBits.join(' &middot; ')}</div>`
      : '';
    // Phase 1.139: Flag-Important pill in the day-card actions row.
    // Only on non-past events (past events are immutable history).
    // ref = `${date}T${time}` so a recurring meeting on different days
    // gets distinct entries; label = subject.
    const flagPill = e.is_past ? '' : `
      <button class="day-flag-btn pipe-flag-btn pipe-flag-btn-icon" title="Flag as Important" data-ref="${escapeHtml(`${d.date}T${e.time}`)}" data-label="${escapeHtml(`${e.subject} (${e.time})`)}"><span class="pipe-flag-icon">!</span></button>`;
    const actionInner = `${joinBtn}${flagPill}`;
    const actionsHtml = actionInner.trim() ? `<div class="dc-actions">${actionInner}</div>` : '';
    return `
      <div class="${classes.join(' ')}"
           data-type="${escapeHtml(type.kind)}"
           data-time="${escapeHtml(e.time)}"
           data-subject="${escapeHtml(e.subject)}"
           data-location="${escapeHtml(e.location || '')}"
           data-minutes-until="${escapeHtml(e.minutes_until)}"
           data-minutes-to-next="${escapeHtml(e.minutes_to_next === null || e.minutes_to_next === undefined ? '' : e.minutes_to_next)}"
           data-is-past="${e.is_past ? '1' : '0'}"
           data-is-next="${e.is_next ? '1' : '0'}"
           data-freshness="${escapeHtml(d.data_time)}" data-stale="${freshnessLevel(d.data_time)}">
        <div class="dc-time"><strong>${escapeHtml(e.time)}</strong></div>
        <div class="day-card-body">
          <div class="dc-type">${escapeHtml(type.label)}</div>
          <div class="dc-title">${e.is_next ? '<strong>' : ''}${escapeHtml(e.subject)}${e.is_next ? '</strong>' : ''}</div>
          ${briefHtml}
          ${actionsHtml}
        </div>
      </div>`;
  }).join('');

  document.getElementById('canvas').innerHTML = `
    <header class="pulse-hero">
      <div class="pulse-breadcrumb">${_breadcrumb('01', 'Today · Day', `${dateLabel} · ${escapeHtml(state.tzLabel)}`)}</div>
      <h1 class="pulse-greeting">Today.</h1>
      <p class="pulse-subhead">
        <strong>${escapeHtml(total)} event${total === 1 ? '' : 's'}</strong> &middot;
        <strong>${escapeHtml(upcoming)} upcoming</strong> &middot;
        <strong>${escapeHtml(past)} done</strong>.
        ${nextEvt ? `Next: <strong>${escapeHtml(nextEvt.time)} ${escapeHtml(nextEvt.subject)}</strong> in <strong>${escapeHtml(nextLabel)}</strong>.` : ''}
      </p>
    </header>
    <section class="day-hero">
      <div class="day-hero-stat"><strong>${escapeHtml(total)}</strong><span>Total</span></div>
      <span class="day-hero-divider"></span>
      <div class="day-hero-stat"><strong>${escapeHtml(upcoming)}</strong><span>Upcoming</span></div>
      <span class="day-hero-divider"></span>
      <div class="day-hero-stat"><strong>${escapeHtml(past)}</strong><span>Done</span></div>
      <span class="day-hero-divider"></span>
      <div class="day-hero-stat"><strong>${escapeHtml(nextLabel)}</strong><span>Until next</span></div>
      <span class="day-hero-divider"></span>
      <div class="day-hero-stat"><strong>${escapeHtml(state.tzLabel)}</strong><span>local time</span></div>
    </section>
    <div class="day-timeline">${rows}</div>`;
  document.querySelectorAll('.day-card').forEach(el => {
    el.addEventListener('click', () => _dayToggleExpand(el));
  });
  // Phase 1.139: wire Flag-Important on each day card. The pill is a
  // <button> inside .dc-actions; clicks must NOT bubble to the card's
  // toggle-expand handler. kind='other' since calendar events don't
  // fit the deal/task/draft/conversation taxonomy.
  document.querySelectorAll('.day-flag-btn').forEach(btn => {
    _wireFlagImportant(btn, 'other', '#/day');
  });
  // Phase 1.145: auto-focus from ?focus={date}T{time} (deep-link from
  // /critical Open). The day-card carries data-time; we match on a
  // fuzzier 'endsWith time' check because the URL focus param is
  // '{date}T{time}' (full ref) while the card only knows data-time.
  const dayFocus = params && params.get ? params.get('focus') : null;
  if (dayFocus) {
    const time = dayFocus.split('T').pop() || dayFocus;
    const row = document.querySelector(`.day-card[data-time="${cssEscapeAttr(time)}"]`);
    if (row) _focusRow(row, () => _dayToggleExpand(row));
  }
  renderSyncIndicator(d);
  await trackPageView('day');
}

function _fmtMinutes(mins) {
  if (mins === null || mins === undefined || Number.isNaN(mins)) return '';
  const abs = Math.abs(mins);
  if (abs < 60) return `${abs}m`;
  const hours = Math.floor(abs / 60);
  const rem = abs % 60;
  return rem === 0 ? `${hours}h` : `${hours}h ${rem}m`;
}

function _dayToggleExpand(rowEl) {
  const existing = document.querySelector('.day-row-expanded');
  if (existing) {
    const isThisRow = existing.previousElementSibling === rowEl;
    existing.remove();
    if (isThisRow) return;
  }
  const time = rowEl.dataset.time || '';
  const subject = rowEl.dataset.subject || '(unknown)';
  const location = rowEl.dataset.location || '';
  const minsUntil = parseInt(rowEl.dataset.minutesUntil, 10);
  const minsToNextRaw = rowEl.dataset.minutesToNext;
  const minsToNext = minsToNextRaw === '' || minsToNextRaw === undefined ? null : parseInt(minsToNextRaw, 10);
  const isPast = rowEl.dataset.isPast === '1';
  const isNext = rowEl.dataset.isNext === '1';

  let statusLine = '';
  if (Number.isFinite(minsUntil)) {
    if (minsUntil > 0) statusLine = `Starts in ${_fmtMinutes(minsUntil)}`;
    else if (minsUntil === 0) statusLine = 'Starting now';
    else statusLine = `Started ${_fmtMinutes(minsUntil)} ago`;
  }
  let pillClass = 'day-row-status';
  if (isNext) pillClass += ' day-row-status-next';
  else if (isPast) pillClass += ' day-row-status-past';

  let gapLine = '';
  if (minsToNext !== null && Number.isFinite(minsToNext)) {
    gapLine = `<div class="day-row-gap">${_fmtMinutes(minsToNext)} until next event</div>`;
  } else {
    gapLine = `<div class="day-row-gap">Last event of the day</div>`;
  }

  let locHtml = '';
  if (location) {
    if (location.startsWith('http')) {
      locHtml = `
        <div class="day-row-label">Location</div>
        <a class="day-row-join" href="${escapeHtml(location)}" target="_blank" rel="noopener noreferrer">Open meeting link</a>
        <div class="day-row-locfull">${escapeHtml(location)}</div>`;
    } else {
      locHtml = `
        <div class="day-row-label">Location</div>
        <div class="day-row-locfull">${escapeHtml(location)}</div>`;
    }
  } else {
    locHtml = `<div class="day-row-locfull day-row-muted">No location on file.</div>`;
  }

  const panel = document.createElement('div');
  panel.className = 'card day-row-expanded';
  panel.innerHTML = `
    <div class="day-row-heading">${escapeHtml(time)} &middot; ${escapeHtml(subject)}</div>
    <div class="${pillClass}">${escapeHtml(statusLine)}</div>
    ${gapLine}
    ${locHtml}`;
  rowEl.insertAdjacentElement('afterend', panel);
}

async function renderSettings() {
  const r = await authFetch('/settings');
  if (!r.ok) {
    document.getElementById('canvas').innerHTML = '<div class="card">Failed to load Settings.</div>';
    return;
  }
  const d = await r.json();

  // Phase 1.25: fetch ops data in parallel. UI degrades silently if absent.
  let ops = null;
  try {
    const opsR = await authFetch('/settings/ops');
    if (opsR.ok) ops = await opsR.json();
  } catch (e) {
    // ops is optional; UI degrades silently.
  }

  // Build daemon info card (preserved from earlier phases).
  const daemonCard = `
    <div class="card">
      <h3>Daemon</h3>
      <div>Version: ${escapeHtml(d.version)}</div>
      <div>PID: ${escapeHtml(d.pid)}</div>
      <div>Uptime: ${escapeHtml(Math.round(d.uptime_s / 60))} min</div>
      <div>User: ${escapeHtml(d.user)}</div>
      <div>Workspace: <code>${escapeHtml(d.workspace)}</code></div>
    </div>`;

  // Phase 1.157: config-history snapshots card. Shows what the CEO
  // could roll back to with `python scripts/bridge-daemon.py
  // --revert-config`. Only renders when at least one snapshot exists
  // (older daemons won't return the field at all).
  let snapshotsCard = '';
  if (Array.isArray(d.config_snapshots) && d.config_snapshots.length > 0) {
    const rows = d.config_snapshots.map((s, i) => {
      const tag = i === 0
        ? '<span class="settings-snap-tag settings-snap-current">current boot</span>'
        : i === 1
        ? '<span class="settings-snap-tag settings-snap-revert">would restore on --revert-config</span>'
        : '';
      const when = s.mtime_iso ? formatRelative(s.mtime_iso) : '-';
      const sizeKB = s.size_bytes != null ? `${Math.round(s.size_bytes / 1024 * 10) / 10} KB` : '';
      return `
        <div class="settings-snap-row">
          <span class="settings-snap-name">${escapeHtml(s.name)}</span>
          <span class="settings-snap-when">${escapeHtml(when)}</span>
          <span class="settings-snap-size">${escapeHtml(sizeKB)}</span>
          ${tag}
        </div>`;
    }).join('');
    snapshotsCard = `
      <div class="card">
        <h3>Config snapshots <span class="ops-foot">(last ${escapeHtml(d.config_snapshots.length)} of 3 kept)</span></h3>
        <div class="settings-snap-list">${rows}</div>
        <div class="ops-foot">
          Restore with: <code>python scripts/bridge-daemon.py --revert-config</code>
          (restart the daemon for the revert to apply).
        </div>
      </div>`;
  }

  // Build Components card (Phase 1.19 per-component freshness, preserved).
  const compRows = d.components.map(c => {
    const ago = formatRelative(c.data_time);
    const fresh = freshnessLevel(c.data_time);
    return `
      <div class="settings-comp-row" data-stale="${escapeHtml(fresh)}">
        <span class="settings-comp-name">${escapeHtml(c.name)}</span>
        <span class="settings-comp-age">${escapeHtml(ago)}</span>
        <span class="settings-comp-interval">${c.interval_s ? 'every ' + escapeHtml(c.interval_s) + 's' : ''}</span>
      </div>`;
  }).join('');
  const componentsCard = `
    <div class="card">
      <h3>Components</h3>
      <div class="settings-comp-list">${compRows}</div>
    </div>`;

  // Phase 1.25: telemetry summary card.
  let telemetryCard = '';
  if (ops && ops.telemetry && ops.telemetry.ok) {
    const t = ops.telemetry;
    const todayItems = Object.entries(t.today)
      .sort((a, b) => b[1] - a[1])
      .map(([k, v]) => `<span class="ops-stat-chip">${escapeHtml(k)} ${escapeHtml(v)}</span>`)
      .join('') || '<span class="ops-stat-muted">no events yet today</span>';
    const week = Object.entries(t.last_7d)
      .sort((a, b) => b[1] - a[1])
      .map(([k, v]) => `<span class="ops-stat-chip">${escapeHtml(k)} ${escapeHtml(v)}</span>`)
      .join('') || '<span class="ops-stat-muted">no events in last 7 days</span>';
    const lastEvent = t.last_event_ts
      ? `Last event: ${escapeHtml(formatRelative(t.last_event_ts))}`
      : 'No events recorded yet.';
    const sizeKB = t.file_size_bytes != null
      ? ` &middot; usage.jsonl: ${escapeHtml(Math.round(t.file_size_bytes / 1024))} KB`
      : '';
    telemetryCard = `
      <div class="card">
        <h3>Telemetry</h3>
        <div class="ops-section-label">Today (${escapeHtml(t.today_total)})</div>
        <div class="ops-stat-row">${todayItems}</div>
        <div class="ops-section-label">Last 7 days (${escapeHtml(t.last_7d_total)})</div>
        <div class="ops-stat-row">${week}</div>
        <div class="ops-foot">${lastEvent}${sizeKB}</div>
      </div>`;
  }

  // Phase 1.151: adoption-gate metrics card. Reads /telemetry/summary
  // and surfaces the four Phase 1 -> Phase 2 gate metrics from the
  // bridge spec. Fails silently if the endpoint is missing (older
  // daemon) - the rest of Settings still renders.
  let adoptionCard = '';
  try {
    const ar = await authFetch('/telemetry/summary?days=14');
    if (ar.ok) {
      const a = await ar.json();
      const m = a.metrics || {};
      const g = a.gate || {};
      const totals = a.totals || {};
      const _pct = (n) => `${Math.round((n || 0) * 100)}%`;
      const _row = (label, value, pass, target) => `
        <div class="settings-adopt-row" data-pass="${pass ? '1' : '0'}">
          <span class="settings-adopt-label">${escapeHtml(label)}</span>
          <span class="settings-adopt-value">${escapeHtml(value)}</span>
          <span class="settings-adopt-target">target ${escapeHtml(target)}</span>
          <span class="settings-adopt-status">${pass ? 'PASS' : 'BELOW'}</span>
        </div>`;
      adoptionCard = `
        <div class="card">
          <h3>Adoption gate <span class="ops-foot">(${escapeHtml(a.window_days)}-day window &middot; ends ${escapeHtml(a.window_end)})</span></h3>
          <div class="settings-adopt-list">
            ${_row('Avg tab time per day',     m.avg_tab_time_min_per_day + ' min', g.tab_time_pass,     '> ' + g.criteria.tab_time_threshold_min + ' min')}
            ${_row('Actions per day',           m.avg_actions_per_day,                g.actions_pass,      '> ' + g.criteria.actions_threshold)}
            ${_row('Browser-first weekdays',    _pct(m.browser_first_pct_weekdays),   g.browser_first_pass, '> ' + Math.round(g.criteria.browser_first_pct * 100) + '%')}
          </div>
          <div class="settings-adopt-foot">
            <strong>Gate verdict:</strong>
            <span class="settings-adopt-verdict" data-pass="${g.all_pass ? '1' : '0'}">${g.all_pass ? 'ALL PASS' : 'NOT YET'}</span>
            <span class="ops-foot">&middot; ${escapeHtml(totals.page_views || 0)} page views &middot; ${escapeHtml(totals.actions || 0)} actions &middot; ${escapeHtml(totals.browser_first_mornings || 0)}/${escapeHtml(totals.weekdays_in_window || 0)} browser-first weekday mornings</span>
          </div>
        </div>`;
    }
  } catch (e) {
    // Silent - the rest of Settings still renders.
  }

  // Phase 1.25: bridge.log tail card.
  let logCard = '';
  if (ops && ops.log_tail && ops.log_tail.ok) {
    const lines = ops.log_tail.lines.slice(-50);
    const linesHtml = lines.length
      ? lines.map(l => escapeHtml(l)).join('\n')
      : '(empty)';
    const sizeKB = ops.log_tail.size_bytes != null
      ? `${escapeHtml(Math.round(ops.log_tail.size_bytes / 1024))} KB`
      : 'unknown size';
    logCard = `
      <div class="card">
        <h3>Bridge log <span class="ops-foot">(last ${escapeHtml(lines.length)} lines &middot; ${sizeKB})</span></h3>
        <pre class="ops-log">${linesHtml}</pre>
      </div>`;
  }

  document.getElementById('canvas').innerHTML = `
    <header class="pulse-hero">
      <div class="pulse-breadcrumb">${_breadcrumb('06', 'Settings · Bridge daemon', `User ${d.user || '-'}`)}</div>
      <h1 class="pulse-greeting">Bridge daemon settings.</h1>
      <p class="pulse-subhead">Live per-component freshness, refresh cadences, and ops log tail.</p>
    </header>
    ${daemonCard}
    ${componentsCard}
    ${adoptionCard}
    ${snapshotsCard}
    ${telemetryCard}
    ${logCard}`;
  await trackPageView('settings');
}

async function launchAction(action, sessionId, cwd) {
  try {
    const r = await authFetch('/launch', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        action,
        session_id: sessionId || null,
        cwd: cwd || null,
        title: action,
      }),
    });
    if (!r.ok) {
      console.warn(`bridge: /launch returned ${r.status}`);
    }
  } catch (e) {
    console.warn('bridge: /launch failed:', e);
  }
}

// Spec section 3.3 deep-link: opens a Claude Code terminal session in
// the user's named window (wt -w 31c-<slug> on Windows, tmux 31c-<slug>
// on macOS) with BRIDGE_ORIGIN=browser, BRIDGE_ACTION=<action>, and a
// base64-encoded BRIDGE_CONTEXT JSON payload so the skill can pre-
// populate (e.g. conv_id for /email-respond). The Stop hook's
// origin-gated 'stay / browser' prompt fires on session end because of
// BRIDGE_ORIGIN. Used by the Inbox conversation panel's
// 'Continue in session' button; future surfaces (Studio, Approvals,
// Investors) can call this directly with their own action+context.
async function _continueInSession({action, title, context, sessionId, cwd}, btn) {
  if (btn) {
    btn.disabled = true;
    btn.textContent = 'Opening terminal...';
  }
  try {
    const r = await authFetch('/launch', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        action,
        title: title || action,
        session_id: sessionId || null,
        cwd: cwd || null,
        context: context || null,
      }),
    });
    if (!r.ok) {
      const detail = await r.text().catch(() => `HTTP ${r.status}`);
      console.warn('bridge: /launch failed:', detail);
      if (btn) btn.textContent = `Launch failed: ${r.status}`;
      return;
    }
    if (btn) {
      btn.textContent = 'Terminal opened';
      setTimeout(() => {
        btn.disabled = false;
        btn.textContent = 'Continue in session ›';
      }, 2000);
    }
  } catch (e) {
    console.warn('bridge: /launch error:', e);
    if (btn) btn.textContent = 'Launch error';
  }
}

// Per-tab page-leave tracking. trackPageView() emits one event per
// page-LEAVE (not per page-enter) so the duration_s carried by the
// event reflects real time-on-page. The adoption summarizer
// (scripts/bridge_daemon/adoption.py) reads duration_s to compute
// tab_time_minutes for the Phase 1 -> Phase 2 adoption gate; before
// this, the fallback added 30s per duration-less event which over-
// estimated tab-time by a factor of ~10x at normal navigation rates.
let _bridgeLastPage = null;
let _bridgeLastPageStartTs = null;
const _BRIDGE_MAX_DURATION_S = 86400;  // cap: 1 day, ignore tab-in-background-forever

async function _bridgeFlushLastPage() {
  if (_bridgeLastPage === null || _bridgeLastPageStartTs === null) return;
  const durSec = Math.round((Date.now() - _bridgeLastPageStartTs) / 1000);
  const capped = Math.max(0, Math.min(durSec, _BRIDGE_MAX_DURATION_S));
  const page = _bridgeLastPage;
  _bridgeLastPage = null;
  _bridgeLastPageStartTs = null;
  try {
    await authFetch('/telemetry/page-view', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ page, duration_s: capped }),
    });
  } catch (e) {
    // Telemetry failure must not block the UI.
  }
}

async function trackPageView(page) {
  // Idempotent on same-page calls. Each page's renderer calls
  // trackPageView() every time the polling loop sees a component
  // version bump (~ every 30-60s) - which is fine for re-render
  // but bad for telemetry: it would emit ~60 events per hour even
  // when the user never left the page. The math still added up
  // (adoption.py sums all duration_s), but usage.jsonl grew 10x
  // faster than needed.
  //
  // Fix: same-page calls are no-ops. Only navigation (different
  // page name) flushes the prior page + records a new start. The
  // visibilitychange / beforeunload listeners below still flush
  // the in-flight page when the session ends.
  if (page === _bridgeLastPage) return;
  await _bridgeFlushLastPage();
  _bridgeLastPage = page;
  _bridgeLastPageStartTs = Date.now();
}

// Flush the in-flight page when the tab goes hidden or the page
// unloads. Without this, the last page of every session never gets
// a duration record - the adoption summarizer falls back to 30s for
// that page and the metric remains slightly noisy at session
// boundaries.
window.addEventListener('visibilitychange', () => {
  if (document.visibilityState === 'hidden') {
    _bridgeFlushLastPage();
  }
});
window.addEventListener('beforeunload', () => {
  // beforeunload can't await; fire-and-forget.
  _bridgeFlushLastPage();
});

async function renderStub() {
  document.getElementById('canvas').innerHTML = `
    <header class="pulse-hero">
      <div class="pulse-breadcrumb">${_breadcrumb('02', 'Work · Conversations', 'Coming in Phase 2')}</div>
      <h1 class="pulse-greeting">Conversations.</h1>
      <p class="pulse-subhead">Unified Telegram + Signal + ephemeral chat surface. Lands in Phase 2 (deferred while the Tribe Fireside daemon owns the telethon session).</p>
    </header>
    <div class="card">
      <p>This screen is not yet wired. Phase 1 ships Pulse, Inbox, and Settings.
         Day, Conversations, Capabilities, Library, Studio, Spaces, and Tribe land
         in Phase 2 once the Phase 1 adoption gate passes.</p>
      <p><a href="#/pulse">&laquo; back to Pulse</a></p>
    </div>`;
  await trackPageView('stub');
}

async function renderSearch(params) {
  const q = (params && params.get('q')) || '';
  if (!q) {
    document.getElementById('canvas').innerHTML = `
      <header class="pulse-hero">
        <div class="pulse-breadcrumb">${_breadcrumb('01', 'Today · Search', 'Ready')}</div>
        <h1 class="pulse-greeting">Search.</h1>
        <p class="pulse-subhead">Unified search across Inbox, Tribe, Tasks, Library, Studio, Day, Capabilities, Pipeline, and Investors. Type in the topbar to begin.</p>
      </header>
      <div class="card search-empty">Type a query in the bar above and press Enter.</div>`;
    renderSyncIndicator(null);
    await trackPageView('search');
    return;
  }
  const r = await authFetch(`/search?q=${encodeURIComponent(q)}&limit=10`);
  if (!r.ok) {
    document.getElementById('canvas').innerHTML = '<div class="card">Search failed.</div>';
    return;
  }
  const d = await r.json();
  renderSyncIndicator(d);
  if (d.total === 0) {
    document.getElementById('canvas').innerHTML = `
      <header class="pulse-hero">
        <div class="pulse-breadcrumb">${_breadcrumb('01', 'Today · Search', `"${q}" · No matches`)}</div>
        <h1 class="pulse-greeting">No matches.</h1>
        <p class="pulse-subhead">Nothing found for <strong>&quot;${escapeHtml(q)}&quot;</strong>. Try a shorter query or a different category.</p>
      </header>`;
    await trackPageView('search');
    return;
  }
  // Render categorized results.
  // Phase 1.78: each result is an <a> linking back to its source page.
  // Categories with focus-and-expand support (pipeline / investors / tribe)
  // append ?focus=<key>; the rest navigate to the page without focus.
  const linkCard = (href, titleHtml, metaHtml) => `
    <a class="card search-result search-result-link" href="${escapeHtml(href)}">
      <div class="search-result-title">${titleHtml}</div>
      <div class="search-result-meta">${metaHtml}</div>
    </a>`;
  const categoryRenderers = {
    inbox: (items) => items.map(r => linkCard(
      `#/inbox?focus=${encodeURIComponent(r.id || '')}`,
      `${r.unread ? '<strong>' : ''}${escapeHtml(r.subject)}${r.unread ? '</strong>' : ''}`,
      escapeHtml(formatRelative(r.ts)),
    )).join(''),
    tribe: (items) => items.map(m => linkCard(
      `#/tribe?focus=${encodeURIComponent(m.slug || '')}`,
      escapeHtml(m.name),
      `${escapeHtml(m.role)}${m.last_touch ? ' &middot; last touch ' + escapeHtml(m.last_touch) : ''}`,
    )).join(''),
    tasks: (items) => items.map(t => linkCard(
      '#/tasks',
      `${escapeHtml(t.priority)} &middot; ${escapeHtml(t.description)}`,
      `${t.due ? 'due ' + escapeHtml(t.due) : 'no due'}${t.is_overdue ? ' (overdue)' : ''}`,
    )).join(''),
    library: (items) => items.map(n => linkCard(
      `#/library?focus=${encodeURIComponent(n.path || '')}`,
      `${n.type ? escapeHtml(n.type) + ' &middot; ' : ''}${escapeHtml(n.title)}`,
      `${escapeHtml(n.path)}${n.updated ? ' &middot; updated ' + escapeHtml(n.updated) : ''}`,
    )).join(''),
    studio: (items) => items.map(it => linkCard(
      '#/studio',
      `${escapeHtml(it.category)} &middot; ${escapeHtml(it.name)}`,
      escapeHtml(it.path),
    )).join(''),
    day: (items) => items.map(e => linkCard(
      '#/day',
      `${e.is_next ? '<strong>' : ''}${escapeHtml(e.time)} ${escapeHtml(e.subject)}${e.is_next ? '</strong>' : ''}`,
      e.location ? escapeHtml(e.location) : '',
    )).join(''),
    capabilities: (items) => items.map(s => linkCard(
      '#/capabilities',
      `/${escapeHtml(s.name)}${s.version ? ' v' + escapeHtml(s.version) : ''}`,
      escapeHtml(s.description),
    )).join(''),
    pipeline: (items) => items.map(d => linkCard(
      `#/pipeline?focus=${encodeURIComponent(d.company || '')}`,
      `${escapeHtml(d.stage)} &middot; ${escapeHtml(d.company)}`,
      `${escapeHtml(d.country || '')}${d.owner ? ' &middot; ' + escapeHtml(d.owner) : ''}${d.value_display ? ' &middot; ' + escapeHtml(d.value_display) : ''}${d.is_overdue ? ' &middot; <span class="hdg-overdue">overdue</span>' : ''}`,
    )).join(''),
    investors: (items) => items.map(f => {
      const sentTag = f.sent_date ? ` &middot; <span class="hdg-sent">sent ${escapeHtml(f.sent_date)}</span>` : '';
      const statusTag = f.status_label ? ` &middot; ${escapeHtml(f.status_label)}` : '';
      return linkCard(
        `#/investors?focus=${encodeURIComponent(String(f.num || ''))}`,
        `${escapeHtml(f.firm)}${statusTag}${sentTag}`,
        `${escapeHtml(f.region)} &middot; ${escapeHtml(f.hq)} &middot; ${escapeHtml(f.cheque)}${f.fit ? ' &middot; fit ' + escapeHtml(f.fit) : ''}`,
      );
    }).join(''),
  };
  const order = ['tasks', 'inbox', 'day', 'pipeline', 'investors', 'tribe', 'library', 'studio', 'capabilities'];
  const sections = order
    .filter(cat => d.categories[cat] && d.categories[cat].length > 0)
    .map(cat => `
      <div class="search-category-heading">${escapeHtml(cat)} &middot; ${escapeHtml(d.categories[cat].length)}</div>
      ${categoryRenderers[cat](d.categories[cat])}
    `).join('');
  document.getElementById('canvas').innerHTML = `
    <header class="pulse-hero">
      <div class="pulse-breadcrumb">${_breadcrumb('01', 'Today · Search', `"${q}" · ${d.total} match${d.total === 1 ? '' : 'es'}`)}</div>
      <h1 class="pulse-greeting">${escapeHtml(d.total)} match${d.total === 1 ? '' : 'es'}.</h1>
      <p class="pulse-subhead">Results for <strong>&quot;${escapeHtml(q)}&quot;</strong> across ${escapeHtml(Object.keys(d.categories).length)} categor${Object.keys(d.categories).length === 1 ? 'y' : 'ies'}.</p>
    </header>
    ${sections}`;
  await trackPageView('search');
}

// ============================================================
// /signals page removed 2026-06-22 with the Critical Signals section
// (CEO: all-red POC-drift noise). #/signals now falls through to the
// Pulse default via ROUTES. The /signals HTTP endpoint remains a
// dormant read-only API; signals() still feeds the Suggested panel.
// ============================================================

// ============================================================
// Phase 1.88: /conversations page - historical thread browser
// Flat list of email conversations from the latest fetch, sorted by
// recency. Drill-down reuses the existing /inbox/conversation endpoint.
// ============================================================
async function renderConversations(params) {
  const r = await authFetch('/conversations');
  if (!r.ok) {
    document.getElementById('canvas').innerHTML = '<div class="card">Failed to load Conversations.</div>';
    return;
  }
  const d = await r.json();
  const countEl = document.getElementById('conversations-count');
  if (countEl) countEl.textContent = d.total;

  if (d.total === 0) {
    document.getElementById('canvas').innerHTML = `
      <header class="pulse-hero">
        <div class="pulse-breadcrumb">${_breadcrumb('01', 'Today · Conversations', 'None', d.data_time)}</div>
        <h1 class="pulse-greeting">No conversations on file.</h1>
        <p class="pulse-subhead">Run <code>/email-intel</code> to fetch the current Exchange window. Conversations show every email thread the bridge has seen, sorted by recency.</p>
      </header>`;
    renderSyncIndicator(d);
    await trackPageView('conversations');
    return;
  }

  // Priority chips strip for at-a-glance counts.
  const priorityOrder = ['urgent', 'high', 'medium', 'low'];
  const chips = priorityOrder
    .filter(p => (d.counts.by_priority || {})[p])
    .map(p => `<span class="cat-chip conv-priority-${escapeHtml(p)}">${escapeHtml(p)} ${escapeHtml(d.counts.by_priority[p])}</span>`)
    .join('');

  const fmtTime = (iso) => iso ? formatRelative(iso) : '';

  const rows = (d.conversations || []).map(c => {
    const participants = c.participants && c.participants.length
      ? escapeHtml(c.participants.join(', ')) + (c.participants_extra > 0 ? ` <span class="conv-row-extra">+${escapeHtml(c.participants_extra)}</span>` : '')
      : '<span class="conv-row-muted">no participants on file</span>';
    const contactBits = [];
    if (c.contact_name) contactBits.push(escapeHtml(c.contact_name));
    if (c.contact_company) contactBits.push(escapeHtml(c.contact_company));
    const contactLine = contactBits.length ? `<span class="conv-row-contact">${contactBits.join(' &middot; ')}</span>` : '';
    const priorityPill = c.priority
      ? `<span class="conv-row-priority conv-priority-${escapeHtml(c.priority)}">${escapeHtml(c.priority)}</span>`
      : '';
    const categoryPill = c.category
      ? `<span class="conv-row-category">${escapeHtml(c.category)}</span>`
      : '';
    return `
      <div class="card conv-row"
           data-id="${escapeHtml(c.id)}"
           data-topic="${escapeHtml(c.topic)}"
           data-direction="${escapeHtml(c.direction)}"
           data-freshness="${escapeHtml(d.data_time)}" data-stale="${freshnessLevel(d.data_time)}">
        <div class="conv-row-tags">
          ${priorityPill}
          ${categoryPill}
        </div>
        <div class="conv-row-body">
          <div class="conv-row-topic">${escapeHtml(c.topic)}</div>
          <div class="conv-row-meta">
            <span class="conv-row-participants">${participants}</span>
            ${contactLine}
          </div>
          ${c.summary ? `<div class="conv-row-summary">${escapeHtml(c.summary)}</div>` : ''}
        </div>
        <div class="conv-row-side">
          <span class="conv-row-count">${escapeHtml(c.message_count)} msg${c.message_count === 1 ? '' : 's'}</span>
          <span class="conv-row-time">${escapeHtml(fmtTime(c.latest_datetime))}</span>
        </div>
      </div>`;
  }).join('');

  document.getElementById('canvas').innerHTML = `
    <header class="pulse-hero">
      <div class="pulse-breadcrumb">${_breadcrumb('01', 'Today · Conversations', `${d.total} on file`, d.data_time)}</div>
      <h1 class="pulse-greeting">${escapeHtml(d.total)} conversation${d.total === 1 ? '' : 's'}.</h1>
      <p class="pulse-subhead">Every email thread the last <code>/email-intel</code> fetch surfaced, sorted by latest activity. Click any row to read the thread.</p>
    </header>
    <div class="cat-chips">${chips}</div>
    <div class="list-card">${rows}</div>`;

  document.querySelectorAll('.conv-row').forEach(el => {
    el.addEventListener('click', () => _convToggleExpand(el));
  });
  // Phase 1.88 + 1.79: deep-link from search/elsewhere to a specific conv.
  const convFocus = params && params.get ? params.get('focus') : null;
  if (convFocus) {
    const row = document.querySelector(`.conv-row[data-id="${cssEscapeAttr(convFocus)}"]`);
    if (row) _focusRow(row, () => _convToggleExpand(row));
  }
  renderSyncIndicator(d);
  await trackPageView('conversations');
}

async function _convToggleExpand(rowEl) {
  // Reuse the /inbox/conversation endpoint - same data, same drill-down.
  const existing = document.querySelector('.conv-row-expanded, .inbox-row-expanded');
  if (existing) {
    const isThisRow = existing.previousElementSibling === rowEl;
    existing.remove();
    if (isThisRow) return;
  }
  const id = rowEl.dataset.id || '';
  if (!id) return;
  const panel = document.createElement('div');
  panel.className = 'card conv-row-expanded';
  panel.innerHTML = '<div class="inbox-row-loading">Loading...</div>';
  rowEl.insertAdjacentElement('afterend', panel);
  try {
    const r = await authFetch(`/inbox/conversation?id=${encodeURIComponent(id)}`);
    if (!r.ok) {
      panel.innerHTML = `<div class="inbox-row-loading">Failed to load: HTTP ${escapeHtml(r.status)}</div>`;
      return;
    }
    const d = await r.json();
    panel.innerHTML = _conversationPanelHtml(d, rowEl);
    const dBtn = panel.querySelector('.inbox-dismiss-btn');
    if (dBtn) dBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      _inboxDismiss(dBtn);
    });
    // Phase 1.136: wire Flag-Important on the conversations drill-down.
    // _conversationPanelHtml already emits the .inbox-flag-btn (added in
    // 1.134); we just attach the toggle handler here. source_page is
    // #/conversations so /critical's 'Open' link routes back here.
    const flagBtn = panel.querySelector('.inbox-flag-btn');
    if (flagBtn) _wireFlagImportant(flagBtn, 'conversation', '#/conversations');
  } catch (e) {
    panel.innerHTML = `<div class="inbox-row-loading">Failed to load.</div>`;
  }
}

// ============================================================
// R1 (2026-06-03): /action-queue page
// Proactive drafted actions (Cold-Sweep + future autonomy) for one-click
// go/no-go. Approve marks the card ready - an executor sends off the request
// path (the daemon never sends from this click). Edit rewrites an email draft;
// Dismiss tombstones with a 14-day re-propose cooldown. Reuses the approvals
// card classes (v8 tokens, no restyle). Equivalent terminal driver:
// scripts/action-queue.py list|show|approve|edit|dismiss.
// ============================================================
async function renderActionQueue(params) {
  const r = await authFetch('/action-queue');
  if (!r.ok) {
    document.getElementById('canvas').innerHTML = '<div class="card">Failed to load Action Queue.</div>';
    return;
  }
  const d = await r.json();
  // Two lanes (tiered-risk.md): 'actionable' = gated sends awaiting a click;
  // 'fyi' = autonomous/notify notes+alerts you only read. Fall back to the
  // pre-banding shape if an older daemon serves no lane fields.
  const actionable = d.actionable || (d.items || []);
  const fyi = d.fyi || [];
  const actTotal = (d.actionable_total != null) ? d.actionable_total : actionable.length;
  const fyiTotal = (d.fyi_total != null) ? d.fyi_total : fyi.length;
  const countEl = document.getElementById('action-queue-count');
  if (countEl) countEl.textContent = actTotal;  // sidebar reflects what's waiting on YOU

  if (actTotal === 0 && fyiTotal === 0) {
    document.getElementById('canvas').innerHTML = `
      <header class="pulse-hero">
        <div class="pulse-breadcrumb">${_breadcrumb('02', 'Work · Action Queue', 'Queue clear', d.data_time)}</div>
        <h1 class="pulse-greeting">Nothing waiting.</h1>
        <p class="pulse-subhead">Cold-Sweep and proactive agents deposit drafted actions here for one-click go/no-go. Drive it from the terminal too: <code>scripts/action-queue.py list</code>.</p>
      </header>`;
    renderSyncIndicator(d);
    await trackPageView('action-queue');
    return;
  }

  const actionRow = it => {
    const isEmail = it.action_type === 'email_send';
    const cites = (it.citations || []).length;
    const statusTail = it.status === 'approved' ? ' · approved'
      : (it.status === 'send_failed' ? ' · send failed' : '');
    const toBit = isEmail ? ' · to ' + escapeHtml(it.to || '-') : '';
    return `
    <div class="card appr-row aq-row" data-id="${escapeHtml(it.id)}"
         data-freshness="${escapeHtml(d.data_time)}" data-stale="${freshnessLevel(d.data_time)}">
      <div class="appr-row-main">
        <div class="appr-row-subject">${escapeHtml(it.priority || 'P3')} · ${escapeHtml(it.title || '(untitled)')}</div>
        <div class="appr-row-meta">${escapeHtml(it.source || '-')}${toBit} · ${escapeHtml(formatRelative(it.created_at))}${statusTail}</div>
        ${it.reasoning ? `<div class="appr-row-meta">${escapeHtml(it.reasoning)}</div>` : ''}
        ${isEmail && it.draft_status === 'needs_draft' ? `<div class="appr-row-meta">Draft pending — run <code>/cold-sweep</code> to write it</div>` : ''}
        ${cites ? `<div class="appr-row-meta">${cites} citation${cites === 1 ? '' : 's'}</div>` : ''}
      </div>
      <div class="appr-row-side">
        <span class="appr-row-meta">approve from the terminal</span>
      </div>
    </div>`;
  };

  // FYI lane: read-only context (notes/alerts). The page is read-only - no
  // mutation buttons; manage from the terminal (scripts/action-queue.py).
  const fyiRow = it => `
    <div class="card appr-row aq-row aq-fyi-row" data-id="${escapeHtml(it.id)}">
      <div class="appr-row-main">
        <div class="appr-row-subject">${escapeHtml(it.priority || 'P3')} · ${escapeHtml(it.title || '(untitled)')}</div>
        <div class="appr-row-meta">${escapeHtml(it.action_type || 'note')} · ${escapeHtml(it.source || '-')} · FYI, read-only · ${escapeHtml(formatRelative(it.created_at))}</div>
        ${it.reasoning ? `<div class="appr-row-meta">${escapeHtml(it.reasoning)}</div>` : ''}
      </div>
    </div>`;

  const actBlock = actTotal > 0
    ? `<div class="list-card">${actionable.map(actionRow).join('')}</div>`
    : `<p class="pulse-subhead">Nothing waiting for your approval right now.</p>`;
  const fyiBlock = fyiTotal > 0
    ? `<h3 class="signals-section-head"><span class="signals-section-title">FYI — read-only context</span><span class="signals-section-count">${escapeHtml(fyiTotal)}</span></h3>
       <div class="list-card">${fyi.map(fyiRow).join('')}</div>`
    : '';
  const heading = actTotal > 0
    ? `${escapeHtml(actTotal)} action${actTotal === 1 ? '' : 's'} waiting.`
    : 'Nothing waiting to send.';

  document.getElementById('canvas').innerHTML = `
    <header class="pulse-hero">
      <div class="pulse-breadcrumb">${_breadcrumb('02', 'Work · Action Queue', `${actTotal} waiting`, d.data_time)}</div>
      <h1 class="pulse-greeting">${heading}</h1>
      <p class="pulse-subhead">Read-only view. Approve and send synchronously from the terminal: <code>scripts/action-queue.py approve &lt;id&gt;</code> (or <code>/queue</code> in chat). The daemon no longer sends.</p>
    </header>
    ${actBlock}
    ${fyiBlock}`;

  renderSyncIndicator(d);
  await trackPageView('action-queue');
}

const ROUTES = {
  pulse: renderPulse,
  inbox: renderInbox,
  approvals: renderApprovals,
  'action-queue': renderActionQueue,
  conversations: renderConversations,
  day: renderDay,
  tasks: renderTasks,
  pipeline: renderPipeline,
  investors: renderInvestors,
  studio: renderStudio,
  threads: renderThreads,
  tribe: renderTribe,
  contacts: renderContacts,
  capabilities: renderCapabilities,
  library: renderLibrary,
  settings: renderSettings,
  search: renderSearch,
  critical: renderCritical,
  stub: renderStub,
};

async function renderCurrentPage() {
  const raw = (location.hash || '#/pulse').replace('#/', '');
  // Split off query string (e.g., "search?q=picasso" -> ["search", "q=picasso"]).
  const [route, queryStr] = raw.split('?');
  // Clear any cross-page interval timers when leaving the page that owns them.
  if (route !== 'pulse' && _nextMeetingTickInterval) {
    clearInterval(_nextMeetingTickInterval);
    _nextMeetingTickInterval = null;
  }
  const params = new URLSearchParams(queryStr || '');
  const fn = ROUTES[route] || renderPulse;
  // Pass params dict to render functions so they can read query state.
  await fn(params);
  document.querySelectorAll('.nav-item').forEach(a => {
    const isActive = a.getAttribute('href') === `#/${route}`;
    a.classList.toggle('active', isActive);
    if (isActive) {
      a.setAttribute('aria-current', 'page');
    } else {
      a.removeAttribute('aria-current');
    }
  });
}

function _refreshThemeBtnIcon() {
  const btn = document.getElementById('theme-toggle');
  if (!btn) return;
  const dark = document.documentElement.getAttribute('data-theme') === 'dark';
  btn.innerHTML = dark ? '☽' : '☀';  // moon vs sun
  btn.title = dark ? 'Switch to light' : 'Switch to dark';
}

// ============================================================
// Tweaks: persistent UI preferences (theme, density, motion, sidebar)
// ============================================================
const TWEAKS_KEY = 'bridge.tweaks';
const TWEAKS_DEFAULTS = { theme: 'light', variant: 'operator', density: 'comfortable', motion: 'on', sidebar: 'expanded' };

function _loadTweaks() {
  let saved = {};
  try {
    const raw = localStorage.getItem(TWEAKS_KEY);
    if (raw) saved = JSON.parse(raw) || {};
  } catch (e) { saved = {}; }
  // One-time migration from the Phase 1.38 'bridge.theme' key.
  const legacyTheme = localStorage.getItem('bridge.theme');
  if (!saved.theme && (legacyTheme === 'light' || legacyTheme === 'dark')) {
    saved.theme = legacyTheme;
  }
  for (const key of Object.keys(TWEAKS_DEFAULTS)) {
    _applyTweak(key, saved[key] || TWEAKS_DEFAULTS[key], { skipSave: true });
  }
  _persistTweaks();  // collapse migrated state into the new key
  _refreshTweaksUI();
}

function _readTweaks() {
  try {
    const raw = localStorage.getItem(TWEAKS_KEY);
    return raw ? (JSON.parse(raw) || {}) : {};
  } catch (e) { return {}; }
}

function _persistTweaks() {
  const all = {
    theme: document.documentElement.getAttribute('data-theme') || 'light',
    variant: document.documentElement.getAttribute('data-variant') || 'operator',
    density: document.documentElement.getAttribute('data-density') || 'comfortable',
    motion: document.documentElement.getAttribute('data-motion') || 'on',
    sidebar: document.body.getAttribute('data-sidebar') || 'expanded',
  };
  try { localStorage.setItem(TWEAKS_KEY, JSON.stringify(all)); } catch (e) {}
}

function _applyTweak(key, value, opts) {
  if (key === 'sidebar') {
    document.body.setAttribute('data-sidebar', value);
  } else {
    document.documentElement.setAttribute('data-' + key, value);
  }
  if (!opts || !opts.skipSave) _persistTweaks();
}

function _refreshTweaksUI() {
  // Mark the active segmented button per current state.
  document.querySelectorAll('.tweak-seg').forEach(seg => {
    const key = seg.dataset.tweak;
    if (!key) return;
    const current = key === 'sidebar'
      ? (document.body.getAttribute('data-sidebar') || 'expanded')
      : (document.documentElement.getAttribute('data-' + key) || TWEAKS_DEFAULTS[key]);
    seg.querySelectorAll('button').forEach(btn => {
      btn.classList.toggle('active', btn.dataset.value === current);
    });
  });
}

function _openTweaks() {
  const panel = document.getElementById('tweaks');
  if (!panel) return;
  panel.hidden = false;
  _refreshTweaksUI();
}

function _closeTweaks() {
  const panel = document.getElementById('tweaks');
  if (!panel) return;
  panel.hidden = true;
}

function _tickTopbarClock() {
  const el = document.getElementById('tb-clock');
  if (!el) return;
  // Phase 1.82: Intl.DateTimeFormat is the only correct way to render
  // local time regardless of where the user's machine is set. The earlier
  // hand-rolled offset double-counted getTimezoneOffset() and showed +4h.
  // Phase 1.122: add the year ('Fri 16 May 2026 · 14:23') so the
  // line matches v8 reference exactly. Timezone is per-instance (state.tz).
  const parts = new Intl.DateTimeFormat('en-GB', {
    timeZone: state.tz,
    weekday: 'short',
    day: 'numeric',
    month: 'short',
    year: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).formatToParts(new Date());
  const get = (t) => {
    const p = parts.find(x => x.type === t);
    return p ? p.value : '';
  };
  // formatToParts hour=24 -> show as 00 (consistent with hour12:false UK locale).
  const hh = get('hour') === '24' ? '00' : get('hour');
  el.innerHTML = `<strong>${get('weekday')} ${get('day')} ${get('month')} ${get('year')} &middot; ${hh}:${get('minute')} ${escapeHtml(state.tzLabel)}</strong>`;
}

async function _refreshSeaState() {
  // Sea-state pill is authed - calls /sea-state via authFetch.
  try {
    const r = await authFetch('/sea-state');
    if (!r.ok) return;
    const d = await r.json();
    const dot = document.querySelector('#sea-pill .sea-dot');
    const lbl = document.getElementById('sea-label');
    const moodEl = document.getElementById('sea-mood');
    if (dot && d.state) dot.setAttribute('data-state', d.state);
    if (lbl && d.label) lbl.textContent = d.label;
    if (moodEl) {
      if (d.mood) moodEl.setAttribute('data-mood', d.mood);
      if (d.mood_label) moodEl.textContent = d.mood_label;
    }
    const pill = document.getElementById('sea-pill');
    if (pill) {
      const stateBit = `${d.overdue_total} overdue (${d.pipeline_overdue} deals, ${d.tasks_overdue} tasks)`;
      const moodBit = `${d.events_today} event${d.events_today === 1 ? '' : 's'} today`;
      pill.title = `Sea state - ${stateBit}\nMood - ${moodBit}`;
    }
  } catch (e) {
    // Silent - pill keeps prior state.
  }
}

async function _refreshTopbarBuild() {
  try {
    const r = await fetch('/health');
    if (!r.ok) return;
    const d = await r.json();
    const buildEl = document.getElementById('tb-build');
    if (buildEl && d.version) buildEl.textContent = `build ${d.version}`;
    const uptimeEl = document.getElementById('tb-uptime');
    if (uptimeEl && typeof d.uptime_s === 'number') {
      const s = d.uptime_s;
      let display;
      if (s < 60) display = `${s}s`;
      else if (s < 3600) display = `${Math.floor(s / 60)}m`;
      else if (s < 86400) display = `${Math.floor(s / 3600)}h ${Math.floor((s % 3600) / 60)}m`;
      else display = `${Math.floor(s / 86400)}d ${Math.floor((s % 86400) / 3600)}h`;
      uptimeEl.textContent = `up ${display}`;
    }
  } catch (e) {
    // Silent - topbar pills stay with prior value.
  }
}

async function init() {
  await bootstrap();
  await renderCurrentPage();
  window.addEventListener('hashchange', renderCurrentPage);
  // v8 sync-pill doubles as the refresh button.
  const refreshBtn = document.getElementById('sync-btn');
  if (refreshBtn) {
    refreshBtn.addEventListener('click', async () => {
      // Phase 1.97: visible feedback - spin during fetch+render, pop
      // green for ~600ms on success. Prevents double-clicks while in flight.
      refreshBtn.classList.add('syncing');
      refreshBtn.classList.remove('just-synced');
      // Strip leading '#/' and trailing query string -> bare route name.
      const route = (location.hash || '#/pulse').replace('#/', '').split('?')[0];
      // Pages that have a server-side refresher component. Settings,
      // search, etc. just re-render client-side.
      const componentMap = {
        pulse: 'pulse',
        inbox: 'inbox',
        day: 'day',
        tasks: 'tasks',
        pipeline: 'pipeline',
        investors: 'investors',
        approvals: 'approvals',
        conversations: 'conversations',
        studio: 'studio',
        capabilities: 'capabilities',
        library: 'library',
        tribe: 'tribe',
        threads: 'threads',
      };
      const component = componentMap[route];
      if (component) {
        try {
          await authFetch('/refresh', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ component }),
          });
        } catch (e) {
          // Silent failure - the page reload below will still happen.
        }
      }
      try {
        await renderCurrentPage();
      } finally {
        refreshBtn.classList.remove('syncing');
        refreshBtn.classList.add('just-synced');
        setTimeout(() => refreshBtn.classList.remove('just-synced'), 600);
      }
    });
  }
  // Search form: submit navigates to #/search?q=...
  const searchForm = document.getElementById('search-form');
  const searchInput = document.getElementById('search-input');
  if (searchForm && searchInput) {
    searchForm.addEventListener('submit', (e) => {
      e.preventDefault();
      const q = searchInput.value.trim();
      if (q) {
        location.hash = `#/search?q=${encodeURIComponent(q)}`;
      }
    });
  }
  // v8 port: stale state is rolled into the sync-pill itself which already
  // has a click handler above. Stale clicks fall through to the same handler.
  // Sidebar collapse/expand toggle (persists via Tweaks).
  const sidebarToggle = document.getElementById('sidebar-toggle');
  if (sidebarToggle) {
    sidebarToggle.addEventListener('click', () => {
      const current = document.body.getAttribute('data-sidebar');
      _applyTweak('sidebar', current === 'collapsed' ? 'expanded' : 'collapsed');
      _refreshTweaksUI();
    });
  }
  // Collapsible People group (Tribe/Contacts/Investors). State persists per
  // browser; the group auto-expands when one of its pages is the active route.
  const peopleSection = document.getElementById('people-section');
  const peopleToggle = document.getElementById('people-toggle');
  if (peopleSection && peopleToggle) {
    const PEOPLE_ROUTES = ['tribe', 'contacts', 'investors'];
    const setPeople = (collapsed, persist) => {
      peopleSection.setAttribute('data-collapsed', collapsed ? 'true' : 'false');
      peopleToggle.setAttribute('aria-expanded', collapsed ? 'false' : 'true');
      if (persist) {
        try { localStorage.setItem('heading.people-collapsed', collapsed ? '1' : '0'); } catch (e) {}
      }
    };
    const stored = (() => { try { return localStorage.getItem('heading.people-collapsed'); } catch (e) { return null; } })();
    const activeRoute = (location.hash || '').replace('#/', '').split('?')[0];
    // Default collapsed; open if stored open, or if a People page is active.
    setPeople(!(stored === '0' || PEOPLE_ROUTES.includes(activeRoute)), false);
    peopleToggle.addEventListener('click', () => {
      setPeople(peopleSection.getAttribute('data-collapsed') !== 'true', true);
    });
    // Keep the group open whenever navigation lands on a People page.
    window.addEventListener('hashchange', () => {
      const r = (location.hash || '').replace('#/', '').split('?')[0];
      if (PEOPLE_ROUTES.includes(r)) setPeople(false, false);
    });
  }
  // Theme toggle + Tweaks panel: shared persistence via _applyTweak below.
  _loadTweaks();
  const themeBtn = document.getElementById('theme-toggle');
  _refreshThemeBtnIcon();
  if (themeBtn) {
    themeBtn.addEventListener('click', () => {
      const current = document.documentElement.getAttribute('data-theme') || 'light';
      _applyTweak('theme', current === 'dark' ? 'light' : 'dark');
      _refreshThemeBtnIcon();
    });
  }
  const tweaksToggle = document.getElementById('tweaks-toggle');
  const tweaksClose = document.getElementById('tweaks-close');
  const tweaksPanel = document.getElementById('tweaks');
  if (tweaksToggle && tweaksPanel) {
    tweaksToggle.addEventListener('click', () => {
      if (tweaksPanel.hidden) _openTweaks();
      else _closeTweaks();
    });
  }
  if (tweaksClose) tweaksClose.addEventListener('click', _closeTweaks);
  // Click-outside-panel closes it.
  document.addEventListener('click', (e) => {
    if (!tweaksPanel || tweaksPanel.hidden) return;
    if (tweaksPanel.contains(e.target)) return;
    if (tweaksToggle && tweaksToggle.contains(e.target)) return;
    _closeTweaks();
  });
  // Wire segmented buttons inside the panel.
  document.querySelectorAll('.tweak-seg').forEach(seg => {
    const key = seg.dataset.tweak;
    if (!key) return;
    seg.querySelectorAll('button').forEach(btn => {
      btn.addEventListener('click', () => {
        _applyTweak(key, btn.dataset.value);
        _refreshTweaksUI();
        if (key === 'theme') _refreshThemeBtnIcon();
      });
    });
  });
  // Phase 1.36: the bottom cmd-bar was removed. The Ctrl+K palette is
  // still available via the keyboard shortcut (see the keydown handler).
  // Topbar live clock (local TZ) + build/uptime pills (refreshed every 30s
  // via /health which is unauthed).
  _tickTopbarClock();
  setInterval(_tickTopbarClock, 30_000);
  _refreshTopbarBuild();
  setInterval(_refreshTopbarBuild, 30_000);
  _refreshSeaState();
  // Sea-state changes slowly; 5-minute poll is enough and uses tiny
  // bandwidth (small endpoint, no caching needed).
  setInterval(_refreshSeaState, 5 * 60_000);
  const statusSec = Math.max(2, Number(state.refresh.status) || 30);
  state.pollInterval = setInterval(checkVersion, statusSec * 1000);
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') {
      state.missedHeartbeats = 0;
      checkVersion();
      renderCurrentPage();
    }
  });
}

// Keyboard shortcuts. Vim-style: `/` focuses search, `g` + letter navigates,
// `?` toggles help overlay. Esc closes overlay or blurs search.
//
// Suppress when the user is typing in an input/textarea (so `/` and letters
// inside the search box don't trigger nav).
const KBD_NAV = {
  p: '#/pulse',
  i: '#/inbox',
  a: '#/approvals',
  o: '#/conversations',  // c is capabilities; 'o' for cOnversations
  d: '#/day',
  t: '#/tasks',
  n: '#/pipeline',       // 'p' is taken by pulse - 'n' for piNeline
  v: '#/investors',      // 'i' is taken by inbox
  s: '#/studio',
  h: '#/threads',        // 't' is taken by tasks - 'h' for tHreads
  r: '#/tribe',
  c: '#/capabilities',
  l: '#/library',
  m: '#/critical',       // Phase 1.140: 'm' for iMportant - 'i' taken by inbox
  e: '#/contacts',       // Phase 1.35: 'e' for pEople/contacts - c/o taken
};

let _kbdGPrefix = false;
let _kbdGPrefixTimer = null;

function _isTypingTarget(target) {
  if (!target) return false;
  const tag = target.tagName;
  return tag === 'INPUT' || tag === 'TEXTAREA' || target.isContentEditable;
}

function _toggleKbdHelp(force) {
  const el = document.getElementById('kbd-help');
  if (!el) return;
  if (force === true) {
    el.hidden = false;
  } else if (force === false) {
    el.hidden = true;
  } else {
    el.hidden = !el.hidden;
  }
}

function _resetGPrefix() {
  _kbdGPrefix = false;
  if (_kbdGPrefixTimer) {
    clearTimeout(_kbdGPrefixTimer);
    _kbdGPrefixTimer = null;
  }
}

document.addEventListener('keydown', (e) => {
  const target = e.target;
  const helpEl = document.getElementById('kbd-help');
  const helpOpen = helpEl && !helpEl.hidden;

  // Esc handling - works even when typing.
  if (e.key === 'Escape') {
    // Close tweaks panel first if open.
    const tweaksPanel = document.getElementById('tweaks');
    if (tweaksPanel && !tweaksPanel.hidden) {
      _closeTweaks();
      e.preventDefault();
      return;
    }
    // Collapse any open drill-down expansion (library, tribe, studio,
    // capabilities, pipeline, investors, day, inbox, approvals on Pulse,
    // approvals page).
    const expanded = document.querySelector('.lib-row-expanded, .tribe-row-expanded, .studio-row-expanded, .cap-row-expanded, .pipe-row-expanded, .inv-row-expanded, .day-row-expanded, .inbox-row-expanded, .pulse-approval-expanded, .appr-row-expanded, .thread-row-expanded, .conv-row-expanded');
    if (expanded) {
      expanded.remove();
      e.preventDefault();
      return;
    }
    if (helpOpen) {
      _toggleKbdHelp(false);
      e.preventDefault();
      return;
    }
    const searchInput = document.getElementById('search-input');
    if (searchInput && document.activeElement === searchInput) {
      searchInput.value = '';
      searchInput.blur();
      e.preventDefault();
      return;
    }
    return;
  }

  // Ignore other keys when typing in inputs.
  if (_isTypingTarget(target)) return;

  // Don't handle when modifier keys are pressed (browser shortcuts).
  if (e.metaKey || e.ctrlKey || e.altKey) return;

  // `/` focuses search.
  if (e.key === '/') {
    const searchInput = document.getElementById('search-input');
    if (searchInput) {
      searchInput.focus();
      searchInput.select();
      e.preventDefault();
    }
    return;
  }

  // `?` toggles help.
  if (e.key === '?') {
    _toggleKbdHelp();
    e.preventDefault();
    return;
  }

  // `g` prefix.
  if (e.key === 'g' && !_kbdGPrefix) {
    _kbdGPrefix = true;
    _kbdGPrefixTimer = setTimeout(_resetGPrefix, 1500);
    e.preventDefault();
    return;
  }

  // `g` followed by nav letter.
  if (_kbdGPrefix) {
    const target_hash = KBD_NAV[e.key.toLowerCase()];
    _resetGPrefix();
    if (target_hash) {
      location.hash = target_hash;
      e.preventDefault();
    }
    return;
  }
});

// Close help when clicking outside the panel.
document.addEventListener('click', (e) => {
  const helpEl = document.getElementById('kbd-help');
  if (!helpEl || helpEl.hidden) return;
  const panel = helpEl.querySelector('.kbd-help-panel');
  if (panel && !panel.contains(e.target)) {
    _toggleKbdHelp(false);
  }
});

// ============================================================================
// CmdK palette - Cmd/Ctrl+K opens a modal action launcher.
// Default items: 9 page nav actions. Typing triggers live /search via the
// existing endpoint, debounced 150ms.
// ============================================================================

const CMDK_DEFAULT_ITEMS = [
  { icon: 'page', label: 'Pulse',        hash: '#/pulse' },
  { icon: 'page', label: 'Important',    hash: '#/critical' },
  { icon: 'page', label: 'Signals',      hash: '#/signals' },
  { icon: 'page', label: 'Inbox',        hash: '#/inbox' },
  { icon: 'page', label: 'Approvals',    hash: '#/approvals' },
  { icon: 'page', label: 'Conversations', hash: '#/conversations' },
  { icon: 'page', label: 'Day',          hash: '#/day' },
  { icon: 'page', label: 'Tasks',        hash: '#/tasks' },
  { icon: 'page', label: 'Pipeline',     hash: '#/pipeline' },
  { icon: 'page', label: 'Investors',    hash: '#/investors' },
  { icon: 'page', label: 'Studio',       hash: '#/studio' },
  { icon: 'page', label: 'Threads',      hash: '#/threads' },
  { icon: 'page', label: 'Tribe',        hash: '#/tribe' },
  { icon: 'page', label: 'Contacts',     hash: '#/contacts' },
  { icon: 'page', label: 'Capabilities', hash: '#/capabilities' },
  { icon: 'page', label: 'Library',      hash: '#/library' },
  { icon: 'page', label: 'Settings',     hash: '#/settings' },
];

let _cmdkActiveIndex = 0;
let _cmdkCurrentItems = [];
let _cmdkSearchTimer = null;
let _cmdkLastSearchQuery = '';

function _cmdkRender(items) {
  _cmdkCurrentItems = items;
  if (_cmdkActiveIndex >= items.length) _cmdkActiveIndex = 0;
  const resultsEl = document.getElementById('cmdk-results');
  if (!resultsEl) return;
  if (items.length === 0) {
    resultsEl.innerHTML = '<div class="cmdk-empty">No matches.</div>';
    return;
  }
  resultsEl.innerHTML = items.map((it, i) => `
    <div class="cmdk-result ${i === _cmdkActiveIndex ? 'cmdk-active' : ''}" data-index="${i}">
      <span class="cmdk-result-icon">${escapeHtml(it.icon)}</span>
      <span class="cmdk-result-label">${escapeHtml(it.label)}</span>
      ${it.meta ? `<span class="cmdk-result-meta">${escapeHtml(it.meta)}</span>` : ''}
    </div>
  `).join('');
  resultsEl.querySelectorAll('.cmdk-result').forEach(el => {
    el.addEventListener('click', () => {
      _cmdkActiveIndex = parseInt(el.dataset.index, 10);
      _cmdkActivate();
    });
    el.addEventListener('mouseenter', () => {
      _cmdkActiveIndex = parseInt(el.dataset.index, 10);
      _cmdkRender(_cmdkCurrentItems);  // re-render to update highlight
    });
  });
  // Scroll active item into view.
  const active = resultsEl.querySelector('.cmdk-active');
  if (active) active.scrollIntoView({ block: 'nearest' });
}

function _cmdkFilterDefault(query) {
  const q = query.toLowerCase();
  return CMDK_DEFAULT_ITEMS.filter(it => it.label.toLowerCase().includes(q));
}

async function _cmdkRunSearch(query) {
  _cmdkLastSearchQuery = query;
  // Start with default items filtered (instant feedback).
  const matchedDefaults = _cmdkFilterDefault(query);
  // Render immediately with filtered defaults.
  _cmdkRender(matchedDefaults);
  // Then fetch /search and append results.
  try {
    const r = await authFetch(`/search?q=${encodeURIComponent(query)}&limit=5`);
    if (!r.ok) return;
    const d = await r.json();
    // If the user typed something else in the meantime, abandon this response.
    if (_cmdkLastSearchQuery !== query) return;
    const searchItems = [];
    const categoryLabels = {
      inbox: 'inbox', tribe: 'tribe', tasks: 'task', library: 'note',
      studio: 'file', day: 'meeting', capabilities: 'skill',
      pipeline: 'deal', investors: 'investor',
    };
    const order = ['tasks', 'inbox', 'day', 'pipeline', 'investors', 'tribe', 'library', 'studio', 'capabilities'];
    for (const cat of order) {
      const items = d.categories[cat];
      if (!items) continue;
      for (const item of items) {
        let label = '';
        let meta = '';
        let hash = `#/${cat}`;
        if (cat === 'inbox') { label = item.subject || ''; meta = item.unread ? 'unread' : ''; }
        else if (cat === 'tribe') { label = item.name; meta = item.role; }
        else if (cat === 'tasks') { label = item.description; meta = item.priority + (item.due ? ` due ${item.due}` : ''); }
        else if (cat === 'library') { label = item.title; meta = item.type; }
        else if (cat === 'studio') { label = item.name; meta = item.category; }
        else if (cat === 'day') { label = `${item.time} ${item.subject}`; meta = item.is_next ? 'next' : ''; }
        else if (cat === 'capabilities') { label = `/${item.name}`; meta = item.version ? `v${item.version}` : ''; }
        else if (cat === 'pipeline') { label = `${item.stage} · ${item.company}`; meta = item.value_display + (item.is_overdue ? ' · overdue' : ''); }
        else if (cat === 'investors') { label = item.firm; meta = `${item.status_label || ''}${item.sent_date ? ' · sent ' + item.sent_date : ''}`; }
        searchItems.push({ icon: categoryLabels[cat], label, meta, hash });
      }
    }
    // Combine: filtered defaults first, then search results.
    _cmdkRender([...matchedDefaults, ...searchItems]);
  } catch (e) {
    // Silent - already rendered the defaults.
  }
}

function _cmdkOpen() {
  const el = document.getElementById('cmdk-palette');
  if (!el) return;
  el.hidden = false;
  _cmdkActiveIndex = 0;
  _cmdkRender(CMDK_DEFAULT_ITEMS);
  const input = document.getElementById('cmdk-input');
  if (input) {
    input.value = '';
    setTimeout(() => input.focus(), 0);
  }
}

function _cmdkClose() {
  const el = document.getElementById('cmdk-palette');
  if (el) el.hidden = true;
  if (_cmdkSearchTimer) {
    clearTimeout(_cmdkSearchTimer);
    _cmdkSearchTimer = null;
  }
}

function _cmdkActivate() {
  const item = _cmdkCurrentItems[_cmdkActiveIndex];
  if (!item) return;
  _cmdkClose();
  if (item.hash) {
    location.hash = item.hash;
  }
}

// Cmd/Ctrl+K opens the palette. Esc closes.
document.addEventListener('keydown', (e) => {
  const isCmdK = (e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k';
  if (isCmdK) {
    e.preventDefault();
    const el = document.getElementById('cmdk-palette');
    if (el && !el.hidden) {
      _cmdkClose();
    } else {
      _cmdkOpen();
    }
    return;
  }
  // Esc handled separately to coexist with kbd-help overlay - check palette first.
  if (e.key === 'Escape') {
    const el = document.getElementById('cmdk-palette');
    if (el && !el.hidden) {
      _cmdkClose();
      e.preventDefault();
      return;
    }
    // Fall through to other Esc handlers (kbd-help, search blur).
  }
});

// Palette-specific keyboard handling (when palette is open + focused).
document.addEventListener('keydown', (e) => {
  const el = document.getElementById('cmdk-palette');
  if (!el || el.hidden) return;
  if (e.key === 'ArrowDown') {
    _cmdkActiveIndex = Math.min(_cmdkActiveIndex + 1, _cmdkCurrentItems.length - 1);
    _cmdkRender(_cmdkCurrentItems);
    e.preventDefault();
  } else if (e.key === 'ArrowUp') {
    _cmdkActiveIndex = Math.max(_cmdkActiveIndex - 1, 0);
    _cmdkRender(_cmdkCurrentItems);
    e.preventDefault();
  } else if (e.key === 'Enter') {
    _cmdkActivate();
    e.preventDefault();
  }
});

// Click-outside to close the Ctrl+K palette.
document.addEventListener('click', (e) => {
  const el = document.getElementById('cmdk-palette');
  if (!el || el.hidden) return;
  const panel = el.querySelector('.cmdk-panel');
  if (panel && !panel.contains(e.target)) {
    _cmdkClose();
  }
});

// Wire the input to trigger filtered renders.
document.addEventListener('DOMContentLoaded', () => {
  const input = document.getElementById('cmdk-input');
  if (!input) return;
  input.addEventListener('input', () => {
    const q = input.value.trim();
    if (_cmdkSearchTimer) clearTimeout(_cmdkSearchTimer);
    if (!q) {
      _cmdkRender(CMDK_DEFAULT_ITEMS);
      return;
    }
    // 150ms debounce before hitting /search.
    _cmdkSearchTimer = setTimeout(() => _cmdkRunSearch(q), 150);
  });
});

init().catch(e => console.error('bridge init failed:', e));
