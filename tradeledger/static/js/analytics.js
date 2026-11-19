// TradeLedger analytics dashboard: draws the seven charts with Chart.js.
//
// This file is PRESENTATION ONLY. It performs no financial calculation:
//   * every figure arrives from Python in the page's #dashboard-data JSON,
//     as an exact decimal string (e.g. "7.688173...") plus display text
//     (e.g. "+7.69R");
//   * the only conversion is toNumber(), which turns an exact string into
//     a JavaScript number to POSITION a point or bar. Tooltips show the
//     Python-formatted text, never a number computed here.
//
// If Chart.js cannot be loaded (offline, CDN blocked) or JavaScript is off,
// the page still shows every figure in its KPI cards, text descriptions and
// tables; each chart area explains why it is empty.
//
// The pure functions (toNumber, buildConfigs, ...) are exported for the
// Node.js tests in tests/js/analytics.test.js.
(function (root, factory) {
    "use strict";
    var api = factory();
    if (typeof module === "object" && module.exports) {
        module.exports = api;
    } else {
        root.TradeLedgerCharts = api;
    }
    if (typeof document !== "undefined") {
        document.addEventListener("DOMContentLoaded", function () {
            api.instances = api.init(document, root.Chart);
        });
    }
}(typeof self !== "undefined" ? self : this, function () {
    "use strict";

    // Phase 8.5: Chart.js is normally served by TradeLedger itself, so the
    // message no longer assumes a CDN or an internet connection.
    var UNAVAILABLE_MESSAGE =
        "Chart unavailable: the Chart.js library could not be loaded, so this chart is not drawn. " +
        "The table below has the same figures.";
    var FAILED_MESSAGE = "This chart could not be drawn. The table below has the same figures.";

    // ---------- Small pure helpers ----------

    // Exact decimal string -> chart number. Absent, non-numeric or
    // non-finite values become null (Chart.js leaves a gap), never NaN or
    // Infinity.
    function toNumber(text) {
        if (text === null || text === undefined || text === "") {
            return null;
        }
        var value = Number(text);
        return Number.isFinite(value) ? value : null;
    }

    // "#4cd79a" + 0.2 -> "rgba(76, 215, 154, 0.2)" for translucent fills.
    function hexToRgba(hex, alpha) {
        var match = /^#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(String(hex).trim());
        if (!match) {
            return hex;
        }
        return "rgba(" + parseInt(match[1], 16) + ", " + parseInt(match[2], 16) + ", " +
            parseInt(match[3], 16) + ", " + alpha + ")";
    }

    // Axis label for a UTC time in milliseconds: "2026-03-02".
    function formatAxisDate(ms) {
        var date = new Date(ms);
        return isNaN(date.getTime()) ? "" : date.toISOString().slice(0, 10);
    }

    // Axis label for an R value: "-0.2R", never "-0.20000000000000002R".
    // Chart.js picks tick positions by repeated addition, which leaves binary
    // floating-point noise; rounding to 4 decimals is far finer than any tick
    // step it chooses here. Labels only: every figure shown as text comes from
    // Python (Phase 8.5 fix, found by the real Chart.js screenshots).
    function formatAxisValue(value) {
        var rounded = Math.round(Number(value) * 10000) / 10000;
        if (!isFinite(rounded)) {
            return "";
        }
        return (rounded === 0 ? 0 : rounded) + "R";
    }

    function signColour(value, theme) {
        if (value === null || value === 0) {
            return theme.muted;
        }
        return value > 0 ? theme.positive : theme.negative;
    }

    // Grid lines, with a stronger line at zero so gains and losses are
    // separated by a clear reference.
    function valueAxis(theme, extra) {
        var axis = {
            grid: {
                color: function (context) {
                    return context.tick && context.tick.value === 0 ? theme.zero : theme.grid;
                },
                lineWidth: function (context) {
                    return context.tick && context.tick.value === 0 ? 2 : 1;
                }
            },
            ticks: {
                callback: function (value) {
                    return formatAxisValue(value);
                }
            },
            title: {display: true, text: "R (risk multiples)"}
        };
        return Object.assign(axis, extra || {});
    }

    function baseOptions() {
        return {
            responsive: true,
            maintainAspectRatio: false,
            animation: false,                      // no decorative motion
            plugins: {
                legend: {display: false},          // one series per chart; the caption explains it
                tooltip: {}
            }
        };
    }

    // ---------- Line charts: cumulative R and drawdown ----------

    function curveTooltipLines(point) {
        if (point.start) {
            return ["Starting point: " + point.cumulative_text];
        }
        var lines = [
            "Trade #" + point.trade_id + " · " + point.symbol,
            "Trade R: " + point.realised_text,
            "Cumulative R: " + point.cumulative_text
        ];
        if (!point.close_time_known) {
            lines.push("Exit time not recorded: placed at entry time");
        }
        return lines;
    }

    function timeAxis() {
        return {
            type: "linear",
            ticks: {
                maxTicksLimit: 6,
                callback: function (value) {
                    return formatAxisDate(value);
                }
            },
            title: {display: true, text: "Realised date (UTC)"}
        };
    }

    function buildCurveConfig(points, theme) {
        var options = baseOptions();
        options.interaction = {mode: "nearest", intersect: false, axis: "x"};
        options.scales = {x: timeAxis(), y: valueAxis(theme)};
        options.plugins.tooltip.callbacks = {
            title: function (items) {
                var point = points[items[0].dataIndex];
                return point.start ? "Before the first trade" : point.when;
            },
            label: function (item) {
                return curveTooltipLines(points[item.dataIndex]);
            }
        };
        return {
            type: "line",
            data: {
                datasets: [{
                    label: "Cumulative realised R",
                    data: points.map(function (p) {
                        return {x: p.x, y: toNumber(p.cumulative_r)};
                    }),
                    borderColor: theme.accent,
                    backgroundColor: theme.accent,
                    borderWidth: 2,
                    pointRadius: points.length > 60 ? 0 : 3,
                    pointHoverRadius: 5,
                    tension: 0
                }]
            },
            options: options
        };
    }

    function buildDrawdownConfig(points, maxDrawdown, theme) {
        var troughId = maxDrawdown ? maxDrawdown.trough_trade_id : null;
        var isTrough = function (p) {
            return !p.start && troughId !== null && p.trade_id === troughId;
        };
        var options = baseOptions();
        options.interaction = {mode: "nearest", intersect: false, axis: "x"};
        options.scales = {
            x: timeAxis(),
            y: valueAxis(theme, {suggestedMax: 0})
        };
        options.plugins.tooltip.callbacks = {
            title: function (items) {
                var point = points[items[0].dataIndex];
                return point.start ? "Before the first trade" : point.when;
            },
            label: function (item) {
                var point = points[item.dataIndex];
                var lines = ["Drawdown: " + point.drawdown_text + " below the running peak"];
                if (!point.start) {
                    lines.push("Trade #" + point.trade_id + " · " + point.symbol);
                    lines.push("Cumulative R: " + point.cumulative_text);
                }
                if (isTrough(point)) {
                    lines.push("Maximum drawdown: " + maxDrawdown.text);
                }
                return lines;
            }
        };
        return {
            type: "line",
            data: {
                datasets: [{
                    label: "Drawdown (R below the running peak)",
                    data: points.map(function (p) {
                        return {x: p.x, y: toNumber(p.underwater_r)};
                    }),
                    borderColor: theme.negative,
                    backgroundColor: hexToRgba(theme.negative, 0.18),
                    fill: "origin",
                    borderWidth: 2,
                    tension: 0,
                    pointRadius: points.map(function (p) {
                        return isTrough(p) ? 6 : (points.length > 60 ? 0 : 2);
                    }),
                    pointStyle: points.map(function (p) {
                        return isTrough(p) ? "triangle" : "circle";
                    }),
                    pointBackgroundColor: points.map(function (p) {
                        return isTrough(p) ? theme.text : theme.negative;
                    })
                }]
            },
            options: options
        };
    }

    // ---------- Bar charts: months, weekdays, groups ----------

    function barTooltipLines(row) {
        if (row.total_r === null) {
            return ["No closed trades"];
        }
        var lines = [
            "Total R: " + row.total_r_text,
            "Closed trades: " + row.trades + " (won " + row.wins + ", lost " + row.losses + ")",
            "Win rate: " + row.win_rate_text,
            "Average R: " + row.average_r_text,
            "Expectancy: " + row.expectancy_text
        ];
        if (row.small_sample) {
            lines.push("Small sample: treat with caution");
        }
        return lines;
    }

    function buildBarConfig(rows, theme, settings) {
        var horizontal = Boolean(settings.horizontal);
        var values = rows.map(function (row) {
            return toNumber(row.total_r);
        });
        var options = baseOptions();
        if (horizontal) {
            options.indexAxis = "y";
            options.scales = {x: valueAxis(theme), y: {grid: {display: false}}};
        } else {
            options.scales = {x: {grid: {display: false}}, y: valueAxis(theme)};
        }
        options.plugins.tooltip.callbacks = {
            title: function (items) {
                return rows[items[0].dataIndex].label;
            },
            label: function (item) {
                return barTooltipLines(rows[item.dataIndex]);
            }
        };
        return {
            type: "bar",
            data: {
                labels: rows.map(function (row) {
                    return row.small_sample ? row.label + " *" : row.label;
                }),
                datasets: [{
                    label: settings.label,
                    data: values,
                    backgroundColor: values.map(function (value) {
                        return signColour(value, theme);
                    }),
                    borderRadius: 3,
                    maxBarThickness: 36
                }]
            },
            options: options
        };
    }

    // Every chart's configuration from the payload. Pure: no DOM, no Chart.
    function buildConfigs(payload, theme) {
        return {
            curve: buildCurveConfig(payload.curve, theme),
            drawdown: buildDrawdownConfig(payload.curve, payload.max_drawdown, theme),
            monthly: buildBarConfig(payload.monthly, theme, {label: "Total R by month"}),
            weekday: buildBarConfig(payload.weekday, theme, {label: "Total R by weekday"}),
            instruments: buildBarConfig(payload.instruments, theme,
                {label: "Total R by instrument", horizontal: true}),
            strategies: buildBarConfig(payload.strategies, theme,
                {label: "Total R by strategy", horizontal: true}),
            sessions: buildBarConfig(payload.sessions, theme,
                {label: "Total R by session", horizontal: true})
        };
    }

    // ---------- Browser glue ----------

    // Colours come from the CSS design tokens in base.css, so the charts
    // follow the same theme as the rest of the application.
    function readTheme(doc) {
        var styles = doc.defaultView.getComputedStyle(doc.documentElement);
        var token = function (name) {
            return styles.getPropertyValue(name).trim();
        };
        return {
            text: token("--color-text"),
            muted: token("--color-text-muted"),
            grid: token("--color-border"),
            zero: token("--color-text-subtle"),
            accent: token("--color-accent"),
            positive: token("--color-positive"),
            negative: token("--color-negative"),
            font: token("--font-sans")
        };
    }

    function showFallback(canvas, message) {
        var box = canvas.closest("[data-chart-box]");
        var note = box ? box.querySelector("[data-chart-fallback]") : null;
        canvas.hidden = true;
        if (box) {
            box.classList.add("is-unavailable");
        }
        if (note) {
            note.textContent = message;          // textContent: never parsed as HTML
            note.hidden = false;
        }
    }

    function init(doc, ChartLib) {
        var dataElement = doc.getElementById("dashboard-data");
        var canvases = Array.prototype.slice.call(doc.querySelectorAll("canvas[data-chart]"));
        if (!dataElement || canvases.length === 0) {
            return [];
        }
        if (typeof ChartLib !== "function") {
            canvases.forEach(function (canvas) {
                showFallback(canvas, UNAVAILABLE_MESSAGE);
            });
            return [];
        }
        var theme = readTheme(doc);
        if (ChartLib.defaults) {
            ChartLib.defaults.color = theme.muted;
            ChartLib.defaults.borderColor = theme.grid;
            if (ChartLib.defaults.font) {
                ChartLib.defaults.font.family = theme.font;
            }
        }
        var configs = buildConfigs(JSON.parse(dataElement.textContent), theme);
        var charts = [];
        canvases.forEach(function (canvas) {
            var config = configs[canvas.getAttribute("data-chart")];
            if (!config) {
                return;
            }
            try {
                charts.push(new ChartLib(canvas, config));
            } catch (error) {
                showFallback(canvas, FAILED_MESSAGE);
                if (typeof console !== "undefined") {
                    console.error("TradeLedger chart failed:", canvas.id, error);
                }
            }
        });
        return charts;
    }

    return {
        toNumber: toNumber,
        hexToRgba: hexToRgba,
        formatAxisDate: formatAxisDate,
        formatAxisValue: formatAxisValue,
        curveTooltipLines: curveTooltipLines,
        barTooltipLines: barTooltipLines,
        buildConfigs: buildConfigs,
        init: init,
        instances: []
    };
}));
