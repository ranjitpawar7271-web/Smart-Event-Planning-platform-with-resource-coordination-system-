/* =========================================================
   EVENTRA - Ticket Check-in Scanner
   Uses the html5-qrcode library (loaded via CDN in scanner.html)
   to read a ticket's QR code from the device camera, then POSTs
   the raw token to the check-in/check-out endpoint for this event.
   The server re-verifies the signature — this file never decides
   whether a ticket is valid, it just relays what the camera saw.
   ========================================================= */
(function () {
  "use strict";

  document.addEventListener("DOMContentLoaded", function () {
    var config = window.EVENTRA_SCANNER;
    if (!config) {
      return;
    }

    var mode = "checkin";
    var resultPanel = document.getElementById("scan-result");
    var csrfInput = document.querySelector("#scan-csrf-form [name=csrfmiddlewaretoken]");

    var lastCode = null;
    var lastScanTime = 0;
    var RESCAN_COOLDOWN_MS = 4000;
    var busy = false;

    document.querySelectorAll("[data-scan-mode]").forEach(function (btn) {
      btn.addEventListener("click", function () {
        mode = btn.getAttribute("data-scan-mode");
        document.querySelectorAll("[data-scan-mode]").forEach(function (b) {
          b.classList.toggle("active", b === btn);
        });
      });
    });

    function showResult(resultType, message) {
      resultPanel.className =
        "glass-card scan-result-panel p-3 mt-3 d-flex align-items-center justify-content-center text-center result-" +
        resultType;
      var p = document.createElement("p");
      p.className = "mb-0";
      p.textContent = message;
      resultPanel.innerHTML = "";
      resultPanel.appendChild(p);
    }

    function submitScan(token) {
      if (busy) {
        return;
      }
      busy = true;

      var url = mode === "checkin" ? config.checkInUrl : config.checkOutUrl;
      var body = new URLSearchParams();
      body.append("token", token);

      fetch(url, {
        method: "POST",
        headers: {
          "X-CSRFToken": csrfInput ? csrfInput.value : "",
          "Content-Type": "application/x-www-form-urlencoded"
        },
        body: body.toString()
      })
        .then(function (res) {
          return res.json();
        })
        .then(function (data) {
          var resultType = data.success
            ? "success"
            : data.result === "duplicate"
            ? "duplicate"
            : "invalid";
          showResult(resultType, data.message || "Scan processed.");
        })
        .catch(function () {
          showResult("invalid", "Couldn't reach the server. Check your connection and try again.");
        })
        .finally(function () {
          window.setTimeout(function () {
            busy = false;
          }, 1200);
        });
    }

    function onScanSuccess(decodedText) {
      var now = Date.now();
      if (decodedText === lastCode && now - lastScanTime < RESCAN_COOLDOWN_MS) {
        return; // ignore immediate re-reads of the same code by the camera
      }
      lastCode = decodedText;
      lastScanTime = now;
      submitScan(decodedText);
    }

    if (typeof Html5Qrcode === "undefined") {
      showResult("invalid", "Camera scanner library failed to load. Check your connection.");
      return;
    }

    var scanner = new Html5Qrcode("qr-reader");
    Html5Qrcode.getCameras()
      .then(function (devices) {
        if (!devices || !devices.length) {
          showResult("invalid", "No camera found on this device.");
          return;
        }
        // Prefer the last camera in the list - on phones this is usually
        // the rear-facing one, which is what staff will use at the door.
        var cameraId = devices[devices.length - 1].id;
        scanner
          .start(cameraId, { fps: 10, qrbox: 240 }, onScanSuccess)
          .catch(function () {
            showResult("invalid", "Couldn't access the camera. Check permissions and try again.");
          });
      })
      .catch(function () {
        showResult("invalid", "Couldn't access the camera. Check permissions and try again.");
      });
  });
})();
