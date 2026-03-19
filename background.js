// background.js - Service worker that polls the monitored tab every 10 seconds

let monitoredTabId = null;
let isMonitoring = false;

// Listen for messages from popup.html and content.js
chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (message.type === "START_MONITORING") {
    monitoredTabId = message.tabId;
    isMonitoring = true;
    chrome.storage.local.set({ monitoredTabId, isMonitoring: true });
    startPolling();
    sendResponse({ success: true });
  }

  if (message.type === "STOP_MONITORING") {
    isMonitoring = false;
    monitoredTabId = null;
    chrome.storage.local.set({ monitoredTabId: null, isMonitoring: false });
    chrome.alarms.clear("pollTab");
    sendResponse({ success: true });
  }

  if (message.type === "GET_STATUS") {
    sendResponse({ monitoredTabId, isMonitoring });
  }

  if (message.type === "POPUP_DETECTED") {
    handlePopupDetected(sender.tab);
  }

  return true;
});

// Restore state when service worker wakes up
chrome.storage.local.get(["monitoredTabId", "isMonitoring"], (data) => {
  if (data.isMonitoring && data.monitoredTabId) {
    monitoredTabId = data.monitoredTabId;
    isMonitoring = true;
    startPolling();
  }
});

function startPolling() {
  chrome.alarms.clear("pollTab", () => {
    chrome.alarms.create("pollTab", { periodInMinutes: 10 / 60 }); // every 10 seconds
  });
}

// Alarm fires every 10 seconds
chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === "pollTab" && isMonitoring && monitoredTabId !== null) {
    pollTab();
  }
});

function pollTab() {
  chrome.tabs.get(monitoredTabId, (tab) => {
    if (chrome.runtime.lastError || !tab) {
      // Tab was closed
      isMonitoring = false;
      monitoredTabId = null;
      chrome.storage.local.set({ monitoredTabId: null, isMonitoring: false });
      chrome.alarms.clear("pollTab");
      return;
    }

    // Inject a check into the tab
    chrome.scripting.executeScript({
      target: { tabId: monitoredTabId },
      func: checkForPopup,
    }).then((results) => {
      if (results && results[0] && results[0].result === true) {
        handlePopupDetected(tab);
      }
    }).catch(() => {
      // Page may not be injectable (e.g. chrome:// pages)
    });
  });
}

// This function runs inside the monitored tab's page context
function checkForPopup() {
  // Checks for visible popup/dialog/modal/question elements
  const selectors = [
    // Generic modal/dialog patterns
    "[role='dialog']",
    "[role='alertdialog']",
    ".modal",
    ".popup",
    ".dialog",
    ".overlay",
    ".question-popup",
    ".question-modal",
    // Common quiz/question platform patterns
    ".question-container:not(.hidden)",
    ".quiz-question",
    "[class*='popup']:not([class*='hidden'])",
    "[class*='modal']:not([class*='hidden'])",
    "[class*='dialog']:not([class*='hidden'])",
    "[class*='question']:not([class*='hidden'])",
    // Visible elements with inline style
  ];

  for (const selector of selectors) {
    try {
      const elements = document.querySelectorAll(selector);
      for (const el of elements) {
        const style = window.getComputedStyle(el);
        const rect = el.getBoundingClientRect();
        const isVisible =
          style.display !== "none" &&
          style.visibility !== "hidden" &&
          style.opacity !== "0" &&
          rect.width > 0 &&
          rect.height > 0;
        if (isVisible) return true;
      }
    } catch (e) {
      // ignore selector errors
    }
  }

  // Also check for any element that contains question-like text in a visible popup
  const allElements = document.querySelectorAll("*");
  for (const el of allElements) {
    const style = window.getComputedStyle(el);
    const rect = el.getBoundingClientRect();
    const isVisible =
      style.display !== "none" &&
      style.visibility !== "hidden" &&
      style.opacity !== "0" &&
      rect.width > 50 &&
      rect.height > 50 &&
      rect.width < window.innerWidth * 0.95; // Not a full-page element

    if (isVisible) {
      const zIndex = parseInt(style.zIndex, 10);
      const position = style.position;
      // High z-index + fixed/absolute = likely a popup
      if (
        !isNaN(zIndex) &&
        zIndex > 100 &&
        (position === "fixed" || position === "absolute")
      ) {
        return true;
      }
    }
  }

  return false;
}

function handlePopupDetected(tab) {
  // Show a browser notification
  chrome.notifications.create("popupDetected", {
    type: "basic",
    iconUrl: "icons/icon128.png",
    title: "Popup Detected!",
    message: `A question or popup appeared on: ${tab.title || tab.url}`,
    priority: 2,
    requireInteraction: true,
  });

  // Focus the window and switch to the tab automatically
  chrome.windows.update(tab.windowId, { focused: true });
  chrome.tabs.update(tab.id, { active: true });
}

// Clicking the notification focuses the tab
chrome.notifications.onClicked.addListener((notificationId) => {
  if (notificationId === "popupDetected" && monitoredTabId !== null) {
    chrome.tabs.get(monitoredTabId, (tab) => {
      if (tab) {
        chrome.windows.update(tab.windowId, { focused: true });
        chrome.tabs.update(tab.id, { active: true });
      }
    });
    chrome.notifications.clear(notificationId);
  }
});
