// content.js - Runs in every page, listens for background poll requests
// (The background script injects checkForPopup() directly, so this file
// is kept minimal — it can be extended for mutation-observer based detection.)

// Optional: use a MutationObserver to catch popups the instant they appear
// rather than waiting up to 10 seconds.
(function () {
  let lastReported = 0;

  const observer = new MutationObserver(() => {
    const now = Date.now();
    // Debounce: don't spam messages more than once every 5 seconds
    if (now - lastReported < 5000) return;

    const popupSelectors = [
      "[role='dialog']",
      "[role='alertdialog']",
      ".modal",
      ".popup",
      ".dialog",
      "[class*='popup']",
      "[class*='modal']",
      "[class*='dialog']",
      "[class*='question']",
    ];

    for (const selector of popupSelectors) {
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

          if (isVisible) {
            lastReported = now;
            chrome.runtime.sendMessage({ type: "POPUP_DETECTED" });
            return;
          }
        }
      } catch (e) {
        // ignore
      }
    }
  });

  observer.observe(document.body, {
    childList: true,
    subtree: true,
    attributes: true,
    attributeFilter: ["style", "class", "hidden"],
  });
})();
