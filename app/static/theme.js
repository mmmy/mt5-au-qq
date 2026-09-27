"use strict";

(() => {
  const storageKey = "mt5-ui-theme";
  let theme = "dark";
  try {
    if (localStorage.getItem(storageKey) === "light") theme = "light";
  } catch (_error) {
    // Theme switching still works when browser storage is unavailable.
  }
  document.documentElement.dataset.theme = theme;

  document.addEventListener("DOMContentLoaded", () => {
    const button = document.getElementById("themeButton");
    function updateButton() {
      button.textContent = theme === "dark" ? "白色主题" : "深色主题";
      button.setAttribute("aria-label", `切换到${button.textContent}`);
    }
    updateButton();
    button.addEventListener("click", () => {
      theme = theme === "dark" ? "light" : "dark";
      document.documentElement.dataset.theme = theme;
      updateButton();
      try { localStorage.setItem(storageKey, theme); }
      catch (_error) { /* Keep the selected theme for this page session. */ }
    });
  });
})();
