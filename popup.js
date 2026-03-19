// popup.js - UI logic for the extension popup

const statusEl = document.getElementById("status");
const startBtn = document.getElementById("startBtn");
const stopBtn = document.getElementById("stopBtn");
const tabInfoEl = document.getElementById("tabInfo");

function updateUI(isMonitoring, tab) {
  if (isMonitoring && tab) {
    statusEl.textContent = `Monitoring every 10s`;
    statusEl.className = "active";
    tabInfoEl.textContent = `Tab: ${tab.title || tab.url}`;
    startBtn.disabled = true;
    stopBtn.disabled = false;
  } else {
    statusEl.textContent = "Not monitoring any tab.";
    statusEl.className = "inactive";
    tabInfoEl.textContent = "";
    startBtn.disabled = false;
    stopBtn.disabled = true;
  }
}

// Load current state on popup open
chrome.runtime.sendMessage({ type: "GET_STATUS" }, (response) => {
  if (response && response.isMonitoring && response.monitoredTabId) {
    chrome.tabs.get(response.monitoredTabId, (tab) => {
      updateUI(true, tab || null);
    });
  } else {
    updateUI(false, null);
  }
});

startBtn.addEventListener("click", () => {
  chrome.tabs.query({ active: true, currentWindow: true }, (tabs) => {
    if (!tabs || tabs.length === 0) return;
    const tab = tabs[0];
    chrome.runtime.sendMessage(
      { type: "START_MONITORING", tabId: tab.id },
      () => updateUI(true, tab)
    );
  });
});

stopBtn.addEventListener("click", () => {
  chrome.runtime.sendMessage({ type: "STOP_MONITORING" }, () =>
    updateUI(false, null)
  );
});
