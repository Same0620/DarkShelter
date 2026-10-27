// DarkShelter EOC board. Vanilla JS; polls /api/state with a chained setTimeout (never setInterval).
(function () {
  "use strict";
  var POLL_MS = 1000;
  var lastSig = "";
  var busy = {};          // "desk:seq" -> true while a status POST is running
  var failures = 0;

  function $(id) { return document.getElementById(id); }
  function esc(s) {
    return String(s === null || s === undefined ? "" : s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }
  function fmtBytes(n) {
    n = n || 0;
    if (n < 1024) return n + " B";
    if (n < 1024 * 1024) return (n / 1024).toFixed(1) + " KB";
    return (n / 1024 / 1024).toFixed(2) + " MB";
  }
  function fmtAge(min) {
    if (min === null || min === undefined) return "—";
    if (min < 60) return min + " 分";
    return Math.floor(min / 60) + " 時 " + (min % 60) + " 分";
  }
  function toast(msg) {
    var t = $("toast");
    t.textContent = msg;
    t.hidden = false;
    setTimeout(function () { t.hidden = true; }, 3500);
  }

  var STATUS_BTNS = [
    { s: 1, zh: "已收到", en: "Ack" },
    { s: 2, zh: "已派遣", en: "Dispatched" },
    { s: 3, zh: "已解決", en: "Resolved" }
  ];

  function flagsHtml(t) {
    var out = [];
    if (t.emergency) out.push('<span class="flag emergency">🚨 緊急 <small>EMERGENCY</small></span>');
    if (t.unverified) out.push('<span class="flag unverified">❔ 未確認 <small>UNVERIFIED</small></span>');
    if (t.escalated) out.push('<span class="flag escalated">⚠ 已升級 <small>ESCALATED</small></span>');
    if (t.protection) out.push('<span class="flag protection">🔒 保護 <small>protection</small></span>');
    else if (t.sensitive) out.push('<span class="flag sensitive">🔒 機密 <small>confidential</small></span>');
    if (t.closed) out.push('<span class="flag closed">✖ 已撤回／結案 <small>closed</small></span>');
    if (t.multi) out.push('<span class="flag multi">👥 多人回報 <small>several reports</small></span>');
    if (t.approx) out.push('<span class="flag multi">≈ 約略數量 <small>approx.</small></span>');
    return out.join("");
  }

  function rowHtml(t) {
    var key = t.desk_id + ":" + t.seq;
    var pr = t.priority || 3;
    var where = t.external ? "📍 外部地點 <small>external</small>" : (t.zone_label ? "🛏 " + esc(t.zone_label) : "—");
    var what = (t.icon || "") + " " + esc(t.category_zh) + (t.item_zh ? "／" + esc(t.item_zh) : "");
    var qty = t.quantity ? (t.approx ? "約 " : "") + t.quantity : "—";
    var btns = STATUS_BTNS.map(function (b) {
      var done = t.status >= b.s;
      var dis = busy[key] || done || t.closed;
      return '<button type="button" data-desk="' + t.desk_id + '" data-seq="' + t.seq + '" data-status="' + b.s +
        '" class="' + (done ? "done" : "") + '"' + (dis ? " disabled" : "") + ">" + b.zh + "<small>" + b.en +
        "</small></button>";
    }).join("");
    return '<div class="row p' + pr + (t.closed ? " closed" : "") + '">' +
      '<div class="code">' + esc(t.code) + '<br><span class="pill p' + pr + '">P' + pr + "</span></div>" +
      '<div class="body"><div class="summary">' + esc(t.summary || "（無摘要 no summary）") + "</div>" +
      '<div class="meta"><span>' + what + "</span><span>數量 <small>qty</small> " + qty + "</span><span>" + where +
      "</span><span>建立 <small>age</small> " + fmtAge(t.age_min) + "</span><span>送達延遲 <small>latency</small> " +
      fmtAge(t.latency_min) + "</span><span>v" + esc(t.version) + "</span>" +
      (t.dup_count ? "<span>重複 <small>dups</small> " + t.dup_count + "</span>" : "") + "</div>" +
      '<div class="flags">' + flagsHtml(t) + "</div></div>" +
      '<div class="actions"><div class="status">' + (t.status ? "✔ " + esc(t.status_zh) : "⏳ 未回覆") +
      '<small>' + esc(t.status ? t.status_en : "no status") + "</small></div>" +
      '<div class="btns">' + btns + "</div></div></div>";
  }

  function render(st) {
    $("c-p1").textContent = st.counts.p1;
    $("c-p2").textContent = st.counts.p2;
    $("c-p3").textContent = st.counts.p3;
    $("c-open").textContent = st.counts.open;
    var groups = { 1: [], 2: [], 3: [] };
    st.tickets.forEach(function (t) { (groups[t.priority] || groups[3]).push(t); });
    var titles = { 1: "P1 緊急 <small>urgent</small>", 2: "P2 優先 <small>priority</small>",
                   3: "P3 一般 <small>routine</small>" };
    var html = "";
    [1, 2, 3].forEach(function (p) {
      if (!groups[p].length) return;
      html += '<div class="group p' + p + '"><h3>' + titles[p] + " · " + groups[p].length + "</h3>" +
        groups[p].map(rowHtml).join("") + "</div>";
    });
    $("groups").innerHTML = html;
    $("empty").hidden = st.tickets.length > 0;

    var dh = st.desks.map(function (d) {
      var c = d.census || {};
      var stale = d.last_seen_s !== null && d.last_seen_s > 60;
      var bat = (c.battery === undefined || c.battery === 255) ? "—" : c.battery + "%";
      return '<div class="desk"><div><strong>' + esc(d.letter) + " 站</strong> <small>desk " + d.desk_id +
        "</small></div><div class=\"people\">👥 " + (c.people || 0) + " <small>人 people</small></div>" +
        "<table><tr><td>長者 <small>elderly</small></td><td>" + (c.elderly || 0) + "</td></tr>" +
        "<tr><td>嬰幼兒 <small>infants/children</small></td><td>" + (c.infant_child || 0) + "</td></tr>" +
        "<tr><td>身障 <small>disabled</small></td><td>" + (c.disabled || 0) + "</td></tr>" +
        "<tr><td>孕婦 <small>pregnant</small></td><td>" + (c.pregnant || 0) + "</td></tr>" +
        "<tr><td>待處理 P1/P2/P3 <small>desk queue</small></td><td>" + (c.q_p1 || 0) + " / " + (c.q_p2 || 0) +
        " / " + (c.q_p3 || 0) + "</td></tr>" +
        "<tr><td>電量 <small>battery</small></td><td>" + bat + "</td></tr>" +
        "<tr><td>最後聯絡 <small>last seen</small></td><td class=\"" + (stale ? "stale" : "") + "\">" +
        (d.last_seen_s === null ? "—" : Math.round(d.last_seen_s) + " 秒前") + "</td></tr></table></div>";
    }).join("");
    $("desks").innerHTML = dh || '<p class="muted">尚未收到心跳 <small>no heartbeat yet</small></p>';

    var s = st.stats || {};
    $("s-records").textContent = s.records_rx || 0;
    $("s-dups").textContent = s.duplicates || 0;
    $("s-bytes").textContent = fmtBytes(s.bytes_rx || 0);
    $("s-dgrams").textContent = s.datagrams_rx || 0;
    $("s-hb").textContent = s.heartbeats_rx || 0;
    $("s-badmac").textContent = s.bad_mac || 0;
  }

  function setConn(ok) {
    $("conn-dot").className = "dot " + (ok ? "ok" : "bad");
    $("conn-text").textContent = ok ? "看板連線正常" : "看板連線中斷";
    $("conn-en").textContent = ok ? "board online" : "board offline";
  }

  function poll() {
    fetch("/api/state", { cache: "no-store" })
      .then(function (r) { if (!r.ok) throw new Error("HTTP " + r.status); return r.json(); })
      .then(function (st) {
        failures = 0;
        setConn(true);
        var sig = JSON.stringify([st.tickets, st.desks.map(function (d) { return [d.census, Math.round(d.last_seen_s || 0)]; }),
          st.stats, Object.keys(busy)]);
        if (sig !== lastSig) { lastSig = sig; render(st); }
      })
      .catch(function () { failures += 1; if (failures > 1) setConn(false); })
      .then(function () { setTimeout(poll, POLL_MS); });
  }

  document.addEventListener("click", function (ev) {
    var b = ev.target.closest ? ev.target.closest("button[data-status]") : null;
    if (!b || b.disabled) return;
    var desk = b.getAttribute("data-desk"), seq = b.getAttribute("data-seq"), status = +b.getAttribute("data-status");
    var key = desk + ":" + seq;
    busy[key] = true;
    b.disabled = true;
    fetch("/api/tickets/" + desk + "/" + seq + "/status", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ status: status })
    }).then(function (r) {
      return r.json().then(function (j) { if (!r.ok) throw j; return j; });
    }).then(function () {
      toast("已更新，下次心跳時回傳給收容處所 · Saved; goes back to the desk with the next heartbeat");
    }).catch(function (e) {
      toast((e && e.zh) ? e.zh + " · " + (e.en || "") : "更新失敗 · update failed");
    }).then(function () { delete busy[key]; lastSig = ""; });
  });

  poll();
})();
