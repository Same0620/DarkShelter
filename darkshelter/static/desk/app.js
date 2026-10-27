// DarkShelter desk UI: vanilla JS, no build step, no CDN. Polls /api/state with a chained setTimeout.
(function () {
  "use strict";

  var S = {
    st: null, cb: null, clerk: "", privacy: true, revealed: {}, cards: {}, lastJobId: null,
    beepAt: {}, audioCtx: null, typeOnly: false, failures: 0, hhAt: 0
  };
  var STAGES = {
    queued: "排隊中 queued", checking: "檢查錄音 checking", transcribing: "語音辨識 transcribing",
    understanding: "理解中 understanding", refining: "AI整理 refining", done: "完成 done", error: "錯誤 error"
  };
  var PRIO_ICON = { 1: "🔴", 2: "🟠", 3: "⚪" };
  var FLAG_LABEL = {
    asr_low_conf: "語音信心低", raised_by_ai_guess: "AI猜測提高", ai_only: "僅AI找到", llm_timeout: "AI逾時",
    llm_error: "AI錯誤", fast_mode: "快速模式", negated: "否定／過去", sensitive: "機密醫療", unverified: "未確認",
    escalated: "已升級", approx: "約略數量", unclassified_red_flag: "未分類危險字詞"
  };
  var EMERGENCY_ACTIONS = [
    ["medic_told", "已通知現場醫護"], ["called_119", "已撥119"], ["119_unreachable", "119打不通"],
    ["runner_sent", "已派人通報"]
  ];
  var FIELDS = [
    ["category", "類別", "Category"], ["item_code", "品項", "Item"], ["quantity", "數量", "Qty"],
    ["subject", "對象", "Who"], ["subject_group", "族群", "Group"], ["location", "位置", "Where"],
    ["time_phrase", "時間", "When"]
  ];
  var QUICK = ["洗腎", "胰島素", "氧氣", "製氧機", "嬰兒奶粉", "尿布", "輪椅", "毛毯", "飲用水", "受困", "失聯"];

  function $(id) { return document.getElementById(id); }
  function esc(s) {
    return String(s === null || s === undefined ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function lsGet(k, d) { try { var v = window.localStorage.getItem(k); return v === null ? d : v; } catch (e) { return d; } }
  function lsSet(k, v) { try { window.localStorage.setItem(k, v); } catch (e) { /* private mode */ } }
  function fmtBytes(n) {
    n = n || 0;
    if (n < 1024) return n + " B";
    if (n < 1048576) return (n / 1024).toFixed(1) + " KB";
    return (n / 1048576).toFixed(2) + " MB";
  }
  function fmtTime(wall) {
    if (!wall) return "";
    var d = new Date(wall * 1000);
    function p(x) { return x < 10 ? "0" + x : "" + x; }
    return p(d.getHours()) + ":" + p(d.getMinutes());
  }
  function ago(wall, now) {
    if (!wall) return "";
    var s = Math.max(0, Math.round(now - wall));
    if (s < 60) return s + "秒前";
    if (s < 3600) return Math.floor(s / 60) + "分前";
    return Math.floor(s / 3600) + "時" + Math.floor((s % 3600) / 60) + "分前";
  }

  function api(method, url, body) {
    var opts = { method: method, headers: {} };
    if (body !== undefined) {
      opts.headers["Content-Type"] = "application/json";
      opts.body = JSON.stringify(body);
    }
    return fetch(url, opts).then(function (r) {
      return r.text().then(function (txt) {
        var data = null;
        try { data = txt ? JSON.parse(txt) : null; } catch (e) { data = { zh: txt, en: txt }; }
        return { ok: r.ok, status: r.status, data: data || {} };
      });
    }).catch(function (e) {
      return { ok: false, status: 0, data: { error: "network", zh: "連不到登記台程式", en: String(e) } };
    });
  }

  var toastTimer = null;
  function toast(msg, kind, actions, ms) {
    var el = $("toast");
    el.className = "toast " + (kind || "");
    el.innerHTML = "<span>" + msg + "</span>";
    (actions || []).forEach(function (a) {
      var b = document.createElement("button");
      b.className = "btn btn-small";
      b.textContent = a.label;
      b.onclick = function () { el.className = "toast hidden"; a.fn(); };
      el.appendChild(b);
    });
    clearTimeout(toastTimer);
    toastTimer = setTimeout(function () { el.className = "toast hidden"; }, ms || 4000);
  }
  function showError(res) {
    var d = res.data || {};
    toast("⚠️ " + esc(d.zh || "發生錯誤") + " <small>" + esc(d.en || "") + "</small>", "error", null, 6000);
  }

  function beep() {
    try {
      var AC = window.AudioContext || window.webkitAudioContext;
      if (!AC) return;
      if (!S.audioCtx) S.audioCtx = new AC();
      var ctx = S.audioCtx, o = ctx.createOscillator(), g = ctx.createGain();
      o.frequency.value = 880; g.gain.value = 0.08;
      o.connect(g); g.connect(ctx.destination);
      o.start(); o.stop(ctx.currentTime + 0.25);
    } catch (e) { /* no audio */ }
  }

  // ------------------------------------------------------------------ codebook helpers
  function catOf(code) {
    if (!S.cb) return { zh: "", icon: "" };
    for (var i = 0; i < S.cb.categories.length; i++) if (S.cb.categories[i].code === code) return S.cb.categories[i];
    return S.cb.categories[0];
  }
  function itemsOf(cat) {
    if (!S.cb) return [];
    return S.cb.items.filter(function (it) { return it.category === cat; });
  }

  // ------------------------------------------------------------------ draft cards
  function isBusy(c) {
    if (c.dirty) return true;
    var a = document.activeElement;
    return !!(a && c.el.contains(a) && /INPUT|SELECT|TEXTAREA/.test(a.tagName));
  }
  function ungrounded(t, f) {
    var key = f === "item_code" ? "item" : f;
    return (t.flags || []).indexOf("ungrounded:" + key) >= 0 || (t.field_sources || {})[key] === "llm_ungrounded";
  }
  function fieldInput(t, f) {
    var v, opts;
    if (f === "category") {
      opts = (S.cb ? S.cb.categories : []).map(function (c) {
        return '<option value="' + c.code + '"' + (c.code === t.category ? " selected" : "") + ">" + esc(c.icon + " " + c.zh) + "</option>";
      }).join("");
      return '<select data-field="category">' + opts + "</select>";
    }
    if (f === "item_code") {
      opts = '<option value="0">（未指定）</option>' + itemsOf(t.category).map(function (it) {
        return '<option value="' + it.code + '"' + (it.code === t.item_code ? " selected" : "") + ">" + esc(it.zh) + "</option>";
      }).join("");
      return '<select data-field="item_code">' + opts + "</select>";
    }
    if (f === "subject_group") {
      opts = (S.cb ? S.cb.subject_groups : []).map(function (g) {
        return '<option value="' + g.key + '"' + (g.key === t.subject_group ? " selected" : "") + ">" + esc(g.zh) + "</option>";
      }).join("");
      return '<select data-field="subject_group">' + opts + "</select>";
    }
    if (f === "quantity") {
      v = t.quantity === null || t.quantity === undefined ? "" : t.quantity;
      return '<input data-field="quantity" type="number" min="0" value="' + esc(v) + '">' + (t.quantity_approx ? '<span class="chip small">約</span>' : "");
    }
    v = t[f] || "";
    return '<input data-field="' + f + '" value="' + esc(v) + '">';
  }
  function evidenceOf(t, f) {
    var key = f === "item_code" ? "item" : f;
    var q = (t.evidence || {})[key];
    var extra = "";
    if (f === "location" && t.zone_label) extra = " → " + esc(t.zone_label);
    if (f === "time_phrase" && t.deadline_text) extra = " → " + esc(t.deadline_text);
    var warn = ungrounded(t, f) ? ' <span class="amber-note">AI猜測，請確認</span>' : "";
    return (q ? "「" + esc(q) + "」" : "") + extra + warn;
  }

  function cardHtml(t) {
    var cat = catOf(t.category);
    var h = [];
    h.push('<div class="card-head">');
    h.push('<span class="code">' + esc(t.code) + "</span>");
    h.push('<span class="pill p' + t.priority + '">' + PRIO_ICON[t.priority] + " P" + t.priority + "</span>");
    if (t.deadline_text) h.push('<span class="deadline">→ ' + esc(t.deadline_text) + "</span>");
    h.push('<span class="cat">' + esc(cat.icon + " " + cat.zh) + "</span>");
    if (t.report_count > 1) h.push('<span class="chip">回報×' + t.report_count + "</span>");
    (t.flags || []).forEach(function (f) {
      if (f.indexOf("ungrounded:") === 0) return;
      h.push('<span class="chip flag">' + esc(FLAG_LABEL[f] || f) + "</span>");
    });
    h.push('<span class="ai-badge hidden">AI 已更新 <small>AI updated</small></span>');
    h.push("</div>");
    if (t.priority_reasons && t.priority_reasons.length) {
      h.push('<div class="reasons">' + t.priority_reasons.map(function (r) {
        return '<span title="' + esc(r.en) + '">' + esc(r.zh) + "</span>";
      }).join("・") + "</div>");
    }
    if (t.emergency && !t.emergency_action) {
      if (t.emergency.indexOf("protection") >= 0) {
        h.push('<div class="emergency protection">🔒 保護個案：請移到隱密處，聯絡社工／113 <small>Protection case: move to a private spot, social worker / 113</small>' +
          '<div class="em-actions"><button class="btn btn-em" data-action="em" data-em="protection_private">已移到隱密處／聯絡社工 113</button></div></div>');
      } else {
        h.push('<div class="emergency">🚨 緊急：請通知現場醫護或撥打119 <small>Emergency: get the on-site medic / call 119</small><div class="em-actions">' +
          EMERGENCY_ACTIONS.map(function (a) {
            return '<button class="btn btn-em" data-action="em" data-em="' + a[0] + '">' + esc(a[1]) + "</button>";
          }).join("") + "</div></div>");
      }
    } else if (t.emergency_action) {
      h.push('<div class="em-done">已處置：' + esc(t.emergency_action) + "</div>");
    }
    if (t.transcript) {
      h.push('<blockquote class="transcript' + ((t.flags || []).indexOf("asr_low_conf") >= 0 ? " amber" : "") + '">「' + esc(t.transcript) + "」</blockquote>");
    }
    if (t.summary) h.push('<div class="summary">📝 ' + esc(t.summary) + "</div>");
    h.push('<div class="fields">');
    FIELDS.forEach(function (fd) {
      var f = fd[0];
      h.push('<div class="field' + (ungrounded(t, f) ? " amber" : "") + '"><label>' + fd[1] + " <small>" + fd[2] + "</small></label>" +
        fieldInput(t, f) + '<div class="evidence">' + evidenceOf(t, f) + "</div></div>");
    });
    h.push("</div>");
    if (t.missing_info && t.missing_info.length) {
      h.push('<div class="askback">💬 ' + t.missing_info.map(function (q) {
        return '<span class="chip ask">' + esc(q) + "</span>";
      }).join("") + "</div>");
    }
    (t.dup_candidates || []).forEach(function (d) {
      h.push('<div class="dup">🔁 可能與 <b>' + esc(d.code) + "</b> 重複" + (d.kind === "add" ? "（可加總）" : "") +
        ' <button class="btn btn-small" data-action="merge" data-target="' + d.seq + '">合併</button>' +
        ' <button class="btn btn-small" data-action="notdup">不是重複</button></div>');
    });
    h.push('<div class="card-actions">');
    h.push('<select class="prio-select" title="優先等級">' + [1, 2, 3].map(function (p) {
      return '<option value="' + p + '"' + (p === t.priority ? " selected" : "") + ">P" + p + "</option>";
    }).join("") + "</select>");
    h.push('<button class="btn btn-confirm" data-action="confirm">✔ 確認 <small>Confirm</small></button>');
    if (t.priority !== 1) h.push('<button class="btn btn-worried" data-action="worried">😟 我擔心→P1</button>');
    h.push('<button class="btn btn-small hidden" data-action="save">💾 儲存修改</button>');
    h.push('<button class="btn btn-small" data-action="refine" title="請 AI 再整理一次">🤖 AI</button>');
    h.push('<button class="btn btn-small btn-discard" data-action="discard">🗑 捨棄</button>');
    h.push('<input class="reason-input hidden" placeholder="原因（必填） Reason (required)">');
    h.push("</div>");
    return h.join("");
  }

  function fillCard(c, t) {
    c.t = t;
    c.el.className = "draft-card p" + t.priority + (t.emergency && !t.emergency_action ? " has-emergency" : "") + (t.hidden ? " hidden-cat" : "");
    c.el.innerHTML = cardHtml(t);
    c.dirty = false;
    c.edited = {};
    c.pending = null;
  }

  function collectEdits(c) {
    var out = {};
    Object.keys(c.edited || {}).forEach(function (f) {
      var el = c.el.querySelector('[data-field="' + f + '"]');
      if (!el) return;
      var v = el.value;
      if (f === "category" || f === "item_code") v = parseInt(v, 10) || 0;
      if (f === "quantity") v = v === "" ? null : parseInt(v, 10);
      out[f] = v;
    });
    return out;
  }

  function needsReason(c, kind) {
    var input = c.el.querySelector(".reason-input");
    var reason = input.value.trim();
    if (reason) return reason;
    input.classList.remove("hidden");
    input.placeholder = kind === "discard" ? "捨棄原因（必填） Reason to discard" : "調降原因（必填） Reason to lower";
    input.focus();
    toast(kind === "discard" ? "這筆是P1或有危險字詞，捨棄需要原因" : "調降優先等級需要填寫原因", "warn");
    return null;
  }

  function onCardClick(c, ev) {
    var b = ev.target.closest("[data-action]");
    if (!b || !c.el.contains(b)) return;
    var t = c.t, seq = t.seq, act = b.getAttribute("data-action");
    var url = "/api/tickets/" + seq + "/";
    if (act === "confirm") {
      var prio = parseInt(c.el.querySelector(".prio-select").value, 10);
      var floor = t.rule_priority || t.priority;
      var body = { fields: collectEdits(c), priority: prio, clerk: S.clerk };
      if (prio > floor) {
        var r = needsReason(c, "lower");
        if (!r) return;
        body.reason = r;
      }
      b.disabled = true;
      api("POST", url + "confirm", body).then(function (res) {
        b.disabled = false;
        if (!res.ok) return showError(res);
        c.dirty = false;
        toast("✓ 已確認並儲存 " + esc(t.code) + " <small>saved</small>", "ok");
        pollSoon();
      });
    } else if (act === "worried") {
      api("POST", url + "worried", { clerk: S.clerk }).then(function (res) {
        if (!res.ok) return showError(res);
        toast("😟 " + esc(t.code) + " 已提高為 P1", "warn");
        c.dirty = false; pollSoon();
      });
    } else if (act === "em") {
      var action = b.getAttribute("data-em");
      api("POST", url + "emergency_action", { action: action, clerk: S.clerk }).then(function (res) {
        if (!res.ok) return showError(res);
        if (action === "119_unreachable" && S.st) {
          var bc = S.st.desk.backup_channel || {};
          toast("📻 備援通報：" + esc(bc.zh || "") + " <small>" + esc(bc.en || "") + "</small>", "error", null, 12000);
        } else {
          toast("已記錄處置 ✓", "ok");
        }
        pollSoon();
      });
    } else if (act === "discard") {
      var body2 = { clerk: S.clerk };
      if (t.priority === 1 || t.rule_priority === 1 || t.emergency) {
        var r2 = needsReason(c, "discard");
        if (!r2) return;
        body2.reason = r2;
      } else {
        body2.reason = c.el.querySelector(".reason-input").value.trim() || null;
      }
      api("POST", url + "discard", body2).then(function (res) {
        if (!res.ok) return showError(res);
        toast("🗑 已捨棄 " + esc(t.code), "", [{
          label: "復原 Undo", fn: function () {
            api("POST", url + "restore", { clerk: S.clerk }).then(function (r3) { if (!r3.ok) showError(r3); pollSoon(); });
          }
        }], 10000);
        pollSoon();
      });
    } else if (act === "merge") {
      api("POST", url + "merge", { target: parseInt(b.getAttribute("data-target"), 10), clerk: S.clerk }).then(function (res) {
        if (!res.ok) return showError(res);
        toast("🔁 已合併 " + esc(t.code), "ok"); pollSoon();
      });
    } else if (act === "notdup") {
      api("POST", url + "not_duplicate", { clerk: S.clerk }).then(function (res) {
        if (!res.ok) return showError(res); pollSoon();
      });
    } else if (act === "save") {
      api("POST", url + "update", { fields: collectEdits(c), expected_version: t.version, clerk: S.clerk }).then(function (res) {
        if (!res.ok) {
          if (res.status === 409) { c.dirty = false; }
          return showError(res);
        }
        c.dirty = false;
        toast("💾 已儲存", "ok"); pollSoon();
      });
    } else if (act === "refine") {
      api("POST", url + "refine", { clerk: S.clerk }).then(function (res) {
        if (!res.ok) return showError(res);
        S.lastJobId = res.data.job_id; toast("🤖 AI 整理中…", "");
      });
    }
  }

  function onCardEdit(c, ev) {
    var f = ev.target.getAttribute("data-field");
    if (!f) return;
    c.dirty = true;
    c.edited = c.edited || {};
    c.edited[f] = true;
    var save = c.el.querySelector('[data-action="save"]');
    if (save) save.classList.remove("hidden");
    if (f === "category") {
      var sel = c.el.querySelector('[data-field="item_code"]');
      var cat = parseInt(ev.target.value, 10) || 0;
      if (sel) {
        sel.innerHTML = '<option value="0">（未指定）</option>' + itemsOf(cat).map(function (it) {
          return '<option value="' + it.code + '">' + esc(it.zh) + "</option>";
        }).join("");
        c.edited.item_code = true;
      }
    }
  }

  function makeCard(seq) {
    var c = { el: document.createElement("div"), sig: null, dirty: false, pending: null, edited: {} };
    c.el.setAttribute("data-seq", seq);
    c.el.addEventListener("click", function (ev) { onCardClick(c, ev); });
    c.el.addEventListener("input", function (ev) { onCardEdit(c, ev); });
    c.el.addEventListener("change", function (ev) { onCardEdit(c, ev); });
    c.el.addEventListener("focusout", function () {
      setTimeout(function () {
        if (c.pending && !isBusy(c)) { fillCard(c, c.pending); c.sig = JSON.stringify(c.t); }
      }, 50);
    });
    return c;
  }

  function renderDrafts(st) {
    var box = $("drafts"), seen = {};
    $("draft-count").textContent = st.drafts.length ? "(" + st.drafts.length + ")" : "";
    st.drafts.forEach(function (t, idx) {
      seen[t.seq] = true;
      var c = S.cards[t.seq];
      if (!c) { c = makeCard(t.seq); S.cards[t.seq] = c; }
      var sig = JSON.stringify(t);
      if (c.sig !== sig) {
        if (c.sig !== null && isBusy(c)) {
          c.pending = t;
          var badge = c.el.querySelector(".ai-badge");
          if (badge) badge.classList.remove("hidden");
        } else {
          fillCard(c, t);
          c.sig = sig;
        }
      }
      if (!isBusy(c) && box.children[idx] !== c.el) box.insertBefore(c.el, box.children[idx] || null);
    });
    Object.keys(S.cards).forEach(function (seq) {
      if (!seen[seq]) {
        var c = S.cards[seq];
        if (c.el.parentNode) c.el.parentNode.removeChild(c.el);
        delete S.cards[seq];
      }
    });
    if (!st.drafts.length) box.innerHTML = '<p class="muted empty">目前沒有草稿 <small>No drafts</small></p>';
    else { var e = box.querySelector(".empty"); if (e) e.parentNode.removeChild(e); }
  }

  // ------------------------------------------------------------------ board
  function renderBoard(st) {
    var b = st.board, now = st.now, c = b.counts;
    $("board-counts").innerHTML =
      '<span class="pill p1">🔴 P1 ' + c.p1 + '</span><span class="pill p2">🟠 P2 ' + c.p2 + '</span><span class="pill p3">⚪ P3 ' + c.p3 +
      '</span><span class="pill">📝 草稿 ' + c.drafts + "</span>" + (b.hidden_count ? '<span class="pill">🔒 保護個案 ' + b.hidden_count + "</span>" : "");
    var groups = { 1: [], 2: [], 3: [], done: [] };
    b.tickets.forEach(function (t) {
      if (t.status === "resolved") groups.done.push(t); else groups[t.priority || 3].push(t);
    });
    var h = [];
    [[1, "P1 緊急"], [2, "P2 優先"], [3, "P3 一般"], ["done", "已結案 Resolved"]].forEach(function (g) {
      var list = groups[g[0]];
      if (!list.length) return;
      h.push('<div class="bgroup"><h4>' + g[1] + " <small>(" + list.length + ")</small></h4>");
      list.forEach(function (t) {
        var show = !S.privacy || S.revealed[t.seq];
        h.push('<div class="brow p' + t.priority + (t.status === "resolved" ? " done" : "") + '" data-seq="' + t.seq + '">' +
          '<span class="code">' + esc(t.code) + '</span><span class="icon" title="' + esc(t.category_zh) + '">' + esc(t.category_icon) + "</span>" +
          '<span class="zone">' + esc(t.zone_label || "—") + "</span>" +
          '<span class="wstate" title="' + esc(t.wire_state.en) + '">' + esc(t.wire_state.icon) + " " + esc(t.wire_state.zh) + "</span>" +
          '<span class="detail' + (show ? "" : " privacy") + '">' + esc(t.wire_summary) +
          (t.eoc_status ? ' <b class="eoc">' + esc(t.eoc_status_zh) + "</b>" : "") +
          ' <small class="muted">' + ago(t.confirmed_wall, now) + "</small>" +
          (t.status === "confirmed" ? ' <button class="btn btn-small" data-act="resolve" data-seq="' + t.seq + '">✔ 結案</button>' +
            (t.priority !== 1 ? ' <button class="btn btn-small" data-act="worried" data-seq="' + t.seq + '">😟 P1</button>' : "") : "") +
          "</span></div>");
      });
      h.push("</div>");
    });
    if (!b.tickets.length) h.push('<p class="muted">尚無已確認需求 <small>No confirmed requests yet</small></p>');
    $("board").innerHTML = h.join("");
    $("print-header").textContent = (st.desk.shelter_name || "") + " " + st.desk.letter + "站 列印時間 " + new Date().toLocaleString();
    // recently closed (undo)
    var rc = st.recent_closed || [];
    $("recent-closed").innerHTML = rc.length ? '<details><summary class="muted">最近捨棄／合併 <small>Recently closed</small> (' + rc.length + ")</summary>" +
      rc.map(function (t) {
        return '<div class="closed-row">' + esc(t.code) + " " + esc(t.category_icon) + " " + esc(t.status === "merged" ? "已合併" : "已捨棄") +
          ' <button class="btn btn-small" data-restore="' + t.seq + '">復原 Restore</button></div>';
      }).join("") + "</details>" : "";
  }

  // ------------------------------------------------------------------ right column
  function renderRight(st) {
    var s = st.sender || {}, hlth = st.health || {};
    var ls = s.link_state || "UNKNOWN";
    var lsZh = { UP: "連線", DOWN: "斷線", UNKNOWN: "未知" }[ls] || ls;
    $("link-state").className = "badge link-" + ls;
    $("link-state").textContent = lsZh + " " + ls;
    $("link-badge").className = "badge link-" + ls;
    $("link-badge").textContent = "📡 " + lsZh;
    var emu = st.emulator;
    var active = emu && emu.profile ? emu.profile : null;
    var names = { DOWN: "斷線", LORA_RELAY: "LoRa中繼", LTE_ONE_BAR: "LTE一格", SATELLITE: "衛星", FLAPPING: "時斷時續", GOOD: "正常" };
    $("profiles").innerHTML = (st.desk.profiles || []).map(function (p) {
      return '<button class="btn btn-small' + (p === active ? " active" : "") + '" data-profile="' + p + '" id="profile-' + p + '">' + esc(names[p] || p) + "<small>" + p + "</small></button>";
    }).join("") + (emu ? "" : '<div class="muted small">模擬器未連線 <small>emulator offline</small></div>');
    var queued = 0, queuedP1 = 0;
    st.board.tickets.forEach(function (t) {
      if (t.wire_state.icon === "⏳" || t.wire_state.icon === "↑") { queued++; if (t.priority === 1) queuedP1++; }
    });
    $("queue-line").innerHTML = "待送 <small>queued</small> " + queued + "（P1 " + queuedP1 + "）";
    var by = st.bytes || {};
    $("bm-data").textContent = fmtBytes(by.data_bytes_up);
    $("bm-voice").textContent = fmtBytes(by.voice_equiv_bytes);
    $("bm-ratio").innerHTML = by.ratio ? "少 <b>" + by.ratio + "</b> 倍 <small>" + by.ratio + "× fewer bytes</small>" : "";
    $("bm-total").innerHTML = "上傳 up " + fmtBytes(by.bytes_up) + " ・ 下載 down " + fmtBytes(by.bytes_down) +
      " <small>（打字以 " + (by.typed_nominal_s || 10) + " 秒語音計 / typed = " + (by.typed_nominal_s || 10) + " s of voice）</small>";
    function n(v, d) { return v === null || v === undefined ? "—" : (+v).toFixed(d); }
    $("netstats").innerHTML = '<div class="kv"><span>SRTT</span><b>' + n(s.srtt, 2) + ' s</b></div><div class="kv"><span>RTO</span><b>' + n(s.rto, 2) +
      ' s</b></div><div class="kv"><span>吞吐 <small>throughput</small></span><b>' + n(s.throughput_Bps, 0) + ' B/s</b></div><div class="kv"><span>重傳 <small>retx</small></span><b>' +
      (s.retransmissions || 0) + '</b></div><div class="kv"><span>封包 <small>datagrams</small></span><b>' + (s.datagrams_up || 0) + '</b></div><div class="kv"><span>已確認 <small>acked</small></span><b>' +
      (s.acked_units || 0) + "</b></div>";
    // system
    var modeZh = { FULL: "完整", FAST: "快速", TYPED: "打字", PAPER: "紙本" }[hlth.mode] || hlth.mode;
    $("mode-badge").className = "badge mode-" + hlth.mode;
    $("mode-badge").textContent = "⚙️ " + modeZh + " " + hlth.mode + (st.desk.mock ? " · MOCK" : "");
    $("mode-line").innerHTML = '<span class="badge mode-' + hlth.mode + '">' + esc(hlth.mode) + "</span> " + esc(hlth.reason_zh || "") + " <small>" + esc(hlth.reason_en || "") + "</small>";
    var ms = $("mode-select");
    if (document.activeElement !== ms) ms.value = hlth.forced || "";
    var lines = [];
    lines.push("🎙️ 語音模型 ASR：" + (hlth.asr_ok ? esc(hlth.asr_model || "ok") : hlth.asr_ok === null ? "載入中 loading" : "✗ " + esc(hlth.asr_error || "")));
    lines.push("🤖 AI：" + (hlth.llm_ok ? (hlth.llm_ready ? "✓ " : "暖機 ") + (hlth.llm_tps ? hlth.llm_tps + " tok/s" : "") + (hlth.prefix_cached ? " · prefix cached" : "") : "✗ 未執行 not running"));
    lines.push("🔋 電量：" + (hlth.battery === null || hlth.battery === undefined ? "未知 unknown" : hlth.battery + "%" + (hlth.charging ? " ⚡" : "")));
    lines.push("🈶 OpenCC：" + (hlth.opencc ? "✓" : "✗"));
    if (st.backlog) lines.push("⏱ 排隊 backlog：" + st.backlog);
    (hlth.warnings || []).forEach(function (w) { lines.push('<span class="amber-note">⚠ ' + esc(w) + "</span>"); });
    $("sys-lines").innerHTML = lines.map(function (l) { return "<div>" + l + "</div>"; }).join("");
    var th = hlth.threads || {};
    $("thread-lights").innerHTML = Object.keys(th).map(function (k) {
      var t = th[k];
      return '<span class="light ' + t.light + '" title="' + esc(t.note || "") + '">' + ({ green: "🟢", amber: "🟠", red: "🔴" }[t.light] || "⚪") + " " + esc(k) + (t.restarts ? " ↻" + t.restarts : "") + "</span>";
    }).join("");
    var cs = st.census || {};
    $("census").innerHTML = '<div class="kv big"><span>人數 <small>people</small></span><b>' + (cs.people || 0) + '</b></div><div class="kv"><span>戶數 <small>households</small></span><b>' + (cs.households || 0) +
      '</b></div><div class="kv"><span>長者 <small>elderly</small></span><b>' + (cs.elderly || 0) + '</b></div><div class="kv"><span>嬰幼兒 <small>infants+children</small></span><b>' + (cs.infant_child || 0) +
      '</b></div><div class="kv"><span>行動不便 <small>disabled</small></span><b>' + (cs.disabled || 0) + '</b></div><div class="kv"><span>孕婦 <small>pregnant</small></span><b>' + (cs.pregnant || 0) +
      '</b></div><div class="kv"><span>未結需求 <small>open P1/P2/P3</small></span><b>' + (cs.q_p1 || 0) + "/" + (cs.q_p2 || 0) + "/" + (cs.q_p3 || 0) + "</b></div>";
    $("hh-count").textContent = cs.households || 0;
  }

  // ------------------------------------------------------------------ banners, jobs, top
  function renderBanners(st) {
    var h = [], now = Date.now();
    if (st.recovered && (st.recovered.drafts || st.recovered.unsent)) {
      h.push('<div class="banner info">♻️ 已復原 ' + st.recovered.drafts + " 筆草稿，" + st.recovered.unsent + " 筆待送 <small>Recovered " +
        st.recovered.drafts + " drafts, " + st.recovered.unsent + " unsent</small></div>");
    }
    if (st.health && st.health.mode === "PAPER") {
      h.push('<div class="banner warn">📄 紙本模式：請匯出 CSV 或列印看板 <small>Paper mode: export CSV or print the board</small> <button class="btn btn-small" data-print="1">🖨️ 列印</button></div>');
    }
    (st.banners || []).forEach(function (b) {
      h.push('<div class="banner ' + (b.level || "warn") + (b.kind === "unconfirmed_p1" ? " flash" : "") + '">' + esc(b.zh) + " <small>" + esc(b.en) + "</small>" +
        (b.print ? ' <button class="btn btn-small" data-print="1">🖨️ 列印</button>' : "") + "</div>");
      if (b.beep) {
        var key = b.kind + b.seq;
        if (!S.beepAt[key] || now - S.beepAt[key] > 30000) { S.beepAt[key] = now; beep(); }
      }
    });
    $("banners").innerHTML = h.join("");
  }

  function renderJob(st) {
    var jobs = st.jobs || [];
    var j = null;
    if (S.lastJobId) jobs.forEach(function (x) { if (x.id === S.lastJobId) j = x; });
    if (!j) j = jobs[0];
    if (!j) return;
    var parts = ["<b>" + esc(STAGES[j.stage] || j.stage) + "</b>"];
    if (j.duration_s !== null && j.duration_s !== undefined) parts.push("錄音 " + j.duration_s + " 秒");
    if (j.transcript) parts.push("「" + esc(j.transcript.slice(0, 60)) + "」");
    var tm = j.timings || {};
    var tparts = Object.keys(tm).map(function (k) { return k + " " + tm[k] + "s"; });
    if (tparts.length) parts.push('<small class="muted">' + esc(tparts.join(" · ")) + "</small>");
    (j.warnings || []).forEach(function (w) { parts.push('<span class="amber-note">⚠ ' + esc(w.zh) + " <small>" + esc(w.en) + "</small></span>"); });
    if (j.seqs && j.seqs.length) parts.push("→ " + j.seqs.length + " 筆草稿");
    var el = $("job-line");
    el.innerHTML = parts.join(" ");
    el.setAttribute("data-job", j.id);
    el.setAttribute("data-stage", j.stage);
  }

  function renderTop(st) {
    $("shelter-name").textContent = st.desk.shelter_name || "DarkShelter";
    $("desk-code").textContent = st.desk.letter + "站 · desk " + st.desk.desk_id;
    var sel = $("clerk-select");
    if (!sel.options.length) {
      (st.desk.clerks || []).forEach(function (c) {
        var o = document.createElement("option"); o.textContent = c; o.value = c; sel.appendChild(o);
      });
      var saved = lsGet("ds_clerk", "");
      if (saved) sel.value = saved;
      S.clerk = sel.value;
    }
  }

  function render(st) {
    S.st = st;
    renderTop(st);
    renderBanners(st);
    renderDrafts(st);
    renderBoard(st);
    renderRight(st);
    renderJob(st);
    document.body.classList.toggle("mode-paper", st.health && st.health.mode === "PAPER");
  }

  // ------------------------------------------------------------------ polling
  var pollTimer = null;
  function poll() {
    clearTimeout(pollTimer);
    api("GET", "/api/state").then(function (res) {
      if (res.ok) { S.failures = 0; render(res.data); }
      else {
        S.failures++;
        if (S.failures > 2) $("banners").innerHTML = '<div class="banner danger">⚠️ 連不到登記台程式 <small>Desk server unreachable</small></div>';
      }
    }).then(function () {
      pollTimer = setTimeout(poll, S.failures ? 2000 : 1000);
    });
  }
  function pollSoon() { clearTimeout(pollTimer); pollTimer = setTimeout(poll, 150); }

  // ------------------------------------------------------------------ intake
  function submitText() {
    var ta = $("text-input"), text = ta.value.trim();
    if (!text) return;
    var btn = $("text-submit");
    btn.disabled = true;
    api("POST", "/api/intake/text", { text: text, clerk: S.clerk }).then(function (res) {
      btn.disabled = false;
      if (!res.ok) return showError(res);
      S.lastJobId = res.data.job_id;
      ta.value = "";
      pollSoon();
    });
  }

  var recorder = null;
  function setupRecorder() {
    var btn = $("record-btn"), bar = $("level-bar"), status = $("rec-status");
    if (!window.DSRecorder) { btn.disabled = true; return; }
    recorder = new window.DSRecorder({
      onLevel: function (db, speech) {
        var pct = Math.max(0, Math.min(100, (db + 60) * 100 / 60));
        bar.style.width = pct + "%";
        bar.className = speech ? "speech" : "";
      },
      onStop: function (wav, reason, seconds) {
        btn.classList.remove("recording");
        btn.innerHTML = "🎙️ 錄音（建議由值班人員複述重點）<small>Record (clerk repeats the key points)</small>";
        bar.style.width = "0%";
        status.textContent = "上傳中… uploading (" + seconds.toFixed(1) + " s)";
        fetch("/api/intake/audio?clerk=" + encodeURIComponent(S.clerk), {
          method: "POST", headers: { "Content-Type": "audio/wav" }, body: wav
        }).then(function (r) { return r.json(); }).then(function (d) {
          status.textContent = "";
          if (d.job_id) { S.lastJobId = d.job_id; pollSoon(); }
          else toast("⚠️ " + esc(d.zh || "上傳失敗"), "error");
        }).catch(function () { status.textContent = ""; toast("⚠️ 上傳失敗 upload failed", "error"); });
      },
      onError: function (err) {
        btn.classList.remove("recording");
        btn.innerHTML = "🎙️ 錄音（建議由值班人員複述重點）<small>Record (clerk repeats the key points)</small>";
        api("POST", "/api/mic", { ok: false });
        toast("🎙️ 麥克風無法使用，已切換為打字 <small>Microphone unavailable: switched to typing (" + esc(err && err.name || err) + ")</small>", "error", null, 8000);
        $("text-input").focus();
      }
    });
    btn.addEventListener("click", function () {
      if (recorder.isRecording()) { recorder.stop("click"); return; }
      btn.classList.add("recording");
      btn.innerHTML = "⏹ 停止 <small>Stop</small>";
      status.textContent = "錄音中… recording";
      recorder.start();
      if (S.st && S.st.health && S.st.health.mic_ok === false) api("POST", "/api/mic", { ok: true });
    });
  }

  function setupCheckin() {
    var form = $("checkin-form");
    form.addEventListener("submit", function (ev) {
      ev.preventDefault();
      var f = form.elements, scr = {};
      ["q1", "q2", "q3", "q4", "q5"].forEach(function (q) { scr[q] = f[q].checked; });
      var body = {
        family_name: f.family_name.value, size: parseInt(f.size.value, 10) || 0, zone: f.zone.value,
        elderly: +f.elderly.value || 0, infants: +f.infants.value || 0, children: +f.children.value || 0,
        pregnant: +f.pregnant.value || 0, disabled: +f.disabled.value || 0, screening: scr, clerk: S.clerk
      };
      api("POST", "/api/checkin", body).then(function (res) {
        if (!res.ok) return showError(res);
        $("checkin-result").textContent = "✓ 已登記第 " + res.data.household_id + " 戶，建立 " + res.data.drafts.length + " 筆草稿";
        form.reset();
        S.hhAt = 0;
        pollSoon();
      });
    });
    document.querySelector(".households").addEventListener("toggle", loadHouseholds);
  }
  function loadHouseholds() {
    api("GET", "/api/households").then(function (res) {
      if (!res.ok) return;
      $("hh-list").innerHTML = res.data.households.map(function (h) {
        return '<div class="hh-row">#' + h.id + " " + esc(h.family_name || "") + " " + h.size + "人 " + esc(h.zone_label || "") +
          ' <button class="btn btn-small" data-checkout="' + h.id + '">離開 Check out</button></div>';
      }).join("") || '<p class="muted">尚無 none</p>';
    });
  }

  function setupGlobal() {
    document.addEventListener("click", function (ev) {
      var el = ev.target.closest("[data-profile],[data-act],[data-restore],[data-checkout],[data-print],.brow");
      if (!el) return;
      if (el.hasAttribute("data-profile")) {
        var name = el.getAttribute("data-profile");
        api("POST", "/api/link/profile", { name: name }).then(function (res) {
          if (!res.ok) return showError(res);
          toast("📡 網路設定：" + esc(name), "ok"); pollSoon();
        });
      } else if (el.hasAttribute("data-act")) {
        ev.stopPropagation();
        var seq = el.getAttribute("data-seq"), act = el.getAttribute("data-act");
        api("POST", "/api/tickets/" + seq + "/" + act, { clerk: S.clerk }).then(function (res) {
          if (!res.ok) return showError(res); pollSoon();
        });
      } else if (el.hasAttribute("data-restore")) {
        api("POST", "/api/tickets/" + el.getAttribute("data-restore") + "/restore", { clerk: S.clerk }).then(function (res) {
          if (!res.ok) return showError(res); pollSoon();
        });
      } else if (el.hasAttribute("data-checkout")) {
        api("POST", "/api/checkout/" + el.getAttribute("data-checkout"), { clerk: S.clerk }).then(function (res) {
          if (!res.ok) return showError(res); loadHouseholds(); pollSoon();
        });
      } else if (el.hasAttribute("data-print")) {
        window.print();
      } else if (el.classList.contains("brow")) {
        var s = el.getAttribute("data-seq");
        S.revealed[s] = !S.revealed[s];
        if (S.st) renderBoard(S.st);
      }
    });
    $("print-btn").addEventListener("click", function () { window.print(); });
    $("privacy-toggle").addEventListener("change", function (ev) {
      S.privacy = ev.target.checked; S.revealed = {}; if (S.st) renderBoard(S.st);
    });
    $("mode-select").addEventListener("change", function (ev) {
      api("POST", "/api/mode", { mode: ev.target.value || null }).then(function (res) {
        if (!res.ok) return showError(res); pollSoon();
      });
    });
    $("clerk-select").addEventListener("change", function (ev) { S.clerk = ev.target.value; lsSet("ds_clerk", S.clerk); });
    $("type-only").addEventListener("change", function (ev) {
      S.typeOnly = ev.target.checked;
      $("record-row").classList.toggle("hidden", S.typeOnly);
      lsSet("ds_type_only", S.typeOnly ? "1" : "0");
    });
    if (lsGet("ds_type_only", "0") === "1") { $("type-only").checked = true; $("record-row").classList.add("hidden"); }
    document.querySelectorAll(".tab").forEach(function (t) {
      t.addEventListener("click", function () {
        document.querySelectorAll(".tab").forEach(function (x) { x.classList.toggle("active", x === t); });
        var which = t.getAttribute("data-tab");
        $("panel-requests").classList.toggle("hidden", which !== "requests");
        $("panel-checkin").classList.toggle("hidden", which !== "checkin");
      });
    });
    $("text-input").addEventListener("keydown", function (ev) {
      if (ev.key === "Enter" && !ev.shiftKey && !ev.isComposing) { ev.preventDefault(); submitText(); }
    });
    $("text-submit").addEventListener("click", submitText);
    $("quick-chips").innerHTML = QUICK.map(function (w) { return '<button class="chip pick" data-word="' + w + '">' + w + "</button>"; }).join("");
    $("quick-chips").addEventListener("click", function (ev) {
      var b = ev.target.closest("[data-word]");
      if (!b) return;
      var ta = $("text-input");
      ta.value = (ta.value ? ta.value + "，" : "") + b.getAttribute("data-word");
      ta.focus();
    });
    S.privacy = $("privacy-toggle").checked;
  }

  function init() {
    setupGlobal();
    setupCheckin();
    setupRecorder();
    api("GET", "/api/codebook").then(function (res) {
      if (res.ok) S.cb = res.data;
      poll();
    });
  }
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", init); else init();
})();
