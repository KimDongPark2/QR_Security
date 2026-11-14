// 주문 접수 — POST /orders (SERVER_SPEC §7.6). 관리자 토큰(X-Admin-Token)이 필요하다.
(function () {
  "use strict";

  var TOKEN_KEY = "seungcurity.adminToken"; // sessionStorage: 탭을 닫으면 사라진다
  var FIELDS = ["name", "phone", "region", "address", "token"];
  var LABELS = { name: "이름", phone: "전화번호", region: "배송 권역", address: "상세 주소", token: "판매자 인증 토큰" };

  var params = new URLSearchParams(location.search);
  // 기본은 이 페이지를 띄운 서버. 다른 서버로 보낼 때만 ?server=https://... 를 붙인다.
  var API = (params.get("server") || "").replace(/\/+$/, "");
  var serverQuery = API ? "&server=" + encodeURIComponent(API) : "";

  var $ = function (id) { return document.getElementById(id); };
  var form = $("order"), button = $("order-submit"), msg = $("order-msg"), done = $("done");
  var inputs = {};
  FIELDS.forEach(function (f) { inputs[f] = $("f-" + f); });

  function readToken() {
    try { return sessionStorage.getItem(TOKEN_KEY) || ""; } catch (e) { return ""; }
  }
  function saveToken(value) {
    try { value ? sessionStorage.setItem(TOKEN_KEY, value) : sessionStorage.removeItem(TOKEN_KEY); } catch (e) { /* 저장 못 해도 주문은 된다 */ }
  }

  function showMessage(text) {
    msg.textContent = text;
    msg.hidden = !text;
  }

  function markInvalid(field) {
    FIELDS.forEach(function (f) {
      if (f === field) inputs[f].setAttribute("aria-invalid", "true");
      else inputs[f].removeAttribute("aria-invalid");
    });
    if (field) inputs[field].focus();
  }

  function showDone(order) {
    var no = encodeURIComponent(order.order_no);
    $("d-order-no").textContent = order.order_no;
    $("d-qr").src = API + (order.qr_url || "/qr/" + no);
    $("d-label").href = API + (order.label_url || "/labels/" + no);
    $("d-track").href = "./?order_no=" + no + serverQuery;

    // 개인정보는 화면에 남기지 않는다(토큰만 유지).
    ["name", "phone", "region", "address"].forEach(function (f) { inputs[f].value = ""; });
    form.hidden = true;
    done.hidden = false;
    $("d-track").focus();
  }

  form.addEventListener("submit", function (e) {
    e.preventDefault();
    showMessage("");

    var values = {};
    for (var i = 0; i < FIELDS.length; i++) {
      var f = FIELDS[i];
      values[f] = inputs[f].value.trim();
      if (!values[f]) {
        showMessage(LABELS[f] + "을(를) 입력해 주세요.");
        markInvalid(f);
        return;
      }
    }
    markInvalid(null);
    button.disabled = true;
    button.textContent = "주문 접수 중…";

    fetch(API + "/orders", {
      method: "POST",
      headers: { "Content-Type": "application/json", "X-Admin-Token": values.token },
      body: JSON.stringify({ name: values.name, phone: values.phone, address: values.address, region: values.region })
    }).then(function (res) {
      return res.json().catch(function () { return {}; }).then(function (body) {
        if (res.ok) {
          saveToken(values.token);
          showDone(body);
        } else if (res.status === 401) {
          saveToken("");
          showMessage("판매자 인증 토큰이 올바르지 않습니다. 서버 .env 의 ADMIN_TOKEN 을 확인해 주세요.");
          markInvalid("token");
        } else if (res.status === 422) {
          // 서버 검증 오류: 어느 칸인지만 알려 준다(보낸 값은 응답에 없다).
          var loc = body.detail && body.detail[0] && body.detail[0].loc;
          var field = loc && loc[loc.length - 1];
          showMessage((LABELS[field] || "입력값") + "을(를) 다시 확인해 주세요.");
          markInvalid(LABELS[field] ? field : null);
        } else {
          showMessage("주문을 접수하지 못했습니다. 잠시 뒤 다시 시도해 주세요. (" + (body.code || res.status) + ")");
        }
      });
    }).catch(function () {
      showMessage("서버에 연결하지 못했습니다. 네트워크와 서버 주소를 확인해 주세요.");
    }).then(function () {
      button.disabled = false;
      button.textContent = "주문하기";
    });
  });

  $("d-again").addEventListener("click", function () {
    done.hidden = true;
    form.hidden = false;
    inputs.name.focus();
  });

  inputs.token.value = readToken();
})();
