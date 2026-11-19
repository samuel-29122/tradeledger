// Unit tests for tradeledger/static/js/analytics.js (Node's built-in test
// runner, no packages needed):   node --test tests/js/*.test.js
//
// They check the PURE parts: number conversion, chart configurations and
// tooltip text. The Playwright tests check the page wiring with a stand-in
// for Chart.js; Chart.js's own drawing is checked by hand (see the guide).
"use strict";

const test = require("node:test");
const assert = require("node:assert/strict");
const path = require("node:path");

const charts = require(path.join(__dirname, "..", "..", "tradeledger", "static", "js", "analytics.js"));

const THEME = {
    text: "#e8eef8", muted: "#a3b1c7", grid: "#24334f", zero: "#8796af",
    accent: "#6aa8ff", positive: "#4cd79a", negative: "#ff8a8a", font: "system-ui"
};

// A payload shaped exactly like performance/dashboard.chart_payload().
const PAYLOAD = {
    curve: [
        {start: true, x: 1000, cumulative_r: "0", cumulative_text: "0.00R", underwater_r: "0", drawdown_text: "0.00R"},
        {start: false, trade_id: 1, symbol: "TEST", x: 1000, when: "31 Dec 2025, 23:30 UTC", close_time_known: true,
            realised_r: "2", realised_text: "+2.00R", cumulative_r: "2", cumulative_text: "+2.00R",
            underwater_r: "0", drawdown_text: "0.00R"},
        {start: false, trade_id: 3, symbol: "TEST", x: 2000, when: "02 Jan 2026, 09:00 UTC", close_time_known: false,
            realised_r: "-2", realised_text: "-2.00R", cumulative_r: "0", cumulative_text: "0.00R",
            underwater_r: "-2", drawdown_text: "2.00R"}
    ],
    max_drawdown: {amount: "2", text: "2.00R", peak_trade_id: 1, trough_trade_id: 3},
    monthly: [
        {key: "2025-12", label: "Dec 2025", trades: 1, wins: 1, losses: 0, total_r: "2", total_r_text: "+2.00R",
            win_rate_text: "100.0%", average_r_text: "+2.00R", expectancy_text: "+2.00R"},
        {key: "2026-01", label: "Jan 2026", trades: 0, wins: 0, losses: 0, total_r: null,
            total_r_text: "No closed trades", win_rate_text: "n/a", average_r_text: "n/a", expectancy_text: "n/a"}
    ],
    weekday: [],
    instruments: [
        {label: "TEST", small_sample: true, trades: 2, wins: 1, losses: 1, total_r: "0.333333333333333333333333",
            total_r_text: "+0.33R", win_rate_text: "50.0%", average_r_text: "+0.17R", expectancy_text: "+0.17R"},
        {label: "LOSS", small_sample: false, trades: 12, wins: 2, losses: 10, total_r: "-3.5",
            total_r_text: "-3.50R", win_rate_text: "16.7%", average_r_text: "-0.29R", expectancy_text: "-0.29R"}
    ],
    strategies: [],
    sessions: []
};

test("toNumber converts exact strings and refuses unsafe values", () => {
    assert.equal(charts.toNumber("7.68817307692307692307692"), 7.688173076923077);
    assert.equal(charts.toNumber("-1.5"), -1.5);
    assert.equal(charts.toNumber("0"), 0);
    for (const bad of [null, undefined, "", "abc", "Infinity", "-Infinity", "NaN", "1e400"]) {
        assert.equal(charts.toNumber(bad), null, String(bad));
    }
});

test("hexToRgba builds translucent colours and leaves other values alone", () => {
    assert.equal(charts.hexToRgba("#ff8a8a", 0.18), "rgba(255, 138, 138, 0.18)");
    assert.equal(charts.hexToRgba("red", 0.5), "red");
});

test("formatAxisValue removes floating-point noise from R tick labels", () => {
    assert.equal(charts.formatAxisValue(-0.20000000000000002), "-0.2R");
    assert.equal(charts.formatAxisValue(-0.6000000000000001), "-0.6R");
    assert.equal(charts.formatAxisValue(1.25), "1.25R");
    assert.equal(charts.formatAxisValue(-0.00000000000000004), "0R");
    assert.equal(charts.formatAxisValue(3), "3R");
    assert.equal(charts.formatAxisValue(NaN), "");
});

test("formatAxisDate shows the UTC date", () => {
    assert.equal(charts.formatAxisDate(Date.UTC(2026, 0, 2, 23, 59)), "2026-01-02");
    assert.equal(charts.formatAxisDate(NaN), "");
});

test("buildConfigs returns all seven charts", () => {
    const configs = charts.buildConfigs(PAYLOAD, THEME);
    assert.deepEqual(Object.keys(configs).sort(),
        ["curve", "drawdown", "instruments", "monthly", "sessions", "strategies", "weekday"]);
    for (const config of Object.values(configs)) {
        assert.equal(config.options.animation, false);
        assert.equal(config.options.responsive, true);
        assert.equal(config.options.maintainAspectRatio, false);
    }
});

test("cumulative R chart plots the exact values on a time axis", () => {
    const config = charts.buildConfigs(PAYLOAD, THEME).curve;
    assert.equal(config.type, "line");
    assert.equal(config.options.scales.x.type, "linear");
    assert.deepEqual(config.data.datasets[0].data, [{x: 1000, y: 0}, {x: 1000, y: 2}, {x: 2000, y: 0}]);
    assert.equal(config.options.scales.x.ticks.callback(Date.UTC(2026, 2, 7)), "2026-03-07");
});

test("cumulative R tooltip uses the server's text, including the legacy warning", () => {
    const callbacks = charts.buildConfigs(PAYLOAD, THEME).curve.options.plugins.tooltip.callbacks;
    assert.equal(callbacks.title([{dataIndex: 0}]), "Before the first trade");
    assert.equal(callbacks.title([{dataIndex: 2}]), "02 Jan 2026, 09:00 UTC");
    assert.deepEqual(callbacks.label({dataIndex: 1}),
        ["Trade #1 · TEST", "Trade R: +2.00R", "Cumulative R: +2.00R"]);
    assert.deepEqual(callbacks.label({dataIndex: 2}),
        ["Trade #3 · TEST", "Trade R: -2.00R", "Cumulative R: 0.00R",
            "Exit time not recorded: placed at entry time"]);
});

test("drawdown chart plots below zero and marks the maximum", () => {
    const config = charts.buildConfigs(PAYLOAD, THEME).drawdown;
    const dataset = config.data.datasets[0];
    assert.deepEqual(dataset.data.map((p) => p.y), [0, 0, -2]);
    assert.equal(dataset.fill, "origin");
    assert.deepEqual(dataset.pointStyle, ["circle", "circle", "triangle"]);
    assert.equal(dataset.pointRadius[2], 6);
    const lines = config.options.plugins.tooltip.callbacks.label({dataIndex: 2});
    assert.ok(lines.includes("Maximum drawdown: 2.00R"));
    assert.equal(lines[0], "Drawdown: 2.00R below the running peak");
});

test("zero line is drawn stronger than other grid lines", () => {
    const grid = charts.buildConfigs(PAYLOAD, THEME).monthly.options.scales.y.grid;
    assert.equal(grid.color({tick: {value: 0}}), THEME.zero);
    assert.equal(grid.color({tick: {value: 1}}), THEME.grid);
    assert.equal(grid.lineWidth({tick: {value: 0}}), 2);
});

test("bar chart leaves empty periods blank instead of drawing zero", () => {
    const config = charts.buildConfigs(PAYLOAD, THEME).monthly;
    assert.deepEqual(config.data.labels, ["Dec 2025", "Jan 2026"]);
    assert.deepEqual(config.data.datasets[0].data, [2, null]);
    assert.deepEqual(config.options.plugins.tooltip.callbacks.label({dataIndex: 1}), ["No closed trades"]);
});

test("group charts are horizontal, coloured by sign and flag small samples", () => {
    const config = charts.buildConfigs(PAYLOAD, THEME).instruments;
    assert.equal(config.options.indexAxis, "y");
    assert.deepEqual(config.data.labels, ["TEST *", "LOSS"]);
    assert.deepEqual(config.data.datasets[0].backgroundColor, [THEME.positive, THEME.negative]);
    const lines = config.options.plugins.tooltip.callbacks.label({dataIndex: 0});
    assert.deepEqual(lines, ["Total R: +0.33R", "Closed trades: 2 (won 1, lost 1)", "Win rate: 50.0%",
        "Average R: +0.17R", "Expectancy: +0.17R", "Small sample: treat with caution"]);
});

test("init without Chart.js shows the fallback message on every chart", () => {
    const made = [];
    const canvas = (name) => {
        const note = {hidden: true, textContent: ""};
        const box = {classList: {add: (c) => made.push(c)}, querySelector: () => note};
        return {name, note, hidden: false, getAttribute: () => name, closest: () => box};
    };
    const canvases = [canvas("curve"), canvas("monthly")];
    const doc = {
        getElementById: () => ({textContent: JSON.stringify(PAYLOAD)}),
        querySelectorAll: () => canvases
    };
    assert.deepEqual(charts.init(doc, undefined), []);
    for (const c of canvases) {
        assert.equal(c.hidden, true);
        assert.equal(c.note.hidden, false);
        assert.match(c.note.textContent, /could not be loaded/);
    }
    assert.deepEqual(made, ["is-unavailable", "is-unavailable"]);
});
