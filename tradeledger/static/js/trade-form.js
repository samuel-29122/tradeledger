// Trade form: show the exit fields only when the trade is CLOSED.
//
// When the user chooses OPEN:
//   * the "Exit" fieldset is disabled and hidden. A disabled fieldset's
//     inputs are not submitted, so stale exit values are never sent;
//   * a note explains that realised results appear after closing.
// When the user chooses CLOSED:
//   * the exit fields are shown and marked required (exit time is not
//     required when editing a legacy trade whose exit time was never
//     recorded: the form carries data-legacy-close).
//
// This is a convenience only. The server applies exactly the same rules
// (validation.py) whether or not this script runs.
(function () {
    "use strict";

    function init(form) {
        var statusControl = form.querySelector("[data-status-control]");
        var exitFields = form.querySelector("[data-exit-fields]");
        var openNote = form.querySelector("[data-open-note]");
        if (!statusControl || !exitFields) {
            return;
        }
        var exitPrice = exitFields.querySelector("[name='exit_price']");
        var closedAt = exitFields.querySelector("[name='closed_at']");
        var legacyClose = form.hasAttribute("data-legacy-close");

        function selectedStatus() {
            var checked = statusControl.querySelector("input[name='status']:checked");
            return checked ? checked.value : null;
        }

        function sync() {
            var isOpen = selectedStatus() === "OPEN";
            exitFields.disabled = isOpen;
            exitFields.hidden = isOpen;
            if (openNote) {
                openNote.hidden = !isOpen;
            }
            if (exitPrice) {
                exitPrice.required = !isOpen;
            }
            if (closedAt) {
                closedAt.required = !isOpen && !legacyClose;
            }
        }

        statusControl.addEventListener("change", sync);
        sync();
    }

    document.addEventListener("DOMContentLoaded", function () {
        document.querySelectorAll("[data-trade-form]").forEach(init);
    });
})();
