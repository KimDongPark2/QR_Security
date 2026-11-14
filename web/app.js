// 주문자 확인 웹 — GET /track/{order_no} (SERVER_SPEC §7.9) 만 쓴다. 개인정보는 받지도 보여주지도 않는다.
(function () {
  "use strict";

  var POLL_MS = 4000; // 배송 완료 전까지 이 간격으로 다시 조회한다
  var STEPS = ["READY", "AT_HUB", "OUT_FOR_DELIVERY", "DELIVERED"];
  var DESCRIPTIONS = {
    READY: "판매자가 상품을 보낼 준비를 하고 있습니다.",
    AT_HUB: "택배가 물류 허브에 도착했습니다. 허브에서는 배송 권역만 확인합니다.",
    OUT_FOR_DELIVERY: "담당 기사가 배송하고 있습니다.",
    DELIVERED: "배송이 완료되었습니다."
  };
  var PII_VIEW = {
    VIEWABLE_BY_ASSIGNED_DRIVER: {
      locked: false,
      title: "담당 기사만 볼 수 있습니다.",
      detail: "이름은 가려지고 전화는 안심번호로 연결됩니다. 조회한 기록은 모두 남습니다."
    },
    BLOCKED: {
      locked: true,
      title: "개인정보 조회가 차단되었습니다.",
      detail: "배송 완료와 함께 조회용 개인정보가 지워졌습니다. 운송장 QR을 다시 찍어도 아무것도 나오지 않습니다."
    }
  };

  var params = new URLSearchParams(location.search);
  // 기본은 이 페이지를 띄운 서버. 다른 서버를 볼 때만 ?server=https://... 를 붙인다.
  var API = (params.get("server") || "").replace(/\/+$/, "");

  var $ = function (id) { return document.getElementById(id); };
  var form = $("search"), input = $("order-no"), button = $("submit"), formMsg = $("form-msg");
  var result = $("result");

  var current = null;   // 지금 보고 있는 주문번호
  var timer = null;
  var requestSeq = 0;   // 늦게 도착한 이전 응답을 버리기 위한 번호

  function showFormMessage(text) {
    formMsg.textContent = text;
    formMsg.hidden = !text;
  }

  function formatKst(iso) {
    var d = new Date(iso);
    if (isNaN(d)) return iso;
    return new Intl.DateTimeFormat("ko-KR", {
      timeZone: "Asia/Seoul", year: "numeric", month: "long", day: "numeric",
      hour: "2-digit", minute: "2-digit", hour12: false
    }).format(d);
  }

  function setLive(state, text) {
    $("live-dot").className = "live-dot" + (state ? " " + state : "");
    $("live-text").textContent = text;
  }

  function render(data) {
    var idx = STEPS.indexOf(data.status);
    var delivered = data.status === "DELIVERED";

    $("r-order-no").textContent = data.order_no;
    var badge = $("r-badge");
    badge.textContent = data.status_label || data.status;
    badge.className = "badge" + (delivered ? " done" : "");

    Array.prototype.forEach.call($("steps").children, function (li, i) {
      var isNow = i === idx && !delivered;
      li.className = i < idx || (delivered && i === idx) ? "done" : isNow ? "now" : "";
      if (isNow) li.setAttribute("aria-current", "step"); else li.removeAttribute("aria-current");
    });

    $("r-desc").textContent = DESCRIPTIONS[data.status] || data.status_label || "";
    $("r-delivered").hidden = !data.delivered_at;
    if (data.delivered_at) {
      $("r-delivered-at").textContent = formatKst(data.delivered_at);
      $("r-delivered-at").setAttribute("datetime", data.delivered_at);
    }

    var view = PII_VIEW[data.pii_status] || { locked: delivered, title: data.pii_message || "", detail: "" };
    $("pii").className = "card pii" + (view.locked ? " locked" : "");
    $("pii-icon-open").toggleAttribute("hidden", view.locked);
    $("pii-icon-locked").toggleAttribute("hidden", !view.locked);
    $("pii-title").textContent = data.pii_message || view.title; // 문구는 서버가 준 것을 우선한다
    $("pii-detail").textContent = view.detail;

    result.hidden = false;
  }

  function fetchTrack(orderNo) {
    return fetch(API + "/track/" + encodeURIComponent(orderNo), { cache: "no-store" })
      .then(function (res) {
        return res.json().catch(function () { return {}; }).then(function (body) {
          if (res.ok) return body;
          var err = new Error(body.message || "HTTP " + res.status);
          err.status = res.status;
          err.code = body.code;
          throw err;
        });
      });
  }

  function stopPolling() {
    if (timer) clearTimeout(timer);
    timer = null;
  }

  function schedule() {
    stopPolling();
    if (current && !document.hidden) timer = setTimeout(refresh, POLL_MS);
  }

  // 자동 새로고침: 실패해도 화면은 그대로 두고 다음 차례에 다시 시도한다.
  function refresh() {
    var orderNo = current, seq = ++requestSeq;
    fetchTrack(orderNo).then(function (data) {
      if (seq !== requestSeq) return;
      render(data);
      afterRender(data);
    }).catch(function () {
      if (seq !== requestSeq) return;
      setLive("err", "서버와 연결이 끊겼습니다. 다시 시도하는 중…");
      schedule();
    });
  }

  function afterRender(data) {
    if (data.status === "DELIVERED") {
      current = null; // 더 바뀔 것이 없으므로 그만 묻는다
      stopPolling();
      setLive("", "배송이 완료되어 자동 새로고침을 멈췄습니다.");
    } else {
      setLive("on", "상태가 바뀌면 자동으로 반영됩니다.");
      schedule();
    }
  }

  function search(orderNo, pushUrl) {
    var seq = ++requestSeq;
    stopPolling();
    current = null;
    showFormMessage("");
    button.disabled = true;

    fetchTrack(orderNo).then(function (data) {
      if (seq !== requestSeq) return;
      current = data.order_no;
      render(data);
      afterRender(data);
      if (pushUrl) {
        var next = new URLSearchParams(location.search);
        next.set("order_no", data.order_no);
        history.replaceState(null, "", "?" + next.toString());
      }
    }).catch(function (err) {
      if (seq !== requestSeq) return;
      result.hidden = true;
      if (err.status === 404) {
        showFormMessage("주문번호 " + orderNo + " 을(를) 찾을 수 없습니다. 번호를 다시 확인해 주세요.");
      } else if (err.status) {
        showFormMessage("조회하지 못했습니다. 잠시 뒤 다시 시도해 주세요. (" + (err.code || err.status) + ")");
      } else {
        showFormMessage("서버에 연결하지 못했습니다. 네트워크와 서버 주소를 확인해 주세요.");
      }
    }).then(function () {
      if (seq === requestSeq) button.disabled = false;
    });
  }

  form.addEventListener("submit", function (e) {
    e.preventDefault();
    var orderNo = input.value.trim().toUpperCase();
    input.value = orderNo;
    if (!orderNo) {
      showFormMessage("주문번호를 입력해 주세요.");
      input.focus();
      return;
    }
    search(orderNo, true);
  });

  // 탭이 가려져 있는 동안에는 묻지 않고, 돌아오면 바로 한 번 조회한다.
  document.addEventListener("visibilitychange", function () {
    if (document.hidden) stopPolling();
    else if (current) refresh();
  });

  // /web/?order_no=SC-20261114-0001 로 열면 바로 조회한다(운송장·문자 링크용).
  var initial = (params.get("order_no") || "").trim().toUpperCase();
  if (initial) {
    input.value = initial;
    search(initial, false);
  } else {
    input.focus();
  }
})();
