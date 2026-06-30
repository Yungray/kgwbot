// 처리현황 대시보드
(function () {
  "use strict";

  const $ = (id) => document.getElementById(id);
  const root = document.querySelector(".main");
  const groupSel = $("lnb-module");          // 모듈 = LNB 전역 스위처 (페이지 공유)
  const moduleIcon = $("lnb-module-icon");
  const resetBtn = $("reset-btn");
  const MODULE_ICONS = window.__MODULE_ICONS__ || {};
  const targetSel = $("dash-target");
  const startEl = $("dash-start");
  const endEl = $("dash-end");
  const excludeBotEl = $("dash-exclude-bot");
  const compareEl = $("dash-compare");
  const runBtn = $("dash-run");
  const refreshBtn = $("dash-refresh");
  const csvBtn = $("dash-csv");
  const searchEl = $("dash-search");
  const aiEl = $("dash-ai");
  const aiStatusEl = $("dash-ai-status");
  const errEl = $("dash-error");
  const loadingEl = $("dash-loading");
  const emptyEl = $("dash-empty");
  const resultsEl = $("dash-results");
  const statusEl = $("dash-status");

  const GROUP_OPTIONS = JSON.parse($("dash-group-options").textContent || "{}");
  const DEFAULT_GROUP = root.dataset.defaultGroup || "";
  const DEFAULT_TARGET = root.dataset.defaultTarget || "";

  const STATUS_COLORS = { "요청": "#6B7280", "진행": "#D97706", "완료": "#16A34A", "승인": "#2563EB" };
  const CARD_ORDER = ["전체 작성 글", "요청", "진행", "완료", "승인", "요청 아님/기타"];
  const CARD_META = {
    "전체 작성 글": { color: "#6366F1", icon: "📄", deltaKey: "전체" },
    "요청": { color: "#6B7280", icon: "📥", deltaKey: "요청" },
    "진행": { color: "#D97706", icon: "⏳", deltaKey: "진행" },
    "완료": { color: "#16A34A", icon: "✅", deltaKey: "완료" },
    "승인": { color: "#2563EB", icon: "🔖", deltaKey: "승인" },
    "요청 아님/기타": { color: "#9CA3AF", icon: "•", deltaKey: null },
  };
  const STATUS_BADGE = {
    "요청": "badge-default", "진행": "badge-warning", "완료": "badge-success",
    "승인": "badge-blue", "비task": "badge-muted",
  };

  let donutChart = null, barChart = null, trendChart = null, templateChart = null;
  let allRows = [];
  let activeFilter = "전체";
  let searchTerm = "";
  let sortKey = "created_at", sortDir = "desc";
  let currentController = null;   // 진행 중 fetch 취소용
  let lastMeta = null;            // CSV 파일명용
  const summaries = {};           // message_id → AI 요약 (클라 캐시, 렌더 간 유지)
  let summaryRunId = 0;           // 조회 바뀌면 진행 중 요약 작업 무효화

  // ── 날짜 유틸 (로컬 기준, UTC 시프트 방지) ─────────────────
  function fmt(d) {
    return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
  }
  function dateRange(start, end) {
    const [ys, ms, ds] = start.split("-").map(Number);
    const [ye, me, de] = end.split("-").map(Number);
    let d = new Date(ys, ms - 1, ds);
    const e = new Date(ye, me - 1, de);
    const out = [];
    while (d <= e && out.length < 400) { out.push(fmt(d)); d.setDate(d.getDate() + 1); }
    return out;
  }
  function setPreset(p) {
    const today = new Date();
    let s, e = today;
    if (p === "7" || p === "30") { s = new Date(); s.setDate(today.getDate() - (parseInt(p, 10) - 1)); }
    else if (p === "this-month") { s = new Date(today.getFullYear(), today.getMonth(), 1); }
    else if (p === "last-month") {
      s = new Date(today.getFullYear(), today.getMonth() - 1, 1);
      e = new Date(today.getFullYear(), today.getMonth(), 0);
    }
    startEl.value = fmt(s);
    endEl.value = fmt(e);
  }

  // ── 셀렉터 초기화 ──────────────────────────────────────────
  function populateTargets(group, preferredId) {
    const opts = GROUP_OPTIONS[group] || [];
    targetSel.innerHTML =
      `<option value="">모듈 전체</option>` +
      opts.map((o) => `<option value="${o.id}">${o.title}</option>`).join("");
    if (preferredId && opts.some((o) => o.id === preferredId)) targetSel.value = preferredId;
  }
  function syncModuleIcon() {
    if (moduleIcon) moduleIcon.textContent = MODULE_ICONS[groupSel.value] || "📁";
  }
  (function initDefaults() {
    // 모듈은 챗봇과 공유하는 전역 컨텍스트(localStorage agit_group)를 우선 반영
    const stored = localStorage.getItem("agit_group") || "";
    if (stored && GROUP_OPTIONS[stored]) groupSel.value = stored;
    else if (DEFAULT_GROUP && GROUP_OPTIONS[DEFAULT_GROUP]) groupSel.value = DEFAULT_GROUP;
    syncModuleIcon();
    populateTargets(groupSel.value, DEFAULT_TARGET);
    setPreset("30");
  })();
  // 모듈 전환: 컨텍스트 저장 → 아이콘·하위 아지트 갱신 → 즉시 재조회
  groupSel.addEventListener("change", () => {
    localStorage.setItem("agit_group", groupSel.value);
    syncModuleIcon();
    populateTargets(groupSel.value, "");
    run();
  });
  // '새 대화'는 대시보드에서 챗봇으로 이동
  if (resetBtn) resetBtn.addEventListener("click", () => { window.location.href = "/"; });

  // ── 상태 배지 ──────────────────────────────────────────────
  function setStatus(text, variant) {
    statusEl.textContent = text;
    statusEl.className = "badge badge-" + (variant || "indigo");
  }
  function showError(msg) { errEl.textContent = "⚠ " + msg; errEl.hidden = false; }

  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"]/g, (c) =>
      ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
  }

  // ── Stat 카드 (전 기간 대비 델타 포함) ─────────────────────
  function renderCards(rows, comparison) {
    const byLabel = {};
    rows.forEach((r) => (byLabel[r.label] = r.count));
    const deltaOf = (key) => {
      if (!comparison || !key) return null;
      if (key === "전체") return comparison.total_delta;
      return (comparison.status_delta || {})[key];
    };
    $("dash-cards").innerHTML = CARD_ORDER.filter((l) => l in byLabel)
      .map((label) => {
        const m = CARD_META[label] || { color: "#6366F1", icon: "•", deltaKey: null };
        const d = deltaOf(m.deltaKey);
        let delta = "";
        if (d != null) {
          const cls = d > 0 ? "up" : d < 0 ? "down" : "flat";
          const arrow = d > 0 ? "▲" : d < 0 ? "▼" : "–";
          delta = `<div class="stat-delta ${cls}">${arrow} ${Math.abs(d).toLocaleString()} <span>vs 전기간</span></div>`;
        }
        return `
        <div class="stat-card">
          <span class="stat-chip" style="background:${m.color}1a;color:${m.color}">${m.icon}</span>
          <div class="stat-label">${label}</div>
          <div class="stat-value">${byLabel[label].toLocaleString()}</div>
          ${delta}
        </div>`;
      })
      .join("");
  }

  // ── 차트 ───────────────────────────────────────────────────
  function renderStatusCharts(statusCounts) {
    const labels = Object.keys(statusCounts);
    const values = labels.map((l) => statusCounts[l].count);
    const colors = labels.map((l) => STATUS_COLORS[l] || "#9CA3AF");
    if (donutChart) donutChart.destroy();
    if (barChart) barChart.destroy();
    donutChart = new Chart($("dash-donut"), {
      type: "doughnut",
      data: { labels, datasets: [{ data: values, backgroundColor: colors, borderWidth: 0 }] },
      options: { cutout: "62%", plugins: { legend: { position: "bottom", labels: { boxWidth: 12, font: { size: 12 } } } }, responsive: true, maintainAspectRatio: false },
    });
    barChart = new Chart($("dash-bar"), {
      type: "bar",
      data: { labels, datasets: [{ data: values, backgroundColor: colors, borderRadius: 6 }] },
      options: { plugins: { legend: { display: false } }, scales: { y: { beginAtZero: true, ticks: { precision: 0 } }, x: { grid: { display: false } } }, responsive: true, maintainAspectRatio: false },
    });
  }

  function renderTrend(rows, ds, de) {
    let labels = dateRange(ds, de);
    const counts = {};
    rows.forEach((r) => {
      const day = (r.created_at || "").slice(0, 10);
      if (day) counts[day] = (counts[day] || 0) + 1;
    });
    // 기간이 너무 길어 채움이 잘리면, 데이터가 있는 날짜만 사용
    if (labels.length >= 400) labels = Object.keys(counts).sort();
    const values = labels.map((d) => counts[d] || 0);
    if (trendChart) trendChart.destroy();
    trendChart = new Chart($("dash-trend"), {
      type: "line",
      data: { labels, datasets: [{ data: values, borderColor: "#6366F1", backgroundColor: "#6366F133", fill: true, tension: 0.3, pointRadius: labels.length > 60 ? 0 : 2 }] },
      options: { plugins: { legend: { display: false } }, scales: { y: { beginAtZero: true, ticks: { precision: 0 } }, x: { grid: { display: false }, ticks: { maxTicksLimit: 12, autoSkip: true } } }, responsive: true, maintainAspectRatio: false },
    });
  }

  function renderTemplateChart(rows) {
    const counts = {};
    rows.forEach((r) => {
      const t = (r.template_name || "").trim() || "(양식 없음)";
      counts[t] = (counts[t] || 0) + 1;
    });
    const top = Object.entries(counts).sort((a, b) => b[1] - a[1]).slice(0, 8);
    const labels = top.map((x) => x[0]);
    const values = top.map((x) => x[1]);
    if (templateChart) templateChart.destroy();
    templateChart = new Chart($("dash-template"), {
      type: "bar",
      data: { labels, datasets: [{ data: values, backgroundColor: "#818CF8", borderRadius: 6 }] },
      options: { indexAxis: "y", plugins: { legend: { display: false } }, scales: { x: { beginAtZero: true, ticks: { precision: 0 } }, y: { grid: { display: false } } }, responsive: true, maintainAspectRatio: false },
    });
  }

  function renderTable(perGroup) {
    const entries = Object.entries(perGroup || {}).sort((a, b) => b[1] - a[1]);
    $("dash-table-body").innerHTML =
      entries.map(([name, cnt]) => `<tr><td>${esc(name)}</td><td>${cnt.toLocaleString()}</td></tr>`).join("") ||
      `<tr><td colspan="2" class="dash-table-empty">데이터 없음</td></tr>`;
  }

  const STATUS_ORDER = { "요청": 0, "진행": 1, "완료": 2, "승인": 3, "비task": 4 };
  function sortValue(r, key) {
    if (key === "status") return STATUS_ORDER[r.status] ?? 9;
    if (key === "children_count") return r.children_count || 0;
    return String(r[key] || "");
  }
  function getVisibleRows() {
    let rows = activeFilter === "전체" ? allRows : allRows.filter((r) => r.status === activeFilter);
    if (searchTerm) {
      const q = searchTerm.toLowerCase();
      rows = rows.filter((r) =>
        `${r.body_preview || ""} ${r.template_name || ""} ${r.author || ""}`.toLowerCase().includes(q));
    }
    const dir = sortDir === "asc" ? 1 : -1;
    rows = rows.slice().sort((a, b) => {
      const va = sortValue(a, sortKey), vb = sortValue(b, sortKey);
      if (va < vb) return -1 * dir;
      if (va > vb) return 1 * dir;
      return 0;
    });
    return rows;
  }

  function renderRows() {
    const rows = getVisibleRows();
    $("dash-rows-count").textContent = `· ${rows.length.toLocaleString()} / ${allRows.length.toLocaleString()}건`;
    // 정렬 표시
    document.querySelectorAll(".dash-rows-table th.sortable").forEach((th) => {
      th.classList.toggle("sort-asc", th.dataset.sort === sortKey && sortDir === "asc");
      th.classList.toggle("sort-desc", th.dataset.sort === sortKey && sortDir === "desc");
    });
    if (!rows.length) {
      $("dash-rows-body").innerHTML = `<tr><td colspan="9" class="dash-table-empty">조건에 맞는 글이 없습니다.</td></tr>`;
      return;
    }
    const aiOn = aiEl.checked;
    $("dash-rows-body").innerHTML = rows.map((r) => {
      const badge = STATUS_BADGE[r.status] || "badge-muted";
      const date = (r.created_at || "").slice(0, 16).replace("T", " ");
      const link = r.url ? `<a href="${esc(r.url)}" target="_blank" rel="noopener">열기 ↗</a>` : "";
      let sumCell;
      if (!aiOn) sumCell = `<span class="cell-ai-off">—</span>`;
      else if (summaries[r.message_id] != null) {
        sumCell = summaries[r.message_id]
          ? esc(summaries[r.message_id])
          : `<span class="cell-ai-off">—</span>`;       // 시도했으나 실패/빈 값
      } else sumCell = `<span class="cell-ai-pending">…</span>`;  // 아직 생성 전
      return `<tr>
        <td class="cell-date">${esc(date)}</td>
        <td><span class="badge ${badge}">${esc(r.status)}</span></td>
        <td class="cell-corp">${esc(r.corp)}</td>
        <td class="cell-template">${esc(r.template_name)}</td>
        <td class="cell-body" title="${esc(r.body_preview)}">${esc(r.body_preview)}</td>
        <td class="cell-ai">${sumCell}</td>
        <td>${esc(r.author)}</td>
        <td class="cell-num">${r.children_count || 0}</td>
        <td>${link}</td>
      </tr>`;
    }).join("");
  }

  function renderMeta(d) {
    const scope = d.target_group_title || "모듈 전체";
    const mismatch = d.count_mismatch_note ? ` · ${d.count_mismatch_note}` : "";
    $("dash-meta").textContent = `${d.group_name} › ${scope} · ${d.date_start} ~ ${d.date_end} · 전체 ${d.total_posts.toLocaleString()}건${mismatch}`;
  }
  function renderFresh(d) {
    const el = $("dash-fresh");
    if (!d.generated_at) { el.textContent = ""; return; }
    const t = d.generated_at.slice(11, 19);
    el.textContent = d.cached ? `· 갱신 ${t} (캐시 ${d.cache_age_sec}s)` : `· 갱신 ${t}`;
  }

  // ── CSV 내보내기 (현재 필터·검색·정렬 반영) ────────────────
  function csvCell(v) {
    return `"${String(v == null ? "" : v).replace(/"/g, '""')}"`;
  }
  function downloadCSV() {
    const rows = getVisibleRows();
    if (!rows.length) { showError("내보낼 행이 없습니다."); return; }
    const headers = ["작성일", "상태", "요청법인", "양식", "내용", "AI요약", "작성자", "댓글", "링크"];
    const lines = [headers.map(csvCell).join(",")];
    rows.forEach((r) => lines.push(
      [r.created_at, r.status, r.corp, r.template_name, r.body_preview,
       summaries[r.message_id] || "", r.author, r.children_count, r.url]
        .map(csvCell).join(",")));
    const blob = new Blob(["﻿" + lines.join("\r\n")], { type: "text/csv;charset=utf-8;" });
    const m = lastMeta || {};
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = `dashboard_${m.group_name || "agit"}_${m.date_start || ""}_${m.date_end || ""}.csv`;
    a.click();
    URL.revokeObjectURL(a.href);
  }

  // ── AI 요약 (조회 후 백그라운드, 청크 단위 점진 패치) ──────
  async function summarizeAll(runId) {
    const CAP = 300, CHUNK = 20;
    const targets = allRows.filter((r) => summaries[r.message_id] == null).slice(0, CAP);
    if (!targets.length) { aiStatusEl.textContent = ""; return; }
    const total = targets.length;
    let done = 0;
    aiStatusEl.textContent = ` · AI 요약 0/${total}`;
    for (let i = 0; i < targets.length; i += CHUNK) {
      if (runId !== summaryRunId) return;   // 새 조회/토글로 무효화 → 중단
      const chunk = targets.slice(i, i + CHUNK);
      const items = chunk.map((r) => ({ message_id: r.message_id, template: r.template_name, text: r.body_preview }));
      try {
        const res = await fetch("/api/dashboard/summarize", {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ items }),
        });
        const data = await res.json();
        if (runId !== summaryRunId) return;
        const got = data.summaries || {};
        chunk.forEach((r) => { summaries[r.message_id] = got[String(r.message_id)] ?? ""; });
      } catch (e) {
        chunk.forEach((r) => { summaries[r.message_id] = ""; });  // 실패 → "—", 재시도 방지
      }
      done += chunk.length;
      aiStatusEl.textContent = done < total ? ` · AI 요약 ${done}/${total}` : "";
      renderRows();
    }
  }

  // ── 조회 ───────────────────────────────────────────────────
  function setBusy(busy) {
    runBtn.disabled = busy;
    refreshBtn.disabled = busy;
    document.querySelectorAll(".preset-chip").forEach((c) => (c.disabled = busy));
  }

  async function run(opts = {}) {
    // 이전 요청 취소 — 프리셋 연타 등으로 늦은 응답이 화면을 덮어쓰는 race 방지
    if (currentController) currentController.abort();
    const controller = new AbortController();
    currentController = controller;
    const myRun = ++summaryRunId;   // 진행 중이던 이전 요약 작업 무효화

    errEl.hidden = true;
    resultsEl.hidden = true;
    emptyEl.hidden = true;
    loadingEl.hidden = false;
    setBusy(true);
    setStatus("집계 중…", "warning");

    const params = new URLSearchParams({
      group: groupSel.value,
      date_start: startEl.value,
      date_end: endEl.value,
      target_group_id: targetSel.value,
      exclude_bot: excludeBotEl.checked,
      compare: compareEl.checked,
      nocache: !!opts.nocache,
    });

    try {
      const res = await fetch(`/api/dashboard/stats?${params.toString()}`, { signal: controller.signal });
      const data = await res.json();
      if (!res.ok) throw new Error(data.detail || `요청 실패 (${res.status})`);
      if (data.error) throw new Error(data.error);

      lastMeta = data;
      renderMeta(data);
      renderFresh(data);
      renderCards(data.report_rows || [], data.comparison);
      renderStatusCharts(data.task_status_counts || {});
      allRows = data.rows || [];
      renderTrend(allRows, data.date_start, data.date_end);
      renderTemplateChart(allRows);
      renderRows();
      resultsEl.hidden = false;
      setStatus("● 완료", "success");
      // 표는 이미 떴고, AI 요약만 백그라운드로 채워 넣음 (전체 조회시간에 영향 X)
      if (aiEl.checked) summarizeAll(myRun);
      else aiStatusEl.textContent = "";
    } catch (e) {
      if (e.name === "AbortError") return;   // 새 요청에 의해 취소됨 — UI 그대로 둠
      showError(e.message || String(e));
      emptyEl.hidden = false;
      setStatus("오류", "error");
    } finally {
      // 이 요청이 아직 최신일 때만 로딩/버튼 상태 복구 (취소된 옛 요청은 건드리지 않음)
      if (currentController === controller) {
        loadingEl.hidden = true;
        setBusy(false);
        currentController = null;
      }
    }
  }

  runBtn.addEventListener("click", () => run());
  refreshBtn.addEventListener("click", () => run({ nocache: true }));
  csvBtn.addEventListener("click", downloadCSV);

  // AI 요약 토글 — 재조회 없이 컬럼 표시/생성 전환 (Agit 호출 X)
  aiEl.addEventListener("change", () => {
    renderRows();
    if (aiEl.checked && allRows.length) summarizeAll(++summaryRunId);
    else aiStatusEl.textContent = "";
  });

  $("dash-presets").addEventListener("click", (e) => {
    const chip = e.target.closest(".preset-chip");
    if (!chip || chip.disabled) return;
    setPreset(chip.dataset.preset);
    run();
  });

  // 검색 (디바운스)
  let searchTimer = null;
  searchEl.addEventListener("input", () => {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => { searchTerm = searchEl.value.trim(); renderRows(); }, 200);
  });

  // 정렬 (컬럼 헤더 클릭)
  document.querySelector(".dash-rows-table thead").addEventListener("click", (e) => {
    const th = e.target.closest("th.sortable");
    if (!th) return;
    const key = th.dataset.sort;
    if (sortKey === key) sortDir = sortDir === "asc" ? "desc" : "asc";
    else { sortKey = key; sortDir = key === "created_at" ? "desc" : "asc"; }
    renderRows();
  });

  $("dash-filter").addEventListener("click", (e) => {
    const chip = e.target.closest(".filter-chip");
    if (!chip) return;
    activeFilter = chip.dataset.status;
    document.querySelectorAll(".filter-chip").forEach((c) => c.classList.toggle("active", c === chip));
    renderRows();
  });
})();
