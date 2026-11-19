// TradeLedger: small progressive enhancements used on every page.
//
// Everything here is optional. With JavaScript turned off every page,
// link and form still works; these functions only make the interface
// smoother. No trading calculation happens in the browser.
//
//   initNavigation()     mobile menu: open/close, Escape key, focus handling
//   initFlashMessages()  "Dismiss" buttons on success/error messages
//   initSubmitOnce()     prevents double submission, shows "Saving…"
//   focusErrorSummary()  moves keyboard focus to the list of form errors
(function () {
    "use strict";

    var DESKTOP_QUERY = "(min-width: 64rem)";

    function initNavigation() {
        var sidebar = document.querySelector("[data-sidebar]");
        var toggle = document.querySelector("[data-nav-toggle]");
        var backdrop = document.querySelector("[data-nav-backdrop]");
        if (!sidebar || !toggle) {
            return;
        }

        function isOpen() {
            return sidebar.getAttribute("data-open") === "true";
        }

        function open() {
            sidebar.setAttribute("data-open", "true");
            toggle.setAttribute("aria-expanded", "true");
            document.body.classList.add("nav-open");
            if (backdrop) {
                backdrop.hidden = false;
            }
            var firstLink = sidebar.querySelector(".nav__link");
            if (firstLink) {
                firstLink.focus();
            }
        }

        function close(returnFocus) {
            sidebar.setAttribute("data-open", "false");
            toggle.setAttribute("aria-expanded", "false");
            document.body.classList.remove("nav-open");
            if (backdrop) {
                backdrop.hidden = true;
            }
            if (returnFocus) {
                toggle.focus();
            }
        }

        toggle.addEventListener("click", function () {
            if (isOpen()) {
                close(true);
            } else {
                open();
            }
        });

        document.querySelectorAll("[data-nav-close]").forEach(function (button) {
            button.addEventListener("click", function () {
                close(true);
            });
        });

        if (backdrop) {
            backdrop.addEventListener("click", function () {
                close(true);
            });
        }

        document.addEventListener("keydown", function (event) {
            if (event.key === "Escape" && isOpen()) {
                close(true);
            }
        });

        // If the window grows to desktop width, the menu is always visible,
        // so reset the mobile "open" state.
        var desktop = window.matchMedia(DESKTOP_QUERY);
        desktop.addEventListener("change", function (event) {
            if (event.matches) {
                close(false);
            }
        });
    }

    function initFlashMessages() {
        document.querySelectorAll("[data-flash-close]").forEach(function (button) {
            button.addEventListener("click", function () {
                var message = button.closest("[data-flash]");
                if (message) {
                    message.remove();
                }
            });
        });
    }

    function initSubmitOnce() {
        document.querySelectorAll("form[data-submit-once]").forEach(function (form) {
            form.addEventListener("submit", function (event) {
                if (form.getAttribute("data-submitting") === "true") {
                    event.preventDefault(); // a second click while the first is in flight
                    return;
                }
                form.setAttribute("data-submitting", "true");
                var button = form.querySelector("button[type='submit']");
                if (button) {
                    button.setAttribute("aria-busy", "true");
                    if (button.dataset.busyLabel) {
                        button.dataset.idleLabel = button.textContent;
                        button.textContent = button.dataset.busyLabel;
                    }
                }
            });
        });

        // Returning with the Back button can restore a page from the browser's
        // cache with the form still marked as submitting; reset it.
        window.addEventListener("pageshow", function (event) {
            if (!event.persisted) {
                return;
            }
            document.querySelectorAll("form[data-submitting='true']").forEach(function (form) {
                form.removeAttribute("data-submitting");
                var button = form.querySelector("button[type='submit']");
                if (button) {
                    button.removeAttribute("aria-busy");
                    if (button.dataset.idleLabel) {
                        button.textContent = button.dataset.idleLabel;
                    }
                }
            });
        });
    }

    function focusErrorSummary() {
        var summary = document.querySelector("[data-error-summary]");
        if (summary) {
            summary.focus();
        }
    }

    document.addEventListener("DOMContentLoaded", function () {
        initNavigation();
        initFlashMessages();
        initSubmitOnce();
        focusErrorSummary();
    });
})();
