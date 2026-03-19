// Paste this into the browser console (F12 > Console) on the tab you want to monitor.
// It checks every 10 seconds for popups and alerts you instantly via sound + title flash + notification.

(function () {
  if (window.__popupMonitorRunning) {
    console.log("Monitor already running. To stop it: window.__popupMonitorStop()");
    return;
  }

  window.__popupMonitorRunning = true;
  const originalTitle = document.title;
  let flashInterval = null;

  // Ask for notification permission upfront
  if (Notification.permission === "default") {
    Notification.requestPermission();
  }

  function beep() {
    try {
      const ctx = new (window.AudioContext || window.webkitAudioContext)();
      const osc = ctx.createOscillator();
      const gain = ctx.createGain();
      osc.connect(gain);
      gain.connect(ctx.destination);
      osc.type = "sine";
      osc.frequency.value = 880;
      gain.gain.setValueAtTime(0.3, ctx.currentTime);
      gain.gain.exponentialRampToValueAtTime(0.001, ctx.currentTime + 0.8);
      osc.start(ctx.currentTime);
      osc.stop(ctx.currentTime + 0.8);
    } catch (e) {}
  }

  function flashTitle() {
    let on = true;
    clearInterval(flashInterval);
    flashInterval = setInterval(() => {
      document.title = on ? "🔔 POPUP DETECTED!" : originalTitle;
      on = !on;
    }, 600);
    // Stop flashing after 30 seconds
    setTimeout(() => {
      clearInterval(flashInterval);
      document.title = originalTitle;
    }, 30000);
  }

  function notify() {
    beep();
    flashTitle();
    if (Notification.permission === "granted") {
      new Notification("Popup detected!", {
        body: "A question or popup appeared on this page.",
        icon: "",
        requireInteraction: true,
      });
    } else {
      alert("POPUP DETECTED on this tab!");
    }
  }

  function isPopupVisible() {
    const selectors = [
      "[role='dialog']", "[role='alertdialog']",
      ".modal", ".popup", ".dialog", ".overlay",
      "[class*='popup']", "[class*='modal']",
      "[class*='dialog']", "[class*='question']",
    ];

    for (const selector of selectors) {
      try {
        for (const el of document.querySelectorAll(selector)) {
          const s = window.getComputedStyle(el);
          const r = el.getBoundingClientRect();
          if (
            s.display !== "none" && s.visibility !== "hidden" &&
            parseFloat(s.opacity) > 0 && r.width > 0 && r.height > 0
          ) return true;
        }
      } catch (e) {}
    }

    // Fallback: high z-index fixed/absolute element (covers part of screen)
    for (const el of document.querySelectorAll("*")) {
      const s = window.getComputedStyle(el);
      const r = el.getBoundingClientRect();
      const z = parseInt(s.zIndex, 10);
      if (
        !isNaN(z) && z > 100 &&
        (s.position === "fixed" || s.position === "absolute") &&
        r.width > 50 && r.height > 50 &&
        r.width < window.innerWidth * 0.95
      ) return true;
    }

    return false;
  }

  // --- MutationObserver for instant detection ---
  let lastNotified = 0;
  const observer = new MutationObserver(() => {
    if (Date.now() - lastNotified < 5000) return;
    if (isPopupVisible()) {
      lastNotified = Date.now();
      console.log("[PopupMonitor] Popup detected via MutationObserver!");
      notify();
    }
  });

  observer.observe(document.body, {
    childList: true, subtree: true,
    attributes: true, attributeFilter: ["style", "class", "hidden"],
  });

  // --- 10-second polling as backup ---
  const pollId = setInterval(() => {
    if (Date.now() - lastNotified < 5000) return;
    if (isPopupVisible()) {
      lastNotified = Date.now();
      console.log("[PopupMonitor] Popup detected via poll!");
      notify();
    }
  }, 10000);

  // Stop function
  window.__popupMonitorStop = function () {
    observer.disconnect();
    clearInterval(pollId);
    clearInterval(flashInterval);
    document.title = originalTitle;
    window.__popupMonitorRunning = false;
    console.log("[PopupMonitor] Stopped.");
  };

  console.log("%c[PopupMonitor] Running! Watching for popups every 10s + instant DOM detection.", "color: green; font-weight: bold");
  console.log("To stop: window.__popupMonitorStop()");
})();
